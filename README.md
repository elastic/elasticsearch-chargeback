# Elasticsearch Chargeback

FinOps is an operational framework and cultural practice designed to maximize the business value of cloud usage. It enables timely, data-driven decision-making and fosters financial accountability through collaboration between engineering, finance, and business teams.

The **Chargeback integration** helps you answer a key question: How is my organization consuming the Elastic solution, and to which tenants can I allocate these costs?

Chargeback is based on the **Elasticsearch Service Billing** and **Elasticsearch** integrations. Its purpose is to provide data for chargeback calculations, offering a breakdown of chargeable units per:

- Deployment
- Data tier
- Data stream
- Day

## Support

Chargeback is created and supported by Elastic **Field** (Customer Engineering), not Elastic Product. It is **not** generally available (GA). While it remains in technical preview, get help through Field—for example your Elastic account team or the Elastician who shared this work with you. Standard Elastic Support does not cover it until that is explicitly arranged.

When Chargeback reaches GA, **Field** will coordinate with **Elastic Support** to put an official support model in place for entitled customers.

## Installation

Follow the [Chargeback integration instructions](integration/Instructions.md) to install Chargeback. For package details, version compatibility, and data flow, see the [integration README](integration/README.md).

## Dashboards

Chargeback ships three focused dashboards. Screenshots from local stack testing (0.4.x):

### [Chargeback] Billing Components Overview

![Billing Components Overview](integration/assets/img/chargeback-billing-overview.png)

### [Chargeback] Usage and Cost Allocation

![Usage and Cost Allocation](integration/assets/img/chargeback-usage-allocation.png)

### [Chargeback] Configuration

![Configuration](integration/assets/img/chargeback-configuration.png)

## Troubleshooting

If the Chargeback dashboard is empty after install, see **[integration/docs/troubleshooting.md](integration/docs/troubleshooting.md)** (step-by-step checks for billing/usage data, transforms, lookup indices, and configuration).

## Legacy: Chargeback module

An earlier, manually assembled **Chargeback module** still exists in this repository for environments that cannot meet the integration’s stack version requirements. It is not maintained and is not the recommended path.

If you need it, see the [Chargeback module README](module/README.md) and [module installation instructions](module/Instructions.md).
