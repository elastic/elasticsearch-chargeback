# On-Premises Billing Integration (`onprem_billing`)

The **On-Premises Billing** integration bridges self-managed and on-premises Elasticsearch clusters into the Elastic FinOps framework. It converts on-premises hardware capacity, RAM, and Elastic Resource Units (ERUs) into normalized billing metric documents compatible with the standard **Elasticsearch Chargeback** integration.

## How it works

1. **Deployment Discovery & Bootstrap**:
   The integration's `config_bootstrap` transform monitors cluster state and populates the `onprem_billing_config` index with discovered deployment entries.
2. **License & ERU Configuration**:
   An organization config document defines the total purchased ERUs, annual license cost, RAM-to-ERU conversion ratio, and currency unit. Individual deployment documents specify allocated ERUs and chargeback group tags.
3. **Cost Calculation Pipeline**:
   The `calculate_cost` ingest pipeline enriches discovered deployment metrics with organization license rates and deployment tags, translating node counts and RAM allocations into `total_ecu` equivalent charges written to `metrics-ess_billing.billing-onprem`.
4. **Unified Chargeback**:
   Because the destination data stream conforms to the standard ESS Billing schema, standard Chargeback transforms (`billing_cluster_cost_lookup`, `billing_realized_pool_lookup`) allocate on-prem costs alongside Elastic Cloud Hosted deployments without requiring any changes to Chargeback dashboards or allocation logic.

## Assets in this directory

- [`assets/dist/`](assets/dist/): Packaged integration zip files (`onprem_billing-0.3.4.zip`, `onprem_billing-0.3.3.zip`).
- [`assets/pipelines/`](assets/pipelines/): Ingest pipeline definitions (`onprem_billing_calculate_cost_pipeline.json`).
- [Instructions.md](Instructions.md): Complete deployment, configuration, and verification instructions.
