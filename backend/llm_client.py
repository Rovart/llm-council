"""Unified LLM client that can route requests to OpenRouter, Ollama, or Custom API.

This module provides `query_model` and `query_models_parallel` with the
same interface as the previous `openrouter` module, but allows selecting
the provider per-call using the `provider` argument ('openrouter', 'ollama', 'custom', or 'hybrid').

In hybrid mode, the provider is determined by checking which provider's model list contains the model.
Fallback heuristics:
- Models with '/' in the name (e.g., 'openai/gpt-4o') -> OpenRouter
- Models without '/' (e.g., 'llama3.2') -> Ollama (local)
"""

from typing import List, Dict, Any, Optional

from . import openrouter
from . import ollama
from . import config_store
from .config import USE_OLLAMA
import time

# Cache for model -> provider mapping (refreshed on each call in hybrid mode)
_model_provider_cache: Dict[str, str] = {}


def _is_openrouter_model(model: str) -> bool:
    """Check if a model name looks like an OpenRouter model (has provider/model format)."""
    return '/' in model and not model.startswith('/')


async def _get_custom_api_models() -> List[str]:
    """Get models from the custom API if configured."""
    try:
        custom_url = config_store.get_custom_api_url()
        custom_key = config_store.get_custom_api_key()
        if custom_url:
            return await openrouter.list_models_from_url(custom_url, custom_key)
    except Exception as e:
        print(f"Error fetching custom API models: {e}")
    return []


def _resolve_provider_for_model(model: str, provider: Optional[str], custom_models: List[str] = None) -> str:
    """Determine which provider to use for a specific model.
    
    Args:
        model: Model name
        provider: Provider hint ('ollama', 'openrouter', 'custom', 'hybrid', or None)
        custom_models: List of models from custom API (for hybrid detection)
    
    Returns:
        'ollama', 'openrouter', or 'custom'
    """
    # Explicit provider (not hybrid)
    if provider and provider.lower() in ('ollama', 'local'):
        return 'ollama'
    if provider and provider.lower() == 'openrouter':
        return 'openrouter'
    if provider and provider.lower() == 'custom':
        return 'custom'
    
    # Check if model is in custom API models
    if custom_models and model in custom_models:
        return 'custom'
    
    # Hybrid mode or None - determine by model name pattern
    if _is_openrouter_model(model):
        return 'openrouter'
    else:
        return 'ollama'


def _should_use_ollama(provider: Optional[str], model: str = None) -> bool:
    """Legacy compatibility - determine if Ollama should be used."""
    if model:
        return _resolve_provider_for_model(model, provider) == 'ollama'
    if provider is None:
        return USE_OLLAMA
    return str(provider).lower() in ("ollama", "local")


async def _query_custom_api_model(model: str, messages: List[Dict[str, str]], timeout: float = 120.0):
    """Query a model via the custom API."""
    custom_url = config_store.get_custom_api_url()
    custom_key = config_store.get_custom_api_key()
    if not custom_url:
        raise ValueError("Custom API URL not configured")
    return await openrouter.query_custom_api(model, messages, custom_url, custom_key, timeout)


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
    provider: Optional[str] = None,
    stream: bool = False,
    custom_models: List[str] = None,
):
    """Query a model with optional streaming support.
    
    Args:
        model: Model name
        messages: List of message dicts
        timeout: Request timeout
        provider: Provider to use ('ollama', 'openrouter', 'custom', 'hybrid', or None for auto)
        stream: If True, returns an async generator yielding chunks. If False, returns complete response dict.
        custom_models: List of models from custom API (for hybrid detection)
    
    Returns:
        If stream=False: Dict with response data, or None
        If stream=True: Async generator yielding chunk dicts
    """
    # Resolve provider based on model name for hybrid mode
    resolved_provider = _resolve_provider_for_model(model, provider, custom_models)
    start = time.time()
    
    if stream:
        return _query_model_stream_generator(model, messages, timeout, provider, resolved_provider, start, custom_models)
    
    # Non-streaming mode (original implementation)
    print(f"[LLM_CLIENT] start provider={provider} resolved={resolved_provider} model={model}")
    try:
        if resolved_provider == 'ollama':
            res = await ollama.query_model(model, messages, timeout=timeout, stream=False)
        elif resolved_provider == 'custom':
            res = await _query_custom_api_model(model, messages, timeout=timeout)
        else:
            res = await openrouter.query_model(model, messages, timeout=timeout)
        dur = time.time() - start
        print(f"[LLM_CLIENT] complete provider={provider} model={model} success={res is not None} duration={dur:.2f}s")
        return res
    except Exception as e:
        dur = time.time() - start
        print(f"[LLM_CLIENT] error provider={provider} model={model} error={e} duration={dur:.2f}s")
        raise


