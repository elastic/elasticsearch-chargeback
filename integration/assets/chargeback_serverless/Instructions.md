# Chargeback Serverless — Setup & Installation Guide

Install on an **Elastic Cloud Serverless hub** (Observability Complete recommended). Do not install on a Hosted monitoring cluster; use the Hosted `chargeback` package for Hosted Elasticsearch clusters.

---

## Prerequisites

- Serverless hub project with Fleet Integrations enabled.
- Fleet package `chargeback_serverless` **0.3.4+**.
- Elastic Cloud Organization ID (from Elastic Cloud Console → Organization).
- Elastic Cloud API key with billing read access and Serverless project read access.
- See [CONFIG.md](CONFIG.md) for detailed configuration options.

---

## Installation

### Method A: Automated E2E Deployment (Recommended)

The deployment script automates package building, dashboard compilation, Fleet package upload, and policy configuration in a single command:

```bash
# Deploys latest build using macOS Keychain or interactive prompts:
./scripts/deploy_chargeback_serverless.sh

# Useful options:
./scripts/deploy_chargeback_serverless.sh --skip-build    # Deploy existing package zip
./scripts/deploy_chargeback_serverless.sh --skip-policy   # Upload package without creating/updating policy
./scripts/deploy_chargeback_serverless.sh --verify-only   # Run health check & verify data stream ingestion
```

The script will:
1. Rebuild and package `chargeback_serverless-0.3.4.zip` using `elastic-package`.
2. Upload and activate the package in Kibana Fleet EPM (`/api/fleet/epm/packages`).
3. Automatically create or update the Fleet package policy with your credentials and settings.
4. Verify data stream health and print dashboard URLs.

---

### Method B: Manual Fleet UI Installation

1. **Upload Package Archive:**
   In your terminal, upload the built zip to the Kibana Fleet EPM API:
   ```bash
   curl -X POST "${KIBANA_URL}/api/fleet/epm/packages" \
     -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
     -H "kbn-xsrf: true" \
     -H "elastic-api-version: 2023-10-31" \
     -H "Content-Type: application/zip" \
     --data-binary @dist/chargeback_serverless-0.3.4.zip
   ```

2. **Add Integration in Fleet:**
   - In Kibana, open **Management → Fleet → Integrations**.
   - Search for **Chargeback Serverless** and click **Add Chargeback Serverless**.
   - Fill in:
     - **Organization ID:** Your Elastic Cloud Organization ID.
     - **Elastic Cloud API Key:** Cloud API key with billing and project read access.
     - **Currency Unit / Symbol / Rate:** (e.g. `USD`, `$`, `1.0` or local currency).
     - *(Optional)* **Remote Project Elasticsearch API Keys:** Least-privileged keys for remote projects.
   - Select your target Agent Policy and click **Save and continue**.

Collection starts automatically via Elastic Agent CEL on the configured schedule. No Kibana Workflows or external cron jobs are required.

---

## Harvesting Remote Projects (Optional)

Billing data (spend per project, SKU breakdown) works with **zero** remote project keys.

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

2. **Register the key(s) in the Fleet Integration Policy**:
   - In **Fleet → Agent Policies → your policy → Chargeback Serverless**, enter `<project_id>:<encoded_api_key>` into **Remote Project Elasticsearch API Keys**.
   - **Multiple remote projects:** Click **Add item** (or **+**) to add rows for additional projects.
   - **Adding keys later:** If keys are already saved, Fleet hides them as policy secrets. Click **↺ Replace remote project elasticsearch api keys** and re-enter **all** keys to retain (both existing and new) before saving.
   - Or run:
     ```bash
     PROJECT_ID="<remote_project_id>" SL_API_KEY="<encoded_api_key>" ./scripts/configure_chargeback_serverless.sh
     ```

---

## Dashboards

Navigate to **Analytics → Dashboards** and search for `Chargeback Serverless`:

- **[Chargeback Serverless] Billing Components Overview**:
  Top-level spend, burn rate, SKU breakdowns, and project cost allocations.
- **[Chargeback Serverless] Usage & Cost Allocation**:
  Attributed cost partitioned by Namespace (`ds_namespace`) and Data Stream Type (`ds_type`), with data stream tables and ECU pools.
- **[Chargeback Serverless] Configuration**:
  Fleet Settings reference guide, remote project key generator, and environment status.

---

## Verification

Check document counts in the native metrics data streams:

```json
GET metrics-chargeback_serverless.billing-*/_count
GET metrics-chargeback_serverless.usage-*/_count
```

Inspect recent ingestion with ES|QL:

```json
POST /_query
{
  "query": "FROM metrics-chargeback_serverless.billing-* | STATS billing_records = count(), latest_bill = MAX(@timestamp) | KEEP billing_records, latest_bill"
}
```

```json
POST /_query
{
  "query": "FROM metrics-chargeback_serverless.usage-* | STATS usage_records = count(), projects_monitored = COUNT_DISTINCT(project_name), latest_usage = MAX(@timestamp) | KEEP usage_records, projects_monitored, latest_usage"
}
```

---

## Security Architecture Notes

- **Zero Plaintext Secrets in Indices:** Credentials and API keys are stored exclusively in Fleet package policies, encrypted by Kibana, and redacted from logs.
- **Least-Privilege Isolation:** Remote project keys only require the `monitor` cluster privilege to inspect index sizes and indexing/query times; they cannot read or query documents.
- **No Ingestion Workflows:** Native Elastic Agent CEL streams perform HTTP polling directly on schedule, eliminating workflow execution overhead.
