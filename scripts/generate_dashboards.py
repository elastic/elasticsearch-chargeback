#!/usr/bin/env python3
"""Generate Kibana assets for chargeback_serverless and export-ready NDJSON.

- Working Links (all dashboardLink with panelId:link_<uuid>_dashboard)
- Project wording (Serverless has projects, not deployments)
- Local currency via Fleet policy settings (unit_rate, currency_unit, currency_symbol) on every visual
- Native metrics data streams: metrics-chargeback_serverless.billing-* and metrics-chargeback_serverless.usage-*
- Configuration dashboard displays Fleet Integration Policy guide, discovered projects registry, and hourly harvest health
- Matches `elastic-package export dashboards` structure (typeMigrationVersion 10.3.0, expanded JSON attributes, no tag files)
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KIBANA = REPO_ROOT / "integrations" / "chargeback-serverless" / "assets" / "kibana"
KIBANA = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_KIBANA

DASH_BILLING = "chargeback_serverless-b6cecac0-ebe7-43de-bfc6-1ff287ad860a"
DASH_USAGE = "chargeback_serverless-21588d0e-fb6a-4f76-ad2f-cd7b3d7a3d7c"
DASH_AI = "chargeback_serverless-5fb3f4c6-5591-5c5c-9625-1d829944c5b2"
DASH_SYNTHETICS = "chargeback_serverless-c188156b-9f97-5e72-975f-022f3c862a1d"
DASH_CONFIG = "chargeback_serverless-7ec9743a-ccfa-43d2-8311-2f25893dadae"

TITLE_BILLING = "[Chargeback Serverless] Billing Components Overview"
TITLE_USAGE = "[Chargeback Serverless] Usage & Cost Allocation"
TITLE_AI = "[Chargeback Serverless] AI & Machine Learning"
TITLE_SYNTHETICS = "[Chargeback Serverless] Synthetics"
TITLE_CONFIG = "[Chargeback Serverless] Configuration"

BILLING = "serverless_billing_cost_lookup"
REALIZED_POOL = "serverless_billing_realized_pool_lookup"
USAGE = "serverless_datastream_contribution_lookup"
PROJECT_CONTRIB = "serverless_project_contribution_lookup"
AI_USAGE = "serverless_ai_model_contribution_lookup"
SYNTHETICS_USAGE = "serverless_synthetics_monitor_contribution_lookup"
PROJECT_STATUS = "serverless_project_status_lookup"

ALERT_TOTAL_DAILY_SPEND = "chargeback_serverless-alert-total-daily-spend"
ALERT_TOTAL_MONTHLY_SPEND = "chargeback_serverless-alert-total-monthly-spend"
ALERT_CATEGORY_SPEND = "chargeback_serverless-alert-category-spend"
ALERT_WORKLOAD_DRIVER_SPEND = "chargeback_serverless-alert-workload-driver-spend"
ALERT_PROJECT_SPEND = "chargeback_serverless-alert-project-spend"
ALERT_PROJECT_SPEND_SPIKE = "chargeback_serverless-alert-project-spend-spike"
ALERT_NAMESPACE_SPEND = "chargeback_serverless-alert-namespace-spend"
ALERT_AI_MODEL_SPEND = "chargeback_serverless-alert-ai-model-spend"
ALERT_SYNTHETICS_MONITOR_SPEND = "chargeback_serverless-alert-synthetics-monitor-spend"
ALERT_DATA_TRUST_HEALTH = "chargeback_serverless-alert-data-trust-health"

INDEX_PATTERN_ID = "chargeback_serverless-billing"

# Currency settings resolved directly from the agent policy configuration.
CURRENCY_RESOLVE_ESQL = """\
| EVAL unit = TO_UPPER(COALESCE(conf_chargeable_unit_rate_unit, "USD"))
| EVAL currency_symbol = CASE(conf_currency_symbol IS NOT NULL AND conf_currency_symbol != "", conf_currency_symbol, "$")"""

# Shared ES|QL time filter: respects Kibana dashboard time picker (?_tstart / ?_tend).
TIME_FILTER_ESQL = "| WHERE @timestamp >= ?_tstart AND @timestamp <= ?_tend"

# Shared ES|QL prelude body: currency rate and symbol from policy config stamped on events,
# resolving missing/ID project_name via LOOKUP JOIN against serverless_project_status_lookup.
COST_PRELUDE_BODY = f"""| EVAL ts = @timestamp
| RENAME project_id AS _b_pid, project_name AS _b_pname, project_type AS _b_ptype, conf_chargeable_unit_rate AS _b_rate, conf_chargeable_unit_rate_unit AS _b_unit, conf_currency_symbol AS _b_sym
| LOOKUP JOIN {PROJECT_STATUS} ON composite_key
| EVAL @timestamp = ts
| EVAL project_id = COALESCE(_b_pid, project_id)
| EVAL project_name = CASE(_b_pname IS NOT NULL AND _b_pname != "" AND _b_pname != project_id, _b_pname, project_name IS NOT NULL AND project_name != "" AND project_name != project_id, project_name, _b_pname IS NOT NULL AND _b_pname != "", _b_pname, project_name IS NOT NULL AND project_name != "", project_name, project_id)
| EVAL project_type = CASE(_b_ptype IS NOT NULL AND _b_ptype != "" AND _b_ptype != "unknown", _b_ptype, project_type IS NOT NULL AND project_type != "", project_type, _b_ptype)
| EVAL conf_chargeable_unit_rate = CASE(_b_rate IS NOT NULL, _b_rate, conf_chargeable_unit_rate IS NOT NULL, conf_chargeable_unit_rate, 1.0), conf_chargeable_unit_rate_unit = COALESCE(_b_unit, conf_chargeable_unit_rate_unit, "USD"), conf_currency_symbol = COALESCE(_b_sym, conf_currency_symbol, "$")
{CURRENCY_RESOLVE_ESQL}
| EVAL units = CASE(total_ecu IS NOT NULL, total_ecu, 0.0)
| EVAL cost = units * conf_chargeable_unit_rate
| EVAL project = CASE(project_name IS NOT NULL AND project_name != "", project_name, project_id IS NOT NULL AND project_id != "", project_id, "organization")
| EVAL project_type_label = CASE(project_type IS NOT NULL AND project_type != "", project_type, "organization")
| EVAL category_label = COALESCE(cost_category, "other")
| EVAL cost_type_label = COALESCE(cost_type, "other")
| WHERE ts IS NOT NULL"""

COST_PRELUDE = f"""FROM {BILLING}
{TIME_FILTER_ESQL}
{COST_PRELUDE_BODY}"""

COST_PRELUDE_NO_TIME = f"""FROM {BILLING}
{COST_PRELUDE_BODY}"""

# Shared ES|QL join body for USAGE (serverless_datastream_contribution_lookup):
# Preserves @timestamp, avoids all column collisions across chained LOOKUP JOINs,
# and resolves human-readable project_name and currency settings.
USAGE_JOIN_BODY = f"""| EVAL ts = @timestamp
| RENAME project_id AS _u_pid, project_name AS _u_pname, project_type AS _u_ptype, conf_chargeable_unit_rate AS _u_rate, conf_chargeable_unit_rate_unit AS _u_unit, conf_currency_symbol AS _u_sym
| LOOKUP JOIN {PROJECT_STATUS} ON composite_key
| RENAME project_id AS _ps_pid, project_name AS _ps_pname, project_type AS _ps_ptype, conf_chargeable_unit_rate AS _ps_rate, conf_chargeable_unit_rate_unit AS _ps_unit, conf_currency_symbol AS _ps_sym
| LOOKUP JOIN {REALIZED_POOL} ON composite_key
| RENAME project_id AS _rp_pid
| LOOKUP JOIN {PROJECT_CONTRIB} ON composite_key
| EVAL @timestamp = ts
| EVAL project_id = COALESCE(_u_pid, _ps_pid, _rp_pid, project_id)
| EVAL project_name = CASE(_u_pname IS NOT NULL AND _u_pname != "" AND _u_pname != project_id, _u_pname, _ps_pname IS NOT NULL AND _ps_pname != "" AND _ps_pname != project_id, _ps_pname, _u_pname IS NOT NULL AND _u_pname != "", _u_pname, _ps_pname IS NOT NULL AND _ps_pname != "", _ps_pname, project_id)
| EVAL project_type = CASE(_u_ptype IS NOT NULL AND _u_ptype != "" AND _u_ptype != "unknown", _u_ptype, _ps_ptype IS NOT NULL AND _ps_ptype != "", _ps_ptype, "unknown")
| EVAL project = CASE(project_name IS NOT NULL AND project_name != "", project_name, project_id IS NOT NULL AND project_id != "", project_id, "unknown")
| EVAL conf_chargeable_unit_rate = CASE(conf_chargeable_unit_rate IS NOT NULL, conf_chargeable_unit_rate, _ps_rate IS NOT NULL, _ps_rate, _u_rate IS NOT NULL, _u_rate, 1.0), conf_chargeable_unit_rate_unit = COALESCE(conf_chargeable_unit_rate_unit, _ps_unit, _u_unit, "USD"), conf_currency_symbol = COALESCE(conf_currency_symbol, _ps_sym, _u_sym, "$")
| EVAL rate = conf_chargeable_unit_rate
{CURRENCY_RESOLVE_ESQL}"""

# Shared ES|QL join body for AI_USAGE (serverless_ai_model_contribution_lookup):
AI_JOIN_BODY = f"""| EVAL ts = @timestamp
| RENAME project_id AS _u_pid, project_name AS _u_pname, project_type AS _u_ptype, conf_chargeable_unit_rate AS _u_rate, conf_chargeable_unit_rate_unit AS _u_unit, conf_currency_symbol AS _u_sym
| LOOKUP JOIN {PROJECT_STATUS} ON composite_key
| RENAME project_id AS _ps_pid, project_name AS _ps_pname, project_type AS _ps_ptype, conf_chargeable_unit_rate AS _ps_rate, conf_chargeable_unit_rate_unit AS _ps_unit, conf_currency_symbol AS _ps_sym
| LOOKUP JOIN {REALIZED_POOL} ON composite_key
| EVAL @timestamp = ts
| EVAL project_id = COALESCE(_u_pid, _ps_pid, project_id)
| EVAL project_name = CASE(_u_pname IS NOT NULL AND _u_pname != "" AND _u_pname != project_id, _u_pname, _ps_pname IS NOT NULL AND _ps_pname != "" AND _ps_pname != project_id, _ps_pname, _u_pname IS NOT NULL AND _u_pname != "", _u_pname, _ps_pname IS NOT NULL AND _ps_pname != "", _ps_pname, project_id)
| EVAL project_type = CASE(_u_ptype IS NOT NULL AND _u_ptype != "" AND _u_ptype != "unknown", _u_ptype, _ps_ptype IS NOT NULL AND _ps_ptype != "", _ps_ptype, "unknown")
| EVAL project = CASE(project_name IS NOT NULL AND project_name != "", project_name, project_id IS NOT NULL AND project_id != "", project_id, "unknown")
| EVAL conf_chargeable_unit_rate = CASE(conf_chargeable_unit_rate IS NOT NULL, conf_chargeable_unit_rate, _ps_rate IS NOT NULL, _ps_rate, _u_rate IS NOT NULL, _u_rate, 1.0), conf_chargeable_unit_rate_unit = COALESCE(conf_chargeable_unit_rate_unit, _ps_unit, _u_unit, "USD"), conf_currency_symbol = COALESCE(conf_currency_symbol, _ps_sym, _u_sym, "$")
| EVAL rate = conf_chargeable_unit_rate
{CURRENCY_RESOLVE_ESQL}"""

# Shared ES|QL join body for SYNTHETICS_USAGE (serverless_synthetics_monitor_contribution_lookup):
SYNTHETICS_JOIN_BODY = AI_JOIN_BODY

# Shared ES|QL monitor cost attribution formula: attributes browser execution SKU pool across
# browser monitors and lightweight ping SKU pool across http/icmp/tcp/dns monitors by check volume.
SYNTHETICS_MONITOR_COST_EVAL = """| EVAL monitor_type = COALESCE(monitor_type, "http")
| EVAL is_browser = (monitor_type == "browser")
| EVAL pings_pool_cost = CASE(synthetics_pings_ecu IS NOT NULL, synthetics_pings_ecu, 0.0) * rate
| EVAL exec_pool_cost = CASE(synthetics_executions_ecu IS NOT NULL, synthetics_executions_ecu, 0.0) * rate
| EVAL tot_pool_cost = CASE(synthetics_ecu IS NOT NULL, synthetics_ecu, 0.0) * rate
| EVAL chk = CASE(check_count IS NOT NULL, TO_DOUBLE(check_count), 0.0)
| EVAL b_chk = CASE(is_browser, chk, 0.0)
| EVAL p_chk = CASE(NOT is_browser, chk, 0.0)
| INLINE STATS day_b_checks = SUM(b_chk), day_p_checks = SUM(p_chk), day_tot_checks = SUM(chk) BY composite_key
| EVAL day_monitor_cost = CASE(
    (pings_pool_cost > 0.0 OR exec_pool_cost > 0.0) AND is_browser AND day_b_checks > 0.0, (chk / day_b_checks) * exec_pool_cost,
    (pings_pool_cost > 0.0 OR exec_pool_cost > 0.0) AND NOT is_browser AND day_p_checks > 0.0, (chk / day_p_checks) * pings_pool_cost,
    tot_pool_cost > 0.0 AND day_tot_checks > 0.0, (chk / day_tot_checks) * tot_pool_cost,
    0.0
  )"""


def stable_uuid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"chargeback_serverless:{name}"))


def adhoc_id(title: str) -> str:
    return hashlib.sha256(title.encode()).hexdigest()


def links_panel(panel_id: str, current: str) -> tuple[dict, list]:
    """Navigation links bar: all five links use dashboardLink with dashboard references."""
    nav = [
        (DASH_BILLING, TITLE_BILLING),
        (DASH_USAGE, TITLE_USAGE),
        (DASH_AI, TITLE_AI),
        (DASH_SYNTHETICS, TITLE_SYNTHETICS),
        (DASH_CONFIG, TITLE_CONFIG),
    ]
    links = []
    refs = []
    for order, (dash_id, label) in enumerate(nav):
        link_id = stable_uuid(f"link:{panel_id}:{dash_id}")
        dest = f"link_{link_id}_dashboard"
        links.append(
            {
                "destinationRefName": dest,
                "id": link_id,
                "label": label,
                "options": {
                    "openInNewTab": False,
                    "useCurrentDateRange": True,
                    "useCurrentFilters": True,
                },
                "order": order,
                "type": "dashboardLink",
            }
        )
        refs.append(
            {
                "id": dash_id,
                "name": f"{panel_id}:{dest}",
                "type": "dashboard",
            }
        )
    panel = {
        "embeddableConfig": {
            "enhancements": {},
            "layout": "horizontal",
            "links": links,
        },
        "gridData": {"x": 0, "y": 0, "w": 48, "h": 2, "i": panel_id},
        "panelIndex": panel_id,
        "type": "links",
    }
    return panel, refs


def col(field: str, es_type: str, label: str | None = None, decimals: int | None = None) -> dict:
    typ = {
        "date": "date",
        "double": "number",
        "long": "number",
        "boolean": "boolean",
        "keyword": "string",
    }.get(es_type, "string")
    res = {
        "columnId": field,
        "customLabel": bool(label),
        "fieldName": field,
        "label": label or field,
        "meta": {"esType": es_type, "type": typ},
    }
    if decimals is not None:
        res["customFormat"] = {"id": "number", "params": {"decimals": decimals}}
    elif es_type == "double":
        res["customFormat"] = {"id": "number", "params": {"decimals": 2}}
    elif es_type == "long":
        res["customFormat"] = {"id": "number", "params": {"decimals": 0}}
    return res


def lens_esql(
    panel_id: str,
    title: str,
    esql: str,
    columns: list[dict],
    visualization_type: str,
    visualization: dict,
    x: int,
    y: int,
    w: int,
    h: int,
    index: str = BILLING,
    time_field: str | None = "@timestamp",
    *,
    section_id: str | None = None,
    description: str = "",
) -> tuple[dict, list]:
    adhoc = adhoc_id(f"esql|{title}|{index}")
    layer_id = stable_uuid(f"layer:{panel_id}")
    state = {
        "adHocDataViews": {
            adhoc: {
                "allowHidden": False,
                "allowNoIndex": False,
                "fieldFormats": {},
                "id": adhoc,
                "managed": False,
                "name": index,
                "runtimeFieldMap": {},
                "sourceFilters": [],
                "timeFieldName": time_field,
                "title": index,
                "type": "esql",
            }
        },
        "datasourceStates": {
            "textBased": {
                "indexPatternRefs": [
                    {"id": adhoc, "timeField": time_field, "title": index}
                ],
                "layers": {
                    layer_id: {
                        "columns": columns,
                        "index": adhoc,
                        "query": {"esql": esql},
                        "timeField": time_field,
                    }
                },
            }
        },
        "filters": [],
        "query": {"esql": esql},
        "visualization": visualization,
    }
    if "layers" in visualization and visualization["layers"]:
        visualization["layers"][0]["layerId"] = layer_id
    elif visualization_type in ("lnsDatatable", "lnsMetric"):
        visualization["layerId"] = layer_id

    grid: dict = {"x": x, "y": y, "w": w, "h": h, "i": panel_id}
    if section_id:
        grid["sectionId"] = section_id

    panel = {
        "description": description,
        "embeddableConfig": {
            "attributes": {
                "description": description,
                "references": [],
                "state": state,
                "title": title,
                "type": "lens",
                "version": 1,
                "visualizationType": visualization_type,
            },
            "description": description,
            "enhancements": {"dynamicActions": {"events": []}},
            "filters": [],
            "query": {"esql": esql},
            "syncColors": False,
            "syncCursor": True,
            "syncTooltips": False,
            "title": title,
        },
        "gridData": grid,
        "panelIndex": panel_id,
        "title": title,
        "type": "lens",
    }
    return panel, []


def xy_vis(metric_col: str, breakdown_col: str | None = None, series: str = "bar_stacked") -> dict:
    layer: dict = {
        "accessors": [metric_col],
        "layerType": "data",
        "position": "top",
        "seriesType": series,
        "showGridlines": False,
        "xAccessor": "@timestamp",
    }
    if breakdown_col:
        layer["splitAccessor"] = breakdown_col
    return {
        "fittingFunction": "Linear",
        "layers": [layer],
        "legend": {"isVisible": True, "position": "right"},
        "preferredSeriesType": series,
        "valueLabels": "hide",
    }


def table_vis(column_ids: list[str]) -> dict:
    return {
        "columns": [{"columnId": c} for c in column_ids],
        "layerType": "data",
    }


def custom_panel(
    panel_id: str,
    title: str,
    template: str,
    esql_query: str | None,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    section_id: str | None = None,
    description: str = "",
) -> tuple[dict, list]:
    grid: dict = {"x": x, "y": y, "w": w, "h": h, "i": panel_id}
    if section_id:
        grid["sectionId"] = section_id
    panel = {
        "description": description,
        "embeddableConfig": {
            "description": description,
            "esql_query": [esql_query] if esql_query else [],
            "template": template,
            "title": title,
        },
        "gridData": grid,
        "panelIndex": panel_id,
        "title": title,
        "type": "custom_content",
    }
    return panel, []



def liquid_money_block(val_var: str, out_var: str, with_decimals: bool = True) -> str:
    """Liquid snippet to format a number with thousands and millions comma separators (e.g. 1,234 or 1,234.56)."""
    p = f"_{out_var}"
    if not with_decimals:
        return f"""\
{{% assign {p}_iv = {val_var} | round %}}
{{% if {p}_iv >= 1000000000 %}}
  {{% assign {p}_b = {p}_iv | divided_by: 1000000000 | floor %}}
  {{% assign {p}_m = {p}_iv | modulo: 1000000000 | divided_by: 1000000 | floor %}}
  {{% assign {p}_k = {p}_iv | modulo: 1000000 | divided_by: 1000 | floor %}}
  {{% assign {p}_u = {p}_iv | modulo: 1000 | floor %}}
  {{% if {p}_m < 10 %}}{{% assign {p}_ms = "00" | append: {p}_m %}}{{% elsif {p}_m < 100 %}}{{% assign {p}_ms = "0" | append: {p}_m %}}{{% else %}}{{% assign {p}_ms = {p}_m %}}{{% endif %}}
  {{% if {p}_k < 10 %}}{{% assign {p}_ks = "00" | append: {p}_k %}}{{% elsif {p}_k < 100 %}}{{% assign {p}_ks = "0" | append: {p}_k %}}{{% else %}}{{% assign {p}_ks = {p}_k %}}{{% endif %}}
  {{% if {p}_u < 10 %}}{{% assign {p}_us = "00" | append: {p}_u %}}{{% elsif {p}_u < 100 %}}{{% assign {p}_us = "0" | append: {p}_u %}}{{% else %}}{{% assign {p}_us = {p}_u %}}{{% endif %}}
  {{% assign {out_var} = {p}_b | append: "," | append: {p}_ms | append: "," | append: {p}_ks | append: "," | append: {p}_us %}}
{{% elsif {p}_iv >= 1000000 %}}
  {{% assign {p}_m = {p}_iv | divided_by: 1000000 | floor %}}
  {{% assign {p}_k = {p}_iv | modulo: 1000000 | divided_by: 1000 | floor %}}
  {{% assign {p}_u = {p}_iv | modulo: 1000 | floor %}}
  {{% if {p}_k < 10 %}}{{% assign {p}_ks = "00" | append: {p}_k %}}{{% elsif {p}_k < 100 %}}{{% assign {p}_ks = "0" | append: {p}_k %}}{{% else %}}{{% assign {p}_ks = {p}_k %}}{{% endif %}}
  {{% if {p}_u < 10 %}}{{% assign {p}_us = "00" | append: {p}_u %}}{{% elsif {p}_u < 100 %}}{{% assign {p}_us = "0" | append: {p}_u %}}{{% else %}}{{% assign {p}_us = {p}_u %}}{{% endif %}}
  {{% assign {out_var} = {p}_m | append: "," | append: {p}_ks | append: "," | append: {p}_us %}}
{{% elsif {p}_iv >= 1000 %}}
  {{% assign {p}_k = {p}_iv | divided_by: 1000 | floor %}}
  {{% assign {p}_u = {p}_iv | modulo: 1000 | floor %}}
  {{% if {p}_u < 10 %}}{{% assign {p}_us = "00" | append: {p}_u %}}{{% elsif {p}_u < 100 %}}{{% assign {p}_us = "0" | append: {p}_u %}}{{% else %}}{{% assign {p}_us = {p}_u %}}{{% endif %}}
  {{% assign {out_var} = {p}_k | append: "," | append: {p}_us %}}
{{% else %}}
  {{% assign {out_var} = {p}_iv %}}
{{% endif %}}"""

    return f"""\
{{% assign {p}_cents = {val_var} | times: 100 | round %}}
{{% assign {p}_iv = {p}_cents | divided_by: 100 | floor %}}
{{% assign {p}_dv = {p}_cents | modulo: 100 | floor %}}
{{% if {p}_dv < 10 %}}{{% assign {p}_ds = "0" | append: {p}_dv %}}{{% else %}}{{% assign {p}_ds = {p}_dv | append: "" %}}{{% endif %}}
{{% if {p}_iv >= 1000000000 %}}
  {{% assign {p}_b = {p}_iv | divided_by: 1000000000 | floor %}}
  {{% assign {p}_m = {p}_iv | modulo: 1000000000 | divided_by: 1000000 | floor %}}
  {{% assign {p}_k = {p}_iv | modulo: 1000000 | divided_by: 1000 | floor %}}
  {{% assign {p}_u = {p}_iv | modulo: 1000 | floor %}}
  {{% if {p}_m < 10 %}}{{% assign {p}_ms = "00" | append: {p}_m %}}{{% elsif {p}_m < 100 %}}{{% assign {p}_ms = "0" | append: {p}_m %}}{{% else %}}{{% assign {p}_ms = {p}_m %}}{{% endif %}}
  {{% if {p}_k < 10 %}}{{% assign {p}_ks = "00" | append: {p}_k %}}{{% elsif {p}_k < 100 %}}{{% assign {p}_ks = "0" | append: {p}_k %}}{{% else %}}{{% assign {p}_ks = {p}_k %}}{{% endif %}}
  {{% if {p}_u < 10 %}}{{% assign {p}_us = "00" | append: {p}_u %}}{{% elsif {p}_u < 100 %}}{{% assign {p}_us = "0" | append: {p}_u %}}{{% else %}}{{% assign {p}_us = {p}_u %}}{{% endif %}}
  {{% assign {out_var} = {p}_b | append: "," | append: {p}_ms | append: "," | append: {p}_ks | append: "," | append: {p}_us | append: "." | append: {p}_ds %}}
{{% elsif {p}_iv >= 1000000 %}}
  {{% assign {p}_m = {p}_iv | divided_by: 1000000 | floor %}}
  {{% assign {p}_k = {p}_iv | modulo: 1000000 | divided_by: 1000 | floor %}}
  {{% assign {p}_u = {p}_iv | modulo: 1000 | floor %}}
  {{% if {p}_k < 10 %}}{{% assign {p}_ks = "00" | append: {p}_k %}}{{% elsif {p}_k < 100 %}}{{% assign {p}_ks = "0" | append: {p}_k %}}{{% else %}}{{% assign {p}_ks = {p}_k %}}{{% endif %}}
  {{% if {p}_u < 10 %}}{{% assign {p}_us = "00" | append: {p}_u %}}{{% elsif {p}_u < 100 %}}{{% assign {p}_us = "0" | append: {p}_u %}}{{% else %}}{{% assign {p}_us = {p}_u %}}{{% endif %}}
  {{% assign {out_var} = {p}_m | append: "," | append: {p}_ks | append: "," | append: {p}_us | append: "." | append: {p}_ds %}}
{{% elsif {p}_iv >= 1000 %}}
  {{% assign {p}_k = {p}_iv | divided_by: 1000 | floor %}}
  {{% assign {p}_u = {p}_iv | modulo: 1000 | floor %}}
  {{% if {p}_u < 10 %}}{{% assign {p}_us = "00" | append: {p}_u %}}{{% elsif {p}_u < 100 %}}{{% assign {p}_us = "0" | append: {p}_u %}}{{% else %}}{{% assign {p}_us = {p}_u %}}{{% endif %}}
  {{% assign {out_var} = {p}_k | append: "," | append: {p}_us | append: "." | append: {p}_ds %}}
{{% else %}}
  {{% assign {out_var} = {p}_iv | append: "." | append: {p}_ds %}}
{{% endif %}}"""


PAGED_BASE_CSS = """\
  .cb-paged-wrap {
    position: relative;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    height: 100%;
    min-height: 0;
    width: 100%;
    box-sizing: border-box;
    overflow: hidden;
  }
  .cb-content-wrap {
    flex: 1 1 auto;
    min-height: 0;
    overflow-x: auto;
    overflow-y: hidden;
  }
  .cb-pg-bar {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 4px;
    padding: 6px 2px 2px 2px;
    border-top: 1px solid var(--cc-color-border);
    font-size: 0.76rem;
    color: var(--cc-color-text);
    user-select: none;
    flex-shrink: 0;
  }
  .cb-pg-info {
    margin-right: auto;
    opacity: 0.75;
    font-size: 0.74rem;
  }
  .cb-pg-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 22px;
    height: 22px;
    padding: 0 5px;
    border-radius: var(--cc-radius-s, 4px);
    border: 1px solid var(--cc-color-border);
    background: var(--cc-color-surface);
    color: var(--cc-color-text);
    cursor: pointer;
    font-size: 0.75rem;
    font-weight: 500;
    text-decoration: none;
    box-sizing: border-box;
    transition: background var(--cc-motion-fast, 0.15s), border-color var(--cc-motion-fast, 0.15s);
    user-select: none;
  }
  .cb-pg-btn:hover {
    background: var(--cc-color-border);
    border-color: var(--cc-color-primary);
  }
  .cb-pg-dir {
    display: none !important;
    font-weight: 700;
  }
  .cb-prev-disabled, .cb-next-disabled {
    opacity: 0.35 !important;
    cursor: not-allowed !important;
    pointer-events: none !important;
  }
  .cb-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px 12px;
    font-size: 0.84rem;
    color: var(--cc-color-text);
    opacity: 0.65;
    text-align: center;
  }
"""


def paged_css(pfx: str, max_pages: int, display_mode: str = "table-row") -> str:
    rules = [
        f"  .cb-pg-radio-{pfx} {{ display: none !important; position: absolute; opacity: 0; pointer-events: none; }}",
        f"  .cb-pg-row-{pfx} {{ display: none !important; }}",
    ]
    for i in range(1, max_pages + 1):
        rules.append(
            f"  #{pfx}_pg_{i}:checked ~ .cb-content-wrap-{pfx} .cb-pg-{pfx}-{i} {{ display: {display_mode} !important; }}"
        )
        rules.append(
            f"  #{pfx}_pg_{i}:checked ~ .cb-pg-bar-{pfx} .cb-btn-{pfx}-{i} {{ background: var(--cc-color-primary); color: #ffffff !important; border-color: var(--cc-color-primary); pointer-events: none; font-weight: 700; }}"
        )
        rules.append(
            f"  #{pfx}_pg_{i}:checked ~ .cb-pg-bar-{pfx} .cb-pg-cur::after {{ content: \"{i}\"; }}"
        )
        rules.append(
            f"  #{pfx}_pg_{i}:checked ~ .cb-pg-bar-{pfx} .cb-prev-{pfx}-{i} {{ display: inline-flex !important; }}"
        )
        rules.append(
            f"  #{pfx}_pg_{i}:checked ~ .cb-pg-bar-{pfx} .cb-next-{pfx}-{i} {{ display: inline-flex !important; }}"
        )
    return "\n".join(rules)


def paged_top(pfx: str, page_size: int, max_pages: int) -> str:
    return f"""\
{{% assign _total_items = rows.size | default: 0 | round %}}
{{% assign _total_pages = _total_items | minus: 1 | divided_by: {page_size} | floor | plus: 1 %}}
{{% if _total_pages < 1 %}}{{% assign _total_pages = 1 %}}{{% endif %}}
{{% if _total_pages > {max_pages} %}}{{% assign _total_pages = {max_pages} %}}{{% endif %}}
<div class="cb-paged-wrap cb-paged-wrap-{pfx}">
{{% for _p_i in (1.._total_pages) %}}
  <input type="radio" name="{pfx}_pg" id="{pfx}_pg_{{{{ _p_i }}}}" class="cb-pg-radio-{pfx}" {{% if _p_i == 1 %}}checked{{% endif %}}>
{{% endfor %}}
<div class="cb-content-wrap cb-content-wrap-{pfx}">\
"""


def paged_bottom(pfx: str) -> str:
    return f"""\
</div>
{{% if _total_pages > 1 %}}
<div class="cb-pg-bar cb-pg-bar-{pfx}">
  <span class="cb-pg-info">Page <span class="cb-pg-cur"></span> of {{{{ _total_pages }}}} ({{{{ _total_items }}}} items)</span>
  <span class="cb-pg-btn cb-pg-dir cb-prev-disabled cb-prev-{pfx}-1" title="Previous page">&lsaquo;</span>
  {{% for _p_i in (2.._total_pages) %}}
    {{% assign _p_prev = _p_i | minus: 1 %}}
    <label for="{pfx}_pg_{{{{ _p_prev }}}}" class="cb-pg-btn cb-pg-dir cb-prev-{pfx}-{{{{ _p_i }}}}" title="Previous page">&lsaquo;</label>
  {{% endfor %}}
  {{% for _p_i in (1.._total_pages) %}}
    <label for="{pfx}_pg_{{{{ _p_i }}}}" class="cb-pg-btn cb-btn-{pfx}-{{{{ _p_i }}}}">{{{{ _p_i }}}}</label>
  {{% endfor %}}
  {{% for _p_i in (1.._total_pages) %}}
    {{% if _p_i < _total_pages %}}
      {{% assign _p_next = _p_i | plus: 1 %}}
      <label for="{pfx}_pg_{{{{ _p_next }}}}" class="cb-pg-btn cb-pg-dir cb-next-{pfx}-{{{{ _p_i }}}}" title="Next page">&rsaquo;</label>
    {{% else %}}
      <span class="cb-pg-btn cb-pg-dir cb-next-disabled cb-next-{pfx}-{{{{ _p_i }}}}" title="Next page">&rsaquo;</span>
    {{% endif %}}
  {{% endfor %}}
</div>
{{% endif %}}
</div>\
"""


BILLING_KPI_TEMPLATE = f"""\
<style>
  .cb-kpi-wrap {{
    position: relative;
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    width: 100%;
    box-sizing: border-box;
    overflow: hidden;
  }}
  .cb-kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(135px, 1fr)); gap: var(--cc-space-s, 8px); margin: 0; padding: 0 0 2px 0; }}
  .cb-kpi {{
    background: var(--cc-color-surface);
    border: 1px solid var(--cc-color-border);
    border-radius: var(--cc-radius);
    padding: 8px 12px 9px 12px;
  }}
  .cb-kpi-label {{ color: var(--cc-color-text); opacity: 0.72; font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.03em; line-height: 1.1; }}
  .cb-kpi-value {{ color: var(--cc-color-text); font-size: 1.35rem; font-weight: 600; margin-top: 3px; line-height: 1.1; font-variant-numeric: tabular-nums; display: flex; align-items: baseline; }}
  .cb-sym {{ font-size: 1.0rem; opacity: 0.85; margin-right: 4px; font-weight: 500; }}
  .cb-kpi-sub {{ color: var(--cc-color-text); font-size: 0.73rem; opacity: 0.68; margin-top: 3px; line-height: 1.1; }}
