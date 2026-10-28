# Chargeback Serverless — setup

Install on a **Serverless hub** (Observability Complete recommended). Do not install on a Hosted monitoring cluster; use the Hosted `chargeback` package and the [`../serverless/`](../serverless/) add-on instead.

## Prerequisites

- Hub project with Fleet Integrations and Kibana Workflows
- Fleet package `chargeback_serverless` **0.1.6+**
- See [CONFIG.md](CONFIG.md) for day-2 configuration (Integration settings + Configuration dashboard + Workflows)
- Elastic Cloud organization ID
- Billing and usage Cloud API keys (set in Integration settings)

## Install

### Automated E2E Deployment (One-Command Deploy)

Builds package with `elastic-package`, loads credentials from macOS Keychain or prompts interactively, uploads to Kibana Fleet EPM, installs infrastructure, syncs configuration, and executes workflows:

```bash
# Deploys latest build to test/target environment using macOS Keychain or interactive prompts
./scripts/deploy_chargeback_serverless.sh

# Options:
./scripts/deploy_chargeback_serverless.sh --skip-build       # deploy existing package
./scripts/deploy_chargeback_serverless.sh --verify-only      # check indices & health
```

### Manual / Step-by-Step Install

#### 1. Fleet package

Upload the zip (Content-Type `application/zip`):

```bash
curl -X POST "${KIBANA_URL}/api/fleet/epm/packages" \
  -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
  -H "kbn-xsrf: true" \
  -H "elastic-api-version: 2023-10-31" \
  -H "Content-Type: application/zip" \
  --data-binary @dist/chargeback_serverless-0.1.6.zip
```

#### 2. Infrastructure & Workflows Install

Bootstrap indices, classification pipeline, and Kibana Workflows:

```bash
./scripts/install_chargeback_serverless.sh
```

#### 3. Integration Configuration & Credentials

Sync credentials, remote harvesting keys, and currency rates into `chargeback-serverless-config` and `chargeback_conf_lookup`:

```bash
./scripts/configure_chargeback_serverless.sh
```

### 4. Remote Projects Usage Harvesting (Least-Privileged Keys)

To break down usage per data stream across projects outside this hub:

1. **Generate a least-privileged key on the remote project** in its Kibana Dev Tools:
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
   *(This key only grants permission to read `_cat/indices` metadata — it cannot read, search, or modify any data).*

2. **Register the key on your Hub project** in Dev Tools:
   ```json
   POST chargeback-serverless-config/_update/config
   {
     "doc": {
       "project_api_keys": {
         "<REMOTE_PROJECT_ID>": "<ENCODED_API_KEY_FROM_STEP_1>"
       }
     }
   }
   ```

### 5. Rates / local currency

On **[Chargeback Serverless] Configuration**, set `conf_chargeable_unit_rate` and `conf_chargeable_unit_rate_unit` in `chargeback_conf_lookup`. All spend visuals use that conversion.

## Dashboards

Links bar navigates between:

- **Billing Components Overview**
- **Usage & Cost Allocation**
- **Configuration**

Filter tag **Chargeback**. Wording is **project** (Serverless), not deployment.

## Verify

1. Run **Chargeback Serverless - Billing**, then check:
   ```json
   GET billing_cluster_cost_lookup/_count
   GET billing_realized_pool_lookup/_count
   ```
2. Run **Chargeback Serverless - Usage**, then check project status:
   ```json
   GET chargeback-serverless-projects/_search
   GET chargeback-serverless-state/_search
   GET cluster_datastream_contribution_lookup/_count
   ```
   * Ensure `last_status` is `"ok"` for all projects being harvested (if a remote project shows `"empty_or_unauthorized"`, check that its least-privileged key was registered in `project_api_keys`).
3. Confirm Links between the three dashboards work.
4. Confirm spend panels show values in your configured currency unit.
5. Confirm Usage & Cost Allocation shows Attributed Cost partitioned by Namespace (`ds_namespace`) and by Data Stream Type (`ds_type`), with matching breakdown columns in the data stream tables.

## Security & Architecture Notes

- Remote project `_cat/indices` requires a per-project Elasticsearch API key (`project_api_keys[project_id]`) with `monitor` privileges (cluster `monitor` and index `monitor` on `*`).
- Restrict read access to `chargeback-serverless-config` using Kibana RBAC so standard dashboard users cannot inspect configuration credentials.
- Indexing time is a proxy for Observability and Security ingest GB.
