# Sunrise Tokens Dune API Sync

This repo pulls Sunrise token DEX volume from Dune with the Dune API. It does **not** use a materialized view.

The output table is a normal CSV data table in this repo:

```text
data/sunrise_tokens_dex_volume_daily.csv
```

Optionally, the same CSV can be synced into a Dune Uploads table:

```sql
dune.<namespace>.sunrise_tokens_dex_volume_daily
```

## What The Script Does

1. Creates or updates one saved, parameterized Dune query.
2. Runs that query through the Dune execution API with `start_date` and `end_date` parameters.
3. For historical backfill, loops from `2025-10-10` to today in 7-day API windows.
4. Stores daily token volume rows in `data/sunrise_tokens_dex_volume_daily.csv`.
5. On daily refresh, re-fetches only the latest rolling window, defaulting to the last 2 days, then merges by `(block_date, token_mint_address)`.
6. Optionally clears and reloads a Dune Uploads table with the merged CSV.

The SQL returns daily volume. The 7-day interval is only the API backfill chunk size.

## Files

- `sql/sunrise_tokens_dex_volume_daily.sql`: parameterized DuneSQL query.
- `scripts/sync_sunrise_dune_volume.py`: Dune API extraction, CSV merge, and optional Dune Uploads sync.
- `.github/workflows/sync-sunrise-dune-volume.yml`: daily GitHub Actions refresh.
- `data/sunrise_tokens_dex_volume_daily.csv`: generated data table after bootstrap/refresh.

## GitHub Setup

Add this repository secret:

- `DUNE_API_KEY`: Dune API key. Query execution needs `Read`; creating saved queries and upload tables needs `Read/Write`.

Add these repository variables:

- `DUNE_QUERY_ID`: set after the first `deploy-query` run creates the saved query.
- `DUNE_SYNC_UPLOAD`: optional, set to `true` to also sync into a Dune Uploads table.
- `DUNE_UPLOAD_NAMESPACE`: required only when `DUNE_SYNC_UPLOAD=true`, for example your Dune user or team namespace.

## First Run

Run the workflow manually:

- `action`: `deploy-query`
- `create_query`: `true`

The logs will print a Dune `query_id`. Save that as repository variable `DUNE_QUERY_ID`.

Then run the workflow manually again:

- `action`: `bootstrap`
- `sync_upload_table`: optional

This backfills from `2025-10-10` to today in 7-day chunks and commits the generated CSV table.

## Daily Refresh

The scheduled workflow runs every day at `03:23 UTC` with:

- `action`: `refresh`

Refresh mode reads the existing CSV, re-fetches the latest 2 days from Dune, merges by `(block_date, token_mint_address)`, and commits the changed data files.

## Local Usage

Create or update the saved Dune query:

```powershell
$env:DUNE_API_KEY = "..."
$env:DUNE_CREATE_QUERY = "true"
python .\scripts\sync_sunrise_dune_volume.py deploy-query
```

Backfill historical data:

```powershell
$env:DUNE_QUERY_ID = "1234567"
python .\scripts\sync_sunrise_dune_volume.py bootstrap
```

Refresh recent data:

```powershell
python .\scripts\sync_sunrise_dune_volume.py refresh
```

Also sync to a Dune Uploads table:

```powershell
$env:DUNE_SYNC_UPLOAD = "true"
$env:DUNE_UPLOAD_NAMESPACE = "your_namespace"
python .\scripts\sync_sunrise_dune_volume.py refresh
```

## CSV Schema

```text
block_date
token_mint_address
symbol
volume_usd
bought_volume_usd
sold_volume_usd
trade_side_count
source_window_start
source_window_end
fetched_at_utc
```

## Querying The Optional Dune Uploads Table

```sql
SELECT
    block_date,
    token_mint_address,
    symbol,
    volume_usd,
    bought_volume_usd,
    sold_volume_usd,
    trade_side_count
FROM dune.<namespace>.sunrise_tokens_dex_volume_daily
ORDER BY block_date DESC, volume_usd DESC;
```
