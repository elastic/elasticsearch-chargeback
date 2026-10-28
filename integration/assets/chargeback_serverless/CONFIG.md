# Chargeback Serverless — configuration (ECH model)

## Principle

Day-2 configuration is **Fleet Integration Settings** (Organization ID, Billing API key, Usage API key) + **Configuration dashboard** + **Kibana Workflows**. Collection does not need an Elastic Agent. For custom zip installs, use the default Fleet deployment mode so Settings can be saved (Fleet rejects agentless for upload packages). The shell installer is optional bootstrap only.

## Credentials (Integration UI)

Set these in **Integrations → Chargeback Serverless → Settings**:

| Field | Secret | Stored in |
| --- | --- | --- |
| Organization ID | No | Package policy |
| Billing API key | Yes | Package policy (encrypted) |
| Usage API key | Yes | Package policy (encrypted) |

Workflows read `chargeback-serverless-config`. After changing Integration settings, upsert the same values into that document (bootstrap script or Dev Tools). For remote projects outside the hub, add per-project Elasticsearch API keys under `project_api_keys` (`{"<project_id>": "<es_api_key>"}`), and optionally filter harvested projects using `include_project_ids` / `exclude_project_ids`. Never put API keys on dashboards.

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
