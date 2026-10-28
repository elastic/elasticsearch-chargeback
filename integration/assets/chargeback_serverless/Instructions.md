# Chargeback Serverless — setup

Install on a **Serverless hub** (Observability Complete recommended). Do not install on a Hosted monitoring cluster; use the Hosted `chargeback` package and the [`../serverless/`](../serverless/) add-on instead.

## Prerequisites

- Hub project with Fleet Integrations and Kibana Workflows
- Fleet package `chargeback_serverless` **0.1.6+**
- See [CONFIG.md](CONFIG.md) for day-2 configuration (Integration settings + Configuration dashboard + Workflows)
- Elastic Cloud organization ID
- Billing and usage Cloud API keys (set in Integration settings)

## Install

### 1. Fleet package

Upload the zip (Content-Type `application/zip`):

```bash
curl -X POST "${KIBANA_URL}/api/fleet/epm/packages" \
  -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
  -H "kbn-xsrf: true" \
  -H "elastic-api-version: 2023-10-31" \
  -H "Content-Type: application/zip" \
  --data-binary @dist/chargeback_serverless-0.1.6.zip
```

### 2. Integration settings (credentials)

**Integrations → Chargeback Serverless → Add / Settings:**

- Organization ID
- Billing API key (secret)
- Usage API key (secret)

### 3. Indices, config sync, and workflows

Bootstrap indices and import Workflows; sync Integration credentials into `chargeback-serverless-config`:

```bash
export ES_URL=...
export ES_API_KEY=...
export KIBANA_URL=...
export KIBANA_API_KEY=...
export ORGANIZATION_ID=...      # same as Integration setting
export BILLING_API_KEY=...      # same as Integration setting
export USAGE_API_KEY=...        # same as Integration setting
export HUB_ES_API_KEY=...

./scripts/install_chargeback_serverless.sh
```

To harvest `_cat/indices` from projects outside this hub, add per-project Elasticsearch API keys under `project_api_keys` (map of `project_id -> api_key`) in `chargeback-serverless-config/_doc/config`.

### 4. Rates / local currency

On **[Chargeback Serverless] Configuration**, set `conf_chargeable_unit_rate` and `conf_chargeable_unit_rate_unit` in `chargeback_conf_lookup`. All spend visuals use that conversion.

## Dashboards

Links bar navigates between:

- **Billing Components Overview**
- **Usage & Cost Allocation**
- **Configuration**

Filter tag **Chargeback**. Wording is **project** (Serverless), not deployment.

## Verify

1. Run **Chargeback Serverless - Billing**, then check `GET billing_cluster_cost_lookup/_count` and `GET billing_realized_pool_lookup/_count`
2. Run **Chargeback Serverless - Usage**, then check `chargeback-serverless-projects`, `chargeback-serverless-state`, and `cluster_datastream_contribution_lookup`
3. Confirm Links between the three dashboards work
4. Confirm spend panels show values in your configured currency unit

## Known limitations

- Remote project `_cat/indices` needs a per-project Elasticsearch API key (`project_api_keys[project_id]`) with `monitor` and `view_index_metadata` privileges
- Indexing time is a proxy for Observability and Security ingest GB
