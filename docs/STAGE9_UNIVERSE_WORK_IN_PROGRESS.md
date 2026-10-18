# Stage 9 — universe expansion (work in progress)

Status: PARTIAL, NOT RELEASED. Branch isolates work from production main.

## Verified local additive prototype
- Offline CSV import of US/KR STOCK/ETF/OTHER into independent SQLite index.
- Atomic rejection of invalid symbols, duplicate rows and malformed CSV.
- Paginated symbol/name/market/kind query; no trade eligibility implied.
- 12,000 synthetic fixture rows loaded; five standalone unit tests passed.
- No live exchange catalog verification, no source data vendor API key, no active trade orders.
- Local prototype provided separately as Wealthy_Stage9_Partial_Catalog.zip.

## Required before stage-9 release
- Read and integrate current Stage 8 code/DB/API/UI; never overwrite owner data.
- Obtain and license-check actual complete US and Korean listed common stock and ETF universe files from trustworthy providers, monitor delistings/renames and stale timestamps.
- Preserve current account/approval/pause/market-price guards; search indexing must never imply trade eligibility.
- Integrate validated lists with UI and background refresh without resource exhaustion; real prices only where the existing vetted provider returns them.
- Run existing regression suite, frontend build, Ubuntu browser smoke, backup/restore, and reset-state packaging.
- Package complete clean source and execution instructions only after all checks.

DO NOT present this branch as full Stage 9 or claim every indexed instrument has current prices.
