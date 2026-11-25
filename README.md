# Elasticsearch Chargeback & FinOps Solutions

FinOps is an operational framework and cultural practice designed to maximize the business value of cloud usage. It enables timely, data-driven decision-making and fosters financial accountability through collaboration between engineering, finance, and business teams.

This repository hosts companion add-ons, On-Premises Billing, helper scripts, and historical pre-release archives for Elastic FinOps and cost allocation. **Chargeback** and **Chargeback Serverless** are now published integrations in the **Kibana Integrations** catalogue and answer a central question: **How is my organisation consuming the Elastic solution, and to which tenants can I allocate these costs?**

> [!IMPORTANT]
> **Install Chargeback and Chargeback Serverless from Kibana Integrations**: Both **Chargeback** (`chargeback`) and **Chargeback Serverless** (`chargeback_serverless`) have been released to the Elastic Package Registry. Install and upgrade them directly from the **Kibana Integrations** page (**Management -> Integrations**) by following [Add an integration to an Elastic Agent policy](https://www.elastic.co/docs/reference/fleet/add-integration-to-policy#add-integration-to-policy-steps).
>
> This repository is no longer the canonical source for those two integration packages. Active package source and current documentation live in [`elastic/integrations`](https://github.com/elastic/integrations) under [`packages/chargeback`](https://github.com/elastic/integrations/tree/main/packages/chargeback) and [`packages/chargeback_serverless`](https://github.com/elastic/integrations/tree/main/packages/chargeback_serverless). **On-Premises Billing** (`onprem_billing`), the optional **FinOps Companion Agent** (`agents/companion-agent/`), helper scripts (`scripts/`), and historical pre-release `.zip` archives remain in this repository.

---

## Solutions Overview

The table below summarises where each FinOps integration and add-on is installed and maintained:

| Solution | Target Environment | Key Capabilities | Installation & Source |
| :--- | :--- | :--- | :--- |
| **Chargeback** | Elastic Cloud Hosted, Self-Managed & Mixed Estates | Allocation of invoice and licence costs across deployments, data tiers, and data streams (Stack 9.2.0+). | Install in Kibana (**Management -> Integrations -> Chargeback**). Source: [`elastic/integrations/packages/chargeback`](https://github.com/elastic/integrations/tree/main/packages/chargeback) ([archived repo docs](integrations/chargeback/README.md)) |
| **Chargeback Serverless** | Elastic Cloud Serverless | Native FinOps hub for Serverless organisations: CEL streams, lookup transforms, ES\|QL dashboards, and alerting rules. | Install in Kibana (**Management -> Integrations -> Chargeback Serverless**). Source: [`elastic/integrations/packages/chargeback_serverless`](https://github.com/elastic/integrations/tree/main/packages/chargeback_serverless) ([archived repo docs](integrations/chargeback-serverless/README.md)) |
| **On-Premises Billing** | Self-Managed / On-Prem | Bridges ERU licences and on-prem hardware into normalised billing metrics for unified chargeback. | [`integrations/onprem-billing/`](integrations/onprem-billing/README.md) (upload `.zip` in Kibana Integrations) |
| **FinOps Companion Agent** | Hosted & Serverless (Hub) | Optional conversational FinOps analysis using Kibana Agent Builder, workflow tools, and lookup indices. | [`agents/companion-agent/`](agents/companion-agent/README.md) |

---

## Directory Structure

```
elasticsearch-chargeback/
├── integrations/
│   ├── chargeback/                   # Archived pre-release Chargeback package (installed via Kibana Integrations; source in elastic/integrations)
│   │   ├── README.md
│   │   ├── Instructions.md
│   │   ├── docs/troubleshooting.md
│   │   ├── addons/                   # Add-ons (Serverless usage workflow collector)
│   │   └── assets/                   # Historical package zips (dist/) and images (img/)
│   │
│   ├── chargeback-serverless/        # Archived pre-release Serverless package (installed via Kibana Integrations; source in elastic/integrations)
│   │   ├── README.md
│   │   ├── Instructions.md
│   │   ├── CONFIG.md
│   │   ├── dashboards/
│   │   └── assets/                   # Historical package zips (dist/), Kibana saved objects, images
│   │
│   └── onprem-billing/               # On-Premises Billing integration (still uploaded from assets/dist/)
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
└── scripts/                          # Helper, backfill, agent install, and historical release tools
```

---

## Quick Installation Guides

1. **Chargeback (Elastic Cloud Hosted, Self-Managed & Mixed Estates)**: In Kibana on your 9.2.0+ monitoring cluster, open **Management -> Integrations**, search for **Chargeback**, and add it to an Elastic Agent policy (see [Add an integration to an Elastic Agent policy](https://www.elastic.co/docs/reference/fleet/add-integration-to-policy#add-integration-to-policy-steps), or install assets only via **Settings -> Install Chargeback** per [Install and uninstall Elastic Agent integration assets](https://www.elastic.co/docs/reference/fleet/install-uninstall-integration-assets#install-integration-assets)). Upgrade existing installs under **Management -> Integrations -> Installed integrations -> Chargeback -> Settings** ([Upgrade an Elastic Agent integration](https://www.elastic.co/docs/reference/fleet/upgrade-integration#upgrade-integration-to-latest-version)).
2. **Chargeback Serverless (Elastic Cloud Serverless)**: In Kibana on your Serverless FinOps hub project (Elasticsearch or Observability Complete), open **Management -> Integrations**, search for **Chargeback Serverless**, and add it to an Elastic Agent policy with a customer-managed Fleet Elastic Agent 9.2.0+ ([Add an integration to an Elastic Agent policy](https://www.elastic.co/docs/reference/fleet/add-integration-to-policy#add-integration-to-policy-steps)). Optional historical usage snapshot projection remains available via [`scripts/run_chargeback_serverless_collector.py`](scripts/run_chargeback_serverless_collector.py).
3. **On-Premises Clusters (`onprem_billing`)**: Follow [On-Premises Billing Instructions](integrations/onprem-billing/Instructions.md) to upload [`onprem_billing-0.3.4.zip`](integrations/onprem-billing/assets/dist/onprem_billing-0.3.4.zip) in Kibana (**Management -> Integrations -> Upload integration**) and configure ERU rate pipelines.
4. **FinOps Companion Agent (Optional Add-On)**: Run [`scripts/install_chargeback_agent.sh`](scripts/install_chargeback_agent.sh) and see [`agents/companion-agent/Instructions.md`](agents/companion-agent/Instructions.md).

---

## Support

**Chargeback** and **Chargeback Serverless** are published in Beta in Kibana Integrations and maintained by Elastic **Field** (Customer Engineering) in [`elastic/integrations`](https://github.com/elastic/integrations). **On-Premises Billing** and the **FinOps Companion Agent** remain maintained in this repository. While these integrations are in technical preview / Beta, get help through Field - for example your Elastic account team or the Elastician who shared this work with you. Standard Elastic Support does not cover them until an official support model is put in place at general availability (GA).

---

## Legacy Module

An earlier, manually assembled **Chargeback module** is archived under [`legacy/module/`](legacy/module/README.md) for legacy environments that cannot meet modern Fleet stack requirements. It is not maintained and not recommended.
