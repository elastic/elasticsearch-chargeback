# Install and configure Chargeback Serverless

Deploy the **Chargeback Serverless (`chargeback_serverless` `0.4.0`)** integration on an Elastic Cloud Serverless project to collect organization-wide Serverless billing history (including deleted projects) and per-project data stream, index, AI model, and Synthetics usage telemetry.

> [!IMPORTANT]
> **Customer Installation vs. Developer / Maintainer Workflows**
> - **Customers & FinOps Operators (Part 1)**: You do **not** need the `elastic/integrations` repository, `elastic-package`, or macOS-specific scripts. Install the prebuilt package [`assets/dist/chargeback_serverless-0.4.0.zip`](assets/dist/chargeback_serverless-0.4.0.zip) directly through the Kibana Fleet UI, pre-create the lookup index templates in Kibana Dev Tools, and optionally run the cross-platform Python backfill collector (`python3 scripts/run_chargeback_serverless_collector.py`).
> - **Developers & Maintainers (Part 2)**: Use the automated deployment script (`./scripts/deploy_chargeback_serverless.sh`) to provision templates, transforms, policies, backfills, and E2E verification from the command line (or pass `--build` when modifying package source templates in `elastic/integrations`).

## Before you begin

Make sure you have the following before installing Chargeback Serverless:

1. **Target Elastic Cloud Serverless hub project**:
   - An Elastic Cloud Serverless project (`elasticsearch`, `observability`, or `security`) where the dashboards, `LOOKUP`-mode indices, transforms, and Fleet policy will run.
   - Note your project's **Elasticsearch endpoint (`ES_URL`)** and **Kibana endpoint (`KIBANA_URL`)**.
