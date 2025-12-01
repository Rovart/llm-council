"""Configuration for the LLM Council."""

import os
from dotenv import load_dotenv

load_dotenv()

# OpenRouter API key
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Council members - list of OpenRouter model identifiers
COUNCIL_MODELS = [
    "openai/gpt-5.1",
    "google/gemini-3-pro-preview",
    "anthropic/claude-sonnet-4.5",
    "x-ai/grok-4",
]

# Chairman model - synthesizes final response
CHAIRMAN_MODEL = "google/gemini-3-pro-preview"

# OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Data directory for conversation storage
DATA_DIR = "data/conversations"

# Optional Ollama (local) settings
# Set USE_OLLAMA=true in .env to enable local Ollama provider by default
USE_OLLAMA = os.getenv("USE_OLLAMA", "false").lower() in ("1", "true", "yes")
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://localhost:11434")
# If you prefer to call the Ollama CLI instead of the local HTTP API set this to true
OLLAMA_USE_CLI = os.getenv("OLLAMA_USE_CLI", "false").lower() in ("1", "true", "yes")
OLLAMA_CLI_PATH = os.getenv("OLLAMA_CLI_PATH", "ollama")

# Recommended local Ollama models (used in UI to suggest installs)
# Provide a mapping of popular families to suggested size variants so the
# UI/backend can pick a variant appropriate for the developer machine.
RECOMMENDED_OLLAMA_MODELS_MAP = {
    "llama-2": ["llama-2-7b", "llama-2-13b", "llama-2-70b"],
    "mistral": ["mistral-7b"],
    "gpt4all": ["gpt4all-13b"],
    # newly requested recommendations
    "gpt-oss": ["gpt-oss-3b", "gpt-oss-7b", "gpt-oss-13b"],
    "deepseek-r1": ["deepseek-r1-1.5b", "deepseek-r1-7b", "deepseek-r1-14b"],
    "qwen3": ["qwen3-7b", "qwen3-14b", "qwen3-34b"],
}

# A flat, stable list of recommended base names for backward compatibility
RECOMMENDED_OLLAMA_MODELS = list(RECOMMENDED_OLLAMA_MODELS_MAP.keys())

# Context summarization settings
# How many recent assistant final answers to include directly in the prompt
IMMEDIATE_CONTEXT_KEEP = 3
# How many assistant final answers to retain before background summarization
SUMMARY_RETENTION = 3
