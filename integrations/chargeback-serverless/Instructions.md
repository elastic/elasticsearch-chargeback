# Install and configure Chargeback Serverless

Deploy the **Chargeback Serverless (`chargeback_serverless` `0.3.15`)** integration on an Elastic Cloud Serverless project to collect organization-wide billing history (including deleted projects and deployments) and per-project data stream, AI model, and Synthetics usage telemetry.

## Before you begin

Make sure you have the following before installing Chargeback Serverless:

- **Target Elastic Cloud Serverless hub project**: An Elastic Cloud Serverless project (`elasticsearch`, `observability`, or `security`) where the dashboards, lookup indices, and Fleet policy will run.
- **Elastic Cloud Organization ID**: Your numeric organization ID from the [Elastic Cloud Console](https://cloud.elastic.co).
- **Single unified Elastic Cloud API key**: Created in the [Elastic Cloud Console API keys page](https://cloud.elastic.co/deployment-features/keys) with both of these roles turned on:
  - **Billing admin** (required to query organization billing costs and `/api/v1/billing/organization/{org_id}/history` for deleted deployments and projects).
  - **Cloud resource access** set to **All resources** with **`Cloud, Elasticsearch, and Kibana API`** selected (required to list active Serverless projects and harvest per-project index, AI, and Synthetics telemetry).
- **Elastic Agent enrolled in Fleet** (for scheduled Elastic Agent collection) or Python `3.9+` (for running the standalone collector via [`scripts/deploy_chargeback_serverless.sh`](../../../scripts/deploy_chargeback_serverless.sh)).

---

## Option A: Automated deployment with `deploy_chargeback_serverless.sh` (recommended)

The [`scripts/deploy_chargeback_serverless.sh`](../../../scripts/deploy_chargeback_serverless.sh) script automates credential lookup from macOS Keychain, package build and upload, Fleet integration policy configuration, initial billing and usage backfill, transform execution, alerting rule installation, and verification.

### Step 1: Store credentials in macOS Keychain

From the repository root, store your organization ID, unified Elastic Cloud API key, and target hub endpoints in the macOS Keychain (`elasticsearch-chargeback` service):

```bash
./scripts/keychain.sh set ORGANIZATION_ID 2001809318
./scripts/keychain.sh set CLOUD_API_KEY
./scripts/keychain.sh set ES_URL "https://<your-hub-project>.es.<region>.<csp>.elastic.cloud"
./scripts/keychain.sh set KIBANA_URL "https://<your-hub-project>.kb.<region>.<csp>.elastic.cloud"
```

### Step 2: Run the deployment script

Run the end-to-end deployment script from the repository root:

```bash
# Full build, package upload, policy update, collector backfill, and verification
./scripts/deploy_chargeback_serverless.sh

# Or skip rebuilding the package zip when using dist/chargeback_serverless-0.3.15.zip
./scripts/deploy_chargeback_serverless.sh --skip-build
```

When you update only your `CLOUD_API_KEY` and want to update the Fleet policy and re-run the collector backfill without rebuilding or re-uploading the package `.zip`:

```bash
./scripts/deploy_chargeback_serverless.sh --skip-build
```

---

## Option B: Manual installation in Kibana Fleet

Use these steps to upload and configure [`dist/chargeback_serverless-0.3.15.zip`](dist/chargeback_serverless-0.3.15.zip) directly through the Kibana UI.

### Step 1: Upload `chargeback_serverless-0.3.15.zip` in Kibana

1. In Kibana on your Serverless hub project, open the **Navigation menu**, go to **Management → Integrations**, and select **Create new integration**.
2. Select **Upload it as a .zip**, choose [`integrations/chargeback-serverless/assets/dist/chargeback_serverless-0.3.15.zip`](assets/dist/chargeback_serverless-0.3.15.zip), and click **Upload**.

### Step 2: Add the integration to an Elastic Agent policy

1. In **Management → Integrations**, search for `Chargeback Serverless` and select **Chargeback Serverless**.
2. Click **Add Chargeback Serverless**.
3. Enter your configuration values:
   - **Elastic Cloud Organization ID (`organization_id`)**: Enter your numeric Elastic Cloud organization ID.
   - **Elastic Cloud API Key (`cloud_api_key`)**: Paste your unified Elastic Cloud API key (configured with **Billing admin** and **Cloud resource access** → **`Cloud, Elasticsearch, and Kibana API`**).
   - **Collection Interval (`collection_interval`)**: Keep the default `24h`.
   - **Initial Billing Lookbehind Days (`initial_lookbehind_days`)**: Keep the default `60` (backfills up to 60 completed UTC days on first run).
4. Select your target **Agent policy** and click **Save and continue**.

### Step 3: Seed the currency conversion rate (`serverless_chargeback_conf_lookup`)

Open **Management → Dev Tools** in Kibana and seed your chargeable unit conversion rate and currency code:

```json
PUT serverless_chargeback_conf_lookup/_doc/config_default
{
  "conf_key": "1",
  "conf_chargeable_unit_rate": 0.85,
  "conf_chargeable_unit_rate_unit": "EUR",
  "conf_start_date": "2010-01-01T00:00:00.000Z",
  "conf_end_date": "2046-12-31T23:59:59.999Z"
}
```

### Step 4: Verify transforms, lookup indices, and project harvest status

1. Go to **Stack Management → Transforms** and filter by `serverless_`. Confirm that all seven transforms are **Started** with `health: green`:
   - `serverless_billing_cost-0.3.15`
   - `serverless_billing_realized_pool-0.3.15`
   - `serverless_project_contribution-0.3.15`
   - `serverless_datastream_contribution-0.3.15`
   - `serverless_ai_model_contribution-0.3.15`
   - `serverless_synthetics_monitor_contribution-0.3.15`
   - `serverless_project_status-0.3.15`
2. Open **Management → Dev Tools** and run the following Elasticsearch Query Language (ES|QL) query to verify project harvest status:

   ```json
   POST _query?format=txt
   {
     "query": "FROM serverless_project_status_lookup | SORT @timestamp DESC | LIMIT 25"
   }
   ```

   Confirm that each active Serverless project displays `status: "ok"` and `key_type: "cloud_key"`.

3. Open **Dashboards** and select **`[Chargeback Serverless] Billing Components Overview`** to review your organization invoice breakdown across active and deleted projects.

## Next steps

- Review the [Chargeback Serverless configuration reference](CONFIG.md) to set up multiple historical rate windows or customize the 10 FinOps ES|QL alerting rules.
- See [Troubleshoot the Chargeback integration](../../docs/troubleshooting.md) if any project shows an authentication warning or missing billing history.