</style>
{{% assign r = rows[0] %}}
{{% assign total = r["total_spend"].value | default: 0.0 %}}
{{% assign alloc = r["allocatable_spend"].value | default: 0.0 %}}
{{% assign unalloc = r["unallocatable_spend"].value | default: 0.0 %}}
{{% assign unit = r["unit"].value | default: "USD" %}}
{{% assign sym = r["currency_symbol"].value | default: "$" %}}
{{% assign denom = total %}}
{{% if denom <= 0 %}}{{% assign denom = 1.0 %}}{{% endif %}}
{{% assign alloc_pct = alloc | times: 100.0 | divided_by: denom | round: 1 %}}
{{% assign unalloc_pct = unalloc | times: 100.0 | divided_by: denom | round: 1 %}}
{{% assign l_day = r["latest_day"].value | default: "" | slice: 0, 10 %}}
{{% if l_day == "" or l_day == "null" %}}{{% assign l_day = "—" %}}{{% endif %}}
{{% assign tot_proj = r["project_count"].value | default: 0 | round %}}
{{% assign act_proj = r["active_project_count"].value | default: tot_proj | round %}}
{{% assign inact_proj = tot_proj | minus: act_proj %}}
{{% if inact_proj < 0 %}}{{% assign inact_proj = 0 %}}{{% endif %}}
{liquid_money_block("total", "fmt_total", with_decimals=True)}
{liquid_money_block("alloc", "fmt_alloc", with_decimals=True)}
{liquid_money_block("unalloc", "fmt_unalloc", with_decimals=True)}
<div class="cb-kpi-wrap">
  <div class="cb-kpis">
    <div class="cb-kpi" title="Total Spend: Sum of all Serverless invoice line items (total_ecu × conf_chargeable_unit_rate) in serverless_billing_cost_lookup.">
      <div class="cb-kpi-label">Total Spend ({{{{ unit }}}})</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_total }}}}</span></div>
      <div class="cb-kpi-sub">All Serverless line items</div>
    </div>
    <div class="cb-kpi" title="Allocatable Pool: Workload-driven SKUs (is_allocatable: true: ingest, search, retained storage, AI/ML, Synthetics) attributed to workloads.">
      <div class="cb-kpi-label">Allocatable Pool ({{{{ unit }}}})</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_alloc }}}}</span></div>
      <div class="cb-kpi-sub">{{{{ alloc_pct }}}}% of total spend</div>
    </div>
    <div class="cb-kpi" title="Unallocatable Spend: Org-wide &amp; pass-through charges (is_allocatable: false: Support, Data Transfer, Snapshots, Platform SKUs) not tied to a single workload.">
      <div class="cb-kpi-label">Unallocatable Spend ({{{{ unit }}}})</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_unalloc }}}}</span></div>
      <div class="cb-kpi-sub">{{{{ unalloc_pct }}}}% pass-through / org-wide</div>
    </div>
    <div class="cb-kpi" title="Billed Projects: Distinct project_id count in window, split into active Control Plane projects vs. decommissioned (inactive) projects.">
      <div class="cb-kpi-label">Billed Projects</div>
      <div class="cb-kpi-value">{{{{ tot_proj }}}}</div>
      <div class="cb-kpi-sub">{{% if inact_proj > 0 %}}{{{{ act_proj }}}} active &middot; {{{{ inact_proj }}}} inactive in window{{% else %}}Active &amp; inactive in window{{% endif %}}</div>
    </div>
    <div class="cb-kpi" title="Days of Data: Distinct UTC billing days (DATE_TRUNC(1 day, @timestamp)) in the selected time range.">
      <div class="cb-kpi-label">Days of Data</div>
      <div class="cb-kpi-value">{{{{ r["days_count"].value | default: 0 }}}}</div>
      <div class="cb-kpi-sub">In selected time range</div>
    </div>
    <div class="cb-kpi" title="Latest Billed Day: Most recent closed UTC invoice snapshot.">
      <div class="cb-kpi-label">Latest Billed Day</div>
      <div class="cb-kpi-value">{{{{ l_day }}}}</div>
      <div class="cb-kpi-sub">UTC invoice snapshot</div>
    </div>
  </div>
</div>
"""


USAGE_KPI_TEMPLATE = f"""\
<style>
  .cb-kpi-wrap {{
    position: relative;
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    width: 100%;
    box-sizing: border-box;
    overflow: hidden;
  }}
  .cb-kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: var(--cc-space-s, 8px); margin: 0; padding: 0 0 2px 0; }}
  .cb-kpi {{
    background: var(--cc-color-surface);
    border: 1px solid var(--cc-color-border);
    border-radius: var(--cc-radius);
    padding: 8px 12px 9px 12px;
  }}
  .cb-kpi-label {{ color: var(--cc-color-text); opacity: 0.72; font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.03em; line-height: 1.1; }}
  .cb-kpi-value {{ color: var(--cc-color-text); font-size: 1.35rem; font-weight: 600; margin-top: 3px; line-height: 1.1; font-variant-numeric: tabular-nums; display: flex; align-items: baseline; }}
  .cb-sym {{ font-size: 1.0rem; opacity: 0.85; margin-right: 4px; font-weight: 500; }}
  .cb-kpi-sub {{ color: var(--cc-color-text); font-size: 0.73rem; opacity: 0.68; margin-top: 3px; line-height: 1.1; }}
</style>
{{% assign r = rows[0] %}}
{{% assign pool = r["pool_spend"].value | default: 0.0 %}}
{{% assign idx_s = r["indexing_spend"].value | default: 0.0 %}}
{{% assign qry_s = r["query_spend"].value | default: 0.0 %}}
{{% assign stg_s = r["storage_spend"].value | default: 0.0 %}}
{{% assign ai_s = r["ai_spend"].value | default: 0.0 %}}
{{% assign syn_s = r["synthetics_spend"].value | default: 0.0 %}}
{{% assign unit = r["unit"].value | default: "USD" %}}
{{% assign sym = r["currency_symbol"].value | default: "$" %}}
{{% assign denom = pool %}}
{{% if denom <= 0 %}}{{% assign denom = 1.0 %}}{{% endif %}}
{{% assign idx_pct = idx_s | times: 100.0 | divided_by: denom | round: 1 %}}
{{% assign qry_pct = qry_s | times: 100.0 | divided_by: denom | round: 1 %}}
{{% assign stg_pct = stg_s | times: 100.0 | divided_by: denom | round: 1 %}}
{{% assign ai_pct = ai_s | times: 100.0 | divided_by: denom | round: 1 %}}
{{% assign syn_pct = syn_s | times: 100.0 | divided_by: denom | round: 1 %}}
{liquid_money_block("pool", "fmt_pool", with_decimals=True)}
{liquid_money_block("idx_s", "fmt_idx_s", with_decimals=True)}
{liquid_money_block("qry_s", "fmt_qry_s", with_decimals=True)}
{liquid_money_block("stg_s", "fmt_stg_s", with_decimals=True)}
{liquid_money_block("ai_s", "fmt_ai_s", with_decimals=True)}
{liquid_money_block("syn_s", "fmt_syn_s", with_decimals=True)}
<div class="cb-kpi-wrap">
  <div class="cb-kpis">
    <div class="cb-kpi" title="Total Allocatable Pool: All workload-attributable spend (is_allocatable: true AND allocation_driver != 'none').">
      <div class="cb-kpi-label">Total Allocatable Pool ({{{{ unit }}}})</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_pool }}}}</span></div>
      <div class="cb-kpi-sub">100% workload-attributable spend</div>
    </div>
    <div class="cb-kpi" title="Indexing Pool: Spend from ingest-vcu &amp; ingest SKUs, attributed across data streams by indexing.index_time.">
      <div class="cb-kpi-label">Indexing Pool ({{{{ unit }}}})</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_idx_s }}}}</span></div>
      <div class="cb-kpi-sub">{{{{ idx_pct }}}}% · ingest-vcu + ingest</div>
    </div>
    <div class="cb-kpi" title="Search &amp; Query Pool: Spend from search-vcu &amp; search SKUs, attributed across data streams by search.query_time.">
      <div class="cb-kpi-label">Search &amp; Query Pool ({{{{ unit }}}})</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_qry_s }}}}</span></div>
      <div class="cb-kpi-sub">{{{{ qry_pct }}}}% · search-vcu</div>
    </div>
    <div class="cb-kpi" title="Storage Pool: Spend from retained storage SKUs, attributed across data streams by dataset.size.">
      <div class="cb-kpi-label">Storage Pool ({{{{ unit }}}})</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_stg_s }}}}</span></div>
      <div class="cb-kpi-sub">{{{{ stg_pct }}}}% · retained storage</div>
    </div>
    <div class="cb-kpi" title="AI &amp; ML Pool: Spend from ml-vcu, inference, and agent-builder SKUs.">
      <div class="cb-kpi-label">AI &amp; ML Pool ({{{{ unit }}}})</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_ai_s }}}}</span></div>
      <div class="cb-kpi-sub">{{{{ ai_pct }}}}% · inference + ml-vcu</div>
    </div>
    <div class="cb-kpi" title="Synthetics Pool: Spend from synthetics SKUs (pings and browser executions).">
      <div class="cb-kpi-label">Synthetics Pool ({{{{ unit }}}})</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_syn_s }}}}</span></div>
      <div class="cb-kpi-sub">{{{{ syn_pct }}}}% · pings + browser</div>
    </div>
  </div>
</div>
"""


SANKEY_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("sankey", 6, "grid")}
  .cb-flow {{ display: flex; flex-direction: column; gap: 6px; padding: 4px 6px; }}
  .cb-flow-head {{
    display: grid;
    grid-template-columns: minmax(80px, 0.95fr) minmax(85px, 1fr) minmax(85px, 1fr) 1.5fr minmax(85px, 115px);
    gap: 6px;
    font-size: 0.76rem; font-weight: 600; opacity: 0.75;
    text-transform: uppercase; letter-spacing: 0.03em;
    padding-bottom: 4px; border-bottom: 1px solid var(--cc-color-border);
    color: var(--cc-color-text);
  }}
  .cb-flow-head .num {{ text-align: right; }}
  .cb-row {{
    display: grid;
    grid-template-columns: minmax(80px, 0.95fr) minmax(85px, 1fr) minmax(85px, 1fr) 1.5fr minmax(85px, 115px);
    gap: 6px;
    align-items: center;
    padding: 2px 0;
  }}
  .cb-stage {{ font-size: 0.84rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--cc-color-text); display: flex; align-items: center; gap: 5px; }}
  .cb-cat-dot {{ width: 8px; height: 8px; border-radius: 2px; flex-shrink: 0; display: inline-block; }}
  .cb-share-wrap {{ display: flex; align-items: center; gap: 6px; }}
  .cb-bar-track {{
    flex: 1; height: 12px; background: var(--cc-color-border);
    border-radius: var(--cc-radius-s, 4px); overflow: hidden;
  }}
  .cb-bar {{ height: 100%; background: var(--cc-vis-0); min-width: 2px; }}
  .cb-share-pct {{ width: 40px; text-align: right; font-variant-numeric: tabular-nums; font-size: 0.76rem; opacity: 0.78; color: var(--cc-color-text); flex-shrink: 0; }}
  .cb-cost {{ text-align: right; font-variant-numeric: tabular-nums; font-size: 0.84rem; white-space: nowrap; color: var(--cc-color-text); }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "USD" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: "$" %}}
{{% assign max_cost = rows[0]["cost"].value | default: 1.0 %}}
{{% if max_cost <= 0 %}}{{% assign max_cost = 1.0 %}}{{% endif %}}
{{% assign tot_cost = 0.0 %}}
{{% for r in rows %}}
  {{% assign rc = r["cost"].value | default: 0.0 %}}
  {{% assign tot_cost = tot_cost | plus: rc %}}
{{% endfor %}}
{{% assign denom_tot = tot_cost %}}
{{% if denom_tot <= 0 %}}{{% assign denom_tot = 1.0 %}}{{% endif %}}
{paged_top("sankey", 10, 6)}
<div class="cb-flow">
  <div class="cb-flow-head">
    <div>Category</div>
    <div>Cost Type</div>
    <div>Project Type</div>
    <div>Share</div>
    <div class="num">Cost ({{{{ unit }}}})</div>
  </div>
{{% if _total_items == 0 %}}
  <div class="cb-empty">No billing spend flows recorded in the selected time range.</div>
{{% else %}}
{{% for row in rows %}}
  {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
  {{% assign cost = row["cost"].value | default: 0.0 %}}
  {{% assign bar_pct = cost | times: 100.0 | divided_by: max_cost | round: 1 %}}
  {{% assign share_pct = cost | times: 100.0 | divided_by: denom_tot | round: 1 %}}
  {{% assign cat = row["category"].value | default: "other" %}}
  {{% if cat == "data_tier" %}}{{% assign bar_color = "var(--cc-vis-0)" %}}
  {{% elsif cat == "platform" %}}{{% assign bar_color = "var(--cc-vis-1)" %}}
  {{% elsif cat == "transfer" %}}{{% assign bar_color = "var(--cc-vis-2)" %}}
  {{% elsif cat == "snapshot" %}}{{% assign bar_color = "var(--cc-vis-3)" %}}
  {{% elsif cat == "support" %}}{{% assign bar_color = "var(--cc-vis-4)" %}}
  {{% else %}}{{% assign bar_color = "var(--cc-vis-5)" %}}{{% endif %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
  <div class="cb-row cb-pg-row-sankey cb-pg-sankey-{{{{ _row_pg }}}}">
    <div class="cb-stage" title="{{{{ cat }}}}"><span class="cb-cat-dot" style="background: {{{{ bar_color }}}};"></span><strong>{{{{ cat }}}}</strong></div>
    <div class="cb-stage" title="{{{{ row[\"cost_type\"].value }}}}">→ {{{{ row["cost_type"].value }}}}</div>
    <div class="cb-stage" title="{{{{ row[\"project_type\"].value }}}}">→ {{{{ row["project_type"].value }}}}</div>
    <div class="cb-share-wrap">
      <div class="cb-bar-track"><div class="cb-bar" style="width: {{{{ bar_pct }}}}%; background: {{{{ bar_color }}}};"></div></div>
      <span class="cb-share-pct">{{{{ share_pct }}}}%</span>
    </div>
    <div class="cb-cost"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost }}}}</span></div>
  </div>
{{% endfor %}}
{{% endif %}}
</div>
{paged_bottom("sankey")}
"""


AI_KPIS_TEMPLATE = f"""\
<style>
  .cb-kpi-wrap {{
    position: relative;
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    width: 100%;
    box-sizing: border-box;
    overflow: hidden;
  }}
  .cb-kpis {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: var(--cc-space-s, 8px);
    margin: 0;
    padding: 0 0 2px 0;
  }}
  .cb-kpi {{
    background: var(--cc-color-surface);
    border: 1px solid var(--cc-color-border);
    border-radius: var(--cc-radius);
    padding: 8px 12px 9px 12px;
  }}
  .cb-kpi-label {{
    font-size: 0.76rem;
    color: var(--cc-color-text);
    opacity: 0.72;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    line-height: 1.1;
  }}
  .cb-kpi-value {{
    font-size: 1.35rem;
    font-weight: 600;
    margin-top: 3px;
    line-height: 1.1;
    color: var(--cc-color-text);
    font-variant-numeric: tabular-nums;
    display: flex;
    align-items: baseline;
  }}
  .cb-sym {{
    font-size: 1.0rem;
    font-weight: 500;
    opacity: 0.85;
    margin-right: 4px;
  }}
  .cb-kpi-sub {{
    font-size: 0.73rem;
    color: var(--cc-color-text);
    opacity: 0.68;
    margin-top: 3px;
    display: flex;
    align-items: center;
    gap: 4px;
    line-height: 1.1;
  }}
  .cb-ai-pill {{
    display: inline-block;
    padding: 1px 6px;
    border-radius: 10px;
    font-size: 0.68rem;
    font-weight: 600;
    background: color-mix(in srgb, var(--cc-color-primary) 15%, transparent);
    color: var(--cc-color-primary);
  }}
  .cb-ai-pill-shared {{
    background: color-mix(in srgb, var(--cc-color-warning, #f59f00) 18%, transparent);
    color: var(--cc-color-warning, #f59f00);
  }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "USD" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: "$" %}}
{{% assign ai_spend = rows[0]["ai_spend"].value | default: 0.0 %}}
{{% assign ded_spend = rows[0]["dedicated_ai_spend"].value | default: 0.0 %}}
{{% assign inf_count = rows[0]["total_inferences"].value | default: 0 %}}
{{% assign model_count = rows[0]["model_count"].value | default: 0 %}}
{{% assign avg_latency = rows[0]["avg_latency_ms"].value | default: 0.0 | round: 1 %}}
{{% assign cost_per_k = rows[0]["cost_per_1k"].value | default: 0.0 %}}

{liquid_money_block("ai_spend", "fmt_ai_spend", with_decimals=True)}
{liquid_money_block("inf_count", "fmt_inf_count", with_decimals=False)}
{liquid_money_block("cost_per_k", "fmt_cost_per_k", with_decimals=True)}

<div class="cb-kpi-wrap">
  <div class="cb-kpis">
    <div class="cb-kpi" title="AI &amp; ML Spend: Dedicated AI SKU spend (ml-vcu, inference, agent-builder); falls back to shared search-vcu pool when built-in models run without dedicated ML SKUs.">
      {{% if ded_spend <= 0.0 and ai_spend > 0.0 %}}
        <div class="cb-kpi-label">AI &amp; ML Pool (Shared Search, {{{{ unit }}}})</div>
        <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_ai_spend }}}}</span></div>
        <div class="cb-kpi-sub"><span class="cb-ai-pill cb-ai-pill-shared">search-vcu fallback</span> · shared with Search pool</div>
      {{% else %}}
        <div class="cb-kpi-label">Total AI &amp; ML Spend ({{{{ unit }}}})</div>
        <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_ai_spend }}}}</span></div>
        <div class="cb-kpi-sub"><span class="cb-ai-pill">ml-vcu + inference</span> · dedicated AI SKUs</div>
      {{% endif %}}
    </div>
    <div class="cb-kpi" title="Inference Volume: Total model inference calls (SUM(inference_count)) harvested via GET /_ml/trained_models/_stats.">
      <div class="cb-kpi-label">Inference Volume</div>
      <div class="cb-kpi-value">{{{{ fmt_inf_count }}}}</div>
      <div class="cb-kpi-sub">Total model inference calls</div>
    </div>
    <div class="cb-kpi" title="Active AI Models: Distinct model_id count with recorded inference activity in the selected window.">
      <div class="cb-kpi-label">Active AI Models</div>
      <div class="cb-kpi-value">{{{{ model_count }}}}</div>
      <div class="cb-kpi-sub">Trained models in window</div>
    </div>
    <div class="cb-kpi" title="Avg Model Latency: Mean inference execution time (average_inference_time_ms) across active deployments.">
      <div class="cb-kpi-label">Avg Model Latency</div>
      <div class="cb-kpi-value">{{{{ avg_latency }}}} <span style="font-size: 0.9rem; font-weight: 500; margin-left: 4px; opacity: 0.85;">ms</span></div>
      <div class="cb-kpi-sub">Compute execution speed</div>
    </div>
    <div class="cb-kpi" title="Unit Cost (1k Inferences): Effective cost per 1,000 inference calls (ai_spend × 1000 / total_inferences).">
      <div class="cb-kpi-label">Unit Cost (1k Inferences)</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost_per_k }}}}</span></div>
      <div class="cb-kpi-sub">Effective unit economics</div>
    </div>
  </div>
</div>
"""


AI_MODELS_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("aimod", 6, "grid")}
  .cb-model-list {{
    display: flex;
    flex-direction: column;
    gap: 8px;
    font-family: inherit;
    padding: 2px 4px;
  }}
  .cb-model-head {{
    display: grid;
    grid-template-columns: minmax(140px, 1.1fr) minmax(170px, 1.8fr) minmax(110px, 1.1fr) minmax(140px, 1.5fr) 100px 95px 135px;
    gap: var(--cc-space-s, 8px);
    font-size: 0.76rem;
    font-weight: 600;
    color: var(--cc-color-text);
    opacity: 0.75;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--cc-color-border);
  }}
  .cb-model-head .num {{ text-align: right; }}
  .cb-model-row {{
    display: grid;
    grid-template-columns: minmax(140px, 1.1fr) minmax(170px, 1.8fr) minmax(110px, 1.1fr) minmax(140px, 1.5fr) 100px 95px 135px;
    gap: var(--cc-space-s, 8px);
    align-items: center;
    font-size: 0.85rem;
    padding: 6px 0;
    border-bottom: 1px dashed var(--cc-color-border);
    color: var(--cc-color-text);
  }}
  .cb-model-name {{
    font-weight: 600;
    color: var(--cc-color-text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    display: flex;
    align-items: center;
    gap: 6px;
  }}
  .cb-model-proj {{
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    opacity: 0.85;
  }}
  .cb-model-tag {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.72rem;
    font-weight: 600;
    background: var(--cc-color-surface);
    border: 1px solid var(--cc-color-border);
    color: var(--cc-color-text);
    opacity: 0.85;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    white-space: nowrap;
  }}
  .cb-share-cell {{
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .cb-track {{
    flex: 1;
    height: 12px;
    background: var(--cc-color-border);
    border-radius: 6px;
    overflow: hidden;
  }}
  .cb-fill {{
    height: 100%;
    background: var(--cc-vis-0);
    border-radius: 6px;
    min-width: 2px;
  }}
  .cb-share-pct {{
    width: 42px;
    text-align: right;
    font-variant-numeric: tabular-nums;
    font-size: 0.76rem;
    opacity: 0.78;
    flex-shrink: 0;
  }}
  .cb-num {{
    text-align: right;
    font-variant-numeric: tabular-nums;
  }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "USD" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: "$" %}}
{{% assign max_time = rows[0]["total_compute"].value | default: 1.0 %}}
{{% if max_time <= 0 %}}{{% assign max_time = 1.0 %}}{{% endif %}}
{{% assign tot_compute = 0.0 %}}
{{% assign tot_inferences = 0 %}}
{{% for r in rows %}}
  {{% assign tc = r["total_compute"].value | default: 0 %}}
  {{% assign tot_compute = tot_compute | plus: tc %}}
  {{% assign ti = r["total_inf"].value | default: 0 %}}
  {{% assign tot_inferences = tot_inferences | plus: ti %}}
{{% endfor %}}

{paged_top("aimod", 10, 6)}
<div class="cb-model-list">
  <div class="cb-model-head">
    <div>Task Type</div>
    <div>Model Name</div>
    <div>Project</div>
    <div>Compute Allocation Share</div>
    <div class="num">Inferences</div>
    <div class="num">Avg Latency</div>
    <div class="num">Attributed Cost ({{{{ unit }}}})</div>
  </div>
{{% if _total_items == 0 %}}
  <div class="cb-empty">No AI model inferences recorded in the selected time range.</div>
{{% else %}}
{{% for r in rows %}}
  {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
  {{% assign c_time = r["total_compute"].value | default: 0 %}}
  {{% assign calls = r["total_inf"].value | default: 0 %}}
  {{% assign lat_ms = r["avg_latency"].value | default: 0.0 | round: 1 %}}
  {{% if max_time > 1 and tot_compute > 0 %}}
    {{% assign bar_w = c_time | times: 100.0 | divided_by: max_time | round: 1 %}}
    {{% assign share_pct = c_time | times: 100.0 | divided_by: tot_compute | round: 1 %}}
  {{% elsif tot_inferences > 0 %}}
    {{% assign bar_w = calls | times: 100.0 | divided_by: tot_inferences | round: 1 %}}
    {{% assign share_pct = calls | times: 100.0 | divided_by: tot_inferences | round: 1 %}}
  {{% else %}}
    {{% assign bar_w = 0.0 %}}
    {{% assign share_pct = 0.0 %}}
  {{% endif %}}
  {{% assign cost = r["attributed_cost"].value | default: 0.0 %}}
  {{% if cost <= 0.0 %}}
    {{% assign p_cost = r["ai_pool"].value | default: 0.0 %}}
    {{% if tot_compute > 0 %}}
      {{% assign cost = c_time | times: p_cost | divided_by: tot_compute %}}
    {{% elsif tot_inferences > 0 %}}
      {{% assign cost = calls | times: p_cost | divided_by: tot_inferences %}}
    {{% endif %}}
  {{% endif %}}
  {liquid_money_block("calls", "fmt_calls", with_decimals=False)}
  {liquid_money_block("cost", "fmt_cost", with_decimals=True)}
  <div class="cb-model-row cb-pg-row-aimod cb-pg-aimod-{{{{ _row_pg }}}}">
    <div><span class="cb-model-tag">{{{{ r["task_type"].value | default: "inference" }}}}</span></div>
    <div class="cb-model-name" title="{{{{ r[\"model_id\"].value }}}}">
      <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--cc-vis-0);"></span>
      {{{{ r["model_id"].value }}}}
    </div>
    <div class="cb-model-proj" title="{{{{ r[\"project\"].value }}}}">{{{{ r["project"].value | default: "—" }}}}</div>
    <div class="cb-share-cell">
      <div class="cb-track">
        <div class="cb-fill" style="width: {{{{ bar_w }}}}%;"></div>
      </div>
      <span class="cb-share-pct">{{{{ share_pct }}}}%</span>
    </div>
    <div class="cb-num">{{{{ fmt_calls }}}}</div>
    <div class="cb-num">{{{{ lat_ms }}}} ms</div>
    <div class="cb-num" style="font-weight: 600;"><span style="font-size:0.8rem; opacity:0.8;">{{{{ sym }}}}</span> {{{{ fmt_cost }}}}</div>
  </div>
{{% endfor %}}
{{% endif %}}
</div>
{paged_bottom("aimod")}
"""


SYNTHETICS_KPI_TEMPLATE = f"""\
<style>
  .cb-kpi-wrap {{
    position: relative;
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    width: 100%;
    box-sizing: border-box;
    overflow: hidden;
  }}
  .cb-kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap: var(--cc-space-s, 8px); margin: 0; padding: 0 0 2px 0; }}
  .cb-kpi {{
    background: var(--cc-color-surface);
    border: 1px solid var(--cc-color-border);
    border-radius: var(--cc-radius);
    padding: 8px 12px 9px 12px;
  }}
  .cb-kpi-label {{ color: var(--cc-color-text); opacity: 0.72; font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.03em; line-height: 1.1; }}
  .cb-kpi-value {{ color: var(--cc-color-text); font-size: 1.35rem; font-weight: 600; margin-top: 3px; line-height: 1.1; font-variant-numeric: tabular-nums; display: flex; align-items: baseline; }}
  .cb-sym {{ font-size: 1.0rem; opacity: 0.85; margin-right: 4px; font-weight: 500; }}
  .cb-kpi-sub {{ color: var(--cc-color-text); font-size: 0.73rem; opacity: 0.68; margin-top: 3px; line-height: 1.1; }}
</style>
{{% assign r = rows[0] %}}
{{% assign synth_spend = r["synthetics_spend"].value | default: 0.0 %}}
{{% assign pings_spend = r["pings_spend"].value | default: 0.0 %}}
{{% assign exec_spend = r["exec_spend"].value | default: 0.0 %}}
{{% assign org_spend = r["total_org_spend"].value | default: 0.0 %}}
{{% assign synth_runs = r["synthetics_runs"].value | default: 0.0 %}}
{{% assign proj_cnt = r["project_count"].value | default: 0 %}}
{{% assign cost_per_k = r["cost_per_1k"].value | default: 0.0 %}}
{{% assign unit = r["unit"].value | default: "USD" %}}
{{% assign sym = r["currency_symbol"].value | default: "$" %}}
{{% assign denom = org_spend %}}
{{% if denom <= 0 %}}{{% assign denom = 1.0 %}}{{% endif %}}
{{% assign synth_pct = synth_spend | times: 100.0 | divided_by: denom | round: 1 %}}
{{% assign s_denom = synth_spend %}}
{{% if s_denom <= 0 %}}{{% assign s_denom = 1.0 %}}{{% endif %}}
{{% assign pings_pct = pings_spend | times: 100.0 | divided_by: s_denom | round: 1 %}}
{{% assign exec_pct = exec_spend | times: 100.0 | divided_by: s_denom | round: 1 %}}
{liquid_money_block("synth_spend", "fmt_synth_spend", with_decimals=True)}
{liquid_money_block("pings_spend", "fmt_pings_spend", with_decimals=True)}
{liquid_money_block("exec_spend", "fmt_exec_spend", with_decimals=True)}
{liquid_money_block("synth_runs", "fmt_synth_runs", with_decimals=False)}
{liquid_money_block("cost_per_k", "fmt_cost_per_k", with_decimals=True)}
<div class="cb-kpi-wrap">
  <div class="cb-kpis">
    <div class="cb-kpi" title="Direct Synthetics Spend: Sum of all Synthetics SKU charges (cost_type == 'synthetics') in serverless_billing_cost_lookup.">
      <div class="cb-kpi-label">Direct Synthetics Spend ({{{{ unit }}}})</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_synth_spend }}}}</span></div>
      <div class="cb-kpi-sub">{{{{ synth_pct }}}}% of org spend · pings + browser</div>
    </div>
    <div class="cb-kpi" title="Lightweight Ping Spend: Spend on observability-synthetic-pings SKUs covering http, icmp, tcp, and dns monitors.">
      <div class="cb-kpi-label">Lightweight Ping Spend ({{{{ unit }}}})</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_pings_spend }}}}</span></div>
      <div class="cb-kpi-sub">{{{{ pings_pct }}}}% · http, icmp, tcp &amp; dns</div>
    </div>
    <div class="cb-kpi" title="Browser Execution Spend: Spend on observability-synthetic-executions SKUs covering Playwright browser journeys.">
      <div class="cb-kpi-label">Browser Execution Spend ({{{{ unit }}}})</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_exec_spend }}}}</span></div>
      <div class="cb-kpi-sub">{{{{ exec_pct }}}}% · Playwright browser journeys</div>
    </div>
    <div class="cb-kpi" title="Total Check Executions: Billed check runs across lightweight pings and Playwright browser executions.">
      <div class="cb-kpi-label">Total Check Executions</div>
      <div class="cb-kpi-value">{{{{ fmt_synth_runs }}}}</div>
      <div class="cb-kpi-sub">Lightweight pings + browser runs</div>
    </div>
    <div class="cb-kpi" title="Unit Cost (1k Checks): Blended cost per 1,000 check executions (synthetics_spend × 1000 / synthetics_runs).">
      <div class="cb-kpi-label">Unit Cost (1k Checks)</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost_per_k }}}}</span></div>
      <div class="cb-kpi-sub">Blended check unit economics</div>
    </div>
    <div class="cb-kpi" title="Billed Synthetics Projects: Distinct Serverless projects incurring direct Synthetics SKU charges in the selected window.">
      <div class="cb-kpi-label">Billed Synthetics Projects</div>
      <div class="cb-kpi-value">{{{{ proj_cnt }}}}</div>
      <div class="cb-kpi-sub">Projects with synthetics SKU spend</div>
    </div>
  </div>
</div>
"""


SYNTHETICS_DS_KPI_TEMPLATE = f"""\
<style>
  .cb-kpi-wrap {{
    position: relative;
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    width: 100%;
    box-sizing: border-box;
    overflow: hidden;
  }}
  .cb-kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: var(--cc-space-s, 8px); margin: 0; padding: 0 0 2px 0; }}
  .cb-kpi {{
    background: var(--cc-color-surface);
    border: 1px solid var(--cc-color-border);
    border-radius: var(--cc-radius);
    padding: 8px 12px 9px 12px;
  }}
  .cb-kpi-label {{ color: var(--cc-color-text); opacity: 0.72; font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.03em; line-height: 1.1; }}
  .cb-kpi-value {{ color: var(--cc-color-text); font-size: 1.35rem; font-weight: 600; margin-top: 3px; line-height: 1.1; font-variant-numeric: tabular-nums; display: flex; align-items: baseline; }}
  .cb-sym {{ font-size: 1.0rem; opacity: 0.85; margin-right: 4px; font-weight: 500; }}
  .cb-kpi-sub {{ color: var(--cc-color-text); font-size: 0.73rem; opacity: 0.68; margin-top: 3px; line-height: 1.1; }}
