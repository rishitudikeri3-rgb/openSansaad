.PHONY: install up down logs test test-integration migrate lint

install:
	uv sync

up:
	docker compose up -d --wait

down:
	docker compose down

logs:
	docker compose logs -f

test:
	uv run pytest

test-integration:
	uv run pytest -m integration

migrate:
	uv run alembic upgrade head

lint:
	uv run ruff check src tests
