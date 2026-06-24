# Sunrise Tokens Dune Volume Table

This bundle creates and refreshes a Dune materialized view for Sunrise token DEX volume on Solana.

The resulting table is:

```sql
dune.<team>.result_sunrise_tokens_dex_volume_7d
```

The source SQL produces non-overlapping seven-day buckets anchored on `DATE '2025-10-10'` and includes data through `CURRENT_DATE`.

## Files

- `sql/sunrise_tokens_dex_volume_7d.sql`: optimized DuneSQL source query.
- `scripts/deploy_matview.py`: Dune API helper for deploying or refreshing the materialized view.
- `.github/workflows/refresh-dune-matview.yml`: GitHub Actions workflow that refreshes the table every 24 hours.
- `.env.example`: local environment variable template.

## SQL Optimization Notes

- Uses a small `VALUES` table for the token allowlist instead of `SELECT *` from token metadata.
- Selects only the token metadata columns needed for the output.
- Replaces the `OR` join across bought/sold mint columns with two `UNION ALL` branches. This is easier for Dune/Trino to plan and preserves per-token volume when both sides of a trade are Sunrise tokens.
- Filters `dex_solana.trades` by `block_date >= DATE '2025-10-10'` in both branches so partition pruning can apply.
- Avoids sorting inside the materialized-view query. Sort when reading the table.

## GitHub Setup

The workflow expects this folder layout at a GitHub repository root so the workflow lives at:

```text
.github/workflows/refresh-dune-matview.yml
```

Add these GitHub repository settings:

- Secret `DUNE_API_KEY`: use a Dune API key with `Read/Write` scope for deploys. Refresh-only runs need `Read`, but `Read/Write` is simplest.
- Variable `DUNE_QUERY_ID`: set this after the first deploy creates the query.
- Variable `DUNE_MATVIEW_FULL_NAME`: optional, but recommended after the first deploy prints the full table name, for example `dune.myteam.result_sunrise_tokens_dex_volume_7d`.

## First Deploy

Run the workflow manually with:

- `action`: `deploy`
- `create_query`: `true`

The log will print a new `query_id`. Save that value as the GitHub repository variable `DUNE_QUERY_ID`.

After that, manual deploys can use:

- `action`: `deploy`
- `create_query`: `false`

Scheduled runs use `refresh` and run daily at `03:23 UTC`.

## Local Deploy Or Refresh

```powershell
$env:DUNE_API_KEY = "..."
$env:DUNE_CREATE_QUERY = "true"
python .\scripts\deploy_matview.py deploy
```

After saving the printed query id:

```powershell
$env:DUNE_QUERY_ID = "1234567"
python .\scripts\deploy_matview.py deploy
python .\scripts\deploy_matview.py refresh
```

## Query The Table

```sql
SELECT
    bucket_start_date,
    bucket_end_date,
    token_mint_address,
    symbol,
    volume_usd,
    bought_volume_usd,
    sold_volume_usd,
    trade_side_count,
    refreshed_at
FROM dune.<team>.result_sunrise_tokens_dex_volume_7d
ORDER BY bucket_start_date DESC, volume_usd DESC;
```
