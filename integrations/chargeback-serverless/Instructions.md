# Install and configure Chargeback Serverless

Deploy the **Chargeback Serverless (`chargeback_serverless` `0.3.15`)** integration on an Elastic Cloud Serverless project to collect organization-wide Serverless billing history (including deleted projects) and per-project data stream, AI model, and Synthetics usage telemetry.

> [!IMPORTANT]
> **Users vs. Developers — You do NOT need the `elastic/integrations` repository to install or backfill Chargeback Serverless.**
> - **Users**: Use the prebuilt package [`assets/dist/chargeback_serverless-0.3.15.zip`](assets/dist/chargeback_serverless-0.3.15.zip) included in this repository. Upload it directly in Kibana (**Option A**) or run `./scripts/deploy_chargeback_serverless.sh` (**Option B**), which installs the prebuilt `.zip` by default without building. You can then run `python3 scripts/run_chargeback_serverless_collector.py` at any time to backfill or refresh data without building a new `.zip`.
> - **Developers**: Only clone `elastic/integrations` and pass `--build` (`./scripts/deploy_chargeback_serverless.sh --build`) when modifying integration source templates under `packages/chargeback_serverless`.

## Before you begin

Make sure you have the following before installing Chargeback Serverless:

1. **Target Elastic Cloud Serverless hub project**:
   - An Elastic Cloud Serverless project (`elasticsearch`, `observability`, or `security`) where the dashboards, `LOOKUP`-mode indices, transforms, and Fleet policy will run.
   - Note your project's **Elasticsearch endpoint (`ES_URL`)** and **Kibana endpoint (`KIBANA_URL`)**.
