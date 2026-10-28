#!/usr/bin/env python3
"""Generate Kibana assets for chargeback_serverless and export-ready NDJSON.

- Working Links (all dashboardLink with panelId:link_<uuid>_dashboard)
- Project wording (Serverless has projects, not deployments)
- Local currency via LOOKUP JOIN chargeback_conf_lookup on every spend visual
- Concrete project_* and deployment_* fields with chargeback-serverless-projects fallback
- Configuration dashboard shows Dev Tools DSL only (no prose setup instructions)
- Matches `elastic-package export dashboards` structure (typeMigrationVersion 10.3.0, expanded JSON attributes, no tag files)
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KIBANA = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "kibana"

DASH_BILLING = "chargeback_serverless-b6cecac0-ebe7-43de-bfc6-1ff287ad860a"
DASH_USAGE = "chargeback_serverless-21588d0e-fb6a-4f76-ad2f-cd7b3d7a3d7c"
DASH_CONFIG = "chargeback_serverless-7ec9743a-ccfa-43d2-8311-2f25893dadae"

TITLE_BILLING = "[Chargeback Serverless] Billing Components Overview"
TITLE_USAGE = "[Chargeback Serverless] Usage & Cost Allocation"
TITLE_CONFIG = "[Chargeback Serverless] Configuration"

BILLING = "billing_cluster_cost_lookup"
POOL = "billing_realized_pool_lookup"
DS_CONTRIB = "cluster_datastream_contribution_lookup"
DEP_CONTRIB = "cluster_deployment_contribution_lookup"
PROJECTS = "chargeback-serverless-projects"
STATE = "chargeback-serverless-state"
CONF = "chargeback_conf_lookup"

ALERT_UNALLOCATED = "chargeback_serverless-alert-unallocated-ecu"
ALERT_STALE_STATE = "chargeback_serverless-alert-stale-project-state"
ALERT_MISSING_PROJECTS = "chargeback_serverless-alert-missing-projects"

INDEX_PATTERN_ID = "chargeback_serverless-billing_cluster_cost_lookup"

# Shared ES|QL prelude: conf rate -> local currency; project labels from concrete deployment_* fields.
COST_PRELUDE = f"""FROM {BILLING}
| EVAL ts = @timestamp
| LOOKUP JOIN {CONF} ON ts >= conf_start_date AND ts <= conf_end_date
| EVAL unit = COALESCE(conf_chargeable_unit_rate_unit, "ECU")
| EVAL currency_symbol = CASE(
    conf_currency_symbol IS NOT NULL AND conf_currency_symbol != "", conf_currency_symbol,
    conf_chargeable_unit_rate_unit == "ZAR", "R",
    conf_chargeable_unit_rate_unit == "USD", "$",
    conf_chargeable_unit_rate_unit == "EUR", "€",
    conf_chargeable_unit_rate_unit == "GBP", "£",
    conf_chargeable_unit_rate_unit == "JPY", "¥",
    conf_chargeable_unit_rate_unit == "CNY", "¥",
    conf_chargeable_unit_rate_unit == "INR", "₹",
    conf_chargeable_unit_rate_unit == "CAD", "CA$",
    conf_chargeable_unit_rate_unit == "AUD", "A$",
    conf_chargeable_unit_rate_unit == "CHF", "CHF",
    conf_chargeable_unit_rate_unit == "BRL", "R$",
    conf_chargeable_unit_rate_unit IS NOT NULL AND conf_chargeable_unit_rate_unit != "", conf_chargeable_unit_rate_unit,
    "ECU"
  )
| EVAL units = COALESCE(total_ecu, 0.0)
| EVAL cost = units * COALESCE(conf_chargeable_unit_rate, 1.0)
| EVAL project = CASE(deployment_name IS NOT NULL AND deployment_name != "", deployment_name, deployment_id IS NOT NULL AND deployment_id != "", deployment_id, "organization")
| EVAL project_type_label = CASE(deployment_type IS NOT NULL AND deployment_type != "", deployment_type, "organization")
| EVAL category_label = COALESCE(cost_category, "other")
| EVAL cost_type_label = COALESCE(cost_type, "other")
| WHERE ts IS NOT NULL"""


def stable_uuid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"chargeback_serverless:{name}"))


def adhoc_id(title: str) -> str:
    return hashlib.sha256(title.encode()).hexdigest()


def links_panel(panel_id: str, current: str) -> tuple[dict, list]:
    """Navigation links bar: all three links use dashboardLink with dashboard references."""
    nav = [
        (DASH_BILLING, "[Chargeback Serverless] Billing Components Overview"),
        (DASH_USAGE, "[Chargeback Serverless] Usage & Cost Allocation"),
        (DASH_CONFIG, "[Chargeback Serverless] Configuration"),
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
    time_field: str = "@timestamp",
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

    panel = {
        "embeddableConfig": {
            "attributes": {
                "description": "",
                "references": [],
                "state": state,
                "title": title,
                "type": "lens",
                "version": 1,
                "visualizationType": visualization_type,
            },
            "enhancements": {"dynamicActions": {"events": []}},
            "filters": [],
            "query": {"esql": esql},
            "syncColors": False,
            "syncCursor": True,
            "syncTooltips": False,
            "title": title,
        },
        "gridData": {"x": x, "y": y, "w": w, "h": h, "i": panel_id},
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
    esql_query: str,
    x: int,
    y: int,
    w: int,
    h: int,
) -> tuple[dict, list]:
    panel = {
        "embeddableConfig": {
            "esql_query": [esql_query],
            "template": template,
        },
        "gridData": {"x": x, "y": y, "w": w, "h": h, "i": panel_id},
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


BILLING_KPI_TEMPLATE = f"""\
<style>
  .cb-kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: var(--cc-space-s, 8px); margin: 0 0 14px 0; padding: 0 0 8px 0; }}
  .cb-kpi {{
    background: var(--cc-color-surface);
    border: 1px solid var(--cc-color-border);
    border-radius: var(--cc-radius);
    padding: 10px 14px 12px 14px;
  }}
  .cb-kpi-label {{ color: var(--cc-color-text); opacity: 0.72; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em; line-height: 1.1; }}
  .cb-kpi-value {{ font-size: 1.45rem; font-weight: 600; margin-top: 4px; line-height: 1.1; font-variant-numeric: tabular-nums; display: flex; align-items: baseline; }}
  .cb-sym {{ font-size: 1.05rem; opacity: 0.85; margin-right: 4px; font-weight: 500; }}
  .cb-kpi-sub {{ font-size: 0.75rem; opacity: 0.68; margin-top: 4px; line-height: 1.1; }}
