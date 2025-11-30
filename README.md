# LLM Council - Ollama ready

![llmcouncil](header.jpg)

The idea of this repo is that instead of asking a question to your favorite LLM provider (e.g. OpenAI GPT 5.1, Google Gemini 3.0 Pro, Anthropic Claude Sonnet 4.5, xAI Grok 4, eg.c), you can group them into your "LLM Council". This repo is a simple, local web app that essentially looks like ChatGPT except it uses OpenRouter to send your query to multiple LLMs, it then asks them to review and rank each other's work, and finally a Chairman LLM produces the final response.

In a bit more detail, here is what happens when you submit a query:

1. **Stage 1: First opinions**. The user query is given to all LLMs individually, and the responses are collected. The individual responses are shown in a "tab view", so that the user can inspect them all one by one.
2. **Stage 2: Review**. Each individual LLM is given the responses of the other LLMs. Under the hood, the LLM identities are anonymized so that the LLM can't play favorites when judging their outputs. The LLM is asked to rank them in accuracy and insight.
3. **Stage 3: Final response**. The designated Chairman of the LLM Council takes all of the model's responses and compiles them into a single final answer that is presented to the user.

## New Features

- **Ollama Provider (local)**: You can now use Ollama as a local, free provider alongside the existing OpenRouter option. When `ollama` is selected from the Sidebar provider switch the app will:
    - Detect installed models via the Ollama HTTP API or CLI.
    - Show a list of installed models and let you add/remove them from the Council.
    - Offer recommended model families and specific candidate versions (tags) that you can select and install via the UI.
    - Stream install/uninstall logs in the modal so you can watch progress and diagnose failures.

- **Model selection & versions**: Recommended model entries expose a family (general name) and specific candidate versions. The UI shows the family name in the model chip while the select lists only the specific candidate versions (no duplicate family name in the dropdown). You can install a different version of an already-installed family.

- **Conversation context summarization**: To improve multi-turn continuity, the backend now includes a prior-context summarization flow:
    - The most recent assistant final answers (up to a 10-message window) are preserved and sent as context.
    - Older assistant final answers are summarized (synchronously or in the background, depending on size and threshold) and appended as a compact assistant message so the LLMs still get the gist of earlier conversation without exceeding token limits.
    - Summaries include metadata so the UI can surface them differently (e.g., a short label indicating the content is a summary).

These changes aim to make local experimentation with models (via Ollama) easier and to keep longer conversations coherent while staying within model context limits.

## Redesign (UI polish)

- The frontend has received a visual refresh focused on clarity and modern, subtle affordances:
    - Pill-style model chips for recommended models that present the family name consistently.
    - Matching, aligned selects for candidate versions so chips and dropdowns share height and baseline.
    - Modernized control buttons (gear, refresh, modal-close) with light backgrounds, subtle shadows and hover elevations.
    - Destructive actions (Uninstall) use a clear red button style to avoid accidental clicks.
    - The conversation UI now always shows the input box and surfaces summary messages with metadata so users can distinguish compacted prior context.

These visual changes focus on usability and reducing visual clutter while keeping interactions obvious and accessible.

## Vibe Code Alert

This project was 99% vibe coded as a fun Saturday hack because I wanted to explore and evaluate a number of LLMs side by side in the process of [reading books together with LLMs](https://x.com/karpathy/status/1990577951671509438). It's nice and useful to see multiple responses side by side, and also the cross-opinions of all LLMs on each other's outputs. I'm not going to support it in any way, it's provided here as is for other people's inspiration and I don't intend to improve it. Code is ephemeral now and libraries are over, ask your LLM to change it in whatever way you like.

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

You can now use either OpenRouter (cloud, paid) or Ollama (local, free) as the LLM provider.

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
