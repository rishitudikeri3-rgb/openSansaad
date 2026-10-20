import socket
from pathlib import Path

import pytest

from opensansad.config import Settings


def test_package_imports() -> None:
    import opensansad.db.base  # noqa: F401
    import opensansad.db.session  # noqa: F401
    import opensansad.graph.client  # noqa: F401
    import opensansad.tasks.celery_app  # noqa: F401


def test_settings_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/x")
    monkeypatch.setenv("MODEL_REASONING", "some-model")
    monkeypatch.setenv("DATA_DIR", "/tmp/os-data")
    s = Settings(_env_file=None)
    assert s.database_url == "postgresql+psycopg://u:p@db:5432/x"
    assert s.model_reasoning == "some-model"
    assert s.raw_dir == Path("/tmp/os-data/raw")
    assert s.text_dir == Path("/tmp/os-data/text")


def test_model_names_have_no_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("MODEL_REASONING", "MODEL_BULK", "LLM_BASE_URL", "LLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.model_reasoning is None
    assert s.model_bulk is None
    assert s.llm_base_url is None
    assert s.llm_api_key is None


def test_celery_ping_runs_locally() -> None:
    from opensansad.tasks.celery_app import app, ping

    assert app.main == "opensansad"
    assert ping.apply().get() == "pong"


def test_network_is_blocked_offline() -> None:
    with pytest.raises(RuntimeError, match="Network access"):
        socket.create_connection(("127.0.0.1", 9), timeout=0.1)


def test_relative_data_dir_anchored_to_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    from opensansad.config import PROJECT_ROOT

    monkeypatch.setenv("DATA_DIR", "data")
    monkeypatch.chdir("/tmp")
    s = Settings(_env_file=None)
    assert s.data_dir == PROJECT_ROOT / "data"
    assert (PROJECT_ROOT / "pyproject.toml").exists()
