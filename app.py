from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query

DB_PATH = Path(os.environ.get("OSA_DB", "osa.db"))
IST = timezone(timedelta(hours=5, minutes=30))
VALID_CITIES = {"Mumbai", "Delhi", "Bengaluru"}

app = FastAPI(title="DataFuel OSA API", version="1.0.0")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ist_date_from_utc(ts: str) -> date:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.astimezone(IST).date()


def get_target_date(value: str | None) -> date:
    if value is None:
        return datetime.now(IST).date() - timedelta(days=1)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/osa")
def osa(
    city: str = Query(...),
    date_value: str | None = Query(default=None, alias="date"),
) -> dict[str, Any]:
    if city not in VALID_CITIES:
        raise HTTPException(status_code=400, detail="city must be Mumbai, Delhi or Bengaluru")

    target = get_target_date(date_value)
    conn = connect()
    try:
        stores = conn.execute(
            "SELECT store_id FROM stores WHERE city = ? AND is_active = 1 ORDER BY store_id",
            (city,),
        ).fetchall()
        expected_ids = [r["store_id"] for r in stores]

        sweep_rows = conn.execute(
            "SELECT DISTINCT sweep_ts FROM sweep_stores ORDER BY sweep_ts"
        ).fetchall()
        target_sweeps = [
            r["sweep_ts"] for r in sweep_rows if ist_date_from_utc(r["sweep_ts"]) == target
        ]

        incomplete: list[dict[str, str]] = []
        complete_store_ids: set[str] = set()
        if target_sweeps:
            for sid in expected_ids:
                store_complete = True
                for sweep in target_sweeps:
                    row = conn.execute(
                        """
                        SELECT status, reason FROM sweep_stores
                        WHERE store_id = ? AND sweep_ts = ?
                        """,
                        (sid, sweep),
                    ).fetchone()
                    if row is None or row["status"] != "complete":
                        store_complete = False
                        incomplete.append({
                            "store_id": sid,
                            "sweep": sweep,
                            "reason": row["reason"] if row else "no sweep result recorded",
                        })
                if store_complete:
                    complete_store_ids.add(sid)

        # Only complete store+sweep observations contribute to the metric. This
        # prevents a partial store snapshot from depressing or inflating OSA.
        if target_sweeps:
            placeholders = ",".join("?" for _ in target_sweeps)
            rows = conn.execute(
                f"""
                SELECT i.sku_id, i.name, i.in_stock
                FROM inventory_observations i
                JOIN stores s ON s.store_id = i.store_id
                JOIN sweep_stores ss
                  ON ss.store_id = i.store_id AND ss.sweep_ts = i.sweep_ts
                WHERE s.city = ?
                  AND s.is_active = 1
                  AND ss.status = 'complete'
                  AND i.sweep_ts IN ({placeholders})
                """,
                (city, *target_sweeps),
            ).fetchall()
        else:
            rows = []

        observations = len(rows)
        in_stock = sum(int(r["in_stock"]) for r in rows)
        sku_counts: dict[str, dict[str, Any]] = {}
        for row in rows:
            sku = row["sku_id"]
            entry = sku_counts.setdefault(
                sku,
                {"sku_id": sku, "name": row["name"], "observations": 0, "in_stock": 0},
            )
            entry["observations"] += 1
            entry["in_stock"] += int(row["in_stock"])
            # Keep the most recent display name if a product was renamed.
            entry["name"] = row["name"]

        skus = []
        for entry in sorted(sku_counts.values(), key=lambda x: x["sku_id"]):
            entry["osa_pct"] = round(100 * entry["in_stock"] / entry["observations"], 2)
            skus.append(entry)

        coverage = {
            "stores_expected": len(expected_ids),
            "stores_complete": len(complete_store_ids),
            "sweeps_expected": len(target_sweeps),
            "incomplete": incomplete,
        }

        result: dict[str, Any] = {
            "city": city,
            "date": target.isoformat(),
            "status": "ok" if observations else "no_data",
            "osa_pct": round(100 * in_stock / observations, 2) if observations else None,
            "observations": observations,
            "coverage": coverage,
            "skus": skus,
        }
        return result
    finally:
        conn.close()
