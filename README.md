# Elasticsearch Chargeback

FinOps is an operational framework and cultural practice designed to maximize the business value of cloud usage. It enables timely, data-driven decision-making and fosters financial accountability through collaboration between engineering, finance, and business teams.

The **Chargeback integration** (and **Chargeback module**) helps users answer a key question: How is my organisation consuming the Elastic solution, and to which tenants can I allocate these costs?

Both Chargeback module and integration are based on the **Elasticsearch Service Billing** and **Elasticsearch** integrations. Their purpose is to provide data for chargeback calculations, offering a breakdown of chargeable units per:
- Deployment
- Data tier
- Data stream
- Day

## Choosing between the **Chargeback integration** and **Chargeback module**

The recommended approach is to use the *Chargeback integration* because of the following:
- Our limited team cannot maintain more than one stream
- All focus will be on the integration
- The "module" has much more moving parts and is more difficult to set up, and is therefore prone to more issues

The only reason you should choose the "module" in favour of the integration is if you cannot upgrade your monitoring cluster to 8.18 or later, and is stuck on 8.17.1 or later.

## Installation options

- For the [Chargeback Integration](integration/README.md): Follow the [`integration/Instructions.md`](integration/Instructions.md) to install the integration.
- For the [Chargeback module](module/README.md): Follow the [`module/Instructions.md`](module/Instructions.md) to install the module.
