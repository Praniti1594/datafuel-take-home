# DataFuel Take-Home

Python scraper + SQLite database + FastAPI OSA reporting API for the QuickMart mock portal.

## Requirements

- Python 3.10+
- `pip install -r requirements.txt`

## Run

Terminal 1:

```bash
python mock_portal.py
```

Terminal 2:

```bash
python -m pip install -r requirements.txt
python sweep.py --as-of 2026-09-27T04:30:00Z
python sweep.py --as-of 2026-09-27T10:30:00Z
python sweep.py --as-of 2026-09-27T19:00:00Z
python sweep.py --as-of 2026-09-28T04:30:00Z
python sweep.py --as-of 2026-09-28T10:30:00Z
python sweep.py --as-of 2026-09-28T18:40:00Z
```

The scraper is safe to run again for the same timestamp.

Start the API:

```bash
uvicorn app:app --reload
```

Then:

```text
GET http://127.0.0.1:8000/osa?city=Mumbai&date=2026-09-28
GET http://127.0.0.1:8000/osa?city=Delhi&date=2026-09-28
GET http://127.0.0.1:8000/osa?city=Bengaluru&date=2026-09-28
```

Run tests:

```bash
pytest -q
```

## Design choices

- Track **active** stores. `is_serviceable` is deliberately not the filter because serviceability is an operational condition that can temporarily change; an active store can still contain inventory.
- Treat a store+sweep as atomic: buffer all pages first and only save them after every page is complete.
- Retry `429`, `500`, `503`, and network/timeouts with bounded exponential backoff. Respect `Retry-After` for `429`.
- Pace requests at 0.35 seconds apart to stay below the portal's sustained fair-use threshold.
- A `200` response from `meta.source=edge` is treated as a degraded/soft-banned response, not as a valid empty page.
- Deduplicate inventory by SKU within a store+sweep because the portal may repeat the last item from the previous page.
- Use the API's `in_stock` flag for OSA, never infer availability from quantity.
- Assign sweeps to reporting dates using the **sweep `as_of` timestamp converted from UTC to IST**.
- Incomplete store+sweeps are excluded from OSA and surfaced in `coverage` rather than converted into out-of-stock observations.
- The database primary key `(store_id, sweep_ts, sku_id)` prevents duplicate observations for the same store, sweep, and SKU.

## Tests

The test suite is in `test_osa.py`.

Run all tests with:

```bash
pytest -q
```