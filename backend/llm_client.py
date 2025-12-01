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
    stream: bool = False,
):
    """Query a model with optional streaming support.
    
    Args:
        model: Model name
        messages: List of message dicts
        timeout: Request timeout
        provider: Provider to use ('ollama', 'openrouter', or None for auto)
        stream: If True, returns an async generator yielding chunks. If False, returns complete response dict.
    
    Returns:
        If stream=False: Dict with response data, or None
        If stream=True: Async generator yielding chunk dicts
    """
    resolved_ollama = _should_use_ollama(provider)
    start = time.time()
    
    if stream:
        return _query_model_stream_generator(model, messages, timeout, provider, resolved_ollama, start)
    
    # Non-streaming mode (original implementation)
    print(f"[LLM_CLIENT] start provider={provider} resolved_ollama={resolved_ollama} model={model}")
    try:
        if resolved_ollama:
            res = await ollama.query_model(model, messages, timeout=timeout, stream=False)
        else:
            res = await openrouter.query_model(model, messages, timeout=timeout)
        dur = time.time() - start
        print(f"[LLM_CLIENT] complete provider={provider} model={model} success={res is not None} duration={dur:.2f}s")
        return res
    except Exception as e:
        dur = time.time() - start
        print(f"[LLM_CLIENT] error provider={provider} model={model} error={e} duration={dur:.2f}s")
        raise


async def _query_model_stream_generator(model, messages, timeout, provider, resolved_ollama, start):
    """Helper generator for streaming response."""
    # Streaming mode
    print(f"[LLM_CLIENT][STREAM] start provider={provider} resolved_ollama={resolved_ollama} model={model}")
    try:
        if resolved_ollama:
            # ollama.query_model(stream=True) returns a generator, so we await it to get the generator
            generator = await ollama.query_model(model, messages, timeout=timeout, stream=True)
            async for chunk in generator:
                yield chunk
        else:
            # For non-Ollama providers, fall back to non-streaming
            print(f"[LLM_CLIENT][STREAM] provider={provider} doesn't support streaming, using non-streaming fallback")
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