</style>
{{% assign r = rows[0] %}}
{{% assign total = r["total_spend"].value | default: 0.0 %}}
{{% assign alloc = r["allocatable_spend"].value | default: 0.0 %}}
{{% assign unalloc = r["unallocatable_spend"].value | default: 0.0 %}}
{{% assign unit = r["unit"].value | default: "ECU" %}}
{{% assign sym = r["currency_symbol"].value | default: unit %}}
{{% assign denom = total %}}
{{% if denom <= 0 %}}{{% assign denom = 1.0 %}}{{% endif %}}
{{% assign alloc_pct = alloc | times: 100.0 | divided_by: denom | round %}}
{{% assign unalloc_pct = unalloc | times: 100.0 | divided_by: denom | round %}}
{liquid_money_block("total", "fmt_total", with_decimals=False)}
{liquid_money_block("alloc", "fmt_alloc", with_decimals=False)}
{liquid_money_block("unalloc", "fmt_unalloc", with_decimals=False)}
<div class="cb-kpis">
  <div class="cb-kpi">
    <div class="cb-kpi-label">Total Spend ({{{{ unit }}}})</div>
    <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_total }}}}</span></div>
    <div class="cb-kpi-sub">All Serverless line items</div>
  </div>
  <div class="cb-kpi">
    <div class="cb-kpi-label">Allocatable Pool ({{{{ unit }}}})</div>
    <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_alloc }}}}</span></div>
    <div class="cb-kpi-sub">{{{{ alloc_pct }}}}% of total spend</div>
  </div>
  <div class="cb-kpi">
    <div class="cb-kpi-label">Unallocatable Spend ({{{{ unit }}}})</div>
    <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_unalloc }}}}</span></div>
    <div class="cb-kpi-sub">{{{{ unalloc_pct }}}}% pass-through / org-wide</div>
  </div>
  <div class="cb-kpi">
    <div class="cb-kpi-label">Billed Projects</div>
    <div class="cb-kpi-value">{{{{ r["project_count"].value | default: 0 }}}}</div>
    <div class="cb-kpi-sub">Active projects in window</div>
  </div>
  <div class="cb-kpi">
    <div class="cb-kpi-label">Latest Billed Day</div>
    <div class="cb-kpi-value">{{{{ r["latest_day"].value | default: "—" | slice: 0, 10 }}}}</div>
    <div class="cb-kpi-sub">UTC invoice snapshot</div>
  </div>
</div>
"""


USAGE_KPI_TEMPLATE = f"""\
<style>
  .cb-kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: var(--cc-space-s, 8px); margin: 0 0 14px 0; padding: 0 0 8px 0; }}
  .cb-kpi {{
    background: var(--cc-color-surface);
    border: 1px solid var(--cc-color-border);
    border-radius: var(--cc-radius);
    padding: 10px 14px 12px 14px;
  }}
  .cb-kpi-label {{ color: var(--cc-color-text); opacity: 0.72; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em; line-height: 1.1; }}
  .cb-kpi-value {{ font-size: 1.45rem; font-weight: 600; margin-top: 4px; line-height: 1.1; font-variant-numeric: tabular-nums; display: flex; align-items: baseline; }}
  .cb-sym {{ font-size: 1.05rem; opacity: 0.85; margin-right: 4px; font-weight: 500; }}
  .cb-kpi-sub {{ font-size: 0.75rem; opacity: 0.68; margin-top: 4px; line-height: 1.1; }}
</style>
{{% assign r = rows[0] %}}
{{% assign pool = r["pool_spend"].value | default: 0.0 %}}
{{% assign idx_s = r["indexing_spend"].value | default: 0.0 %}}
{{% assign qry_s = r["query_spend"].value | default: 0.0 %}}
{{% assign stg_s = r["storage_spend"].value | default: 0.0 %}}
{{% assign unit = r["unit"].value | default: "ECU" %}}
{{% assign sym = r["currency_symbol"].value | default: unit %}}
{{% assign denom = pool %}}
{{% if denom <= 0 %}}{{% assign denom = 1.0 %}}{{% endif %}}
{{% assign idx_pct = idx_s | times: 100.0 | divided_by: denom | round %}}
{{% assign qry_pct = qry_s | times: 100.0 | divided_by: denom | round %}}
{{% assign stg_pct = stg_s | times: 100.0 | divided_by: denom | round %}}
{liquid_money_block("pool", "fmt_pool", with_decimals=False)}
{liquid_money_block("idx_s", "fmt_idx_s", with_decimals=False)}
{liquid_money_block("qry_s", "fmt_qry_s", with_decimals=False)}
{liquid_money_block("stg_s", "fmt_stg_s", with_decimals=False)}
<div class="cb-kpis">
  <div class="cb-kpi">
    <div class="cb-kpi-label">Total Allocatable Pool ({{{{ unit }}}})</div>
    <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_pool }}}}</span></div>
    <div class="cb-kpi-sub">100% workload-attributable spend</div>
  </div>
  <div class="cb-kpi">
    <div class="cb-kpi-label">Indexing Pool ({{{{ unit }}}})</div>
    <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_idx_s }}}}</span></div>
    <div class="cb-kpi-sub">{{{{ idx_pct }}}}% · ingest-vcu + ingest</div>
  </div>
  <div class="cb-kpi">
    <div class="cb-kpi-label">Search &amp; Query Pool ({{{{ unit }}}})</div>
    <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_qry_s }}}}</span></div>
    <div class="cb-kpi-sub">{{{{ qry_pct }}}}% · search-vcu + ml-vcu</div>
  </div>
  <div class="cb-kpi">
    <div class="cb-kpi-label">Storage Pool ({{{{ unit }}}})</div>
    <div class="cb-kpi-value"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_stg_s }}}}</span></div>
    <div class="cb-kpi-sub">{{{{ stg_pct }}}}% · retained storage</div>
  </div>
