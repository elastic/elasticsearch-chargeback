FROM billing_cluster_cost_lookup
| KEEP 
    @timestamp, 
    deployment_name.keyword, 
    total_ecu, 
    total_ecu_value