async def _query_model_stream_generator(model, messages, timeout, provider, resolved_provider, start, custom_models=None):
    """Helper generator for streaming response."""
    # Streaming mode
    print(f"[LLM_CLIENT][STREAM] start provider={provider} resolved={resolved_provider} model={model}")
    try:
        if resolved_provider == 'ollama':
            # ollama.query_model(stream=True) returns a generator, so we await it to get the generator
            generator = await ollama.query_model(model, messages, timeout=timeout, stream=True)
            async for chunk in generator:
                yield chunk
        elif resolved_provider == 'custom':
            # For custom API, fall back to non-streaming
            print(f"[LLM_CLIENT][STREAM] custom API doesn't support streaming, using non-streaming fallback")
            res = await _query_custom_api_model(model, messages, timeout=timeout)
            if res:
                yield {'type': 'chunk', 'content': res.get('content', ''), 'done': True}
                yield {'type': 'done'}
            else:
                yield {'type': 'error', 'message': 'Failed to get response'}
        else:
            # For OpenRouter, fall back to non-streaming
            print(f"[LLM_CLIENT][STREAM] openrouter doesn't support streaming, using non-streaming fallback")
            res = await openrouter.query_model(model, messages, timeout=timeout)
            if res:
                yield {'type': 'chunk', 'content': res.get('content', ''), 'done': True}
                yield {'type': 'done'}
            else:
                yield {'type': 'error', 'message': 'Failed to get response'}
        
        dur = time.time() - start
        print(f"[LLM_CLIENT][STREAM] complete provider={provider} model={model} duration={dur:.2f}s")
    except Exception as e:
        dur = time.time() - start
        print(f"[LLM_CLIENT][STREAM] error provider={provider} model={model} error={e} duration={dur:.2f}s")
        yield {'type': 'error', 'message': str(e)}



async def query_models_parallel(models: List[str], messages: List[Dict[str, str]], provider: Optional[str] = None) -> Dict[str, Optional[Dict[str, Any]]]:
    if _should_use_ollama(provider):
        return await ollama.query_models_parallel(models, messages)
    return await openrouter.query_models_parallel(models, messages)


async def query_models_parallel_stream(models: List[str], messages: List[Dict[str, str]], provider: Optional[str] = None):
    """Stream responses from multiple models in parallel.
    
    Yields tuples of (model_name, chunk_dict).
    """
    import asyncio
    queue = asyncio.Queue()
    
    async def worker(model_name):
        try:
            # Use the unified query_model with stream=True
            # Note: query_model is async, so we await it to get the generator
            generator = await query_model(model_name, messages, provider=provider, stream=True)
            # Announce that this model's worker has started so callers/UI can show a started state
            await queue.put((model_name, {'type': 'start'}))
            async for chunk in generator:
                await queue.put((model_name, chunk))
        except Exception as e:
            await queue.put((model_name, {'type': 'error', 'message': str(e)}))
        finally:
            await queue.put((model_name, {'type': 'complete'}))

    # Start workers
    tasks = [asyncio.create_task(worker(m)) for m in models]
    active_workers = len(models)
    
    while active_workers > 0:
        item = await queue.get()
        model_name, chunk = item
        if chunk.get('type') == 'complete':
            active_workers -= 1
        else:
            yield model_name, chunk
    
    # Ensure all tasks are done (they should be)
    await asyncio.gather(*tasks)
