"""OpenRouter API client for making LLM requests."""

import httpx
from typing import List, Dict, Any, Optional


def _get_api_config():
    """Get OpenRouter API configuration dynamically."""
    from . import config_store
    api_key = config_store.get_openrouter_api_key()
    api_url = config_store.get_openrouter_api_url()
    return api_key, api_url


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0
) -> Optional[Dict[str, Any]]:
    """
    Query a single model via OpenRouter API.

    Args:
        model: OpenRouter model identifier (e.g., "openai/gpt-4o")
        messages: List of message dicts with 'role' and 'content'
        timeout: Request timeout in seconds

    Returns:
        Response dict with 'content' and optional 'reasoning_details', or None if failed
    """
    api_key, api_url = _get_api_config()
    
    if not api_key:
        print("Error: OpenRouter API key not configured")
        return None
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                api_url,
                headers=headers,
                json=payload
            )
            response.raise_for_status()

            data = response.json()
            message = data['choices'][0]['message']

            return {
                'content': message.get('content'),
                'reasoning_details': message.get('reasoning_details')
            }

    except Exception as e:
        print(f"Error querying model {model}: {e}")
        return None


async def list_models(timeout: float = 30.0) -> List[str]:
    """
    Fetch available models from OpenRouter API.
    
    Returns:
        List of model identifiers (e.g., ["openai/gpt-4o", "anthropic/claude-3-opus"])
    """
    api_key, api_url = _get_api_config()
    
    if not api_key:
        print("Error: OpenRouter API key not configured")
        return []
    
    # OpenRouter models endpoint
    models_url = api_url.replace('/chat/completions', '/models')
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(models_url, headers=headers)
            response.raise_for_status()
            data = response.json()
            # OpenRouter returns { "data": [ { "id": "model-id", ... }, ... ] }
            models = [m.get('id') for m in data.get('data', []) if m.get('id')]
            return sorted(models)
    except Exception as e:
        print(f"Error fetching OpenRouter models: {e}")
        return []


async def validate_api_key(api_key: str, api_url: str = None, timeout: float = 10.0) -> Dict[str, Any]:
    """
    Validate an OpenRouter API key by making a test request.
    
    Returns:
        Dict with 'valid' (bool) and 'message' (str)
    """
    if not api_key:
        return {'valid': False, 'message': 'API key is empty'}
    
    if not api_url:
        api_url = 'https://openrouter.ai/api/v1/chat/completions'
    
    models_url = api_url.replace('/chat/completions', '/models')
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(models_url, headers=headers)
            if response.status_code == 401:
                return {'valid': False, 'message': 'Invalid API key (401 Unauthorized)'}
            if response.status_code == 403:
                return {'valid': False, 'message': 'API key forbidden (403)'}
            response.raise_for_status()
            data = response.json()
            model_count = len(data.get('data', []))
            return {'valid': True, 'message': f'API key valid. {model_count} models available.'}
    except httpx.TimeoutException:
        return {'valid': False, 'message': 'Connection timed out'}
    except httpx.ConnectError as e:
        return {'valid': False, 'message': f'Connection error: {e}'}
    except Exception as e:
        return {'valid': False, 'message': f'Error: {e}'}


async def list_models_from_url(api_url: str, api_key: str = None, timeout: float = 30.0) -> List[str]:
    """
    Fetch available models from a custom OpenAI-compatible API endpoint.
    
    Args:
        api_url: Base URL of the API (e.g., "http://localhost:8080/v1/chat/completions")
        api_key: Optional API key (may be empty for local APIs)
        timeout: Request timeout
    
    Returns:
        List of model identifiers
    """
    # Convert chat/completions URL to models URL
    models_url = api_url
    if '/chat/completions' in api_url:
        models_url = api_url.replace('/chat/completions', '/models')
    elif api_url.endswith('/v1'):
        models_url = api_url + '/models'
    elif not api_url.endswith('/models'):
        models_url = api_url.rstrip('/') + '/v1/models'
    
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(models_url, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            # Handle different API response formats
            models = []
            if isinstance(data, dict):
                # OpenAI format: { "data": [ { "id": "model-id" }, ... ] }
                model_list = data.get('data', []) or data.get('models', [])
                for m in model_list:
                    if isinstance(m, str):
                        models.append(m)
                    elif isinstance(m, dict):
                        model_id = m.get('id') or m.get('name') or m.get('model')
                        if model_id:
                            models.append(model_id)
            elif isinstance(data, list):
                for m in data:
                    if isinstance(m, str):
                        models.append(m)
                    elif isinstance(m, dict):
                        model_id = m.get('id') or m.get('name') or m.get('model')
                        if model_id:
                            models.append(model_id)
            
            return sorted(models)
    except Exception as e:
        print(f"Error fetching models from {models_url}: {e}")
        return []


async def query_custom_api(
    model: str,
    messages: List[Dict[str, str]],
    api_url: str,
    api_key: str = None,
    timeout: float = 120.0
) -> Optional[Dict[str, Any]]:
    """
    Query a custom OpenAI-compatible API.
    
    Args:
        model: Model identifier
        messages: List of message dicts with 'role' and 'content'
        api_url: Full URL to the chat completions endpoint
        api_key: Optional API key
        timeout: Request timeout in seconds
    
    Returns:
        Response dict with 'content' and optional 'reasoning_details', or None if failed
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    payload = {
        "model": model,
        "messages": messages,
    }
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                api_url,
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            
            data = response.json()
            message = data['choices'][0]['message']
            
            return {
                'content': message.get('content'),
                'reasoning_details': message.get('reasoning_details')
            }
    
    except Exception as e:
        print(f"Error querying custom API {model}: {e}")
        return None


async def query_models_parallel(
    models: List[str],
    messages: List[Dict[str, str]]
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Query multiple models in parallel.

    Args:
        models: List of OpenRouter model identifiers
        messages: List of message dicts to send to each model

    Returns:
        Dict mapping model identifier to response dict (or None if failed)
    """
    import asyncio

    # Create tasks for all models
    tasks = [query_model(model, messages) for model in models]

    # Wait for all to complete
    responses = await asyncio.gather(*tasks)

    # Map models to their responses
    return {model: response for model, response in zip(models, responses)}
