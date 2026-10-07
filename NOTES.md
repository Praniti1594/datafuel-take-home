# Notes

## Store selection

I track every store where `is_active=true`. I do not filter on `is_serviceable`: serviceability is described by the API as something that can flip during the day for operational reasons, while an active store remains part of the network and can still have inventory. This also avoids incorrectly including inactive stores that happen to be marked serviceable.

## Handling the portal's failures

- `429`: respect `Retry-After`, with a bounded fallback backoff.
- `500/503`: retry with exponential backoff and jitter.
- Network errors/timeouts: retry with a finite timeout and bounded attempts so one bad endpoint cannot hang the sweep forever.
- Slow calls: 10-second request timeout.
- Soft ban: a `200` response whose `meta.source` is `edge` is treated as degraded, retried after a longer delay, and never silently accepted as an empty snapshot.
- `partial=true`: discard the store's buffered pages and mark that store+sweep incomplete.
- Duplicate page items: deduplicate by stable `sku_id` within the store+sweep.
- Pagination loops: detect a repeated cursor and mark the store incomplete.
- Idempotency: observations use `(store_id, sweep_ts, sku_id)` as the primary key and are replaced atomically for a rerun.

## Brand says Delhi availability fell from 92% to 41%

Before replying, I would first verify data coverage and freshness: which sweeps/stores are incomplete, whether the set of active stores changed, and whether there was an upstream/API anomaly. Then I would compare the raw store/SKU observations against the previous day's calculation and verify the IST date boundary. A large metric change should not be trusted until the underlying observations and coverage are sound.

## Dashboard says city sales ₹4.20 lakh but store totals add to ₹4.61 lakh

I would not silently choose either number. I would flag the discrepancy and investigate the definition, time window, refunds/cancellations, store inclusion, and aggregation logic. Until reconciled, the brand-facing dashboard should show the trusted canonical metric with a clear data-quality warning rather than presenting a number known to conflict with another source.

## Where I would not use an AI/LLM

I would not put an LLM in the critical numeric pipeline for inventory facts, OSA calculations, deduplication, completeness decisions, or financial aggregation. These need deterministic, testable rules. An LLM could assist with explanations or anomaly triage, but it should not decide the underlying numbers.

## Optional scaling idea: 20,000 stores every 30 minutes

I would move from a single-process sequential sweep to a queue-based worker architecture. Store/sweep jobs could be partitioned across workers with per-partner rate limits, retries and dead-letter queues. I would use Postgres for durable coordination and ClickHouse or another analytical store for high-volume observations, with idempotency keys and metrics for latency, retries, completeness and partner health.
