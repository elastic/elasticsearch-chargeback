FROM billing_cluster_cost_lookup 
| LOOKUP JOIN cluster_deployment_contribution_lookup ON composite_key
| LOOKUP JOIN cluster_datastream_contribution_lookup ON composite_key
| EVAL 
    indexing = CASE (deployment_sum_indexing_time > 0, datastream_sum_indexing_time / deployment_sum_indexing_time * total_ecu),
    querying = CASE (deployment_sum_query_time > 0, datastream_sum_query_time / deployment_sum_query_time * total_ecu),
    data_set = CASE (deployment_sum_data_set_store_size > 0, datastream_sum_data_set_store_size / deployment_sum_data_set_store_size * total_ecu), // total data set storage
    store = CASE (deployment_sum_store_size > 0, datastream_sum_store_size / deployment_sum_store_size * total_ecu), // only primary indices
    storage = CASE (store == 0 , data_set, store),
    storage_weight = 40,
    query_weight = 20,
    index_weight = 20,
    total_weight_hot = storage_weight + query_weight + index_weight,
    total_weight_cold = storage_weight + query_weight,
    blended = ((storage * storage_weight) +
            (querying * query_weight) +
            (indexing * index_weight)
        ) / total_weight_hot
| STATS  
    agg_blended = sum(blended)
    BY 
    @timestamp, 
    datastream.keyword
| WHERE agg_blended > 0 
| SORT agg_blended DESC