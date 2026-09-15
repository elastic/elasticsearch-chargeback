# Elastic Chargeback cost analysis

You are analysing cost and usage data produced by the **Elasticsearch Chargeback**
integration on a monitoring cluster. This skill explains the data model, the arithmetic,
and the traps. Read it before making any claim about cost.

## Where the numbers come from

Two independent feeds, joined per day per deployment:

1. **Cost** — the Elasticsearch Service Billing integration writes
   `metrics-ess_billing.billing-*`, which the `billing_cluster_cost` transform aggregates
   into `billing_cluster_cost_lookup`: one row per day, per deployment, per SKU. This is
   the invoice. It covers Elastic Cloud Hosted deployments **and** Serverless projects.
2. **Usage** — per-index stats become `cluster_*_contribution_lookup`: bytes stored,
   indexing time and query time per day, aggregated by deployment, by tier, by data
   stream, and by tier+data stream.

Everything joins on `composite_key`, which is `YYYY-MM-DD_<deployment_id>`.

`chargeback_conf_lookup` holds the currency rate and the weights. Every cost figure is
`chargeable_units x conf_chargeable_unit_rate`, so **always read the configuration before
quoting a currency amount** — the rate and the currency are customer-configurable and
default to 0.85 EUR.

## How cost is attributed to a data stream

Billing does not know about data streams. Attribution is a **share of a pool**:

```
chargeable_pool = data_tier_capacity_ecu x utilization_score
datastream_cost = (datastream_sum_X / deployment_sum_X) x chargeable_pool x rate
```

for X in indexing time, query time, and storage bytes; the blended cost weights those
three by `conf_indexing_weight`, `conf_query_weight` and `conf_storage_weight`
(defaults 20 / 20 / 40, so storage dominates).

Two consequences you must understand:

- Because it is a **ratio**, the absolute magnitude of the `*_sum_*` fields is
  meaningless. They are sums of cumulative counter snapshots, so they scale with how
  often the source sampled. Never present a `*_sum_indexing_time` value as
  "milliseconds spent indexing". Only the *share* is meaningful.
- Only the **allocatable** pool is distributed. Data-tier capacity is allocatable;
  support, inference, data transfer, snapshots, ML and platform components are not.
  The per-data-stream costs will therefore **not** add up to the invoice, and that is
  correct, not a bug. Use `chargeback.unallocatable_cost` to quantify and explain the gap.

## Serverless projects

Serverless projects appear with `ess.billing.deployment_type: elasticsearch` and behave
differently in four ways. Do not describe these as data quality problems:

- **No data tiers.** All serverless usage is reported as `tier: serverless`. Tier-migration
  and ILM advice does not apply; do not recommend moving serverless data to a warm or cold
  tier, because those tiers do not exist there.
- **No utilization discount.** Serverless auto-scales, so there is no idle provisioned
  capacity. `cluster_capacity_utilization_lookup` has no rows and utilization is 1.0 by
  design. Rightsizing advice does not apply — recommend reducing *data volume* or *query
  load* instead.
- **Indexing and query signals are sparse.** Stateless shards recycle constantly, so
  counters cover only the current shard generation. Storage is the reliable signal. Prefer
  storage-based reasoning for serverless and say when you are doing so.
- **ML VCU is billed but not allocated.** It appears in the invoice and in
  `chargeback.unallocatable_cost`, never in per-data-stream costs.

### Identifying a Serverless project correctly

Use `chargeback.deployment_inventory`, which returns `deployment_type` directly:
`deployment` means Elastic Cloud Hosted; `elasticsearch`, `observability` and `security`
mean Serverless.

Do **not** infer the platform from either of these, because both are wrong:

- **A blank `deployment_name` is a hint, not proof.** Serverless projects do have a blank
  name, but always confirm with `deployment_type`.
- **Missing utilization data does not mean Serverless.** A *named* Hosted deployment with
  null `heap_used_pct_p95` and `disk_used_pct_p95` is simply a deployment that is not
  shipping `node_stats`. The fix there is to enable monitoring, and rightsizing advice
  still applies once data exists - it just cannot be quantified yet. Telling the owner of a
  Hosted deployment that it is Serverless, and that tier and ILM advice does not apply, is
  a materially wrong answer.

## Timing

Billing for day D arrives on **day D+1**. Usage for day D is written once the day bucket
closes. So the most recent complete, joinable day is normally yesterday. If cost-allocation
tools return no rows for today, that is expected — do not report it as missing data. Check
`chargeback.cost_trend_by_day` to see which days are actually populated before concluding
anything is broken.
