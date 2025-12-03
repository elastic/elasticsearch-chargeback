# Chargeback (ES|QL Version) Integration

This document outlines the steps to install the Chargeback integration, which makes use of the `ES|QL LOOKUP JOIN` feature introduced in 8.18.

## Migrate from "module" to integration

If you have already installed the Chargeback "module" and want to rather use the integration, please follow the [Decommisioning](../module/Decommisioning.md) instructions of the module, and then return to these instructions.

## Setup Instructions

To install the Chargeback integration, please follow these steps:

### 1. Meet Prerequisites

See [Requirements](README.md#requirements) for details.

### 2. Create Lookup Indices and Data View: 
- Copy the Index creation commands (below) to Kibana Dev Tools.
- If required, modify the desired values for the `chargeback_conf_lookup` index. Note, these can be changed at a later stage.
    - Change the `conf_*_weight` values to set the blended weight calculations, or use defaults.
    - Change the `conf_ecu_rate` value so that the dashboard is in the desired rate, and `conf_ecu_rate_unit` to the unit representing the rate. For example a rate of `17.6` and unit of `ZAR` will show the cost in South African Rand, wherease a rate of `1` and unit of `USD` will show the cost in United States Dollar.
- Create the required `mode: lookup` indices by executing all of the commands.
- Create the required Kibana data view which is used in the Dashboard control.

<details>

```
# Create the config lookup index for chargeback configuration.
# This index will store the configuration settings. If you require different conversion rates per time window you can add multiple docs for this;
# Be careful not to overlap dates.

PUT chargeback_conf_lookup
{
  "settings": { 
    "index.mode": "lookup"
  },
  "mappings": {
    "_meta": {
      "managed": true,
      "package": { "name": "chargeback", "version": "0.2.6" }
    },
    "properties": {
      "config_join_key": { "type": "keyword" },
      "conf_ecu_rate": { "type": "float" },
      "conf_ecu_rate_unit": { "type": "keyword"},
      "conf_indexing_weight": { "type": "integer" },
      "conf_query_weight": { "type": "integer" },
      "conf_storage_weight": { "type": "integer" },
      "conf_start_date": {"type": "date"},
      "conf_end_date": {"type": "date"}
    }
  }
}

# Add the default configuration to the chargeback_conf_lookup index.
POST chargeback_conf_lookup/_doc/config
{
  "config_join_key": "chargeback_config",
  "conf_ecu_rate": 0.85,
  "conf_ecu_rate_unit": "EUR",
  "conf_indexing_weight": 20,
  "conf_query_weight": 20,
  "conf_storage_weight": 40,
  "conf_start_date": "2024-01-01T12:00:00.000Z",
  "conf_end_date": "2030-12-31T23:59:59.000Z"
}
```

</details>


### 3. Upload ZIP File: 

- Asset: [`chargeback-0.2.6.zip`](assets/0.2.6/chargeback-0.2.6.zip)
- Browse to Integrations, and click on `+ Create new integration`

![alt text](assets/img/CreateNewIntegration.png)

- Upload the provided ZIP file by clicking on `upload it as a .zip`

![alt text](assets/img/UploadItAsAZip.png)


## Upgrade integration

To upgrade the integration, do the following:
- Depending on the change in version, you might need to delete the `*_lookup` indices, and create them again.
- Upload the new asset (ZIP) file to Kibana.
- Start the transforms.
