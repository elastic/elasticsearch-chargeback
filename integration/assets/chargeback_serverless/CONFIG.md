# Chargeback Serverless — Configuration Guide

## Principle

All configuration for Chargeback Serverless is managed **100% within the Fleet Integration Policy** (matching the native `ess_billing` architecture).

- **Zero secrets in indices**: API keys are securely encrypted by Fleet and never written to plaintext Elasticsearch indices.
- **Zero workflows**: Scheduled collection runs automatically via native Elastic Agent CEL streams (`billing` and `usage` polling every 1h, advancing once daily after 02:00 UTC for completed UTC days up to yesterday `D-1`, with an initial backfill up to 60 days).
- **Single pane of glass**: Configure organization credentials, remote project keys, backfill windows, and currency rates directly in **Fleet → Agent Policies → Chargeback Serverless** or via `./scripts/deploy_chargeback_serverless.sh`.

---

## Policy Configuration Settings

Set these parameters in **Kibana → Management → Fleet → Integrations → Chargeback Serverless**:

| Variable | Type | Secret | Default | Description |
|---|---|---|---|---|
| `organization_id` | Text | No | *(required)* | Elastic Cloud organization ID (from Cloud Console → Organization). |
| `cloud_api_key` | Password | **Yes** | *(required)* | Cloud API key with billing read and Serverless project read access. |
| `hub_es_api_key` | Password | **Yes** | *(optional)* | Elasticsearch API key for the local Hub project (if harvesting local data streams). |
| `project_api_keys` | Password (Multi) | **Yes** | `[]` | List of remote project harvesting keys in `<project_id>:<encoded_api_key>` format. |
| `lookbehind` | Integer | No | `60` | Number of historical days of billing data to backfill on initial install. |
| `currency_unit` | Text | No | `USD` | Target display currency unit code (e.g. `USD`, `EUR`, `ZAR`, `GBP`, `ECU`). |
| `currency_symbol` | Text | No | `$` | Target display currency symbol (e.g. `$`, `€`, `R`, `£`). |
| `unit_rate` | Text | No | `1.0` | Currency conversion rate per ECU (e.g. `1.0` for 1:1, or `18.5` for local currency). |
| `billing_api_base` | Text | No | `https://billing.elastic-cloud.com` | Base URL of Elastic Cloud Billing API. |
| `cloud_api_base` | Text | No | `https://api.elastic-cloud.com` | Base URL of Elastic Cloud Control Plane API. |

---

## Harvesting Remote Projects: Least-Privileged API Keys

In Elastic Cloud Serverless, a Cloud Organization API key can discover projects via the Control Plane (`api.elastic-cloud.com`), but **cannot** authenticate against the Elasticsearch Data Plane (`https://<project-id>.es.<region>.<csp>.elastic.cloud/`) of remote projects across regions or separate clusters.

* **Billing data** (spend per project, SKU breakdown) works with **zero** remote project keys.
* **Granular data stream & index allocation** on remote projects requires an Elasticsearch API key with strictly least-privileged permissions.

### 1. Principle of Least Privilege

The key created on remote projects requires **only cluster and index metadata permissions**. It has **no access to documents, indices data, or search**:

* **Cluster:** `monitor` (allows reading `_cat/indices` metrics: shard sizes, indexing time, query time, and ML stats)
* **Indices:** `monitor` on `*`
* **Data:** *None* (cannot search, query, read, write, or delete documents)

### 2. Generate the Key on Remote Project(s)

In the Kibana Dev Tools of each remote project (e.g., `telco-analytics`):

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

### 3. Register the Keys in Fleet Integration Policy

In **Kibana Fleet UI**:
1. Navigate to **Fleet → Agent Policies → your policy → Chargeback Serverless**.
2. Locate **Remote Project Elasticsearch API Keys**:
   - **Adding for the first time:** Paste `<remote_project_id>:<encoded_api_key>` in the input field.
   - **Adding multiple remote projects:** Click **Add item** (or **+**) to create an additional row for each remote project:
     ```text
     <remote_project_id_1>:<encoded_api_key_1>
     <remote_project_id_2>:<encoded_api_key_2>
     ```
     *Example:*
     ```text
     bf694be853c246c0b5304648ec385fb4:VjFSa0Jad0J4...
     e3b0c44298fc1c149afbf4c8996fb924:YmFzZTY0a2V5...
     ```
   - **Adding more keys later (Policy Secrets):**  
     Because this field is stored as an encrypted Fleet Policy Secret (`type: password`), saved values are masked with:
     > *"The saved remote project elasticsearch api keys is hidden. You can only replace the remote project elasticsearch api keys."*  
     
     Click **↺ Replace remote project elasticsearch api keys** to open editable rows.  
     > [!IMPORTANT]
     > Fleet Policy Secrets are write-only for security and cannot be read back by Kibana. Clicking **Replace** overwrites the stored secret list. When adding a new key to an existing deployment, you must enter **all** keys you want to retain (both existing and new) before saving.
3. Click **Save integration**. Fleet encrypts the keys and pushes the configuration immediately to the agent collector.

Alternatively, register via CLI:
```bash
PROJECT_ID="bf694be853c246c0b5304648ec385fb4" \
SL_API_KEY="VjFSa0Jad0J4..." \
./scripts/configure_chargeback_serverless.sh
```

---

## Currency & Rate Multipliers

To display all billing and usage metrics in your local accounting currency:
1. In the integration policy settings, set:
   - **Currency Unit:** e.g., `ZAR`, `EUR`, `GBP`
   - **Currency Symbol:** e.g., `R`, `€`, `£`
   - **Chargeable Unit Rate:** e.g., `18.5` (1 ECU = 18.50 ZAR)
2. All dashboard calculations compute spend as:
   ```
   cost = total_chargeable_units * unit_rate
   ```
   with the appropriate currency symbol prepended.

---

## Data Streams & Retention

Chargeback Serverless produces two native metrics data streams:

1. `metrics-chargeback_serverless.billing-default`:
   - Daily billing events per project and SKU.
   - Run interval: Polling check every 1 hour (advancing once per completed UTC day, with automatic initial backfill up to `lookbehind` days).
2. `metrics-chargeback_serverless.usage-default`:
   - Daily usage events per project, data stream, and AI model.
   - Run interval: Polling check every 1 hour (harvesting once per completed UTC day).

No manual index management jobs or manual mapping templates are required. Fleet automatically creates and manages index templates, component templates, transforms, and lookup indices.
