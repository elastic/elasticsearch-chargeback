# Changelog

All notable changes to the Chargeback Integration and Module will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

---

## Integration Releases

### [0.4.1] - 2026-08-03

#### Added

- Parse data stream names into `ds_type` / `ds_namespace` on the usage path; Usage dashboard blended-cost breakdown panels by namespace and by type (no control-bar filters) ([#23](https://github.com/elastic/elasticsearch-chargeback/issues/23)).
- ECS `event.ingested` on all Chargeback transform destination pipelines ([#97](https://github.com/elastic/elasticsearch-chargeback/issues/97)).
- Docs for `chargeback_conf_lookup` install-order prerequisite ([#96](https://github.com/elastic/elasticsearch-chargeback/issues/96)) and ESS Billing day-bucket reconciliation notes ([#66](https://github.com/elastic/elasticsearch-chargeback/issues/66)).

#### Changed

- Package **0.4.1** keeps Kibana `^9.2.0`. Asset: [`integration/assets/0.4.1/chargeback-0.4.1.zip`](integration/assets/0.4.1/chargeback-0.4.1.zip). Source: [elastic/integrations#20479](https://github.com/elastic/integrations/pull/20479).
- **Bugfix:** `billing_cluster_cost` and `billing_realized_pool` sync on `@timestamp` instead of `event.ingested` so On-Prem Billing sources (often without a mapped `event.ingested`) still populate `billing_realized_pool_lookup` and the Usage & Cost Allocation dashboard.

### [0.4.0] - 2026-06-01

#### Added

- **Realized cost model** — allocatable data-tier capacity pool (ECU/ERU) discounted by a p95 utilization score, giving a `chargeable_pool` that reflects actual resource consumption rather than raw provisioned capacity ([#8](https://github.com/elastic/elasticsearch-chargeback/issues/8)).
  - New transform: `billing_realized_pool` — aggregates daily allocatable data-tier ECU per deployment from `is_allocatable` SKUs.
  - New transform: `cluster_capacity_utilization` — computes p95 heap and disk utilization across data-role nodes per deployment/day from `node_stats`.
  - Utilization formula: `util_score = GREATEST((mem_w × heap_p95 + disk_w × disk_p95) / (mem_w + disk_w), floor)`, `chargeable_pool = provisioned_ecu × util_score`.
  - Defaults: memory weight 70, disk weight 30, floor 0.10. All configurable in `chargeback_conf_lookup`.
- **SKU cost classification** — `cost_type`, `cost_category`, and `is_allocatable` fields added to `billing_cluster_cost_lookup` via the `billing.yml` ingest pipeline. Covers data tiers (datahot/datacontent, datawarm, datacold, datafrozen), data transfer, snapshots, inference, and on-premises SKUs ([#8](https://github.com/elastic/elasticsearch-chargeback/issues/8)).
- **Three focused dashboards** replacing the previous single monolithic dashboard ([#8](https://github.com/elastic/elasticsearch-chargeback/issues/8)):
  - **`[Chargeback] Billing Components Overview`** — full invoice by deployment group and billing component (SKU-based).
  - **`[Chargeback] Usage & Cost Allocation`** — realized pool vs provisioned capacity, chargeable pool by tier, top-20 data streams by cost, time-series cost breakdown by data stream and tier (indexing / querying / storage / blended).
  - **`[Chargeback] Configuration`** — rate, weights, and date-window reference with visualised weight bar charts.
  - All dashboards carry a horizontal navigation bar with the exact dashboard titles as labels, preserving the active time range and filters.
- **New configuration weights** in `chargeback_conf_lookup`:
  - `conf_utilization_memory_weight` (default 70) and `conf_utilization_storage_weight` (default 30).
  - `conf_utilization_floor` (default 0.10) — minimum utilization to prevent realized cost reaching zero for idle or unmonitored clusters.
  - `conf_memory_cost_weight` / `conf_storage_cost_weight` (default 50/50) — illustrative memory vs storage split shown in data tiers panels.

#### Changed

- `billing_cluster_cost` transform: `sku` field now stored and mapped in the lookup index.
- All transform `fleet_transform_version` and ingest pipeline references bumped to `0.4.0`.
- Dashboard ES|QL panels updated to use `chargeable_pool` for tier and data-stream allocation.
- Integration source: [elastic/integrations#19309](https://github.com/elastic/integrations/pull/19309).

#### Upgrade notes

When upgrading from 0.3.x:
1. The two new transforms (`billing_realized_pool`, `cluster_capacity_utilization`) are created automatically. They require `node_stats` data in `metrics-elasticsearch.stack_monitoring.node_stats-*`; if absent, utilization defaults to 100%.
2. Reset and restart the `billing_cluster_cost` transform to backfill `cost_type`/`cost_category`/`is_allocatable` fields.
3. The old `[Chargeback] Cost and Consumption breakdown` dashboard is removed and replaced by three new dashboards.

---

### [0.3.2] - 2026-05-26

#### Added

- Troubleshooting guide for empty or incomplete Chargeback dashboards (`integration/docs/troubleshooting.md`), linked from the repository and integration READMEs ([#93](https://github.com/elastic/elasticsearch-chargeback/issues/93)).
- Support section in repository README documenting Field ownership and non-GA status ([#93](https://github.com/elastic/elasticsearch-chargeback/issues/93)).

#### Fixed

- Adds legacy ECU field aliases on lookup mappings so bundled dashboard ES|QL `COALESCE` queries validate without duplicating values ([#99](https://github.com/elastic/elasticsearch-chargeback/issues/99)); package source: [elastic/integrations#19196](https://github.com/elastic/integrations/pull/19196).

#### Changed

- Contribution transforms read usage data from `monitoring-indices*` and cross-cluster `*:monitoring-indices*` (from integrations [#18269](https://github.com/elastic/integrations/pull/18269)).

### [0.3.0] - TBD
#### Breaking changes
- **ECU → chargeable units:** Renamed fields for consistency: `total_ecu` → `total_chargeable_units`, `conf_ecu_rate` → `conf_chargeable_unit_rate`, `conf_ecu_rate_unit` → `conf_chargeable_unit_rate_unit`. Dashboard ES|QL queries and config lookup index use the new field names. Existing lookup indices from 0.2.x retain the old schema; new data from updated transforms uses the new schema. See upgrade documentation for migrating existing data.

#### Changed
- Bumped transform pipeline versions to 0.3.0
- Documentation and UI text updated from "ECU" to "chargeable units"

### [0.2.10] - 2026-01-27
#### Fixed
- Visualizations not loading correctly due to integer division returning zero in ES|QL queries. All calculations now use `TO_DOUBLE` type conversion.

#### Added
- Automated `chargeback_conf_lookup` index creation via a bootstrap transform during installation
  - Default ECU rate: 0.85 EUR
  - Default weights: indexing=20, query=20, storage=40
  - Default date range: 2010-01-01 to 2046-12-31

#### Changed
- Requires ESS Billing integration v1.7.0+
- Bumped all transform pipeline versions to 0.2.10

### [0.2.9] - 2025-12-05
#### Added
- CSS support for dashboard styling

### [0.2.8] - 2025-12-03
#### Added
- Three pre-configured Kibana alerting rule templates:
  - Transform Health Monitoring
  - New Chargeback Group Detection
  - Missing Usage Data alerts
- All transforms now auto-start upon installation
- Performance warning documentation for initial transform execution

### [0.2.7] - 2025-12-03
#### Fixed
- Removed broken 0.2.4 assets
- Fixed README version references

### [0.2.6] - 2025-12-03
#### Added
- Extract deployment group from Billing tags
- Merged main branch changes and resolved version conflicts

### [0.2.3] - 2025-11-28
#### Added
- Dashboard control and Dataview improvements

### [0.2.2] - 2025-11-25
#### Added
- Conversion rate configuration based on time windows
- Stack version compatibility table for smart lookup join requirements

### [0.2.1] - 2025-11-06
#### Fixed
- Visualization not displaying values due to integer division in ES|QL (changed to use double values)

### [0.2.0] - 2025-09-23
#### Changed
- Make use of new elastic-package version for automatic lookup index creation

### [0.1.7] - 2025-08-08
#### Changed
- Swap deployment_id/name to concatenation of both for easier identification in dashboards

### [0.1.6] - 2025-08-06
#### Changed
- Remove usage alias dependency, use transform output directly
- Updated to support non-default namespaces
- Performance improvements when relying on Stack Monitoring data

### [0.1.5] - 2025-08-04
#### Fixed
- Dashboard data view bug

### [0.1.4] - 2025-07-17
#### Changed
- Consistent naming of datastream
- Added LIMIT 5000 to ES|QL top query for large organisations

### [0.1.3] - 2025-07-16
#### Fixed
- Fixed datastream.keyword issue
- Fixed colour palette
- Added rate to unit table
- Changed instructions to favour ES integration

### [0.1.2] - 2025-07-15
#### Fixed
- Bug: transforms not starting on integration installation
- Bug: aligning ES|QL returned field names with field names used in Lens

### [0.0.4] - 2025-07-03
#### Added
- ECU rate unit to the configuration lookup index

#### Fixed
- Sorting on 'Blended value: % ECU per data stream per day'

### [0.0.3] - 2025-07-02
#### Fixed
- Transforms conditions too restrictive

### [0.0.2] - 2025-06-30
#### Fixed
- Updated zip with correct alias for stack monitoring as source
- Transforms on stack monitoring now work with @timestamp instead of event.ingested

### [0.0.1] - 2025-06-xx
#### Added
- Initial integration release
- Basic chargeback calculation per deployment, data stream, and data tier

---

## Module Releases

### [module-0.2.0] - 2025-02-28
#### Added
- Data tiering support

### [module-0.1.0] - 2025-02-22
#### Added
- Initial module release
- Chargeback information per day, per deployment, and per data stream

---

## Version Notes

- **0.2.4 and 0.2.5**: These versions were skipped/removed due to issues
- **Integration vs Module**: The Integration is the recommended approach as of 2025. The Module is deprecated and will not receive updates.
