"""The only LLM entry point (rule 1's counterpart for model calls): JSON-mode chat completions
against the OpenAI-compatible endpoint in config, cached on disk.

Every call is keyed by (base_url, model, messages): a repeated call is served from
`DATA_DIR/llm_cache/` and never re-sent, so runs are reproducible and free-tier quotas are
spent once. Rate limits and server errors are retried with exponential backoff.
"""

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from opensansad.config import get_settings

log = logging.getLogger(__name__)

Role = Literal["reasoning", "bulk"]
# (model, messages) -> raw JSON text; replaced in tests
Transport = Callable[[str, list[dict]], str]


class LLMError(RuntimeError):
    pass


@dataclass
class LLM:
    models: dict[str, str]
    cache_dir: Path | None
    transport: Transport
    base_url: str = ""
    calls: int = 0  # requests sent (cache misses)
    hits: int = 0
    log: list[dict] = field(default_factory=list)

    @classmethod
    def from_settings(cls) -> "LLM":
        s = get_settings()
        if not (s.llm_base_url and s.llm_api_key and s.model_reasoning and s.model_bulk):
            raise LLMError("set LLM_BASE_URL, LLM_API_KEY, MODEL_REASONING, MODEL_BULK in .env")
        return cls({"reasoning": s.model_reasoning, "bulk": s.model_bulk},
                   s.data_dir / "llm_cache", _openai_transport(s.llm_base_url,
                                                               s.llm_api_key.get_secret_value()),
                   base_url=s.llm_base_url)

    def json(self, role: Role, system: str, user: str) -> dict:
        model = self.models[role]
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        key = hashlib.sha256(json.dumps([self.base_url, model, messages],
                                        sort_keys=True).encode()).hexdigest()
        path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if path and path.exists():
            self.hits += 1
            raw = json.loads(path.read_text())["response"]
        else:
            raw = self.transport(model, messages)
            self.calls += 1
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"model": model, "messages": messages,
                                           "response": raw}, ensure_ascii=False, indent=1))
                tmp.replace(path)
        self.log.append({"role": role, "model": model, "key": key[:12]})
        try:
            out = json.loads(raw)
        except json.JSONDecodeError as e:
            raise LLMError(f"model returned non-JSON: {raw[:200]!r}") from e
        if not isinstance(out, dict):
            raise LLMError(f"model returned {type(out).__name__}, not an object")
        return out


def _openai_transport(base_url: str, api_key: str, *, attempts: int = 6) -> Transport:
    from langchain_openai import ChatOpenAI
    from openai import APIConnectionError, APIStatusError, RateLimitError

    clients: dict[str, ChatOpenAI] = {}

    def send(model: str, messages: list[dict]) -> str:
        client = clients.get(model) or clients.setdefault(model, ChatOpenAI(
            model=model, base_url=base_url, api_key=api_key, temperature=0, max_retries=0,
            timeout=120, model_kwargs={"response_format": {"type": "json_object"}}))
        delay = 4.0
        for attempt in range(attempts):
            try:
                return str(client.invoke([(m["role"], m["content"]) for m in messages]).content)
            except (RateLimitError, APIConnectionError) as e:
                err: Exception = e
            except APIStatusError as e:
                if e.status_code < 500:
                    raise LLMError(f"{model}: HTTP {e.status_code}: {e.message}") from e
                err = e
            log.warning("%s: %s; retry %d in %.0fs", model, type(err).__name__, attempt + 1,
                        delay)
            time.sleep(delay)
            delay = min(delay * 2, 120)
        raise LLMError(f"{model}: gave up after {attempts} attempts")

    return send
