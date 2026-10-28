# Chargeback Serverless — configuration (ECH model)

## Principle

Day-2 configuration is **Fleet Integration Settings** (Organization ID, Billing API key, Usage API key) + **Configuration dashboard** + **Kibana Workflows**. Collection does not need an Elastic Agent. For custom zip installs, use the default Fleet deployment mode so Settings can be saved (Fleet rejects agentless for upload packages). The shell installer is optional bootstrap only.

## Credentials (Integration UI)

Set these in **Integrations → Chargeback Serverless → Settings**:

| Field | Secret | Stored in |
| --- | --- | --- |
| Organization ID | No | Package policy | Elastic Cloud organization identifier |
| Billing API key | Yes | Package policy (encrypted) | Cloud Billing API reader |
| Usage API key | Yes | Package policy (encrypted) | Cloud API key to list projects across the org |

Workflows read `chargeback-serverless-config`. After changing Integration settings, upsert the same values into that document (via bootstrap script or Dev Tools).

## Harvesting Remote Projects: Least-Privileged API Keys

In Elastic Cloud Serverless, a Cloud Organization API key can list projects via the Control Plane (`api.elastic-cloud.com`), but **cannot** authenticate against the Elasticsearch Data Plane (`https://<project-id>.es.<region>.<csp>.elastic.cloud/`) of remote projects across regions or separate clusters.

* **Billing data** (spend per project, SKU breakdown) works with **zero** remote project keys.
* **Granular datastream / index allocation** on remote projects requires an Elasticsearch API key with strictly least-privileged permissions.

### 1. Principle of Least Privilege

The key created on remote projects requires **only cluster and index metadata permissions**. It has **no access to documents, indices data, or search**:

* **Cluster:** `monitor` (allows reading `_cat/indices` metrics: shard sizes, indexing time, query time)
* **Indices:** `monitor` on `*`
* **Data:** *None* (cannot search, query, read, write, or delete documents)

Even if an unauthorized user inspects the workflow config document or execution history, this key cannot access any business data.

### 2. Generate the Key on Remote Project(s)

In the Kibana Dev Tools of each remote project (e.g., `telco`):

```json
POST _security/api_key
{
  "name": "chargeback-usage-harvesting",
  "role_descriptors": {
    "chargeback_usage": {
      "cluster": ["monitor"],
      "indices": [
        {
          "names": ["*"],
          "privileges": ["monitor"]
        }
      ]
    }
  }
}
```

Copy the resulting `encoded` API key string.

### 3. Register the Key on the Hub Project

In Dev Tools on your **Hub project**, update `chargeback-serverless-config` using `_update` to avoid replacing other fields:

```json
POST chargeback-serverless-config/_update/config
{
  "doc": {
    "project_api_keys": {
      "<REMOTE_PROJECT_ID>": "<ENCODED_API_KEY>"
    }
  }
}
```

*Example for project `bf694be853c246c0b5304648ec385fb4` (`telco`):*
```json
POST chargeback-serverless-config/_update/config
{
  "doc": {
    "project_api_keys": {
      "bf694be853c246c0b5304648ec385fb4": "<ENCODED_API_KEY>"
    }
  }
}
```

### 4. Optional Project Filtering

If you have test, staging, or transient projects that should not have their usage harvested:
* `include_project_ids`: List of project IDs to harvest (leave empty to harvest all discovered projects).
* `exclude_project_ids`: List of project IDs to skip from data stream harvesting.

```json
POST chargeback-serverless-config/_update/config
{
  "doc": {
    "exclude_project_ids": ["bf694be853c246c0b5304648ec385fb4"]
  }
}
```

## Security Best Practice: Restricting Access (RBAC)

Because `chargeback-serverless-config` stores operational API keys:

1. **FinOps Analysts / Dashboard Consumers:**
   * Grant `read` on lookup indices: `billing_*_lookup`, `cluster_*_lookup`, `chargeback_conf_lookup`.
   * **Do not grant access** to `chargeback-serverless-config` or `chargeback-serverless-state`.
2. **Kibana Workflows:**
   * Restrict access to **Stack Management → Workflows** to workspace administrators so non-admin users cannot view workflow execution run history.

## Rates / local currency

Edit `chargeback_conf_lookup` from the Configuration dashboard:

- `conf_chargeable_unit_rate`
- `conf_chargeable_unit_rate_unit`
- `conf_start_date` / `conf_end_date`

All spend visuals use:

```
LOOKUP JOIN chargeback_conf_lookup ON ts >= conf_start_date AND ts <= conf_end_date
| EVAL cost = total_chargeable_units * COALESCE(conf_chargeable_unit_rate, 1.0)
```

## Projects (not deployments)

Serverless billable units are **projects**. Dashboards and docs say project. Index mappings expose `project_*` aliases over the billing API’s `deployment_*` field names for compatibility.

## Custom Liquid panels

Custom panels work on Serverless hubs and ship in the package (KPI strips, allocation flow, harvest status, allocation table).
