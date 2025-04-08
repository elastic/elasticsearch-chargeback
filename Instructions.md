# Chargeback Module Setup Instructions

Follow these steps in order using the Kibana Dev Console.

## 1. Create Pipelines

### Set Composite Key Pipeline
This standardises `deployment_id` from `cluster_name` and generates a `composite_key`.

```json
PUT _ingest/pipeline/set_composite_key
{
  "description": "Standardise on deployment_id (vs cluster_name) and set composite_key from @timestamp and deployment_id",
  "processors": [
    {
      "script": {
        "lang": "painless",
        "source": """
          if (ctx.cluster_name != null) {
            ctx.deployment_id = ctx.cluster_name;
          }
          if (ctx['@timestamp'] != null) {
              ctx.composite_key = ctx['@timestamp'] + '_' + ctx.deployment_id;
          }
        """
      }
    }
  ]
}
```
File: [`set_composite_key_pipeline.json`](./assets/pipelines/set_composite_key_pipeline.json)

### Set Composite Tier Key Pipeline
Extends `set_composite_key` by adding the tier value to `composite_key`.

```json
PUT _ingest/pipeline/set_composite_tier_key
{
  "description": "Standardise on deployment_id (vs cluster_name) and set composite_key from @timestamp, deployment_id and tier",
  "processors": [
    {
      "script": {
        "lang": "painless",
        "source": """
          if (ctx.cluster_name != null) {
            ctx.deployment_id = ctx.cluster_name;
          }
          if (ctx['@timestamp'] != null) {
              ctx.composite_tier_key = ctx['@timestamp'] + '_' + ctx.deployment_id + '_' + ctx.tier.replace("/", "_");
          }
        """
      }
    }
  ]
}
```
File: [`set_composite_tier_key_pipeline.json`](./assets/pipelines/set_composite_tier_key_pipeline.json)

### Add pipeline for the ESS billing integration
To calculate the _value_ of your ECU, you need to add a rate to the ESS billing information. This will cascade down.
Modify the `ess.billing.ecu_value` field with your value rate. E.g if 1 ECU is $2.2 worth you would modify the `0.85` to `2.2`

>These instructions assume this pipeline does not exist yet!

