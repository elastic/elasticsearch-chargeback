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

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KIBANA = REPO_ROOT / "integration" / "assets" / "chargeback_serverless" / "kibana"
KIBANA = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_KIBANA

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
AI_CONTRIB = "cluster_ai_model_contribution_lookup"
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
    time_field: str | None = "@timestamp",
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


PAGED_BASE_CSS = """\
  .cb-paged-wrap {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    height: 100%;
    min-height: 0;
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
    border-top: 1px solid var(--cc-color-border, #e0e0e0);
    font-size: 0.76rem;
    color: var(--cc-color-text, #333);
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
    border-radius: 4px;
    border: 1px solid var(--cc-color-border, #ccc);
    background: var(--cc-color-surface, #fff);
    color: var(--cc-color-text, #333);
    cursor: pointer;
    font-size: 0.75rem;
    font-weight: 500;
    text-decoration: none;
    box-sizing: border-box;
    transition: background 0.15s, border-color 0.15s;
    user-select: none;
  }
  .cb-pg-btn:hover {
    background: var(--cc-color-hover, #f0f0f0);
    border-color: var(--cc-color-primary, #0077cc);
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
            f"  #{pfx}_pg_{i}:checked ~ .cb-pg-bar-{pfx} .cb-btn-{pfx}-{i} {{ background: var(--cc-color-primary, #0077cc); color: #ffffff !important; border-color: var(--cc-color-primary, #0077cc); pointer-events: none; font-weight: 700; }}"
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
    <div class="cb-kpi-label">Days of Data</div>
    <div class="cb-kpi-value">{{{{ r["days_count"].value | default: 0 }}}}</div>
    <div class="cb-kpi-sub">In selected time range</div>
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
{PAGED_BASE_CSS}
{paged_css("sankey", 6, "grid")}
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
{paged_top("sankey", 10, 6)}
<div class="cb-flow">
{{% for row in rows %}}
  {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
  {{% assign cost = row["cost"].value | default: 0.0 %}}
  {{% assign bar_pct = cost | times: 100.0 | divided_by: max_cost | round: 1 %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
  <div class="cb-row cb-pg-row-sankey cb-pg-sankey-{{{{ _row_pg }}}}">
    <div class="cb-stage"><strong>{{{{ row["category"].value }}}}</strong></div>
    <div class="cb-stage">→ {{{{ row["cost_type"].value }}}}</div>
    <div class="cb-stage">→ {{{{ row["project_type"].value }}}}</div>
    <div class="cb-bar-track"><div class="cb-bar" style="width: {{{{ bar_pct }}}}%;"></div></div>
    <div class="cb-cost"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost }}}}</span></div>
  </div>
{{% endfor %}}
</div>
{paged_bottom("sankey")}
"""


