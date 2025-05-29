FROM monitoring-indices
| WHERE @timestamp < now() - 3 day
| EVAL 
    @timestamp = DATE_TRUNC(1 day, @timestamp),
    composite_key = CONCAT(DATE_FORMAT("yyyy-MM-dd", @timestamp), "_", elasticsearch.cluster.name),
    composite_tier_key = CONCAT(composite_key, "_", REPLACE(elasticsearch.index.tier,"/", "_"))
| STATS 
    sum_query_time_local = sum(elasticsearch.index.total.search.query_time_in_millis), 
    sum_indexing_time_local = sum(elasticsearch.index.total.indexing.index_time_in_millis),
    sum_data_set_store_size_local = sum(elasticsearch.index.primaries.store.total_data_set_size_in_bytes),
    sum_store_size_local = sum(elasticsearch.index.total.store.size_in_bytes)
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
| KEEP 
    @timestamp,composite_key,composite_tier_key,
    elasticsearch.cluster.name,elasticsearch.index.datastream,elasticsearch.index.tier,
    ecu_rate,total_ecu