</style>
{{% assign r = rows[0] %}}
{{% assign ds_spend = r["ds_spend"].value | default: 0.0 %}}
{{% assign idx_s = r["indexing_spend"].value | default: 0.0 %}}
{{% assign qry_s = r["query_spend"].value | default: 0.0 %}}
{{% assign stg_s = r["storage_spend"].value | default: 0.0 %}}
{{% assign ds_cnt = r["ds_count"].value | default: 0 %}}
{{% assign unit = r["unit"].value | default: "USD" %}}
{{% assign sym = r["currency_symbol"].value | default: "$" %}}
{liquid_money_block("ds_spend", "fmt_ds_spend", with_decimals=True)}
{liquid_money_block("idx_s", "fmt_idx_s", with_decimals=True)}
{liquid_money_block("stg_s", "fmt_stg_s", with_decimals=True)}
<div class="cb-kpi-wrap">
  <div class="cb-kpis">
    <div class="cb-kpi" title="Attributed Data-Tier Spend: Total Elasticsearch data-tier spend (Indexing + Search + Storage) attributed to synthetics-* data streams.">
      <div class="cb-kpi-label">Attributed Data-Tier Spend ({{{{ unit }}}})</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_ds_spend }}}}</span></div>
      <div class="cb-kpi-sub">Ingest + Search + Storage for synthetics-*</div>
    </div>
    <div class="cb-kpi" title="Synthetics Indexing Spend: Portion of project ingest-vcu &amp; ingest pools consumed by indexing synthetic check results and browser traces.">
      <div class="cb-kpi-label">Synthetics Indexing Spend ({{{{ unit }}}})</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_idx_s }}}}</span></div>
      <div class="cb-kpi-sub">Attributed ingest-vcu + ingest</div>
    </div>
    <div class="cb-kpi" title="Synthetics Storage Spend: Portion of project retained storage pool consumed by storing synthetics-* backing indices.">
      <div class="cb-kpi-label">Synthetics Storage Spend ({{{{ unit }}}})</div>
      <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_stg_s }}}}</span></div>
      <div class="cb-kpi-sub">Attributed retained storage</div>
    </div>
    <div class="cb-kpi" title="Synthetics Data Streams: Count of active synthetics-* data streams harvested via _cat/indices.">
      <div class="cb-kpi-label">Synthetics Data Streams</div>
      <div class="cb-kpi-value">{{{{ ds_cnt }}}}</div>
      <div class="cb-kpi-sub">Monitored synthetics-* streams</div>
    </div>
  </div>
</div>
"""


def make_daily_cost_template(
    group_field: str,
    header_label: str = "Project",
    is_category: bool = False,
) -> str:
    palette_colors = (
        "var(--cc-vis-0),var(--cc-vis-1),var(--cc-vis-2),var(--cc-vis-3),var(--cc-vis-4),var(--cc-vis-5),var(--cc-vis-6),var(--cc-vis-7),var(--cc-vis-8),var(--cc-vis-9)"
    )
    if is_category:
        legend_color_code = """\
      {% if itm == "data_tier" %}{% assign item_color = "var(--cc-vis-0)" %}
      {% elsif itm == "platform" %}{% assign item_color = "var(--cc-vis-1)" %}
      {% elsif itm == "transfer" %}{% assign item_color = "var(--cc-vis-2)" %}
      {% elsif itm == "snapshot" %}{% assign item_color = "var(--cc-vis-3)" %}
      {% elsif itm == "support" %}{% assign item_color = "var(--cc-vis-4)" %}
      {% elsif itm == "other" %}{% assign item_color = "var(--cc-vis-5)" %}
      {% else %}
        {% assign c_idx = forloop.index0 | modulo: 10 %}
        {% assign item_color = colors[c_idx] %}
      {% endif %}"""
        seg_color_code = """\
          {% if p_name == "data_tier" %}{% assign item_color = "var(--cc-vis-0)" %}
          {% elsif p_name == "platform" %}{% assign item_color = "var(--cc-vis-1)" %}
          {% elsif p_name == "transfer" %}{% assign item_color = "var(--cc-vis-2)" %}
          {% elsif p_name == "snapshot" %}{% assign item_color = "var(--cc-vis-3)" %}
          {% elsif p_name == "support" %}{% assign item_color = "var(--cc-vis-4)" %}
          {% elsif p_name == "other" %}{% assign item_color = "var(--cc-vis-5)" %}
          {% else %}
            {% assign item_color = "var(--cc-vis-9)" %}
            {% for itm in items %}
              {% if itm == p_name %}
                {% assign c_idx = forloop.index0 | modulo: 10 %}
                {% assign item_color = colors[c_idx] %}
              {% endif %}
            {% endfor %}
          {% endif %}"""
    else:
        legend_color_code = """\
      {% assign c_idx = forloop.index0 | modulo: 10 %}
      {% assign item_color = colors[c_idx] %}"""
        seg_color_code = """\
          {% assign item_color = "var(--cc-vis-0)" %}
          {% for itm in items %}
            {% if itm == p_name %}
              {% assign c_idx = forloop.index0 | modulo: 10 %}
              {% assign item_color = colors[c_idx] %}
            {% endif %}
          {% endfor %}"""

    return f"""\
{{% assign _total_rows = rows.size | default: 0 %}}
<style>
  .cb-daily-container {{
    position: relative;
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 180px;
    width: 100%;
    box-sizing: border-box;
    padding: 4px 8px 6px 8px;
    overflow: hidden;
  }}
  .cb-daily-head {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.78rem;
    font-weight: 600;
    opacity: 0.92;
    padding: 0 4px 6px 4px;
    border-bottom: 1px solid var(--cc-color-border);
    margin-bottom: 6px;
    gap: 10px;
    flex-shrink: 0;
  }}
  .cb-daily-title {{
    text-transform: uppercase;
    letter-spacing: 0.03em;
    white-space: nowrap;
    opacity: 0.75;
    color: var(--cc-color-text);
    font-size: 0.76rem;
  }}
  .cb-daily-legend {{
    display: flex;
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-end;
    gap: 6px 12px;
    user-select: none;
    max-height: 40px;
    overflow-y: auto;
    margin-left: auto;
  }}
  .cb-legend-item {{
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 0.75rem;
    font-weight: 500;
    color: var(--cc-color-text);
  }}
  .cb-legend-box {{
    width: 10px;
    height: 10px;
    border-radius: 2px;
    flex-shrink: 0;
    display: inline-block;
    border: 1px solid var(--cc-color-border);
  }}
  .cb-legend-label {{
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 165px;
  }}
  .cb-daily-wrap {{
    display: flex;
    flex-direction: row;
    align-items: stretch;
    flex: 1;
    min-height: 150px;
    font-family: inherit;
    box-sizing: border-box;
    padding: 0;
    overflow: hidden;
  }}
  .cb-daily-main {{
    display: flex;
    flex: 1;
    position: relative;
    min-height: 150px;
    gap: 8px;
    min-width: 0;
  }}
  .cb-hist-y {{
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    font-size: 0.72rem;
    color: var(--cc-color-text);
    opacity: 0.72;
    text-align: right;
    padding-right: 8px;
    min-width: 48px;
    white-space: nowrap;
    border-right: 1px solid var(--cc-color-border);
    user-select: none;
    margin-top: 1px;
    margin-bottom: 20px;
    box-sizing: border-box;
    font-variant-numeric: tabular-nums;
  }}
  .cb-hist-plot {{
    display: flex;
    flex: 1;
    overflow-x: auto;
    position: relative;
    min-width: 0;
  }}
  .cb-hist-canvas {{
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    min-width: 100%;
    height: 100%;
    position: relative;
    padding-bottom: 26px;
    padding-top: 6px;
    box-sizing: border-box;
  }}
  .cb-hist-grid {{
    position: absolute;
    top: 6px;
    left: 0;
    right: 0;
    bottom: 26px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    pointer-events: none;
    z-index: 0;
  }}
  .cb-grid-line {{
    width: 100%;
    height: 1px;
    background: var(--cc-color-border);
    opacity: 0.4;
  }}
  .cb-grid-line-zero {{
    background: var(--cc-color-border);
    opacity: 1;
  }}
  .cb-hist-col {{
    display: flex;
    flex-direction: column;
    align-items: center;
    flex: 1 1 0;
    min-width: 4px;
    max-width: 38px;
    height: 100%;
    justify-content: flex-end;
    position: relative;
    z-index: 1;
    margin: 0 1px;
  }}
  .cb-hist-stack {{
    display: flex;
    flex-direction: column-reverse;
    width: 100%;
    border-radius: 2px 2px 0 0;
    overflow: hidden;
  }}
  .cb-hist-seg {{
    width: 100%;
    transition: opacity var(--cc-motion-fast, 0.15s) ease-in-out;
    cursor: pointer;
  }}
  .cb-hist-seg:hover {{
    filter: brightness(1.15);
    opacity: 0.9;
  }}
  .cb-hist-date {{
    position: absolute;
    bottom: -22px;
    font-size: 0.70rem;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
    color: var(--cc-color-text);
    opacity: 0.75;
    white-space: nowrap;
    text-align: center;
    left: 50%;
    transform: translateX(-50%);
  }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
  .cb-empty {{ display:flex; align-items:center; justify-content:center; flex:1; min-height:140px; font-size:0.84rem; color:var(--cc-color-text); opacity:0.65; }}
</style>
{{% assign unit = "USD" %}}
{{% assign sym = "$" %}}
{{% for r in rows %}}
  {{% assign r_unit = r["unit"].value | default: "" %}}
  {{% if r_unit != "" %}}
    {{% assign unit = r_unit %}}
    {{% assign r_sym = r["currency_symbol"].value | default: "" %}}
    {{% if r_sym != "" %}}
      {{% assign sym = r_sym %}}
    {{% else %}}
      {{% assign sym = unit %}}
    {{% endif %}}
    {{% break %}}
  {{% endif %}}
{{% endfor %}}
{{% assign colors = "{palette_colors}" | split: "," %}}

{{% assign item_str = "||" %}}
{{% assign b_str = "||" %}}
{{% assign max_day_cost = 1.0 %}}
{{% for r in rows %}}
  {{% assign itm = r["{group_field}"].value | default: "unknown" %}}
  {{% assign itm_tok = "||" | append: itm | append: "||" %}}
  {{% unless item_str contains itm_tok %}}
    {{% assign item_str = item_str | append: itm | append: "||" %}}
  {{% endunless %}}
  {{% assign b_val = r["day"].value | default: "" %}}
  {{% assign b_tok = "||" | append: b_val | append: "||" %}}
  {{% unless b_str contains b_tok %}}
    {{% assign b_str = b_str | append: b_val | append: "||" %}}
  {{% endunless %}}
  {{% assign dc = r["day_cost"].value | default: 0.0 %}}
  {{% if dc > max_day_cost %}}
    {{% assign max_day_cost = dc %}}
  {{% endif %}}
{{% endfor %}}
{{% assign items = item_str | remove_first: "||" | split: "||" %}}
{{% assign all_b = b_str | remove_first: "||" | split: "||" %}}
{{% assign b_count = all_b.size | minus: 1 %}}

{{% if b_count <= 8 %}}
  {{% assign step_mod = 1 %}}
{{% elsif b_count <= 16 %}}
  {{% assign step_mod = 2 %}}
{{% elsif b_count <= 24 %}}
  {{% assign step_mod = 3 %}}
{{% elsif b_count <= 35 %}}
  {{% assign step_mod = 5 %}}
{{% elsif b_count <= 50 %}}
  {{% assign step_mod = 7 %}}
{{% elsif b_count <= 75 %}}
  {{% assign step_mod = 10 %}}
{{% else %}}
  {{% assign step_mod = b_count | divided_by: 6 | floor %}}
  {{% if step_mod < 10 %}}{{% assign step_mod = 10 %}}{{% endif %}}
{{% endif %}}

{{% assign raw_top = max_day_cost | times: 1.15 %}}
{{% if raw_top <= 4 %}}{{% assign y_top = 4 %}}
{{% elsif raw_top <= 8 %}}{{% assign y_top = 8 %}}
{{% elsif raw_top <= 16 %}}{{% assign y_top = 16 %}}
{{% elsif raw_top <= 40 %}}{{% assign y_top = 40 %}}
{{% elsif raw_top <= 80 %}}{{% assign y_top = 80 %}}
{{% elsif raw_top <= 120 %}}{{% assign y_top = 120 %}}
{{% elsif raw_top <= 200 %}}{{% assign y_top = 200 %}}
{{% elsif raw_top <= 320 %}}{{% assign y_top = 320 %}}
{{% elsif raw_top <= 480 %}}{{% assign y_top = 480 %}}
{{% elsif raw_top <= 800 %}}{{% assign y_top = 800 %}}
{{% elsif raw_top <= 1200 %}}{{% assign y_top = 1200 %}}
{{% elsif raw_top <= 2000 %}}{{% assign y_top = 2000 %}}
{{% elsif raw_top <= 4000 %}}{{% assign y_top = 4000 %}}
{{% elsif raw_top <= 8000 %}}{{% assign y_top = 8000 %}}
{{% elsif raw_top <= 20000 %}}{{% assign y_top = 20000 %}}
{{% elsif raw_top <= 40000 %}}{{% assign y_top = 40000 %}}
{{% else %}}
  {{% assign y_top = raw_top | divided_by: 10000 | floor | plus: 1 | times: 10000 %}}
{{% endif %}}
{{% assign y_1 = y_top | times: 0.25 | round %}}
{{% assign y_2 = y_top | times: 0.50 | round %}}
{{% assign y_3 = y_top | times: 0.75 | round %}}
{{% assign y_4 = y_top %}}

{liquid_money_block("y_4", "fmt_y_4", with_decimals=False)}
{liquid_money_block("y_3", "fmt_y_3", with_decimals=False)}
{liquid_money_block("y_2", "fmt_y_2", with_decimals=False)}
{liquid_money_block("y_1", "fmt_y_1", with_decimals=False)}

{{% assign m_arr = "Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec" | split: "," %}}

<div class="cb-daily-container">
  <div class="cb-daily-head">
    <div class="cb-daily-title">Cost ({{{{ unit }}}})</div>
    <div class="cb-daily-legend">
      {{% for itm in items %}}
        {{% if itm != "" %}}
  {legend_color_code}
          <div class="cb-legend-item">
            <span class="cb-legend-box" style="background: {{{{ item_color }}}};"></span>
            <span class="cb-legend-label" title="{{{{ itm }}}}">{{{{ itm }}}}</span>
          </div>
        {{% endif %}}
      {{% endfor %}}
    </div>
  </div>
{{% if _total_rows > 0 %}}
  <div class="cb-daily-wrap">
    <div class="cb-daily-main">
      <div class="cb-hist-y">
        <div><span class="cb-sym">{{{{ sym }}}}</span>{{{{ fmt_y_4 }}}}</div>
        <div><span class="cb-sym">{{{{ sym }}}}</span>{{{{ fmt_y_3 }}}}</div>
        <div><span class="cb-sym">{{{{ sym }}}}</span>{{{{ fmt_y_2 }}}}</div>
        <div><span class="cb-sym">{{{{ sym }}}}</span>{{{{ fmt_y_1 }}}}</div>
        <div><span class="cb-sym">{{{{ sym }}}}</span>0</div>
      </div>
      <div class="cb-hist-plot">
        <div class="cb-hist-canvas">
          <div class="cb-hist-grid">
            <div class="cb-grid-line"></div>
            <div class="cb-grid-line"></div>
            <div class="cb-grid-line"></div>
            <div class="cb-grid-line"></div>
            <div class="cb-grid-line cb-grid-line-zero"></div>
          </div>
          {{% assign cur_bucket = "" %}}
          {{% assign cur_label = "" %}}
          {{% assign b_idx = -1 %}}
          {{% for r in rows %}}
            {{% assign b_raw = r["day"].value | default: "" %}}
            {{% if b_raw != cur_bucket %}}
              {{% if cur_bucket != "" %}}
                  </div>
                  {{% assign show_lbl = b_idx | modulo: step_mod %}}
                  {{% if show_lbl == 0 %}}
                    <div class="cb-hist-date">{{{{ cur_label }}}}</div>
                  {{% endif %}}
                </div>
              {{% endif %}}
              {{% assign cur_bucket = b_raw %}}
              {{% assign b_idx = b_idx | plus: 1 %}}
              {{% assign time_sub = cur_bucket | slice: 11, 8 %}}
              {{% if time_sub == "00:00:00" or time_sub == "" %}}
                {{% assign m_idx = cur_bucket | slice: 5, 2 | plus: 0 | minus: 1 %}}
                {{% assign m_name = m_arr[m_idx] | default: "" %}}
                {{% assign d_num = cur_bucket | slice: 8, 2 | plus: 0 %}}
                {{% if m_name != "" %}}
                  {{% assign cur_label = m_name | append: " " | append: d_num %}}
                {{% else %}}
                  {{% assign cur_label = cur_bucket | slice: 5, 5 %}}
                {{% endif %}}
                {{% assign cur_tip = cur_bucket | slice: 0, 10 %}}
              {{% else %}}
                {{% assign cur_label = cur_bucket | slice: 11, 5 %}}
                {{% assign cur_tip = cur_bucket | slice: 0, 16 | replace: "T", " " %}}
              {{% endif %}}
              {{% assign d_cost = r["day_cost"].value | default: 0.0 %}}
              {{% assign stack_pct = d_cost | times: 100.0 | divided_by: y_top | round: 2 %}}
              {{% if stack_pct > 100.0 %}}{{% assign stack_pct = 100.0 %}}{{% endif %}}
              {liquid_money_block("d_cost", "fmt_d_cost", with_decimals=True)}
              <div class="cb-hist-col" title="{{{{ cur_tip }}}}: {{{{ sym }}}}{{{{ fmt_d_cost }}}}">
                <div class="cb-hist-stack" style="height: {{{{ stack_pct }}}}%;">
            {{% endif %}}
            {{% assign p_cost = r["cost"].value | default: 0.0 %}}
            {{% assign p_name = r["{group_field}"].value | default: "unknown" %}}
            {{% assign seg_pct = 0.0 %}}
            {{% if d_cost > 0 %}}
              {{% assign seg_pct = p_cost | times: 100.0 | divided_by: d_cost | round: 2 %}}
            {{% endif %}}
            {{% if seg_pct > 0 %}}
  {seg_color_code}
              {liquid_money_block("p_cost", "fmt_p_cost", with_decimals=True)}
              <div class="cb-hist-seg" style="flex: 0 0 {{{{ seg_pct }}}}%; height: {{{{ seg_pct }}}}%; background: {{{{ item_color }}}};" title="{{{{ p_name }}}}: {{{{ sym }}}}{{{{ fmt_p_cost }}}} ({{{{ cur_tip }}}})"></div>
            {{% endif %}}
          {{% endfor %}}
          {{% if cur_bucket != "" %}}
              </div>
              {{% assign show_lbl = b_idx | modulo: step_mod %}}
              {{% if show_lbl == 0 %}}
                <div class="cb-hist-date">{{{{ cur_label }}}}</div>
              {{% endif %}}
            </div>
          {{% endif %}}
        </div>
      </div>
    </div>
  </div>
{{% else %}}
  <div class="cb-empty">No daily cost data in the selected time range.</div>
{{% endif %}}
</div>
"""


DAILY_PROJECT_COST_TEMPLATE = make_daily_cost_template(
    "project",
    "Project",
    is_category=False,
)

DAILY_CATEGORY_COST_TEMPLATE = make_daily_cost_template(
    "category",
    "Cost category",
    is_category=True,
)

DAILY_DRIVER_COST_TEMPLATE = make_daily_cost_template(
    "driver",
    "Workload driver",
    is_category=False,
)

DAILY_MODEL_COST_TEMPLATE = make_daily_cost_template(
    "model_id",
    "Model",
    is_category=False,
)

DAILY_SYNTH_TYPE_COST_TEMPLATE = make_daily_cost_template(
    "monitor_type",
    "Synthetic type",
    is_category=False,
)


AI_BILLING_TABLE_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("aibill", 10, "table-row")}
  table.cb-t {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; color: var(--cc-color-text); }}
  table.cb-t th {{
    position: sticky; top: 0; background: var(--cc-color-surface); z-index: 1;
    text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--cc-color-border);
    font-weight: 600; opacity: 0.75; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em;
    color: var(--cc-color-text);
  }}
  table.cb-t td {{ padding: 6px 8px; border-bottom: 1px solid var(--cc-color-border); color: var(--cc-color-text); }}
  table.cb-t th.num, table.cb-t td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "USD" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: "$" %}}
{paged_top("aibill", 10, 10)}
<table class="cb-t">
  <thead>
    <tr><th>Project</th><th>Project Type</th><th>Cost Type</th><th>SKU</th><th class="num">Quantity</th><th class="num">Units (ECU)</th><th class="num">Cost ({{{{ unit }}}})</th></tr>
  </thead>
  <tbody>
  {{% if _total_items == 0 %}}
    <tr><td colspan="7" class="cb-empty">No dedicated AI, ML VCU, Inference, or Agent Builder SKU line items in the selected time range.</td></tr>
  {{% else %}}
  {{% for row in rows %}}
    {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
    {{% assign cost = row["cost"].value | default: 0.0 %}}
    {{% assign ecu = row["ecu"].value | default: 0.0 %}}
    {{% assign qty = row["qty"].value | default: 0.0 %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
{liquid_money_block("ecu", "fmt_ecu", with_decimals=True)}
{liquid_money_block("qty", "fmt_qty", with_decimals=True)}
    <tr class="cb-pg-row-aibill cb-pg-aibill-{{{{ _row_pg }}}}">
      <td><strong>{{{{ row["project"].value }}}}</strong></td>
      <td>{{{{ row["project_type"].value }}}}</td>
      <td>{{{{ row["cost_type"].value }}}}</td>
      <td><code>{{{{ row["sku"].value }}}}</code></td>
      <td class="num">{{{{ fmt_qty }}}}</td>
      <td class="num">{{{{ fmt_ecu }}}}</td>
      <td class="num"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost }}}}</span></td>
    </tr>
  {{% endfor %}}
  {{% endif %}}
  </tbody>
</table>
{paged_bottom("aibill")}
"""


SYNTHETICS_SKU_TABLE_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("synbill", 10, "table-row")}
  table.cb-t {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; color: var(--cc-color-text); }}
  table.cb-t th {{
    position: sticky; top: 0; background: var(--cc-color-surface); z-index: 1;
    text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--cc-color-border);
    font-weight: 600; opacity: 0.75; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em;
    color: var(--cc-color-text);
  }}
  table.cb-t td {{ padding: 6px 8px; border-bottom: 1px solid var(--cc-color-border); color: var(--cc-color-text); }}
  table.cb-t th.num, table.cb-t td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "USD" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: "$" %}}
{paged_top("synbill", 10, 10)}
<table class="cb-t">
  <thead>
    <tr><th>Project</th><th>Project Type</th><th>SKU</th><th class="num">Check Quantity</th><th class="num">Units (ECU)</th><th class="num">Cost ({{{{ unit }}}})</th></tr>
  </thead>
  <tbody>
  {{% if _total_items == 0 %}}
    <tr><td colspan="6" class="cb-empty">No direct Synthetics billing SKUs recorded in the selected time range.</td></tr>
  {{% else %}}
  {{% for row in rows %}}
    {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
    {{% assign cost = row["cost"].value | default: 0.0 %}}
    {{% assign ecu = row["ecu"].value | default: 0.0 %}}
    {{% assign qty = row["qty"].value | default: 0.0 %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
{liquid_money_block("ecu", "fmt_ecu", with_decimals=True)}
{liquid_money_block("qty", "fmt_qty", with_decimals=True)}
    <tr class="cb-pg-row-synbill cb-pg-synbill-{{{{ _row_pg }}}}">
      <td><strong>{{{{ row["project"].value }}}}</strong></td>
      <td>{{{{ row["project_type"].value }}}}</td>
      <td><code>{{{{ row["sku"].value }}}}</code></td>
      <td class="num">{{{{ fmt_qty }}}}</td>
      <td class="num">{{{{ fmt_ecu }}}}</td>
      <td class="num"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost }}}}</span></td>
    </tr>
  {{% endfor %}}
  {{% endif %}}
  </tbody>
</table>
{paged_bottom("synbill")}
"""


SYNTHETICS_TYPE_BREAKDOWN_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("syntype", 8, "table-row")}
  table.cb-t {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; color: var(--cc-color-text); }}
  table.cb-t th {{
    position: sticky; top: 0; background: var(--cc-color-surface); z-index: 1;
    text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--cc-color-border);
    font-weight: 600; opacity: 0.75; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em;
    color: var(--cc-color-text);
  }}
  table.cb-t td {{ padding: 6px 8px; border-bottom: 1px solid var(--cc-color-border); color: var(--cc-color-text); }}
  table.cb-t th.num, table.cb-t td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
  .cb-share-wrap {{ display: flex; align-items: center; gap: 6px; min-width: 110px; }}
  .cb-bar-track {{
    flex: 1; height: 10px; background: var(--cc-color-border);
    border-radius: var(--cc-radius-s); overflow: hidden; min-width: 55px;
  }}
  .cb-bar-fill {{ height: 100%; min-width: 2px; }}
  .cb-share-pct {{ width: 42px; text-align: right; font-variant-numeric: tabular-nums; font-size: 0.76rem; opacity: 0.78; color: var(--cc-color-text); flex-shrink: 0; }}
  .cb-st-pill {{
    display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 0.73rem;
    font-weight: 600; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    text-transform: uppercase; letter-spacing: 0.03em;
    border: 1px solid rgba(128, 128, 128, 0.25); background: rgba(128, 128, 128, 0.12);
  }}
  .cb-st-http {{ background: rgba(37, 99, 235, 0.14); color: #3b82f6; border-color: rgba(37, 99, 235, 0.35); }}
  .cb-st-browser {{ background: rgba(147, 51, 234, 0.14); color: #a855f7; border-color: rgba(147, 51, 234, 0.35); }}
  .cb-st-icmp {{ background: rgba(16, 185, 129, 0.14); color: #10b981; border-color: rgba(16, 185, 129, 0.35); }}
  .cb-st-tcp {{ background: rgba(245, 158, 11, 0.14); color: #f59e0b; border-color: rgba(245, 158, 11, 0.35); }}
  .cb-st-dns {{ background: rgba(6, 182, 212, 0.14); color: #06b6d4; border-color: rgba(6, 182, 212, 0.35); }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "USD" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: "$" %}}
{{% assign max_cost = rows[0]["cost"].value | default: 1.0 %}}
{{% if max_cost <= 0 %}}{{% assign max_cost = 1.0 %}}{{% endif %}}
{{% assign tot_cost = 0.0 %}}
{{% for r in rows %}}
  {{% assign rc = r["cost"].value | default: 0.0 %}}
  {{% assign tot_cost = tot_cost | plus: rc %}}
{{% endfor %}}
{{% assign denom_tot = tot_cost %}}
{{% if denom_tot <= 0 %}}{{% assign denom_tot = 1.0 %}}{{% endif %}}
{paged_top("syntype", 10, 8)}
<table class="cb-t">
  <thead>
    <tr><th>Synthetic Type</th><th class="num">Monitors</th><th class="num">Check Runs</th><th>Cost Share</th><th class="num">Attributed Cost ({{{{ unit }}}})</th></tr>
  </thead>
  <tbody>
  {{% if _total_items == 0 %}}
    <tr><td colspan="5" class="cb-empty">No synthetics monitor telemetry in the selected time range.</td></tr>
  {{% else %}}
  {{% for row in rows %}}
    {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
    {{% assign mtype = row["monitor_type"].value | default: "http" | downcase %}}
    {{% assign cost = row["cost"].value | default: 0.0 %}}
    {{% assign checks = row["checks"].value | default: 0 %}}
    {{% assign monitors = row["monitors"].value | default: 0 %}}
    {{% assign bar_pct = cost | times: 100.0 | divided_by: max_cost | round: 1 %}}
    {{% assign share_pct = cost | times: 100.0 | divided_by: denom_tot | round: 1 %}}
    {{% if mtype == "http" %}}{{% assign _bcol = "#3b82f6" %}}
    {{% elsif mtype == "browser" %}}{{% assign _bcol = "#a855f7" %}}
    {{% elsif mtype == "icmp" %}}{{% assign _bcol = "#10b981" %}}
    {{% elsif mtype == "tcp" %}}{{% assign _bcol = "#f59e0b" %}}
    {{% elsif mtype == "dns" %}}{{% assign _bcol = "#06b6d4" %}}
    {{% else %}}{{% assign _bcol = "var(--cc-vis-0)" %}}{{% endif %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
{liquid_money_block("checks", "fmt_checks", with_decimals=False)}
    <tr class="cb-pg-row-syntype cb-pg-syntype-{{{{ _row_pg }}}}">
      <td><span class="cb-st-pill cb-st-{{{{ mtype }}}}">{{{{ mtype }}}}</span></td>
      <td class="num">{{{{ monitors }}}}</td>
      <td class="num">{{{{ fmt_checks }}}}</td>
      <td>
        <div class="cb-share-wrap">
          <div class="cb-bar-track"><div class="cb-bar-fill" style="width: {{{{ bar_pct }}}}%; background: {{{{ _bcol }}}};"></div></div>
          <span class="cb-share-pct">{{{{ share_pct }}}}%</span>
        </div>
      </td>
      <td class="num"><strong><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost }}}}</span></strong></td>
    </tr>
  {{% endfor %}}
  {{% endif %}}
  </tbody>
</table>
{paged_bottom("syntype")}
"""


