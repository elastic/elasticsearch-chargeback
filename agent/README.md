# Chargeback companion agent (add-on)

An optional Agent Builder agent that explains cost and usage from Chargeback lookup indices. Default output is a **chargeback report**; optimization is secondary.

This is an **add-on**, not part of the Fleet zip. Pure Chargeback does not depend on it.

## Version

Requires Chargeback integration **0.5.0+** and Kibana **9.2.0+** with Agent Builder enabled.

## Install

```bash
export KIBANA_URL="https://<monitoring>.kb.<region>.<csp>.cloud.es.io"
export KIBANA_API_KEY="<api key>"
./scripts/install_chargeback_agent.sh
```

Or run `python3 agent/install.py` with the same environment variables. See [Instructions.md](Instructions.md).

## Architecture

```
                        Chargeback companion  (agent)
                                    │
                 report-first instructions; currency from conf
                                    │
              ┌─────────────────────┴─────────────────────┐
              │                                           │
   chargeback.cost_review                        drill-down ES|QL tools
   (workflow tool — always first)                component / datastream / tier /
              │                                  inventory / unallocatable
   Kibana Workflow: fixed chain
     1. configuration (rate + unit)
     2. deployment inventory
     3. cost per deployment
     4. provisioned vs realized capacity
```

## Why this is separate from the integration ZIP

Agents, skills, and tools are not Fleet package asset types. Like [`serverless/`](../serverless/README.md), they install against the monitoring cluster Kibana.
