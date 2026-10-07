from __future__ import annotations

import argparse
import random
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

DB_PATH = Path("osa.db")
PORTAL = "http://127.0.0.1:8765"
API_KEY = "dfhire-2026"
REQUEST_TIMEOUT = 10.0
MAX_RETRIES = 6
# 0.35s/request keeps sustained traffic below the mock portal's fair-use threshold
# while remaining much faster than waiting for a soft-ban to occur.
MIN_REQUEST_INTERVAL = 0.35


SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    store_id TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    name TEXT NOT NULL,
    is_active INTEGER NOT NULL,
    is_serviceable INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sweep_stores (
    store_id TEXT NOT NULL,
    sweep_ts TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('complete', 'incomplete')),
    reason TEXT,
    PRIMARY KEY (store_id, sweep_ts),
    FOREIGN KEY (store_id) REFERENCES stores(store_id)
);

CREATE TABLE IF NOT EXISTS inventory_observations (
    store_id TEXT NOT NULL,
    sweep_ts TEXT NOT NULL,
    sku_id TEXT NOT NULL,
    name TEXT NOT NULL,
    in_stock INTEGER NOT NULL,
    qty INTEGER,
    price REAL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (store_id, sweep_ts, sku_id),
    FOREIGN KEY (store_id) REFERENCES stores(store_id)
);

CREATE INDEX IF NOT EXISTS idx_inventory_city_day
ON inventory_observations (store_id, sweep_ts);
"""


@dataclass
class SweepResult:
    store_id: str
    complete: bool
    reason: str | None = None
    observations: int = 0


class PortalClient:
    def __init__(self, base_url: str = PORTAL) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"X-Api-Key": API_KEY})
        self._last_request = 0.0

    def _pace(self) -> None:
        wait = MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)

    def get(self, path: str, params: dict[str, Any]) -> requests.Response:
        last_error = "unknown error"
        for attempt in range(MAX_RETRIES):
            self._pace()
            try:
                response = self.session.get(
                    f"{self.base_url}{path}",
                    params=params,
                    timeout=REQUEST_TIMEOUT,
                )
                self._last_request = time.monotonic()
            except requests.RequestException as exc:
                self._last_request = time.monotonic()
                last_error = f"request error: {exc}"
                if attempt == MAX_RETRIES - 1:
                    raise RuntimeError(last_error) from exc
                time.sleep(self._backoff(attempt))
                continue

            if response.status_code == 200:
                return response

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                delay = self._retry_after(retry_after, attempt)
                last_error = "HTTP 429 rate limited"
                if attempt == MAX_RETRIES - 1:
                    break
                time.sleep(delay)
                continue

            if response.status_code in {500, 502, 503, 504}:
                last_error = f"HTTP {response.status_code}"
                if attempt == MAX_RETRIES - 1:
                    break
                time.sleep(self._backoff(attempt))
                continue

            # 400/401/404 are not transient. Retrying them just wastes time and
            # can make a bad upstream response look like a scraper problem.
            raise RuntimeError(
                f"non-retryable HTTP {response.status_code}: {response.text[:300]}"
            )

        raise RuntimeError(last_error)

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(8.0, 0.5 * (2**attempt)) + random.uniform(0, 0.2)

    @staticmethod
    def _retry_after(value: str | None, attempt: int) -> float:
        try:
            return max(0.5, min(30.0, float(value))) if value is not None else PortalClient._backoff(attempt)
        except ValueError:
            return PortalClient._backoff(attempt)

    def stores(self) -> list[dict[str, Any]]:
        stores: list[dict[str, Any]] = []
        page: int | None = 1
        while page is not None:
            body = self.get("/v1/stores", {"page": page}).json()
            stores.extend(body["stores"])
            page = body.get("next_page")
        return stores

    def inventory_page(self, store_id: str, as_of: str, cursor: str = "0") -> dict[str, Any]:
        return self.get(
            f"/v1/stores/{store_id}/inventory",
            {"as_of": as_of, "cursor": cursor},
        ).json()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def normalize_as_of(value: str) -> str:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("--as-of must include a timezone")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upsert_stores(conn: sqlite3.Connection, stores: list[dict[str, Any]]) -> None:
    conn.executemany(
        """
        INSERT INTO stores(store_id, city, name, is_active, is_serviceable)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(store_id) DO UPDATE SET
            city=excluded.city,
            name=excluded.name,
            is_active=excluded.is_active,
            is_serviceable=excluded.is_serviceable
        """,
        [
            (
                s["store_id"], s["city"], s["name"],
                int(s["is_active"]), int(s["is_serviceable"]),
            )
            for s in stores
        ],
    )
    conn.commit()


def fetch_complete_inventory(
    client: PortalClient, store_id: str, as_of: str
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch all pages, returning no rows unless the whole store snapshot is trusted."""
    cursor = "0"
    all_items: dict[str, dict[str, Any]] = {}
    soft_ban_retries = 0
    seen_cursors: set[str] = set()

    while True:
        if cursor in seen_cursors:
            return [], "pagination loop detected"
        seen_cursors.add(cursor)

        # A soft-banned response is HTTP 200 and can look valid. The edge source
        # is the explicit signal provided by the mock API that the response was
        # degraded; never silently treat it as an empty inventory page.
        try:
            body = client.inventory_page(store_id, as_of, cursor)
        except RuntimeError as exc:
            return [], str(exc)

        if body.get("meta", {}).get("source") == "edge":
            soft_ban_retries += 1
            if soft_ban_retries > 3:
                return [], "persistent soft ban/degraded edge response"
            time.sleep(min(30.0, 5.0 * (2 ** (soft_ban_retries - 1))))
            continue

        if body.get("partial") is True:
            return [], "portal marked inventory snapshot partial"

        for item in body.get("items", []):
            sku = item.get("sku_id")
            if not sku:
                return [], "inventory item missing sku_id"
            # The portal can repeat the final item of the previous page. SKU is
            # stable, so dedupe within this store+sweep before writing.
            all_items[sku] = item

        next_cursor = body.get("next_cursor")
        if next_cursor is None:
            return list(all_items.values()), None
        cursor = str(next_cursor)


