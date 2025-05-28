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
| EVAL 
    ecu_query_contribution = CASE (sum_query_time > 0, (sum_query_time_local / sum_query_time) * total_ecu, 0), 
    ecu_value_query_contribution = CASE (sum_query_time > 0, (sum_query_time_local / sum_query_time) * total_ecu_value, 0),
    ecu_index_contribution = CASE (sum_indexing_time > 0, (sum_indexing_time_local / sum_indexing_time) * total_ecu, 0), 
    ecu_value_index_contribution = CASE (sum_indexing_time > 0, (sum_indexing_time_local / sum_indexing_time) * total_ecu_value, 0),
    // Gets the storage contribution from the primary data set size. For searchable snapshots this is the only value available.
    ecu_storage_contribution = CASE (sum_data_set_store_size > 0, (sum_data_set_store_size_local / sum_data_set_store_size) * total_ecu, 0), 
    ecu_value_storage_contribution = CASE (sum_data_set_store_size > 0, (sum_data_set_store_size_local / sum_data_set_store_size) * total_ecu_value, 0),
    // Overwrites the storage contribution when we have sum_store_size availble. This will be the case for all non-searchable snapshot data streams.
    ecu_storage_contribution = CASE (sum_store_size > 0, (sum_store_size_local / sum_store_size) * total_ecu, 0), 
    ecu_value_storage_contribution = CASE (sum_store_size > 0, (sum_store_size_local / sum_store_size) * total_ecu_value, 0),
    // Weighted ECU contribution calculation
    // Edit these weights
    storage_weight = 40,
    query_weight = 20,
    index_weight = 20,
    total_weight_hot = storage_weight + query_weight + index_weight,
    total_weight_cold = storage_weight + query_weight,
    ecu_weighted_contribution = CASE (
        elasticsearch.index.tier == "hot/content",
        (
            (ecu_storage_contribution * storage_weight) +
            (ecu_query_contribution * query_weight) +
            (ecu_index_contribution * index_weight)
        ) / total_weight_hot,
        (
            (ecu_storage_contribution * storage_weight) +
            (ecu_query_contribution * query_weight)
        ) / total_weight_cold
    ),
    ecu_value_weighted_contribution = CASE (
        elasticsearch.index.tier == "hot/content",
        (
            (ecu_value_storage_contribution * storage_weight) +
            (ecu_value_query_contribution * query_weight) +
            (ecu_value_index_contribution * index_weight)
        ) / total_weight_hot,
        (
            (ecu_value_storage_contribution * storage_weight) +
            (ecu_value_query_contribution * query_weight)
        ) / total_weight_cold
    )
| KEEP 
    @timestamp,composite_key,composite_tier_key,
    elasticsearch.cluster.name,elasticsearch.index.datastream,elasticsearch.index.tier,
    ecu_rate,total_ecu,total_ecu_value,
    ecu_query_contribution, 
    ecu_index_contribution, 
    ecu_storage_contribution,
    ecu_weighted_contribution
//| LIMIT 10