AI_KPIS_TEMPLATE = f"""\
<style>
  .cb-ai-kpis {{
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: var(--cc-space-m, 16px);
    font-family: inherit;
  }}
  .cb-ai-card {{
    background: var(--cc-color-surface, #ffffff);
    border: 1px solid var(--cc-color-border, #d3dae6);
    border-radius: var(--cc-radius-m, 8px);
    padding: var(--cc-space-m, 16px);
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  }}
  .cb-ai-label {{
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--cc-color-text-secondary, #69707d);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 8px;
  }}
  .cb-ai-val {{
    font-size: 1.65rem;
    font-weight: 700;
    line-height: 1.2;
    color: var(--cc-color-text, #1a1c21);
    font-variant-numeric: tabular-nums;
  }}
  .cb-ai-val .sym {{
    font-size: 1.1rem;
    font-weight: 500;
    opacity: 0.75;
    margin-right: 2px;
  }}
  .cb-ai-sub {{
    font-size: 0.78rem;
    color: var(--cc-color-text-secondary, #69707d);
    margin-top: 8px;
    display: flex;
    align-items: center;
    gap: 4px;
  }}
  .cb-ai-pill {{
    display: inline-block;
    padding: 1px 6px;
    border-radius: 10px;
    font-size: 0.7rem;
    font-weight: 600;
    background: var(--cc-color-primary-subdued, #e8f2fe);
    color: var(--cc-color-primary, #006bb4);
  }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "ECU" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: unit %}}
{{% assign ai_spend = rows[0]["ai_spend"].value | default: 0.0 %}}
{{% assign inf_count = rows[0]["total_inferences"].value | default: 0 %}}
{{% assign avg_latency = rows[0]["avg_latency_ms"].value | default: 0.0 | round: 1 %}}
{{% assign cost_per_k = rows[0]["cost_per_1k"].value | default: 0.0 %}}

{liquid_money_block("ai_spend", "fmt_ai_spend", with_decimals=True)}
{liquid_money_block("inf_count", "fmt_inf_count", with_decimals=False)}
{liquid_money_block("cost_per_k", "fmt_cost_per_k", with_decimals=True)}

<div class="cb-ai-kpis">
  <div class="cb-ai-card">
    <div class="cb-ai-label">Total AI &amp; ML Spend</div>
    <div class="cb-ai-val"><span class="sym">{{{{ sym }}}}</span>{{{{ fmt_ai_spend }}}}</div>
    <div class="cb-ai-sub"><span class="cb-ai-pill">ml-vcu + inference</span> attributable</div>
  </div>
  <div class="cb-ai-card">
    <div class="cb-ai-label">Inference Volume</div>
    <div class="cb-ai-val">{{{{ fmt_inf_count }}}}</div>
    <div class="cb-ai-sub">Total model inference calls</div>
  </div>
  <div class="cb-ai-card">
    <div class="cb-ai-label">Avg Model Latency</div>
    <div class="cb-ai-val">{{{{ avg_latency }}}} <span style="font-size: 1rem; font-weight: 500;">ms</span></div>
    <div class="cb-ai-sub">Compute execution speed</div>
  </div>
  <div class="cb-ai-card">
    <div class="cb-ai-label">Unit Cost (1k Inferences)</div>
    <div class="cb-ai-val"><span class="sym">{{{{ sym }}}}</span>{{{{ fmt_cost_per_k }}}}</div>
    <div class="cb-ai-sub">Effective unit economics</div>
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
    grid-template-columns: minmax(140px, 1.8fr) 110px 2fr 90px 110px;
    gap: var(--cc-space-s, 8px);
    font-size: 0.76rem;
    font-weight: 600;
    color: var(--cc-color-text-secondary, #69707d);
    text-transform: uppercase;
    letter-spacing: 0.03em;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--cc-color-border, #d3dae6);
  }}
  .cb-model-head .num {{ text-align: right; }}
  .cb-model-row {{
    display: grid;
    grid-template-columns: minmax(140px, 1.8fr) 110px 2fr 90px 110px;
    gap: var(--cc-space-s, 8px);
    align-items: center;
    font-size: 0.85rem;
    padding: 6px 0;
    border-bottom: 1px dashed var(--cc-color-border, #e7ebf0);
  }}
  .cb-model-name {{
    font-weight: 600;
    color: var(--cc-color-text, #1a1c21);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    display: flex;
    align-items: center;
    gap: 6px;
  }}
  .cb-model-tag {{
    display: inline-block;
    padding: 1px 6px;
    border-radius: 4px;
    font-size: 0.7rem;
    font-weight: 500;
    background: var(--cc-color-lightShade, #f0f3f6);
    color: var(--cc-color-text-secondary, #535966);
    text-transform: uppercase;
  }}
  .cb-track {{
    height: 12px;
    background: var(--cc-color-border, #e7ebf0);
    border-radius: 6px;
    overflow: hidden;
  }}
  .cb-fill {{
    height: 100%;
    background: linear-gradient(90deg, #1b85b8, #5a9e6f);
    border-radius: 6px;
  }}
  .cb-num {{
    text-align: right;
    font-variant-numeric: tabular-nums;
  }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "ECU" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: unit %}}
{{% assign max_time = rows[0]["total_compute"].value | default: 1.0 %}}
{{% if max_time <= 0 %}}{{% assign max_time = 1.0 %}}{{% endif %}}
{{% assign tot_compute = 0.0 %}}
{{% for r in rows %}}
  {{% assign tc = r["total_compute"].value | default: 0 %}}
  {{% assign tot_compute = tot_compute | plus: tc %}}
{{% endfor %}}

{paged_top("aimod", 10, 6)}
<div class="cb-model-list">
  <div class="cb-model-head">
    <div>Model Name</div>
    <div>Task Type</div>
    <div>Compute Allocation Share</div>
    <div class="num">Inferences</div>
    <div class="num">Attributed Cost</div>
  </div>
{{% for r in rows %}}
  {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
  {{% assign c_time = r["total_compute"].value | default: 0 %}}
  {{% assign bar_w = c_time | times: 100.0 | divided_by: max_time | round: 1 %}}
  {{% assign calls = r["total_inf"].value | default: 0 %}}
  {{% assign cost = r["attributed_cost"].value | default: 0.0 %}}
  {{% if cost <= 0.0 and tot_compute > 0 %}}
    {{% assign p_cost = r["ai_pool"].value | default: 0.0 %}}
    {{% assign cost = c_time | times: p_cost | divided_by: tot_compute %}}
  {{% endif %}}
  {liquid_money_block("calls", "fmt_calls", with_decimals=False)}
  {liquid_money_block("cost", "fmt_cost", with_decimals=True)}
  <div class="cb-model-row cb-pg-row-aimod cb-pg-aimod-{{{{ _row_pg }}}}">
    <div class="cb-model-name" title="{{{{ r[\"model_id\"].value }}}}">
      <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:#1b85b8;"></span>
      {{{{ r["model_id"].value }}}}
    </div>
    <div><span class="cb-model-tag">{{{{ r["task_type"].value | default: "inference" }}}}</span></div>
    <div>
      <div class="cb-track">
        <div class="cb-fill" style="width: {{{{ bar_w }}}}%;"></div>
      </div>
    </div>
    <div class="cb-num">{{{{ fmt_calls }}}}</div>
    <div class="cb-num" style="font-weight: 600;"><span style="font-size:0.8rem; opacity:0.8;">{{{{ sym }}}}</span> {{{{ fmt_cost }}}}</div>
  </div>
{{% endfor %}}
</div>
{paged_bottom("aimod")}
"""


