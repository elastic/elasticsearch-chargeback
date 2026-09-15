# 0.4.4 evidence — conf lookup document ID

Screenshots from verifying the 0.4.4 bootstrap dest-pipeline fix (`set _id = config`) against a monitoring cluster that previously hit [#110](https://github.com/elastic/elasticsearch-chargeback/issues/110).

| File | What it shows |
|------|----------------|
| `01-before-hashed-id.png` | Bootstrap row with hashed `_id` |
| `02-before-update-404.png` | `POST .../_update/config` → `document_missing_exception` |
| `05-after-get-config.png` | `GET .../_doc/config` → `found: true` |
| `06-after-update-ok.png` | `POST .../_update/config` → `result: updated` |
| `00-verification-full.png` | Combined before/after summary |

Rate was restored to `0.85` after the update proof.
