.PHONY: install up down logs test test-integration migrate lint worker demo site chat-worker chat-worker-key

install:
	uv sync --all-extras

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

worker:
	uv run celery -A opensansad.tasks.celery_app worker --loglevel=info --concurrency=2

demo:
	uv run uvicorn opensansad.api.app:app --host 127.0.0.1 --port 8000

site:
	rm -rf site && uv run python -m opensansad.api.app export site

# chat router for the static demo (Cloudflare Worker, needs Node + `npx wrangler login`)
chat-worker:
	cd worker && npx --yes wrangler@4 deploy

# copies LLM_API_KEY from .env into the Worker's secrets (never printed)
chat-worker-key:
	@grep -m1 '^LLM_API_KEY=' .env | cut -d= -f2- | (cd worker && npx --yes wrangler@4 secret put LLM_API_KEY)
