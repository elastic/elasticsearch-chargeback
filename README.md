# Elasticsearch Chargeback & FinOps Solutions

FinOps is an operational framework and cultural practice designed to maximize the business value of cloud usage. It enables timely, data-driven decision-making and fosters financial accountability through collaboration between engineering, finance, and business teams.

This repository provides field-supported integrations, add-ons, and AI tooling to answer a central question: **How is my organization consuming the Elastic solution, and to which tenants can I allocate these costs?**

---

## Solutions Overview

This repository contains three specialized integrations alongside AI tooling:

| Solution | Target Environment | Key Capabilities | Documentation & Assets |
| :--- | :--- | :--- | :--- |
| **Chargeback** | Elastic Cloud Hosted | Allocation of invoice cost from billing and usage across deployments, data tiers, and data streams. | [`integrations/chargeback/`](integrations/chargeback/README.md) |
| **Chargeback Serverless** | Elastic Cloud Serverless | Native FinOps hub for Serverless organizations. CEL streams, 7 lookup transforms, 5 ES\|QL dashboards, and 10 alerting rules. | [`integrations/chargeback-serverless/`](integrations/chargeback-serverless/README.md) |
| **On-Premises Billing** | Self-Managed / On-Prem | Bridges ERU licenses and on-prem hardware into normalized billing metrics for unified chargeback. | [`integrations/onprem-billing/`](integrations/onprem-billing/README.md) |
| **FinOps Companion Agent** | Hosted & Serverless (Hub) | Conversational FinOps analysis using Agent Builder, workflow tools, and lookup indices. | [`agents/companion-agent/`](agents/companion-agent/README.md) |

---

## Directory Structure

```
elasticsearch-chargeback/
├── integrations/
│   ├── chargeback/                   # Elastic Cloud Hosted Chargeback package
│   │   ├── README.md
│   │   ├── Instructions.md
│   │   ├── docs/troubleshooting.md
│   │   ├── addons/                   # Add-ons (Serverless usage workflow collector)
│   │   └── assets/                   # Package zips (dist/) and images (img/)
│   │
│   ├── chargeback-serverless/        # Native Serverless FinOps integration
│   │   ├── README.md
│   │   ├── Instructions.md
│   │   ├── CONFIG.md
│   │   ├── dashboards/
│   │   └── assets/                   # Built package (dist/), Kibana saved objects, images
│   │
│   └── onprem-billing/               # On-Premises Billing integration
│       ├── README.md
│       ├── Instructions.md
│       └── assets/                   # Built package (dist/) and calculation pipelines
│
├── agents/
│   └── companion-agent/              # Agent Builder companion agent (tools, skills, agent)
│
├── legacy/
│   └── module/                       # Legacy pre-Fleet manual chargeback module
│
└── scripts/                          # Automated deployment, release, and E2E testing tools
```

---

## Quick Installation Guides

> [!IMPORTANT]
> **Users vs. Developers**: To install any integration in this repository, **you do NOT need the `elastic/integrations` repository or `elastic-package` CLI**. Each solution ships with a prebuilt `.zip` package under `assets/dist/` that you can upload directly in Kibana (**Management → Integrations → Upload integration**). For **Chargeback Serverless**, you can either upload [`integrations/chargeback-serverless/assets/dist/chargeback_serverless-0.3.15.zip`](integrations/chargeback-serverless/assets/dist/chargeback_serverless-0.3.15.zip) in Kibana and run [`python3 scripts/run_chargeback_serverless_collector.py`](scripts/run_chargeback_serverless_collector.py) to backfill historical data, or run [`./scripts/deploy_chargeback_serverless.sh`](scripts/deploy_chargeback_serverless.sh) (which uses the prebuilt `.zip` by default without building). Building from `elastic/integrations` (`--build`) is only for developers modifying package source files.

1. **Elastic Cloud Hosted**: Follow the [Chargeback Installation Instructions](integrations/chargeback/Instructions.md). Current release: `0.5.0` in [`integrations/chargeback/assets/dist/0.5.0/`](integrations/chargeback/assets/dist/0.5.0/).
2. **Elastic Cloud Serverless**: Follow the [Chargeback Serverless Instructions](integrations/chargeback-serverless/Instructions.md) (upload [`chargeback_serverless-0.3.15.zip`](integrations/chargeback-serverless/assets/dist/chargeback_serverless-0.3.15.zip) in Kibana + run [`scripts/run_chargeback_serverless_collector.py`](scripts/run_chargeback_serverless_collector.py), or deploy end-to-end with [`scripts/deploy_chargeback_serverless.sh`](scripts/deploy_chargeback_serverless.sh)).
3. **On-Premises Clusters**: Follow the [On-Premises Billing Instructions](integrations/onprem-billing/Instructions.md) to install `onprem_billing` and configure ERU rate pipelines.
4. **FinOps Companion Agent**: Run [`scripts/install_chargeback_agent.sh`](scripts/install_chargeback_agent.sh) and see [`agents/companion-agent/Instructions.md`](agents/companion-agent/Instructions.md).

---

## Support

Chargeback is created and supported by Elastic **Field** (Customer Engineering), not Elastic Product. It is **not** generally available (GA). While it remains in technical preview, get help through Field—for example your Elastic account team or the Elastician who shared this work with you. Standard Elastic Support does not cover it until that is explicitly arranged.

When Chargeback reaches GA, **Field** will coordinate with **Elastic Support** to put an official support model in place for entitled customers.

---

## Legacy Module

An earlier, manually assembled **Chargeback module** is archived under [`legacy/module/`](legacy/module/README.md) for legacy environments that cannot meet modern Fleet stack requirements. It is not maintained and not recommended.
