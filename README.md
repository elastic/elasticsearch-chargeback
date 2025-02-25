# Chargeback module

FinOps is an operational framework and cultural practice which maximizes the business value of cloud, enables timely data-driven decision making, and creates financial accountability through collaboration between engineering, finance, and business teams.

The Chargeback module is there to help users answer the question: How is my organisation consuming the Elastic solution, and more specifically, to which tenants can I charge back what costs?

[Tech Preview] of the Chargeback module, based on the Elasticsearch Service Billing and the Elasticsearch integrations. This provides a breakdown of ECU per deployment, per data stream, per day. 

Version 0.2.0

## Dependencies

This process must be followed on your Monitoring cluster, to which all the monitoring data is sent.

- The Monitoring cluster must be on Elastic Stack 8.17.1 or higher.
- The Monitoring cluster must run on ECH (Elastic Cloud Hosted).
- Elasticsearch Service Billing integration [version 1.16.0+] needs to run on the Monitoring cluster.
- Elasticsearch integrations [version 1.0.0+] needs to collect data from all deployments sending data to the Monitoring cluster.
- Transform `logs-elasticsearch.index_pivot-default-{VERSION}` should be running on the Monitoring cluster.

## Setting up the assets

Please follow the instructions in order from the Kibana Development Console.

1. Create `set_composite_key` pipeline to add a composite key for enrichment correlation

This pipeline is used by the enrich policies to look up and match billing data to consumption data.

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

2. Create Billing Transform

This transform will provide billing data (total ECU) per day, per deployment. This transform will be executed every hour, and will consider all documents from the index `metrics-ess_billing.billing*` that has been indexed for an hour.

- Create `billing_cluster_cost` transform

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
      },
      "billing_type": {
        "terms": {
          "field": "ess.billing.type"
        }
      },
      "billing_name": {
        "terms": {
          "field": "ess.billing.name"
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
  },
  "settings": {}
}
```
- Make sure to start the transform
```sh
POST _transform/billing_cluster_cost/_start
```

3. Create Consumption Transform per deployment

This transform will provide consumption data - total querying time, total indexing time, and total storage size -  aggregated per *deployment* per day.

This transform will collect data from the `monitoring-indices` index (output of the `logs-elasticsearch.index_pivot-default-{VERSION}` that should be running). This transform will be executed every hour, and will consider all documents from the above mentioned index that has been indexed for an hour.

- Create `cluster_deployment_contribution` transform
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
      "cluster_name": {
        "terms": {
          "field": "elasticsearch.cluster.name"
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
      }
    }
  },
  "settings": {}
}
```
- Make sure to start the transform
```sh
POST _transform/cluster_deployment_contribution/_start
```

4. Create Consumption Transform per data stream

This transform will provide consumption data - total querying time, total indexing time, and total storage size - aggregated per *data stream* per day. It will also provide us data tier information.

This transform will collect data from the `monitoring-indices` index (output of the `logs-elasticsearch.index_pivot-default-{VERSION}` that should be running). This transform will be executed every hour, and will consider all documents from the above mentioned index that has been indexed for *24 hours*. This is needed to make sure we have all the data for the day bucket we will be calculating the cost for.

NOTE: Since the final version of this transform needs to run enrichment policies which are based on the output of this data, this transform will serve as a placeholder function for now. 

