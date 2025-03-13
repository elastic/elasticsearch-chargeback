# Chargeback module

FinOps is an operational framework and cultural practice designed to maximize the business value of cloud usage. It enables timely, data-driven decision-making and fosters financial accountability through collaboration between engineering, finance, and business teams.

The **Chargeback Module** helps users answer a key question: How is my organisation consuming the Elastic solution, and to which tenants can I allocate these costs?

The Chargeback Module is based on the **Elasticsearch Service Billing** and **Elasticsearch** integrations. Its purpose is to provide data for chargeback calculations, offering a breakdown of Elastic Consumption Units (ECU) per:
- Deployment
- Data tier
- Data stream
- Day

Version 0.2.0

## Dependencies

This process must be set up on the **Monitoring cluster**, where all monitoring data is collected.

### Requirements:
- The Monitoring cluster must be running Elastic Stack 8.17.1 or higher.
- The Monitoring cluster must be hosted on Elastic Cloud (ECH).
- **Elasticsearch Service Billing** integration (version 1.16.0+) must be installed on the Monitoring cluster.
- **Elasticsearch** integration (version 1.0.0+) must collect data from all deployments sending data to the Monitoring cluster.
- The **Transform**  `logs-elasticsearch.index_pivot-default-{VERSION}` must be running on the Monitoring cluster.

> The `logs-elasticsearch.index_pivot-default-{VERSION}` transform job will process all compatible historical data, which has two implications: 1. if you have pre-8.17.1 data, this will not get picked up by the job and 2. it might take time for "live" data to be available, as the transform job works its way through all documents.

## Setting up the assets

Follow the instructions below in order, using the Kibana Dev Console.

1. Create the `set_composite_key` pipeline

This pipeline standardises the `deployment_id` (from `cluster_name`) and generates a `composite_key` by concatenating the timestamp and deployment ID. This key is used for billing enrichment correlation.

```sh
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

2. Create the `set_composite_tier_key` pipeline

This pipeline extends `set_composite_key` by including the tier value in the composite key. This key is used for consumption enrichment correlation.

```sh
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

3. Create the Billing transform

This transform aggregates ECU consumption per deployment per day. It runs hourly and processes documents from `metrics-ess_billing.billing-default`.

```sh
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
      }
    },
    "aggregations": {
      "total_ecu": {
        "sum": {
          "field": "ess.billing.total_ecu"
        }
      }
    }
  }
}
```

Start the transform:
```sh
POST _transform/billing_cluster_cost/_start
```

4. Create the Consumption transform per deployment

This transform aggregates query time, indexing time, and storage size per deployment per day. It processes data from `monitoring-indices`.

```sh
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

Start the transform:
```sh
POST _transform/cluster_deployment_contribution/_start
```

5. Create the Consumption transform per data stream

This transform aggregates query time, indexing time, and storage size per deployment, per tier per day. It also processes data from `monitoring-indices`.

This transform will be executed every hour, and will consider all documents from the above mentioned index that has been indexed for *24 hours*. This is needed to make sure we have all the data for the day bucket we will be calculating the cost for.

NOTE: Since the final version of this transform needs to run enrichment policies which are based on the output of this data, this transform will serve as a placeholder function for now. 

```sh
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

Start the transform:
```sh
POST _transform/cluster_datastreams_contribution/_start
```

6. Create Enrichment policy: Cluster cost

The first enrichment policy will be used to join the `total_ecu` and `deployment_name` from the Billing integration data with the usage data.

```sh
PUT /_enrich/policy/cluster_cost_enrich_policy
{
  "match": {
    "indices": "billing_cluster_cost",
    "match_field": "composite_key",
    "enrich_fields": ["total_ecu","deployment_name"]
  }
}
```

Execute the enrichment policy so that the internal `.enrich*` indices that will be the "lookup table" used later is created.
```sh
POST /_enrich/policy/cluster_cost_enrich_policy/_execute
```

7. Create Enrichment policy: Cluster contribution
> This step will require you to wait untill the `cluster_deployment_contribution` has processed data, which will take about 1h
> Execute `GET _transform/cluster_deployment_contribution/_stats` to see processed information

The second enrichment policy will be used to join the `sum_query_time`,`sum_indexing_time`, `sum_store_size`, `sum_data_set_store_size`, and `tier` from the Elasticsearch integration data with the usage data.

```sh
PUT /_enrich/policy/cluster_contribution_enrich_policy
{
  "match": {
    "indices": "cluster_deployment_contribution",
    "match_field": "composite_tier_key",
    "enrich_fields": ["sum_query_time","sum_indexing_time", "sum_store_size", "sum_data_set_store_size", "tier"]
  }
}
```

Execute the enrichment policy so that the internal `.enrich*` indices that will be the "lookup table" used later is created.
```sh
POST /_enrich/policy/cluster_contribution_enrich_policy/_execute
```

8. Create an ingest pipeline to enrich the consumption data with billing data

This ingest pipeline enriches the consumption data with billing data. It will use the `total_ecu` that we created in the `billing_cluster_cost` transform per deployment. It will also take the total indexing, querying and storage per deployment that we created in the `cluster_deployment_contribution` transform. Based on these figures, we are able to calculate the portions of the total for querying, indexing and storage for each data stream.

