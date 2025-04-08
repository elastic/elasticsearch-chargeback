# DECOMMISIONING
Use below commands to decommision the chargeback components (remove them from the cluster)

>This includes removing the indices!

```
DELETE cluster_datastreams_contribution
DELETE _ingest/pipeline/cluster_cost_enrichment_pipeline

POST _enrich/policy/cluster_cost_enrich_policy/_stop
POST _enrich/policy/cluster_contribution_enrich_policy/_stop
DELETE _enrich/policy/cluster_cost_enrich_policy
DELETE _enrich/policy/cluster_contribution_enrich_policy

POST _transform/cluster_deployment_contribution/_stop
DELETE cluster_deployment_contribution
DELETE _transform/cluster_deployment_contribution
POST _transform/cluster_datastreams_contribution/_stop
DELETE _transform/cluster_datastreams_contribution
POST _transform/billing_cluster_cost/_stop
DELETE _transform/billing_cluster_cost

DELETE _ingest/pipeline/set_composite_key
```