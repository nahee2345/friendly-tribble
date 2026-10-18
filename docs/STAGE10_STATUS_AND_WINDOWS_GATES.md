# Stage 10 status and Stage 11 Windows readiness

**IN PROGRESS — NOT APPROVED FOR MAIN**

Stage10 branch inherits unfinished Stage9 catalog import. Existing main untouched.

Delivered: standalone offline research metrics (20/60-day averages, 60-day momentum, volatility, drawdown); look-ahead and invalid quote rejection; research-only fail-closed trade gates; unavailable status for empty data; unit tests and non-destructive acceptance script.

Open release gates:
1. Wire the research module into current dashboard API and actual UI; never invent prices, news or historic P&L.
2. Offline news and current prices are impossible without existing saved observations. Public no-key providers still require internet.
3. Audit all 8 strategy calculations, coverage and trading guards; don't convert research scores into order authorization.
4. Complete Stage9 US/KR stock/ETF universe sourcing with data provenance, delisting changes, and Korean ETF completeness flags.
5. Verify CSRF/auth, SQLite backup and restart safety, emergency pause, strategy lock, quantity limits, position risk and auto-worker race safety.
6. Run all Python regression tests, frontend build, authenticated Ubuntu Chrome checks; attach actual results to release record.
7. Validate future Windows port against file permissions, paths, encoding, DB locking, timezone, scheduled process lifecycle and browser.
8. Do not delete user data or promote this branch to main until all gates pass.

Safe inspect-only commands:

    cd /home/ww/wealthy-github
    git status --short
    git fetch origin stage10-offline-hardening
    git diff --stat main..origin/stage10-offline-hardening

No branch switch or reset of user production environment is authorized here.
