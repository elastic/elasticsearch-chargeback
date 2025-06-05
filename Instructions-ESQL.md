# ES|QL Chargeback Module Setup Instructions

**Note:** Please only follow these instructions if the cluster where you are deploying this module has ES|QL LOOKUP JOIN (Version 8.18.x+)

Follow these steps in order using the Kibana Dev Console.

## 1. Set up Billing-related Indices and Transform

### 1.1. Add Pipeline for the ESS Billing Integration

To calculate the _value_ of your ECU, you need to add a rate to the ESS billing information. This will cascade down.
Modify the `ess.billing.ecu_value` field with your value rate. E.g if 1 ECU is $2.2 worth you would modify the `0.85` to `2.2`.

```JSON
PUT _ingest/pipeline/metrics-ess_billing.billing@custom
{
  "description": "Add the value of ECU to the billing information using plain decimal math",
  "version": 2,
  "processors": [
    {
      "set": {
        "field": "ess.billing.ecu_rate",
        "value": 0.85
      }
    },
    {
      "script": {
        "lang": "painless",
        "tag": "total_ecu_cost",
        "source": """
          if (ctx.ess.billing.ecu_rate != null && ctx.ess.billing.total_ecu != null) {
            def ecuRate = new BigDecimal(ctx.ess.billing.ecu_rate.toString());
            def totalEcu = new BigDecimal(ctx.ess.billing.total_ecu.toString());
            def totalValue = ecuRate.multiply(totalEcu).setScale(3, RoundingMode.HALF_UP);
            ctx.ess.billing.total_ecu_value = totalValue.doubleValue();
          }
        """,
        "ignore_failure": true
      }
    },
    {
      "script": {
        "lang": "painless",
        "tag": "rate_and_formatted",
        "source": """
          if (ctx.ess.billing.ecu_rate != null && ctx.ess.billing.rate?.value != null) {
            def ecu = new BigDecimal(ctx.ess.billing.ecu_rate.toString());
            def rate = new BigDecimal(ctx.ess.billing.rate.value.toString());
            def ecuRate = rate.multiply(ecu).setScale(3, RoundingMode.HALF_UP);
            ctx.ess.billing.rate.ecu_value = ecuRate.doubleValue();
            ctx.ess.billing.rate.ecu_formatted_value = ecuRate.toPlainString() + ' per ' + ctx.ess.billing.unit;
          }
        """,
        "ignore_failure": true
      }
    }
  ],
  "on_failure": [
    {
      "set": {
        "field": "event.kind",
        "value": "pipeline_error"
      }
    },
    {
      "append": {
        "field": "error.message",
        "value": "{{{ _ingest.on_failure_message }}}"
      }
    }
  ]
}
```
File: [`metrics-ess_billing.billing@custom`](./assets/pipelines/metrics-ess_billing.billing@custom_pipeline.json)


### 1.2. Set Composite Tier Key Pipeline

Extends `set_composite_key` by adding the tier value to `composite_key`.

```JSON
PUT _ingest/pipeline/set_billing_composite_key
{
  "description": "Billing Transform: Set composite_key from @timestamp and deployment_id",
  "processors": [
    {
      "script": {
        "lang": "painless",
        "source": """
          if (ctx['@timestamp'] != null) {
              ctx.composite_key = ZonedDateTime.parse(ctx['@timestamp']).toLocalDate().toString() + '_' + ctx.deployment_id;
          }
        """
      }
    }
  ]
}
```
File: [`set_billing_composite_key`](./assets/pipelines/set_billing_composite_key_pipeline.json)

### 1.3.  Create Destination Index

Create the `billing_cluster_cost_lookup` index with index mode "lookup".

```JSON
PUT billing_cluster_cost_lookup
{
  "settings": {
    "index.mode": "lookup"
  },
  "mappings": {
    "properties": {
      "@timestamp": {
        "type": "date"
      },
      "billing_name": {
        "type": "text",
        "fields": {
          "keyword": {
            "type": "keyword",
            "ignore_above": 256
          }
        }
      },
      "billing_type": {
        "type": "text",
        "fields": {
          "keyword": {
            "type": "keyword",
            "ignore_above": 256
          }
        }
      },
      "composite_key": {
        "type": "keyword"
      },
      "deployment_id": {
        "type": "keyword"
      },
      "deployment_name": {
        "type": "text",
        "fields": {
          "keyword": {
            "type": "keyword",
            "ignore_above": 256
          }
        }
      },
      "ecu_rate": {
        "type": "float"
      },
      "total_ecu": {
        "type": "float"
      },
      "total_ecu_value": {
        "type": "float"
      }
    }
  }
}
```
File: [`billing_cluster_cost_lookup`](./assets/indices/billing_cluster_cost_lookup.json)

