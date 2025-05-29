FROM monitoring-indices
| WHERE @timestamp < now() - 3 day
| EVAL 
    @timestamp = DATE_TRUNC(1 day, @timestamp),
    composite_key = CONCAT(DATE_FORMAT("yyyy-MM-dd", @timestamp), "_", elasticsearch.cluster.name),
    composite_tier_key = CONCAT(composite_key, "_", REPLACE(elasticsearch.index.tier,"/", "_"))
| STATS 
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
| EVAL 
    ecu_storage_contribution = CASE (sum_data_set_store_size > 0, (sum_data_set_store_size_local / sum_data_set_store_size) * total_ecu, 0), 
    ecu_storage_contribution = CASE (sum_store_size > 0, (sum_store_size_local / sum_store_size) * total_ecu, 0)

| KEEP 
    @timestamp,composite_key,composite_tier_key,
    elasticsearch.cluster.name,elasticsearch.index.datastream,elasticsearch.index.tier,
    ecu_storage_contribution