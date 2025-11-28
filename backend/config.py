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
RECOMMENDED_OLLAMA_MODELS = [
    "llama-2-13b",
    "llama-2-7b",
    "mistral-7b",
    "gpt4all",
]