### 1.4. Create Billing Transform

```JSON
PUT _transform/billing_cluster_cost_transform/
{
  "description": "Aggregates daily total ECU usage per deployment from billing metrics, using ingested timestamps with a 1-hour sync delay and running every 60 minutes.",
  "source": {
    "index": [
      "metrics-ess_billing.billing-default"
    ],
    "query": {
      "range": {
        "ess.billing.total_ecu": {
          "gt": 0
        }
      }
    }
  },
  "dest": {
    "index": "billing_cluster_cost_lookup",
    "pipeline": "set_billing_composite_key"
  },
  "frequency": "60m",
  "sync": {
    "time": {
      "field": "event.ingested",
      "delay": "1h"
    }
  },
  "pivot": {
    "group_by": {
      "@timestamp": {
        "date_histogram": {
          "field": "@timestamp",
          "calendar_interval": "1d"
        }
      },
      "deployment_id": {
        "terms": {
          "field": "ess.billing.deployment_id"
        }
      },
      "deployment_name": {
        "terms": {
          "field": "ess.billing.deployment_name"
        }
      }
    },
    "aggregations": {
      "total_ecu": {
        "sum": {
          "field": "ess.billing.total_ecu"
        }
      },
      "total_ecu_value": {
        "sum": {
          "field": "ess.billing.total_ecu_value"
        }
      },
      "ecu_rate": {
        "max": {
          "field": "ess.billing.ecu_value"
        }
      }
    }
  }
}
```
File: [`billing_cluster_cost_transform`](./assets/transforms/billing_cluster_cost_transform.json)


Start the transform.

```
POST _transform/billing_cluster_cost_transform/_start
```

## 2. Set up Consumption-related Indices and Transforms

### 2.1 Dimension: Deployment (Lookup Index + Transform)

#### Create Destination Index

```JSON
PUT cluster_deployment_contribution_lookup
{
  "settings": {
    "index.mode": "lookup"
  },
  "mappings": {
    "properties": {
      "@timestamp": {
        "type": "date"
      },
      "composite_key": {
        "type": "keyword"
      },
      "cluster_name": {
        "type": "keyword"
      },
      "deployment_id": {
        "type": "keyword"
      },
      "deployment_sum_indexing_time": {
        "type": "double"
      },
      "deployment_sum_query_time": {
        "type": "double"
      },
      "deployment_sum_store_size": {
        "type": "double"
      },
      "deployment_sum_data_set_store_size": {
        "type": "double"
      }
    }
  }
}
```

#### Create Transform

```json
PUT _transform/cluster_deployment_contribution_transform
{
  "description": "Aggregates daily total ECU usage per DEPLOYMENT from billing metrics, using ingested timestamps with a 1-hour sync delay and running every 60 minutes.",
  "source": {
    "index": [
      "monitoring-indices"
    ],
    "query": {
      "match_all": {}
    }
  },
  "dest": {
    "index": "cluster_deployment_contribution_lookup",
    "pipeline": "set_consumption_composite_key"
  },
  "frequency": "60m",
  "sync": {
    "time": {
      "field": "event.ingested",
      "delay": "1h"
    }
  },
  "pivot": {
    "group_by": {
      "@timestamp": {
        "date_histogram": {
          "field": "@timestamp",
          "calendar_interval": "1d"
        }
      },
      "cluster_name": {
        "terms": {
          "field": "elasticsearch.cluster.name"
        }
      }
    },
    "aggregations": {
      "deployment_sum_indexing_time": {
        "sum": {
          "field": "elasticsearch.index.total.indexing.index_time_in_millis"
        }
      },
      "deployment_sum_query_time": {
        "sum": {
          "field": "elasticsearch.index.total.search.query_time_in_millis"
        }
      },
      
      "deployment_sum_store_size": {
        "sum": {
          "field": "elasticsearch.index.total.store.size_in_bytes"
        }
      },
      "deployment_sum_data_set_store_size": {
        "sum": {
          "field": "elasticsearch.index.primaries.store.total_data_set_size_in_bytes"
        }
      }
    }
  }
}
```

