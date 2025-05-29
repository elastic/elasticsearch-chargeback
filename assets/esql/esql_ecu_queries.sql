FROM monitoring-indices
| WHERE @timestamp < now() - 3 day
| EVAL 
    @timestamp = DATE_TRUNC(1 day, @timestamp),
    composite_key = CONCAT(DATE_FORMAT("yyyy-MM-dd", @timestamp), "_", elasticsearch.cluster.name),
    composite_tier_key = CONCAT(composite_key, "_", REPLACE(elasticsearch.index.tier,"/", "_"))
| STATS 
    sum_query_time_local = sum(elasticsearch.index.total.search.query_time_in_millis)
  BY 
    @timestamp,
    composite_key,
    composite_tier_key,
    elasticsearch.cluster.name,
    elasticsearch.index.datastream,
    elasticsearch.index.tier
| LOOKUP JOIN billing_cluster_cost_lookup
ON composite_key
| LOOKUP JOIN cluster_deployment_contribution_lookup
ON composite_tier_key
| EVAL 
    ecu_query_contribution = CASE (sum_query_time > 0, (sum_query_time_local / sum_query_time) * total_ecu, 0)
| KEEP 
    @timestamp,composite_key,composite_tier_key,
    elasticsearch.cluster.name,elasticsearch.index.datastream,elasticsearch.index.tier,
    ecu_query_contribution