def save_store_snapshot(
    conn: sqlite3.Connection,
    store_id: str,
    as_of: str,
    items: list[dict[str, Any]],
) -> None:
    # Delete first makes the operation idempotent even if an older local DB was
    # created before the primary key existed.
    with conn:
        conn.execute(
            "DELETE FROM inventory_observations WHERE store_id = ? AND sweep_ts = ?",
            (store_id, as_of),
        )
        conn.executemany(
            """
            INSERT INTO inventory_observations
                (store_id, sweep_ts, sku_id, name, in_stock, qty, price, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    store_id,
                    as_of,
                    item["sku_id"],
                    item["name"],
                    int(bool(item["in_stock"])),
                    item.get("qty"),
                    float(item["price"]),
                    item["observed_at"],
                )
                for item in items
            ],
        )
        conn.execute(
            """
            INSERT INTO sweep_stores(store_id, sweep_ts, status, reason)
            VALUES (?, ?, 'complete', NULL)
            ON CONFLICT(store_id, sweep_ts) DO UPDATE SET
                status='complete', reason=NULL
            """,
            (store_id, as_of),
        )


def mark_incomplete(conn: sqlite3.Connection, store_id: str, as_of: str, reason: str) -> None:
    with conn:
        # Do not retain a partial snapshot. This is the core correctness rule.
        conn.execute(
            "DELETE FROM inventory_observations WHERE store_id = ? AND sweep_ts = ?",
            (store_id, as_of),
        )
        conn.execute(
            """
            INSERT INTO sweep_stores(store_id, sweep_ts, status, reason)
            VALUES (?, ?, 'incomplete', ?)
            ON CONFLICT(store_id, sweep_ts) DO UPDATE SET
                status='incomplete', reason=excluded.reason
            """,
            (store_id, as_of, reason),
        )


def run_sweep(as_of: str, db_path: Path = DB_PATH, portal: str = PORTAL) -> list[SweepResult]:
    as_of = normalize_as_of(as_of)
    started = time.monotonic()
    client = PortalClient(portal)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    try:
        roster = client.stores()
        upsert_stores(conn, roster)
        # Active means the store belongs to the live network. Serviceability can
        # temporarily flip for operational reasons, so it is not used to exclude
        # an otherwise active store from inventory collection.
        tracked = [s for s in roster if s["is_active"]]
        results: list[SweepResult] = []

        for store in tracked:
            sid = store["store_id"]
            items, reason = fetch_complete_inventory(client, sid, as_of)
            if reason:
                mark_incomplete(conn, sid, as_of, reason)
                results.append(SweepResult(sid, False, reason))
            else:
                save_store_snapshot(conn, sid, as_of, items)
                results.append(SweepResult(sid, True, observations=len(items)))

        complete = sum(r.complete for r in results)
        incomplete = [r for r in results if not r.complete]
        elapsed = time.monotonic() - started
        print(f"{len(results)} stores: {complete} complete, {len(incomplete)} incomplete · {elapsed:.1f}s")
        for r in incomplete:
            print(f"  {r.store_id}: {r.reason}")
        return results
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one QuickMart inventory sweep")
    parser.add_argument("--as-of", required=True, help="ISO-8601 timestamp with timezone")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--portal", default=PORTAL)
    args = parser.parse_args()
    run_sweep(args.as_of, Path(args.db), args.portal)


if __name__ == "__main__":
    main()