Start the transform.

```
POST _transform/cluster_deployment_contribution_transform/_start
```

### 2.2 Dimension: Data tier (Lookup Index + Transform)

#### Create Destination Index

```json
PUT cluster_tier_contribution_lookup
{
  "settings": {
    "index.mode": "lookup"
  },
  "mappings": {
    "properties": {
      "@timestamp": {
        "type": "date"
      },
      "composite_key": {
        "type": "keyword"
      },
      "composite_tier_key": {
        "type": "keyword"
      },
      "cluster_name": {
        "type": "keyword"
      },
      "deployment_id": {
        "type": "keyword"
      },
      "tier": {
        "type": "keyword"
      },
      "tier_sum_indexing_time": {
        "type": "double"
      },
      "tier_sum_query_time": {
        "type": "double"
      },
      "tier_sum_store_size": {
        "type": "double"
      },
      "tier_sum_data_set_store_size": {
        "type": "double"
      }
    }
  }
}
```

#### Create Transform

```json
PUT _transform/cluster_tier_contribution_transform
{
  "description": "Aggregates daily total ECU usage per TIER from billing metrics, using ingested timestamps with a 1-hour sync delay and running every 60 minutes.",
  "source": {
    "index": [
      "monitoring-indices"
    ],
    "query": {
      "match_all": {}
    }
  },
  "dest": {
    "index": "cluster_tier_contribution_lookup",
    "pipeline": "set_consumption_composite_key"
  },
  "frequency": "60m",
  "sync": {
    "time": {
      "field": "event.ingested",
      "delay": "1h"
    }
  },
  "pivot": {
    "group_by": {
      "@timestamp": {
        "date_histogram": {
          "field": "@timestamp",
          "calendar_interval": "1d"
        }
      },
      "cluster_name": {
        "terms": {
          "field": "elasticsearch.cluster.name"
        }
      },
      "tier": {
        "terms": {
          "field": "elasticsearch.index.tier"
        }
      }
    },
    "aggregations": {
      "tier_sum_indexing_time": {
        "sum": {
          "field": "elasticsearch.index.total.indexing.index_time_in_millis"
        }
      },
      "tier_sum_query_time": {
        "sum": {
          "field": "elasticsearch.index.total.search.query_time_in_millis"
        }
      },
      "tier_sum_store_size": {
        "sum": {
          "field": "elasticsearch.index.total.store.size_in_bytes"
        }
      },
      "tier_sum_data_set_store_size": {
        "sum": {
          "field": "elasticsearch.index.primaries.store.total_data_set_size_in_bytes"
        }
      }
    }
  }
}
```

Start the transform.

```
POST _transform/cluster_tier_contribution_transform/_start
```

### 2.3 Dimension: Data stream (Lookup Index + Transfrom)

```json
PUT cluster_datastream_contribution_lookup
{
  "settings": {
    "index.mode": "lookup"
  },
  "mappings": {
    "properties": {
      "@timestamp": {
        "type": "date"
      },
      "composite_key": {
        "type": "keyword"
      },
      "composite_datastream_key": {
        "type": "keyword"
      },
      "cluster_name": {
        "type": "keyword"
      },
      "deployment_id": {
        "type": "keyword"
      },
      "datastream_name": {
        "type": "keyword"
      },
      "datastream_sum_indexing_time": {
        "type": "double"
      },
      "datastream_sum_query_time": {
        "type": "double"
      },
      "datastream_sum_store_size": {
        "type": "double"
      },
      "datastream_sum_data_set_store_size": {
        "type": "double"
      }
    }
  }
}
```

#### Create Transform

