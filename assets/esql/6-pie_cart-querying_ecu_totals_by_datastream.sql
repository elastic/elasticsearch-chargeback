FROM billing_cluster_cost_lookup 
| LOOKUP JOIN cluster_deployment_contribution_lookup ON composite_key
| LOOKUP JOIN cluster_datastream_contribution_lookup ON composite_key
| EVAL 
    querying = CASE (deployment_sum_query_time > 0, datastream_sum_query_time / deployment_sum_query_time * total_ecu)
| STATS  
    agg_querying = sum(querying)
    BY 
    datastream.keyword
| WHERE agg_querying > 0 
| SORT agg_querying DESC