</div>
"""


SANKEY_TEMPLATE = f"""\
<style>
  .cb-flow {{ display: flex; flex-direction: column; gap: var(--cc-space-s); }}
  .cb-row {{
    display: grid;
    grid-template-columns: minmax(90px, 0.9fr) minmax(110px, 1.1fr) minmax(100px, 1fr) 2fr 135px;
    gap: var(--cc-space-s);
    align-items: center;
  }}
  .cb-stage {{ font-size: 0.88rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .cb-bar-track {{
    height: 14px; background: var(--cc-color-border);
    border-radius: var(--cc-radius-s); overflow: hidden;
  }}
  .cb-bar {{ height: 100%; background: var(--cc-vis-0); min-width: 2px; }}
  .cb-cost {{ text-align: right; font-variant-numeric: tabular-nums; font-size: 0.88rem; white-space: nowrap; }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "ECU" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: unit %}}
{{% assign max_cost = rows[0]["cost"].value | default: 1.0 %}}
{{% if max_cost <= 0 %}}{{% assign max_cost = 1.0 %}}{{% endif %}}
<div class="cb-flow">
{{% for row in rows %}}
  {{% assign cost = row["cost"].value | default: 0.0 %}}
  {{% assign bar_pct = cost | times: 100.0 | divided_by: max_cost | round: 1 %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
  <div class="cb-row">
    <div class="cb-stage"><strong>{{{{ row["category"].value }}}}</strong></div>
    <div class="cb-stage">→ {{{{ row["cost_type"].value }}}}</div>
    <div class="cb-stage">→ {{{{ row["project_type"].value }}}}</div>
    <div class="cb-bar-track"><div class="cb-bar" style="width: {{{{ bar_pct }}}}%;"></div></div>
    <div class="cb-cost"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost }}}}</span></div>
  </div>
{{% endfor %}}
</div>
"""


def make_bar_template(label_key: str, label_header: str) -> str:
    return f"""\
<style>
  .cb-bars {{ display: flex; flex-direction: column; gap: 6px; padding: 4px 6px; max-height: 100%; overflow-y: auto; }}
  .cb-bar-head {{
    display: grid;
    grid-template-columns: minmax(110px, 1.4fr) 2fr minmax(90px, 1fr);
    gap: var(--cc-space-s, 8px);
    font-size: 0.78rem; font-weight: 600; opacity: 0.75;
    text-transform: uppercase; letter-spacing: 0.03em;
    padding-bottom: 4px; border-bottom: 1px solid var(--cc-color-border);
  }}
  .cb-bar-head .num {{ text-align: right; }}
  .cb-bar-row {{
    display: grid;
    grid-template-columns: minmax(110px, 1.4fr) 2fr minmax(90px, 1fr);
    gap: var(--cc-space-s, 8px);
    align-items: center; font-size: 0.85rem; padding: 2px 0;
  }}
  .cb-bar-label {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }}
  .cb-bar-track {{
    height: 12px; background: var(--cc-color-border);
    border-radius: var(--cc-radius-s, 4px); overflow: hidden;
  }}
  .cb-bar-fill {{ height: 100%; background: var(--cc-vis-0, #3274D9); min-width: 2px; }}
  .cb-bar-val {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "ECU" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: unit %}}
{{% assign max_cost = rows[0]["cost"].value | default: 1.0 %}}
{{% if max_cost <= 0 %}}{{% assign max_cost = 1.0 %}}{{% endif %}}
<div class="cb-bars">
  <div class="cb-bar-head">
    <div>{label_header}</div>
    <div>Share</div>
    <div class="num">Cost ({{{{ unit }}}})</div>
  </div>
{{% for row in rows %}}
  {{% assign cost = row["cost"].value | default: 0.0 %}}
  {{% assign bar_pct = cost | times: 100.0 | divided_by: max_cost | round: 1 %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
  <div class="cb-bar-row">
    <div class="cb-bar-label" title="{{{{ row["{label_key}"].value }}}}">{{{{ row["{label_key}"].value }}}}</div>
    <div class="cb-bar-track"><div class="cb-bar-fill" style="width: {{{{ bar_pct }}}}%;"></div></div>
    <div class="cb-bar-val"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost }}}}</span></div>
  </div>
{{% endfor %}}
</div>
"""


UNALLOC_TABLE_TEMPLATE = f"""\
<style>
  .cb-tbl-wrap {{ max-height: 100%; overflow: auto; }}
  table.cb-t {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; }}
  table.cb-t th {{
    position: sticky; top: 0; background: var(--cc-color-surface); z-index: 1;
    text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--cc-color-border);
    font-weight: 600; opacity: 0.75; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em;
  }}
  table.cb-t td {{ padding: 6px 8px; border-bottom: 1px solid var(--cc-color-border); }}
  table.cb-t th.num, table.cb-t td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "ECU" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: unit %}}