```json
PUT _transform/cluster_datastream_contribution_transform
{
  "description": "Aggregates daily total ECU usage per DATASTREAM from billing metrics, using ingested timestamps with a 1-hour sync delay and running every 60 minutes.",
  "source": {
    "index": [
      "monitoring-indices"
    ],
    "query": {
      "match_all": {}
    }
  },
  "dest": {
    "index": "cluster_datastream_contribution_lookup",
    "pipeline": "set_consumption_composite_key"
  },
  "frequency": "60m",
  "sync": {
    "time": {
      "field": "event.ingested",
      "delay": "1h"
    }
  },
  "pivot": {
    "group_by": {
      "@timestamp": {
        "date_histogram": {
          "field": "@timestamp",
          "calendar_interval": "1d"
        }
      },
      "cluster_name": {
        "terms": {
          "field": "elasticsearch.cluster.name"
        }
      },
      "datastream": {
        "terms": {
          "field": "elasticsearch.index.datastream"
        }
      }
    },
    "aggregations": {
      "datastream_sum_indexing_time": {
        "sum": {
          "field": "elasticsearch.index.total.indexing.index_time_in_millis"
        }
      },
      "datastream_sum_query_time": {
        "sum": {
          "field": "elasticsearch.index.total.search.query_time_in_millis"
        }
      },
      "datastream_sum_store_size": {
        "sum": {
          "field": "elasticsearch.index.total.store.size_in_bytes"
        }
      },
      "datastream_sum_data_set_store_size": {
        "sum": {
          "field": "elasticsearch.index.primaries.store.total_data_set_size_in_bytes"
        }
      }
    }
  }
}
```

Start the transform.

```
POST _transform/cluster_datastream_contribution_transform/_start
```

### 2.4 Dimension: Data Tier and Data Stream (Lookup Index + Transform)

#### Create Lookup Index

```json
PUT cluster_tier_and_datastream_contribution_lookup
{
  "settings": {
    "index.mode": "lookup"
  },
  "mappings": {
    "properties": {
      "@timestamp": {
        "type": "date"
      },
      "composite_key": {
        "type": "keyword"
      },
      "composite_tier_key": {
        "type": "keyword"
      },
      "cluster_name": {
        "type": "keyword"
      },
      "deployment_id": {
        "type": "keyword"
      },
      "tier": {
        "type": "keyword"
      },
      "datastream": {
        "type": "keyword"
      },
      "tier_and_datastream_sum_indexing_time": {
        "type": "double"
      },
      "tier_and_datastream_sum_query_time": {
        "type": "double"
      },
      "tier_and_datastream_sum_store_size": {
        "type": "double"
      },
      "tier_and_datastream_sum_data_set_store_size": {
        "type": "double"
      }
    }
  }
}
```

#### Create Transform

```json
PUT _transform/cluster_tier_and_datastream_contribution_transform
{
  "description": "Aggregates daily total ECU usage per TIER and DATA STREAM from billing metrics, using ingested timestamps with a 1-hour sync delay and running every 60 minutes.",
  "source": {
    "index": [
      "monitoring-indices"
    ],
    "query": {
      "match_all": {}
    }
  },
  "dest": {
    "index": "cluster_tier_and_datastream_contribution_lookup",
    "pipeline": "set_consumption_composite_key"
  },
  "frequency": "60m",
  "sync": {
    "time": {
      "field": "event.ingested",
      "delay": "1h"
    }
  },
  "pivot": {
    "group_by": {
      "@timestamp": {
        "date_histogram": {
          "field": "@timestamp",
          "calendar_interval": "1d"
        }
      },
      "cluster_name": {
        "terms": {
          "field": "elasticsearch.cluster.name"
        }
      },
      "tier": {
        "terms": {
          "field": "elasticsearch.index.tier"
        }
      },
      "datastream": {
        "terms": {
          "field": "elasticsearch.index.datastream"
        }
      }
    },
    "aggregations": {
      "tier_and_datastream_sum_indexing_time": {
        "sum": {
          "field": "elasticsearch.index.total.indexing.index_time_in_millis"
        }
      },
      "tier_and_datastream_sum_query_time": {
        "sum": {
          "field": "elasticsearch.index.total.search.query_time_in_millis"
        }
      },
      "tier_and_datastream_sum_store_size": {
        "sum": {
          "field": "elasticsearch.index.total.store.size_in_bytes"
        }
      },
      "tier_and_datastream_sum_data_set_store_size": {
        "sum": {
          "field": "elasticsearch.index.primaries.store.total_data_set_size_in_bytes"
        }
      }
    }
  }
}
```

Start the transform.

```
POST _transform/cluster_tier_and_datastream_contribution_transform/_start
```

## 3. Load the Dashboard

- Navigate to _Deployment > Stack Management > Saved objects_
- Click *Import* and upload the ndjson file
- Select _Check for existing objects_

File: [`esql-dashboard.json`](./assets/saved_objects/esql-dashboard.ndjson)