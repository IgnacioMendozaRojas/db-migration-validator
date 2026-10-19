# Migration validation report

**FAIL**: data or access differs between source and target.

| | Source | Target |
|---|---|---|
| Engine | postgresql 16.15 | postgresql 16.15 |
| Snapshot taken | 2026-03-14T02:00:00+00:00 | 2026-03-14T06:30:00+00:00 |
| Tables | 2 | 2 |
| Views | 1 | 1 |
| Rows | 700 | 697 |
| Grants | 27 | 24 |

Content fingerprints compared for 2 of 2 source tables.

**5 critical · 2 warnings · 0 info**

## ✗ Critical

| Category | Object | Detail |
|---|---|---|
| content | `accounts` | content fingerprint differs (row counts differ, so every column is affected) |
| privilege | `public.accounts` | SELECT for nightly_batch lost |
| privilege | `public.customers` | SELECT for nightly_batch lost |
| privilege | `public.v_balances` | SELECT for nightly_batch lost |
| rows | `accounts` | row count 500 -> 497 (-3) |

## ⚠ Warning

| Category | Object | Detail |
|---|---|---|
| index | `accounts` | index on (customer_id) missing |
| type | `accounts.balance` | NUMERIC(14, 2) -> NUMERIC(14, 0) |