2. **Elastic Cloud Organization ID (`ORGANIZATION_ID`)**:
   - Your numeric organization ID from the [Elastic Cloud Console](https://cloud.elastic.co).
3. **Single unified Elastic Cloud API key (`CLOUD_API_KEY`)**:
   - Create this key in the [Elastic Cloud Console API keys page](https://cloud.elastic.co/deployment-features/keys) (**Organization → API keys → Create API key**) with:
     - **Organization role**: Select **`Billing Admin`** (required to query organization billing costs and `/api/v1/billing/organization/{org_id}/history` for deleted projects).
     - **Enable access to Cloud resources**: Toggle **ON**, select **All projects** (or the specific Serverless projects you want to monitor), and in the separate per-project-type **Roles / API Access** dropdowns configure **`Elasticsearch`**, **`Observability`**, and **`Security`** (`Admin`) so the key can discover Serverless projects and harvest `_resolve/index/*`, `_cat/indices`, `_ml/trained_models/_stats`, `_inference`, and `.ds-synthetics-*` telemetry.
4. **Hub Project Elasticsearch & Kibana API key (`ES_API_KEY` / `KIBANA_API_KEY`)** *(required when running `deploy_chargeback_serverless.sh` or `run_chargeback_serverless_collector.py`)*:
   - In your Serverless hub project in Kibana, go to **Management → Security → API keys → Create API key** and copy the encoded Base64 API key. You can use the same hub project API key for both `ES_API_KEY` and `KIBANA_API_KEY`.
5. **Python `3.9+` virtual environment with `PyYAML`** *(required when running `deploy_chargeback_serverless.sh`)*:
   - On macOS, Homebrew Python enforces PEP 668 (`externally-managed-environment`) and blocks global `pip install`. Create and activate a local virtual environment in the repository root before running the deployment script:
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     pip install pyyaml
     ```

---

## Option A: Upload the prebuilt `.zip` in Kibana + run the backfill script (User workflow)

Use these steps to upload [`assets/dist/chargeback_serverless-0.3.15.zip`](assets/dist/chargeback_serverless-0.3.15.zip) through the Kibana UI and run the standalone Python collector for immediate historical backfill.

### Step 1: Upload `chargeback_serverless-0.3.15.zip` in Kibana

1. In Kibana on your Serverless hub project, open the **Navigation menu**, go to **Management → Integrations**, and select **Upload integration** (or **Create new integration → Upload it as a .zip**).
2. Choose [`integrations/chargeback-serverless/assets/dist/chargeback_serverless-0.3.15.zip`](assets/dist/chargeback_serverless-0.3.15.zip) from this repository and click **Upload**.

### Step 2: Add the integration to a dedicated Elastic Agent policy

1. In **Management → Integrations**, search for `Chargeback Serverless` and select **Chargeback Serverless**.
2. Click **Add Chargeback Serverless**.
3. Enter your configuration values:
   - **Elastic Cloud Organization ID (`organization_id`)**: Enter your numeric Elastic Cloud organization ID.
   - **Elastic Cloud API Key (`cloud_api_key`)**: Paste your unified Elastic Cloud API key (configured with **Organization role: `Billing Admin`** and **Cloud resource access** across **`Elasticsearch`**, **`Observability`**, and **`Security`** project types).
   - **Currency Unit (`currency_unit`)**: Enter your local currency code (for example, `USD`, `EUR`, `ZAR`, `GBP`).
   - **Currency Symbol (`currency_symbol`)**: Enter your currency symbol (for example, `$`, `€`, `R`, `£`).
   - **Chargeable Unit Rate (`unit_rate`)**: Enter your conversion multiplier from 1.0 ECU to your currency (for example, `1.0` or `0.85`).
   - **Collection Interval (`collection_interval`)**: Keep the default `24h`.
   - **Initial Billing Lookbehind Days (`initial_lookbehind_days`)**: Keep the default `60` (backfills up to 60 completed UTC days on first run).
4. Under **Where to add this integration?**, select **New hosts** and name the policy **`Chargeback Serverless Policy`**.
   > [!WARNING]
   > Always use a dedicated Agent Policy (`Chargeback Serverless Policy`) rather than attaching Chargeback Serverless to an existing multi-agent policy (such as a Synthetics or workload policy). Attaching to a policy with multiple agents causes duplicate organization-wide collection across every agent in that policy.
5. Click **Save and continue**.

### Step 3: Run the historical backfill collector (no package build required)

To immediately backfill up to 60 days of Serverless billing and usage telemetry (or refresh data on demand without an Elastic Agent), store your credentials in macOS Keychain (or export them as environment variables) and run [`scripts/run_chargeback_serverless_collector.py`](../../scripts/run_chargeback_serverless_collector.py):

```bash
./scripts/keychain.sh set ORGANIZATION_ID 2001809318
./scripts/keychain.sh set CLOUD_API_KEY
./scripts/keychain.sh set ES_URL "https://<your-hub-project>.es.<region>.<csp>.elastic.cloud"
./scripts/keychain.sh set ES_API_KEY
./scripts/keychain.sh set KIBANA_URL "https://<your-hub-project>.kb.<region>.<csp>.elastic.cloud"
./scripts/keychain.sh set KIBANA_API_KEY

source scripts/keychain.sh
python3 scripts/run_chargeback_serverless_collector.py
```

---

## Option B: One-command automated deployment with `deploy_chargeback_serverless.sh`

The [`scripts/deploy_chargeback_serverless.sh`](../../scripts/deploy_chargeback_serverless.sh) script automates credential lookup from macOS Keychain, uploading the prebuilt [`assets/dist/chargeback_serverless-0.3.15.zip`](assets/dist/chargeback_serverless-0.3.15.zip) to Fleet, provisioning persistent `mode: lookup` index templates and all 7 Fleet transforms, creating/updating the dedicated **`Chargeback Serverless Policy`**, running the initial billing and usage backfill, installing the 10 alerting rules, and verifying all dashboards.

### Step 1: Activate a Python virtual environment with `PyYAML`

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pyyaml
```

### Step 2: Store all required credentials in macOS Keychain

From the repository root, store your organization ID, unified Elastic Cloud API key, and target hub Elasticsearch and Kibana endpoints and API keys in the macOS Keychain (`elasticsearch-chargeback` service):

```bash
./scripts/keychain.sh set ORGANIZATION_ID 2001809318
./scripts/keychain.sh set CLOUD_API_KEY
./scripts/keychain.sh set ES_URL "https://<your-hub-project>.es.<region>.<csp>.elastic.cloud"
./scripts/keychain.sh set ES_API_KEY
./scripts/keychain.sh set KIBANA_URL "https://<your-hub-project>.kb.<region>.<csp>.elastic.cloud"
./scripts/keychain.sh set KIBANA_API_KEY
```

### Step 3: Run the deployment script

Run the deployment script from the repository root. By default, it uses the prebuilt [`assets/dist/chargeback_serverless-0.3.15.zip`](assets/dist/chargeback_serverless-0.3.15.zip) without requiring `elastic/integrations`:

```bash
# User installation & backfill (uses prebuilt dist/chargeback_serverless-0.3.15.zip by default)
./scripts/deploy_chargeback_serverless.sh

# Verification and health check only
./scripts/deploy_chargeback_serverless.sh --verify-only

# Developer workflow ONLY: rebuilds the .zip from elastic/integrations before deploying
./scripts/deploy_chargeback_serverless.sh --build
```

---

## Scheduled collection & why `agentless` is disabled in `manifest.yml`

In `manifest.yml`, `deployment_modes.agentless.enabled` is set to `false`:
- **Why Agentless is disabled for custom `.zip` uploads**: Elastic Cloud Serverless only allows managed Agentless pods to spawn for integrations published in the official public Elastic Package Registry (EPR) with a signed Elastic container image. Custom integrations uploaded as a `.zip` cannot launch Serverless Agentless pods.
- **How to run unattended daily collection**:
  1. **Elastic Agent (Fleet)**: Enroll a single Elastic Agent (`v9.2+` / `v9.5+`) into the dedicated **`Chargeback Serverless Policy`**. The agent runs the `billing` and `usage` CEL programs on the configured `collection_interval` (default `24h`).
  2. **Scheduled Python collector (Agent-free)**: Schedule `python3 scripts/run_chargeback_serverless_collector.py` via cron, GitHub Actions, or a lightweight runner once per day after `02:00 UTC`.

---

## Step 4: Verify transforms, lookup indices, and project harvest status

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

3. Open **Dashboards** and select **`[Chargeback Serverless] Billing Components Overview`** to review your organization invoice breakdown across active and deleted Serverless projects.

## Next steps

- Review the [Chargeback Serverless configuration reference](CONFIG.md) to customize currency rates, Hosted vs. Serverless billing filters, or the 10 FinOps ES|QL alerting rules.
- See [Troubleshoot the Chargeback integration](../chargeback/docs/troubleshooting.md) if any project shows an authentication warning or missing billing history.
