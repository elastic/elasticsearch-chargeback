# Chargeback Serverless dashboards — design

**Platform:** Kibana custom panels (Liquid HTML + ES|QL) work on Elastic Cloud Serverless hubs (Observability Complete). Use them in the Fleet package dashboards. Stack support may still be limited on classic.

**Rule:** Use a custom panel only when Lens / Markdown cannot carry the story. Docs call out Sankey, scorecards, and status boards as the intended custom-panel jobs.

## Pages (two-word titles)

| Dashboard | Audience question | Hero |
| --- | --- | --- |
| Spend Overview | Where did org ECU go this period? | Sankey: driver → project type → SKU family |
| Project Detail | What does this project cost, and why? | KPI strip + contribution table (Lens) |
| Data Trust | Are collectors healthy and complete? | Status board (custom) |

Navigation bar identical on every page (Links panel). Default time range: last 7 days (billing rows are daily-ish; usage state is hourly).

## Panel type map

### Custom panel (Liquid + one ES|QL query)

| Panel | Why not Lens | Query shape (words) |
| --- | --- | --- |
| **Allocation Sankey** | Flow across 3–4 stages; Lens has no Sankey | Rows of `from`, `to`, `ecu` edges. Stages: `allocation_driver` → `deployment_type` (or project type) → SKU family (prefix before `_`). Weight = `SUM(total_ecu)`. Drop `allocation_driver == "none"` into an “Unallocated” sink so the story stays honest. |
| **KPI scorecards** | Mixed units, badges, thresholds in one strip | One row: `total_ecu`, `allocatable_ecu`, `unallocated_ecu`, `project_count`. Template renders four cards; amber if unallocated share > 15%. |
| **Collector status board** | One tile per project with health colour | Join `chargeback-serverless-projects` with `chargeback-serverless-state` on `project_id`. Green = `updated_at` within 2h and `indices` non-empty; amber = state exists but empty acc (typical remote 401); red = no state doc. |
| **Coverage banner** | Branded one-liner with live % | `allocatable / total` as a single metric plus short prose. |

Sankey rendering constraint: no JavaScript in the sandbox. Build with **inline SVG** (paths sized from `row["ecu"].pct` / edge weights) or stacked CSS flex flows. Prefer SVG so links read as classic Sankey; keep template under 500 KB.

### Lens / ES|QL visualization

| Panel | Why Lens |
| --- | --- |
| ECU over time by `allocation_driver` | Time series |
| Top SKUs table | Sortable, filterable |
| Top datastreams by contribution (after day-close flush) | Table + drill |
| Unallocated SKU list (`allocation_driver == "none"`) | Evidence under the Sankey |

### Markdown / Links

- Page chrome, how-to blurb (≤10 words per description)
- Cross-dashboard nav

## Live data we have now (hub spike)

From `billing_cluster_cost_lookup` on the test hub:

- Drivers that work: `indexing`, `query`, `storage`, `none`
- Real SKUs: `security.siem-complete-{ingested,retained}_*`, `elasticsearch.{retained_,general-purpose-search-vcu_}*`, plus unallocated inference / workflow / support / data-out
- Fields: `@timestamp`, `sku`, `allocation_driver`, `deployment_id`, `deployment_name`, `deployment_type`, `total_ecu`, `is_allocatable`, `cost_category`, `cost_type`

From usage:

- `chargeback-serverless-projects`: 2 projects (hub ES + telco Security)
- `chargeback-serverless-state`: hub has `acc` / `indices`; telco empty until a per-project ES key exists
- Contribution lookups: not flushed yet (day-close deferred) → Project Detail datastream panels wait on that

## Sankey edge contract (generator input)

ES|QL must emit **edges**, not a pivoted matrix. Example shape:

```esql
FROM billing_cluster_cost_lookup
| WHERE @timestamp >= ?_tstart AND @timestamp < ?_tend
| EVAL stage1 = allocation_driver,
       stage2 = COALESCE(deployment_type, "unknown"),
       stage3 = REPLACE(sku, "_[a-z0-9-]+$", "")
| STATS ecu = SUM(total_ecu) BY stage1, stage2
| EVAL `from` = stage1, `to` = stage2
| KEEP `from`, `to`, ecu
```

Run a second (or unioned) query for `stage2 → stage3` the same way, or one query with a `hop` column (`1` / `2`) so the Liquid template draws two layers.

## Shipped in package 0.1.1

Fleet package dashboards (Lens ES|QL — no custom Liquid panels yet):

1. **Spend Overview** — total / allocatable / unallocated ECU, donut by driver, SKU table, unallocated table
2. **Data Trust** — project count, state count, discovered projects, usage state

## Next build order

1. Sankey-style allocation flow (custom Liquid panel in package 0.3.0+)
2. Project Detail after contribution flush lands
3. Port ideas back into hosted FinOps dashboards only where Stack supports the same panel types

## Out of scope for v0

- Vega Sankey (custom panel is the Serverless-native path)
- Hand-clicked Kibana objects — everything ships from repo specs + import, same rule as hosted FinOps
- Fake demo data unless the hub period is empty
