FROM billing_cluster_cost_lookup 
| LOOKUP JOIN cluster_deployment_contribution_lookup ON composite_key
| LOOKUP JOIN cluster_tier_contribution_lookup ON composite_key
| EVAL 
    data_set = CASE (deployment_sum_data_set_store_size > 0, tier_sum_data_set_store_size / deployment_sum_data_set_store_size * total_ecu), // total data set storage
    store = CASE (deployment_sum_store_size > 0, tier_sum_store_size / deployment_sum_store_size * total_ecu), // only primary indices
    storage = CASE (store == 0 , data_set, store)
| STATS  
    agg_storage = sum(storage)
    BY 
    @timestamp,
    tier 
| WHERE agg_storage > 0