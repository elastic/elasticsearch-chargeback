# Elastic Chargeback cost optimization

Use together with the `chargeback-cost-analysis` skill, which defines the data model and
the attribution arithmetic. This skill covers turning that data into ranked, quantified
recommendations.

## Utilization and waste

`utilization_score` is a weighted blend of p95 heap and p95 disk utilization
(`conf_utilization_memory_weight` / `conf_utilization_storage_weight`, defaults 70 / 30),
floored at `conf_utilization_floor` (default 0.10).

**There are two unrelated sets of weights. Do not mix them up:**

| Weights | Fields | Defaults | What they control |
| --- | --- | --- | --- |
| Blended cost | `conf_indexing_weight`, `conf_query_weight`, `conf_storage_weight` | 20 / 20 / 40 | How indexing, query and storage shares combine into a data stream's blended cost |
| Utilization | `conf_utilization_memory_weight`, `conf_utilization_storage_weight` | 70 / 30 | How heap and disk combine into the utilization score that discounts the pool |

So when explaining *why* a deployment looks under-utilized, cite the utilization weights
(heap 70, disk 30) - not the storage weight of 40, which belongs to the blended cost formula
and has nothing to do with the utilization score.

- `provisioned_cost` is what the deployment is billed for its data tiers.
- `realized_cost` is `provisioned_cost x utilization_score`.
- The difference is the **rightsizing opportunity** — capacity paid for but not used.

**If `heap_used_pct_p95` and `disk_used_pct_p95` are both null, utilization defaults to
100%** and the waste figure will be zero. That does not mean the deployment is efficient;
it means there is no `node_stats` data. Say so explicitly rather than reporting zero waste.

Before explaining *why* utilization data is missing, call
`chargeback.deployment_inventory` and read `deployment_type`. For a Serverless project
the absence is by design. For a Hosted deployment it is a monitoring gap, and the
recommendation is to enable node stats so the opportunity can be measured - not to
declare the deployment Serverless and drop tier and ILM advice.

## How to give a useful answer

1. Establish the window and the configuration first.
2. Get totals before details — `chargeback.cost_by_deployment`, then drill in.
3. Separate *allocatable* from *unallocatable* spend so the numbers reconcile.
4. Check the trend before recommending anything, so you can say whether a cost is new,
   growing, or steady.
5. Quantify each recommendation in the configured currency, and state the assumption
   behind it.

Optimization levers, roughly in order of typical payoff:

- **Rightsizing** — the gap between provisioned and realized capacity. Hosted only.
- **Retention** — a data stream's storage share is usually the single biggest driver.
  Shortening ILM retention or deleting abandoned streams is the most direct saving.
- **Tier placement** — moving rarely-queried data to warm, cold or frozen. Look for
  streams with a large storage share and a near-zero query share. Hosted only.
- **Index mode** — `logsdb` or TSDB typically cut storage substantially for log and
  metric data.
- **Replicas** — each replica is a full copy of the storage cost.
- **Query load** — for serverless, search VCU is driven by query volume.

Be honest about precision. These are allocation estimates derived from shares, not
metered per-stream billing. When a figure rests on an assumption — a missing utilization
signal, a sparse counter, a partial day — say which, in one sentence, and continue.
Never invent a saving you cannot derive from the tools.
