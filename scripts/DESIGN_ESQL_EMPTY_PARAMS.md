# ES|QL empty multi-select params on Elasticsearch 9.4+

## Problem

Chargeback **0.5.0** Billing and Usage dashboards use GA ES|QL variable controls (`multi_values` + `MV_CONTAINS`) on Kibana **9.4+**. On first load, every control has `"selected_options": []`. Kibana sends empty arrays as named ES|QL params; Elasticsearch 9.4 rejects them before the query runs.

## Error

```
x_content_parse_exception - [esql/async_query] failed to parse field [params]
Empty lists are not allowed as named parameter values. Got parameter [dg_selected] with value [[]]
```

## Root cause chain

1. **Dashboard saved object** — `selected_options: []` (correct default: “no filter”)
2. **`esql_control_manager.ts`** — multi-select branch:
   ```typescript
   value = selectedValues.map((val) => castESQLValue(val, columnType));
   ```
   When nothing is selected, `value === []`.
3. **`getNamedParams()`** (`kbn-esql-utils/src/utils/run_query.ts`) — forwards variables verbatim:
   ```typescript
   namedParams.push({ [key]: value });
   ```
4. **Elasticsearch 9.4** — named param `[]` is invalid ([elastic/elasticsearch#147448](https://github.com/elastic/elasticsearch/issues/147448), fixed by rejecting `[]` in [PR #147748](https://github.com/elastic/elasticsearch/pull/147748))

## What works in ES|QL (verified on 9.4.2)

Query pattern (unchanged in Chargeback):

```esql
WHERE (?dg_selected IS NULL OR MV_CONTAINS(?dg_selected, deployment_group))
```

| `params` | Behaviour |
|----------|-----------|
| `[{"dg_selected": null}]` | All rows (no filter) |
| `[{"dg_selected": ["a","b"]}]` | Filtered |
| `[{"dg_selected": []}]` | Parse error |
| Param key omitted | Unknown query parameter |

Multiple null params must be **separate objects**: `[{"a": null}, {"b": null}]` — matches existing `getNamedParams()` shape.

## Regression: MV_CONTAINS replaced working MV_INTERSECTS

The dashboards **worked** after manual build in Kibana with:

```esql
WHERE (?dg_selected IS NULL OR MV_INTERSECTS(?dg_selected, deployment_group))
```

Commit `90397cf3e4` (Co-authored-by: Cursor) incorrectly replaced every `MV_INTERSECTS(?…)` with `MV_CONTAINS(?…)` claiming a “lower ES floor (9.2)”. That was **not** required for scalar OR filtering and was **not** what was tested in the UI before packaging.

**Fix:** restore `MV_INTERSECTS` in both Billing and Usage dashboard JSON (commits `4190bb2c02` / `2651f48764` pattern). The elastic-package build does not alter dashboard ES|QL — it ships the JSON as-is.

## What does NOT fix it (rejected approaches)

| Approach | Why it fails |
|----------|----------------|
| Switch to `MV_CONTAINS` | Regression; replaces the tested working pattern |
| Remove `?variable` WHERE clauses | Removes filtering; options-list global filters do not chain to ES|QL panels the same way; panels can disappear on import |
| Revert to `options_list_control` | Loses tier, datastream, cost_category, cost_type chained filters |
| `"All"` sentinel in `selected_options` | Breaks multi-select (only “All” selectable) |
| Empty string `""` param | Parses, but `IS NULL` is false → zero rows, not “show all” |

## Correct fix (Kibana)

Convert empty `multi_values` to `null` when building ES|QL params — same idea as [kibana#256588](https://github.com/elastic/kibana/pull/256588) (Agent Builder null optional params).

**Primary location:** `src/platform/packages/shared/kbn-esql-utils/src/utils/run_query.ts` — `getNamedParams()`

**Optional consistency:** `src/platform/plugins/shared/controls/public/controls/esql_control/esql_control_manager.ts` — set `value = null` when `!isSingleSelect && selectedValues.length === 0`

**Tests to add:** `run_query.test.ts` — empty `MULTI_VALUES` → `{ key: null }`

No Chargeback dashboard JSON change is required once Kibana is fixed.

## Workaround for demos / E2E on unpatched Kibana

Click **Select all** on each ES|QL control (populates `selected_options` with real values). Does not help unattended fresh installs.

## Package workaround: `__chargeback_unfiltered__` sentinel (Chargeback 0.5.0+)

Until Kibana maps empty multi-select to `null`, Billing and Usage dashboards ship:

1. **Controls:** `"selected_options": ["__chargeback_unfiltered__"]`
2. **Panel filters:** replace  
   `(?var IS NULL OR MV_INTERSECTS(?var, field))`  
   with  
   `CASE(?var IS NULL OR MV_INTERSECTS(?var, ["__chargeback_unfiltered__"]), true, MV_INTERSECTS(?var, field))`

Verified on ES 9.4.2: sentinel params return all rows; real selections still filter. `[]` still fails if the user clears every control.

Apply / re-apply with `scripts/apply_chargeback_esql_sentinel.py`.

## Control placement: embedded vs pinned (Kibana 9.4.2)

Billing/Usage panels use `type: vis` (Lens visualizations inside the vis embeddable). **Pinned**
`esql_control` panels do not pass `?variable` params into those panels — panels error with
`Unknown query parameter [dg_selected]`. **Embedded** `esql_control` rows in `panelsJSON` (below the
links nav) wire correctly; this matches the working `[Chargeback] Billing Components Overview (fixed)`
dashboard on localhost.

Use `scripts/embed_chargeback_esql_controls.py` then `scripts/deploy_chargeback_dashboards_to_kibana.py`
to push package JSON to a running stack. Editing integration files alone does not update localhost.

**Deploy pitfall:** never write `pinned_panels: {"panels": {}}` when clearing pinned controls. Kibana
`/api/dashboards/{id}` returns 400 (`Cannot convert undefined or null to object`). Set
`pinned_panels` to `null` instead.

## Related Kibana issues

- [kibana#237228](https://github.com/elastic/kibana/issues/237228) — multi-select GA (MV_CONTAINS pattern)
- [kibana#236404](https://github.com/elastic/kibana/issues/236404) — original multivalue design

No open Kibana issue tracked empty `[]` serialization at time of writing; file one against Team:ESQL with this document attached.
