"""Unified LLM client that can route requests to OpenRouter or Ollama.

This module provides `query_model` and `query_models_parallel` with the
same interface as the previous `openrouter` module, but allows selecting
the provider per-call using the `provider` argument ('openrouter' or 'ollama').
If `provider` is None, the default behavior uses config.USE_OLLAMA to
decide.
"""

from typing import List, Dict, Any, Optional

from . import openrouter
from . import ollama
from .config import USE_OLLAMA
import time


def _should_use_ollama(provider: Optional[str]) -> bool:
    if provider is None:
        return USE_OLLAMA
    return str(provider).lower() in ("ollama", "local")


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
    provider: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_ollama = _should_use_ollama(provider)
    # Lightweight request logging for debugging provider/model routing and timings
    start = time.time()
    print(f"[LLM_CLIENT] start provider={provider} resolved_ollama={resolved_ollama} model={model}")
    try:
        if resolved_ollama:
            res = await ollama.query_model(model, messages, timeout=timeout)
        else:
            res = await openrouter.query_model(model, messages, timeout=timeout)
        dur = time.time() - start
        print(f"[LLM_CLIENT] complete provider={provider} model={model} success={res is not None} duration={dur:.2f}s")
        return res
    except Exception as e:
        dur = time.time() - start
        print(f"[LLM_CLIENT] error provider={provider} model={model} error={e} duration={dur:.2f}s")
        raise


async def query_models_parallel(models: List[str], messages: List[Dict[str, str]], provider: Optional[str] = None) -> Dict[str, Optional[Dict[str, Any]]]:
    if _should_use_ollama(provider):
        return await ollama.query_models_parallel(models, messages)
    return await openrouter.query_models_parallel(models, messages)
