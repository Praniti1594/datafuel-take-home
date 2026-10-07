import sqlite3
from datetime import datetime, timezone

import app

SCHEMA = """
CREATE TABLE stores (store_id TEXT PRIMARY KEY, city TEXT, name TEXT, is_active INTEGER, is_serviceable INTEGER);
CREATE TABLE sweep_stores (store_id TEXT, sweep_ts TEXT, status TEXT, reason TEXT, PRIMARY KEY(store_id,sweep_ts));
CREATE TABLE inventory_observations (store_id TEXT, sweep_ts TEXT, sku_id TEXT, name TEXT, in_stock INTEGER, qty INTEGER, price REAL, observed_at TEXT, PRIMARY KEY(store_id,sweep_ts,sku_id));
"""


def seed_db(tmp_path):
    db = tmp_path / "osa.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO stores VALUES (?,?,?,?,?)", [
        ("MUM-001", "Mumbai", "M1", 1, 1),
        ("MUM-002", "Mumbai", "M2", 1, 1),
    ])
    conn.executemany("INSERT INTO sweep_stores VALUES (?,?,?,?)", [
        ("MUM-001", "2026-09-28T04:30:00Z", "complete", None),
        ("MUM-002", "2026-09-28T04:30:00Z", "complete", None),
    ])
    conn.executemany("INSERT INTO inventory_observations VALUES (?,?,?,?,?,?,?,?)", [
        ("MUM-001", "2026-09-28T04:30:00Z", "SKU-1", "Milk", 1, 0, 10, "2026-09-28T04:30:00Z"),
        ("MUM-001", "2026-09-28T04:30:00Z", "SKU-2", "Bread", 0, 10, 20, "2026-09-28T04:30:00Z"),
        ("MUM-002", "2026-09-28T04:30:00Z", "SKU-1", "Milk", 0, 10, 10, "2026-09-28T04:30:00Z"),
    ])
    conn.commit(); conn.close()
    return db


def test_osa_uses_in_stock_not_qty(tmp_path, monkeypatch):
    db = seed_db(tmp_path); monkeypatch.setattr(app, "DB_PATH", db)
    result = app.osa("Mumbai", "2026-09-28")
    assert result["observations"] == 3
    assert result["osa_pct"] == 33.33


def test_incomplete_store_is_excluded(tmp_path, monkeypatch):
    db = seed_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE sweep_stores SET status='incomplete', reason='partial' WHERE store_id='MUM-002'")
    conn.commit(); conn.close()
    monkeypatch.setattr(app, "DB_PATH", db)
    result = app.osa("Mumbai", "2026-09-28")
    assert result["observations"] == 2
    assert result["osa_pct"] == 50.0
    assert result["coverage"]["stores_complete"] == 1


def test_utc_sweep_is_assigned_to_ist_day(tmp_path, monkeypatch):
    db = tmp_path / "osa.db"
    conn = sqlite3.connect(db); conn.executescript(SCHEMA)
    conn.execute("INSERT INTO stores VALUES (?,?,?,?,?)", ("MUM-001","Mumbai","M1",1,1))
    # 19:00 UTC on Sep 27 is Sep 28 in IST.
    ts="2026-09-27T19:00:00Z"
    conn.execute("INSERT INTO sweep_stores VALUES (?,?,?,?)", ("MUM-001",ts,"complete",None))
    conn.execute("INSERT INTO inventory_observations VALUES (?,?,?,?,?,?,?,?)", ("MUM-001",ts,"SKU-1","Milk",1,1,10,"2026-09-27T19:00:00Z"))
    conn.commit(); conn.close()
    monkeypatch.setattr(app, "DB_PATH", db)
    assert app.osa("Mumbai", "2026-09-28")["observations"] == 1
    assert app.osa("Mumbai", "2026-09-27")["observations"] == 0


def test_no_data_is_not_zero_percent(tmp_path, monkeypatch):
    db=seed_db(tmp_path); monkeypatch.setattr(app,"DB_PATH",db)
    result=app.osa("Mumbai","2026-09-29")
    assert result["status"] == "no_data"
    assert result["osa_pct"] is None


def test_duplicate_sku_cannot_be_stored_twice(tmp_path):
    db=tmp_path/"x.db"
    conn=sqlite3.connect(db); conn.executescript(SCHEMA)
    conn.execute("INSERT INTO inventory_observations VALUES (?,?,?,?,?,?,?,?)", ("MUM-001","ts","SKU-1","Milk",1,1,10,"obs"))
    try:
        conn.execute("INSERT INTO inventory_observations VALUES (?,?,?,?,?,?,?,?)", ("MUM-001","ts","SKU-1","Milk",1,1,10,"obs"))
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("duplicate observation was accepted")
