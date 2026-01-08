# polymarket-watch

Bootstrap for monitoring Polymarket data with Python 3.11, Poetry, Ruff, Pytest, Alembic, SQLAlchemy, httpx, and python-telegram-bot.

## Quickstart
1. Install Poetry (>=1.7).
2. Copy `.env.example` to `.env` and set values (DB, Telegram, etc.).
3. Start Postgres: `docker-compose up -d postgres`.
4. Install dependencies: `make install`.
5. Run database migrations: `make migrate` (or `python scripts/init_db.py`).
6. Run the full stack: `make dev` (honcho starts ingestion, profiling, signals, scoring, notifier, bot).
10. Start the notifier worker: `make run-notifier` (set `TELEGRAM_CHAT_ID`, unset `NOTIFIER_DRY_RUN` to send).

## Tooling
- `make install` installs dependencies with dev extras.
- `make lint` runs Ruff.
- `make test` runs pytest.
- Logging defaults to JSON; set `LOG_FORMAT=console` for human-readable logs.
- Database URL resolves from `DATABASE_URL` or individual DB settings.
- Ingestion worker polls markets every 10 minutes and trades every 30-60s with backoff on errors.
- Signals worker consumes trades since last cursor, evaluates triggers, and writes to `signal_events`.
- Scoring worker aggregates recent signals into alerts with cooldown dedupe.
- Notifier worker streams alerts to Telegram (dry-run by default).
- Profiling worker placeholder runs on an interval.
- `make dev` uses `honcho` + `Procfile` to run ingestion, profiling, signals, scoring, notifier, and bot processes together.
- Backtest: use `scripts/backfill_trades.py` (historical trades), `scripts/replay.py` (replay signals/scoring), `scripts/evaluate_alerts.py` (price deltas), `scripts/report_backtest.py` (summary + `backtest_report.json`).

## Apps
- `apps/bot/main.py` exposes `/ping` -> `pong` for a minimal health check.
- `apps/api/` is a placeholder for future HTTP interfaces.

## Database & Migrations
- Postgres is provided via `docker-compose.yml`.
- Alembic migration scripts live in `db/migrations` with versions under `db/migrations/versions`.
- Run migrations with `make migrate` or `python scripts/init_db.py` (uses `alembic.ini` at repo root).
- Key env vars: `DATABASE_URL` (preferred), or `DATABASE_HOST/DATABASE_PORT/DATABASE_USER/DATABASE_PASSWORD/DATABASE_NAME`; `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` for bot/notifier; `NOTIFIER_DRY_RUN`; `LOG_LEVEL/LOG_FORMAT`; ingestion URLs/intervals; optional `HONCHO_PORT` to set procfile port env.

## Backtesting
- Backfill trades: `poetry run python scripts/backfill_trades.py --days 30 --market-limit 200 --concurrency 10`
- Replay signals/scoring: `poetry run python scripts/replay.py --start 2024-01-01 --end 2024-01-31 --batch-size 500 --speed 0`
- Evaluate alert outcomes: `poetry run python scripts/evaluate_alerts.py`
- Report summary: `poetry run python scripts/report_backtest.py` (writes `backtest_report.json`)

## Troubleshooting
- If migrations fail, ensure Postgres is running and `DATABASE_URL` is correct.
- Bot/notifier will refuse to send without `TELEGRAM_BOT_TOKEN`; notifier stays dry-run if `NOTIFIER_DRY_RUN` is true or `TELEGRAM_CHAT_ID` missing.
- Honcho: install via `make install` (pulls dev dependency). Use `honcho start -f Procfile <process>` to run a single process.
- If services exit immediately, check logs for missing env vars or database connectivity.