<div class="cb-tbl-wrap">
<table class="cb-t">
  <thead>
    <tr><th>Category</th><th>Cost Type</th><th>SKU</th><th>Project</th><th class="num">Units (ECU)</th><th class="num">Cost ({{{{ unit }}}})</th></tr>
  </thead>
  <tbody>
  {{% for row in rows %}}
    {{% assign cost = row["cost"].value | default: 0.0 %}}
    {{% assign ecu = row["ecu"].value | default: 0.0 %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
{liquid_money_block("ecu", "fmt_ecu", with_decimals=True)}
    <tr>
      <td><strong>{{{{ row["category"].value }}}}</strong></td>
      <td>{{{{ row["cost_type"].value }}}}</td>
      <td><code>{{{{ row["sku"].value }}}}</code></td>
      <td>{{{{ row["project"].value }}}}</td>
      <td class="num">{{{{ fmt_ecu }}}}</td>
      <td class="num"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost }}}}</span></td>
    </tr>
  {{% endfor %}}
  </tbody>
</table>
</div>
"""


INVOICE_TABLE_TEMPLATE = f"""\
<style>
  .cb-tbl-wrap {{ max-height: 100%; overflow: auto; }}
  table.cb-t {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; }}
  table.cb-t th {{
    position: sticky; top: 0; background: var(--cc-color-surface); z-index: 1;
    text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--cc-color-border);
    font-weight: 600; opacity: 0.75; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em;
  }}
  table.cb-t td {{ padding: 6px 8px; border-bottom: 1px solid var(--cc-color-border); }}
  table.cb-t th.num, table.cb-t td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "ECU" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: unit %}}
<div class="cb-tbl-wrap">
<table class="cb-t">
  <thead>
    <tr><th>Project</th><th>Type</th><th>Category</th><th>Cost Type</th><th>SKU</th><th>Driver</th><th class="num">Quantity</th><th class="num">Units (ECU)</th><th class="num">Cost ({{{{ unit }}}})</th></tr>
  </thead>
  <tbody>
  {{% for row in rows %}}
    {{% assign cost = row["cost"].value | default: 0.0 %}}
    {{% assign ecu = row["ecu"].value | default: 0.0 %}}
    {{% assign qty = row["qty"].value | default: 0.0 %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
{liquid_money_block("ecu", "fmt_ecu", with_decimals=True)}
{liquid_money_block("qty", "fmt_qty", with_decimals=True)}
    <tr>
      <td><strong>{{{{ row["project"].value }}}}</strong></td>
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
  </tbody>
</table>
</div>
"""


DATASTREAM_COST_TABLE_TEMPLATE = f"""\
<style>
  .cb-tbl-wrap {{ max-height: 100%; overflow: auto; }}
  table.cb-t {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; }}
  table.cb-t th {{
    position: sticky; top: 0; background: var(--cc-color-surface); z-index: 1;
    text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--cc-color-border);
    font-weight: 600; opacity: 0.75; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em;
  }}
  table.cb-t td {{ padding: 6px 8px; border-bottom: 1px solid var(--cc-color-border); }}
  table.cb-t th.num, table.cb-t td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "ECU" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: unit %}}
<div class="cb-tbl-wrap">
<table class="cb-t">
  <thead>
    <tr><th>Project</th><th>Type</th><th>Data Stream</th><th class="num">Indexing ({{{{ sym }}}})</th><th class="num">Search ({{{{ sym }}}})</th><th class="num">Storage ({{{{ sym }}}})</th><th class="num">Attributed Cost ({{{{ unit }}}})</th></tr>
  </thead>
  <tbody>
  {{% for row in rows %}}
    {{% assign idx = row["indexing_cost"].value | default: 0.0 %}}
    {{% assign qry = row["query_cost"].value | default: 0.0 %}}
    {{% assign stg = row["storage_cost"].value | default: 0.0 %}}
    {{% assign total = row["attributed_cost"].value | default: 0.0 %}}
{liquid_money_block("idx", "fmt_idx", with_decimals=True)}
{liquid_money_block("qry", "fmt_qry", with_decimals=True)}
{liquid_money_block("stg", "fmt_stg", with_decimals=True)}
{liquid_money_block("total", "fmt_total", with_decimals=True)}
    <tr>
      <td><strong>{{{{ row["project"].value }}}}</strong></td>
      <td>{{{{ row["project_type"].value }}}}</td>
      <td><code>{{{{ row["datastream"].value }}}}</code></td>
      <td class="num"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_idx }}}}</span></td>
      <td class="num"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_qry }}}}</span></td>
      <td class="num"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_stg }}}}</span></td>
      <td class="num"><strong><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_total }}}}</span></strong></td>
    </tr>
  {{% endfor %}}
  </tbody>