- Create `cluster_datastreams_contribution` transform
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
    "pipeline": "set_composite_key"
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
      }
    }
  },
  "settings": {}
}
```
- Make sure to start the transform
```sh
POST _transform/cluster_datastreams_contribution/_start
```

5. Create Enrichment policy: Cluster cost

The first enrichment policy will bring in the `total_ecu` for the deployment from the data. It also give us the `deployment_name`.

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
- Make sure to excute the policy, so that the internal `.enrich*` indices that will be the "lookup table" used later is created.
```sh
POST /_enrich/policy/cluster_cost_enrich_policy/_execute
```

6. Create Enrichment policy: Cluster contribution

The second enrichment policy will bring in the total query time, total indexing time and the total storage used by the deployment, or the deployment and data stream on the day.
```sh
PUT /_enrich/policy/cluster_contribution_enrich_policy
{
  "match": {
    "indices": "cluster_deployment_contribution",
    "match_field": "composite_key",
    "enrich_fields": ["sum_query_time","sum_indexing_time", "sum_store_size"]
  }
}
```
- Make sure to excute the policy, so that the internal `.enrich*` indices that will be the "lookup table" used later is created.
```sh
POST /_enrich/policy/cluster_contribution_enrich_policy/_execute
```

7. Create an ingest pipeline to enrich the consumption data with billing data

This ingest pipeline enriches the consumption data with billing data. It will use the `total_ecu` that we created in the `billing_cluster_cost` transform per deployment. It will also take the total indexing, querying and storage per deployment that we created in the `cluster_deployment_contribution` transform. Based on these figures, we are able to calculate the portions of the total for querying, indexing and storage for each data stream.

```sh
PUT _ingest/pipeline/cluster_cost_enrichment_pipeline
{
  "processors": [
    {
      "script": {
        "source": """
          ctx.composite_key = ctx['@timestamp'] + '_' + ctx.cluster_name;
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
        "field": "composite_key",
        "target_field": "deployment_contribution",
        "max_matches": 1
      }
    },
    {
      "script": {
      "source": """ 
            ctx.deployment_name = ctx.data_stream_cost.deployment_name;
            if (ctx.data_stream_cost?.total_ecu != null && 
                ctx.deployment_contribution?.sum_query_time != null && 
                ctx.deployment_contribution?.sum_indexing_time != null && 
                ctx.deployment_contribution?.sum_store_size != null) {
            
            if (ctx.deployment_contribution.sum_indexing_time != 0) {
                ctx.ecu_index_contribution = Math.round((ctx.sum_indexing_time / ctx.deployment_contribution.sum_indexing_time) * ctx.data_stream_cost.total_ecu * 1000) / 1000.0;
            } else {
                ctx.ecu_index_contribution = null;
            }

            if (ctx.deployment_contribution.sum_query_time != 0) {
                ctx.ecu_query_contribution = Math.round((ctx.sum_query_time / ctx.deployment_contribution.sum_query_time) * ctx.data_stream_cost.total_ecu * 1000) / 1000.0;
            } else {
                ctx.ecu_query_contribution = null;
            }

            if (ctx.deployment_contribution.sum_store_size != 0) {
                ctx.ecu_storage_contribution = Math.round((ctx.sum_store_size / ctx.deployment_contribution.sum_store_size) * ctx.data_stream_cost.total_ecu * 1000) / 1000.0;
            } else {
                ctx.ecu_storage_contribution = null;
            }
            } else {
            ctx.ecu_index_contribution = null;
            ctx.ecu_query_contribution = null;
            ctx.ecu_storage_contribution = null;
            }
        """
      }
    }
  ]
}
```

8. Add the ingest pipeline to the placeholder `cluster_datastreams_contribution` transform

Now, we will recreate the `cluster_datastreams_contribution` transform, and this time it will use the pipeline we just created, to look up all the values, and do the calculations described above. We first need to clean up, ie delete the index, stop the transform, and delete the transform. Thereafter, we can create and start it again.

- Clean up
```sh
# Clean up
DELETE cluster_datastreams_contribution
POST _transform/cluster_datastreams_contribution/_stop
DELETE _transform/cluster_datastreams_contribution
```
- Re-create `cluster_datastreams_contribution` transform
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
      }
    }
  },
  "settings": {}
}
```
- Make sure to start the transform
```sh
POST _transform/cluster_datastreams_contribution/_start
```

9. Automate refreshing the enrich policies

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

9. Load the Dashboard

If the data collected by the specified dependencies above is already there, i.e. if these have been running and collecting data for more than a day, there will be data to be displayed on the dashboard. However, if you have just set up the dependencies, then you will have to wait for 24 hours before you will see anything in the dashboard.

- Navigate to _Deployment > Stack Management > Saved objects_. 
- Click the *import* button located at the top right of the screen.
- Upload the file `chargeback_module_{version}.json`.
- Make sure to select _Check for existing objects_ so that the correct object IDs can be generated.

After this has been uploaded, you can navigate to the dashboard `[Tech Preview] Chargeback - Overview (0.2.0)` that gives an overview of the module's data. Other dashboards which you can navigate to from this dashboard are:
- `[Tech Preview] Chargeback - Billing Statistics (0.2.0)` that provides data parsed from the Elastic Service Billing integration.
- `[Tech Preview] Chargeback - Usage Statistics (0.2.0)` that provides insight into deployments, data streams and data tiers.
- `[Tech Preview] Chargeback - Meta Data (0.2.0)` that can be helpful when troubleshooting, as this provides the date ranges that has been parsed, etc.