```json
PUT _ingest/pipeline/metrics-ess_billing.billing@custom
{
    "description": "Add the value of ECU to the billing information",
    "processors": [
        {
            "set": {
                "field": "ess.billing.ecu_value",
                "value": 0.85
            }
        },
        {
            "script": {
                "lang": "painless",
                "tag": "cost_script",
                "description": "calculates the total ECU value based on the ecu_value field and ess.billing.total_ecu",
                "source": "ctx['ess']['billing']['total_ecu_value'] = ctx['ess']['billing']['total_ecu'] * ctx['ess']['billing']['ecu_value'];",
                "ignore_failure": true
            }
        },
        {
            "script": {
                "lang": "painless",
                "tag": "cost_script",
                "description": "calculates the ECU rate value based on the ecu_value field and ess.billing.rate.value",
                "source": "ctx['ess']['billing']['rate']['ecu_value'] = ctx['ess']['billing']['rate']['value'] * ctx['ess']['billing']['ecu_value'];ctx['ess']['billing']['rate']['ecu_formatted_value'] = ctx['ess']['billing']['rate']['value'] * ctx['ess']['billing']['ecu_value'] + ' per ' + ctx['ess']['billing']['unit'];",
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
File: [`ess_billing_custom.json`](./assets/pipelines/ess_billing_custom.json)


## 2. Create Billing Transform
Aggregates ECU consumption per deployment per day. Runs hourly, processing `metrics-ess_billing.billing-default`.

```json
PUT _transform/billing_cluster_cost
{
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
    "index": "billing_cluster_cost",
    "pipeline": "set_composite_key"
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
      },
      "ecu_rate": {
        "terms": {
          "field": "ess.billing.ecu_value"
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
      }
    }
  }
}
```
File: [`billing_cluster_cost_transform.json`](./assets/transforms/billing_cluster_cost_transform.json)

```sh
POST _transform/billing_cluster_cost/_start
```

## 3. Create Consumption Transforms

### Per Deployment
Aggregates query time, indexing time, and storage size per deployment per day.

```json
PUT _transform/cluster_deployment_contribution
{
  "source": {
    "index": [
      "monitoring-indices"
    ],
    "query": {
      "match_all": {}
    }
  },
  "dest": {
    "index": "cluster_deployment_contribution",
    "pipeline": "set_composite_tier_key"
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
      "sum_query_time": {
        "sum": {
          "field": "elasticsearch.index.total.search.query_time_in_millis"
        }
      },
      "sum_indexing_time": {
        "sum": {
          "field": "elasticsearch.index.total.indexing.index_time_in_millis"
        }
      },
      "sum_store_size": {
        "sum": {
          "field": "elasticsearch.index.total.store.size_in_bytes"
        }
      },
      "sum_data_set_store_size": {
        "sum": {
          "field": "elasticsearch.index.primaries.store.total_data_set_size_in_bytes"
        }
      }
    }
  }
}
```
File: [`cluster_deployment_contribution_transform.json`](./assets/transforms/cluster_deployment_contribution_transform.json)

```sh
POST _transform/cluster_deployment_contribution/_start
```

### Per Data Stream
Aggregates query time, indexing time, and storage size per deployment, per tier, per day. Runs hourly with a 24-hour delay to ensure completeness. This does mean that if you just setup the required integrations, you don't have 24h old data yet.

**Note:** Since the enrichment policies and pipelines are interdependent on the data stream transform, we first create the transform without the final pipeline.

```json
PUT _transform/cluster_datastreams_contribution
{
  "source": {
    "index": [
      "monitoring-indices"
    ],
    "query": {
      "match_all": {}
    }
  },
  "dest": {
    "index": "cluster_datastreams_contribution",
    "pipeline": "set_composite_tier_key"
  },
  "frequency": "60m",
  "sync": {
    "time": {
      "field": "event.ingested",
      "delay": "24h"
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
      "data_stream": {
        "terms": {
          "field": "elasticsearch.index.datastream"
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
      "sum_query_time": {
        "sum": {
          "field": "elasticsearch.index.total.search.query_time_in_millis"
        }
      },
      "sum_indexing_time": {
        "sum": {
          "field": "elasticsearch.index.total.indexing.index_time_in_millis"
        }
      },
      "sum_store_size": {
        "sum": {
          "field": "elasticsearch.index.total.store.size_in_bytes"
        }
      },
      "sum_data_set_store_size": {
        "sum": {
          "field": "elasticsearch.index.primaries.store.total_data_set_size_in_bytes"
        }
      }
    }
  }
}
```
File: [`cluster_datastreams_contribution-placeholder_transform.json`](./assets/transforms/cluster_datastreams_contribution-placeholder_transform.json)

```sh
POST _transform/cluster_datastreams_contribution/_start
```

## 4. Create Enrichment Policies

### Cluster Cost
Joins `total_ecu` and `deployment_name` from Billing integration with usage data.

```json
PUT /_enrich/policy/cluster_cost_enrich_policy
{
  "match": {
    "indices": "billing_cluster_cost",
    "match_field": "composite_key",
    "enrich_fields": ["total_ecu","deployment_name","total_ecu_value"]
  }
}
```
File: [`cluster_cost_enrich_policy.json`](./assets/enrich/cluster_cost_enrich_policy.json)

```sh
POST /_enrich/policy/cluster_cost_enrich_policy/_execute
```

### Cluster Contribution
Joins `sum_query_time`, `sum_indexing_time`, `sum_store_size`, `sum_data_set_store_size`, and `tier` from Elasticsearch integration with usage data.

```json
PUT /_enrich/policy/cluster_contribution_enrich_policy
{
  "match": {
    "indices": "cluster_deployment_contribution",
    "match_field": "composite_tier_key",
    "enrich_fields": ["sum_query_time","sum_indexing_time", "sum_store_size", "sum_data_set_store_size", "tier"]
  }
}
```
File: [`cluster_contribution_enrich_policy.json`](./assets/enrich/cluster_contribution_enrich_policy.json)

```sh
POST /_enrich/policy/cluster_contribution_enrich_policy/_execute
```

## 5. Create Enrichment Ingest Pipeline
This pipeline enriches consumption data with billing data, using the results from previous transforms.

```json
PUT _ingest/pipeline/cluster_cost_enrichment_pipeline
{
  "processors": [
    {
      "script": {
        "source": """
          ctx.composite_key = ctx['@timestamp'] + '_' + ctx.cluster_name;
          ctx.composite_tier_key = ctx.composite_key + "_" + ctx.tier.replace("/","_");
          ctx.deployment_id = ctx.cluster_name;
        """
      }
    },
    {
      "enrich": {
        "policy_name": "cluster_cost_enrich_policy",
        "field": "composite_key",
        "target_field": "data_stream_cost",
        "max_matches": 1
      }
    },
    {
      "enrich": {
        "policy_name": "cluster_contribution_enrich_policy",
        "field": "composite_tier_key",
        "target_field": "deployment_contribution",
        "max_matches": 1
      }
    },
    {
      "script": {
      "source": """ 
        if (ctx.data_stream_cost != null && ctx.deployment_contribution != null) {

            ctx.deployment_name = ctx.data_stream_cost.deployment_name;

            if (ctx.sum_indexing_time > 0) {
                if (ctx.deployment_contribution.sum_indexing_time != null && ctx.deployment_contribution.sum_indexing_time != 0) 
                    ctx.ecu_index_contribution = Math.round((ctx.sum_indexing_time / ctx.deployment_contribution.sum_indexing_time) * ctx.data_stream_cost.total_ecu * 1000) / 1000.0;
                    ctx.ecu_value_index_contribution = Math.round((ctx.sum_indexing_time / ctx.deployment_contribution.sum_indexing_time) * ctx.data_stream_cost.total_ecu_value * 1000) / 1000.0;
            }

            if (ctx.sum_query_time > 0) {
                if (ctx.deployment_contribution.sum_query_time != null && ctx.deployment_contribution.sum_query_time != 0)
                    ctx.ecu_query_contribution = Math.round((ctx.sum_query_time / ctx.deployment_contribution.sum_query_time) * ctx.data_stream_cost.total_ecu * 1000) / 1000.0;
                    ctx.ecu_value_query_contribution = Math.round((ctx.sum_query_time / ctx.deployment_contribution.sum_query_time) * ctx.data_stream_cost.total_ecu_value * 1000) / 1000.0;
            }

            // Gets the storage contribution from the primary data set size. For searchable snapshots this is the only value available.
            if (ctx.sum_data_set_store_size > 0) {
                if (ctx.deployment_contribution.sum_data_set_store_size != null && ctx.deployment_contribution.sum_data_set_store_size != 0)
                    ctx.ecu_storage_contribution = Math.round((ctx.sum_data_set_store_size / ctx.deployment_contribution.sum_data_set_store_size) * ctx.data_stream_cost.total_ecu * 1000000) / 1000000.0;
                    ctx.ecu_value_storage_contribution = Math.round((ctx.sum_data_set_store_size / ctx.deployment_contribution.sum_data_set_store_size) * ctx.data_stream_cost.total_ecu_value * 1000000) / 1000000.0;
            }

            // Overwrites the storage contribution when we have sum_store_size availble. This will be the case for all non-searchable snapshot data streams.
            if (ctx.sum_store_size > 0) {
              if (ctx.deployment_contribution.sum_store_size != null && ctx.deployment_contribution.sum_store_size != 0)
                  ctx.ecu_storage_contribution = Math.round((ctx.sum_store_size / ctx.deployment_contribution.sum_store_size) * ctx.data_stream_cost.total_ecu * 1000000) / 1000000.0;
                  ctx.ecu_value_storage_contribution = Math.round((ctx.sum_store_size / ctx.deployment_contribution.sum_store_size) * ctx.data_stream_cost.total_ecu_value * 1000000) / 1000000.0;
            }
         }
        """
      }
    }
  ]
}
```
File: [`cluster_cost_enrichment_pipeline.json`](./assets/pipelines/cluster_cost_enrichment_pipeline.json)

## 6. Recreate data stream Transform
First, clean up:

```sh
POST _transform/cluster_datastreams_contribution/_stop
DELETE cluster_datastreams_contribution
DELETE _transform/cluster_datastreams_contribution
```

Then, recreate and start the transform with the correct pipelines.

```json
PUT _transform/cluster_datastreams_contribution
{
  "source": {
    "index": [
      "monitoring-indices"
    ],
    "query": {
      "match_all": {}
    }
  },
  "dest": {
    "index": "cluster_datastreams_contribution",
    "pipeline": "cluster_cost_enrichment_pipeline"
  },
  "frequency": "60m",
  "sync": {
    "time": {
      "field": "event.ingested",
      "delay": "24h"
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
      "data_stream": {
        "terms": {
          "field": "elasticsearch.index.datastream"
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
      "sum_query_time": {
        "sum": {
          "field": "elasticsearch.index.total.search.query_time_in_millis"
        }
      },
      "sum_indexing_time": {
        "sum": {
          "field": "elasticsearch.index.total.indexing.index_time_in_millis"
        }
      },
      "sum_store_size": {
        "sum": {
          "field": "elasticsearch.index.total.store.size_in_bytes"
        }
      },
      "sum_data_set_store_size": {
        "sum": {
          "field": "elasticsearch.index.primaries.store.total_data_set_size_in_bytes"
        }
      }
    }
  }
}
```
File: [`cluster_datastreams_contribution_transform.json`](./assets/transforms/cluster_datastreams_contribution_transform.json)

```sh
POST _transform/cluster_datastreams_contribution/_start
```

As explained, you need 24h of data being available. If you just setup the integrations, wait for 24h+ then perform the actions.

## 7. Add Runtime Fields for Blended Cost Calculation
Create a runtime field on `cluster_datastreams_contribution` with default weights:
- **Indexing**: 20 (only for hot tier)
- **Querying**: 20
- **Storage**: 40

Weights can be adjusted based on requirements.

```json
PUT cluster_datastreams_contribution/_mapping
{
  "runtime": {
    "blended_cost": {
      "type": "double",
      "script": {
        "source": """
          // Edit these weights
          def storage_weight = 40;
          def indexing_weight = 20;
          def search_weight = 20;
          
          // Do not change this
          def storage_cost = doc['ecu_storage_contribution'].size() != 0 ? doc['ecu_storage_contribution'].value : 0;
          def indexing_cost = doc['ecu_index_contribution'].size() != 0 ? doc['ecu_index_contribution'].value : 0;
          def search_cost = doc['ecu_query_contribution'].size() != 0 ? doc['ecu_query_contribution'].value : 0;
          def total_weight = storage_weight + search_weight;
          def weighted_costs = (storage_cost * storage_weight) + (search_cost * search_weight);
          
          // For hot tier, also consider indexing
          // For any other tier, indexing is not considered
          if (doc['tier'].value == 'hot/content') {
            total_weight += indexing_weight;
            weighted_costs += indexing_cost * indexing_weight;
          }
          emit(weighted_costs / total_weight);
        """
      }
    }
  }
}
```
File: [`cluster_datastreams_contribution_mapping.json`](./assets/mappings/cluster_datastreams_contribution_mapping.json)

## 8. Automate Enrich Policy Refreshing
Two Watchers will execute daily to refresh the enrichment data. Follow these steps:
- Create a role with minimal privileges.
- Create a user with these privileges (choose your own password).
- Set up Watchers for (add password and endpoint):
  - `execute_cluster_cost_enrich_policy`
  - `execute_cluster_contribution_enrich_policy`

```json
PUT /_security/role/enrichment_policy_role
{
  "cluster": ["manage_enrich"],
  "indices": [
    {
      "names": ["cluster_deployment_contribution", "billing_cluster_cost"],
      "privileges": ["view_index_metadata", "read"]
    }
  ]
}
```

```json
POST /_security/user/cf-watcher-user
{
  "password": "{SELECT_YOUR_OWN_PASSWORD}",
  "roles": ["enrichment_policy_role"],
  "full_name": "CF Watcher User",
  "email": "cf-watcher-user@example.com"
}
```

```json
PUT _watcher/watch/execute_cluster_contribution_enrich_policy
{
    "trigger" : {
        "schedule" : { "daily" : { "at" : "00:05" } } 
    },
    "input" : {
        "http" : {
            "request" : {
                "url" : "{ELASTIC_ENDPOINT}/_enrich/policy/cluster_contribution_enrich_policy/_execute",
                "method": "post",
                "auth" : {
                    "basic" : {
                    "username" : "cf-watcher-user",
                    "password" : "{SELECT_YOUR_OWN_PASSWORD}"
                    }
                }
            }
        }
    }
}
```

```json
PUT _watcher/watch/execute_cluster_cost_enrich_policy
{
    "trigger" : {
        "schedule" : { "daily" : { "at" : "00:05" } } 
    },
    "input" : {
        "http" : {
            "request" : {
                "url" : "{ELASTIC_ENDPOINT}/_enrich/policy/cluster_cost_enrich_policy/_execute",
                "method": "post",
                "auth" : {
                    "basic" : {
                    "username" : "cf-watcher-user",
                    "password" : "{SELECT_YOUR_OWN_PASSWORD}"
                    }
                }
            }
        }
    }
}
```

Files: 
- [`enrichment_policy_role.json`](./assets/security/enrichment_policy_role.json)
- [`cf-watcher-user.json`](./assets/security/cf-watcher-user.json)
- [`execute_cluster_contribution_enrich_policy_watcher.json`](./assets/watchers/execute_cluster_contribution_enrich_policy_watcher.json)
- [`execute_cluster_cost_enrich_policy_watcher.json`](./assets/watchers/execute_cluster_cost_enrich_policy_watcher.json)

## 9. Load the Dashboard

- Navigate to _Deployment > Stack Management > Saved objects_
- Click *Import* and upload the ndjson file
- Select _Check for existing objects_

File: [`chargeback_module.ndjson`](./assets/saved_objects/chargeback_module.ndjson)