DAILY_PROJECT_COST_TEMPLATE = f"""\
{{% assign _total_rows = rows.size | default: 0 %}}
{{% if _total_rows > 0 %}}
<style>
  .cb-hist-wrap {{
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 250px;
    font-family: inherit;
    box-sizing: border-box;
    padding: 10px 14px 14px 14px;
    overflow: hidden;
  }}
  .cb-hist-head {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 12px;
  }}
  .cb-hist-legend {{
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    font-size: 0.78rem;
  }}
  .cb-legend-item {{
    display: flex;
    align-items: center;
    gap: 5px;
    font-weight: 500;
    color: var(--cc-color-text, #1a1c21);
  }}
  .cb-legend-dot {{
    width: 9px;
    height: 9px;
    border-radius: 2px;
    display: inline-block;
  }}
  .cb-hist-body {{
    display: flex;
    flex: 1;
    position: relative;
    min-height: 180px;
    gap: 10px;
  }}
  .cb-hist-y {{
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    font-size: 0.72rem;
    color: var(--cc-color-text-secondary, #69707d);
    text-align: right;
    padding-right: 8px;
    min-width: 55px;
    border-right: 1px solid var(--cc-color-border, #d3dae6);
    user-select: none;
    margin-bottom: 26px;
  }}
  .cb-hist-plot {{
    display: flex;
    flex: 1;
    overflow-x: auto;
    position: relative;
  }}
  .cb-hist-canvas {{
    display: flex;
    align-items: flex-end;
    gap: 12px;
    min-width: 100%;
    height: 100%;
    position: relative;
    padding-bottom: 26px;
    box-sizing: border-box;
  }}
  .cb-hist-grid {{
    position: absolute;
    top: 0;
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
    background: var(--cc-color-border, #e7ebf0);
  }}
  .cb-grid-line-zero {{
    background: var(--cc-color-border, #d3dae6);
  }}
  .cb-hist-col {{
    display: flex;
    flex-direction: column;
    align-items: center;
    flex: 1;
    min-width: 44px;
    max-width: 72px;
    height: 100%;
    justify-content: flex-end;
    position: relative;
    z-index: 1;
  }}
  .cb-col-tip {{
    font-size: 0.68rem;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: var(--cc-color-text-secondary, #69707d);
    margin-bottom: 4px;
    white-space: nowrap;
  }}
  .cb-hist-stack {{
    display: flex;
    flex-direction: column-reverse;
    width: 100%;
    height: 100%;
    border-radius: 4px 4px 0 0;
    overflow: hidden;
  }}
  .cb-hist-seg {{
    width: 100%;
    transition: opacity 0.15s ease-in-out;
    cursor: pointer;
  }}
  .cb-hist-seg:hover {{
    filter: brightness(1.15);
    opacity: 0.9;
  }}
  .cb-hist-date {{
    position: absolute;
    bottom: -22px;
    font-size: 0.72rem;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
    color: var(--cc-color-text-secondary, #69707d);
    white-space: nowrap;
  }}
</style>
{{% assign unit = rows[0]["unit"].value | default: "ECU" %}}
{{% assign sym = rows[0]["currency_symbol"].value | default: unit %}}
{{% assign colors = "#3274D9,#54B399,#E0752F,#8E63CE,#00BFB3,#F2BE42,#FA6E6E,#1B85B8,#11A987,#46B3A4" | split: "," %}}

{{% assign proj_str = "" %}}
{{% assign max_day_cost = 1.0 %}}
{{% for r in rows %}}
  {{% assign pn = r["project"].value | default: "unknown" %}}
  {{% unless proj_str contains pn %}}
    {{% assign proj_str = proj_str | append: pn | append: "||" %}}
  {{% endunless %}}
  {{% assign dc = r["day_cost"].value | default: 0.0 %}}
  {{% if dc > max_day_cost %}}
    {{% assign max_day_cost = dc %}}
  {{% endif %}}
{{% endfor %}}
{{% assign projects = proj_str | split: "||" %}}

{{% assign half_cost = max_day_cost | times: 0.5 %}}
{liquid_money_block("max_day_cost", "fmt_max_cost", with_decimals=True)}
{liquid_money_block("half_cost", "fmt_half_cost", with_decimals=True)}

<div class="cb-hist-wrap">
  <div class="cb-hist-head">
    <div class="cb-hist-legend">
      {{% for p in projects %}}
        {{% if p != "" %}}
          {{% assign c_idx = forloop.index0 | modulo: 10 %}}
          <div class="cb-legend-item">
            <span class="cb-legend-dot" style="background: {{{{ colors[c_idx] }}}};"></span>
            <span>{{{{ p }}}}</span>
          </div>
        {{% endif %}}
      {{% endfor %}}
    </div>
  </div>
  <div class="cb-hist-body">
    <div class="cb-hist-y">
      <div>{{{{ sym }}}}{{{{ fmt_max_cost }}}}</div>
      <div>{{{{ sym }}}}{{{{ fmt_half_cost }}}}</div>
      <div>0</div>
    </div>
    <div class="cb-hist-plot">
      <div class="cb-hist-canvas">
        <div class="cb-hist-grid">
          <div class="cb-grid-line"></div>
          <div class="cb-grid-line"></div>
          <div class="cb-grid-line cb-grid-line-zero"></div>
        </div>
        {{% assign cur_bucket = "" %}}
        {{% for r in rows %}}
          {{% assign b_raw = r["day"].value | default: "" %}}
          {{% if b_raw != cur_bucket %}}
            {{% if cur_bucket != "" %}}
                </div>
                <div class="cb-hist-date">{{{{ cur_label }}}}</div>
              </div>
            {{% endif %}}
            {{% assign cur_bucket = b_raw %}}
            {{% assign time_sub = cur_bucket | slice: 11, 8 %}}
            {{% if time_sub == "00:00:00" or time_sub == "" %}}
              {{% assign cur_label = cur_bucket | slice: 5, 5 %}}
              {{% assign cur_tip = cur_bucket | slice: 0, 10 %}}
            {{% else %}}
              {{% assign cur_label = cur_bucket | slice: 11, 5 %}}
              {{% assign cur_tip = cur_bucket | slice: 0, 16 | replace: "T", " " %}}
            {{% endif %}}
            {{% assign d_cost = r["day_cost"].value | default: 0.0 %}}
            {liquid_money_block("d_cost", "fmt_d_cost", with_decimals=True)}
            <div class="cb-hist-col" title="{{{{ cur_tip }}}}: {{{{ sym }}}}{{{{ fmt_d_cost }}}}">
              <div class="cb-col-tip">{{{{ sym }}}}{{{{ fmt_d_cost }}}}</div>
              <div class="cb-hist-stack">
          {{% endif %}}
          {{% assign p_cost = r["cost"].value | default: 0.0 %}}
          {{% assign p_pct = p_cost | times: 100.0 | divided_by: max_day_cost | round: 2 %}}
          {{% if p_pct < 0.5 and p_cost > 0 %}}{{% assign p_pct = 0.5 %}}{{% endif %}}
          {{% assign p_name = r["project"].value | default: "unknown" %}}
          {{% assign p_color = "#3274D9" %}}
          {{% for p in projects %}}
            {{% if p == p_name %}}
              {{% assign c_idx = forloop.index0 | modulo: 10 %}}
              {{% assign p_color = colors[c_idx] %}}
            {{% endif %}}
          {{% endfor %}}
          {liquid_money_block("p_cost", "fmt_p_cost", with_decimals=True)}
          <div class="cb-hist-seg" style="height: {{{{ p_pct }}}}%; background: {{{{ p_color }}}};" title="{{{{ p_name }}}}: {{{{ sym }}}}{{{{ fmt_p_cost }}}} ({{{{ cur_tip }}}})"></div>
        {{% endfor %}}
        {{% if cur_bucket != "" %}}
            </div>
            <div class="cb-hist-date">{{{{ cur_label }}}}</div>
          </div>
        {{% endif %}}
      </div>
    </div>
  </div>
</div>
{{% endif %}}
"""


