# OpenSansad

A multi-agent system that makes Indian legislation legible: upcoming parliamentary business, session summaries, bill metadata (2006 onward), and clause-level amendment genealogy for acts.

See `CLAUDE.md` for scope, architecture, conventions and current status.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
cp .env.example .env      # then fill in USER_AGENT contact; API keys come later
make install              # uv sync (Python 3.12)
make up                   # postgres, redis, neo4j — waits until healthy
make migrate              # alembic upgrade head
```

## Tests

```bash
make test                 # offline; network access is blocked
make test-integration     # needs `make up`
```
