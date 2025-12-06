"""Persistent configuration store for council settings.

This simple JSON store persists selected provider (openrouter/ollama), the
council model list, and chairman model into `data/config.json` under the
`DATA_DIR` directory.
"""

import json
import os
from typing import Dict, Any, List
from pathlib import Path

from .config import DATA_DIR, COUNCIL_MODELS, CHAIRMAN_MODEL, USE_OLLAMA, OPENROUTER_API_KEY, OPENROUTER_API_URL

CONFIG_PATH = os.path.join(DATA_DIR, 'config.json')


def ensure_data_dir():
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)


def _default_config() -> Dict[str, Any]:
    return {
        'provider': 'ollama' if USE_OLLAMA else 'openrouter',
        'council_models': COUNCIL_MODELS,
        'chairman_model': CHAIRMAN_MODEL,
        # OpenRouter configuration
        'openrouter_api_key': OPENROUTER_API_KEY or '',
        'openrouter_api_url': OPENROUTER_API_URL or 'https://openrouter.ai/api/v1/chat/completions',
    }


def get_config() -> Dict[str, Any]:
    ensure_data_dir()
    if not os.path.exists(CONFIG_PATH):
        conf = _default_config()
        save_config(conf)
        return conf

    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


def save_config(conf: Dict[str, Any]):
    ensure_data_dir()
    with open(CONFIG_PATH, 'w') as f:
        json.dump(conf, f, indent=2)


def get_council_models() -> List[str]:
    return get_config().get('council_models', COUNCIL_MODELS)


def set_council_models(models: List[str]):
    conf = get_config()
    conf['council_models'] = models
    save_config(conf)


def get_chairman_model() -> str:
    return get_config().get('chairman_model', CHAIRMAN_MODEL)


def set_chairman_model(model: str):
    conf = get_config()
    conf['chairman_model'] = model
    save_config(conf)


def get_provider() -> str:
    return get_config().get('provider', 'ollama' if USE_OLLAMA else 'openrouter')


def set_provider(provider: str):
    conf = get_config()
    conf['provider'] = provider
    save_config(conf)


def get_openrouter_api_key() -> str:
    """Get OpenRouter API key from config, falling back to env var."""
    conf = get_config()
    key = conf.get('openrouter_api_key', '')
    if not key:
        key = OPENROUTER_API_KEY or ''
    return key


def set_openrouter_api_key(key: str):
    conf = get_config()
    conf['openrouter_api_key'] = key
    save_config(conf)


def get_openrouter_api_url() -> str:
    """Get OpenRouter API URL from config, falling back to env var."""
    conf = get_config()
    url = conf.get('openrouter_api_url', '')
    if not url:
        url = OPENROUTER_API_URL or 'https://openrouter.ai/api/v1/chat/completions'
    return url


def set_openrouter_api_url(url: str):
    conf = get_config()
    conf['openrouter_api_url'] = url
    save_config(conf)


# Custom API configuration
def get_custom_api_url() -> str:
    """Get Custom API URL from config."""
    conf = get_config()
    return conf.get('custom_api_url', '')


def set_custom_api_url(url: str):
    conf = get_config()
    conf['custom_api_url'] = url
    save_config(conf)


def get_custom_api_key() -> str:
    """Get Custom API key from config (may be empty)."""
    conf = get_config()
    return conf.get('custom_api_key', '')


def set_custom_api_key(key: str):
    conf = get_config()
    conf['custom_api_key'] = key
    save_config(conf)