def make_bar_template(label_key: str, label_header: str) -> str:
    pfx = f"bar_{label_key}"
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
{paged_top(pfx, 10, 8)}
<div class="cb-bars">
  <div class="cb-bar-head">
    <div>{label_header}</div>
    <div>Share</div>
    <div class="num">Cost ({{{{ unit }}}})</div>
  </div>
{{% for row in rows %}}
  {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
  {{% assign cost = row["cost"].value | default: 0.0 %}}
  {{% assign bar_pct = cost | times: 100.0 | divided_by: max_cost | round: 1 %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
  <div class="cb-bar-row cb-pg-row-{pfx} cb-pg-{pfx}-{{{{ _row_pg }}}}">
    <div class="cb-bar-label" title="{{{{ row["{label_key}"].value }}}}">{{{{ row["{label_key}"].value }}}}</div>
    <div class="cb-bar-track"><div class="cb-bar-fill" style="width: {{{{ bar_pct }}}}%;"></div></div>
    <div class="cb-bar-val"><span class="cb-sym">{{{{ sym }}}}</span> <span class="cb-money">{{{{ fmt_cost }}}}</span></div>
  </div>
{{% endfor %}}
</div>
{paged_bottom(pfx)}
"""


UNALLOC_TABLE_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("unalloc", 10, "table-row")}
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
{paged_top("unalloc", 10, 10)}
<table class="cb-t">
  <thead>
    <tr><th>Category</th><th>Cost Type</th><th>SKU</th><th>Project</th><th class="num">Units (ECU)</th><th class="num">Cost ({{{{ unit }}}})</th></tr>
  </thead>
  <tbody>
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
  </tbody>
</table>
{paged_bottom("unalloc")}
"""


INVOICE_TABLE_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("inv", 25, "table-row")}
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
{paged_top("inv", 10, 25)}
<table class="cb-t">
  <thead>
    <tr><th>Project</th><th>Type</th><th>Category</th><th>Cost Type</th><th>SKU</th><th>Driver</th><th class="num">Quantity</th><th class="num">Units (ECU)</th><th class="num">Cost ({{{{ unit }}}})</th></tr>
  </thead>
  <tbody>
  {{% for row in rows %}}
    {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
    {{% assign cost = row["cost"].value | default: 0.0 %}}
    {{% assign ecu = row["ecu"].value | default: 0.0 %}}
    {{% assign qty = row["qty"].value | default: 0.0 %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
{liquid_money_block("ecu", "fmt_ecu", with_decimals=True)}
{liquid_money_block("qty", "fmt_qty", with_decimals=True)}
    <tr class="cb-pg-row-inv cb-pg-inv-{{{{ _row_pg }}}}">
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
{paged_bottom("inv")}
"""


DATASTREAM_COST_TABLE_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("dscost", 25, "table-row")}
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
{paged_top("dscost", 10, 25)}
<table class="cb-t">
  <thead>
    <tr><th>Project</th><th>Project Type</th><th>Type</th><th>Namespace</th><th>Data Stream</th><th class="num">Indexing ({{{{ sym }}}})</th><th class="num">Search ({{{{ sym }}}})</th><th class="num">Storage ({{{{ sym }}}})</th><th class="num">Attributed Cost ({{{{ unit }}}})</th></tr>
  </thead>
  <tbody>
  {{% for row in rows %}}
    {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
    {{% assign idx = row["indexing_cost"].value | default: 0.0 %}}
    {{% assign qry = row["query_cost"].value | default: 0.0 %}}
    {{% assign stg = row["storage_cost"].value | default: 0.0 %}}
    {{% assign total = row["attributed_cost"].value | default: 0.0 %}}
{liquid_money_block("idx", "fmt_idx", with_decimals=True)}
{liquid_money_block("qry", "fmt_qry", with_decimals=True)}
{liquid_money_block("stg", "fmt_stg", with_decimals=True)}
{liquid_money_block("total", "fmt_total", with_decimals=True)}
    <tr class="cb-pg-row-dscost cb-pg-dscost-{{{{ _row_pg }}}}">
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
  </tbody>
</table>
{paged_bottom("dscost")}
"""

STATUS_BOARD_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("statbrd", 20, "grid")}
  .cb-status {{ display: flex; flex-direction: column; gap: var(--cc-space-xs); }}
  .cb-item {{
    display: grid; grid-template-columns: 18px 1.4fr 0.9fr 0.8fr 1.1fr 1.2fr; gap: var(--cc-space-s);
    align-items: center; padding: 4px var(--cc-space-s);
    background: var(--cc-color-surface); border-radius: var(--cc-radius-s);
    border: 1px solid var(--cc-color-border);
  }}
  .cb-dot {{ width: 10px; height: 10px; border-radius: 50%; }}
  .cb-ok {{ background: var(--cc-vis-1, #54b399); }}
  .cb-warn {{ background: var(--cc-vis-8, #da8b45); }}
  .cb-err {{ background: var(--cc-vis-7, #c9404f); }}
  .cb-name {{ font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .cb-meta {{ opacity: 0.8; font-size: 0.85rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
</style>
{paged_top("statbrd", 10, 20)}
<div class="cb-status">
{{% for row in rows %}}
  {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
  {{% assign h = row["health"].value | default: "stale" %}}
  <div class="cb-item cb-pg-row-statbrd cb-pg-statbrd-{{{{ _row_pg }}}}">
    <div class="cb-dot {{% if h == "ok" %}}cb-ok{{% elsif h == "empty_or_unauthorized" %}}cb-err{{% else %}}cb-warn{{% endif %}}"></div>
    <div class="cb-name">{{{{ row["project_name"].value | default: row["project_id"].value }}}}</div>
    <div class="cb-meta">{{{{ row["project_type"].value | default: "unknown" }}}}</div>
    <div class="cb-meta">{{{{ row["index_count"].value | default: 0 }}}} indices</div>
    <div class="cb-meta">{{{{ h }}}} · day {{{{ row["day"].value | default: "—" }}}}</div>
    <div class="cb-meta">{{{{ row["updated_at"].value | default: "never" | slice: 0, 19 }}}}</div>
  </div>
{{% endfor %}}
</div>
{paged_bottom("statbrd")}
"""

ALLOC_TABLE_TEMPLATE = f"""\
<style>
{PAGED_BASE_CSS}
{paged_css("alloc", 10, "table-row")}
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
{paged_top("alloc", 10, 10)}
<table class="cb-t">
  <thead>
    <tr><th>Project</th><th>Type</th><th>Cost Type</th><th>Driver</th><th class="num">Cost ({{{{ unit }}}})</th><th>Share</th></tr>
  </thead>
  <tbody>
  {{% for row in rows %}}
    {{% assign _row_pg = forloop.index0 | divided_by: 10 | floor | plus: 1 %}}
    {{% assign cost = row["cost"].value | default: 0.0 %}}
    {{% assign bar_pct = cost | times: 100.0 | divided_by: max_cost | round: 1 %}}
{liquid_money_block("cost", "fmt_cost", with_decimals=True)}
    <tr class="cb-pg-row-alloc cb-pg-alloc-{{{{ _row_pg }}}}">
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
{paged_bottom("alloc")}
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
| EVAL day = DATE_TRUNC(1 day, ts)
| STATS total_spend = SUM(cost), allocatable_spend = SUM(alloc_cost), unallocatable_spend = SUM(unalloc_cost), project_count = COUNT_DISTINCT(project_id), days_count = COUNT_DISTINCT(day), billed_days = COUNT_DISTINCT(day), latest_day = TO_STRING(MAX(ts)), currency_symbol = MAX(currency_symbol), unit = MAX(unit)""",
        0,
        2,
        48,
        6,
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-billing-daily-project"),
        "Daily cost by project",
        DAILY_PROJECT_COST_TEMPLATE,
        f"""{COST_PRELUDE}
| WHERE @timestamp >= ?_tstart AND @timestamp <= ?_tend
| STATS cost = SUM(cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY b = BUCKET(@timestamp, 30, ?_tstart, ?_tend), project
| EVAL day = TO_STRING(b)
| INLINE STATS day_cost = SUM(cost) BY day
| SORT day ASC, cost DESC
| LIMIT 500
| KEEP day, project, cost, day_cost, currency_symbol, unit""",
        0,
        8,
        48,
        14,
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
        22,
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
        22,
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
        36,
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
        36,
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
        50,
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
        stable_uuid("cc-usage-namespace"),
        "Attributed cost by namespace",
        make_bar_template("namespace", "Namespace"),
        f"""FROM {DS_CONTRIB}
| EVAL ts = @timestamp
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
| EVAL namespace = COALESCE(ds_namespace, "other")
| STATS cost = SUM(total_ds_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY namespace
| SORT cost DESC
| LIMIT 25
| KEEP namespace, cost, currency_symbol, unit""",
        0,
        22,
        24,
        14,
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-usage-dstype"),
        "Attributed cost by data stream type",
        make_bar_template("ds_type", "Data stream type"),
        f"""FROM {DS_CONTRIB}
| EVAL ts = @timestamp
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
| EVAL ds_type_label = COALESCE(ds_type, "other")
| STATS cost = SUM(total_ds_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY ds_type_label
| RENAME ds_type_label AS ds_type
| SORT cost DESC
| LIMIT 25
| KEEP ds_type, cost, currency_symbol, unit""",
        24,
        22,
        24,
        14,
    )
    panels.append(p)

    p, _ = custom_panel(
        stable_uuid("cc-usage-datastream-cost"),
        "Data stream cost attribution",
        DATASTREAM_COST_TABLE_TEMPLATE,
        f"""FROM {DS_CONTRIB}
| EVAL ts = @timestamp, ds_project_name = CASE(project_name IS NOT NULL AND project_name != "", project_name, cluster_name IS NOT NULL AND cluster_name != "", cluster_name, "unknown"), ds_project_type = COALESCE(project_type, "unknown"), data_type = COALESCE(ds_type, "other"), namespace = COALESCE(ds_namespace, "other")
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
| STATS indexing_cost = SUM(idx_cost), query_cost = SUM(qry_cost), storage_cost = SUM(stg_cost), attributed_cost = SUM(total_ds_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY ds_project_name, ds_project_type, data_type, namespace, datastream
| RENAME ds_project_name AS project, ds_project_type AS project_type, data_type AS ds_type, namespace AS ds_namespace
| SORT attributed_cost DESC
| LIMIT 200
| KEEP project, project_type, ds_type, ds_namespace, datastream, indexing_cost, query_cost, storage_cost, attributed_cost, currency_symbol, unit""",
        0,
        36,
        48,
        14,
    )
    panels.append(p)

    esql = f"""FROM {DS_CONTRIB}
| EVAL `Project` = CASE(project_name IS NOT NULL AND project_name != "", project_name, cluster_name IS NOT NULL AND cluster_name != "", cluster_name, "unknown"), `Project type` = COALESCE(project_type, "unknown"), `Data stream type` = COALESCE(ds_type, "other"), `Namespace` = COALESCE(ds_namespace, "other")
| STATS `Indexing time (ms)` = SUM(datastream_sum_indexing_time), `Search query time (ms)` = SUM(datastream_sum_query_time), `Avg dataset size (MB)` = ROUND(AVG(TO_DOUBLE(datastream_sum_data_set_store_size)) / 1048576.0, 2) BY `Project`, `Project type`, `Data stream type`, `Namespace`, datastream
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
            col("Data stream type", "keyword"),
            col("Namespace", "keyword"),
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
                "Data stream type",
                "Namespace",
                "Data stream",
                "Indexing time (ms)",
                "Search query time (ms)",
                "Avg dataset size (MB)",
            ]
        ),
        0,
        50,
        48,
        14,
        index=DS_CONTRIB,
    )
    panels.append(p)

    ai_kpi_esql = f"""FROM {AI_CONTRIB}
| EVAL ts = @timestamp
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
| EVAL ai_pool_cost = COALESCE(ai_ecu, query_ecu, 0.0) * rate
| STATS proj_spend = MAX(ai_pool_cost), proj_inf = SUM(inference_count), proj_latency = AVG(average_inference_time_ms), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY composite_key
| STATS ai_spend = SUM(proj_spend), total_inferences = SUM(proj_inf), avg_latency_ms = ROUND(AVG(proj_latency), 1), currency_symbol = MAX(currency_symbol), unit = MAX(unit)
| EVAL cost_per_1k = CASE(total_inferences > 0, ai_spend * 1000.0 / total_inferences, 0.0)
| KEEP unit, currency_symbol, ai_spend, total_inferences, avg_latency_ms, cost_per_1k"""

    p, _ = custom_panel(
        stable_uuid("cc-usage-ai-kpi"),
        "AI & Machine Learning FinOps Overview",
        AI_KPIS_TEMPLATE,
        ai_kpi_esql,
        0,
        64,
        48,
        6,
    )
    panels.append(p)

    ai_models_esql = f"""FROM {AI_CONTRIB}
| EVAL ts = @timestamp
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
| EVAL compute_time = COALESCE(total_inference_time_ms, 0)
| EVAL ai_pool_cost = COALESCE(ai_ecu, query_ecu, 0.0) * rate
| STATS total_compute = SUM(compute_time), total_inf = SUM(inference_count), ai_pool = MAX(ai_pool_cost), currency_symbol = MAX(currency_symbol), unit = MAX(unit) BY model_id, task_type
| INLINE STATS all_compute = SUM(total_compute)
| EVAL share = CASE(all_compute > 0, total_compute * 1.0 / all_compute, 0.0)
| EVAL attributed_cost = share * ai_pool
| SORT total_compute DESC
| LIMIT 25
| KEEP model_id, task_type, total_compute, total_inf, attributed_cost, currency_symbol, unit"""

    p, _ = custom_panel(
        stable_uuid("cc-usage-ai-models"),
        "AI Model Compute & Spend Breakdown",
        AI_MODELS_TEMPLATE,
        ai_models_esql,
        0,
        70,
        48,
        14,
    )
    panels.append(p)

    return dashboard_doc(
        DASH_USAGE,
        TITLE_USAGE,
        "Attribute project workload pools (Indexing, Search, Storage) across namespaces, data stream types (logs, metrics, traces), and individual data streams.",
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
    "chargeback_usage": {
      "cluster": ["monitor"],
      "indices": [
        {
          "names": ["*"],
          "privileges": ["monitor"]
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
        time_field=None,
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
        time_field=None,
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
