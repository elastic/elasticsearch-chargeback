FROM billing_cluster_cost_lookup 
| LOOKUP JOIN cluster_deployment_contribution_lookup ON composite_key
| LOOKUP JOIN cluster_datastream_contribution_lookup ON composite_key
| EVAL 
    indexing = CASE (deployment_sum_indexing_time > 0, datastream_sum_indexing_time / deployment_sum_indexing_time * total_ecu)
| STATS  
    agg_indexing = sum(indexing)
    BY 
    datastream.keyword
| WHERE agg_indexing > 0 
| SORT agg_indexing DESC