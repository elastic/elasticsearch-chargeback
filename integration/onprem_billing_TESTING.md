# On-Premises Billing Integration - Testing Notes

This document captures the testing process and workarounds needed when testing in a single-cluster environment.

## Test Environment

- **Stack Version**: 9.2.0 (required for Chargeback dashboard compatibility)
- **Tool**: `elastic-package stack up -d --version=9.2.0`
- **Cluster**: Single-node local cluster (no CCS configured)

## Testing Steps

### 1. Install Prerequisites

```bash
# Build and install Elasticsearch integration
cd packages/elasticsearch && elastic-package build && elastic-package install

# Disable legacy Stack Monitoring
PUT _cluster/settings
{
  "persistent": { "xpack.monitoring.collection.enabled": false }
}
```

### 2. Enable Agent-Based Stack Monitoring

The Elasticsearch integration's transform requires the **new Stack Monitoring format** (`metrics-elasticsearch.stack_monitoring.index*`), not legacy `.monitoring-es-*`.

Add Elasticsearch metrics to the Elastic Agent policy via Fleet API:

```bash
POST /api/fleet/package_policies
{
  "name": "elasticsearch-monitoring",
  "namespace": "default",
  "policy_id": "elastic-agent-managed-ep",
  "enabled": true,
  "inputs": [{
    "type": "elasticsearch/metrics",
    "enabled": true,
    "vars": {
      "hosts": { "value": ["https://elasticsearch:9200"], "type": "text" },
      "username": { "value": "elastic", "type": "text" },
      "password": { "value": "changeme", "type": "password" }
    },
    "streams": [{
      "enabled": true,
      "data_stream": { "type": "metrics", "dataset": "elasticsearch.stack_monitoring.index" },
      "vars": { "period": { "value": "10s", "type": "text" } }
    }]
  }],
  "package": { "name": "elasticsearch", "version": "1.19.0" }
}
```

### 3. Start Elasticsearch Transform

```json
POST _transform/logs-elasticsearch.index_pivot-default-0.3.0/_start
```

Wait for `monitoring-indices` to populate:

```json
GET monitoring-indices/_count
```

### 4. Install On-Premises Billing Integration

```bash
cd packages/onprem_billing && elastic-package build && elastic-package install
```

---

## Testing Workarounds

### Issue 1: Single-Cluster CCS Pattern

**Problem**: The transforms use `*:monitoring-indices` which is a Cross-Cluster Search (CCS) pattern that only searches **remote clusters**, not the local cluster.

**Error**:
```
No clusters exist for [*:monitoring-indices]
```

**Solution**: The package was updated to use both patterns:
```yaml
source:
  index:
    - "monitoring-indices"      # Local cluster
    - "*:monitoring-indices"    # Remote clusters (CCS)
```

This change is now in the package source and works for both single-cluster testing and production CCS setups.

### Issue 2: Daily Aggregation Timing

**Problem**: The billing transform uses `calendar_interval: 1d` with `delay: 1h`. It only produces output for **complete days** that ended more than 1 hour ago. Testing today's data produces no output.

**Workaround for testing**:

1. **Reduce delay temporarily**:
```json
POST _transform/logs-onprem_billing.billing-default-0.1.0/_update
{
  "sync": {
    "time": {
      "field": "@timestamp",
      "delay": "1m"
    }
  }
}
```

2. **Add test data with yesterday's timestamp**:
```json
POST monitoring-indices/_doc
{
  "@timestamp": "2026-01-27T12:00:00.000Z",
  "elasticsearch": {
    "cluster": { "name": "elasticsearch" }
  }
}
```

3. **Reset and restart transform** to reprocess:
```json
POST _transform/logs-onprem_billing.billing-default-0.1.0/_stop
POST _transform/logs-onprem_billing.billing-default-0.1.0/_reset
POST _transform/logs-onprem_billing.billing-default-0.1.0/_start
```

### Issue 3: Config Bootstrap Not Discovering Deployments

**Problem**: The config_bootstrap transform may need a reset to pick up data.

**Workaround**:
```json
POST _transform/logs-onprem_billing.config_bootstrap-default-0.1.0/_stop
POST _transform/logs-onprem_billing.config_bootstrap-default-0.1.0/_reset
POST _transform/logs-onprem_billing.config_bootstrap-default-0.1.0/_start
```

---

## Chargeback Integration Testing

### Issue 4: Chargeback billing_cluster_cost Uses event.ingested

**Problem**: The Chargeback `billing_cluster_cost` transform syncs on `event.ingested` with a delay. Test data may not have proper `event.ingested` timestamps.

**Workaround for testing**:
```json
POST _transform/logs-chargeback.billing_cluster_cost-default-0.2.5/_stop

POST _transform/logs-chargeback.billing_cluster_cost-default-0.2.5/_update
{
  "sync": {
    "time": {
      "field": "@timestamp",
      "delay": "1m"
    }
  }
}

POST _transform/logs-chargeback.billing_cluster_cost-default-0.2.5/_reset
POST _transform/logs-chargeback.billing_cluster_cost-default-0.2.5/_start
```

### Issue 5: Kibana Version Compatibility

**Problem**: Chargeback 0.2.10 dashboard uses Kibana features (`sections`, `typeMigrationVersion: 10.3.0`) not available in Kibana 9.0.0.

**Solution**: Use Elastic Stack 9.2.0+ for testing.

---

## Complete Test Flow

1. Start stack with 9.2.0
2. Install Elasticsearch integration, enable agent-based monitoring
3. Start ES transform, wait for `monitoring-indices`
4. Install On-Prem Billing integration
5. Wait for config_bootstrap to discover deployments (reset if needed)
6. Configure deployment in `onprem_billing_config` (name, daily_ecu, tags)
7. Create enrich policy and execute
8. Create `calculate_cost` ingest pipeline
9. Update billing transform to use pipeline
10. Reduce delay and add yesterday's test data
11. Start billing transform
12. Verify `metrics-ess_billing.billing-onprem` has data
13. Install Chargeback integration
14. Update `billing_cluster_cost` transform sync field for testing
15. Verify `billing_cluster_cost_lookup` has data with `deployment_group`

## Expected Final Output

**metrics-ess_billing.billing-onprem**:
```json
{
  "@timestamp": "2026-01-27T00:00:00.000Z",
  "ess.billing": {
    "deployment_id": "elasticsearch",
    "deployment_name": "Production Cluster",
    "deployment_tags": ["chargeback_group:platform_team"],
    "total_ecu": 500.0,
    "sku": "onprem-daily-elasticsearch",
    "type": "capacity",
    "kind": "elasticsearch",
    "deployment_type": "onprem"
  }
}
```

**billing_cluster_cost_lookup** (Chargeback output):
```json
{
  "@timestamp": "2026-01-27T00:00:00.000Z",
  "deployment_id": "elasticsearch",
  "deployment_name": "Production Cluster",
  "deployment_group": "platform_team",
  "total_ecu": 500.0,
  "sku": "onprem-daily-elasticsearch",
  "cost_type": "daily"
}
```
