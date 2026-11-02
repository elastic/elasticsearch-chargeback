# Chargeback Serverless (native hub)

Native Chargeback for **Serverless-only** organizations. Runs on a Serverless hub project (recommended: Observability Complete). Collects billing and usage through native Elastic Agent CEL streams and Fleet transforms. No CCS, no CPS, no ESS Billing package, no `.monitoring-es-*` emulation, and no Kibana Workflows required.

The Fleet package source lives in the integrations repository (`packages/chargeback_serverless` on branch `wip-johannes-chargeback-serverless`), and built artifacts are stored in `dist/`.

## Install

```bash
./scripts/deploy_chargeback_serverless.sh
```

See [Instructions.md](Instructions.md) and [CONFIG.md](CONFIG.md) for full setup and configuration details.

## Assets

| Path | Description |
| --- | --- |
| `dist/chargeback_serverless-0.3.13.zip` | Built Fleet integration package archive (`0.3.13`) |
| `kibana/` | Kibana dashboards (exported via `elastic-package export dashboards`), alerting rule templates, and index pattern |
| `CONFIG.md` | Fleet policy configuration reference and remote project API key guide |
| `Instructions.md` | End-to-end installation and verification guide |

## Related

- Hosted monitoring cluster + Serverless sources: [`../serverless/`](../serverless/) (existing add-on)
- Tracking: [elasticsearch-chargeback#123](https://github.com/elastic/elasticsearch-chargeback/issues/123)

