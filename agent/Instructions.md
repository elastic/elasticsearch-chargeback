# Elastic Cost Optimizer agent — setup

Install on the **monitoring cluster** — the deployment where the Chargeback integration and
the ESS Billing integration already run, and where the `*_lookup` indices live. The agent
queries those indices directly, so it must run in the same place.

## Prerequisites

- Chargeback integration **0.5.0+** installed, with its transforms in `health: green` and the
  lookup indices populated. Verify with `GET _transform/_stats` and
  `GET cluster_datastream_contribution_lookup/_count`. An agent over empty indices will
  correctly tell you it has no data, which is rarely what you wanted to learn.
- Kibana **9.2.0+** with **Agent Builder** enabled. Check with
  `GET /api/agent_builder/agents` — it should return `200`.
- An LLM connector configured for Agent Builder.
- A Kibana API key with privileges to manage Agent Builder assets and to read the
  `*_lookup` indices and `metrics-ess_billing.billing-*`.

## Install

```bash
export KIBANA_URL=https://<deployment>.kb.<region>.<csp>.cloud.es.io
export KIBANA_API_KEY=<base64 api key>

cd agent
python install.py
```

The script creates, in dependency order: one Kibana Workflow, ten tools (nine ES|QL plus the
workflow-backed `chargeback.cost_review`), two skills, and the agent. It is **idempotent**:
anything that already exists is updated in place, so the same command is also the upgrade
path.

One subtlety it handles for you: **Kibana derives a workflow's id from its name and does not
guarantee it matches.** If the slug is already taken it appends a suffix, so a
create/delete/recreate cycle can yield `chargeback-cost-review-2`. The committed tool JSON
therefore stores the asset *file stem* in `workflow_id`, and the installer resolves it to the
real id by matching on the workflow's `name` before creating the tool. If you install the
workflow by hand through the UI, check the id it actually received and set the tool's
`workflow_id` to match.

To check what is present without changing anything:

```bash
python install.py --verify
```

Requires Python 3.8+ and no third-party packages.

### Installing through the UI instead

Each asset is a plain JSON file under [`assets/`](assets), so you can paste them into
**Agent Builder → Tools / Skills / Agents** by hand. Two things to watch, both verified
against Kibana 9.4.3:

- Tools **require** a `type`: `"esql"` for the query tools, `"workflow"` for
  `chargeback.cost_review`. It is present in the committed JSON.
- Agents **reject** a `type` property. It is deliberately absent from the agent JSON.
- Skill `content` lives in a sibling `.md` file rather than inline in the JSON, so it stays
  reviewable in diffs. `install.py` injects it from the `content_file` field. If you paste a
  skill by hand, paste the `.md` contents into the skill's content field yourself.

## Verify

Open **Agent Builder** in Kibana, pick **Elastic Cost Optimizer**, and ask:

> Which of my deployments spends the most over the last 30 days, and where is the biggest
> rightsizing opportunity?

A healthy answer names deployments with currency amounts, states the configured rate, and
either quantifies waste or explains that utilization data is missing. If it reports no data
at all, check that the transforms have run and that billing and usage cover a common day —
billing for day D arrives on D+1, so today will usually be empty.

Or from the command line:

```bash
curl -X POST "$KIBANA_URL/api/agent_builder/converse" \
  -H "Authorization: ApiKey $KIBANA_API_KEY" \
  -H 'kbn-xsrf: true' -H 'Content-Type: application/json' \
  -d '{"agent_id":"elastic-cost-optimizer","input":"Summarise cost by deployment for the last 30 days."}'
```

The response includes a `steps` array showing which tools were called, which is the quickest
way to see whether the agent is reaching the data or guessing.

## Customising

- **Currency and weights** are not in the agent. They come from `chargeback_conf_lookup`, so
  update them there (see the integration
  [Instructions](../integration/Instructions.md#configuration)) and the agent follows.
- **Adding a tool:** create the JSON in `assets/tools/`, add its ID to a skill's `tool_ids`
  and to the agent's `configuration.tools[0].tool_ids`, then re-run `install.py`. Remember
  the 5-tool cap per skill.
- **Changing tone or method:** edit `configuration.instructions` in
  [`assets/agents/elastic-cost-optimizer.json`](assets/agents/elastic-cost-optimizer.json).
- **Changing domain knowledge:** edit the skill `.md` files. Prefer putting durable facts and
  guardrails in the skill content and keeping the agent instructions about method and output
  format — the skills are loaded only when relevant, which keeps the agent's context smaller.

## Uninstall

```bash
for t in configuration deployment_inventory cost_by_deployment cost_by_component \
         cost_trend_by_day unallocatable_cost utilization_waste \
         top_datastreams_by_cost cost_by_tier; do
  curl -X DELETE "$KIBANA_URL/api/agent_builder/tools/chargeback.$t" \
    -H "Authorization: ApiKey $KIBANA_API_KEY" -H 'kbn-xsrf: true'
done

for s in chargeback-cost-analysis chargeback-optimization; do
  curl -X DELETE "$KIBANA_URL/api/agent_builder/skills/$s" \
    -H "Authorization: ApiKey $KIBANA_API_KEY" -H 'kbn-xsrf: true'
done

curl -X DELETE "$KIBANA_URL/api/agent_builder/agents/elastic-cost-optimizer" \
  -H "Authorization: ApiKey $KIBANA_API_KEY" -H 'kbn-xsrf: true'
```

Delete the agent and skills before the tools if you want to avoid dangling references.
Nothing here writes to Elasticsearch, so removing the agent has no effect on your data.
