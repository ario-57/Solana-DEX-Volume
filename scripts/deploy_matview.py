#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib import error, parse, request


API_BASE = os.getenv("DUNE_API_BASE", "https://api.dune.com/api/v1").rstrip("/")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQL_PATH = ROOT / "sql" / "sunrise_tokens_dex_volume_7d.sql"
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


def require(value: str | None, message: str) -> str:
    if not value:
        raise SystemExit(message)
    return value


def dune_request(method: str, path: str, api_key: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "X-Dune-Api-Key": api_key,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"

    req = request.Request(f"{API_BASE}{path}", data=body, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=60) as res:
            raw = res.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Dune API {method} {path} failed with HTTP {exc.code}: {raw}") from exc


def poll_execution(api_key: str, execution_id: str, timeout_seconds: int, interval_seconds: int) -> dict:
    deadline = time.time() + timeout_seconds
    last_status: dict = {}

    while time.time() < deadline:
        last_status = dune_request("GET", f"/execution/{execution_id}/status", api_key)
        state = last_status.get("state")
        print(f"execution_id={execution_id} state={state}")

        if state in TERMINAL_STATES:
            if state != "QUERY_STATE_COMPLETED":
                print(json.dumps(last_status, indent=2, sort_keys=True))
                raise SystemExit(f"Dune execution finished in non-success state: {state}")
            return last_status

        time.sleep(interval_seconds)

    print(json.dumps(last_status, indent=2, sort_keys=True))
    raise SystemExit(f"Timed out waiting for execution_id={execution_id}")


def deploy(args: argparse.Namespace) -> None:
    sql = Path(args.sql).read_text(encoding="utf-8")
    query_id = args.query_id

    query_payload = {
        "name": args.query_name,
        "description": args.description,
        "query_sql": sql,
        "is_private": args.private,
        "tags": args.tags,
    }

    if query_id:
        response = dune_request("PATCH", f"/query/{query_id}", args.api_key, query_payload)
        query_id = str(response.get("query_id", query_id))
        print(f"Updated Dune query_id={query_id}")
    elif args.create_query:
        response = dune_request("POST", "/query", args.api_key, query_payload)
        query_id = str(response["query_id"])
        print(f"Created Dune query_id={query_id}")
        print("Save this as a GitHub Actions repository variable named DUNE_QUERY_ID.")
    else:
        raise SystemExit(
            "Deploy needs DUNE_QUERY_ID, or run once with DUNE_CREATE_QUERY=true "
            "to create the saved Dune query."
        )

    matview_payload = {
        "name": args.matview_name,
        "query_id": int(query_id),
        "is_private": args.private,
    }
    if args.performance:
        matview_payload["performance"] = args.performance
    if args.matview_cron:
        matview_payload["cron_expression"] = args.matview_cron
    if args.expires_at:
        matview_payload["expires_at"] = args.expires_at

    response = dune_request("POST", "/materialized-views", args.api_key, matview_payload)
    print(f"Upserted materialized view: {response.get('name', args.matview_name)}")
    if response.get("name"):
        print("Save the full name as DUNE_MATVIEW_FULL_NAME if refresh by short name fails.")

    execution_id = response.get("execution_id")
    if execution_id and not args.no_wait:
        poll_execution(args.api_key, execution_id, args.timeout_seconds, args.poll_interval_seconds)


def refresh(args: argparse.Namespace) -> None:
    matview_ref = args.matview_full_name or args.matview_name
    encoded_name = parse.quote(matview_ref, safe="")
    payload = {}
    if args.performance:
        payload["performance"] = args.performance

    response = dune_request(
        "POST",
        f"/materialized-views/{encoded_name}/refresh",
        args.api_key,
        payload,
    )
    print(f"Triggered refresh for {response.get('sql_id', matview_ref)}")

    execution_id = response.get("execution_id")
    if execution_id and not args.no_wait:
        poll_execution(args.api_key, execution_id, args.timeout_seconds, args.poll_interval_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy or refresh the Sunrise tokens Dune matview.")
    parser.add_argument(
        "action",
        nargs="?",
        choices=("deploy", "refresh"),
        default=os.getenv("DUNE_ACTION", "refresh"),
    )
    parser.add_argument("--api-key", default=os.getenv("DUNE_API_KEY"))
    parser.add_argument("--query-id", default=os.getenv("DUNE_QUERY_ID"))
    parser.add_argument("--create-query", action="store_true", default=env_bool("DUNE_CREATE_QUERY"))
    parser.add_argument("--sql", default=os.getenv("DUNE_SQL_PATH", str(DEFAULT_SQL_PATH)))
    parser.add_argument(
        "--query-name",
        default=os.getenv("DUNE_QUERY_NAME", "Sunrise tokens DEX volume - 7 day buckets"),
    )
    parser.add_argument(
        "--description",
        default=os.getenv(
            "DUNE_QUERY_DESCRIPTION",
            "Seven-day DEX volume buckets for Sunrise tokens on Solana, anchored on 2025-10-10.",
        ),
    )
    parser.add_argument(
        "--matview-name",
        default=os.getenv("DUNE_MATVIEW_NAME", "result_sunrise_tokens_dex_volume_7d"),
    )
    parser.add_argument("--matview-full-name", default=os.getenv("DUNE_MATVIEW_FULL_NAME"))
    parser.add_argument("--matview-cron", default=os.getenv("DUNE_MATVIEW_CRON", ""))
    parser.add_argument("--expires-at", default=os.getenv("DUNE_MATVIEW_EXPIRES_AT", ""))
    parser.add_argument("--performance", default=os.getenv("DUNE_PERFORMANCE", "medium"))
    parser.add_argument("--private", action="store_true", default=env_bool("DUNE_PRIVATE", True))
    parser.add_argument(
        "--tags",
        type=split_csv,
        default=split_csv(os.getenv("DUNE_TAGS", "sunrise,solana,dex-volume")),
    )
    parser.add_argument("--no-wait", action="store_true", default=env_bool("DUNE_NO_WAIT"))
    parser.add_argument("--timeout-seconds", type=int, default=int(os.getenv("DUNE_TIMEOUT_SECONDS", "3600")))
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=int(os.getenv("DUNE_POLL_INTERVAL_SECONDS", "15")),
    )
    args = parser.parse_args()
    args.api_key = require(args.api_key, "Missing DUNE_API_KEY.")

    if args.action == "refresh" and not (args.matview_full_name or args.matview_name):
        raise SystemExit("Refresh needs DUNE_MATVIEW_FULL_NAME or DUNE_MATVIEW_NAME.")

    return args


def main() -> None:
    args = parse_args()
    if args.action == "deploy":
        deploy(args)
    else:
        refresh(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