```sh
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
            }

            if (ctx.sum_query_time > 0) {
                if (ctx.deployment_contribution.sum_query_time != null && ctx.deployment_contribution.sum_query_time != 0)
                    ctx.ecu_query_contribution = Math.round((ctx.sum_query_time / ctx.deployment_contribution.sum_query_time) * ctx.data_stream_cost.total_ecu * 1000) / 1000.0;
            }

            // Gets the storage contribution from the primary data set size. For searchable snapshots this is the only value available.
            if (ctx.sum_data_set_store_size > 0) {
                if (ctx.deployment_contribution.sum_data_set_store_size != null && ctx.deployment_contribution.sum_data_set_store_size != 0)
                    ctx.ecu_storage_contribution = Math.round((ctx.sum_data_set_store_size / ctx.deployment_contribution.sum_data_set_store_size) * ctx.data_stream_cost.total_ecu * 1000000) / 1000000.0;
            }

            // Overwrites the storage contribution when we have sum_store_size availble. This will be the case for all non-searchable snapshot data streams.
            if (ctx.sum_store_size > 0) {
              if (ctx.deployment_contribution.sum_store_size != null && ctx.deployment_contribution.sum_store_size != 0)
                  ctx.ecu_storage_contribution = Math.round((ctx.sum_store_size / ctx.deployment_contribution.sum_store_size) * ctx.data_stream_cost.total_ecu * 1000000) / 1000000.0;
            }
        }
        """
      }
    }
  ]
}
```

9. Add the ingest pipeline to the placeholder `cluster_datastreams_contribution` transform

Now, we will recreate the `cluster_datastreams_contribution` transform, and this time it will use the pipeline we just created, to look up all the values, and do the calculations described above. We first need to clean up, ie delete the index, stop the transform, and delete the transform. Thereafter, we can create and start it again.

First, we will stop the running transform, and delete the transform and resulting destination index:
```sh
# Clean up
POST _transform/cluster_datastreams_contribution/_stop
DELETE cluster_datastreams_contribution
DELETE _transform/cluster_datastreams_contribution
```

The we will re-create the `cluster_datastreams_contribution` transform
```sh
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

Start the transform:
```sh
POST _transform/cluster_datastreams_contribution/_start
```

10. Add the runtime fields to calculate a blended cost

To be able to take indexing, querying and storage into consideration in a weighted fasion, we create a runtime field on the `cluster_datastreams_contribution` index with the following default weights:
- indexing: 20 (only considered for the hot tier)
- querying: 20
- storage: 40

This means that storage will contribute the most to the blended cost calculation, and that indexing will only contribute to this blended cost on the hot tier. You should consider these weights, and adjust these based on your own best judgement. We consider these good default values.

```sh
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

11. Automate refreshing the enrich policies

At this step the correct data will be collected already, but if the enrich policies are not refreshed on a daily basis, the lookup data required by the ingest policy created above, will not be available, and the data will not be populated properly. For the automation we will create a user that will be used by two watchers. The watchers will be executed once a day to update the enrich data.

Make sure you treat the credentials in the proper way, as dictated by your company.

- Create a role with minimum privileges
```sh
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
- Create a user with this limited privileges (replace `{PLACEHOLDERS}`)
```sh
POST /_security/user/cf-watcher-user
{
  "password": "{SELECT_YOUR_OWN_PASSWORD}",
  "roles": ["enrichment_policy_role"],
  "full_name": "CF Watcher User",
  "email": "cf-watcher-user@example.com"
}
```
- Create `execute_cluster_contribution_enrich_policy` watcher (replace `{PLACEHOLDERS}`)
```sh
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
- Create `execute_cluster_cost_enrich_policy` watcher (replace `{PLACEHOLDERS}`)
```sh
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

12. Load the Dashboard

If the data collected by the specified dependencies above is already there, i.e. if these have been running and collecting data for more than a day, there will be data to be displayed on the dashboard. However, if you have just set up the dependencies, then you will have to wait for 24 hours before you will see anything in the dashboard.

- Navigate to _Deployment > Stack Management > Saved objects_. 
- Click the *import* button located at the top right of the screen.
- Upload the file `chargeback_module_{version}.json`.
- Make sure to select _Check for existing objects_ so that the correct object IDs can be generated.

After this has been uploaded, you can navigate to the dashboard `[Tech Preview] Chargeback (0.2.0)` that provides chargeback insight into deployments, data streams and data tiers.
> For new deployments, or newly upgraded to 8.17.1+, a 24h waiting period is required to allow the transforms to generate the first insights.

The dashboard also links out to:
- `[Tech Preview] Chargeback - Meta Data (0.2.0)` which can be helpful when troubleshooting, as this provides the date ranges that has been parsed, etc.
- `[Metrics ESS Billing] Billing dashboard`, the dashboard for the Billing integration.
- `[Elasticsearch] Indices & data streams usage (Technical Preview/Beta)`, the dashboard for the Elasticsearch integration usage data.

## Sample dashboard

![Chargeback](https://github.com/elastic/elasticsearch-chargeback/blob/main/img/%5BTech%20Preview%5D%20Chargeback%20(0.2.0).png)
