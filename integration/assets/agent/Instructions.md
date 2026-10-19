# Chargeback companion agent — setup

This **optional add-on** installs on the **monitoring cluster** — where the Chargeback integration, ESS Billing, and `*_lookup` indices already run. The agent queries those indices directly.

Prefer [`scripts/install_chargeback_agent.sh`](../../../scripts/install_chargeback_agent.sh) from the repository root.

## Prerequisites

- Chargeback integration **0.5.0+** with transforms in `health: green` and lookup indices populated. Verify with `GET _transform/_stats` and `GET cluster_datastream_contribution_lookup/_count`.
- Kibana **9.2.0+** with **Agent Builder** enabled (`GET /api/agent_builder/agents` returns `200`).
- An LLM connector configured for Agent Builder.
- A Kibana API key that can manage Agent Builder assets and read `*_lookup` plus `metrics-ess_billing.billing-*`.

## Install

```bash
export KIBANA_URL=https://<deployment>.kb.<region>.<csp>.cloud.es.io
export KIBANA_API_KEY=<base64 api key>

./scripts/install_chargeback_agent.sh
```

(Run from the repository root.)

The script creates, in dependency order: one Kibana Workflow, tools (ES|QL plus workflow-backed `chargeback.cost_review`), two skills, and the **Chargeback companion** agent. It is **idempotent**: existing assets are updated in place.

Kibana derives a workflow id from its name and may append a suffix. The installer resolves the real id by matching on workflow `name` before creating the tool.

On Kibana builds without Workflows (for example local elastic-package), use `--skip-workflows`. That installs the ES|QL tools and companion agent with alternate instructions that open with `chargeback.configuration` → `chargeback.deployment_inventory` → `chargeback.cost_by_deployment` → `chargeback.utilization_waste` instead of `chargeback.cost_review`.

TLS for the bootstrap API-key call is verified by default; localhost / `CURL_INSECURE=1` skip verification. The Python installer also verifies TLS except for localhost or `KIBANA_INSECURE=1`.

## Verify

```bash
./scripts/install_chargeback_agent.sh --verify
```

In Agent Builder, open **Chargeback companion** and ask for a 30-day chargeback report. With a full install the first tool call should be `chargeback.cost_review`; with `--skip-workflows` it should start with the four overview ES|QL tools above. Amounts should quote the configured currency unit from conf, not a hard-coded EUR default.

## Uninstall

Remove the agent, skills, tools, and the `chargeback-cost-review` workflow from Agent Builder / Workflows as needed. Lookup indices and the Chargeback Fleet package are unchanged.