SYNTHETICS_MONITORS_TABLE_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("synmon", 25, "table-row")}
  .cb-flt-bar-synmon {{ display: flex; align-items: center; gap: 5px; padding: 2px 2px 6px 2px; font-size: 0.75rem; color: var(--cc-color-text); flex-shrink: 0; flex-wrap: wrap; }}
  .cb-flt-lbl {{ opacity: 0.72; font-weight: 600; font-size: 0.73rem; text-transform: uppercase; letter-spacing: 0.03em; margin-right: 2px; }}
  #synmon_flt_all:checked ~ .cb-flt-bar-synmon .cb-flt-btn-synmon-all,
  #synmon_flt_http:checked ~ .cb-flt-bar-synmon .cb-flt-btn-synmon-http,
  #synmon_flt_browser:checked ~ .cb-flt-bar-synmon .cb-flt-btn-synmon-browser,
  #synmon_flt_dns:checked ~ .cb-flt-bar-synmon .cb-flt-btn-synmon-dns,
  #synmon_flt_tcp:checked ~ .cb-flt-bar-synmon .cb-flt-btn-synmon-tcp,
  #synmon_flt_icmp:checked ~ .cb-flt-bar-synmon .cb-flt-btn-synmon-icmp {{ background: var(--cc-color-primary); color: #ffffff !important; border-color: var(--cc-color-primary); font-weight: 700; }}
  #synmon_flt_http:checked ~ .cb-content-wrap-synmon,
  #synmon_flt_browser:checked ~ .cb-content-wrap-synmon,
  #synmon_flt_dns:checked ~ .cb-content-wrap-synmon,
  #synmon_flt_tcp:checked ~ .cb-content-wrap-synmon,
  #synmon_flt_icmp:checked ~ .cb-content-wrap-synmon {{ overflow-y: auto !important; }}
  #synmon_flt_http:checked ~ .cb-content-wrap-synmon .cb-pg-row-synmon,
  #synmon_flt_browser:checked ~ .cb-content-wrap-synmon .cb-pg-row-synmon,
  #synmon_flt_dns:checked ~ .cb-content-wrap-synmon .cb-pg-row-synmon,
  #synmon_flt_tcp:checked ~ .cb-content-wrap-synmon .cb-pg-row-synmon,
  #synmon_flt_icmp:checked ~ .cb-content-wrap-synmon .cb-pg-row-synmon {{ display: none !important; }}
  #synmon_flt_http:checked ~ .cb-content-wrap-synmon .cb-pg-row-synmon.cb-mt-http,
  #synmon_flt_browser:checked ~ .cb-content-wrap-synmon .cb-pg-row-synmon.cb-mt-browser,
  #synmon_flt_dns:checked ~ .cb-content-wrap-synmon .cb-pg-row-synmon.cb-mt-dns,
  #synmon_flt_tcp:checked ~ .cb-content-wrap-synmon .cb-pg-row-synmon.cb-mt-tcp,
  #synmon_flt_icmp:checked ~ .cb-content-wrap-synmon .cb-pg-row-synmon.cb-mt-icmp {{ display: table-row !important; }}
  #synmon_flt_http:checked ~ .cb-pg-bar-synmon,
  #synmon_flt_browser:checked ~ .cb-pg-bar-synmon,
  #synmon_flt_dns:checked ~ .cb-pg-bar-synmon,
  #synmon_flt_tcp:checked ~ .cb-pg-bar-synmon,
  #synmon_flt_icmp:checked ~ .cb-pg-bar-synmon {{ display: none !important; }}
  table.cb-t {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; color: var(--cc-color-text); }}
  table.cb-t th {{
    position: sticky; top: 0; background: var(--cc-color-surface); z-index: 1;
    text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--cc-color-border);
    font-weight: 600; opacity: 0.75; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em;
    color: var(--cc-color-text);
  }}
  table.cb-t td {{ padding: 6px 8px; border-bottom: 1px solid var(--cc-color-border); color: var(--cc-color-text); }}
  table.cb-t th.num, table.cb-t td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
  .cb-share-wrap {{ display: flex; align-items: center; gap: 6px; min-width: 110px; }}
  .cb-bar-track {{
    flex: 1; height: 10px; background: var(--cc-color-border);
    border-radius: var(--cc-radius-s); overflow: hidden; min-width: 55px;
  }}
  .cb-bar-fill {{ height: 100%; min-width: 2px; }}
  .cb-share-pct {{ width: 42px; text-align: right; font-variant-numeric: tabular-nums; font-size: 0.76rem; opacity: 0.78; color: var(--cc-color-text); flex-shrink: 0; }}
  .cb-st-pill {{
    display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 0.72rem;
    font-weight: 600; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    text-transform: uppercase; letter-spacing: 0.03em;
    border: 1px solid rgba(128, 128, 128, 0.25); background: rgba(128, 128, 128, 0.12);
  }}
  .cb-st-http {{ background: rgba(37, 99, 235, 0.14); color: #3b82f6; border-color: rgba(37, 99, 235, 0.35); }}
  .cb-st-browser {{ background: rgba(147, 51, 234, 0.14); color: #a855f7; border-color: rgba(147, 51, 234, 0.35); }}
  .cb-st-icmp {{ background: rgba(16, 185, 129, 0.14); color: #10b981; border-color: rgba(16, 185, 129, 0.35); }}
  .cb-st-tcp {{ background: rgba(245, 158, 11, 0.14); color: #f59e0b; border-color: rgba(245, 158, 11, 0.35); }}
  .cb-st-dns {{ background: rgba(6, 182, 212, 0.14); color: #06b6d4; border-color: rgba(6, 182, 212, 0.35); }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "USD" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: "$" %}}
{{% assign max_cost = rows[0]["cost"].value | default: 1.0 %}}
{{% if max_cost <= 0 %}}{{% assign max_cost = 1.0 %}}{{% endif %}}
{{% assign tot_cost = 0.0 %}}
{{% for r in rows %}}
  {{% assign rc = r["cost"].value | default: 0.0 %}}
  {{% assign tot_cost = tot_cost | plus: rc %}}
{{% endfor %}}
{{% assign denom_tot = tot_cost %}}
{{% if denom_tot <= 0 %}}{{% assign denom_tot = 1.0 %}}{{% endif %}}
{{% assign _total_items = rows.size | default: 0 | round %}}
{{% assign _total_pages = _total_items | minus: 1 | divided_by: 10 | floor | plus: 1 %}}
{{% if _total_pages < 1 %}}{{% assign _total_pages = 1 %}}{{% endif %}}
{{% if _total_pages > 25 %}}{{% assign _total_pages = 25 %}}{{% endif %}}
<div class="cb-paged-wrap cb-paged-wrap-synmon">
  <input type="radio" name="synmon_flt" id="synmon_flt_all" class="cb-pg-radio-synmon" checked>
  <input type="radio" name="synmon_flt" id="synmon_flt_http" class="cb-pg-radio-synmon">
  <input type="radio" name="synmon_flt" id="synmon_flt_browser" class="cb-pg-radio-synmon">
  <input type="radio" name="synmon_flt" id="synmon_flt_dns" class="cb-pg-radio-synmon">
  <input type="radio" name="synmon_flt" id="synmon_flt_tcp" class="cb-pg-radio-synmon">
  <input type="radio" name="synmon_flt" id="synmon_flt_icmp" class="cb-pg-radio-synmon">
{{% for _p_i in (1.._total_pages) %}}
  <input type="radio" name="synmon_pg" id="synmon_pg_{{{{ _p_i }}}}" class="cb-pg-radio-synmon" {{% if _p_i == 1 %}}checked{{% endif %}}>
{{% endfor %}}
<div class="cb-flt-bar cb-flt-bar-synmon">
  <span class="cb-flt-lbl">Synthetic Type:</span>
  <label for="synmon_flt_all" class="cb-pg-btn cb-flt-btn-synmon-all">All ({{{{ _total_items }}}})</label>
  <label for="synmon_flt_http" class="cb-pg-btn cb-flt-btn-synmon-http">http</label>
  <label for="synmon_flt_browser" class="cb-pg-btn cb-flt-btn-synmon-browser">browser</label>
  <label for="synmon_flt_dns" class="cb-pg-btn cb-flt-btn-synmon-dns">dns</label>
  <label for="synmon_flt_tcp" class="cb-pg-btn cb-flt-btn-synmon-tcp">tcp</label>
  <label for="synmon_flt_icmp" class="cb-pg-btn cb-flt-btn-synmon-icmp">icmp</label>
</div>
<div class="cb-content-wrap cb-content-wrap-synmon">
<table class="cb-t">
  <thead>
    <tr><th>Monitor Name</th><th>Monitor ID</th><th>Synthetic Type</th><th>Project</th><th>Namespace</th><th class="num">Locations</th><th class="num">Check Runs</th><th class="num">Availability</th><th class="num">Avg Duration</th><th>Cost Share</th><th class="num">Attributed Spend ({{{{ unit }}}})</th></tr>
  </thead>
  <tbody>
  {{% if _total_items == 0 %}}
    <tr><td colspan="11" class="cb-empty">No synthetics monitor telemetry in the selected time range.</td></tr>
  {{% else %}}
  {{% for row in rows %}}
    {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
    {{% assign mtype = row["monitor_type"].value | default: "http" | downcase %}}
    {{% assign cost = row["cost"].value | default: 0.0 %}}
    {{% assign checks = row["checks"].value | default: 0 %}}
    {{% assign avail = row["availability_pct"].value | default: 100.0 | round: 2 %}}
    {{% assign dur = row["avg_duration_ms"].value | default: 0.0 | round: 1 %}}
    {{% assign locs = row["locations"].value | default: 1 %}}
    {{% assign bar_pct = cost | times: 100.0 | divided_by: max_cost | round: 1 %}}
    {{% assign share_pct = cost | times: 100.0 | divided_by: denom_tot | round: 1 %}}
    {{% if mtype == "http" %}}{{% assign _bcol = "#3b82f6" %}}
    {{% elsif mtype == "browser" %}}{{% assign _bcol = "#a855f7" %}}
    {{% elsif mtype == "icmp" %}}{{% assign _bcol = "#10b981" %}}
    {{% elsif mtype == "tcp" %}}{{% assign _bcol = "#f59e0b" %}}
    {{% elsif mtype == "dns" %}}{{% assign _bcol = "#06b6d4" %}}
    {{% else %}}{{% assign _bcol = "var(--cc-vis-0)" %}}{{% endif %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
{liquid_money_block("checks", "fmt_checks", with_decimals=False)}
    <tr class="cb-pg-row-synmon cb-pg-synmon-{{{{ _row_pg }}}} cb-mt-{{{{ mtype }}}}">
      <td><strong>{{{{ row["monitor_name"].value }}}}</strong></td>
      <td><code>{{{{ row["monitor_id"].value }}}}</code></td>
      <td><span class="cb-st-pill cb-st-{{{{ mtype }}}}">{{{{ mtype }}}}</span></td>
      <td>{{{{ row["project"].value }}}}</td>
      <td>{{{{ row["monitor_namespace"].value }}}}</td>
      <td class="num">{{{{ locs }}}}</td>
      <td class="num">{{{{ fmt_checks }}}}</td>
      <td class="num">{{{{ avail }}}}%</td>
      <td class="num">{{{{ dur }}}} ms</td>
      <td>
        <div class="cb-share-wrap">
          <div class="cb-bar-track"><div class="cb-bar-fill" style="width: {{{{ bar_pct }}}}%; background: {{{{ _bcol }}}};"></div></div>
          <span class="cb-share-pct">{{{{ share_pct }}}}%</span>
        </div>
      </td>
      <td class="num"><strong><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost }}}}</span></strong></td>
    </tr>
  {{% endfor %}}
  {{% endif %}}
  </tbody>
</table>
{paged_bottom("synmon")}
"""


SYNTHETICS_DS_TABLE_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("synds", 20, "table-row")}
  table.cb-t {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; color: var(--cc-color-text); }}
  table.cb-t th {{
    position: sticky; top: 0; background: var(--cc-color-surface); z-index: 1;
    text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--cc-color-border);
    font-weight: 600; opacity: 0.75; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em;
    color: var(--cc-color-text);
  }}
  table.cb-t td {{ padding: 6px 8px; border-bottom: 1px solid var(--cc-color-border); color: var(--cc-color-text); }}
  table.cb-t th.num, table.cb-t td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
  .cb-st-pill {{
    display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 0.72rem;
    font-weight: 600; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    text-transform: uppercase; letter-spacing: 0.03em;
    border: 1px solid rgba(128, 128, 128, 0.25); background: rgba(128, 128, 128, 0.12);
  }}
  .cb-st-http {{ background: rgba(37, 99, 235, 0.14); color: #3b82f6; border-color: rgba(37, 99, 235, 0.35); }}
  .cb-st-browser {{ background: rgba(147, 51, 234, 0.14); color: #a855f7; border-color: rgba(147, 51, 234, 0.35); }}
  .cb-st-icmp {{ background: rgba(16, 185, 129, 0.14); color: #10b981; border-color: rgba(16, 185, 129, 0.35); }}
  .cb-st-tcp {{ background: rgba(245, 158, 11, 0.14); color: #f59e0b; border-color: rgba(245, 158, 11, 0.35); }}
  .cb-st-dns {{ background: rgba(6, 182, 212, 0.14); color: #06b6d4; border-color: rgba(6, 182, 212, 0.35); }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "USD" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: "$" %}}
{paged_top("synds", 10, 20)}
<table class="cb-t">
  <thead>
    <tr><th>Project</th><th>Project Type</th><th>Synthetic Type</th><th>Namespace</th><th>Data Stream</th><th class="num">Indexing ({{{{ sym }}}})</th><th class="num">Search ({{{{ sym }}}})</th><th class="num">Storage ({{{{ sym }}}})</th><th class="num">Attributed Cost ({{{{ unit }}}})</th></tr>
  </thead>
  <tbody>
  {{% if _total_items == 0 %}}
    <tr><td colspan="9" class="cb-empty">No synthetics-* data stream usage attribution data in the selected time range.</td></tr>
  {{% else %}}
  {{% for row in rows %}}
    {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
    {{% assign st = row["synth_type"].value | default: "http" | downcase %}}
    {{% assign st_base = st | split: "." | first %}}
    {{% assign idx = row["indexing_cost"].value | default: 0.0 %}}
    {{% assign qry = row["query_cost"].value | default: 0.0 %}}
    {{% assign stg = row["storage_cost"].value | default: 0.0 %}}
    {{% assign total = row["attributed_cost"].value | default: 0.0 %}}
{liquid_money_block("idx", "fmt_idx", with_decimals=True)}
{liquid_money_block("qry", "fmt_qry", with_decimals=True)}
{liquid_money_block("stg", "fmt_stg", with_decimals=True)}
{liquid_money_block("total", "fmt_total", with_decimals=True)}
    <tr class="cb-pg-row-synds cb-pg-synds-{{{{ _row_pg }}}}">
      <td><strong>{{{{ row["project"].value }}}}</strong></td>
      <td>{{{{ row["project_type"].value }}}}</td>
      <td><span class="cb-st-pill cb-st-{{{{ st_base }}}}">{{{{ st }}}}</span></td>
      <td>{{{{ row["ds_namespace"].value }}}}</td>
      <td><code>{{{{ row["datastream"].value }}}}</code></td>
      <td class="num"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_idx }}}}</span></td>
      <td class="num"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_qry }}}}</span></td>
      <td class="num"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_stg }}}}</span></td>
      <td class="num"><strong><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_total }}}}</span></strong></td>
    </tr>
  {{% endfor %}}
  {{% endif %}}
  </tbody>
</table>
{paged_bottom("synds")}
"""


def make_bar_template(
    label_key: str,
    label_header: str,
    prefix: str = "",
) -> str:
    pfx = f"bar_{prefix or label_key}"
    if label_key == "category":
        bar_color_logic = """\
  {% assign _lbl = row["category"].value | default: "other" %}
  {% if _lbl == "data_tier" %}{% assign _bcol = "var(--cc-vis-0)" %}
  {% elsif _lbl == "platform" %}{% assign _bcol = "var(--cc-vis-1)" %}
  {% elsif _lbl == "transfer" %}{% assign _bcol = "var(--cc-vis-2)" %}
  {% elsif _lbl == "snapshot" %}{% assign _bcol = "var(--cc-vis-3)" %}
  {% elsif _lbl == "support" %}{% assign _bcol = "var(--cc-vis-4)" %}
  {% else %}{% assign _bcol = "var(--cc-vis-5)" %}{% endif %}"""
    else:
        bar_color_logic = '  {% assign _bcol = "var(--cc-vis-0)" %}'
    return f"""\
<style>
{PAGED_BASE_CSS}
{paged_css(pfx, 8, "grid")}
  .cb-bars {{ display: flex; flex-direction: column; gap: 6px; padding: 4px 6px; }}
  .cb-bar-head {{
    display: grid;
    grid-template-columns: minmax(110px, 1.4fr) 2fr minmax(90px, 1fr);
    gap: var(--cc-space-s, 8px);
    font-size: 0.78rem; font-weight: 600; opacity: 0.75;
    text-transform: uppercase; letter-spacing: 0.03em;
    padding-bottom: 4px; border-bottom: 1px solid var(--cc-color-border);
    color: var(--cc-color-text);
  }}
  .cb-bar-head .num {{ text-align: right; }}
  .cb-bar-row {{
    display: grid;
    grid-template-columns: minmax(110px, 1.4fr) 2fr minmax(90px, 1fr);
    gap: var(--cc-space-s, 8px);
    align-items: center; font-size: 0.85rem; padding: 2px 0;
    color: var(--cc-color-text);
  }}
  .cb-bar-label {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; color: var(--cc-color-text); display: flex; align-items: center; gap: 6px; }}
  .cb-inact-pill {{
    display: inline-block; padding: 1px 5px; border-radius: 4px; font-size: 0.66rem;
    font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; flex-shrink: 0;
    background: color-mix(in srgb, var(--cc-color-warning, #f59f00) 16%, transparent);
    color: var(--cc-color-warning, #f59f00); border: 1px solid color-mix(in srgb, var(--cc-color-warning, #f59f00) 35%, transparent);
  }}
  .cb-share-wrap {{ display: flex; align-items: center; gap: 6px; }}
  .cb-bar-track {{
    flex: 1; height: 12px; background: var(--cc-color-border);
    border-radius: var(--cc-radius-s, 4px); overflow: hidden;
  }}
  .cb-bar-fill {{ height: 100%; background: var(--cc-vis-0); min-width: 2px; }}
  .cb-share-pct {{ width: 42px; text-align: right; font-variant-numeric: tabular-nums; font-size: 0.76rem; opacity: 0.78; color: var(--cc-color-text); flex-shrink: 0; }}
  .cb-bar-val {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; color: var(--cc-color-text); }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "USD" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: "$" %}}
{{% assign max_cost = rows[0]["cost"].value | default: 1.0 %}}
{{% if max_cost <= 0 %}}{{% assign max_cost = 1.0 %}}{{% endif %}}
{{% assign tot_cost = 0.0 %}}
{{% for r in rows %}}
  {{% assign rc = r["cost"].value | default: 0.0 %}}
  {{% assign tot_cost = tot_cost | plus: rc %}}
{{% endfor %}}
{{% assign denom_tot = tot_cost %}}
{{% if denom_tot <= 0 %}}{{% assign denom_tot = 1.0 %}}{{% endif %}}
{paged_top(pfx, 10, 8)}
<div class="cb-bars">
  <div class="cb-bar-head">
    <div>{label_header}</div>
    <div>Share</div>
    <div class="num">Cost ({{{{ unit }}}})</div>
  </div>
{{% if _total_items == 0 %}}
  <div class="cb-empty">No data in the selected time range.</div>
{{% else %}}
{{% for row in rows %}}
  {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
  {{% assign cost = row["cost"].value | default: 0.0 %}}
  {{% assign bar_pct = cost | times: 100.0 | divided_by: max_cost | round: 1 %}}
  {{% assign share_pct = cost | times: 100.0 | divided_by: denom_tot | round: 1 %}}
{bar_color_logic}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
  <div class="cb-bar-row cb-pg-row-{pfx} cb-pg-{pfx}-{{{{ _row_pg }}}}">
    <div class="cb-bar-label" title="{{{{ row["{label_key}"].value }}}}"><span>{{{{ row["{label_key}"].value }}}}</span>{{% if row["is_inactive"] and row["is_inactive"].value == 1 %}}<span class="cb-inact-pill">inactive</span>{{% endif %}}</div>
    <div class="cb-share-wrap">
      <div class="cb-bar-track"><div class="cb-bar-fill" style="width: {{{{ bar_pct }}}}%; background: {{{{ _bcol }}}};"></div></div>
      <span class="cb-share-pct">{{{{ share_pct }}}}%</span>
    </div>
    <div class="cb-bar-val"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost }}}}</span></div>
  </div>
{{% endfor %}}
{{% endif %}}
</div>
{paged_bottom(pfx)}
"""


UNALLOC_TABLE_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("unalloc", 10, "table-row")}
  table.cb-t {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; color: var(--cc-color-text); }}
  table.cb-t th {{
    position: sticky; top: 0; background: var(--cc-color-surface); z-index: 1;
    text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--cc-color-border);
    font-weight: 600; opacity: 0.75; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em;
    color: var(--cc-color-text);
  }}
  table.cb-t td {{ padding: 6px 8px; border-bottom: 1px solid var(--cc-color-border); color: var(--cc-color-text); }}
  table.cb-t th.num, table.cb-t td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "USD" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: "$" %}}
{paged_top("unalloc", 10, 10)}
<table class="cb-t">
  <thead>
    <tr><th>Category</th><th>Cost Type</th><th>SKU</th><th>Project</th><th class="num">Units (ECU)</th><th class="num">Cost ({{{{ unit }}}})</th></tr>
  </thead>
  <tbody>
  {{% if _total_items == 0 %}}
    <tr><td colspan="6" class="cb-empty">No unallocatable or pass-through charges in the selected time range.</td></tr>
  {{% else %}}
  {{% for row in rows %}}
    {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
    {{% assign cost = row["cost"].value | default: 0.0 %}}
    {{% assign ecu = row["ecu"].value | default: 0.0 %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
{liquid_money_block("ecu", "fmt_ecu", with_decimals=True)}
    <tr class="cb-pg-row-unalloc cb-pg-unalloc-{{{{ _row_pg }}}}">
      <td><strong>{{{{ row["category"].value }}}}</strong></td>
      <td>{{{{ row["cost_type"].value }}}}</td>
      <td><code>{{{{ row["sku"].value }}}}</code></td>
      <td>{{{{ row["project"].value }}}}</td>
      <td class="num">{{{{ fmt_ecu }}}}</td>
      <td class="num"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost }}}}</span></td>
    </tr>
  {{% endfor %}}
  {{% endif %}}
  </tbody>
</table>
{paged_bottom("unalloc")}
"""


INVOICE_TABLE_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("inv", 25, "table-row")}
  table.cb-t {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; color: var(--cc-color-text); }}
  table.cb-t th {{
    position: sticky; top: 0; background: var(--cc-color-surface); z-index: 1;
    text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--cc-color-border);
    font-weight: 600; opacity: 0.75; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em;
    color: var(--cc-color-text);
  }}
  table.cb-t td {{ padding: 6px 8px; border-bottom: 1px solid var(--cc-color-border); color: var(--cc-color-text); }}
  table.cb-t th.num, table.cb-t td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
  .cb-inact-pill {{
    display: inline-block; padding: 1px 5px; border-radius: 4px; font-size: 0.66rem;
    font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; margin-left: 6px;
    background: color-mix(in srgb, var(--cc-color-warning, #f59f00) 16%, transparent);
    color: var(--cc-color-warning, #f59f00); border: 1px solid color-mix(in srgb, var(--cc-color-warning, #f59f00) 35%, transparent);
  }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "USD" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: "$" %}}
{paged_top("inv", 10, 25)}
<table class="cb-t">
  <thead>
    <tr><th>Project</th><th>Type</th><th>Category</th><th>Cost Type</th><th>SKU</th><th>Driver</th><th class="num">Quantity</th><th class="num">Units (ECU)</th><th class="num">Cost ({{{{ unit }}}})</th></tr>
  </thead>
  <tbody>
  {{% if _total_items == 0 %}}
    <tr><td colspan="9" class="cb-empty">No invoice line items in the selected time range.</td></tr>
  {{% else %}}
  {{% for row in rows %}}
    {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
    {{% assign cost = row["cost"].value | default: 0.0 %}}
    {{% assign ecu = row["ecu"].value | default: 0.0 %}}
    {{% assign qty = row["qty"].value | default: 0.0 %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
{liquid_money_block("ecu", "fmt_ecu", with_decimals=True)}
{liquid_money_block("qty", "fmt_qty", with_decimals=True)}
    <tr class="cb-pg-row-inv cb-pg-inv-{{{{ _row_pg }}}}">
      <td><strong>{{{{ row["project"].value }}}}</strong>{{% if row["is_inactive"] and row["is_inactive"].value == 1 %}}<span class="cb-inact-pill">inactive</span>{{% endif %}}</td>
      <td>{{{{ row["project_type"].value }}}}</td>
      <td>{{{{ row["category"].value }}}}</td>
      <td>{{{{ row["cost_type"].value }}}}</td>
      <td><code>{{{{ row["sku"].value }}}}</code></td>
      <td>{{{{ row["driver"].value }}}}</td>
      <td class="num">{{{{ fmt_qty }}}}</td>
      <td class="num">{{{{ fmt_ecu }}}}</td>
      <td class="num"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost }}}}</span></td>
    </tr>
  {{% endfor %}}
  {{% endif %}}
  </tbody>
</table>
{paged_bottom("inv")}
"""


DATASTREAM_COST_TABLE_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("dscost", 25, "table-row")}
  .cb-flt-bar-dscost {{ display: flex; align-items: center; gap: 5px; padding: 2px 2px 6px 2px; font-size: 0.75rem; color: var(--cc-color-text); flex-shrink: 0; flex-wrap: wrap; }}
  .cb-flt-lbl {{ opacity: 0.72; font-weight: 600; font-size: 0.73rem; text-transform: uppercase; letter-spacing: 0.03em; margin-right: 2px; }}
  #dscost_flt_all:checked ~ .cb-flt-bar-dscost .cb-flt-btn-dscost-all {{ background: var(--cc-color-primary); color: #ffffff !important; border-color: var(--cc-color-primary); font-weight: 700; }}
  #dscost_flt_logs:checked ~ .cb-flt-bar-dscost .cb-flt-btn-dscost-logs,
  #dscost_flt_metrics:checked ~ .cb-flt-bar-dscost .cb-flt-btn-dscost-metrics,
  #dscost_flt_traces:checked ~ .cb-flt-bar-dscost .cb-flt-btn-dscost-traces,
  #dscost_flt_synthetics:checked ~ .cb-flt-bar-dscost .cb-flt-btn-dscost-synthetics,
  #dscost_flt_other:checked ~ .cb-flt-bar-dscost .cb-flt-btn-dscost-other {{ background: var(--cc-color-primary); color: #ffffff !important; border-color: var(--cc-color-primary); font-weight: 700; }}
  #dscost_flt_logs:checked ~ .cb-content-wrap-dscost,
  #dscost_flt_metrics:checked ~ .cb-content-wrap-dscost,
  #dscost_flt_traces:checked ~ .cb-content-wrap-dscost,
  #dscost_flt_synthetics:checked ~ .cb-content-wrap-dscost,
  #dscost_flt_other:checked ~ .cb-content-wrap-dscost {{ overflow-y: auto !important; }}
  #dscost_flt_logs:checked ~ .cb-content-wrap-dscost .cb-pg-row-dscost,
  #dscost_flt_metrics:checked ~ .cb-content-wrap-dscost .cb-pg-row-dscost,
  #dscost_flt_traces:checked ~ .cb-content-wrap-dscost .cb-pg-row-dscost,
  #dscost_flt_synthetics:checked ~ .cb-content-wrap-dscost .cb-pg-row-dscost,
  #dscost_flt_other:checked ~ .cb-content-wrap-dscost .cb-pg-row-dscost {{ display: none !important; }}
  #dscost_flt_logs:checked ~ .cb-content-wrap-dscost .cb-pg-row-dscost.cb-dst-logs,
  #dscost_flt_metrics:checked ~ .cb-content-wrap-dscost .cb-pg-row-dscost.cb-dst-metrics,
  #dscost_flt_traces:checked ~ .cb-content-wrap-dscost .cb-pg-row-dscost.cb-dst-traces,
  #dscost_flt_synthetics:checked ~ .cb-content-wrap-dscost .cb-pg-row-dscost.cb-dst-synthetics,
  #dscost_flt_other:checked ~ .cb-content-wrap-dscost .cb-pg-row-dscost.cb-dst-other {{ display: table-row !important; }}
  #dscost_flt_logs:checked ~ .cb-pg-bar-dscost,
  #dscost_flt_metrics:checked ~ .cb-pg-bar-dscost,
  #dscost_flt_traces:checked ~ .cb-pg-bar-dscost,
  #dscost_flt_synthetics:checked ~ .cb-pg-bar-dscost,
  #dscost_flt_other:checked ~ .cb-pg-bar-dscost {{ display: none !important; }}
  table.cb-t {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; color: var(--cc-color-text); }}
  table.cb-t th {{
    position: sticky; top: 0; background: var(--cc-color-surface); z-index: 1;
    text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--cc-color-border);
    font-weight: 600; opacity: 0.75; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em;
    color: var(--cc-color-text);
  }}
  table.cb-t td {{ padding: 6px 8px; border-bottom: 1px solid var(--cc-color-border); color: var(--cc-color-text); }}
  table.cb-t th.num, table.cb-t td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "USD" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: "$" %}}
{{% assign _total_items = rows.size | default: 0 | round %}}
{{% assign _total_pages = _total_items | minus: 1 | divided_by: 10 | floor | plus: 1 %}}
{{% if _total_pages < 1 %}}{{% assign _total_pages = 1 %}}{{% endif %}}
{{% if _total_pages > 25 %}}{{% assign _total_pages = 25 %}}{{% endif %}}
<div class="cb-paged-wrap cb-paged-wrap-dscost">
  <input type="radio" name="dscost_flt" id="dscost_flt_all" class="cb-pg-radio-dscost" checked>
  <input type="radio" name="dscost_flt" id="dscost_flt_logs" class="cb-pg-radio-dscost">
  <input type="radio" name="dscost_flt" id="dscost_flt_metrics" class="cb-pg-radio-dscost">
  <input type="radio" name="dscost_flt" id="dscost_flt_traces" class="cb-pg-radio-dscost">
  <input type="radio" name="dscost_flt" id="dscost_flt_synthetics" class="cb-pg-radio-dscost">
  <input type="radio" name="dscost_flt" id="dscost_flt_other" class="cb-pg-radio-dscost">
{{% for _p_i in (1.._total_pages) %}}
  <input type="radio" name="dscost_pg" id="dscost_pg_{{{{ _p_i }}}}" class="cb-pg-radio-dscost" {{% if _p_i == 1 %}}checked{{% endif %}}>
{{% endfor %}}
<div class="cb-flt-bar cb-flt-bar-dscost">
  <span class="cb-flt-lbl">Type:</span>
  <label for="dscost_flt_all" class="cb-pg-btn cb-flt-btn-dscost-all">All ({{{{ _total_items }}}})</label>
  <label for="dscost_flt_logs" class="cb-pg-btn cb-flt-btn-dscost-logs">logs</label>
  <label for="dscost_flt_metrics" class="cb-pg-btn cb-flt-btn-dscost-metrics">metrics</label>
  <label for="dscost_flt_traces" class="cb-pg-btn cb-flt-btn-dscost-traces">traces</label>
  <label for="dscost_flt_synthetics" class="cb-pg-btn cb-flt-btn-dscost-synthetics">synthetics</label>
  <label for="dscost_flt_other" class="cb-pg-btn cb-flt-btn-dscost-other">other</label>
</div>
<div class="cb-content-wrap cb-content-wrap-dscost">
<table class="cb-t">
  <thead>
    <tr><th>Project</th><th>Project Type</th><th>Type</th><th>Namespace</th><th>Data Stream</th><th class="num">Indexing ({{{{ sym }}}})</th><th class="num">Search ({{{{ sym }}}})</th><th class="num">Storage ({{{{ sym }}}})</th><th class="num">Attributed Cost ({{{{ unit }}}})</th></tr>
  </thead>
  <tbody>
  {{% if _total_items == 0 %}}
    <tr><td colspan="9" class="cb-empty">No data stream usage attribution data in the selected time range.</td></tr>
  {{% else %}}
  {{% for row in rows %}}
    {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
    {{% assign _dst = row["ds_type"].value | default: "other" | downcase %}}
    {{% if _dst != "logs" and _dst != "metrics" and _dst != "traces" and _dst != "synthetics" %}}{{% assign _dst = "other" %}}{{% endif %}}
    {{% assign idx = row["indexing_cost"].value | default: 0.0 %}}
    {{% assign qry = row["query_cost"].value | default: 0.0 %}}
    {{% assign stg = row["storage_cost"].value | default: 0.0 %}}
    {{% assign total = row["attributed_cost"].value | default: 0.0 %}}
{liquid_money_block("idx", "fmt_idx", with_decimals=True)}
{liquid_money_block("qry", "fmt_qry", with_decimals=True)}
{liquid_money_block("stg", "fmt_stg", with_decimals=True)}
{liquid_money_block("total", "fmt_total", with_decimals=True)}
    <tr class="cb-pg-row-dscost cb-pg-dscost-{{{{ _row_pg }}}} cb-dst-{{{{ _dst }}}}">
      <td><strong>{{{{ row["project"].value }}}}</strong></td>
      <td>{{{{ row["project_type"].value }}}}</td>
      <td>{{{{ row["ds_type"].value }}}}</td>
      <td>{{{{ row["ds_namespace"].value }}}}</td>
      <td><code>{{{{ row["datastream"].value }}}}</code></td>
      <td class="num"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_idx }}}}</span></td>
      <td class="num"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_qry }}}}</span></td>
      <td class="num"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_stg }}}}</span></td>
      <td class="num"><strong><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_total }}}}</span></strong></td>
    </tr>
  {{% endfor %}}
  {{% endif %}}
  </tbody>
