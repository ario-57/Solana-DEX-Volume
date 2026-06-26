#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib import error, parse, request


API_BASE = os.getenv("DUNE_API_BASE", "https://api.dune.com/api/v1").rstrip("/")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQL_PATH = ROOT / "sql" / "sunrise_tokens_dex_volume_daily.sql"
DEFAULT_DATA_PATH = ROOT / "data" / "sunrise_tokens_dex_volume_daily.csv"
DEFAULT_STATE_PATH = ROOT / "data" / "sunrise_tokens_dex_volume_state.json"

CSV_COLUMNS = [
    "block_date",
    "token_mint_address",
    "symbol",
    "volume_usd",
    "bought_volume_usd",
    "sold_volume_usd",
    "trade_side_count",
    "source_window_start",
    "source_window_end",
    "fetched_at_utc",
]

UPLOAD_SCHEMA = [
    {"name": "block_date", "type": "date", "nullable": False},
    {"name": "token_mint_address", "type": "varchar", "nullable": False},
    {"name": "symbol", "type": "varchar", "nullable": True},
    {"name": "volume_usd", "type": "double", "nullable": True},
    {"name": "bought_volume_usd", "type": "double", "nullable": True},
    {"name": "sold_volume_usd", "type": "double", "nullable": True},
    {"name": "trade_side_count", "type": "bigint", "nullable": True},
    {"name": "source_window_start", "type": "date", "nullable": False},
    {"name": "source_window_end", "type": "date", "nullable": False},
    {"name": "fetched_at_utc", "type": "timestamp", "nullable": False},
]

TERMINAL_STATES = {
    "QUERY_STATE_COMPLETED",
    "QUERY_STATE_FAILED",
    "QUERY_STATE_CANCELED",
    "QUERY_STATE_EXPIRED",
    "QUERY_STATE_COMPLETED_PARTIAL",
}


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_day(value: str) -> date:
    return date.fromisoformat(value[:10])


def utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def decimal_text(value: object) -> str:
    if value is None or value == "":
        return "0"
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    text = format(decimal_value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def int_text(value: object) -> str:
    if value is None or value == "":
        return "0"
    return str(int(Decimal(str(value))))


def require(value: str | None, message: str) -> str:
    if not value:
        raise SystemExit(message)
    return value


def dune_request(
    method: str,
    path: str,
    api_key: str,
    payload: dict | str | bytes | None = None,
    content_type: str | None = "application/json",
) -> dict:
    if isinstance(payload, dict):
        body = json.dumps(payload).encode("utf-8")
    elif isinstance(payload, str):
        body = payload.encode("utf-8")
    else:
        body = payload

    headers = {
        "Accept": "application/json",
        "X-Dune-Api-Key": api_key,
    }
    if body is not None and content_type:
        headers["Content-Type"] = content_type

    req = request.Request(f"{API_BASE}{path}", data=body, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=120) as res:
            raw = res.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Dune API {method} {path} failed with HTTP {exc.code}: {raw}") from exc


def ensure_saved_query(args: argparse.Namespace) -> str:
    query_id = args.query_id
    sql = Path(args.sql).read_text(encoding="utf-8")
    payload = {
        "name": args.query_name,
        "description": args.query_description,
        "query_sql": sql,
        "is_private": args.query_private,
        "tags": args.tags,
    }

    if query_id:
        response = dune_request("PATCH", f"/query/{query_id}", args.api_key, payload)
        resolved_query_id = str(response.get("query_id", query_id))
        print(f"Updated saved Dune query_id={resolved_query_id}")
        return resolved_query_id

    if args.create_query:
        response = dune_request("POST", "/query", args.api_key, payload)
        resolved_query_id = str(response["query_id"])
        print(f"Created saved Dune query_id={resolved_query_id}")
        print("Save this as GitHub repository variable DUNE_QUERY_ID.")
        return resolved_query_id

    raise SystemExit("Missing DUNE_QUERY_ID. Run deploy-query once with DUNE_CREATE_QUERY=true.")


def poll_execution(args: argparse.Namespace, execution_id: str) -> None:
    deadline = time.time() + args.timeout_seconds
    last_status: dict = {}

    while time.time() < deadline:
        last_status = dune_request("GET", f"/execution/{execution_id}/status", args.api_key)
        state = last_status.get("state")
        print(f"execution_id={execution_id} state={state}")

        if state in TERMINAL_STATES:
            if state != "QUERY_STATE_COMPLETED":
                print(json.dumps(last_status, indent=2, sort_keys=True))
                raise SystemExit(f"Dune execution finished in non-success state: {state}")
            return

        time.sleep(args.poll_interval_seconds)

    print(json.dumps(last_status, indent=2, sort_keys=True))
    raise SystemExit(f"Timed out waiting for execution_id={execution_id}")


def fetch_execution_rows(args: argparse.Namespace, execution_id: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0

    while True:
        query = parse.urlencode({"limit": args.result_limit, "offset": offset})
        response = dune_request("GET", f"/execution/{execution_id}/results?{query}", args.api_key)
        result = response.get("result", {})
        batch = result.get("rows", [])
        rows.extend(batch)

        next_offset = response.get("next_offset")
        if next_offset is None or not batch:
            break

        offset = int(next_offset)

    return rows


def run_window(args: argparse.Namespace, query_id: str, start: date, end: date) -> list[dict]:
    payload = {
        "query_parameters": {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
        "performance": args.performance,
    }
    response = dune_request("POST", f"/query/{query_id}/execute", args.api_key, payload)
    execution_id = response["execution_id"]
    print(f"Started Dune window {start} <= block_date < {end}: execution_id={execution_id}")
    poll_execution(args, execution_id)
    return fetch_execution_rows(args, execution_id)


def load_existing(path: Path) -> dict[tuple[str, str], dict]:
    if not path.exists():
        return {}

    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return {
            (row["block_date"], row["token_mint_address"]): {column: row.get(column, "") for column in CSV_COLUMNS}
            for row in reader
        }


def normalize_row(row: dict, window_start: date, window_end: date, fetched_at: str) -> dict:
    block_date = parse_day(str(row["block_date"])).isoformat()
    return {
        "block_date": block_date,
        "token_mint_address": str(row["token_mint_address"]),
        "symbol": "" if row.get("symbol") is None else str(row.get("symbol")),
        "volume_usd": decimal_text(row.get("volume_usd")),
        "bought_volume_usd": decimal_text(row.get("bought_volume_usd")),
        "sold_volume_usd": decimal_text(row.get("sold_volume_usd")),
        "trade_side_count": int_text(row.get("trade_side_count")),
        "source_window_start": window_start.isoformat(),
        "source_window_end": window_end.isoformat(),
        "fetched_at_utc": fetched_at,
    }


def write_csv(path: Path, rows_by_key: dict[tuple[str, str], dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered_rows = sorted(rows_by_key.values(), key=lambda row: (row["block_date"], row["token_mint_address"]))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(ordered_rows)


def to_csv_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def selected_windows(args: argparse.Namespace, action: str, existing_rows: dict[tuple[str, str], dict]) -> list[tuple[date, date]]:
    today_exclusive = date.today() + timedelta(days=1)

    if action == "bootstrap" or not existing_rows:
        start = parse_day(args.historical_start)
    else:
        latest_existing = max(parse_day(row["block_date"]) for row in existing_rows.values())
        start = max(parse_day(args.historical_start), latest_existing - timedelta(days=args.refresh_lookback_days))

    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor < today_exclusive:
        end = min(cursor + timedelta(days=args.window_days), today_exclusive)
        windows.append((cursor, end))
        cursor = end

    return windows


def sync_data(args: argparse.Namespace, action: str) -> None:
    query_id = ensure_saved_query(args) if args.create_query or not args.query_id else args.query_id
    rows_by_key = load_existing(Path(args.data_path))
    windows = selected_windows(args, action, rows_by_key)

    fetched_at = utc_now_text()
    total_fetched = 0
    for start, end in windows:
        rows = run_window(args, query_id, start, end)
        print(f"Fetched {len(rows)} rows for {start} <= block_date < {end}")
        total_fetched += len(rows)
        for row in rows:
            normalized = normalize_row(row, start, end, fetched_at)
            rows_by_key[(normalized["block_date"], normalized["token_mint_address"])] = normalized

    data_path = Path(args.data_path)
    write_csv(data_path, rows_by_key)
    write_state(args, action, query_id, windows, total_fetched, len(rows_by_key))

    if args.sync_upload:
        sync_upload_table(args, data_path)

    print(f"Wrote {len(rows_by_key)} merged rows to {data_path}")


def write_state(
    args: argparse.Namespace,
    action: str,
    query_id: str,
    windows: list[tuple[date, date]],
    fetched_rows: int,
    merged_rows: int,
) -> None:
    state = {
        "action": action,
        "query_id": query_id,
        "ran_at_utc": utc_now_text(),
        "historical_start": args.historical_start,
        "window_days": args.window_days,
        "refresh_lookback_days": args.refresh_lookback_days,
        "fetched_rows": fetched_rows,
        "merged_rows": merged_rows,
        "windows": [
            {"start_date": start.isoformat(), "end_date": end.isoformat()}
            for start, end in windows
        ],
    }
    state_path = Path(args.state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sync_upload_table(args: argparse.Namespace, data_path: Path) -> None:
    namespace = require(args.upload_namespace, "DUNE_UPLOAD_NAMESPACE is required when DUNE_SYNC_UPLOAD=true.")
    table = require(args.upload_table, "DUNE_UPLOAD_TABLE is required when DUNE_SYNC_UPLOAD=true.")

    if args.create_upload_table:
        payload = {
            "namespace": namespace,
            "table_name": table,
            "description": args.upload_description,
            "is_private": args.upload_private,
            "schema": UPLOAD_SCHEMA,
        }
        try:
            dune_request("POST", "/uploads", args.api_key, payload)
            print(f"Created Dune upload table dune.{namespace}.{table}")
        except RuntimeError as exc:
            if "already exists" in str(exc).lower() or "409" in str(exc):
                print(f"Dune upload table dune.{namespace}.{table} already exists")
            else:
                raise

    if args.clear_upload_before_insert:
        dune_request("POST", f"/uploads/{namespace}/{table}/clear", args.api_key, payload=None)
        print(f"Cleared Dune upload table dune.{namespace}.{table}")

    dune_request(
        "POST",
        f"/uploads/{namespace}/{table}/insert",
        args.api_key,
        payload=to_csv_text(data_path),
        content_type="text/csv",
    )
    print(f"Inserted CSV into Dune upload table dune.{namespace}.{table}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Sunrise token DEX volume from Dune in date windows and maintain a CSV table."
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("deploy-query", "bootstrap", "refresh"),
        default=os.getenv("DUNE_ACTION", "refresh"),
    )
    parser.add_argument("--api-key", default=os.getenv("DUNE_API_KEY"))
    parser.add_argument("--query-id", default=os.getenv("DUNE_QUERY_ID"))
    parser.add_argument("--create-query", action="store_true", default=env_bool("DUNE_CREATE_QUERY"))
    parser.add_argument("--query-private", action="store_true", default=env_bool("DUNE_QUERY_PRIVATE", True))
    parser.add_argument("--sql", default=os.getenv("DUNE_SQL_PATH", str(DEFAULT_SQL_PATH)))
    parser.add_argument("--query-name", default=os.getenv("DUNE_QUERY_NAME", "Sunrise tokens DEX volume daily"))
    parser.add_argument(
        "--query-description",
        default=os.getenv(
            "DUNE_QUERY_DESCRIPTION",
            "Daily Sunrise token DEX volume on Solana, parameterized by start_date/end_date.",
        ),
    )
    parser.add_argument("--tags", type=split_csv, default=split_csv(os.getenv("DUNE_TAGS", "sunrise,solana,dex-volume")))
    parser.add_argument("--performance", default=os.getenv("DUNE_PERFORMANCE", "medium"))
    parser.add_argument("--historical-start", default=os.getenv("DUNE_HISTORICAL_START", "2025-10-10"))
    parser.add_argument("--window-days", type=int, default=int(os.getenv("DUNE_WINDOW_DAYS", "7")))
    parser.add_argument(
        "--refresh-lookback-days",
        type=int,
        default=int(os.getenv("DUNE_REFRESH_LOOKBACK_DAYS", "2")),
    )
    parser.add_argument("--data-path", default=os.getenv("DUNE_DATA_PATH", str(DEFAULT_DATA_PATH)))
    parser.add_argument("--state-path", default=os.getenv("DUNE_STATE_PATH", str(DEFAULT_STATE_PATH)))
    parser.add_argument("--result-limit", type=int, default=int(os.getenv("DUNE_RESULT_LIMIT", "32000")))
    parser.add_argument("--timeout-seconds", type=int, default=int(os.getenv("DUNE_TIMEOUT_SECONDS", "3600")))
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=int(os.getenv("DUNE_POLL_INTERVAL_SECONDS", "15")),
    )
    parser.add_argument("--sync-upload", action="store_true", default=env_bool("DUNE_SYNC_UPLOAD"))
    parser.add_argument("--upload-namespace", default=os.getenv("DUNE_UPLOAD_NAMESPACE"))
    parser.add_argument("--upload-table", default=os.getenv("DUNE_UPLOAD_TABLE", "sunrise_tokens_dex_volume_daily"))
    parser.add_argument("--upload-private", action="store_true", default=env_bool("DUNE_UPLOAD_PRIVATE"))
    parser.add_argument("--create-upload-table", action="store_true", default=env_bool("DUNE_CREATE_UPLOAD_TABLE", True))
    parser.add_argument(
        "--clear-upload-before-insert",
        action="store_true",
        default=env_bool("DUNE_CLEAR_UPLOAD_BEFORE_INSERT", True),
    )
    parser.add_argument(
        "--upload-description",
        default=os.getenv(
            "DUNE_UPLOAD_DESCRIPTION",
            "Daily Sunrise token DEX volume fetched from Dune API and synced by GitHub Actions.",
        ),
    )
    args = parser.parse_args()
    args.api_key = require(args.api_key, "Missing DUNE_API_KEY.")

    if args.action == "deploy-query":
        args.create_query = args.create_query or not args.query_id

    if args.window_days < 1:
        raise SystemExit("--window-days must be at least 1.")

    return args


def main() -> None:
    args = parse_args()
    if args.action == "deploy-query":
        ensure_saved_query(args)
    else:
        sync_data(args, args.action)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
