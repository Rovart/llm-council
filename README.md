# LLM Council

![llmcouncil](header.jpg)

The idea of this repo is that instead of asking a question to your favorite LLM provider (e.g. OpenAI GPT 5.1, Google Gemini 3.0 Pro, Anthropic Claude Sonnet 4.5, xAI Grok 4, etc.), you can group them into your "LLM Council". This repo is a simple, local web app that essentially looks like ChatGPT except it uses OpenRouter (or Ollama locally) to send your query to multiple LLMs, it then asks them to review and rank each other's work, and finally a Chairman LLM produces the final response.

In a bit more detail, here is what happens when you submit a query:

1. **Stage 1: First opinions**. The user query is given to all LLMs individually, and the responses are collected. The individual responses are shown in a "tab view", so that the user can inspect them all one by one.
2. **Stage 2: Review**. Each individual LLM is given the responses of the other LLMs. Under the hood, the LLM identities are anonymized so that the LLM can't play favorites when judging their outputs. The LLM is asked to rank them in accuracy and insight.
3. **Stage 3: Final response**. The designated Chairman of the LLM Council takes all of the model's responses and compiles them into a single final answer that is presented to the user.

## Features

### Dual Provider Support
- **OpenRouter (cloud, paid)**: Access to many frontier models (GPT-4, Claude, Gemini, Grok, etc.) via the OpenRouter API.
- **Ollama (local, free)**: Run models locally with zero cost. Seamlessly switch between providers from the sidebar.

### Ollama Integration
- Auto-detect installed Ollama models via HTTP API or CLI.
- Browse recommended model families with specific version tags.
- Install/uninstall models directly from the UI with real-time streaming logs.
- Add/remove models from the Council and select the Chairman model.

### Streaming Responses
- Real-time SSE streaming for all three stages (first opinions, reviews, final response).
- Progressive UI updates as each model responds.
- Per-conversation loading state — switch conversations while streaming continues in the background.
- Sidebar spinner indicator for conversations with active streams.

### Conversation Management
- Create, delete, and switch between multiple conversations.
- Automatic title generation based on conversation content.
- Persistent storage in JSON files.

### Context Summarization
- Preserves recent assistant responses (up to 10-message window) as full context.
- Automatically summarizes older conversation history to stay within token limits.
- Summary metadata displayed in UI so users can distinguish compacted prior context.

### Error Handling & Recovery
- Messages interrupted by page reload are automatically marked as failed.
- Retry failed messages with a single click (retry button).
- Edit and resubmit failed messages (edit button).
- Old failed/pending messages are auto-cleaned on new submissions.

### Skip to Chairman Mode
- Toggle to bypass Stage 1 & 2 and send queries directly to the Chairman model.
- Useful for quick responses without the full council deliberation.

### Modern UI
- Clean, responsive design with a parliament/council aesthetic.
- Collapsible stage views for first opinions and reviews.
- Markdown rendering for all responses.
- Pill-style model chips and aligned dropdowns.
- Hover effects, subtle shadows, and clear destructive action styling.

## Setup

### 1. Install Dependencies

The project uses [uv](https://docs.astral.sh/uv/) for project management.

**Backend:**
```bash
uv sync
```

**Frontend:**
```bash
cd frontend
npm install
cd ..
```

### 2. Configure API Key

Create a `.env` file in the project root:

```bash
OPENROUTER_API_KEY=sk-or-v1-...
```

Get your API key at [openrouter.ai](https://openrouter.ai/). Make sure to purchase the credits you need, or sign up for automatic top up.

### 3. Configure Models (Optional)

Edit `backend/config.py` to customize the council:

```python
COUNCIL_MODELS = [
    "openai/gpt-5.1",
    "google/gemini-3-pro-preview",
    "anthropic/claude-sonnet-4.5",
    "x-ai/grok-4",
]

CHAIRMAN_MODEL = "google/gemini-3-pro-preview"
```

## Running the Application

**Option 1: Use the start script**
```bash
./start.sh
```

**Option 2: Run manually**

Terminal 1 (Backend):
```bash
uv run python -m backend.main
```

Terminal 2 (Frontend):
```bash
cd frontend
npm run dev
```

Then open http://localhost:5173 in your browser.

## Provider: OpenRouter vs Ollama (Local)

You can use either OpenRouter (cloud, paid) or Ollama (local, free) as the LLM provider.

- To use Ollama by default, update `.env` and set `USE_OLLAMA=true` and configure `OLLAMA_API_URL` and `OLLAMA_USE_CLI` if you want automatic CLI installs.
- Start the app as usual with `./start.sh` or `uv run python -m backend.main` and `npm run dev` for the frontend.

In the UI (Sidebar) you can switch between providers. When Ollama is selected, a new section appears allowing you to:

- See detected **installed** Ollama models (via the local Ollama API or CLI).
- See **recommended** models that are not installed and an `Install` action to pull them (if `OLLAMA_USE_CLI` is configured on the backend).
- Add/remove models to the Council and pick the **Chairman** model (persisted in `data/conversations/config.json`).

Note: If you switch to `ollama` provider and your existing council contains OpenRouter model IDs, update the council to local Ollama model names to ensure the calls succeed.

## Tech Stack

- **Backend:** FastAPI (Python 3.10+), async httpx, OpenRouter API
- **Frontend:** React + Vite, react-markdown for rendering
- **Storage:** JSON files in `data/conversations/`
- **Package Management:** uv for Python, npm for JavaScript