</table>
{paged_bottom("dscost")}
"""


DATASTREAM_METRICS_TABLE_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("dsmet", 25, "table-row")}
  .cb-flt-bar-dsmet {{ display: flex; align-items: center; gap: 5px; padding: 2px 2px 6px 2px; font-size: 0.75rem; color: var(--cc-color-text); flex-shrink: 0; flex-wrap: wrap; }}
  .cb-flt-lbl {{ opacity: 0.72; font-weight: 600; font-size: 0.73rem; text-transform: uppercase; letter-spacing: 0.03em; margin-right: 2px; }}
  #dsmet_flt_all:checked ~ .cb-flt-bar-dsmet .cb-flt-btn-dsmet-all {{ background: var(--cc-color-primary); color: #ffffff !important; border-color: var(--cc-color-primary); font-weight: 700; }}
  #dsmet_flt_logs:checked ~ .cb-flt-bar-dsmet .cb-flt-btn-dsmet-logs,
  #dsmet_flt_metrics:checked ~ .cb-flt-bar-dsmet .cb-flt-btn-dsmet-metrics,
  #dsmet_flt_traces:checked ~ .cb-flt-bar-dsmet .cb-flt-btn-dsmet-traces,
  #dsmet_flt_synthetics:checked ~ .cb-flt-bar-dsmet .cb-flt-btn-dsmet-synthetics,
  #dsmet_flt_other:checked ~ .cb-flt-bar-dsmet .cb-flt-btn-dsmet-other {{ background: var(--cc-color-primary); color: #ffffff !important; border-color: var(--cc-color-primary); font-weight: 700; }}
  #dsmet_flt_logs:checked ~ .cb-content-wrap-dsmet,
  #dsmet_flt_metrics:checked ~ .cb-content-wrap-dsmet,
  #dsmet_flt_traces:checked ~ .cb-content-wrap-dsmet,
  #dsmet_flt_synthetics:checked ~ .cb-content-wrap-dsmet,
  #dsmet_flt_other:checked ~ .cb-content-wrap-dsmet {{ overflow-y: auto !important; }}
  #dsmet_flt_logs:checked ~ .cb-content-wrap-dsmet .cb-pg-row-dsmet,
  #dsmet_flt_metrics:checked ~ .cb-content-wrap-dsmet .cb-pg-row-dsmet,
  #dsmet_flt_traces:checked ~ .cb-content-wrap-dsmet .cb-pg-row-dsmet,
  #dsmet_flt_synthetics:checked ~ .cb-content-wrap-dsmet .cb-pg-row-dsmet,
  #dsmet_flt_other:checked ~ .cb-content-wrap-dsmet .cb-pg-row-dsmet {{ display: none !important; }}
  #dsmet_flt_logs:checked ~ .cb-content-wrap-dsmet .cb-pg-row-dsmet.cb-dst-logs,
  #dsmet_flt_metrics:checked ~ .cb-content-wrap-dsmet .cb-pg-row-dsmet.cb-dst-metrics,
  #dsmet_flt_traces:checked ~ .cb-content-wrap-dsmet .cb-pg-row-dsmet.cb-dst-traces,
  #dsmet_flt_synthetics:checked ~ .cb-content-wrap-dsmet .cb-pg-row-dsmet.cb-dst-synthetics,
  #dsmet_flt_other:checked ~ .cb-content-wrap-dsmet .cb-pg-row-dsmet.cb-dst-other {{ display: table-row !important; }}
  #dsmet_flt_logs:checked ~ .cb-pg-bar-dsmet,
  #dsmet_flt_metrics:checked ~ .cb-pg-bar-dsmet,
  #dsmet_flt_traces:checked ~ .cb-pg-bar-dsmet,
  #dsmet_flt_synthetics:checked ~ .cb-pg-bar-dsmet,
  #dsmet_flt_other:checked ~ .cb-pg-bar-dsmet {{ display: none !important; }}
  table.cb-t {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; color: var(--cc-color-text); }}
  table.cb-t th {{
    position: sticky; top: 0; background: var(--cc-color-surface); z-index: 1;
    text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--cc-color-border);
    font-weight: 600; opacity: 0.75; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em;
    color: var(--cc-color-text);
  }}
  table.cb-t td {{ padding: 6px 8px; border-bottom: 1px solid var(--cc-color-border); color: var(--cc-color-text); }}
  table.cb-t th.num, table.cb-t td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
</style>
{{% assign _total_items = rows.size | default: 0 | round %}}
{{% assign _total_pages = _total_items | minus: 1 | divided_by: 10 | floor | plus: 1 %}}
{{% if _total_pages < 1 %}}{{% assign _total_pages = 1 %}}{{% endif %}}
{{% if _total_pages > 25 %}}{{% assign _total_pages = 25 %}}{{% endif %}}
<div class="cb-paged-wrap cb-paged-wrap-dsmet">
  <input type="radio" name="dsmet_flt" id="dsmet_flt_all" class="cb-pg-radio-dsmet" checked>
  <input type="radio" name="dsmet_flt" id="dsmet_flt_logs" class="cb-pg-radio-dsmet">
  <input type="radio" name="dsmet_flt" id="dsmet_flt_metrics" class="cb-pg-radio-dsmet">
  <input type="radio" name="dsmet_flt" id="dsmet_flt_traces" class="cb-pg-radio-dsmet">
  <input type="radio" name="dsmet_flt" id="dsmet_flt_synthetics" class="cb-pg-radio-dsmet">
  <input type="radio" name="dsmet_flt" id="dsmet_flt_other" class="cb-pg-radio-dsmet">
{{% for _p_i in (1.._total_pages) %}}
  <input type="radio" name="dsmet_pg" id="dsmet_pg_{{{{ _p_i }}}}" class="cb-pg-radio-dsmet" {{% if _p_i == 1 %}}checked{{% endif %}}>
{{% endfor %}}
<div class="cb-flt-bar cb-flt-bar-dsmet">
  <span class="cb-flt-lbl">Type:</span>
  <label for="dsmet_flt_all" class="cb-pg-btn cb-flt-btn-dsmet-all">All ({{{{ _total_items }}}})</label>
  <label for="dsmet_flt_logs" class="cb-pg-btn cb-flt-btn-dsmet-logs">logs</label>
  <label for="dsmet_flt_metrics" class="cb-pg-btn cb-flt-btn-dsmet-metrics">metrics</label>
  <label for="dsmet_flt_traces" class="cb-pg-btn cb-flt-btn-dsmet-traces">traces</label>
  <label for="dsmet_flt_synthetics" class="cb-pg-btn cb-flt-btn-dsmet-synthetics">synthetics</label>
  <label for="dsmet_flt_other" class="cb-pg-btn cb-flt-btn-dsmet-other">other</label>
</div>
<div class="cb-content-wrap cb-content-wrap-dsmet">
<table class="cb-t">
  <thead>
    <tr><th>Project</th><th>Project Type</th><th>Type</th><th>Namespace</th><th>Data Stream</th><th class="num">Indexing Time (ms)</th><th class="num">Search Query Time (ms)</th><th class="num">Avg Dataset Size (MB)</th></tr>
  </thead>
  <tbody>
  {{% if _total_items == 0 %}}
    <tr><td colspan="8" class="cb-empty">No harvested data stream usage metrics in the selected time range.</td></tr>
  {{% else %}}
  {{% for row in rows %}}
    {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
    {{% assign _dst = row["ds_type"].value | default: "other" | downcase %}}
    {{% if _dst != "logs" and _dst != "metrics" and _dst != "traces" and _dst != "synthetics" %}}{{% assign _dst = "other" %}}{{% endif %}}
    {{% assign idx_ms = row["indexing_ms"].value | default: 0 %}}
    {{% assign qry_ms = row["query_ms"].value | default: 0 %}}
    {{% assign sz_mb = row["size_mb"].value | default: 0.0 %}}
{liquid_money_block("idx_ms", "fmt_idx_ms", with_decimals=False)}
{liquid_money_block("qry_ms", "fmt_qry_ms", with_decimals=False)}
{liquid_money_block("sz_mb", "fmt_sz_mb", with_decimals=True)}
    <tr class="cb-pg-row-dsmet cb-pg-dsmet-{{{{ _row_pg }}}} cb-dst-{{{{ _dst }}}}">
      <td><strong>{{{{ row["project"].value }}}}</strong></td>
      <td>{{{{ row["project_type"].value }}}}</td>
      <td>{{{{ row["ds_type"].value }}}}</td>
      <td>{{{{ row["ds_namespace"].value }}}}</td>
      <td><code>{{{{ row["datastream"].value }}}}</code></td>
      <td class="num">{{{{ fmt_idx_ms }}}}</td>
      <td class="num">{{{{ fmt_qry_ms }}}}</td>
      <td class="num">{{{{ fmt_sz_mb }}}}</td>
    </tr>
  {{% endfor %}}
  {{% endif %}}
  </tbody>
</table>
{paged_bottom("dsmet")}
"""

