# Chargeback (ES|QL Version) Integration

This document outlines the steps to install the Chargeback integration, which makes use of the ES|QL LOOKUP JOIN feature introduced in 8.18.

## Migrate from "module" to integration

If you have already installed the Chargeback "module" and want to rather use the integration, please follow the [Decommisioning](../module/Decommisioning.md) instructions of the module, and then return to these instructions.

## Setup Instructions

To install the Chargeback integration, please follow these steps:

### 1. Meet Prerequisites

See [Requirements](README.md) for details.

### 2. Set up the usage alias, and create Lookup Indices: 
- Copy the Index creation commands (below) to Kibana Dev Tools.
- Run the appropriate command to create the right alias. Only one of the alias must be created. 
- If required, modify the desired values for the `chargeback_conf_lookup` index. Note, these can be changed at a later stage.
    - Change the `conf_*_weigh` values to set the blended weight calculations, or use defaults.
    - Change the `conf_ecu_rate` value so that the dashboard is in the desired rate, and `conf_ecu_rate_unit` to the unit representing the rate. For example a rate of `17.6` and unit of `ZAR` will show the cost in South African Rand.
- Create the required `mode: lookup` indices by executing all of the commands.

<details>
<summary>Index creation commands</summary>

```JSON
# The usage transforms can work on both Cloud Stack monitoring data, or Elasticsearch Integration data

# Check to see which of the sources you have available.
# ES Integration
GET monitoring-indices/_count
# Stack Monitoring
GET .monitoring-es-8-mb/_count 

# If you do have the Elasticsearch Integration running, create this alias. It is preferable to use the Elasticsearch Integration as it is more performant.
POST _aliases
{
  "actions": [
    { "add": { "index": "monitoring-indices", "alias": "chargeback-monitoring-read", "is_write_index": false }}
  ]
}

# If you cannot use the Elasticsearch Integration, but have Stack monitoring in place, create this alias.
POST _aliases
{
  "actions": [
    { "add": { "index": ".monitoring-es-8-mb", "alias": "chargeback-monitoring-read", "is_write_index": false }}
  ]
}

# Create the lookup indices for chargeback configuration and billing metrics
# These indices are used to store configuration and billing data for chargeback calculations.

PUT chargeback_conf_lookup
{
  "settings": { 
    "index.mode": "lookup", 
    "index.hidden": true 
  },
  "mappings": {
    "_meta": {
      "managed": true,
      "package": { "name": "chargeback", "version": "0.1.3" }
    },
    "properties": {
      "config_join_key": { "type": "keyword" },
      "conf_ecu_rate": { "type": "float" },
      "conf_ecu_rate_unit": { "type": "keyword"},
      "conf_indexing_weight": { "type": "integer" },
      "conf_query_weight": { "type": "integer" },
      "conf_storage_weight": { "type": "integer" }
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
  "conf_storage_weight": 40
}

# Create the lookup indices for billing and cluster contributions.
PUT billing_cluster_cost_lookup
{
  "settings": {
    "index.mode": "lookup",
    "index.hidden": true
  },
  "mappings": {
    "_meta": {
      "managed": true,
      "package": { "name": "chargeback", "version": "0.1.3" }
    },
    "properties": {
      "@timestamp": { "type": "date" },
      "billing_name": {
        "type": "text",
        "fields": { "keyword": { "type": "keyword", "ignore_above": 256 } }
      },
      "billing_type": {
        "type": "text",
        "fields": { "keyword": { "type": "keyword", "ignore_above": 256 } }
      },
      "composite_key": { "type": "keyword" },
      "config_join_key": { "type": "keyword" },
      "deployment_id": { "type": "keyword" },
      "deployment_name": {
        "type": "text",
        "fields": { "keyword": { "type": "keyword", "ignore_above": 256 } }
      },
      "total_ecu": { "type": "float" }
    }
  }
}

PUT cluster_datastream_contribution_lookup
{
  "settings": {
    "index.mode": "lookup",
    "index.hidden": true
  },
  "mappings": {
    "_meta": {
      "managed": true,
      "package": { "name": "chargeback", "version": "0.1.3" }
    },
    "properties": {
      "@timestamp": { "type": "date" },
      "composite_key": { "type": "keyword" },
      "composite_datastream_key": { "type": "keyword" },
      "config_join_key": { "type": "keyword" },
      "cluster_name": { "type": "keyword" },
      "deployment_id": { "type": "keyword" },
      "datastream_name": { "type": "keyword" },
      "datastream_sum_indexing_time": { "type": "double" },
      "datastream_sum_query_time": { "type": "double" },
      "datastream_sum_store_size": { "type": "double" },
      "datastream_sum_data_set_store_size": { "type": "double" }
    }
  }
}

PUT cluster_deployment_contribution_lookup
{
  "settings": {
    "index.mode": "lookup",
    "index.hidden": true
  },
  "mappings": {
    "_meta": {
      "managed": true,
      "package": { "name": "chargeback", "version": "0.1.3" }
    },
    "properties": {
      "@timestamp": { "type": "date" },
      "composite_key": { "type": "keyword" },
      "config_join_key": { "type": "keyword" },
      "cluster_name": { "type": "keyword" },
      "deployment_id": { "type": "keyword" },
      "deployment_sum_indexing_time": { "type": "double" },
      "deployment_sum_query_time": { "type": "double" },
      "deployment_sum_store_size": { "type": "double" },
      "deployment_sum_data_set_store_size": { "type": "double" }
    }
  }
}

PUT cluster_tier_and_datastream_contribution_lookup
{
  "settings": {
    "index.mode": "lookup",
    "index.hidden": true
  },
  "mappings": {
    "_meta": {
      "managed": true,
      "package": { "name": "chargeback", "version": "0.1.3" }
    },
    "properties": {
      "@timestamp": { "type": "date" },
      "composite_key": { "type": "keyword" },
      "composite_tier_key": { "type": "keyword" },
      "config_join_key": { "type": "keyword" },
      "cluster_name": { "type": "keyword" },
      "deployment_id": { "type": "keyword" },
      "tier": { "type": "keyword" },
      "datastream": { "type": "keyword" },
      "tier_and_datastream_sum_indexing_time": { "type": "double" },
      "tier_and_datastream_sum_query_time": { "type": "double" },
      "tier_and_datastream_sum_store_size": { "type": "double" },
      "tier_and_datastream_sum_data_set_store_size": { "type": "double" }
    }
  }
}

PUT cluster_tier_contribution_lookup
{
  "settings": {
    "index.mode": "lookup",
    "index.hidden": true
  },
  "mappings": {
    "_meta": {
      "managed": true,
      "package": { "name": "chargeback", "version": "0.1.3" }
    },
    "properties": {
      "@timestamp": { "type": "date" },
      "composite_key": { "type": "keyword" },
      "composite_tier_key": { "type": "keyword" },
      "config_join_key": { "type": "keyword" },
      "cluster_name": { "type": "keyword" },
      "deployment_id": { "type": "keyword" },
      "tier": { "type": "keyword" },
      "tier_sum_indexing_time": { "type": "double" },
      "tier_sum_query_time": { "type": "double" },
      "tier_sum_store_size": { "type": "double" },
      "tier_sum_data_set_store_size": { "type": "double" }
    }
  }
}

```

</details>


3. Upload ZIP File: 

Asset: [`chargeback-0.1.3.zip`](assets/0.1.3/chargeback-0.1.3.zip)

- Browse to Integrations, and click on `+ Create new integration`

![alt text](assets/img/CreateNewIntegration.png)

- Upload the provided ZIP file by clicking on `upload it as a .zip`

![alt text](assets/img/UploadItAsAZip.png)

## Update config

<details>
<summary>Update blended rate weightings</summary>

```JSON
POST chargeback_conf_lookup/_update/config
{
  "doc": {
    "conf_ecu_rate": 0.85,
    "conf_ecu_rate_unit": "EUR",
    "conf_indexing_weight": 20,
    "conf_query_weight": 20,
    "conf_storage_weight": 40
  }
}
```
</details>
