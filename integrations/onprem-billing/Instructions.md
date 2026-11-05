# On-Premises Billing Installation & Configuration

Follow these steps to install the `onprem_billing` integration and configure cost calculation for on-premises deployments.

---

## 1. Install the Integration

1. In Kibana, navigate to **Management → Integrations → Installed**.
2. Click **Upload integration** (or use `elastic-package install --zip`):
   - Select [`assets/dist/onprem_billing-0.3.4.zip`](assets/dist/onprem_billing-0.3.4.zip).
3. Create an integration policy targeting your monitoring cluster.

---

## 2. Start Configuration Bootstrap Transform

The bootstrap transform discovers deployments and initializes the `onprem_billing_config` index.

```http
POST _transform/logs-onprem_billing.config_bootstrap-*/_start
```

Wait a few seconds for the initial run to complete.

---

## 3. Configure Organization Licensing

Create the organization license configuration document in `onprem_billing_config`:

```http
PUT onprem_billing_config/_doc/organization
{
  "config_type": "organization",
  "total_annual_license_cost": 180000,
  "total_erus_purchased": 12,
  "eru_to_ram_gb": 64,
  "currency_unit": "EUR"
}
```

---

## 4. Tag Deployments and Assign ERUs

For each discovered deployment, update its entry in `onprem_billing_config` with its assigned ERUs and chargeback group:

```http
POST onprem_billing_config/_update/<doc_id>
{
  "doc": {
    "deployment_name": "prod",
    "deployment_tags": ["chargeback_group:product"],
    "deployment_erus": 8
  }
}
```

---

## 5. Create and Execute Enrich Policies

Create two enrich policies to provide license and deployment metadata to the ingest pipeline:

```http
PUT _enrich/policy/onprem_billing_config_enrich_policy
{
  "match": {
    "indices": "onprem_billing_config",
    "match_field": "deployment_id",
    "enrich_fields": [
      "daily_meru",
      "deployment_erus",
      "deployment_name",
      "deployment_tags",
      "node_count",
      "ram_per_node_gb"
    ]
  }
}

PUT _enrich/policy/onprem_billing_org_config_policy
{
  "match": {
    "indices": "onprem_billing_config",
    "match_field": "config_type",
    "enrich_fields": [
      "total_annual_license_cost",
      "total_erus_purchased",
      "eru_to_ram_gb",
      "currency_unit"
    ]
  }
}

POST _enrich/policy/onprem_billing_config_enrich_policy/_execute
POST _enrich/policy/onprem_billing_org_config_policy/_execute
```

---

## 6. Install the Cost Calculation Pipeline

Install the ingest pipeline from [`assets/pipelines/onprem_billing_calculate_cost_pipeline.json`](assets/pipelines/onprem_billing_calculate_cost_pipeline.json):

```bash
curl -X PUT "${ES_HOST}/_ingest/pipeline/calculate_cost" \
  -H "Content-Type: application/json" \
  -H "Authorization: ApiKey ${ES_API_KEY}" \
  -d @"integrations/onprem-billing/assets/pipelines/onprem_billing_calculate_cost_pipeline.json"
```

---

## 7. Configure and Start Billing Transform

Attach the `calculate_cost` pipeline to the billing transform destination and start the transform:

```http
POST _transform/logs-onprem_billing.billing-*/_update
{
  "dest": {
    "index": "metrics-ess_billing.billing-onprem",
    "pipeline": "calculate_cost"
  }
}

POST _transform/logs-onprem_billing.billing-*/_start
```

Once the transform runs, normalized billing records appear in `metrics-ess_billing.billing-onprem` and are automatically ingested into Chargeback's lookup transforms.
