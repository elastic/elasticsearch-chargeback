# Changelog

All notable changes to the Chargeback Integration and Module will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

---

## Integration Releases

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
