"""
review_me.py: intentionally flawed sample for the review exercise.
The three fixes below address bounded transient retries, the mutable default,
and unsafe SQL construction. Other flaws are documented in REVIEW.md.
"""
import sqlite3
import time
from datetime import date, datetime, timedelta

import requests

PORTAL = "http://127.0.0.1:8765"
HEADERS = {"X-Api-Key": "dfhire-2026"}
TIMEOUT = 10
MAX_RETRIES = 5


def fetch_inventory(store_id, as_of, cursor="0", results=None):
    """Fetch inventory pages with bounded retries for transient failures."""
    if results is None:
        results = []

    while True:
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                r = requests.get(
                    f"{PORTAL}/v1/stores/{store_id}/inventory",
                    params={"as_of": as_of, "cursor": cursor},
                    headers=HEADERS,
                    timeout=TIMEOUT,
                )
                if r.status_code == 429:
                    delay = float(r.headers.get("Retry-After", "2"))
                    time.sleep(delay)
                    continue
                if r.status_code in {500, 502, 503, 504}:
                    time.sleep(min(8, 0.5 * (2**attempt)))
                    continue
                r.raise_for_status()
                body = r.json()
                break
            except requests.RequestException as exc:
                last_error = exc
                if attempt == MAX_RETRIES - 1:
                    raise
                time.sleep(min(8, 0.5 * (2**attempt)))
        else:
            raise RuntimeError(f"inventory request failed: {last_error}")
        break

    results.extend(body["items"])
    if body["next_cursor"]:
        return fetch_inventory(store_id, as_of, body["next_cursor"], results)
    return results


def save(conn, store_id, items):
    for it in items:
        conn.execute(
            "INSERT INTO inventory VALUES (?, ?, ?, ?, ?, ?)",
            (store_id, it["sku_id"], it["name"], int(it["in_stock"]), it["qty"], it["observed_at"]),
        )
    conn.commit()


def city_osa(conn, city, day=None):
    """On-shelf availability for a city on a day. Defaults to yesterday."""
    day = day or (date.today() - timedelta(days=1)).isoformat()
    stores = [r[0] for r in conn.execute(
        "SELECT store_id FROM stores WHERE city = ?", (city,))]
    per_store = []
    for s in stores:
        rows = conn.execute(
            "SELECT qty FROM inventory WHERE store_id = ? AND substr(observed_at, 1, 10) = ?",
            (s, day),
        ).fetchall()
        in_stock = sum(1 for (qty,) in rows if qty > 0)
        per_store.append(in_stock / len(rows) if rows else 0.0)
    return round(100 * sum(per_store) / len(per_store), 2)


if __name__ == "__main__":
    conn = sqlite3.connect("osa.db")
    conn.execute("CREATE TABLE IF NOT EXISTS stores (store_id TEXT, city TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS inventory (store_id TEXT, sku_id TEXT, name TEXT, "
                 "in_stock INT, qty INT, observed_at TEXT)")
    for sid in ["MUM-001", "MUM-002"]:
        save(conn, sid, fetch_inventory(sid, datetime.utcnow().isoformat()))
    print(city_osa(conn, "Mumbai"))
