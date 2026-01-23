# Chargeback (ES|QL Version) Integration

This document outlines the steps to install the Chargeback integration, which makes use of the smart `ES|QL LOOKUP JOIN` feature (conditional joins) requiring Stack version 9.2.0+.

## Migrate from "module" to integration

If you have already installed the Chargeback "module" and want to rather use the integration, please follow the [Decommisioning](../module/Decommisioning.md) instructions of the module, and then return to these instructions.

## Setup Instructions

To install the Chargeback integration, please follow these steps:

### 1. Meet Prerequisites

See [Requirements](README.md#requirements) for details.

### 2. Upload ZIP File: 

- Asset: [`chargeback-0.2.10.zip`](assets/0.2.10/chargeback-0.2.10.zip)
- Browse to Integrations, and click on `+ Create new integration`

![alt text](assets/img/CreateNewIntegration.png)

- Upload the provided ZIP file by clicking on `upload it as a .zip`

![alt text](assets/img/UploadItAsAZip.png)

### 3. Transforms Auto-Start

Starting from version 0.2.8, all Chargeback transforms are configured to auto-start upon installation. You no longer need to manually start the transforms.

**Starting from version 0.2.10**, the `chargeback_conf_lookup` index is automatically created via a bootstrap transform during installation. No manual setup is required! The transform creates the index with default configuration:
- **ECU rate:** 0.85 EUR
- **Weights:** indexing=20, query=20, storage=40
- **Date range:** 2010-01-01 to 2046-12-31

You can modify these values after installation if needed (see Configuration section below).

**Performance Note:** On clusters with months of historical monitoring data for multiple deployments, the initial transform execution may process a large volume of data. This can cause temporary performance impact during the first run. The transforms will then run incrementally on their configured schedules (15-60 minute intervals), processing only new data with minimal overhead.

### 4. Configure Alerting Rules (Optional)

Version 0.2.8 includes three pre-configured alerting rule templates:
- **Transform Health Monitoring** - Monitors transform health status
- **New Chargeback Group Detection** - Alerts on new chargeback group tags
- **Missing Usage Data** - Detects deployments with missing usage data

These rules can be configured in **Stack Management → Rules** after installation.

**Important:** For the New Chargeback Group Detection and Missing Usage Data alert rules, ensure that the Chargeback transforms are running before setting them up. These alerting rules query the lookup indices created by the transforms. If the transforms are not started, the alerts will not function correctly.

## Upgrade integration

To upgrade the integration, do the following:
- Upload the new asset (ZIP) file to Kibana.
- Transforms will auto-start (from version 0.2.8 onwards).

**Upgrading from 0.2.9 to 0.2.10:**
- No manual steps required for the `chargeback_conf_lookup` index - the bootstrap transform will automatically create it if it doesn't exist.
- If you previously manually created the `chargeback_conf_lookup` index, it will continue to work with the new version.

## Configuration

Configuration values are stored in the `chargeback_conf_lookup` index, which is automatically created by version 0.2.10. The dashboard automatically applies the correct configuration based on the billing date falling within the `conf_start_date` and `conf_end_date` range.

### Update the default configuration:

Using `_update/config` updates the document with ID `config`:

```
POST chargeback_conf_lookup/_update/config
{
  "doc": {
    "conf_ecu_rate": 0.85,
    "conf_ecu_rate_unit": "EUR",
    "conf_indexing_weight": 20,
    "conf_query_weight": 20,
    "conf_storage_weight": 40,
    "conf_start_date": "2024-01-01T00:00:00.000Z",
    "conf_end_date": "2024-12-31T23:59:59.999Z"
  }
}
```

### Add a new configuration period (for time-based rate changes):

Using `_doc` creates a new document with an auto-generated ID:

```
POST chargeback_conf_lookup/_doc
{
  "config_join_key": "chargeback_config",
  "conf_ecu_rate": 0.95,
  "conf_ecu_rate_unit": "EUR",
  "conf_indexing_weight": 20,
  "conf_query_weight": 20,
  "conf_storage_weight": 40,
  "conf_start_date": "2025-01-01T00:00:00.000Z",
  "conf_end_date": "2025-12-31T23:59:59.999Z"
}
```