2. **Elastic Cloud Organization ID (`ORGANIZATION_ID`)**:
   - Your numeric organization ID (`<YOUR_ORGANIZATION_ID>`) from the [Elastic Cloud Console](https://cloud.elastic.co).
3. **Single unified Elastic Cloud API key (`CLOUD_API_KEY`)**:
   - One Elastic Cloud API key configured with **Billing Admin** and **Cloud, Elasticsearch, and Kibana API** access across your Serverless projects (see Step 1 below).

---

## Part 1: Customer Installation Guide (Kibana Fleet UI)

Follow these steps to install and configure Chargeback Serverless through Kibana Fleet on any operating system.

### Step 1: Create Unified Elastic Cloud API Key

Create a single unified Elastic Cloud API key (`cloud_api_key`) in the [Elastic Cloud Console API keys page](https://cloud.elastic.co/deployment-features/keys) (**Organization → API keys → Create API key**):

1. **Organization role**: Select **`Billing Admin`** (required to query organization billing costs via `GET /api/v2/billing/organizations/{org_id}/costs/instances`, including deleted projects).
2. **Enable access to Cloud resources**: Toggle **ON**, select **All projects** (or the specific Serverless projects you want to monitor), and in the per-project-type **Roles / API Access** dropdowns grant **`Cloud, Elasticsearch, and Kibana API`** (`Admin`) across **`Elasticsearch`**, **`Observability`**, and **`Security`** project types so the key can discover Serverless projects and harvest `GET /_cat/indices`, `_ml/trained_models/_stats`, `_inference`, and `.ds-synthetics-*` telemetry.
3. Click **Create API key** and copy the key value.

### Step 2: Pre-Create Lookup Index Templates (Required for Fleet-Only Installations)

Chargeback Serverless dashboards and alerting rules use ES|QL `LOOKUP JOIN`, which requires the three right-hand lookup indices (`serverless_project_status_lookup`, `serverless_billing_realized_pool_lookup`, and `serverless_project_contribution_lookup`) to be created with `"index.mode": "lookup"` and their corresponding `default_pipeline` (`logs-chargeback_serverless.lookup-0.4.0-billing`, `logs-chargeback_serverless.lookup-0.4.0-realized_pool`, and `logs-chargeback_serverless.lookup-0.4.0-usage`) **before** the Fleet transforms first run and auto-create their destination indices.

In Kibana on your Serverless hub project, open **Management → Dev Tools** and run the following requests before uploading the integration package:

```json
PUT _index_template/chargeback_serverless_lookup_billing_cost
{
  "index_patterns": ["serverless_billing_cost_lookup"],
  "priority": 500,
  "template": {
    "settings": {
      "index": {
        "mode": "lookup",
        "codec": "best_compression",
        "default_pipeline": "logs-chargeback_serverless.lookup-0.4.0-billing"
      }
    },
    "mappings": {
      "dynamic": false,
      "properties": {
        "@timestamp": { "type": "date" },
        "composite_key": { "type": "keyword" },
        "project_id": { "type": "keyword" },
        "project_name": { "type": "keyword" },
        "project_type": { "type": "keyword" },
        "sku": { "type": "keyword" },
        "dimension": { "type": "keyword" },
        "unit": { "type": "keyword" },
        "cost_category": { "type": "keyword" },
        "cost_type": { "type": "keyword" },
        "allocation_driver": { "type": "keyword" },
        "is_allocatable": { "type": "boolean" },
        "rate": { "type": "double" },
        "total_ecu": { "type": "double" },
        "total_chargeable_units": { "type": "double" },
        "total_quantity": { "type": "double" },
        "conf_chargeable_unit_rate": { "type": "double" },
        "conf_chargeable_unit_rate_unit": { "type": "keyword" },
        "conf_currency_symbol": { "type": "keyword" },
        "event": { "properties": { "ingested": { "type": "date" } } }
      }
    }
  }
}

PUT _index_template/chargeback_serverless_lookup_realized_pool
{
  "index_patterns": ["serverless_billing_realized_pool_lookup"],
  "priority": 500,
  "template": {
    "settings": {
      "index": {
        "mode": "lookup",
        "codec": "best_compression",
        "default_pipeline": "logs-chargeback_serverless.lookup-0.4.0-realized_pool"
      }
    },
    "mappings": {
      "dynamic": false,
      "properties": {
        "@timestamp": { "type": "date" },
        "composite_key": { "type": "keyword" },
        "project_id": { "type": "keyword" },
        "indexing_ecu": { "type": "double" },
        "query_ecu": { "type": "double" },
        "storage_ecu": { "type": "double" },
        "ai_ecu": { "type": "double" },
        "synthetics_ecu": { "type": "double" },
        "synthetics_pings_ecu": { "type": "double" },
        "synthetics_executions_ecu": { "type": "double" },
        "data_tier_capacity_ecu": { "type": "double" },
        "realized_chargeable_units": { "type": "double" },
        "total_chargeable_units": { "type": "double" },
        "total_ecu": { "type": "double" },
        "all_sku_ecu": { "type": "double" },
        "conf_chargeable_unit_rate": { "type": "double" },
        "conf_chargeable_unit_rate_unit": { "type": "keyword" },
        "conf_currency_symbol": { "type": "keyword" },
        "event": { "properties": { "ingested": { "type": "date" } } }
      }
    }
  }
}

PUT _index_template/chargeback_serverless_lookup_proj_contrib
{
  "index_patterns": ["serverless_project_contribution_lookup"],
  "priority": 500,
  "template": {
    "settings": {
      "index": {
        "mode": "lookup",
        "codec": "best_compression",
        "default_pipeline": "logs-chargeback_serverless.lookup-0.4.0-usage"
      }
    },
    "mappings": {
      "dynamic": false,
      "properties": {
        "@timestamp": { "type": "date" },
        "composite_key": { "type": "keyword" },
        "project_id": { "type": "keyword" },
        "deployment_sum_indexing_time": { "type": "long" },
        "deployment_sum_query_time": { "type": "long" },
        "deployment_sum_data_set_store_size": { "type": "long" },
        "event": { "properties": { "ingested": { "type": "date" } } }
      }
    }
  }
}

PUT _index_template/chargeback_serverless_lookup_project_status
{
  "index_patterns": ["serverless_project_status_lookup"],
  "priority": 500,
  "template": {
    "settings": {
      "index": {
        "mode": "lookup",
        "codec": "best_compression",
        "default_pipeline": "logs-chargeback_serverless.lookup-0.4.0-usage"
      }
    },
    "mappings": {
      "dynamic": false,
      "properties": {
        "@timestamp": { "type": "date" },
        "composite_key": { "type": "keyword" },
        "project_id": { "type": "keyword" },
        "project_name": { "type": "keyword" },
        "project_type": { "type": "keyword" },
        "optimized_for": { "type": "keyword" },
        "es_url": { "type": "keyword" },
        "last_status": { "type": "keyword" },
        "api_key_type": { "type": "keyword" },
        "synthetics_status": { "type": "keyword" },
        "index_count": { "type": "long" },
        "datastream_count": { "type": "long" },
        "ai_model_count": { "type": "long" },
        "synthetics_monitor_count": { "type": "long" },
        "updated_at": { "type": "date" },
        "conf_chargeable_unit_rate": { "type": "double" },
        "conf_chargeable_unit_rate_unit": { "type": "keyword" },
        "conf_currency_symbol": { "type": "keyword" },
        "event": { "properties": { "ingested": { "type": "date" } } }
      }
    }
  }
}
```

### Step 3: Install Integration Package in Kibana & Configure Policy Settings

1. In Kibana on your Serverless hub project, open **Management → Integrations**, and select **Upload integration** (or **Create new integration → Upload it as a .zip**).
2. Choose [`integrations/chargeback-serverless/assets/dist/chargeback_serverless-0.4.0.zip`](assets/dist/chargeback_serverless-0.4.0.zip) from this repository and click **Upload**.
3. Search for `Chargeback Serverless`, select **Chargeback Serverless**, and click **Add Chargeback Serverless**.
4. Enter your configuration values (matching the fields in `manifest.yml`):
   - **Organization ID (`organization_id`)**: Enter your numeric Elastic Cloud organization ID (`<YOUR_ORGANIZATION_ID>`).
   - **Elastic Cloud API Key (`cloud_api_key`)**: Paste your unified Elastic Cloud API key from Step 1.
   - **Billing Lookbehind Days (`lookbehind`)**: Keep the default `60` (backfills up to 60 completed UTC days of billing history on initial run).
   - **Currency Unit (`currency_unit`)**: Enter your target display currency code (default `USD`; for example, `USD`, `EUR`, `ZAR`, `GBP`, `ECU`).
   - **Currency Symbol (`currency_symbol`)**: Enter your currency symbol (default `$`; for example, `$`, `€`, `R`, `£`).
   - **Chargeable Unit Rate (`unit_rate`)**: Enter your conversion multiplier from `1.0` ECU to your target currency (default `1.0`).
   > [!NOTE]
   > The Elastic Agent CEL `billing` and `usage` streams poll hourly (`interval: 1h`) with daily UTC cursor gating after `02:00 UTC`. Advanced API base URLs (`billing_api_base` defaulting to `https://billing.elastic-cloud.com` and `cloud_api_base` defaulting to `https://api.elastic-cloud.com`) are hidden variables and do not need to be modified for standard Elastic Cloud environments.
5. Under **Where to add this integration?**, select **New hosts** and name the policy **`Chargeback Serverless Policy`**.
   > [!WARNING]
   > Always use a dedicated Agent Policy (`Chargeback Serverless Policy`) with a single enrolled Elastic Agent rather than attaching Chargeback Serverless to an existing multi-agent policy. Attaching to a policy with multiple agents causes duplicate organization-wide collection across every agent in that policy.
6. Click **Save and continue** and enroll a single Elastic Agent (`v9.2+`) into **`Chargeback Serverless Policy`**.

### Step 4: Optional Historical Usage Backfill (Cross-Platform `.env` / Environment Variables)

When an Elastic Agent is enrolled in the **`Chargeback Serverless Policy`**:
- The **`billing` CEL stream** automatically backfills up to `lookbehind` days (default `60` days) of historical billing data from the Elastic Cloud Billing API.
- The **`usage` CEL stream** harvests point-in-time per-project index, data stream, AI model, and Synthetics telemetry daily from the day of enrollment onward.

If you want to immediately backfill historical `usage` snapshots across your past billing dates (or run collection without waiting for an Elastic Agent), run the cross-platform Python backfill collector [`scripts/run_chargeback_serverless_collector.py`](../../scripts/run_chargeback_serverless_collector.py) using standard environment variables (Linux, Windows/WSL, or macOS) or a `.env` file:

```bash
export ORGANIZATION_ID="<YOUR_ORGANIZATION_ID>"
export CLOUD_API_KEY="<YOUR_UNIFIED_CLOUD_API_KEY>"
export ES_URL="https://<your-hub-project>.es.<region>.<csp>.elastic.cloud"
export ES_API_KEY="<YOUR_HUB_PROJECT_API_KEY>"
export KIBANA_URL="https://<your-hub-project>.kb.<region>.<csp>.elastic.cloud"
export KIBANA_API_KEY="<YOUR_HUB_PROJECT_API_KEY>"

python3 scripts/run_chargeback_serverless_collector.py
```

### Step 5: Activating Packaged Alerting Rule Templates in Kibana

The integration package installs 10 pre-configured ES|QL FinOps alerting rule templates into Kibana:

1. In Kibana, navigate to **Stack Management → Rules**.
2. Click **Create rule** and search for **`[Chargeback Serverless]`** under rule types/templates.
3. Select any of the 10 pre-configured ES|QL rule templates (such as **`[Chargeback Serverless] Total daily spend threshold exceeded`** or **`[Chargeback Serverless] Data trust & project harvest health issue`**).
4. Customize the threshold or entity filter on **line 2** of the ES|QL query (`EVAL threshold_...`), choose your notification connector (Slack, Email, Webhook, PagerDuty, or Index), and click **Save**.

---

## Part 2: Developer & Maintainer Automated Deployment (`deploy_chargeback_serverless.sh`)

Developers and maintainers can automate the entire deployment—including lookup index template creation, Fleet package upload, transform provisioning, dedicated Fleet policy creation, historical billing and usage backfill, alerting rule provisioning, and E2E ES|QL verification—with a single command using [`scripts/deploy_chargeback_serverless.sh`](../../scripts/deploy_chargeback_serverless.sh).

### Step 1: Set up a Python virtual environment with `PyYAML`

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pyyaml
```

### Step 2: Configure Environment Variables (Cross-Platform) or macOS Keychain

#### Option 2A: Cross-Platform Environment Variables (Linux, Windows/WSL, CI/CD, macOS)

Export the required variables directly in your shell (or place them in a local `.env` file in the repository root):

```bash
export ORGANIZATION_ID="<YOUR_ORGANIZATION_ID>"
export CLOUD_API_KEY="<YOUR_UNIFIED_CLOUD_API_KEY>"
export ES_URL="https://<your-hub-project>.es.<region>.<csp>.elastic.cloud"
export KIBANA_URL="https://<your-hub-project>.kb.<region>.<csp>.elastic.cloud"
export HUB_ES_API_KEY="<YOUR_HUB_PROJECT_API_KEY>"
export ES_API_KEY="$HUB_ES_API_KEY"
export KIBANA_API_KEY="$HUB_ES_API_KEY"
```

#### Option 2B: Optional macOS Keychain Helper (macOS Local Developers)

On macOS, you can optionally store credentials in the macOS Keychain (`elasticsearch-chargeback` service) using [`scripts/keychain.sh`](../../scripts/keychain.sh):

```bash
./scripts/keychain.sh set ORGANIZATION_ID <YOUR_ORGANIZATION_ID>
./scripts/keychain.sh set CLOUD_API_KEY
./scripts/keychain.sh set ES_URL "https://<your-hub-project>.es.<region>.<csp>.elastic.cloud"
./scripts/keychain.sh set ES_API_KEY
./scripts/keychain.sh set KIBANA_URL "https://<your-hub-project>.kb.<region>.<csp>.elastic.cloud"
./scripts/keychain.sh set KIBANA_API_KEY
```

### Step 3: Run the Automated Deployment Script

```bash
# Deploy using the prebuilt assets/dist/chargeback_serverless-0.4.0.zip (no elastic/integrations checkout required)
./scripts/deploy_chargeback_serverless.sh

# Run verification queries and health checks only
./scripts/deploy_chargeback_serverless.sh --verify-only

# Maintainer workflow ONLY: rebuild the package .zip from elastic/integrations before deploying
./scripts/deploy_chargeback_serverless.sh --build
```

---

## Scheduled collection & why `agentless` is disabled in `manifest.yml`

In `manifest.yml`, `deployment_modes.agentless.enabled` is set to `false`:
- **Why Agentless is disabled for custom `.zip` uploads**: Elastic Cloud Serverless only allows managed Agentless pods to spawn for integrations published in the official public Elastic Package Registry (EPR) with a signed Elastic container image. Custom integrations uploaded as a `.zip` cannot launch Serverless Agentless pods.
- **How to run unattended daily collection**:
  1. **Elastic Agent (Fleet)**: Enroll a single Elastic Agent (`v9.2+`) into the dedicated **`Chargeback Serverless Policy`**. The agent polls the `billing` and `usage` CEL programs hourly (`interval: 1h`) and ingests once per completed UTC day after `02:00 UTC`.
  2. **Scheduled Python collector (Agent-free)**: Schedule `python3 scripts/run_chargeback_serverless_collector.py` via cron, GitHub Actions, or a lightweight runner once per day after `02:00 UTC`.

---

## Verify transforms, lookup indices, and project harvest status

### 1. Verify the 7 Fleet transforms

Go to **Stack Management → Transforms** and filter by `logs-chargeback_serverless.`. Confirm that all seven transforms are **Started** with `health: green`:
- `logs-chargeback_serverless.billing_cost-default-0.4.0`
- `logs-chargeback_serverless.realized_pool-default-0.4.0`
- `logs-chargeback_serverless.proj_contrib-default-0.4.0`
- `logs-chargeback_serverless.ds_contrib-default-0.4.0`
- `logs-chargeback_serverless.ai_contrib-default-0.4.0`
- `logs-chargeback_serverless.synthetics_contrib-default-0.4.0`
- `logs-chargeback_serverless.project_status-default-0.4.0`

### 2. Verify project harvest status in Dev Tools

Open **Management → Dev Tools** and run the following Elasticsearch Query Language (ES|QL) query to verify project harvest status:

```json
POST _query?format=txt
{
  "query": "FROM serverless_project_status_lookup | SORT @timestamp DESC | LIMIT 25"
}
```

Confirm that each active Serverless project displays **`last_status: "ok"`** and **`api_key_type: "cloud_key"`** (or `"hub_key"` / `"remote_key"` if override keys are configured).

### 3. Open the Chargeback Serverless dashboards

Open **Dashboards** and select **`[Chargeback Serverless] Billing Components Overview`** to review your organization invoice breakdown across active and deleted Serverless projects.

## Next steps

- Review the [Chargeback Serverless configuration reference](CONFIG.md) to customize currency rates, Hosted vs. Serverless billing filters, or the 10 FinOps ES|QL alerting rules.
- See [Troubleshoot the Chargeback integration](../chargeback/docs/troubleshooting.md) if any project shows an authentication warning or missing billing history.
