# Review of `review_me.py`

## 1. Infinite retry can hang forever

`fetch_inventory()` catches every exception and loops forever. A permanently invalid request, authentication failure, or broken endpoint can therefore make the process never finish. For example, the current caller sends a naive timestamp (`datetime.utcnow().isoformat()`), but QuickMart requires a timezone; the API returns 400 and the function retries forever.

**Fix:** use a finite retry budget, a request timeout, and retry only transient statuses/network errors.

## 2. No request timeout

`requests.get()` has no timeout. The mock portal intentionally sleeps for 8 seconds on some requests, and a real partner can hang indefinitely. One request can therefore stall the entire sweep.

**Fix:** add a bounded request timeout.

## 3. `429` is retried every 100ms and ignores `Retry-After`

The portal explicitly tells the client how long to wait after a rate limit. Sleeping only 0.1 seconds causes more traffic while the client is already rate limited and can contribute to the longer fair-use degradation.

**Fix:** honor `Retry-After` and use exponential backoff for transient errors.

## 4. Mutable default argument leaks results between calls

`results=[]` is created once and reused across function calls. Calling `fetch_inventory("MUM-001", ...)` and later calling it for another store without supplying `results` can return the first store's items as well. That can produce cross-store contamination.

**Fix:** default to `None` and create a new list inside the function.

## 5. SQL is built with string interpolation

`save()` constructs SQL using f-strings. A product name containing `'` breaks the SQL, and untrusted text could become SQL injection. Even a harmless name such as `Farmer's Bread` would generate invalid SQL.

**Fix:** use parameterized SQL placeholders.

## 6. Pagination duplicates are stored as separate observations

QuickMart can repeat the previous page's final SKU at the start of the next page. `results.extend()` accepts both copies, so one real observation can count twice and distort OSA.

## 7. The database has no sweep timestamp

The `inventory` table stores `observed_at` but not the requested `as_of` sweep timestamp. Two sweeps can therefore become difficult to distinguish, and rerunning a sweep cannot be made safely idempotent.

## 8. Partial snapshots are not represented

The API explicitly provides `partial=true`, but this code ignores it. A partial snapshot could be stored and then treated as complete, making missing data look like genuine low availability.

## 9. OSA uses `qty` instead of the API's `in_stock` field

The contract says `in_stock` is the availability flag. The mock deliberately creates cases where `in_stock=true` but `qty=0` (a ghost-stock case). The code would incorrectly mark those observations as out of stock.

## 10. It averages store percentages instead of calculating the requested city ratio

The assignment defines city OSA as total in-stock observations divided by total observed observations. The code first calculates one percentage per store and then averages those percentages, giving a small store the same weight as a large store.

## 11. It groups by `observed_at`'s text date instead of the sweep's IST date

Delhi timestamps are returned with an IST offset while other cities are returned in UTC. More importantly, the assignment says the reporting day is based on the sweep timestamp converted to IST. A sweep at `2026-09-27T19:00:00Z` belongs to Sep 28 IST, but the current query would not reliably place it there.

## 12. Empty stores are treated as 0% and included in the average

A store with no rows contributes `0.0` to `per_store`. That turns "no observations" into "0% availability", which violates the golden rule in the assignment.

## 13. The example only processes two hard-coded stores

The real task requires reading the paginated store roster and processing every store that should be tracked.

## Fixes made

The original file is intentionally left structurally recognizable. The three most serious implementation fixes are applied there: bounded/status-aware retries with a timeout, removal of the mutable default, and parameterized SQL. The other issues are documented above because the final scraper/API uses a separate, correct data model.
