.PHONY: install dev lint test migrate bot run-ingestion run-signals run-scoring run-notifier

install:
	poetry install --with dev

dev:
	poetry run honcho start -f Procfile

lint:
	poetry run ruff check .

test:
	poetry run pytest

migrate:
	poetry run alembic upgrade head

bot:
	poetry run python -m apps.bot.main

run-ingestion:
	poetry run python -m services.ingestion.worker

run-signals:
	poetry run python -m services.signals.worker

run-scoring:
	poetry run python -m services.scoring.worker

run-notifier:
	poetry run python -m services.notifier.worker