CONFIG_UNIFIED_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("cfgreg", 20, "table-row")}
  .cb-cfg {{ position: relative; width: 100%; box-sizing: border-box; display: flex; flex-direction: column; gap: var(--cc-space-m, 12px); color: var(--cc-color-text); }}

  /* Top Banner */
  .cb-cfg-banner {{
    display: flex; align-items: center; justify-content: space-between; gap: var(--cc-space-m, 12px);
    padding: 10px 16px;
    background: color-mix(in srgb, var(--cc-color-primary, #0077cc) 6%, var(--cc-color-surface));
    border: 1px solid color-mix(in srgb, var(--cc-color-primary, #0077cc) 20%, var(--cc-color-border));
    border-radius: var(--cc-radius);
  }}
  .cb-cfg-banner-title {{ font-size: 1.02rem; font-weight: 700; color: var(--cc-color-text); display: flex; align-items: center; gap: 8px; line-height: 1.2; }}
  .cb-cfg-banner-sub {{ font-size: 0.76rem; opacity: 0.72; color: var(--cc-color-text); margin-top: 2px; }}
  .cb-cfg-banner-pills {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  .cb-cfg-banner-pill {{
    display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 20px;
    font-size: 0.73rem; font-weight: 600; background: var(--cc-color-surface);
    border: 1px solid var(--cc-color-border); white-space: nowrap; flex-shrink: 0;
  }}

  /* KPI Summary Strip */
  .cb-cfg-kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(165px, 1fr)); gap: var(--cc-space-s, 8px); }}
  .cb-cfg-kpi {{
    background: var(--cc-color-surface); border: 1px solid var(--cc-color-border);
    border-radius: var(--cc-radius); padding: 10px 14px 12px 14px;
    display: flex; flex-direction: column; justify-content: space-between;
  }}
  .cb-cfg-kpi-label {{ color: var(--cc-color-text); opacity: 0.72; font-size: 0.73rem; text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600; line-height: 1.1; }}
  .cb-cfg-kpi-val {{
    color: var(--cc-color-text); font-size: 1.3rem; font-weight: 700; margin-top: 4px; line-height: 1.15;
    font-variant-numeric: tabular-nums; display: flex; align-items: baseline; gap: 4px;
  }}
  .cb-cfg-kpi-sub {{ color: var(--cc-color-text); font-size: 0.71rem; opacity: 0.68; margin-top: 4px; line-height: 1.15; }}

  /* Section Title Bar */
  .cb-cfg-sec-bar {{
    display: flex; align-items: center; justify-content: space-between;
    padding-bottom: 4px; border-bottom: 1px solid var(--cc-color-border); margin-top: 4px;
  }}
  .cb-cfg-sec-title {{
    font-size: 0.84rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em;
    color: var(--cc-color-text); display: flex; align-items: center; gap: 8px;
  }}
  .cb-cfg-sec-badge {{
    background: color-mix(in srgb, var(--cc-color-primary, #0077cc) 12%, transparent);
    color: var(--cc-color-primary, #0077cc); font-size: 0.7rem; padding: 2px 7px; border-radius: 4px; font-weight: 600;
  }}

  /* Guide Grid */
  .cb-cfg-guide-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: var(--cc-space-s, 8px); }}
  .cb-cfg-card {{
    background: var(--cc-color-surface); border: 1px solid var(--cc-color-border);
    border-radius: var(--cc-radius); padding: 12px 14px; display: flex; flex-direction: column; gap: 8px;
  }}
  .cb-cfg-card-head {{ display: flex; align-items: center; gap: 8px; }}
  .cb-cfg-step-pill {{
    background: var(--cc-color-primary, #0077cc); color: #ffffff; font-size: 0.68rem; font-weight: 700;
    padding: 2px 7px; border-radius: 12px; text-transform: uppercase; letter-spacing: 0.04em; flex-shrink: 0;
  }}
  .cb-cfg-card-title {{ font-size: 0.86rem; font-weight: 600; color: var(--cc-color-text); }}
  .cb-cfg-card-desc {{ font-size: 0.75rem; opacity: 0.75; margin: 0; line-height: 1.35; color: var(--cc-color-text); }}
  .cb-cfg-code {{
    background: color-mix(in srgb, var(--cc-color-surface) 60%, var(--cc-color-border) 40%);
    border: 1px solid var(--cc-color-border); border-radius: var(--cc-radius-s);
    padding: 8px 10px; margin: 0; font-family: monospace; font-size: 0.71rem; line-height: 1.35;
    overflow-x: auto; color: var(--cc-color-text); flex: 1 1 auto;
  }}
  .cb-cfg-code-select {{ user-select: all; cursor: pointer; }}
  .cb-cfg-card-foot {{ font-size: 0.71rem; opacity: 0.72; display: flex; align-items: center; gap: 4px; }}

  /* Tables */
  .cb-paged-wrap-cfgreg {{ height: auto !important; min-height: 0; display: flex; flex-direction: column; }}
  .cb-content-wrap-cfgreg {{ overflow-x: auto; overflow-y: visible !important; flex: none !important; }}
  .cb-stream-wrap {{ overflow-x: auto; }}
  table.cb-cfg-table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; color: var(--cc-color-text); }}
  table.cb-cfg-table th {{
    position: sticky; top: 0; background: var(--cc-color-surface); z-index: 1; text-align: left;
    padding: 6px 8px; border-bottom: 2px solid var(--cc-color-border); font-weight: 600; opacity: 0.75;
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.03em; color: var(--cc-color-text); white-space: nowrap;
  }}
  table.cb-cfg-table td {{ padding: 6px 8px; border-bottom: 1px solid var(--cc-color-border); color: var(--cc-color-text); vertical-align: middle; }}
  table.cb-cfg-table tr:hover td {{ background: color-mix(in srgb, var(--cc-color-primary, #0077cc) 3%, transparent); }}

  .cb-proj-cell {{ display: flex; flex-direction: column; gap: 1px; }}
  .cb-proj-name {{ font-weight: 600; color: var(--cc-color-text); }}
  .cb-proj-id {{ font-family: monospace; font-size: 0.69rem; opacity: 0.65; }}
  .cb-sub-meta {{ font-size: 0.7rem; opacity: 0.72; margin-top: 2px; line-height: 1.2; }}

  .cb-type-pill {{
    display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 0.69rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.02em;
    background: color-mix(in srgb, var(--cc-color-border) 70%, transparent); color: var(--cc-color-text);
  }}
  .cb-key-code {{
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.71rem; font-weight: 600;
    padding: 1px 5px; border-radius: 4px;
    background: color-mix(in srgb, var(--cc-color-primary, #0077cc) 10%, var(--cc-color-surface));
    border: 1px solid color-mix(in srgb, var(--cc-color-primary, #0077cc) 25%, var(--cc-color-border));
    color: var(--cc-color-text); display: inline-block;
  }}
  .cb-prof-text {{ font-size: 0.7rem; opacity: 0.68; margin-top: 1px; }}
  .cb-endpoint-text {{
    font-family: monospace; font-size: 0.7rem; opacity: 0.82; max-width: 240px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: block;
  }}

  .cb-dot {{ width: 7px; height: 7px; border-radius: 50%; display: inline-block; margin-right: 5px; flex-shrink: 0; }}
  .cb-dot-ok {{ background: var(--cc-color-accent, var(--cc-vis-1, #00b474)); box-shadow: 0 0 5px var(--cc-color-accent, var(--cc-vis-1, #00b474)); }}
  .cb-dot-err {{ background: var(--cc-color-danger, #e03131); box-shadow: 0 0 5px var(--cc-color-danger, #e03131); }}
  .cb-dot-warn {{ background: var(--cc-color-warning, #f59f00); box-shadow: 0 0 5px var(--cc-color-warning, #f59f00); }}

  .cb-badge {{
    display: inline-flex; align-items: center; padding: 2px 7px; border-radius: 12px;
    font-size: 0.69rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; white-space: nowrap;
  }}
  .cb-badge-ok {{ background: color-mix(in srgb, var(--cc-color-accent, var(--cc-vis-1, #00b474)) 15%, transparent); color: var(--cc-color-accent, var(--cc-vis-1, #00b474)); }}
  .cb-badge-err {{ background: color-mix(in srgb, var(--cc-color-danger, #e03131) 15%, transparent); color: var(--cc-color-danger, #e03131); }}
  .cb-badge-warn {{ background: color-mix(in srgb, var(--cc-color-warning, #f59f00) 15%, transparent); color: var(--cc-color-warning, #f59f00); }}

  .cb-indices-badge {{ font-variant-numeric: tabular-nums; font-weight: 600; font-size: 0.76rem; }}
  .cb-time-col {{ font-variant-numeric: tabular-nums; font-size: 0.73rem; opacity: 0.78; white-space: nowrap; }}
</style>
{{% assign _total_projects = rows.size | default: 0 | round %}}
{{% assign _active_projects = 0 %}}
{{% assign _needs_key_projects = 0 %}}
{{% assign _inactive_projects = 0 %}}
{{% assign _stale_projects = 0 %}}
{{% assign _hub_key_projects = 0 %}}
{{% assign _remote_key_projects = 0 %}}
{{% assign _billed_projects = 0 %}}
{{% assign _synth_fallback_projects = 0 %}}
{{% assign _total_indices = 0 %}}
{{% assign _total_datastreams = 0 %}}
{{% assign _total_ai_models = 0 %}}
{{% assign _total_synth_monitors = 0 %}}
{{% assign _total_org_cost = 0.0 %}}
{{% assign _total_org_ecu = 0.0 %}}
{{% assign _max_window_days = 0 %}}
{{% assign _active_rate = 1.0 %}}
{{% assign _active_unit = "USD" %}}
{{% assign _active_sym = "$" %}}
{{% assign _latest_poll = "—" %}}
{{% assign _latest_bill_day = "—" %}}

{{% for r in rows %}}
  {{% assign h = r["health"].value | default: "stale" %}}
  {{% if h == "ok" %}}
    {{% assign _active_projects = _active_projects | plus: 1 %}}
  {{% elsif h == "empty_or_unauthorized" %}}
    {{% assign _needs_key_projects = _needs_key_projects | plus: 1 %}}
  {{% elsif h == "inactive" %}}
    {{% assign _inactive_projects = _inactive_projects | plus: 1 %}}
  {{% else %}}
    {{% assign _stale_projects = _stale_projects | plus: 1 %}}
  {{% endif %}}

  {{% assign kt = r["api_key_type"].value | default: "" %}}
  {{% if kt == "cloud_key" or kt == "hub_key" %}}
    {{% assign _hub_key_projects = _hub_key_projects | plus: 1 %}}
  {{% elsif kt == "remote_key" %}}
    {{% assign _remote_key_projects = _remote_key_projects | plus: 1 %}}
  {{% endif %}}

  {{% assign ss = r["synthetics_status"].value | default: "" %}}
  {{% if ss == "billing_derived" %}}
    {{% assign _synth_fallback_projects = _synth_fallback_projects | plus: 1 %}}
  {{% endif %}}

  {{% assign ic = r["index_count"].value | default: 0 | round %}}
  {{% assign dsc = r["datastream_count"].value | default: 0 | round %}}
  {{% assign aim = r["ai_model_count"].value | default: 0 | round %}}
  {{% assign symon = r["synthetics_monitor_count"].value | default: 0 | round %}}
  {{% assign _total_indices = _total_indices | plus: ic %}}
  {{% assign _total_datastreams = _total_datastreams | plus: dsc %}}
  {{% assign _total_ai_models = _total_ai_models | plus: aim %}}
  {{% assign _total_synth_monitors = _total_synth_monitors | plus: symon %}}

  {{% assign pc = r["total_cost"].value | default: 0.0 %}}
  {{% assign pe = r["total_ecu"].value | default: 0.0 %}}
  {{% assign bd = r["billed_days"].value | default: 0 | round %}}
  {{% assign wd = r["window_days"].value | default: 0 | round %}}
  {{% assign _total_org_cost = _total_org_cost | plus: pc %}}
  {{% assign _total_org_ecu = _total_org_ecu | plus: pe %}}
  {{% if bd > 0 %}}
    {{% assign _billed_projects = _billed_projects | plus: 1 %}}
  {{% endif %}}
  {{% if wd > _max_window_days %}}
    {{% assign _max_window_days = wd %}}
  {{% endif %}}

  {{% assign lbt = r["latest_bill_ts"].value | default: "" | slice: 0, 10 %}}
  {{% if lbt != "" and lbt != "null" %}}
    {{% if _latest_bill_day == "—" or lbt > _latest_bill_day %}}
      {{% assign _latest_bill_day = lbt %}}
    {{% endif %}}
  {{% endif %}}

  {{% if r["conf_chargeable_unit_rate"].value != null and r["conf_chargeable_unit_rate"].value != "" %}}
    {{% assign _active_rate = r["conf_chargeable_unit_rate"].value %}}
  {{% endif %}}
  {{% if r["conf_chargeable_unit_rate_unit"].value != null and r["conf_chargeable_unit_rate_unit"].value != "" %}}
    {{% assign _active_unit = r["conf_chargeable_unit_rate_unit"].value | upcase %}}
  {{% endif %}}
  {{% if r["conf_currency_symbol"].value != null and r["conf_currency_symbol"].value != "" %}}
    {{% assign _active_sym = r["conf_currency_symbol"].value %}}
  {{% endif %}}

  {{% assign upd = r["updated_at"].value | default: "" | slice: 0, 19 | replace: "T", " " %}}
  {{% if upd != "" and upd != "null" %}}
    {{% if _latest_poll == "—" or upd > _latest_poll %}}
      {{% assign _latest_poll = upd %}}
    {{% endif %}}
  {{% endif %}}
{{% endfor %}}
{{% assign _cp_active_projects = _total_projects | minus: _inactive_projects %}}
{{% if _cp_active_projects < 0 %}}{{% assign _cp_active_projects = 0 %}}{{% endif %}}
{liquid_money_block("_total_org_cost", "fmt_org_cost", with_decimals=True)}
{liquid_money_block("_total_org_ecu", "fmt_org_ecu", with_decimals=True)}

<div class="cb-cfg">
  <!-- Top Banner -->
  <div class="cb-cfg-banner">
    <div>
      <div class="cb-cfg-banner-title">
        <span>⚙️</span> Fleet Configuration, API Key Data Streams &amp; Serverless Workload Registry
      </div>
      <div class="cb-cfg-banner-sub">
        End-to-end health of Cloud Billing, Control Plane Discovery, and Data Plane API key streams (_cat/indices, AI &amp; ML, Synthetics)
      </div>
    </div>
    <div class="cb-cfg-banner-pills">
      <div class="cb-cfg-banner-pill">
        {{% if _billed_projects > 0 %}}
          <span class="cb-dot cb-dot-ok"></span> <span>Billing Stream: Active ({{{{ _billed_projects }}}}/{{{{ _total_projects }}}} Billed)</span>
        {{% else %}}
          <span class="cb-dot cb-dot-warn"></span> <span>Billing Stream: Awaiting Poll</span>
        {{% endif %}}
      </div>
      <div class="cb-cfg-banner-pill">
        {{% if _needs_key_projects > 0 %}}
          <span class="cb-dot cb-dot-err"></span> <span>Usage Streams: {{{{ _active_projects }}}}/{{{{ _cp_active_projects }}}} Active &middot; {{{{ _needs_key_projects }}}} Need Remote Key</span>
        {{% elsif _total_projects > 0 %}}
          <span class="cb-dot cb-dot-ok"></span> <span>Usage Streams: {{{{ _active_projects }}}}/{{{{ _cp_active_projects }}}} Active</span>
        {{% else %}}
          <span class="cb-dot cb-dot-warn"></span> <span>Awaiting First Discovery</span>
        {{% endif %}}
      </div>
      <div class="cb-cfg-banner-pill">
        <span class="cb-dot cb-dot-ok"></span> <span>Retention: 365d (DLM &amp; Transforms)</span>
      </div>
    </div>
  </div>

  <!-- KPI Strip -->
  <div class="cb-cfg-kpis">
    <div class="cb-cfg-kpi">
      <div class="cb-cfg-kpi-label">Cloud Billing Stream</div>
      <div class="cb-cfg-kpi-val" style="color: {{% if _billed_projects > 0 %}}var(--cc-color-accent, var(--cc-vis-1)){{% else %}}var(--cc-color-warning){{% endif %}};">
        {{% if _billed_projects > 0 %}}Active{{% else %}}Pending{{% endif %}}
      </div>
      <div class="cb-cfg-kpi-sub">{{{{ _active_sym }}}} {{{{ fmt_org_cost }}}} &middot; {{{{ _max_window_days }}}}d (latest: {{{{ _latest_bill_day }}}})</div>
    </div>
    <div class="cb-cfg-kpi">
      <div class="cb-cfg-kpi-label">Discovered Projects</div>
      <div class="cb-cfg-kpi-val">{{{{ _total_projects }}}}</div>
      <div class="cb-cfg-kpi-sub">{{{{ _cp_active_projects }}}} active &middot; {{{{ _inactive_projects }}}} decommissioned</div>
    </div>
    <div class="cb-cfg-kpi">
      <div class="cb-cfg-kpi-label">Data Plane API Keys</div>
      <div class="cb-cfg-kpi-val" style="color: {{% if _needs_key_projects > 0 %}}var(--cc-color-warning){{% else %}}var(--cc-color-accent, var(--cc-vis-1)){{% endif %}};">
        {{{{ _active_projects }}}} / {{{{ _cp_active_projects }}}}
      </div>
      <div class="cb-cfg-kpi-sub">
        {{% if _needs_key_projects > 0 %}}
          <strong style="color: var(--cc-color-danger);">{{{{ _needs_key_projects }}}} need access</strong> ({{{{ _hub_key_projects }}}} cloud/hub, {{{{ _remote_key_projects }}}} project)
        {{% else %}}
          {{{{ _hub_key_projects }}}} cloud key &middot; {{{{ _remote_key_projects }}}} project key(s)
        {{% endif %}}
      </div>
    </div>
    <div class="cb-cfg-kpi">
      <div class="cb-cfg-kpi-label">Data Stream Telemetry</div>
      <div class="cb-cfg-kpi-val">{{{{ _total_indices }}}} <span style="font-size:0.82rem; font-weight:500; opacity:0.75;">idx</span></div>
      <div class="cb-cfg-kpi-sub">{{{{ _total_datastreams }}}} data streams harvested</div>
    </div>
    <div class="cb-cfg-kpi">
      <div class="cb-cfg-kpi-label">AI &amp; Synthetics Streams</div>
      <div class="cb-cfg-kpi-val">{{{{ _total_ai_models }}}} <span style="font-size:0.82rem; font-weight:500; opacity:0.75;">AI</span> &middot; {{{{ _total_synth_monitors }}}} <span style="font-size:0.82rem; font-weight:500; opacity:0.75;">Syn</span></div>
      <div class="cb-cfg-kpi-sub">{{{{ _total_ai_models }}}} ML models &middot; {{{{ _total_synth_monitors }}}} monitors</div>
    </div>
    <div class="cb-cfg-kpi">
      <div class="cb-cfg-kpi-label">Currency &amp; Last Poll</div>
      <div class="cb-cfg-kpi-val" style="font-size: 1.15rem;">
        <span>{{{{ _active_sym }}}}</span> {{{{ _active_unit }}}} <span style="font-size:0.78rem; font-weight:500; opacity:0.75;">(&times;{{{{ _active_rate }}}})</span>
      </div>
      <div class="cb-cfg-kpi-sub">Poll: {{{{ _latest_poll }}}} UTC</div>
    </div>
  </div>

  <!-- Section 1: API Keys & Data Streams Status Matrix -->
  <div class="cb-cfg-sec-bar">
    <div class="cb-cfg-sec-title">
      <span>🔑</span> API Keys, Data Streams &amp; Lookup Pipeline Status
    </div>
    <span class="cb-cfg-sec-badge">5 Collection Streams &middot; 7 Lookup Indices &middot; 365d Retention</span>
  </div>

  <div class="cb-stream-wrap">
    <table class="cb-cfg-table">
      <thead>
        <tr>
          <th style="width: 175px;">Stream Status</th>
          <th>Data Stream &amp; Lookup Target</th>
          <th>API Key Setting</th>
          <th>Source API Endpoint</th>
          <th>Coverage &amp; Collected Volume</th>
          <th style="text-align: right; width: 145px;">Latest Snapshot (UTC)</th>
        </tr>
      </thead>
      <tbody>
        <!-- 1. Billing Stream -->
        <tr>
          <td>
            {{% if _billed_projects > 0 %}}
              <span class="cb-badge cb-badge-ok"><span class="cb-dot cb-dot-ok"></span>Active &amp; Synced</span>
            {{% else %}}
              <span class="cb-badge cb-badge-warn"><span class="cb-dot cb-dot-warn"></span>Awaiting Poll</span>
            {{% endif %}}
          </td>
          <td>
            <div class="cb-proj-cell">
              <span class="cb-proj-name">Billing &amp; Invoice Line Items (<code>chargeback_serverless.billing</code>)</span>
              <span class="cb-proj-id">metrics-chargeback_serverless.billing-* &rarr; serverless_billing_cost_lookup, serverless_billing_realized_pool_lookup</span>
            </div>
          </td>
          <td>
            <span class="cb-key-code">cloud_api_key</span>
            <div class="cb-prof-text">Elastic Cloud API Key + organization_id</div>
          </td>
          <td>
            <span class="cb-endpoint-text" title="GET https://billing.elastic-cloud.com/api/v2/billing/organizations/{{org_id}}/costs/instances">GET /api/v2/billing/organizations/&lt;org_id&gt;/costs/instances</span>
            <div class="cb-prof-text">Daily closed-day backfill ({{{{ _max_window_days }}}}d lookbehind)</div>
          </td>
          <td>
            <strong>{{{{ _billed_projects }}}} of {{{{ _total_projects }}}} projects billed</strong> &middot; {{{{ _active_sym }}}} {{{{ fmt_org_cost }}}} ({{{{ fmt_org_ecu }}}} ECU)
            <div class="cb-sub-meta">Includes active, $0/0-ECU, and decommissioned projects across {{{{ _max_window_days }}}} days</div>
          </td>
          <td style="text-align: right;" class="cb-time-col">{{{{ _latest_bill_day }}}}</td>
        </tr>

        <!-- 2. Control Plane Project Discovery -->
        <tr>
          <td>
            {{% if _total_projects > 0 %}}
              <span class="cb-badge cb-badge-ok"><span class="cb-dot cb-dot-ok"></span>Active &amp; Synced</span>
            {{% else %}}
              <span class="cb-badge cb-badge-warn"><span class="cb-dot cb-dot-warn"></span>Awaiting Poll</span>
            {{% endif %}}
          </td>
          <td>
            <div class="cb-proj-cell">
              <span class="cb-proj-name">Control Plane Project Discovery (<code>chargeback_serverless.usage &middot; project_status</code>)</span>
              <span class="cb-proj-id">metrics-chargeback_serverless.usage-* &rarr; serverless_project_status_lookup</span>
            </div>
          </td>
          <td>
            <span class="cb-key-code">cloud_api_key</span>
            <div class="cb-prof-text">Elastic Cloud API Key</div>
          </td>
          <td>
            <span class="cb-endpoint-text" title="GET https://api.elastic-cloud.com/api/v1/serverless/projects/{{all project types}}">GET /api/v1/serverless/projects/{{all project types}}</span>
            <div class="cb-prof-text">+ Historical Billing API project reconciliation</div>
          </td>
          <td>
            <strong>{{{{ _total_projects }}}} projects tracked</strong> ({{{{ _cp_active_projects }}}} active in Control Plane &middot; {{{{ _inactive_projects }}}} decommissioned)
            <div class="cb-sub-meta">Resolves human-readable project_name, project_type, and endpoint URLs</div>
          </td>
          <td style="text-align: right;" class="cb-time-col">{{{{ _latest_poll }}}}</td>
        </tr>

        <!-- 3. Data Stream Usage (_cat/indices) -->
        <tr>
          <td>
            {{% if _needs_key_projects > 0 %}}
              <span class="cb-badge cb-badge-warn"><span class="cb-dot cb-dot-warn"></span>Partial ({{{{ _needs_key_projects }}}} Need Key)</span>
            {{% elsif _active_projects > 0 %}}
              <span class="cb-badge cb-badge-ok"><span class="cb-dot cb-dot-ok"></span>Active (All Keys OK)</span>
            {{% else %}}
              <span class="cb-badge cb-badge-err"><span class="cb-dot cb-dot-err"></span>Needs API Key</span>
            {{% endif %}}
          </td>
          <td>
            <div class="cb-proj-cell">
              <span class="cb-proj-name">Data Stream Usage &amp; Storage (<code>chargeback_serverless.usage &middot; datastream</code>)</span>
              <span class="cb-proj-id">metrics-chargeback_serverless.usage-* &rarr; serverless_datastream_contribution_lookup, serverless_project_contribution_lookup</span>
            </div>
          </td>
          <td>
            <span class="cb-key-code">cloud_api_key</span>
            <div class="cb-prof-text">{{{{ _hub_key_projects }}}} Cloud key &middot; {{{{ _remote_key_projects }}}} Project key &middot; {{{{ _needs_key_projects }}}} Missing</div>
          </td>
          <td>
            <span class="cb-endpoint-text" title="GET /_cat/indices?h=index,dataset.size,indexing.index_time,search.query_time&format=json">GET /_cat/indices?h=index,dataset.size,indexing...,search...</span>
            <div class="cb-prof-text">Per-project Elasticsearch data plane</div>
          </td>
          <td>
            <strong>{{{{ _total_indices }}}} indices &rarr; {{{{ _total_datastreams }}}} data streams</strong> across {{{{ _active_projects }}}}/{{{{ _cp_active_projects }}}} active projects
            <div class="cb-sub-meta">Attributes Indexing, Search, and Retained Storage pools by namespace &amp; type</div>
          </td>
          <td style="text-align: right;" class="cb-time-col">{{{{ _latest_poll }}}}</td>
        </tr>

        <!-- 4. AI & ML Inference Telemetry -->
        <tr>
          <td>
            {{% if _needs_key_projects > 0 %}}
              <span class="cb-badge cb-badge-warn"><span class="cb-dot cb-dot-warn"></span>Partial ({{{{ _needs_key_projects }}}} Need Key)</span>
            {{% elsif _active_projects > 0 %}}
              <span class="cb-badge cb-badge-ok"><span class="cb-dot cb-dot-ok"></span>Active &amp; Synced</span>
            {{% else %}}
              <span class="cb-badge cb-badge-err"><span class="cb-dot cb-dot-err"></span>Needs API Key</span>
            {{% endif %}}
          </td>
          <td>
            <div class="cb-proj-cell">
              <span class="cb-proj-name">AI &amp; ML Model Inference (<code>chargeback_serverless.usage &middot; ai_model</code>)</span>
              <span class="cb-proj-id">metrics-chargeback_serverless.usage-* &rarr; serverless_ai_model_contribution_lookup</span>
            </div>
          </td>
          <td>
            <span class="cb-key-code">cloud_api_key</span>
            <div class="cb-prof-text">Cloud resource access (Cloud, ES, and Kibana API)</div>
          </td>
          <td>
            <span class="cb-endpoint-text" title="GET /_ml/trained_models/_stats">GET /_ml/trained_models/_stats</span>
            <div class="cb-prof-text">+ Billing AI SKUs (ml-vcu, inference, agent-builder)</div>
          </td>
          <td>
            <strong>{{{{ _total_ai_models }}}} trained AI/ML models</strong> harvested ({{{{ _active_projects }}}}/{{{{ _cp_active_projects }}}} active projects polled)
            <div class="cb-sub-meta">Attributes AI/ML &amp; Search VCU pools across ELSER, E5, and LLM deployments</div>
          </td>
          <td style="text-align: right;" class="cb-time-col">{{{{ _latest_poll }}}}</td>
        </tr>

        <!-- 5. Synthetics Monitor Telemetry -->
        <tr>
          <td>
            {{% if _total_synth_monitors > 0 and _synth_fallback_projects > 0 %}}
              <span class="cb-badge cb-badge-warn"><span class="cb-dot cb-dot-warn"></span>Active (SKU Fallback)</span>
            {{% elsif _active_projects > 0 or _total_synth_monitors > 0 %}}
              <span class="cb-badge cb-badge-ok"><span class="cb-dot cb-dot-ok"></span>Active &amp; Synced</span>
            {{% else %}}
              <span class="cb-badge cb-badge-err"><span class="cb-dot cb-dot-err"></span>Needs API Key</span>
            {{% endif %}}
          </td>
          <td>
            <div class="cb-proj-cell">
              <span class="cb-proj-name">Synthetics Monitor Telemetry (<code>chargeback_serverless.usage &middot; synthetics_monitor</code>)</span>
              <span class="cb-proj-id">metrics-chargeback_serverless.usage-* &rarr; serverless_synthetics_monitor_contribution_lookup</span>
            </div>
          </td>
          <td>
            <span class="cb-key-code">cloud_api_key</span>
            <div class="cb-prof-text">Index monitor on synthetics-* or Billing SKU fallback</div>
          </td>
          <td>
            <span class="cb-endpoint-text" title="GET /synthetics-*/_search">GET /synthetics-*/_search (aggs by monitor.id)</span>
            <div class="cb-prof-text">+ synthetic-pings &amp; synthetic-executions SKUs</div>
          </td>
          <td>
            <strong>{{{{ _total_synth_monitors }}}} Synthetics monitors</strong> tracked ({{% if _synth_fallback_projects > 0 %}}{{{{ _synth_fallback_projects }}}} project via Billing SKU attribution{{% else %}}direct _search harvest{{% endif %}})
            <div class="cb-sub-meta">Attributes lightweight ping and Playwright browser execution pools per monitor</div>
          </td>
          <td style="text-align: right;" class="cb-time-col">{{{{ _latest_poll }}}}</td>
        </tr>
      </tbody>
    </table>
  </div>

  <!-- Section 2: Per-Project Registry Table -->
  <div class="cb-cfg-sec-bar">
    <div class="cb-cfg-sec-title">
      <span>🖥️</span> Per-Project API Key &amp; Data Stream Status Registry
    </div>
    <span class="cb-cfg-sec-badge">{{{{ _total_projects }}}} Projects ({{{{ _cp_active_projects }}}} Active &middot; {{{{ _inactive_projects }}}} Inactive)</span>
  </div>

  {paged_top("cfgreg", 10, 20)}
  <table class="cb-cfg-table">
    <thead>
      <tr>
        <th>Project</th>
        <th>Type &amp; Endpoint</th>
        <th>API Key Credential</th>
        <th>Billing Stream (<code>cloud_api_key</code>)</th>
        <th>Data Streams (<code>_cat/indices</code>)</th>
        <th>AI &amp; ML (<code>_ml</code>)</th>
        <th>Synthetics (<code>synthetics-*</code>)</th>
        <th style="width: 145px; text-align: right;">Last Polled (UTC)</th>
      </tr>
    </thead>
    <tbody>
    {{% if _total_items == 0 %}}
      <tr>
        <td colspan="8" class="cb-empty">
          No Serverless projects discovered yet. Configure Organization ID and Cloud API Key in Fleet Integration Settings below to initiate discovery.
        </td>
      </tr>
    {{% else %}}
    {{% for row in rows %}}
      {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
      {{% assign h = row["health"].value | default: "stale" %}}
      {{% assign kt = row["api_key_type"].value | default: "missing_key" %}}
      {{% assign ss = row["synthetics_status"].value | default: "unauthorized" %}}
      {{% assign p_cost = row["total_cost"].value | default: 0.0 %}}
      {{% assign p_ecu = row["total_ecu"].value | default: 0.0 %}}
      {{% assign p_bdays = row["billed_days"].value | default: 0 | round %}}
      {{% assign p_bday = row["latest_bill_ts"].value | default: "" | slice: 0, 10 %}}
      {{% if p_bday == "" or p_bday == "null" %}}{{% assign p_bday = "none" %}}{{% endif %}}
      {{% assign p_idx = row["index_count"].value | default: 0 | round %}}
      {{% assign p_ds = row["datastream_count"].value | default: 0 | round %}}
      {{% assign p_ai = row["ai_model_count"].value | default: 0 | round %}}
      {{% assign p_syn = row["synthetics_monitor_count"].value | default: 0 | round %}}
      {liquid_money_block("p_cost", "fmt_p_cost", with_decimals=True)}
      {liquid_money_block("p_ecu", "fmt_p_ecu", with_decimals=True)}
      <tr class="cb-pg-row-cfgreg cb-pg-cfgreg-{{{{ _row_pg }}}}">
        <td>
          <div class="cb-proj-cell">
            <span class="cb-proj-name">{{{{ row["project_name"].value | default: row["project_id"].value }}}}</span>
            <span class="cb-proj-id">{{{{ row["project_id"].value }}}}</span>
          </div>
        </td>
        <td>
          <span class="cb-type-pill">{{{{ row["project_type"].value | default: "unknown" }}}}</span>
          <span class="cb-prof-text" style="margin-left:4px;">{{{{ row["optimized_for"].value | default: "general_purpose" }}}}</span>
          {{% assign ep = row["es_url"].value | default: "" %}}
          {{% if ep != "" and ep != "null" %}}
            <span class="cb-endpoint-text" title="{{{{ ep }}}}">{{{{ ep }}}}</span>
          {{% else %}}
            <span class="cb-endpoint-text" style="opacity:0.5;">No active endpoint (decommissioned)</span>
          {{% endif %}}
        </td>
        <td>
          {{% if kt == "remote_key" %}}
            <span class="cb-badge cb-badge-ok"><span class="cb-dot cb-dot-ok"></span>Project ES Key</span>
            <div class="cb-sub-meta"><code>project_api_keys</code> override</div>
          {{% elsif kt == "cloud_key" or kt == "hub_key" or h == "ok" %}}
            <span class="cb-badge cb-badge-ok"><span class="cb-dot cb-dot-ok"></span>Unified Cloud Key</span>
            <div class="cb-sub-meta"><code>cloud_api_key</code> authenticated</div>
          {{% elsif h == "inactive" or kt == "cloud_billing_only" %}}
            <span class="cb-badge cb-badge-warn"><span class="cb-dot cb-dot-warn"></span>Billing History Only</span>
            <div class="cb-sub-meta">Project decommissioned in Control Plane</div>
          {{% else %}}
            <span class="cb-badge cb-badge-err"><span class="cb-dot cb-dot-err"></span>Needs Resource Access</span>
            <div class="cb-sub-meta">Enable <code>Cloud, ES, and Kibana API</code> on <code>cloud_api_key</code></div>
          {{% endif %}}
        </td>
        <td>
          {{% if p_bdays > 0 %}}
            <span class="cb-badge cb-badge-ok"><span class="cb-dot cb-dot-ok"></span>Active &middot; {{{{ _active_sym }}}} {{{{ fmt_p_cost }}}}</span>
            <div class="cb-sub-meta">{{{{ fmt_p_ecu }}}} ECU &middot; {{{{ p_bdays }}}}d billed (latest: {{{{ p_bday }}}})</div>
          {{% else %}}
            <span class="cb-badge cb-badge-warn"><span class="cb-dot cb-dot-warn"></span>$0.00 in Window</span>
            <div class="cb-sub-meta">0.00 ECU &middot; 0 billed days</div>
          {{% endif %}}
        </td>
        <td>
          {{% if h == "ok" %}}
            <span class="cb-badge cb-badge-ok"><span class="cb-dot cb-dot-ok"></span>Active &middot; {{{{ p_idx }}}} idx</span>
            <div class="cb-sub-meta">{{{{ p_ds }}}} data streams harvested</div>
          {{% elsif h == "inactive" %}}
            <span class="cb-badge cb-badge-warn"><span class="cb-dot cb-dot-warn"></span>Inactive</span>
            <div class="cb-sub-meta">Decommissioned project</div>
          {{% else %}}
            <span class="cb-badge cb-badge-err"><span class="cb-dot cb-dot-err"></span>Unauthorized</span>
            <div class="cb-sub-meta">{{% if p_ds > 0 %}}{{{{ p_ds }}}} streams via SKU fallback{{% else %}}Needs Cloud resource access{{% endif %}}</div>
          {{% endif %}}
        </td>
        <td>
          {{% if h == "ok" %}}
            <span class="cb-badge cb-badge-ok"><span class="cb-dot cb-dot-ok"></span>Active &middot; {{{{ p_ai }}}} models</span>
            <div class="cb-sub-meta"><code>_ml/trained_models/_stats</code> OK</div>
          {{% elsif h == "inactive" %}}
            <span class="cb-badge cb-badge-warn"><span class="cb-dot cb-dot-warn"></span>Inactive</span>
            <div class="cb-sub-meta">Decommissioned project</div>
          {{% else %}}
            <span class="cb-badge cb-badge-err"><span class="cb-dot cb-dot-err"></span>Unauthorized</span>
            <div class="cb-sub-meta">Needs Cloud resource access</div>
          {{% endif %}}
        </td>
        <td>
          {{% if ss == "ok" or h == "ok" %}}
            <span class="cb-badge cb-badge-ok"><span class="cb-dot cb-dot-ok"></span>Active &middot; {{{{ p_syn }}}} monitors</span>
            <div class="cb-sub-meta"><code>synthetics-*/_search</code> OK</div>
          {{% elsif ss == "billing_derived" or p_syn > 0 %}}
            <span class="cb-badge cb-badge-warn"><span class="cb-dot cb-dot-warn"></span>SKU Fallback &middot; {{{{ p_syn }}}}</span>
            <div class="cb-sub-meta">Derived from Synthetics billing SKUs</div>
          {{% elsif h == "inactive" %}}
            <span class="cb-badge cb-badge-warn"><span class="cb-dot cb-dot-warn"></span>Inactive</span>
            <div class="cb-sub-meta">Decommissioned project</div>
          {{% else %}}
            <span class="cb-badge cb-badge-err"><span class="cb-dot cb-dot-err"></span>Unauthorized</span>
            <div class="cb-sub-meta">Needs Cloud resource access</div>
          {{% endif %}}
        </td>
        <td style="text-align: right;" class="cb-time-col">
          {{{{ row["updated_at"].value | default: "never" | slice: 0, 19 | replace: "T", " " }}}}
        </td>
      </tr>
    {{% endfor %}}
    {{% endif %}}
    </tbody>
  </table>
  {paged_bottom("cfgreg")}

  <!-- Section 3: Setup Guide -->
  <div class="cb-cfg-sec-bar">
    <div class="cb-cfg-sec-title">
      <span>📖</span> Fleet Integration &amp; Unified API Key Deployment Guide
    </div>
    <span class="cb-cfg-sec-badge">Single Unified API Key Setup</span>
  </div>

  <div class="cb-cfg-guide-grid">
    <!-- Step 1 -->
    <div class="cb-cfg-card">
      <div class="cb-cfg-card-head">
        <span class="cb-cfg-step-pill">Step 1</span>
        <span class="cb-cfg-card-title">Create Unified Elastic Cloud API Key</span>
      </div>
      <p class="cb-cfg-card-desc">In <strong>Cloud Console &rarr; Organization &rarr; API keys</strong>, create a single key with both roles enabled:</p>
      <pre class="cb-cfg-code"># Assign Roles in Cloud Console:
1. Billing admin:          ON (✓)
   (Required to see deleted projects &amp; deployments)

2. Cloud resource access:  ON (✓)
   Resources: All projects
   Access:    Cloud, Elasticsearch, and Kibana API</pre>
      <div class="cb-cfg-card-foot">
        <span>🔑 One key unlocks Billing, Control Plane, and ES Data Plane</span>
      </div>
    </div>

    <!-- Step 2 -->
    <div class="cb-cfg-card">
      <div class="cb-cfg-card-head">
        <span class="cb-cfg-step-pill">Step 2</span>
        <span class="cb-cfg-card-title">Configure Fleet Integration Policy</span>
      </div>
      <p class="cb-cfg-card-desc">Enter your Organization ID, unified Cloud API Key, and currency settings in Kibana Fleet.</p>
      <pre class="cb-cfg-code"># Configured in Kibana Fleet UI:
Organization ID:  &lt;YOUR_ORG_ID&gt;
Cloud API Key:    ••••••••••••••••
Lookbehind Days:  60
Currency Unit:    {{{{ _active_unit }}}} (e.g. USD, EUR, ZAR)
Currency Symbol:  {{{{ _active_sym }}}} (e.g. $, €, R, £)
Unit Rate:        {{{{ _active_rate }}}}</pre>
      <div class="cb-cfg-card-foot">
        <span>Active Policy:</span> <strong>{{{{ _active_unit }}}} ({{{{ _active_sym }}}} {{{{ _active_rate }}}})</strong>
      </div>
    </div>

    <!-- Step 3 -->
    <div class="cb-cfg-card">
      <div class="cb-cfg-card-head">
        <span class="cb-cfg-step-pill">Step 3</span>
        <span class="cb-cfg-card-title">Automatic Multi-Project Harvesting</span>
      </div>
      <p class="cb-cfg-card-desc">Your single Cloud API key automatically harvests all active and deleted projects across the organization.</p>
      <pre class="cb-cfg-code"># Automatically collected every day:
• Billing API:     All active + deleted projects/SKUs
• Control Plane:   All Serverless project endpoints
• Data Streams:    _cat/indices across all projects
• AI &amp; ML Models:  _ml/trained_models/_stats
• Synthetics:      synthetics-*/_search + SKU fallback
• Security:        Encrypted in Fleet Policy Secrets</pre>
      <div class="cb-cfg-card-foot">
        <span>🔒 Masked &amp; encrypted at rest in Fleet agent policy</span>
      </div>
    </div>
  </div>
</div>
"""

ALLOC_TABLE_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("alloc", 10, "table-row")}
  table.cb-t {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; color: var(--cc-color-text); }}
  table.cb-t th {{
    position: sticky; top: 0; background: var(--cc-color-surface); z-index: 1;
    text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--cc-color-border);
    font-weight: 600; opacity: 0.75; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em;
    color: var(--cc-color-text);
  }}
  table.cb-t td {{ padding: 6px 8px; border-bottom: 1px solid var(--cc-color-border); color: var(--cc-color-text); }}
  table.cb-t th.num, table.cb-t td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
  .cb-share-wrap {{ display: flex; align-items: center; gap: 6px; }}
  .cb-bar-track {{
    flex: 1; height: 10px; background: var(--cc-color-border);
    border-radius: var(--cc-radius-s); overflow: hidden; min-width: 60px;
  }}
  .cb-bar {{ height: 100%; background: var(--cc-vis-0); min-width: 2px; }}
  .cb-share-pct {{ width: 42px; text-align: right; font-variant-numeric: tabular-nums; font-size: 0.76rem; opacity: 0.78; color: var(--cc-color-text); flex-shrink: 0; }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "USD" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: "$" %}}
{{% assign max_cost = rows[0]["cost"].value | default: 1.0 %}}
{{% if max_cost <= 0 %}}{{% assign max_cost = 1.0 %}}{{% endif %}}
{{% assign tot_cost = 0.0 %}}
{{% for r in rows %}}
  {{% assign rc = r["cost"].value | default: 0.0 %}}
  {{% assign tot_cost = tot_cost | plus: rc %}}
{{% endfor %}}
{{% assign denom_tot = tot_cost %}}
{{% if denom_tot <= 0 %}}{{% assign denom_tot = 1.0 %}}{{% endif %}}
{paged_top("alloc", 10, 10)}
<table class="cb-t">
  <thead>
    <tr><th>Project</th><th>Type</th><th>Cost Type</th><th>Driver</th><th>Share</th><th class="num">Cost ({{{{ unit }}}})</th></tr>
  </thead>
  <tbody>
  {{% if _total_items == 0 %}}
    <tr><td colspan="6" class="cb-empty">No allocatable workload cost in the selected time range.</td></tr>
  {{% else %}}
  {{% for row in rows %}}
    {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
    {{% assign cost = row["cost"].value | default: 0.0 %}}
    {{% assign bar_pct = cost | times: 100.0 | divided_by: max_cost | round: 1 %}}
    {{% assign share_pct = cost | times: 100.0 | divided_by: denom_tot | round: 1 %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
    <tr class="cb-pg-row-alloc cb-pg-alloc-{{{{ _row_pg }}}}">
      <td><strong>{{{{ row["project"].value }}}}</strong></td>
      <td>{{{{ row["project_type_label"].value }}}}</td>
      <td>{{{{ row["cost_type_label"].value }}}}</td>
      <td>{{{{ row["scope"].value }}}}</td>
      <td>
        <div class="cb-share-wrap">
          <div class="cb-bar-track"><div class="cb-bar" style="width: {{{{ bar_pct }}}}%;"></div></div>
          <span class="cb-share-pct">{{{{ share_pct }}}}%</span>
        </div>
      </td>
      <td class="num"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost }}}}</span></td>
    </tr>
  {{% endfor %}}
  {{% endif %}}
  </tbody>
</table>
{paged_bottom("alloc")}
"""


def control_group(mode: str = "billing") -> dict:
    if mode == "usage":
        controls = [
            (stable_uuid("ctrl-project-type"), "project_type", "Project type", 0),
            (stable_uuid("ctrl-project-name"), "project_name", "Project name", 1),
            (stable_uuid("ctrl-allocation-driver"), "allocation_driver", "Workload driver", 2),
            (stable_uuid("ctrl-ds-namespace"), "ds_namespace", "Namespace", 3),
            (stable_uuid("ctrl-ds-type"), "ds_type", "Data stream type", 4),
        ]
    elif mode == "ai":
        controls = [
            (stable_uuid("ctrl-project-type"), "project_type", "Project type", 0),
            (stable_uuid("ctrl-project-name"), "project_name", "Project name", 1),
            (stable_uuid("ctrl-model-id"), "model_id", "AI model", 2),
            (stable_uuid("ctrl-task-type"), "task_type", "Task type", 3),
        ]
    elif mode == "synthetics":
        controls = [
            (stable_uuid("ctrl-project-type"), "project_type", "Project type", 0),
            (stable_uuid("ctrl-project-name"), "project_name", "Project name", 1),
            (stable_uuid("ctrl-monitor-type"), "monitor_type", "Synthetic type", 2),
            (stable_uuid("ctrl-monitor-id"), "monitor_id", "Monitor", 3),
            (stable_uuid("ctrl-ds-dataset"), "ds_dataset", "Synthetic dataset", 4),
            (stable_uuid("ctrl-ds-namespace"), "ds_namespace", "Namespace", 5),
        ]
    elif mode == "config":
        controls = [
            (stable_uuid("ctrl-project-type"), "project_type", "Project type", 0),
            (stable_uuid("ctrl-project-name"), "project_name", "Project name", 1),
        ]
    else:
        controls = [
            (stable_uuid("ctrl-project-type"), "project_type", "Project type", 0),
            (stable_uuid("ctrl-project-name"), "project_name", "Project name", 1),
            (stable_uuid("ctrl-cost-category"), "cost_category", "Cost category", 2),
            (stable_uuid("ctrl-cost-type"), "cost_type", "Cost type", 3),
            (stable_uuid("ctrl-allocation-driver"), "allocation_driver", "Allocation driver", 4),
        ]
    panels = {}
    refs = []
    for cid, field, title, order in controls:
        panels[cid] = {
            "explicitInput": {
                "dataViewId": INDEX_PATTERN_ID,
                "exclude": False,
                "existsSelected": False,
                "fieldName": field,
                "id": cid,
                "searchTechnique": "prefix",
                "selectedOptions": [],
                "sort": {"by": "_count", "direction": "desc"},
                "title": title,
            },
            "grow": False,
            "order": order,
            "type": "optionsListControl",
            "width": "medium",
        }
        refs.append(
            {
                "id": INDEX_PATTERN_ID,
                "name": f"controlGroup_{cid}:optionsListDataView",
                "type": "index-pattern",
            }
        )
    return {
        "controlGroupInput": {
            "chainingSystem": "HIERARCHICAL",
            "controlStyle": "oneLine",
            "ignoreParentSettingsJSON": {
                "ignoreFilters": False,
                "ignoreQuery": False,
                "ignoreTimerange": False,
                "ignoreValidations": False,
            },
            "panelsJSON": panels,
            "showApplySelections": False,
        },
        "controlGroupInputReferences": refs,
    }


def dashboard_doc(
    dash_id: str,
    title: str,
    description: str,
    panels: list,
    references: list,
    *,
    with_controls: bool = False,
    controls_mode: str = "billing",
    sections: list | None = None,
    hide_panel_titles: bool = False,
) -> dict:
    attrs: dict = {
        "description": description,
        "kibanaSavedObjectMeta": {
            "searchSourceJSON": {"filter": [], "query": {"language": "kuery", "query": ""}}
        },
        "optionsJSON": {
            "hidePanelTitles": hide_panel_titles,
            "syncColors": False,
            "syncCursor": True,
            "syncTooltips": False,
            "useMargins": True,
        },
        "panelsJSON": panels,
        "timeFrom": "now-30d",
        "timeRestore": True,
        "timeTo": "now",
        "title": title,
        "version": 1,
    }
    if sections:
        attrs["sections"] = sections
    refs = list(references)
    if with_controls:
        cg = control_group(controls_mode)
        attrs["controlGroupInput"] = cg["controlGroupInput"]
        refs.extend(cg["controlGroupInputReferences"])
    return {
        "attributes": attrs,
        "coreMigrationVersion": "8.8.0",
        "id": dash_id,
        "references": refs,
        "type": "dashboard",
        "typeMigrationVersion": "10.3.0",
    }


def to_kibana_import_doc(doc: dict) -> dict:
    """Convert an exported-format dashboard doc into a Kibana Saved Objects _import doc."""
    copy = json.loads(json.dumps(doc))
    if copy.get("type") == "dashboard":
        attrs = copy["attributes"]
        if isinstance(attrs.get("panelsJSON"), list):
            attrs["panelsJSON"] = json.dumps(attrs["panelsJSON"])
        if isinstance(attrs.get("optionsJSON"), dict):
            attrs["optionsJSON"] = json.dumps(attrs["optionsJSON"])
        kibana_meta = attrs.get("kibanaSavedObjectMeta", {})
        if isinstance(kibana_meta.get("searchSourceJSON"), dict):
            kibana_meta["searchSourceJSON"] = json.dumps(kibana_meta["searchSourceJSON"])
        cgi = attrs.get("controlGroupInput")
        if isinstance(cgi, dict):
            if isinstance(cgi.get("panelsJSON"), dict):
                cgi["panelsJSON"] = json.dumps(cgi["panelsJSON"])
            if isinstance(cgi.get("ignoreParentSettingsJSON"), dict):
                cgi["ignoreParentSettingsJSON"] = json.dumps(cgi["ignoreParentSettingsJSON"])
    return copy


def build_billing() -> dict:
    panels: list = []
    refs: list = []
    panel_id = stable_uuid("links-billing")
    p, r = links_panel(panel_id, DASH_BILLING)
    panels.append(p)
    refs.extend(r)

    p, _ = custom_panel(
        stable_uuid("cc-billing-kpi"),
        "Billing Summary",
        BILLING_KPI_TEMPLATE,
        f"""{COST_PRELUDE}
| EVAL alloc_cost = CASE(COALESCE(is_allocatable, false) == true AND allocation_driver != "none", cost, 0.0)
| EVAL unalloc_cost = CASE(COALESCE(is_allocatable, false) == false OR allocation_driver == "none", cost, 0.0)
| EVAL day = DATE_TRUNC(1 day, ts)
| EVAL active_pid = CASE(cost > 0 AND COALESCE(last_status, "") != "inactive" AND COALESCE(optimized_for, "") != "decommissioned", project_id, null)
| STATS total_spend = SUM(cost), allocatable_spend = SUM(alloc_cost), unallocatable_spend = SUM(unalloc_cost), project_count = COUNT_DISTINCT(project_id), active_project_count = COUNT_DISTINCT(active_pid), days_count = COUNT_DISTINCT(day), billed_days = COUNT_DISTINCT(day), latest_day = TO_STRING(MAX(ts)), currency_symbol = MAX(currency_symbol), unit = MAX(unit)""",
        0,
        2,
        48,
        6,
        description=(
            "Organization-wide Serverless invoice totals and spend pools:\n\n"
            "• Total Spend: Sum of all Serverless invoice line items (total_ecu × conf_chargeable_unit_rate) in serverless_billing_cost_lookup.\n"
            "• Allocatable Pool: Workload-driven SKUs (is_allocatable: true: ingest, search, retained storage, AI/ML, Synthetics) attributed to workloads.\n"
            "• Unallocatable Spend: Org-wide & pass-through charges (is_allocatable: false: Support contracts, Network Egress & Data Transfer, Snapshots, Platform SKUs) not tied to a single workload.\n"
            "• Billed Projects: Distinct project_id count in window, split into active Control Plane projects vs. decommissioned (inactive) projects.\n"
            "• Days of Data & Latest Day: Distinct UTC billing days (DATE_TRUNC 1 day) and most recent closed invoice snapshot."
        ),
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-billing-daily-project"),
        "Daily cost by project",
        DAILY_PROJECT_COST_TEMPLATE,
        f"""{COST_PRELUDE}
| STATS cost = SUM(cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY b = DATE_TRUNC(1 day, @timestamp), project
| EVAL day = TO_STRING(b)
| INLINE STATS day_cost = SUM(cost) BY day
| INLINE STATS proj_total = SUM(cost) BY project
| SORT day ASC, proj_total DESC, project ASC
| LIMIT 5000
| KEEP day, project, cost, day_cost, currency_symbol, unit""",
        0,
        8,
        24,
        11,
        description="Stacked daily invoice spend grouped by UTC day and Serverless project, resolved from Control Plane metadata.",
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-billing-daily-category"),
        "Daily cost by cost category",
        DAILY_CATEGORY_COST_TEMPLATE,
        f"""{COST_PRELUDE}
| WHERE COALESCE(sku, "none") != "none"
| STATS cost = SUM(cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY b = DATE_TRUNC(1 day, @timestamp), category_label
| RENAME category_label AS category
| EVAL day = TO_STRING(b)
| INLINE STATS day_cost = SUM(cost) BY day
| EVAL cat_order = CASE(category == "data_tier", 1, category == "platform", 2, category == "transfer", 3, category == "snapshot", 4, category == "support", 5, 6)
| SORT day ASC, cat_order ASC, cost DESC
| LIMIT 5000
| KEEP day, category, cost, day_cost, currency_symbol, unit""",
        24,
        8,
        24,
        11,
        description="Stacked daily invoice spend grouped by UTC day and normalized Cloud Billing category (data_tier, platform, transfer, snapshot, support, other).",
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-billing-project"),
        "Cost by project",
        make_bar_template(
            "project",
            "Project",
            prefix="bill_proj",
        ),
        f"""{COST_PRELUDE}
| STATS cost = SUM(cost), is_inactive = MAX(CASE(COALESCE(last_status, "") == "inactive" OR COALESCE(optimized_for, "") == "decommissioned", 1, 0)), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY project
| SORT cost DESC, project ASC
| LIMIT 200
| KEEP project, cost, is_inactive, currency_symbol, unit""",
        0,
        19,
        24,
        14,
        description="Ranks all discovered Serverless projects by total billed spend (ECU × chargeable_unit_rate) with active vs inactive project status.",
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-billing-type"),
        "Cost by cost category",
        make_bar_template(
            "category",
            "Cost category",
            prefix="bill_cat",
        ),
        f"""{COST_PRELUDE}
| WHERE COALESCE(sku, "none") != "none"
| STATS cost = SUM(cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY category_label
| RENAME category_label AS category
| SORT cost DESC
| LIMIT 50
| KEEP category, cost, currency_symbol, unit""",
        24,
        19,
        24,
        14,
        description="Ranks total organization spend across high-level Cloud Billing categories: data_tier, platform, transfer, snapshot, support, and other.",
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-billing-sankey"),
        "Spend flow (Cost category → Cost type → Project type)",
        SANKEY_TEMPLATE,
        f"""{COST_PRELUDE}
| WHERE COALESCE(sku, "none") != "none"
| STATS cost = SUM(cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY category_label, cost_type_label, project_type_label
| SORT cost DESC
| LIMIT 100
| RENAME category_label AS category, cost_type_label AS cost_type, project_type_label AS project_type
| KEEP category, cost_type, project_type, cost, currency_symbol, unit""",
        0,
        33,
        24,
        18,
        description=(
            "Multi-stage Sankey cost flow tracing billed spend from high-level Cloud Billing category through individual cost types into project types:\n\n"
            "• Category (cost_category): High-level bucket (data_tier, platform, support, transfer, snapshot).\n"
            "• Cost Type (cost_type): Normalized SKU family (ingest-vcu, search-vcu, retained, synthetics, inference, support).\n"
            "• Project Type (project_type): Serverless solution type (elasticsearch, observability, security, search, or custom project type)."
        ),
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-billing-unallocatable"),
        "Unallocatable & pass-through charges",
        UNALLOC_TABLE_TEMPLATE,
        f"""{COST_PRELUDE}
| EVAL is_placeholder = (COALESCE(sku, "none") == "none")
| EVAL real_sku_cnt = CASE(is_placeholder, 0, 1)
| INLINE STATS proj_real_skus = SUM(real_sku_cnt) BY project_id
| WHERE (COALESCE(is_allocatable, false) == false OR allocation_driver == "none") AND (NOT is_placeholder OR proj_real_skus == 0)
| STATS cost = SUM(cost), ecu = SUM(units), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY category_label, cost_type_label, sku, project
| RENAME category_label AS category, cost_type_label AS cost_type
| SORT cost DESC, ecu DESC, project ASC
| LIMIT 200
| KEEP category, cost_type, sku, project, ecu, cost, currency_symbol, unit""",
        24,
        33,
        24,
        18,
        description=(
            "Unallocatable & Pass-Through Charges (is_allocatable: false · driver: none):\n\n"
            "Line items classified with is_allocatable: false and allocation_driver: 'none' during billing ingestion:\n"
            "• Support Tiers (*support*): Organization-wide support contracts (e.g. serverless.support-tier4).\n"
            "• Network Egress & Data Transfer (*egress*, *data-transfer*): Infrastructure pass-through charges not tied to a single data stream or model.\n"
            "• Unclassified SKUs: Any non-workload administrative or account-level adjustment."
        ),
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-billing-table"),
        "Invoice reconciliation detail",
        INVOICE_TABLE_TEMPLATE,
        f"""{COST_PRELUDE}
| EVAL is_placeholder = (COALESCE(sku, "none") == "none")
| EVAL real_sku_cnt = CASE(is_placeholder, 0, 1)
| INLINE STATS proj_real_skus = SUM(real_sku_cnt) BY project_id
| WHERE NOT is_placeholder OR proj_real_skus == 0
| EVAL qty_val = CASE(quantity_value IS NOT NULL, quantity_value, 0.0)
| STATS qty = SUM(qty_val), ecu = SUM(units), cost = SUM(cost), is_inactive = MAX(CASE(COALESCE(last_status, "") == "inactive" OR COALESCE(optimized_for, "") == "decommissioned", 1, 0)), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY project, project_type_label, category_label, cost_type_label, sku, allocation_driver
| RENAME project_type_label AS project_type, category_label AS category, cost_type_label AS cost_type, allocation_driver AS driver
| SORT cost DESC, ecu DESC, qty DESC, project ASC
| LIMIT 1000
| KEEP project, project_type, category, cost_type, sku, driver, qty, ecu, cost, is_inactive, currency_symbol, unit""",
        0,
        51,
        48,
        14,
        description="Complete SKU-level invoice ledger reconciling project, project type, category, cost type, SKU, driver, quantity, and ECU spend.",
    )
    panels.append(p)

    return dashboard_doc(
        DASH_BILLING,
        TITLE_BILLING,
        "Serverless organization billing by project, cost category, and SKU in configured local currency.",
        panels,
        refs,
        with_controls=True,
    )


def build_usage() -> dict:
    panels: list = []
    refs: list = []
    panel_id = stable_uuid("links-usage")
    p, r = links_panel(panel_id, DASH_USAGE)
    panels.append(p)
    refs.extend(r)

    sec_workload_id = stable_uuid("sec-usage-workload")
    sec_datastream_id = stable_uuid("sec-usage-datastream")

    sections = [
        {
            "collapsed": False,
            "gridData": {
                "i": sec_workload_id,
                "y": 2,
            },
            "title": "Workload drivers & project allocation",
        },
        {
            "collapsed": False,
            "gridData": {
                "i": sec_datastream_id,
                "y": 3,
            },
            "title": "Data stream cost attribution & usage",
        },
    ]

    p, _ = custom_panel(
        stable_uuid("cc-usage-kpi"),
        "Workload Pool Summary",
        USAGE_KPI_TEMPLATE,
        f"""{COST_PRELUDE}
| WHERE COALESCE(is_allocatable, false) == true AND allocation_driver != "none"
| EVAL idx_c = CASE(cost_type_label IN ("ingest-vcu", "ingest"), cost, 0.0)
| EVAL qry_c = CASE(cost_type_label IN ("search-vcu", "search"), cost, 0.0)
| EVAL stg_c = CASE(cost_type_label == "retained", cost, 0.0)
| EVAL ai_c = CASE(allocation_driver == "ai" OR cost_type_label IN ("inference", "ml-vcu", "agent-builder"), cost, 0.0)
| EVAL syn_c = CASE(allocation_driver == "synthetics" OR cost_type_label == "synthetics", cost, 0.0)
| STATS pool_spend = SUM(cost), indexing_spend = SUM(idx_c), query_spend = SUM(qry_c), storage_spend = SUM(stg_c), ai_spend = SUM(ai_c), synthetics_spend = SUM(syn_c), currency_symbol = MAX(currency_symbol), unit = MAX(unit)""",
        0,
        0,
        48,
        6,
        section_id=sec_workload_id,
        description=(
            "Attributed workload spend pools across primary compute drivers:\n\n"
            "• Total Allocatable Pool: All workload-attributable spend (is_allocatable: true AND allocation_driver != 'none').\n"
            "• Indexing Pool: Spend from ingest-vcu & ingest SKUs, attributed across data streams by indexing.index_time.\n"
            "• Search & Query Pool: Spend from search-vcu & search SKUs, attributed across data streams by search.query_time.\n"
            "• Storage Pool: Spend from retained storage SKUs, attributed across data streams by dataset.size.\n"
            "• AI & ML Pool: Spend from ml-vcu, inference, and agent-builder SKUs.\n"
            "• Synthetics Pool: Spend from synthetics SKUs (pings and browser executions)."
        ),
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-usage-driver"),
        "Daily allocatable cost by workload driver",
        DAILY_DRIVER_COST_TEMPLATE,
        f"""{COST_PRELUDE}
| WHERE COALESCE(is_allocatable, false) == true AND allocation_driver != "none"
| EVAL driver = CASE(cost_type_label IN ("ingest-vcu", "ingest"), "Indexing (ingest-vcu + ingest)", cost_type_label IN ("search-vcu", "search"), "Search & Query (search-vcu)", cost_type_label == "retained", "Storage (retained)", allocation_driver == "ai" OR cost_type_label IN ("inference", "ml-vcu", "agent-builder"), "AI & ML (inference + ml-vcu + agent-builder)", allocation_driver == "synthetics" OR cost_type_label == "synthetics", "Synthetics (pings + executions)", cost_type_label)
| STATS cost = SUM(cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY b = DATE_TRUNC(1 day, @timestamp), driver
| EVAL day = TO_STRING(b)
| INLINE STATS day_cost = SUM(cost) BY day
| INLINE STATS drv_total = SUM(cost) BY driver
| SORT day ASC, drv_total DESC, driver ASC
| LIMIT 5000
| KEEP day, driver, cost, day_cost, currency_symbol, unit""",
        0,
        6,
        24,
        14,
        section_id=sec_workload_id,
        description="Stacked daily allocatable spend grouped by workload driver pool: Indexing, Search & Query, Storage, AI & ML, and Synthetics.",
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-usage-alloc"),
        "Cost allocation by project",
        ALLOC_TABLE_TEMPLATE,
        f"""{COST_PRELUDE}
| WHERE COALESCE(is_allocatable, false) == true AND allocation_driver != "none"
| STATS cost = SUM(cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY project, project_type_label, cost_type_label, allocation_driver
| SORT cost DESC
| LIMIT 50
| EVAL scope = allocation_driver
| KEEP project, project_type_label, cost_type_label, scope, cost, currency_symbol, unit""",
        24,
        6,
        24,
        14,
        section_id=sec_workload_id,
        description="Breaks down allocatable project spend by Cost Type and Allocation Driver with percentage share of total allocatable workload spend.",
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-usage-namespace"),
        "Attributed cost by namespace",
        make_bar_template(
            "namespace",
            "Namespace",
            prefix="usage_ns",
        ),
        f"""FROM {USAGE}
{TIME_FILTER_ESQL}
{USAGE_JOIN_BODY}
| EVAL idx_share = CASE(deployment_sum_indexing_time > 0, TO_DOUBLE(datastream_sum_indexing_time) / TO_DOUBLE(deployment_sum_indexing_time), 0.0)
| EVAL qry_share = CASE(deployment_sum_query_time > 0, TO_DOUBLE(datastream_sum_query_time) / TO_DOUBLE(deployment_sum_query_time), 0.0)
| EVAL stg_share = CASE(deployment_sum_data_set_store_size > 0, TO_DOUBLE(datastream_sum_data_set_store_size) / TO_DOUBLE(deployment_sum_data_set_store_size), 0.0)
| EVAL idx_cost = CASE(indexing_ecu IS NOT NULL, indexing_ecu, 0.0) * rate * idx_share
| EVAL qry_cost = CASE(query_ecu IS NOT NULL, query_ecu, 0.0) * rate * qry_share
| EVAL stg_cost = CASE(storage_ecu IS NOT NULL, storage_ecu, 0.0) * rate * stg_share
| EVAL total_ds_cost = idx_cost + qry_cost + stg_cost
| EVAL namespace = COALESCE(ds_namespace, "other")
| STATS cost = SUM(total_ds_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY namespace
| SORT cost DESC
| LIMIT 25
| KEEP namespace, cost, currency_symbol, unit""",
        0,
        0,
        24,
        14,
        section_id=sec_datastream_id,
        description="Sums daily attributed data-stream costs (indexing, query, storage) grouped by namespace to support tenant and environment chargeback.",
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-usage-dstype"),
        "Attributed cost by data stream type",
        make_bar_template(
            "ds_type",
            "Data stream type",
            prefix="usage_dstype",
        ),
        f"""FROM {USAGE}
{TIME_FILTER_ESQL}
{USAGE_JOIN_BODY}
| EVAL idx_share = CASE(deployment_sum_indexing_time > 0, TO_DOUBLE(datastream_sum_indexing_time) / TO_DOUBLE(deployment_sum_indexing_time), 0.0)
| EVAL qry_share = CASE(deployment_sum_query_time > 0, TO_DOUBLE(datastream_sum_query_time) / TO_DOUBLE(deployment_sum_query_time), 0.0)
| EVAL stg_share = CASE(deployment_sum_data_set_store_size > 0, TO_DOUBLE(datastream_sum_data_set_store_size) / TO_DOUBLE(deployment_sum_data_set_store_size), 0.0)
| EVAL idx_cost = CASE(indexing_ecu IS NOT NULL, indexing_ecu, 0.0) * rate * idx_share
| EVAL qry_cost = CASE(query_ecu IS NOT NULL, query_ecu, 0.0) * rate * qry_share
| EVAL stg_cost = CASE(storage_ecu IS NOT NULL, storage_ecu, 0.0) * rate * stg_share
| EVAL total_ds_cost = idx_cost + qry_cost + stg_cost
| EVAL ds_type_label = COALESCE(ds_type, "other")
| STATS cost = SUM(total_ds_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY ds_type_label
| RENAME ds_type_label AS ds_type
| SORT cost DESC
| LIMIT 25
| KEEP ds_type, cost, currency_symbol, unit""",
        24,
        0,
        24,
        14,
        section_id=sec_datastream_id,
        description="Sums daily attributed data-stream costs (indexing, query, storage) grouped by signal type (logs, metrics, traces, synthetics, other).",
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-usage-datastream-cost"),
        "Data stream cost attribution",
        DATASTREAM_COST_TABLE_TEMPLATE,
        f"""FROM {USAGE}
{TIME_FILTER_ESQL}
{USAGE_JOIN_BODY}
| EVAL idx_share = CASE(deployment_sum_indexing_time > 0, TO_DOUBLE(datastream_sum_indexing_time) / TO_DOUBLE(deployment_sum_indexing_time), 0.0)
| EVAL qry_share = CASE(deployment_sum_query_time > 0, TO_DOUBLE(datastream_sum_query_time) / TO_DOUBLE(deployment_sum_query_time), 0.0)
| EVAL stg_share = CASE(deployment_sum_data_set_store_size > 0, TO_DOUBLE(datastream_sum_data_set_store_size) / TO_DOUBLE(deployment_sum_data_set_store_size), 0.0)
| EVAL day_idx_cost = CASE(indexing_ecu IS NOT NULL, indexing_ecu, 0.0) * rate * idx_share
| EVAL day_qry_cost = CASE(query_ecu IS NOT NULL, query_ecu, 0.0) * rate * qry_share
| EVAL day_stg_cost = CASE(storage_ecu IS NOT NULL, storage_ecu, 0.0) * rate * stg_share
| EVAL day_attr_cost = day_idx_cost + day_qry_cost + day_stg_cost
| STATS indexing_cost = SUM(day_idx_cost), query_cost = SUM(day_qry_cost), storage_cost = SUM(day_stg_cost), attributed_cost = SUM(day_attr_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY project, project_type, ds_type, ds_namespace, datastream
| SORT attributed_cost DESC
| LIMIT 200
| KEEP project, project_type, ds_type, ds_namespace, datastream, indexing_cost, query_cost, storage_cost, attributed_cost, currency_symbol, unit""",
        0,
        14,
        48,
        14,
        section_id=sec_datastream_id,
        description="Detailed table attributing project Indexing, Search, and Storage spend pools to individual data streams based on harvested usage metrics.",
    )
    panels.append(p)

    esql = f"""FROM {USAGE}
{TIME_FILTER_ESQL}
| EVAL ts = @timestamp
| RENAME project_id AS _u_pid, project_name AS _u_pname, project_type AS _u_ptype
| LOOKUP JOIN {PROJECT_STATUS} ON composite_key
| EVAL @timestamp = ts
| EVAL project_id = COALESCE(_u_pid, project_id)
| EVAL project_name = CASE(_u_pname IS NOT NULL AND _u_pname != "" AND _u_pname != project_id, _u_pname, project_name IS NOT NULL AND project_name != "" AND project_name != project_id, project_name, _u_pname IS NOT NULL AND _u_pname != "", _u_pname, project_name IS NOT NULL AND project_name != "", project_name, project_id)
| EVAL project_type = CASE(_u_ptype IS NOT NULL AND _u_ptype != "" AND _u_ptype != "unknown", _u_ptype, project_type IS NOT NULL AND project_type != "", project_type, "unknown")
| EVAL project = CASE(project_name IS NOT NULL AND project_name != "", project_name, project_id IS NOT NULL AND project_id != "", project_id, "unknown"), ds_type = COALESCE(ds_type, "other"), ds_namespace = COALESCE(ds_namespace, "other")
| STATS indexing_ms = SUM(datastream_sum_indexing_time), query_ms = SUM(datastream_sum_query_time), size_mb = ROUND(AVG(TO_DOUBLE(datastream_sum_data_set_store_size)) / 1048576.0, 2) BY project, project_type, ds_type, ds_namespace, datastream
| SORT indexing_ms DESC
| LIMIT 200
| KEEP project, project_type, ds_type, ds_namespace, datastream, indexing_ms, query_ms, size_mb"""
    p, _ = custom_panel(
        stable_uuid("lens-usage-datastream-metrics"),
        "Data stream harvested usage metrics (_cat/indices)",
        DATASTREAM_METRICS_TABLE_TEMPLATE,
        esql,
        0,
        28,
        48,
        14,
        section_id=sec_datastream_id,
        description="Raw data-plane usage telemetry harvested via GET /_cat/indices: cumulative indexing time, query execution time, and primary dataset store size.",
    )
    panels.append(p)

    return dashboard_doc(
        DASH_USAGE,
        TITLE_USAGE,
        "Attribute project workload pools (Indexing, Search, Storage) across namespaces, data stream types (logs, metrics, traces), and individual data streams.",
        panels,
        refs,
        with_controls=True,
        controls_mode="usage",
        sections=sections,
    )


def build_ai() -> dict:
    panels: list = []
    refs: list = []
    panel_id = stable_uuid("links-ai")
    p, r = links_panel(panel_id, DASH_AI)
    panels.append(p)
    refs.extend(r)

    ai_kpi_esql = f"""FROM {AI_USAGE}
{TIME_FILTER_ESQL}
{AI_JOIN_BODY}
| EVAL ai_ecu_val = CASE(ai_ecu IS NOT NULL, ai_ecu, 0.0), qry_ecu_val = CASE(query_ecu IS NOT NULL, query_ecu, 0.0)
| EVAL ded_cost = ai_ecu_val * rate
| EVAL ai_pool_cost = CASE(ai_ecu_val > 0.0, ai_ecu_val, qry_ecu_val) * rate
| INLINE STATS total_models = COUNT_DISTINCT(model_id)
| STATS proj_spend = MAX(ai_pool_cost), proj_ded = MAX(ded_cost), proj_inf = SUM(inference_count), proj_latency = AVG(average_inference_time_ms), model_count = MAX(total_models), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY composite_key
| STATS ai_spend = SUM(proj_spend), dedicated_ai_spend = SUM(proj_ded), total_inferences = SUM(proj_inf), model_count = MAX(model_count), avg_latency_ms = ROUND(AVG(proj_latency), 1), currency_symbol = MAX(currency_symbol), unit = MAX(unit)
| EVAL cost_per_1k = CASE(total_inferences > 0, ai_spend * 1000.0 / TO_DOUBLE(total_inferences), 0.0)
| KEEP unit, currency_symbol, ai_spend, dedicated_ai_spend, total_inferences, model_count, avg_latency_ms, cost_per_1k"""

    p, _ = custom_panel(
        stable_uuid("cc-ai-kpi"),
        "AI & Machine Learning FinOps Overview",
        AI_KPIS_TEMPLATE,
        ai_kpi_esql,
        0,
        2,
        48,
        6,
        description=(
            "AI & Machine Learning compute attribution and unit economics:\n\n"
            "• AI & ML Spend: Dedicated AI SKU spend (ml-vcu, inference, agent-builder); falls back to shared search-vcu pool when built-in models run without dedicated ML SKUs.\n"
            "• Inference Volume: Total model inference calls (SUM(inference_count)) harvested via GET /_ml/trained_models/_stats.\n"
            "• Active AI Models: Distinct model_id count with recorded inference activity in the selected window.\n"
            "• Avg Model Latency: Mean inference execution time (average_inference_time_ms) across active deployments.\n"
            "• Unit Cost (1k Inferences): Effective cost per 1,000 inference calls (ai_spend × 1000 / total_inferences)."
        ),
    )
    panels.append(p)

    ai_daily_model_esql = f"""FROM {AI_USAGE}
{TIME_FILTER_ESQL}
{AI_JOIN_BODY}
| EVAL compute_time = CASE(total_inference_time_ms IS NOT NULL, TO_DOUBLE(total_inference_time_ms), 0.0)
| EVAL model_weight = CASE(compute_time > 0.0, compute_time, TO_DOUBLE(inference_count))
| EVAL ai_ecu_val = CASE(ai_ecu IS NOT NULL, ai_ecu, 0.0), qry_ecu_val = CASE(query_ecu IS NOT NULL, query_ecu, 0.0)
| EVAL ai_pool_cost = CASE(ai_ecu_val > 0.0, ai_ecu_val, qry_ecu_val) * rate
| INLINE STATS day_proj_weight = SUM(model_weight) BY composite_key
| EVAL day_model_cost = CASE(day_proj_weight > 0, (model_weight / day_proj_weight) * ai_pool_cost, 0.0)
| STATS cost = SUM(day_model_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY b = DATE_TRUNC(1 day, @timestamp), model_id
| EVAL day = TO_STRING(b)
| INLINE STATS day_cost = SUM(cost) BY day
| INLINE STATS mod_total = SUM(cost) BY model_id
| SORT day ASC, mod_total DESC, model_id ASC
| LIMIT 5000
| KEEP day, model_id, cost, day_cost, currency_symbol, unit"""

    p, _ = custom_panel(
        stable_uuid("cc-ai-daily-model"),
        "Daily attributed AI cost by model",
        DAILY_MODEL_COST_TEMPLATE,
        ai_daily_model_esql,
        0,
        8,
        24,
        12,
        description="Stacked daily AI compute spend attributed across active trained models based on each model's share of inference execution time.",
    )
    panels.append(p)

    ai_by_proj_esql = f"""FROM {AI_USAGE}
{TIME_FILTER_ESQL}
{AI_JOIN_BODY}
| EVAL compute_time = CASE(total_inference_time_ms IS NOT NULL, TO_DOUBLE(total_inference_time_ms), 0.0)
| EVAL model_weight = CASE(compute_time > 0.0, compute_time, TO_DOUBLE(inference_count))
| EVAL ai_ecu_val = CASE(ai_ecu IS NOT NULL, ai_ecu, 0.0), qry_ecu_val = CASE(query_ecu IS NOT NULL, query_ecu, 0.0)
| EVAL ai_pool_cost = CASE(ai_ecu_val > 0.0, ai_ecu_val, qry_ecu_val) * rate
| INLINE STATS day_proj_weight = SUM(model_weight) BY composite_key
| EVAL day_model_cost = CASE(day_proj_weight > 0, (model_weight / day_proj_weight) * ai_pool_cost, 0.0)
| EVAL project = CASE(project_name IS NOT NULL AND project_name != "", project_name, project_id IS NOT NULL AND project_id != "", project_id, "unknown")
| STATS cost = SUM(day_model_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY project
| SORT cost DESC
| LIMIT 25
| KEEP project, cost, currency_symbol, unit"""

    p, _ = custom_panel(
        stable_uuid("cc-ai-by-project"),
        "Attributed AI & ML cost by project",
        make_bar_template(
            "project",
            "Project",
            prefix="ai_proj",
        ),
        ai_by_proj_esql,
        24,
        8,
        24,
        12,
        description="Ranks Serverless projects by total attributed AI & ML spend across all deployed models and inference endpoints.",
    )
    panels.append(p)

    ai_models_esql = f"""FROM {AI_USAGE}
{TIME_FILTER_ESQL}
{AI_JOIN_BODY}
| EVAL compute_time = CASE(total_inference_time_ms IS NOT NULL, TO_DOUBLE(total_inference_time_ms), 0.0)
| EVAL model_weight = CASE(compute_time > 0.0, compute_time, TO_DOUBLE(inference_count))
| EVAL ai_ecu_val = CASE(ai_ecu IS NOT NULL, ai_ecu, 0.0), qry_ecu_val = CASE(query_ecu IS NOT NULL, query_ecu, 0.0)
| EVAL ai_pool_cost = CASE(ai_ecu_val > 0.0, ai_ecu_val, qry_ecu_val) * rate
| INLINE STATS day_proj_weight = SUM(model_weight) BY composite_key
| EVAL day_model_cost = CASE(day_proj_weight > 0, (model_weight / day_proj_weight) * ai_pool_cost, 0.0)
| EVAL project = CASE(project_name IS NOT NULL AND project_name != "", project_name, project_id IS NOT NULL AND project_id != "", project_id, "unknown")
| STATS total_compute = SUM(compute_time), total_inf = SUM(inference_count), avg_latency = ROUND(AVG(average_inference_time_ms), 1), attributed_cost = SUM(day_model_cost), ai_pool = SUM(ai_pool_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY task_type, model_id, project
| SORT total_compute DESC, total_inf DESC, model_id ASC
| LIMIT 50
| KEEP task_type, model_id, project, total_compute, total_inf, avg_latency, attributed_cost, ai_pool, currency_symbol, unit"""

    p, _ = custom_panel(
        stable_uuid("cc-ai-models"),
        "AI Model Compute & Spend Breakdown",
        AI_MODELS_TEMPLATE,
        ai_models_esql,
        0,
        20,
        48,
        14,
        description=(
            "Attributes project AI/ML spend across individual trained models harvested from GET /_ml/trained_models/_stats:\n\n"
            "• Compute Allocation Share: Model's share of total inference compute time (total_inference_time_ms = inference_count × average_inference_time_ms, falling back to inference_count).\n"
            "• Attributed Cost: Daily project AI pool (ai_ecu × rate, or query_ecu × rate fallback) weighted by the model's daily compute share."
        ),
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-ai-billing-table"),
        "Dedicated AI, Inference & Agent Builder Billing Line Items",
        AI_BILLING_TABLE_TEMPLATE,
        f"""{COST_PRELUDE}
| WHERE cost_type_label IN ("ml-vcu", "inference", "agent-builder") OR sku LIKE "*ml*" OR sku LIKE "*inference*" OR sku LIKE "*llm*" OR sku LIKE "*agent-builder*"
| EVAL qty_val = CASE(quantity_value IS NOT NULL, quantity_value, 0.0)
| STATS qty = SUM(qty_val), ecu = SUM(units), cost = SUM(cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY project, project_type_label, cost_type_label, sku
| RENAME project_type_label AS project_type, cost_type_label AS cost_type
| SORT cost DESC
| LIMIT 100
| KEEP project, project_type, cost_type, sku, qty, ecu, cost, currency_symbol, unit""",
        0,
        34,
        48,
        14,
        description="Cloud Billing invoice line items directly billed under AI & ML cost types or SKUs (inference, ml-vcu, agent-builder).",
    )
    panels.append(p)

    return dashboard_doc(
        DASH_AI,
        TITLE_AI,
        "AI and Machine Learning FinOps: model inference volume, compute latency, attributed AI pool spend, and dedicated ML/Inference/Agent Builder billing line items.",
        panels,
        refs,
        with_controls=True,
        controls_mode="ai",
    )


def build_synthetics() -> dict:
    panels: list = []
    refs: list = []
    panel_id = stable_uuid("links-synthetics")
    p, r = links_panel(panel_id, DASH_SYNTHETICS)
    panels.append(p)
    refs.extend(r)

    p, _ = custom_panel(
        stable_uuid("cc-synthetics-kpi"),
        "Synthetics Billing Summary",
        SYNTHETICS_KPI_TEMPLATE,
        f"""{COST_PRELUDE}
| EVAL is_synth = (cost_type_label == "synthetics" OR allocation_driver == "synthetics" OR sku LIKE "*synthetic*")
| EVAL is_pings = (sku LIKE "*synthetic-ping*")
| EVAL is_exec = (sku LIKE "*synthetic-exec*")
| EVAL synth_cost = CASE(is_synth, cost, 0.0)
| EVAL pings_cost = CASE(is_pings, cost, 0.0)
| EVAL exec_cost = CASE(is_exec, cost, 0.0)
| EVAL synth_ecu = CASE(is_synth, units, 0.0)
| EVAL raw_qty = CASE(is_synth AND quantity_value IS NOT NULL, quantity_value, 0.0)
| EVAL norm_runs = CASE(is_pings AND raw_qty > 0.0 AND raw_qty < 100.0 AND units > raw_qty * 5.0, raw_qty * 1000.0, raw_qty)
| EVAL synth_proj = CASE(is_synth AND cost > 0, project_id, null)
| STATS synthetics_spend = SUM(synth_cost), pings_spend = SUM(pings_cost), exec_spend = SUM(exec_cost), total_org_spend = SUM(cost), synthetics_ecu = SUM(synth_ecu), synthetics_runs = SUM(norm_runs), project_count = COUNT_DISTINCT(synth_proj), currency_symbol = MAX(currency_symbol), unit = MAX(unit)
| EVAL cost_per_1k = CASE(synthetics_runs > 0, synthetics_spend * 1000.0 / TO_DOUBLE(synthetics_runs), 0.0)
| KEEP unit, currency_symbol, synthetics_spend, pings_spend, exec_spend, total_org_spend, synthetics_ecu, synthetics_runs, project_count, cost_per_1k""",
        0,
        2,
        48,
        6,
        description=(
            "Direct Synthetics SKU spend and execution unit economics:\n\n"
            "• Direct Synthetics Spend: Sum of all Synthetics SKU charges (cost_type == 'synthetics') in serverless_billing_cost_lookup.\n"
            "• Lightweight Ping Spend: Spend on observability-synthetic-pings SKUs covering http, icmp, tcp, and dns monitors.\n"
            "• Browser Execution Spend: Spend on observability-synthetic-executions SKUs covering Playwright browser journeys.\n"
            "• Total Check Executions: Billed check runs across lightweight pings and Playwright browser executions.\n"
            "• Unit Cost (1k Checks): Blended cost per 1,000 check executions (synthetics_spend × 1000 / synthetics_runs).\n"
            "• Billed Synthetics Projects: Distinct Serverless projects incurring direct Synthetics SKU charges in the selected window."
        ),
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-synthetics-ds-kpi"),
        "Synthetics Data Stream Attribution Summary",
        SYNTHETICS_DS_KPI_TEMPLATE,
        f"""FROM {USAGE}
{TIME_FILTER_ESQL}
{USAGE_JOIN_BODY}
| EVAL is_synth_ds = (ds_type == "synthetics" OR datastream LIKE "*synthetics*")
| EVAL idx_share = CASE(is_synth_ds AND deployment_sum_indexing_time > 0, TO_DOUBLE(datastream_sum_indexing_time) / TO_DOUBLE(deployment_sum_indexing_time), 0.0)
| EVAL qry_share = CASE(is_synth_ds AND deployment_sum_query_time > 0, TO_DOUBLE(datastream_sum_query_time) / TO_DOUBLE(deployment_sum_query_time), 0.0)
| EVAL stg_share = CASE(is_synth_ds AND deployment_sum_data_set_store_size > 0, TO_DOUBLE(datastream_sum_data_set_store_size) / TO_DOUBLE(deployment_sum_data_set_store_size), 0.0)
| EVAL idx_cost = CASE(indexing_ecu IS NOT NULL, indexing_ecu, 0.0) * rate * idx_share
| EVAL qry_cost = CASE(query_ecu IS NOT NULL, query_ecu, 0.0) * rate * qry_share
| EVAL stg_cost = CASE(storage_ecu IS NOT NULL, storage_ecu, 0.0) * rate * stg_share
| EVAL ds_cost = idx_cost + qry_cost + stg_cost
| EVAL synth_ds_name = CASE(is_synth_ds, datastream, null)
| STATS ds_spend = SUM(ds_cost), indexing_spend = SUM(idx_cost), query_spend = SUM(qry_cost), storage_spend = SUM(stg_cost), ds_count = COUNT_DISTINCT(synth_ds_name), currency_symbol = MAX(currency_symbol), unit = MAX(unit)
| KEEP unit, currency_symbol, ds_spend, indexing_spend, query_spend, storage_spend, ds_count""",
        0,
        8,
        48,
        6,
        description=(
            "Secondary Elasticsearch data-tier attribution for Synthetics telemetry:\n\n"
            "• Attributed Data-Tier Spend: Total Elasticsearch data-tier spend (Indexing + Search + Storage) attributed to synthetics-* data streams.\n"
            "• Synthetics Indexing Spend: Portion of project ingest-vcu & ingest pools consumed by indexing synthetic check results and browser traces.\n"
            "• Synthetics Storage Spend: Portion of project retained storage pool consumed by storing synthetics-* backing indices.\n"
            "• Synthetics Data Streams: Count of active synthetics-* data streams harvested via _cat/indices."
        ),
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-synthetics-daily-type"),
        "Daily attributed synthetics cost by synthetic type",
        DAILY_SYNTH_TYPE_COST_TEMPLATE,
        f"""FROM {SYNTHETICS_USAGE}
{TIME_FILTER_ESQL}
{SYNTHETICS_JOIN_BODY}
{SYNTHETICS_MONITOR_COST_EVAL}
| STATS cost = SUM(day_monitor_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY b = DATE_TRUNC(1 day, @timestamp), monitor_type
| EVAL day = TO_STRING(b)
| INLINE STATS day_cost = SUM(cost) BY day
| INLINE STATS type_total = SUM(cost) BY monitor_type
| SORT day ASC, type_total DESC, monitor_type ASC
| LIMIT 5000
| KEEP day, monitor_type, cost, day_cost, currency_symbol, unit""",
        0,
        14,
        24,
        12,
        description="Stacked daily Synthetics spend partitioned by monitor protocol type (http, browser, icmp, tcp, dns).",
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-synthetics-by-type"),
        "Synthetics cost & check volume by synthetic type",
        SYNTHETICS_TYPE_BREAKDOWN_TEMPLATE,
        f"""FROM {SYNTHETICS_USAGE}
{TIME_FILTER_ESQL}
{SYNTHETICS_JOIN_BODY}
{SYNTHETICS_MONITOR_COST_EVAL}
| STATS monitors = COUNT_DISTINCT(monitor_id), checks = SUM(check_count), cost = SUM(day_monitor_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY monitor_type
| SORT cost DESC, checks DESC
| LIMIT 25
| KEEP monitor_type, monitors, checks, cost, currency_symbol, unit""",
        24,
        14,
        24,
        12,
        description="Aggregates Synthetics monitors, check runs, cost share, and attributed SKU spend across synthetic protocol types.",
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-synthetics-by-monitor"),
        "Attributed synthetics cost by monitor",
        make_bar_template(
            "monitor_label",
            "Monitor",
            prefix="synth_mon",
        ),
        f"""FROM {SYNTHETICS_USAGE}
{TIME_FILTER_ESQL}
{SYNTHETICS_JOIN_BODY}
{SYNTHETICS_MONITOR_COST_EVAL}
| EVAL monitor_label = CONCAT(COALESCE(monitor_name, monitor_id, "unknown"), " (", monitor_type, ")")
| STATS cost = SUM(day_monitor_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY monitor_label
| SORT cost DESC
| LIMIT 25
| KEEP monitor_label, cost, currency_symbol, unit""",
        0,
        26,
        24,
        12,
        description="Ranks individual Synthetics monitors by attributed daily SKU spend across browser executions and lightweight pings.",
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-synthetics-by-project"),
        "Synthetics SKU cost by project",
        make_bar_template(
            "project",
            "Project",
            prefix="synth_proj",
        ),
        f"""{COST_PRELUDE}
| WHERE cost_type_label == "synthetics" OR allocation_driver == "synthetics" OR sku LIKE "*synthetic*"
| STATS cost = SUM(cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY project
| SORT cost DESC
| LIMIT 25
| KEEP project, cost, currency_symbol, unit""",
        24,
        26,
        24,
        12,
        description="Ranks Serverless projects by direct Cloud Billing Synthetics SKU spend for ping and browser execution pools.",
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-synthetics-monitors-table"),
        "Synthetics Monitor Usage & Cost Attribution Breakdown",
        SYNTHETICS_MONITORS_TABLE_TEMPLATE,
        f"""FROM {SYNTHETICS_USAGE}
{TIME_FILTER_ESQL}
{SYNTHETICS_JOIN_BODY}
{SYNTHETICS_MONITOR_COST_EVAL}
| EVAL weighted_dur = CASE(average_duration_ms IS NOT NULL, average_duration_ms, 0.0) * chk
| STATS monitor_name = MAX(monitor_name), monitor_namespace = MAX(monitor_namespace), locations = MAX(locations_count), checks = SUM(check_count), up_checks = SUM(up_count), sum_weighted_dur = SUM(weighted_dur), cost = SUM(day_monitor_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY project, monitor_id, monitor_type
| EVAL availability_pct = CASE(checks > 0, TO_DOUBLE(up_checks) * 100.0 / TO_DOUBLE(checks), 100.0)
| EVAL avg_duration_ms = CASE(checks > 0, sum_weighted_dur / TO_DOUBLE(checks), 0.0)
| SORT cost DESC, checks DESC
| LIMIT 250
| KEEP monitor_name, monitor_id, monitor_type, project, monitor_namespace, locations, checks, availability_pct, avg_duration_ms, cost, currency_symbol, unit""",
        0,
        38,
        48,
        15,
        description="Comprehensive per-monitor table with monitor type filter tabs, location counts, check runs, availability %, latency, and attributed spend.",
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-synthetics-ds-namespace"),
        "Synthetics data stream cost by synthetic type & namespace",
        make_bar_template(
            "synth_stream_label",
            "Synthetic Type / Namespace",
            prefix="synth_ds_ns",
        ),
        f"""FROM {USAGE}
{TIME_FILTER_ESQL}
| WHERE ds_type == "synthetics" OR datastream LIKE "*synthetics*"
{USAGE_JOIN_BODY}
| EVAL idx_share = CASE(deployment_sum_indexing_time > 0, TO_DOUBLE(datastream_sum_indexing_time) / TO_DOUBLE(deployment_sum_indexing_time), 0.0)
| EVAL qry_share = CASE(deployment_sum_query_time > 0, TO_DOUBLE(datastream_sum_query_time) / TO_DOUBLE(deployment_sum_query_time), 0.0)
| EVAL stg_share = CASE(deployment_sum_data_set_store_size > 0, TO_DOUBLE(datastream_sum_data_set_store_size) / TO_DOUBLE(deployment_sum_data_set_store_size), 0.0)
| EVAL idx_cost = CASE(indexing_ecu IS NOT NULL, indexing_ecu, 0.0) * rate * idx_share
| EVAL qry_cost = CASE(query_ecu IS NOT NULL, query_ecu, 0.0) * rate * qry_share
| EVAL stg_cost = CASE(storage_ecu IS NOT NULL, storage_ecu, 0.0) * rate * stg_share
| EVAL total_ds_cost = idx_cost + qry_cost + stg_cost
| EVAL synth_type = COALESCE(ds_dataset, CASE(datastream LIKE "synthetics-browser.network-*", "browser.network", datastream LIKE "synthetics-browser.screenshot-*", "browser.screenshot", datastream LIKE "synthetics-browser-*", "browser", datastream LIKE "synthetics-http-*", "http", datastream LIKE "synthetics-icmp-*", "icmp", datastream LIKE "synthetics-tcp-*", "tcp", datastream LIKE "synthetics-dns-*", "dns", "http"))
| EVAL synth_stream_label = CONCAT(synth_type, " (", COALESCE(ds_namespace, "default"), ")")
| STATS cost = SUM(total_ds_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY synth_stream_label
| SORT cost DESC
| LIMIT 25
| KEEP synth_stream_label, cost, currency_symbol, unit""",
        0,
        53,
        24,
        13,
        description="Aggregates Elasticsearch data-tier spend (Indexing, Search, Storage) for synthetics-* data streams by protocol type and namespace.",
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-synthetics-sku-table"),
        "Synthetics billing line items (SKUs)",
        SYNTHETICS_SKU_TABLE_TEMPLATE,
        f"""{COST_PRELUDE}
| WHERE cost_type_label == "synthetics" OR allocation_driver == "synthetics" OR sku LIKE "*synthetic*"
| EVAL qty_val = CASE(quantity_value IS NOT NULL, quantity_value, 0.0)
| STATS qty = SUM(qty_val), ecu = SUM(units), cost = SUM(cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY project, project_type_label, sku
| RENAME project_type_label AS project_type
| SORT cost DESC
| LIMIT 100
| KEEP project, project_type, sku, qty, ecu, cost, currency_symbol, unit""",
        24,
        53,
        24,
        13,
        description="Direct Cloud Billing invoice line items for Elastic Synthetics (synthetic-pings and synthetic-executions SKUs).",
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-synthetics-ds-table"),
        "Synthetics data stream cost attribution & usage",
        SYNTHETICS_DS_TABLE_TEMPLATE,
        f"""FROM {USAGE}
{TIME_FILTER_ESQL}
| WHERE ds_type == "synthetics" OR datastream LIKE "*synthetics*"
{USAGE_JOIN_BODY}
| EVAL idx_share = CASE(deployment_sum_indexing_time > 0, TO_DOUBLE(datastream_sum_indexing_time) / TO_DOUBLE(deployment_sum_indexing_time), 0.0)
| EVAL qry_share = CASE(deployment_sum_query_time > 0, TO_DOUBLE(datastream_sum_query_time) / TO_DOUBLE(deployment_sum_query_time), 0.0)
| EVAL stg_share = CASE(deployment_sum_data_set_store_size > 0, TO_DOUBLE(datastream_sum_data_set_store_size) / TO_DOUBLE(deployment_sum_data_set_store_size), 0.0)
| EVAL day_idx_cost = CASE(indexing_ecu IS NOT NULL, indexing_ecu, 0.0) * rate * idx_share
| EVAL day_qry_cost = CASE(query_ecu IS NOT NULL, query_ecu, 0.0) * rate * qry_share
| EVAL day_stg_cost = CASE(storage_ecu IS NOT NULL, storage_ecu, 0.0) * rate * stg_share
| EVAL day_attr_cost = day_idx_cost + day_qry_cost + day_stg_cost
| EVAL synth_type = COALESCE(ds_dataset, CASE(datastream LIKE "synthetics-browser.network-*", "browser.network", datastream LIKE "synthetics-browser.screenshot-*", "browser.screenshot", datastream LIKE "synthetics-browser-*", "browser", datastream LIKE "synthetics-http-*", "http", datastream LIKE "synthetics-icmp-*", "icmp", datastream LIKE "synthetics-tcp-*", "tcp", datastream LIKE "synthetics-dns-*", "dns", "http"))
| STATS indexing_cost = SUM(day_idx_cost), query_cost = SUM(day_qry_cost), storage_cost = SUM(day_stg_cost), attributed_cost = SUM(day_attr_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY project, project_type, synth_type, ds_namespace, datastream
| SORT attributed_cost DESC
| LIMIT 200
| KEEP project, project_type, synth_type, ds_namespace, datastream, indexing_cost, query_cost, storage_cost, attributed_cost, currency_symbol, unit""",
        0,
        66,
        48,
        14,
        description="Attributes Elasticsearch Indexing, Search, and Storage spend pools to individual synthetics-* data streams.",
    )
    panels.append(p)

    return dashboard_doc(
        DASH_SYNTHETICS,
        TITLE_SYNTHETICS,
        "Synthetics FinOps: direct Synthetics SKU billing, synthetic type breakdown (http, browser, icmp, tcp, dns), per-monitor cost attribution, and data-tier costs for synthetics-* data streams.",
        panels,
        refs,
        with_controls=True,
        controls_mode="synthetics",
    )


def build_config() -> dict:
    panels: list = []
    refs: list = []
    panel_id = stable_uuid("links-config")
    p, r = links_panel(panel_id, DASH_CONFIG)
    panels.append(p)
    refs.extend(r)

    p, _ = custom_panel(
        stable_uuid("cc-config-unified"),
        "Fleet Configuration, Policy Settings & Serverless Project Registry",
        CONFIG_UNIFIED_TEMPLATE,
        f"""FROM {PROJECT_STATUS}
| EVAL ts = @timestamp
| RENAME project_id AS _ps_pid, conf_chargeable_unit_rate AS _ps_rate, conf_chargeable_unit_rate_unit AS _ps_unit, conf_currency_symbol AS _ps_sym
| LOOKUP JOIN {REALIZED_POOL} ON composite_key
| RENAME project_id AS _rp_pid
| LOOKUP JOIN {PROJECT_CONTRIB} ON composite_key
| EVAL @timestamp = ts
| EVAL project_id = COALESCE(_ps_pid, _rp_pid, project_id)
| EVAL conf_chargeable_unit_rate = CASE(_ps_rate IS NOT NULL, _ps_rate, conf_chargeable_unit_rate IS NOT NULL, conf_chargeable_unit_rate, 1.0), conf_chargeable_unit_rate_unit = COALESCE(_ps_unit, conf_chargeable_unit_rate_unit, "USD"), conf_currency_symbol = COALESCE(_ps_sym, conf_currency_symbol, "$")
{CURRENCY_RESOLVE_ESQL}
| EVAL day_ecu = CASE(all_sku_ecu IS NOT NULL, all_sku_ecu, total_ecu IS NOT NULL, total_ecu, 0.0)
| EVAL day_cost = day_ecu * conf_chargeable_unit_rate
| EVAL billed_day = CASE(day_ecu > 0.0, ts, null)
| INLINE STATS latest_upd = MAX(updated_at), latest_ts = MAX(ts) BY project_id
| EVAL is_latest = CASE(updated_at IS NOT NULL AND latest_upd IS NOT NULL, updated_at == latest_upd, ts == latest_ts)
| EVAL cur_status = CASE(is_latest, last_status, null),
       cur_idx_cnt = CASE(is_latest, index_count, null)
| STATS
    project_name = MAX(project_name),
    project_type = MAX(project_type),
    optimized_for = MAX(optimized_for),
    es_url = MAX(es_url),
    last_status = COALESCE(MAX(cur_status), MAX(last_status)),
    index_count = COALESCE(MAX(cur_idx_cnt), MAX(index_count)),
    store_bytes = MAX(deployment_sum_data_set_store_size),
    total_ecu = ROUND(SUM(day_ecu), 2),
    total_cost = ROUND(SUM(day_cost), 2),
    billed_days = COUNT_DISTINCT(billed_day),
    latest_bill_ts = MAX(billed_day),
    window_days = COUNT_DISTINCT(ts),
    updated_at = MAX(updated_at),
    conf_chargeable_unit_rate = MAX(conf_chargeable_unit_rate),
    conf_chargeable_unit_rate_unit = MAX(unit),
    conf_currency_symbol = MAX(currency_symbol)
  BY project_id
| EVAL health = CASE(
    last_status == "inactive", "inactive",
    last_status == "empty_or_unauthorized", "empty_or_unauthorized",
    updated_at IS NOT NULL AND updated_at > NOW() - 26 hours, "ok",
    "stale"
  )
| EVAL sort_order = CASE(health == "ok", 1, health == "empty_or_unauthorized", 2, health == "inactive", 3, 4)
| SORT sort_order ASC, total_cost DESC, project_name ASC
| LIMIT 200
| KEEP project_id, project_name, project_type, optimized_for, es_url, last_status, index_count, store_bytes, total_ecu, total_cost, billed_days, latest_bill_ts, window_days, updated_at, health, conf_chargeable_unit_rate, conf_chargeable_unit_rate_unit, conf_currency_symbol""",
        0,
        2,
        48,
        44,
        description="Fleet Configuration, policy settings, API key collection health, and discovered Serverless project status registry.",
    )
    panels.append(p)

    return dashboard_doc(
        DASH_CONFIG,
        TITLE_CONFIG,
        "Fleet Integration Policy configuration guide, active currency settings, discovered Serverless projects, and daily usage harvest status.",
        panels,
        refs,
        with_controls=True,
        controls_mode="config",
        hide_panel_titles=True,
    )


def alert_template(
    alert_id: str,
    name: str,
    esql: str,
    interval: str = "24h",
    time_window_size: int = 3,
    time_window_unit: str = "d",
) -> dict:
    return {
        "id": alert_id,
        "type": "alerting_rule_template",
        "attributes": {
            "name": name,
            "tags": ["Chargeback", "Chargeback Serverless"],
            "ruleTypeId": ".es-query",
            "schedule": {"interval": interval},
            "params": {
                "searchType": "esqlQuery",
                "timeWindowSize": time_window_size,
                "timeWindowUnit": time_window_unit,
                "esqlQuery": {"esql": esql},
                "size": 100,
                "threshold": [0],
                "thresholdComparator": ">",
                "timeField": "@timestamp",
                "excludeHitsFromPreviousRun": True,
            },
        },
    }


def build_alerts() -> list[dict]:
    return [
        # 1. Total Organization Daily Spend Threshold
        alert_template(
            ALERT_TOTAL_DAILY_SPEND,
            "[Chargeback Serverless] Total daily spend threshold exceeded",
            f"""FROM {BILLING}
| EVAL threshold_spend = 1000.0
| INLINE STATS latest_day = MAX(@timestamp)
| WHERE @timestamp == latest_day
{COST_PRELUDE_BODY}
| EVAL alloc_cost = CASE(COALESCE(is_allocatable, false) == true AND allocation_driver != "none", cost, 0.0)
| EVAL unalloc_cost = CASE((COALESCE(is_allocatable, false) == false OR allocation_driver == "none") AND COALESCE(sku, "none") != "none", cost, 0.0)
| EVAL active_pid = CASE(cost > 0 AND COALESCE(last_status, "") != "inactive", project_id, null)
| STATS spend = ROUND(SUM(cost), 2), allocatable_spend = ROUND(SUM(alloc_cost), 2), unallocatable_spend = ROUND(SUM(unalloc_cost), 2), ecu = ROUND(SUM(units), 2), active_projects = COUNT_DISTINCT(active_pid), threshold = MAX(threshold_spend), sym = COALESCE(MAX(currency_symbol), "$"), unit = COALESCE(MAX(unit), "USD"), day = LEFT(TO_STRING(MAX(@timestamp)), 10)
| WHERE spend > threshold
| EVAL result = CONCAT("[", day, "] Total organization daily spend of ", sym, TO_STRING(spend), " ", unit, " (", TO_STRING(ecu), " ECU across ", TO_STRING(active_projects), " active projects) exceeded threshold of ", sym, TO_STRING(threshold), " ", unit, ".")
| KEEP result, day, spend, allocatable_spend, unallocatable_spend, ecu, active_projects, threshold, unit""",
            interval="24h",
            time_window_size=3,
            time_window_unit="d",
        ),
        # 2. Rolling 30-Day Total Spend Budget Threshold
        alert_template(
            ALERT_TOTAL_MONTHLY_SPEND,
            "[Chargeback Serverless] Rolling 30-day total spend budget exceeded",
            f"""FROM {BILLING}
| EVAL threshold_budget = 25000.0
| INLINE STATS latest_ts = MAX(@timestamp)
| WHERE @timestamp > latest_ts - 30 days
{COST_PRELUDE_BODY}
| EVAL bill_day = DATE_TRUNC(1 day, ts)
| STATS spend = ROUND(SUM(cost), 2), ecu = ROUND(SUM(units), 2), billed_days = COUNT_DISTINCT(bill_day), latest_day = LEFT(TO_STRING(MAX(ts)), 10), threshold = MAX(threshold_budget), sym = COALESCE(MAX(currency_symbol), "$"), unit = COALESCE(MAX(unit), "USD")
| EVAL daily_avg_spend = CASE(billed_days > 0, ROUND(spend / TO_DOUBLE(billed_days), 2), 0.0)
| EVAL projected_30d_spend = ROUND(daily_avg_spend * 30.0, 2)
| WHERE spend > threshold
| EVAL result = CONCAT("Rolling 30-day spend of ", sym, TO_STRING(spend), " ", unit, " (", TO_STRING(ecu), " ECU over ", TO_STRING(billed_days), " billed days; avg ", sym, TO_STRING(daily_avg_spend), "/day, projected 30d: ", sym, TO_STRING(projected_30d_spend), " ", unit, ") exceeded budget of ", sym, TO_STRING(threshold), " ", unit, ".")
| KEEP result, latest_day, billed_days, spend, daily_avg_spend, projected_30d_spend, ecu, threshold, unit""",
            interval="24h",
            time_window_size=30,
            time_window_unit="d",
        ),
        # 3. Cost Category Daily Spend Threshold
        alert_template(
            ALERT_CATEGORY_SPEND,
            "[Chargeback Serverless] Cost category daily spend threshold exceeded",
            f"""FROM {BILLING}
| EVAL threshold_spend = 300.0, filter_category = "*"
| INLINE STATS latest_day = MAX(@timestamp)
| WHERE @timestamp == latest_day
{COST_PRELUDE_BODY}
| WHERE COALESCE(sku, "none") != "none"
| EVAL category = category_label, day = LEFT(TO_STRING(@timestamp), 10)
| WHERE filter_category == "*" OR category == filter_category
| STATS spend = ROUND(SUM(cost), 2), ecu = ROUND(SUM(units), 2), project_count = COUNT_DISTINCT(project_id), threshold = MAX(threshold_spend), sym = COALESCE(MAX(currency_symbol), "$"), unit = COALESCE(MAX(unit), "USD") BY day, category
| WHERE spend > threshold
| SORT spend DESC
| EVAL result = CONCAT("[", day, "] Cost category `", category, "` daily spend of ", sym, TO_STRING(spend), " ", unit, " (", TO_STRING(ecu), " ECU across ", TO_STRING(project_count), " projects) exceeded threshold of ", sym, TO_STRING(threshold), " ", unit, ".")
| KEEP result, day, category, spend, ecu, project_count, threshold, unit""",
            interval="24h",
            time_window_size=3,
            time_window_unit="d",
        ),
        # 4. Workload Driver Daily Spend Threshold
        alert_template(
            ALERT_WORKLOAD_DRIVER_SPEND,
            "[Chargeback Serverless] Workload driver daily spend threshold exceeded",
            f"""FROM {BILLING}
| EVAL threshold_spend = 250.0, filter_driver = "*"
| INLINE STATS latest_day = MAX(@timestamp)
| WHERE @timestamp == latest_day
{COST_PRELUDE_BODY}
| WHERE COALESCE(sku, "none") != "none"
| EVAL workload_driver = CASE(
    cost_type_label IN ("ingest-vcu", "ingest") OR allocation_driver == "indexing", "indexing",
    cost_type_label IN ("search-vcu", "search") OR allocation_driver == "query", "search",
    cost_type_label == "retained" OR allocation_driver == "storage", "storage",
    allocation_driver == "ai" OR cost_type_label IN ("inference", "ml-vcu", "agent-builder"), "ai",
    allocation_driver == "synthetics" OR cost_type_label == "synthetics", "synthetics",
    "unallocatable"
  )
| WHERE filter_driver == "*" OR workload_driver == filter_driver
| EVAL day = LEFT(TO_STRING(@timestamp), 10)
| STATS spend = ROUND(SUM(cost), 2), ecu = ROUND(SUM(units), 2), project_count = COUNT_DISTINCT(project_id), threshold = MAX(threshold_spend), sym = COALESCE(MAX(currency_symbol), "$"), unit = COALESCE(MAX(unit), "USD") BY day, workload_driver
| WHERE spend > threshold
| SORT spend DESC
| EVAL result = CONCAT("[", day, "] Workload driver `", workload_driver, "` daily spend of ", sym, TO_STRING(spend), " ", unit, " (", TO_STRING(ecu), " ECU across ", TO_STRING(project_count), " projects) exceeded threshold of ", sym, TO_STRING(threshold), " ", unit, ".")
| KEEP result, day, workload_driver, spend, ecu, project_count, threshold, unit""",
            interval="24h",
            time_window_size=3,
            time_window_unit="d",
        ),
        # 5. Project Daily Spend Threshold
        alert_template(
            ALERT_PROJECT_SPEND,
            "[Chargeback Serverless] Project daily spend threshold exceeded",
            f"""FROM {BILLING}
| EVAL threshold_spend = 200.0, filter_project = "*"
| INLINE STATS latest_day = MAX(@timestamp)
| WHERE @timestamp == latest_day
{COST_PRELUDE_BODY}
| EVAL project_type = project_type_label, day = LEFT(TO_STRING(@timestamp), 10)
| WHERE filter_project == "*" OR project == filter_project OR project_id == filter_project
| STATS spend = ROUND(SUM(cost), 2), ecu = ROUND(SUM(units), 2), threshold = MAX(threshold_spend), sym = COALESCE(MAX(currency_symbol), "$"), unit = COALESCE(MAX(unit), "USD") BY day, project, project_id, project_type
| WHERE spend > threshold
| SORT spend DESC
| EVAL result = CONCAT("[", day, "] Project `", project, "` (", project_id, ", ", project_type, ") daily spend of ", sym, TO_STRING(spend), " ", unit, " (", TO_STRING(ecu), " ECU) exceeded threshold of ", sym, TO_STRING(threshold), " ", unit, ".")
| KEEP result, day, project, project_id, project_type, spend, ecu, threshold, unit""",
            interval="24h",
            time_window_size=3,
            time_window_unit="d",
        ),
        # 6. Project Daily Spend Spike vs. 7-Day Baseline
        alert_template(
            ALERT_PROJECT_SPEND_SPIKE,
            "[Chargeback Serverless] Project daily spend spike vs. 7-day baseline",
            f"""FROM {BILLING}
| EVAL threshold_pct_increase = 50.0, min_daily_spend = 25.0, filter_project = "*"
| INLINE STATS latest_ts = MAX(@timestamp)
| WHERE @timestamp >= latest_ts - 7 days
{COST_PRELUDE_BODY}
| EVAL project_type = project_type_label
| WHERE filter_project == "*" OR project == filter_project OR project_id == filter_project
| EVAL day_ts = DATE_TRUNC(1 day, @timestamp)
| STATS day_spend = SUM(cost), day_ecu = SUM(units), threshold_pct = MAX(threshold_pct_increase), min_spend = MAX(min_daily_spend), sym = COALESCE(MAX(currency_symbol), "$"), unit = COALESCE(MAX(unit), "USD") BY day_ts, project, project_id, project_type
| INLINE STATS latest_day_ts = MAX(day_ts)
| EVAL is_latest = (day_ts == latest_day_ts)
| EVAL latest_val = CASE(is_latest, day_spend, null), baseline_val = CASE(NOT is_latest, day_spend, null), latest_ecu_val = CASE(is_latest, day_ecu, null)
| STATS latest_spend = ROUND(MAX(latest_val), 2), baseline_avg = ROUND(AVG(baseline_val), 2), baseline_days = COUNT(baseline_val), ecu = ROUND(MAX(latest_ecu_val), 2), day = LEFT(TO_STRING(MAX(latest_day_ts)), 10), threshold_pct = MAX(threshold_pct), min_spend = MAX(min_spend), sym = COALESCE(MAX(sym), "$"), unit = COALESCE(MAX(unit), "USD") BY project, project_id, project_type
| WHERE latest_spend IS NOT NULL AND latest_spend >= min_spend AND baseline_days > 0 AND baseline_avg > 0.0
| EVAL pct_increase = ROUND((latest_spend - baseline_avg) * 100.0 / baseline_avg, 1)
| WHERE pct_increase > threshold_pct
| SORT pct_increase DESC, latest_spend DESC
| EVAL result = CONCAT("[", day, "] Project `", project, "` (", project_type, ") daily spend spiked +", TO_STRING(pct_increase), "% to ", sym, TO_STRING(latest_spend), " ", unit, " vs. ", TO_STRING(baseline_days), "-day baseline avg of ", sym, TO_STRING(baseline_avg), " ", unit, " (threshold: +", TO_STRING(threshold_pct), "%).")
| KEEP result, day, project, project_id, project_type, latest_spend, baseline_avg, pct_increase, threshold_pct, unit""",
            interval="24h",
            time_window_size=10,
            time_window_unit="d",
        ),
        # 7. Namespace Attributed Daily Spend Threshold
        alert_template(
            ALERT_NAMESPACE_SPEND,
            "[Chargeback Serverless] Namespace attributed daily spend threshold exceeded",
            f"""FROM {USAGE}
| EVAL threshold_spend = 100.0, filter_namespace = "*"
| INLINE STATS latest_day = MAX(@timestamp)
| WHERE @timestamp == latest_day
{USAGE_JOIN_BODY}
| EVAL idx_share = CASE(deployment_sum_indexing_time > 0, TO_DOUBLE(datastream_sum_indexing_time) / TO_DOUBLE(deployment_sum_indexing_time), 0.0)
| EVAL qry_share = CASE(deployment_sum_query_time > 0, TO_DOUBLE(datastream_sum_query_time) / TO_DOUBLE(deployment_sum_query_time), 0.0)
| EVAL stg_share = CASE(deployment_sum_data_set_store_size > 0, TO_DOUBLE(datastream_sum_data_set_store_size) / TO_DOUBLE(deployment_sum_data_set_store_size), 0.0)
| EVAL idx_cost = CASE(indexing_ecu IS NOT NULL, indexing_ecu, 0.0) * rate * idx_share
| EVAL qry_cost = CASE(query_ecu IS NOT NULL, query_ecu, 0.0) * rate * qry_share
| EVAL stg_cost = CASE(storage_ecu IS NOT NULL, storage_ecu, 0.0) * rate * stg_share
| EVAL total_ds_cost = idx_cost + qry_cost + stg_cost
| EVAL namespace = COALESCE(ds_namespace, "other"), day = LEFT(TO_STRING(@timestamp), 10)
| WHERE filter_namespace == "*" OR namespace == filter_namespace
| STATS spend = ROUND(SUM(total_ds_cost), 2), indexing_spend = ROUND(SUM(idx_cost), 2), query_spend = ROUND(SUM(qry_cost), 2), storage_spend = ROUND(SUM(stg_cost), 2), datastream_count = COUNT_DISTINCT(datastream), project_count = COUNT_DISTINCT(project_id), threshold = MAX(threshold_spend), sym = COALESCE(MAX(currency_symbol), "$"), unit = COALESCE(MAX(unit), "USD") BY day, namespace
| WHERE spend > threshold
| SORT spend DESC
| EVAL result = CONCAT("[", day, "] Namespace `", namespace, "` attributed daily spend of ", sym, TO_STRING(spend), " ", unit, " across ", TO_STRING(datastream_count), " data streams (", TO_STRING(project_count), " projects) exceeded threshold of ", sym, TO_STRING(threshold), " ", unit, ".")
| KEEP result, day, namespace, spend, indexing_spend, query_spend, storage_spend, datastream_count, threshold, unit""",
            interval="24h",
            time_window_size=3,
            time_window_unit="d",
        ),
        # 8. AI Model Attributed Daily Spend Threshold
        alert_template(
            ALERT_AI_MODEL_SPEND,
            "[Chargeback Serverless] AI model attributed daily spend threshold exceeded",
            f"""FROM {AI_USAGE}
| EVAL threshold_spend = 50.0, filter_model = "*"
| INLINE STATS latest_day = MAX(@timestamp)
| WHERE @timestamp == latest_day
{AI_JOIN_BODY}
| EVAL compute_time = CASE(total_inference_time_ms IS NOT NULL, TO_DOUBLE(total_inference_time_ms), 0.0)
| EVAL model_weight = CASE(compute_time > 0.0, compute_time, TO_DOUBLE(inference_count))
| EVAL ai_ecu_val = CASE(ai_ecu IS NOT NULL, ai_ecu, 0.0), qry_ecu_val = CASE(query_ecu IS NOT NULL, query_ecu, 0.0)
| EVAL ai_pool_cost = CASE(ai_ecu_val > 0.0, ai_ecu_val, qry_ecu_val) * rate
| INLINE STATS day_proj_weight = SUM(model_weight) BY composite_key
| EVAL day_model_cost = CASE(day_proj_weight > 0, (model_weight / day_proj_weight) * ai_pool_cost, 0.0)
| EVAL project = CASE(project_name IS NOT NULL AND project_name != "", project_name, project_id IS NOT NULL AND project_id != "", project_id, "unknown"), day = LEFT(TO_STRING(@timestamp), 10)
| WHERE filter_model == "*" OR model_id == filter_model
| STATS spend = ROUND(SUM(day_model_cost), 2), inferences = SUM(inference_count), avg_latency_ms = ROUND(AVG(average_inference_time_ms), 1), threshold = MAX(threshold_spend), sym = COALESCE(MAX(currency_symbol), "$"), unit = COALESCE(MAX(unit), "USD") BY day, model_id, task_type, project
| WHERE spend > threshold
| SORT spend DESC
| EVAL result = CONCAT("[", day, "] AI model `", model_id, "` (", COALESCE(task_type, "inference"), ") in project `", project, "` attributed spend of ", sym, TO_STRING(spend), " ", unit, " (", TO_STRING(inferences), " inferences) exceeded threshold of ", sym, TO_STRING(threshold), " ", unit, ".")
| KEEP result, day, model_id, task_type, project, spend, inferences, avg_latency_ms, threshold, unit""",
            interval="24h",
            time_window_size=3,
            time_window_unit="d",
        ),
        # 9. Synthetics Monitor Attributed Daily Spend Threshold
        alert_template(
            ALERT_SYNTHETICS_MONITOR_SPEND,
            "[Chargeback Serverless] Synthetics monitor attributed daily spend threshold exceeded",
            f"""FROM {SYNTHETICS_USAGE}
| EVAL threshold_spend = 25.0, filter_monitor = "*"
| INLINE STATS latest_day = MAX(@timestamp)
| WHERE @timestamp == latest_day
{SYNTHETICS_JOIN_BODY}
{SYNTHETICS_MONITOR_COST_EVAL}
| EVAL project = CASE(project_name IS NOT NULL AND project_name != "", project_name, project_id IS NOT NULL AND project_id != "", project_id, "unknown")
| EVAL monitor_name = COALESCE(monitor_name, monitor_id, "unknown"), day = LEFT(TO_STRING(@timestamp), 10)
| WHERE filter_monitor == "*" OR monitor_id == filter_monitor OR monitor_name == filter_monitor
| STATS spend = ROUND(SUM(day_monitor_cost), 2), checks = SUM(check_count), threshold = MAX(threshold_spend), sym = COALESCE(MAX(currency_symbol), "$"), unit = COALESCE(MAX(unit), "USD") BY day, monitor_name, monitor_id, monitor_type, project
| WHERE spend > threshold
| SORT spend DESC
| EVAL result = CONCAT("[", day, "] Synthetics monitor `", monitor_name, "` (", monitor_type, ") in project `", project, "` attributed spend of ", sym, TO_STRING(spend), " ", unit, " (", TO_STRING(checks), " checks) exceeded threshold of ", sym, TO_STRING(threshold), " ", unit, ".")
| KEEP result, day, monitor_name, monitor_id, monitor_type, project, spend, checks, threshold, unit""",
            interval="24h",
            time_window_size=3,
            time_window_unit="d",
        ),
        # 10. Consolidated Data Trust & Harvest Health Alert
        alert_template(
            ALERT_DATA_TRUST_HEALTH,
            "[Chargeback Serverless] Data trust & project harvest health issue",
            f"""FROM {BILLING}
| EVAL threshold_unallocated_spend = 100.0, threshold_unallocated_pct = 25.0
| INLINE STATS latest_day = MAX(@timestamp)
| WHERE @timestamp == latest_day
{COST_PRELUDE_BODY}
| EVAL unalloc_cost = CASE((COALESCE(is_allocatable, false) == false OR allocation_driver == "none") AND COALESCE(sku, "none") != "none", cost, 0.0)
| EVAL is_stale_row = CASE(updated_at IS NULL OR updated_at < NOW() - 26 hours, 1, 0), day = LEFT(TO_STRING(@timestamp), 10)
| INLINE STATS org_total_spend = SUM(cost)
| STATS
    project = MAX(project),
    raw_pname = MAX(project_name),
    project_type = MAX(project_type_label),
    harvest_status = COALESCE(MAX(last_status), "missing"),
    updated_at_str = COALESCE(TO_STRING(MAX(updated_at)), "never"),
    is_stale_time = MAX(is_stale_row),
    proj_spend = ROUND(SUM(cost), 2),
    proj_ecu = ROUND(SUM(units), 2),
    unallocated_spend = ROUND(SUM(unalloc_cost), 2),
    org_spend = MAX(org_total_spend),
    thresh_unalloc = MAX(threshold_unallocated_spend),
    thresh_pct = MAX(threshold_unallocated_pct),
    sym = COALESCE(MAX(currency_symbol), "$"),
    unit = COALESCE(MAX(unit), "USD")
  BY day, project_id
| EVAL has_harvest_issue = (harvest_status != "inactive" AND (harvest_status != "ok" OR is_stale_time == 1))
| EVAL has_missing_name = (proj_ecu > 0.0 AND (raw_pname IS NULL OR raw_pname == "" OR raw_pname == project_id))
| EVAL unallocated_pct = CASE(org_spend > 0.0, ROUND(unallocated_spend * 100.0 / org_spend, 1), 0.0)
| EVAL has_unalloc_issue = (unallocated_spend > thresh_unalloc AND unallocated_pct > thresh_pct)
| WHERE has_harvest_issue OR has_missing_name OR has_unalloc_issue
| EVAL issue_type = CASE(
    has_harvest_issue, "stale_or_unauthorized_harvest",
    has_missing_name, "missing_project_name",
    "high_unallocated_spend"
  )
| EVAL result = CONCAT("[", day, "] Project `", COALESCE(project, project_id), "` (", project_id, ") issue=", issue_type, ": harvest_status=", harvest_status, " (last updated ", updated_at_str, "), unallocated=", sym, TO_STRING(unallocated_spend), " ", unit, " (", TO_STRING(unallocated_pct), "% of org spend).")
| KEEP result, day, project, project_id, project_type, issue_type, harvest_status, unallocated_spend, unallocated_pct, unit""",
            interval="24h",
            time_window_size=3,
            time_window_unit="d",
        ),
    ]


def build_index_pattern() -> dict:
    def f(name: str, typ: str, es_type: str) -> dict:
        return {
            "name": name,
            "type": typ,
            "esTypes": [es_type],
            "searchable": True,
            "aggregatable": True,
            "readFromDocValues": True,
        }

    fields = [
        f("@timestamp", "date", "date"),
        f("composite_key", "string", "keyword"),
        f("sku", "string", "keyword"),
        f("project_id", "string", "keyword"),
        f("project_name", "string", "keyword"),
        f("project_type", "string", "keyword"),
        f("deployment_id", "string", "keyword"),
        f("deployment_name", "string", "keyword"),
        f("deployment_type", "string", "keyword"),
        f("cost_category", "string", "keyword"),
        f("cost_type", "string", "keyword"),
        f("allocation_driver", "string", "keyword"),
        f("is_allocatable", "boolean", "boolean"),
        f("quantity_value", "number", "double"),
        f("total_ecu", "number", "double"),
        f("total_chargeable_units", "number", "double"),
        f("conf_chargeable_unit_rate", "number", "double"),
        f("conf_chargeable_unit_rate_unit", "string", "keyword"),
        f("conf_currency_symbol", "string", "keyword"),
        f("datastream", "string", "keyword"),
        f("ds_type", "string", "keyword"),
        f("ds_dataset", "string", "keyword"),
        f("ds_namespace", "string", "keyword"),
        f("model_id", "string", "keyword"),
        f("task_type", "string", "keyword"),
        f("monitor_id", "string", "keyword"),
        f("monitor_name", "string", "keyword"),
        f("monitor_type", "string", "keyword"),
        f("monitor_namespace", "string", "keyword"),
    ]
    return {
        "attributes": {
            "allowHidden": True,
            "fieldAttrs": "{}",
            "fieldFormatMap": "{}",
            "fields": json.dumps(fields),
            "name": "[Chargeback Serverless] Billing & Usage",
            "runtimeFieldMap": "{}",
            "sourceFilters": "[]",
            "timeFieldName": "@timestamp",
            "title": "serverless_*_lookup",
        },
        "coreMigrationVersion": "8.8.0",
        "id": INDEX_PATTERN_ID,
        "managed": False,
        "references": [],
        "type": "index-pattern",
        "typeMigrationVersion": "8.0.0",
    }


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=4, sort_keys=True) + "\n")


def main() -> None:
    for sub in ("dashboard", "tag", "alerting_rule_template", "index_pattern"):
        d = KIBANA / sub
        if d.exists():
            shutil.rmtree(d)
        if sub != "tag":
            d.mkdir(parents=True)

    dashboards = (
        build_billing(),
        build_usage(),
        build_ai(),
        build_synthetics(),
        build_config(),
    )
    for doc in dashboards:
        write_json(KIBANA / "dashboard" / f"{doc['id']}.json", doc)

    for alert in build_alerts():
        write_json(KIBANA / "alerting_rule_template" / f"{alert['id']}.json", alert)

    ip = build_index_pattern()
    write_json(KIBANA / "index_pattern" / f"{ip['id']}.json", ip)

    if len(sys.argv) > 2:
        ndjson_path = Path(sys.argv[2]).resolve()
        lines = [json.dumps(ip)]
        for doc in dashboards:
            lines.append(json.dumps(to_kibana_import_doc(doc)))
        ndjson_path.write_text("\n".join(lines) + "\n")
        print("Wrote NDJSON import bundle to", ndjson_path)

    print("Wrote Kibana assets under", KIBANA)


if __name__ == "__main__":
    main()