</table>
</div>
"""

STATUS_BOARD_TEMPLATE = """\
<style>
  .cb-status { display: flex; flex-direction: column; gap: var(--cc-space-xs); }
  .cb-item {
    display: grid; grid-template-columns: 18px 1.4fr 0.9fr 0.8fr 1.1fr 1.2fr; gap: var(--cc-space-s);
    align-items: center; padding: var(--cc-space-s);
    background: var(--cc-color-surface); border-radius: var(--cc-radius-s);
    border: 1px solid var(--cc-color-border);
  }
  .cb-dot { width: 10px; height: 10px; border-radius: 50%; }
  .cb-ok { background: var(--cc-vis-1, #54b399); }
  .cb-warn { background: var(--cc-vis-8, #da8b45); }
  .cb-err { background: var(--cc-vis-7, #c9404f); }
  .cb-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cb-meta { opacity: 0.8; font-size: 0.85rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
<div class="cb-status">
{% for row in rows %}
  {% assign h = row["health"].value | default: "stale" %}
  <div class="cb-item">
    <div class="cb-dot {% if h == "ok" %}cb-ok{% elsif h == "empty_or_unauthorized" %}cb-err{% else %}cb-warn{% endif %}"></div>
    <div class="cb-name">{{ row["project_name"].value | default: row["project_id"].value }}</div>
    <div class="cb-meta">{{ row["project_type"].value | default: "unknown" }}</div>
    <div class="cb-meta">{{ row["index_count"].value | default: 0 }} indices</div>
    <div class="cb-meta">{{ h }} · day {{ row["day"].value | default: "—" }}</div>
    <div class="cb-meta">{{ row["updated_at"].value | default: "never" | slice: 0, 19 }}</div>
  </div>
{% endfor %}
</div>
"""

ALLOC_TABLE_TEMPLATE = f"""\
<style>
  .cb-tbl-wrap {{ max-height: 100%; overflow: auto; }}
  table.cb-t {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; }}
  table.cb-t th {{
    position: sticky; top: 0; background: var(--cc-color-surface); z-index: 1;
    text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--cc-color-border);
    font-weight: 600; opacity: 0.75; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em;
  }}
  table.cb-t td {{ padding: 6px 8px; border-bottom: 1px solid var(--cc-color-border); }}
  table.cb-t th.num, table.cb-t td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .cb-sym {{ font-weight: 500; opacity: 0.85; margin-right: 2px; }}
  .cb-bar-track {{
    height: 10px; background: var(--cc-color-border);
    border-radius: var(--cc-radius-s); overflow: hidden; min-width: 80px;
  }}
  .cb-bar {{ height: 100%; background: var(--cc-vis-0); min-width: 2px; }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "ECU" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: unit %}}
{{% assign max_cost = rows[0]["cost"].value | default: 1.0 %}}
{{% if max_cost <= 0 %}}{{% assign max_cost = 1.0 %}}{{% endif %}}
<div class="cb-tbl-wrap">
<table class="cb-t">
  <thead>
    <tr><th>Project</th><th>Type</th><th>Cost Type</th><th>Driver</th><th class="num">Cost ({{{{ unit }}}})</th><th>Share</th></tr>
  </thead>
  <tbody>
  {{% for row in rows %}}
    {{% assign cost = row["cost"].value | default: 0.0 %}}
    {{% assign bar_pct = cost | times: 100.0 | divided_by: max_cost | round: 1 %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
    <tr>
      <td><strong>{{{{ row["project"].value }}}}</strong></td>
      <td>{{{{ row["project_type_label"].value }}}}</td>
      <td>{{{{ row["cost_type_label"].value }}}}</td>
      <td>{{{{ row["scope"].value }}}}</td>
      <td class="num"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost }}}}</span></td>
      <td><div class="cb-bar-track"><div class="cb-bar" style="width: {{{{ bar_pct }}}}%;"></div></div></td>
    </tr>
  {{% endfor %}}
  </tbody>
</table>
</div>
"""


def control_group() -> dict:
    controls = [
        (stable_uuid("ctrl-project-type"), "deployment_type", "Project type", 0),
        (stable_uuid("ctrl-project-name"), "deployment_name", "Project name", 1),
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
) -> dict:
    attrs: dict = {
        "description": description,
        "kibanaSavedObjectMeta": {
            "searchSourceJSON": {"filter": [], "query": {"language": "kuery", "query": ""}}
        },
        "optionsJSON": {
            "hidePanelTitles": False,
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
    refs = list(references)
    if with_controls:
        cg = control_group()
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
| STATS total_spend = SUM(cost), allocatable_spend = SUM(alloc_cost), unallocatable_spend = SUM(unalloc_cost), project_count = COUNT_DISTINCT(project_id), latest_day = TO_STRING(MAX(ts)), currency_symbol = MAX(currency_symbol), unit = MAX(unit)""",
        0,
        2,
        48,
        6,
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-billing-project"),
        "Cost by project",
        make_bar_template("project", "Project"),
        f"""{COST_PRELUDE}
| STATS cost = SUM(cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY project
| SORT cost DESC
| LIMIT 25
| KEEP project, cost, currency_symbol, unit""",
        0,
        8,
        24,
        14,
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-billing-type"),
        "Cost by cost category",
        make_bar_template("category", "Cost category"),
        f"""{COST_PRELUDE}
| STATS cost = SUM(cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY category_label
| RENAME category_label AS category
| SORT cost DESC
| LIMIT 25
| KEEP category, cost, currency_symbol, unit""",
        24,
        8,
        24,
        14,
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-billing-sankey"),
        "Spend flow (Cost category → Cost type → Project type)",
        SANKEY_TEMPLATE,
        f"""{COST_PRELUDE}
| STATS cost = SUM(cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY category_label, cost_type_label, project_type_label
| SORT cost DESC
| LIMIT 25
| RENAME category_label AS category, cost_type_label AS cost_type, project_type_label AS project_type
| KEEP category, cost_type, project_type, cost, currency_symbol, unit""",
        0,
        22,
        28,
        14,
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-billing-unallocatable"),
        "Unallocatable & pass-through charges",
        UNALLOC_TABLE_TEMPLATE,
        f"""{COST_PRELUDE}
| WHERE COALESCE(is_allocatable, false) == false OR allocation_driver == "none"
| STATS cost = SUM(cost), ecu = SUM(units), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY category_label, cost_type_label, sku, project
| RENAME category_label AS category, cost_type_label AS cost_type
| SORT cost DESC
| LIMIT 50
| KEEP category, cost_type, sku, project, ecu, cost, currency_symbol, unit""",
        28,
        22,
        20,
        14,
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-billing-table"),
        "Invoice reconciliation detail",
        INVOICE_TABLE_TEMPLATE,
        f"""{COST_PRELUDE}
| STATS qty = SUM(COALESCE(quantity_value, 0.0)), ecu = SUM(units), cost = SUM(cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY project, project_type_label, category_label, cost_type_label, sku, allocation_driver
| RENAME project_type_label AS project_type, category_label AS category, cost_type_label AS cost_type, allocation_driver AS driver
| SORT cost DESC
| LIMIT 200
| KEEP project, project_type, category, cost_type, sku, driver, qty, ecu, cost, currency_symbol, unit""",
        0,
        36,
        48,
        14,
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

    p, _ = custom_panel(
        stable_uuid("cc-usage-kpi"),
        "Workload Pool Summary",
        USAGE_KPI_TEMPLATE,
        f"""{COST_PRELUDE}
| WHERE COALESCE(is_allocatable, false) == true AND allocation_driver != "none"
| EVAL idx_c = CASE(cost_type_label IN ("ingest-vcu", "ingest"), cost, 0.0)
| EVAL qry_c = CASE(cost_type_label IN ("search-vcu", "ml-vcu"), cost, 0.0)
| EVAL stg_c = CASE(cost_type_label == "retained", cost, 0.0)
| STATS pool_spend = SUM(cost), indexing_spend = SUM(idx_c), query_spend = SUM(qry_c), storage_spend = SUM(stg_c), currency_symbol = MAX(currency_symbol), unit = MAX(unit)""",
        0,
        2,
        48,
        6,
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-usage-driver"),
        "Allocatable cost by workload driver",
        make_bar_template("driver", "Workload driver"),
        f"""{COST_PRELUDE}
| WHERE COALESCE(is_allocatable, false) == true AND allocation_driver != "none"
| EVAL driver = CASE(cost_type_label IN ("ingest-vcu", "ingest"), "Indexing (ingest-vcu + ingest)", cost_type_label IN ("search-vcu", "ml-vcu"), "Search & Query (search-vcu + ml-vcu)", cost_type_label == "retained", "Storage (retained)", cost_type_label)
| STATS cost = SUM(cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY driver
| SORT cost DESC
| LIMIT 25
| KEEP driver, cost, currency_symbol, unit""",
        0,
        8,
        24,
        14,
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
        8,
        24,
        14,
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-usage-datastream-cost"),
        "Data stream cost attribution",
        DATASTREAM_COST_TABLE_TEMPLATE,
        f"""FROM {DS_CONTRIB}
| EVAL ts = @timestamp, ds_project_name = CASE(project_name IS NOT NULL AND project_name != "", project_name, cluster_name IS NOT NULL AND cluster_name != "", cluster_name, "unknown"), ds_project_type = COALESCE(project_type, "unknown")
| LOOKUP JOIN {DEP_CONTRIB} ON composite_key
| LOOKUP JOIN {POOL} ON composite_key
| LOOKUP JOIN {CONF} ON ts >= conf_start_date AND ts <= conf_end_date
| EVAL rate = COALESCE(conf_chargeable_unit_rate, 1.0)
| EVAL unit = COALESCE(conf_chargeable_unit_rate_unit, "ECU")
| EVAL currency_symbol = CASE(
    conf_currency_symbol IS NOT NULL AND conf_currency_symbol != "", conf_currency_symbol,
    conf_chargeable_unit_rate_unit == "ZAR", "R",
    conf_chargeable_unit_rate_unit == "USD", "$",
    conf_chargeable_unit_rate_unit == "EUR", "€",
    conf_chargeable_unit_rate_unit == "GBP", "£",
    conf_chargeable_unit_rate_unit == "JPY", "¥",
    conf_chargeable_unit_rate_unit == "CNY", "¥",
    conf_chargeable_unit_rate_unit == "INR", "₹",
    conf_chargeable_unit_rate_unit == "CAD", "CA$",
    conf_chargeable_unit_rate_unit == "AUD", "A$",
    conf_chargeable_unit_rate_unit == "CHF", "CHF",
    conf_chargeable_unit_rate_unit == "BRL", "R$",
    conf_chargeable_unit_rate_unit IS NOT NULL AND conf_chargeable_unit_rate_unit != "", conf_chargeable_unit_rate_unit,
    "ECU"
  )
| EVAL idx_share = CASE(COALESCE(deployment_sum_indexing_time, 0) > 0, TO_DOUBLE(datastream_sum_indexing_time) / TO_DOUBLE(deployment_sum_indexing_time), 0.0)
| EVAL qry_share = CASE(COALESCE(deployment_sum_query_time, 0) > 0, TO_DOUBLE(datastream_sum_query_time) / TO_DOUBLE(deployment_sum_query_time), 0.0)
| EVAL stg_share = CASE(COALESCE(deployment_sum_data_set_store_size, 0) > 0, TO_DOUBLE(datastream_sum_data_set_store_size) / TO_DOUBLE(deployment_sum_data_set_store_size), 0.0)
| EVAL idx_cost = COALESCE(indexing_ecu, 0.0) * rate * idx_share
| EVAL qry_cost = COALESCE(query_ecu, 0.0) * rate * qry_share
| EVAL stg_cost = COALESCE(storage_ecu, 0.0) * rate * stg_share
| EVAL total_ds_cost = idx_cost + qry_cost + stg_cost
| STATS indexing_cost = SUM(idx_cost), query_cost = SUM(qry_cost), storage_cost = SUM(stg_cost), attributed_cost = SUM(total_ds_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY ds_project_name, ds_project_type, datastream
| RENAME ds_project_name AS project, ds_project_type AS project_type
| SORT attributed_cost DESC
| LIMIT 200
| KEEP project, project_type, datastream, indexing_cost, query_cost, storage_cost, attributed_cost, currency_symbol, unit""",
        0,
        22,
        48,
        14,
    )
    panels.append(p)

    esql = f"""FROM {DS_CONTRIB}
| EVAL `Project` = CASE(project_name IS NOT NULL AND project_name != "", project_name, cluster_name IS NOT NULL AND cluster_name != "", cluster_name, "unknown"), `Project type` = COALESCE(project_type, "unknown")
| STATS `Indexing time (ms)` = SUM(datastream_sum_indexing_time), `Search query time (ms)` = SUM(datastream_sum_query_time), `Avg dataset size (MB)` = ROUND(AVG(TO_DOUBLE(datastream_sum_data_set_store_size)) / 1048576.0, 2) BY `Project`, `Project type`, datastream
| RENAME datastream AS `Data stream`
| SORT `Indexing time (ms)` DESC
| LIMIT 200"""
    p, _ = lens_esql(
        stable_uuid("lens-usage-datastream-metrics"),
        "Data stream harvested usage metrics (_cat/indices deltas)",
        esql,
        [
            col("Project", "keyword"),
            col("Project type", "keyword"),
            col("Data stream", "keyword"),
            col("Indexing time (ms)", "long"),
            col("Search query time (ms)", "long"),
            col("Avg dataset size (MB)", "double"),
        ],
        "lnsDatatable",
        table_vis(
            [
                "Project",
                "Project type",
                "Data stream",
                "Indexing time (ms)",
                "Search query time (ms)",
                "Avg dataset size (MB)",
            ]
        ),
        0,
        36,
        48,
        14,
        index=DS_CONTRIB,
    )
    panels.append(p)

    return dashboard_doc(
        DASH_USAGE,
        TITLE_USAGE,
        "Attribute project workload pools (Indexing, Search, Storage) to individual data streams using harvested _cat/indices usage.",
        panels,
        refs,
        with_controls=True,
    )


def build_config() -> dict:
    panels: list = []
    refs: list = []
    panel_id = stable_uuid("links-config")
    p, r = links_panel(panel_id, DASH_CONFIG)
    panels.append(p)
    refs.extend(r)

    config_dsl = """\
<style>
  .cb-dsl { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: var(--cc-space-m); font-size: 0.85rem; }
  .cb-dsl h4 { margin: 0 0 4px 0; font-size: 0.9rem; font-weight: 600; }
  .cb-dsl p { margin: 0 0 6px 0; font-size: 0.75rem; color: var(--cc-color-text-secondary); }
  .cb-dsl pre {
    background: var(--cc-color-surface);
    border: 1px solid var(--cc-color-border);
    border-radius: var(--cc-radius-s);
    padding: var(--cc-space-s);
    margin: 0;
    font-family: monospace;
    font-size: 0.74rem;
    overflow-x: auto;
  }
</style>
{% assign r = rows[0] %}
<div class="cb-dsl">
  <div>
    <h4>1. Currency Rate Window (Dev Tools)</h4>
    <p>Set active rate, currency, and date bounds in chargeback_conf_lookup.</p>
    <pre>PUT chargeback_conf_lookup/_doc/config
{
  "@timestamp": "1970-01-01T00:00:00.000Z",
  "config_join_key": "config",
  "conf_start_date": "{{ r["conf_start_date"].value | default: "2020-01-01T00:00:00.000Z" }}",
  "conf_end_date": "{{ r["conf_end_date"].value | default: "2099-12-31T23:59:59.999Z" }}",
  "conf_chargeable_unit_rate": {{ r["conf_chargeable_unit_rate"].value | default: 1.0 }},
  "conf_chargeable_unit_rate_unit": "{{ r["conf_chargeable_unit_rate_unit"].value | default: "USD" }}",
  "conf_currency_symbol": "{{ r["conf_currency_symbol"].value | default: "$" }}"
}</pre>
  </div>
  <div>
    <h4>2. Remote Least-Privilege Key (Dev Tools)</h4>
    <p>Run on remote project. Grants index metadata read only (no data access).</p>
    <pre>POST _security/api_key
{
  "name": "chargeback-usage-harvesting",
  "role_descriptors": {
    "chargeback_harvest": {
      "cluster": ["monitor"],
      "indices": [
        {
          "names": ["*"],
          "privileges": ["view_index_metadata"]
        }
      ]
    }
  }
}</pre>
  </div>
  <div>
    <h4>3. Register Remote Key on Hub (Dev Tools)</h4>
    <p>Run on Hub to register encoded key for remote project ID.</p>
    <pre>POST chargeback-serverless-config/_update/config
{
  "doc": {
    "project_api_keys": {
      "&lt;REMOTE_PROJECT_ID&gt;": "&lt;ENCODED_API_KEY&gt;"
    }
  }
}</pre>
  </div>
</div>
"""
    p, _ = custom_panel(
        stable_uuid("cc-config-help"),
        "Dev Tools DSL — Configuration",
        config_dsl,
        f"""FROM {CONF}
| KEEP conf_start_date, conf_end_date, conf_chargeable_unit_rate, conf_chargeable_unit_rate_unit, conf_currency_symbol
| SORT conf_start_date DESC
| LIMIT 1""",
        0,
        2,
        48,
        13,
    )
    panels.append(p)

    esql = f"""FROM {CONF}
| KEEP conf_start_date, conf_end_date, conf_chargeable_unit_rate, conf_chargeable_unit_rate_unit, conf_currency_symbol
| SORT conf_start_date DESC"""
    p, _ = lens_esql(
        stable_uuid("lens-config-rates"),
        "Configured currency rates by date window",
        esql,
        [
            col("conf_start_date", "date", "Start date"),
            col("conf_end_date", "date", "End date"),
            col("conf_chargeable_unit_rate", "double", "Unit rate"),
            col("conf_chargeable_unit_rate_unit", "keyword", "Currency"),
            col("conf_currency_symbol", "keyword", "Symbol"),
        ],
        "lnsDatatable",
        table_vis(
            [
                "conf_start_date",
                "conf_end_date",
                "conf_chargeable_unit_rate",
                "conf_chargeable_unit_rate_unit",
                "conf_currency_symbol",
            ]
        ),
        0,
        13,
        20,
        10,
        index=CONF,
        time_field="conf_start_date",
    )
    panels.append(p)

    esql = f"""FROM {PROJECTS}
| KEEP project_name, project_id, project_type, optimized_for, es_url
| RENAME project_name AS `Project`, project_id AS `Project ID`, project_type AS `Type`, optimized_for AS `Optimized for`, es_url AS `Elasticsearch endpoint`
| SORT `Project` ASC
| LIMIT 200"""
    p, _ = lens_esql(
        stable_uuid("lens-config-projects"),
        "Discovered Serverless projects registry",
        esql,
        [
            col("Project", "keyword"),
            col("Project ID", "keyword"),
            col("Type", "keyword"),
            col("Optimized for", "keyword"),
            col("Elasticsearch endpoint", "keyword"),
        ],
        "lnsDatatable",
        table_vis(
            [
                "Project",
                "Project ID",
                "Type",
                "Optimized for",
                "Elasticsearch endpoint",
            ]
        ),
        20,
        13,
        28,
        10,
        index=PROJECTS,
        time_field="updated_at",
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-config-state"),
        "Project usage harvest status",
        STATUS_BOARD_TEMPLATE,
        f"""FROM {STATE}
| SORT updated_at DESC NULLS LAST
| LIMIT 100
| EVAL health = CASE(COALESCE(last_status, "") == "empty_or_unauthorized", "empty_or_unauthorized", updated_at IS NOT NULL AND updated_at > NOW() - 2 hours, "ok", "stale")
| KEEP project_id, project_name, project_type, index_count, health, day, updated_at""",
        0,
        23,
        48,
        14,
    )
    panels.append(p)

    return dashboard_doc(
        DASH_CONFIG,
        TITLE_CONFIG,
        "Configure currency rates via Dev Tools DSL, verify discovered Serverless projects, and monitor hourly usage harvest health.",
        panels,
        refs,
        with_controls=False,
    )


def alert_template(alert_id: str, name: str, esql: str, interval: str = "1h") -> dict:
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
                "timeWindowSize": 1,
                "timeWindowUnit": "d",
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
        alert_template(
            ALERT_UNALLOCATED,
            "[Chargeback Serverless] Unallocated cost present",
            f"""{COST_PRELUDE}
| WHERE (COALESCE(is_allocatable, false) == false OR allocation_driver == "none") AND cost > 0
| STATS unallocated = ROUND(SUM(cost), 2), sym = COALESCE(MAX(currency_symbol), "ECU"), unit = COALESCE(MAX(unit), "ECU"), rows = COUNT(*)
| WHERE unallocated > 0
| EVAL result = CONCAT("Unallocated cost: ", COALESCE(sym, unit, "ECU"), " ", TO_STRING(unallocated), " (", COALESCE(unit, "ECU"), ") across ", TO_STRING(rows), " billing rows.")
| KEEP result""",
            "6h",
        ),
        alert_template(
            ALERT_STALE_STATE,
            "[Chargeback Serverless] Stale project usage harvest",
            f"""FROM {STATE}
| WHERE COALESCE(last_status, "") != "ok" OR updated_at IS NULL OR updated_at < NOW() - 6 hours
| EVAL result = CONCAT("Project `", COALESCE(project_name, project_id, "unknown"), "` (", COALESCE(project_id, "unknown"), ") status=", COALESCE(last_status, "stale"), " last updated ", COALESCE(TO_STRING(updated_at), "never"), ".")
| STATS result = VALUES(result)
| MV_EXPAND result""",
            "1h",
        ),
        alert_template(
            ALERT_MISSING_PROJECTS,
            "[Chargeback Serverless] Billed project missing from registry",
            f"""FROM {BILLING}
| WHERE deployment_id IS NOT NULL AND deployment_id != "" AND (deployment_name IS NULL OR deployment_name == "" OR deployment_name == deployment_id)
| STATS ecu = ROUND(SUM(COALESCE(total_ecu, 0.0)), 2) BY deployment_id, deployment_type
| EVAL result = CONCAT("Billed project `", deployment_id, "` (", COALESCE(deployment_type, "unknown"), ", ", TO_STRING(ecu), " ECU) has no resolved project_name from chargeback-serverless-projects.")
| STATS result = VALUES(result)
| MV_EXPAND result""",
            "12h",
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
        f("type", "string", "keyword"),
        f("kind", "string", "keyword"),
        f("project_id", "string", "keyword"),
        f("project_name", "string", "keyword"),
        f("project_type", "string", "keyword"),
        f("deployment_id", "string", "keyword"),
        f("deployment_name", "string", "keyword"),
        f("deployment_type", "string", "keyword"),
        f("deployment_group", "string", "keyword"),
        f("cost_category", "string", "keyword"),
        f("cost_type", "string", "keyword"),
        f("allocation_driver", "string", "keyword"),
        f("is_allocatable", "boolean", "boolean"),
        f("quantity_value", "number", "double"),
        f("total_ecu", "number", "double"),
        f("total_chargeable_units", "number", "double"),
    ]
    return {
        "attributes": {
            "allowHidden": True,
            "fieldAttrs": "{}",
            "fieldFormatMap": "{}",
            "fields": json.dumps(fields),
            "name": "[Chargeback Serverless] Billing Cluster Cost",
            "runtimeFieldMap": "{}",
            "sourceFilters": "[]",
            "timeFieldName": "@timestamp",
            "title": BILLING,
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

    dashboards = (build_billing(), build_usage(), build_config())
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
