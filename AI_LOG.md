# AI Log

## Tools used

- ChatGPT: used for implementation discussion, code review, debugging, test-case design, and checking edge cases against the assignment requirements.

## Important prompts

1. I asked ChatGPT to help design the scraper around the QuickMart API, particularly pagination, retries, timeouts, rate limits, partial responses, and idempotent storage.

2. I asked ChatGPT to review the OSA calculation and explain how the UTC sweep timestamps should be mapped to IST reporting dates.

3. I asked ChatGPT to review the implementation and identify edge cases that could produce incorrect OSA numbers, then used those cases to design pytest tests.

## Where AI was wrong or needed correction

1. AI initially suggested using a `conftest.py` file to solve the pytest import issue when `test_osa.py` was under the `tests/` directory. I tested the import directly from the project root and confirmed that `app` already imported correctly. Instead of adding unnecessary test configuration, I moved `test_osa.py` to the project root and verified that all 5 tests passed.

2. During an earlier API check, AI treated the returned OSA value as if it were the final assignment result before all six required sweeps had been run. I checked the assignment's UTC-to-IST rule and realized that the final Sep 28 IST result requires three relevant sweeps. I ran all six required sweeps before treating the OSA values as final.

3. I independently verified AI-generated implementation suggestions against the mock portal, database, and tests rather than assuming they were correct. In particular, I verified the incomplete `DEL-004` snapshot, reran the same sweep to test idempotency, and confirmed that the OSA values remained unchanged.

4. AI suggestions were also evaluated against the assignment's data-integrity requirements. I deliberately kept the pipeline deterministic for critical OSA calculations: missing or incomplete inventory was not converted into out-of-stock data, and availability was based on the portal's `in_stock` field rather than inferred from quantity. This helped avoid producing plausible-looking but incorrect OSA numbers.