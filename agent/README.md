# Elastic Cost Optimizer agent

An Agent Builder agent that analyses and explains the cost and usage data this integration
collects, and proposes quantified optimizations. It answers questions like *"which data
streams cost the most?"*, *"why did last week get more expensive?"* and *"where am I
over-provisioned?"* — grounded in the Chargeback lookup indices rather than in general
knowledge of Elastic Cloud pricing.

## Version

Requires Chargeback integration **0.5.0+** and Kibana **9.2.0+** with Agent Builder enabled.

## Why this is separate from the integration ZIP

Agents, skills and tools are not Fleet package asset types — there is no `kibana/agent` or
`kibana/skill` directory in the package spec. Like the [`serverless/`](../serverless/README.md)
assets, they are installed directly against the monitoring cluster's Kibana. See
[Instructions.md](Instructions.md).

## Architecture

```
                        Elastic Cost Optimizer  (agent)
                                    │
                 instructions: method, reporting rules, guardrails
                                    │
              ┌─────────────────────┴─────────────────────┐
              │                                           │
   chargeback.cost_review                        6 drill-down ES|QL tools
   (workflow tool - ALWAYS FIRST)                by component / datastream / tier /
              │                                  trend / inventory / unallocatable
   Kibana Workflow: a fixed 4-step chain
     1. configuration
     2. deployment inventory (hosted vs serverless)
     3. cost per deployment
     4. provisioned vs realized capacity
              │                                           │
              └─────────────────────┬─────────────────────┘
                                    │
        ┌───────────────────────────┴───────────────────────────┐
  chargeback-cost-analysis  (skill)               chargeback-optimization  (skill)
  data model, attribution arithmetic,             utilization + rightsizing arithmetic,
  reconciliation, Serverless differences,         optimization levers, how to rank and
  the one-day billing lag                         quantify recommendations
        └───────────────────────────┬───────────────────────────┘
                                    │
                    Chargeback lookup indices + ESS Billing
```

### Why the first tool is a workflow

LLM tool selection is not deterministic. Early versions relied on the agent calling the
configuration and classification tools before advising, and it sometimes skipped them — then
guessed that a Hosted deployment with no `node_stats` was a Serverless project, which is a
materially wrong answer.

`chargeback.cost_review` is a **workflow-typed tool** wrapping a Kibana Workflow that runs
the four opening queries in a fixed order and returns them as one payload. The ordering is
guaranteed by the workflow engine rather than by the model's judgement, so the platform
classification and the configured currency are always in front of the model before it
reasons.

The three tools that chain fully subsumes (`configuration`, `cost_by_deployment`,
`utilization_waste`) are deliberately **excluded from the agent's own tool list** so there is
no partial-overview alternative to pick by mistake. They remain attached to the skills. With
that redundancy removed, three consecutive runs of the same question each made exactly one
tool call: `chargeback.cost_review`.

A **tool** is either one parameterised ES|QL query or a wrapper around a Kibana Workflow. A
**skill** bundles domain knowledge (its `content`) with the tools that knowledge is about, and
is loaded based on its description when the user's question matches. The agent ties them
together with instructions.

> A skill may reference **at most 5 tools**. That cap is why the nine ES|QL tools are split
> across two skills rather than one.

## Tools

| Tool | Answers |
| --- | --- |
| `chargeback.cost_review` **(workflow)** | The deterministic opening snapshot: configuration + classification + cost + utilization in one call |
| `chargeback.configuration` | What rate, currency and weights is everything computed with? |
| `chargeback.deployment_inventory` | Which deployments exist, and is each one Hosted or Serverless? |
| `chargeback.cost_by_deployment` | Who spends what, per deployment and chargeback group? |
| `chargeback.cost_by_component` | Where does one deployment's money go, by SKU and category? |
| `chargeback.unallocatable_cost` | Which spend cannot be attributed to a data stream, and why? |
| `chargeback.cost_trend_by_day` | Is a cost new, growing, or steady? |
| `chargeback.utilization_waste` | Which deployments are over-provisioned, and by how much? |
| `chargeback.top_datastreams_by_cost` | Which data streams drive cost, split by indexing/query/storage? |
| `chargeback.cost_by_tier` | How is cost distributed across data tiers? |

The allocation tools reuse the **exact formula the shipped dashboards use**, so the agent's
numbers and the dashboards' numbers cannot drift apart.

## Skills

| Skill | Content | Tools |
| --- | --- | --- |
| [`chargeback-cost-analysis`](assets/skills/chargeback-cost-analysis.md) | Where the numbers come from, how per-data-stream cost is a share of an allocatable pool, why allocated costs never sum to the invoice, how to identify a Serverless project correctly, the one-day billing lag | 5 |
| [`chargeback-optimization`](assets/skills/chargeback-optimization.md) | Utilization and rightsizing arithmetic, the two distinct weight sets, optimization levers in payoff order, what does not apply to Serverless | 4 |

The skill content is deliberately as much about **what not to conclude** as about what to
compute. The two mistakes it exists to prevent, both observed during development:

- Treating a Hosted deployment with no `node_stats` as a Serverless project, and therefore
  dropping tier and ILM advice that does in fact apply.
- Citing the blended-cost storage weight (40) when explaining a utilization score, which is
  computed from an entirely different pair of weights (heap 70 / disk 30).

## Example

> **Which of my deployments spends the most over the last 30 days, and where is the biggest
> rightsizing opportunity?**

One `chargeback.cost_review` call returns the configuration, the classification of every
deployment, cost per deployment and utilization. The agent answers with a ranked table plus
the arithmetic — for
example that a deployment with p95 heap 84.6% and p95 disk 30.4% scores
`(0.70 × 84.6 + 0.30 × 30.4) ≈ 68.3%` utilization, making €9.89 of its €31.29 provisioned
tier cost recoverable, and that other deployments cannot be assessed because they are not
shipping node stats.

## Limitations

- **Determinism is bounded by tool choice, not by the chain.** The workflow guarantees the
  order of the four opening queries *once it is invoked*; nothing forces the model to invoke
  it. Removing the redundant overview tools made it consistent in testing, but a sufficiently
  unusual question could still route to a drill-down tool first. The chain itself cannot run
  out of order.
- **Allocation is an estimate.** Costs are derived from usage *shares* of a pool, not metered
  per-data-stream billing. Treat the figures as directional.
- **The agent cannot see today.** Billing lags usage by a day, so the newest fully joinable
  day is normally yesterday.
