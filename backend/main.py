"""FastAPI backend for LLM Council."""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any
from datetime import datetime
import uuid
import json
import asyncio

from . import storage
from .council import run_full_council, generate_conversation_title, stage1_collect_responses, stage2_collect_rankings, stage3_synthesize_final, calculate_aggregate_rankings
from . import ollama
from . import config_store
from .config import IMMEDIATE_CONTEXT_KEEP, SUMMARY_RETENTION

app = FastAPI(title="LLM Council API")


async def _background_summarize_and_persist(conversation_id: str, num_to_summarize: int, chair: str | None, provider: str | None):
    """Background task: summarize the oldest `num_to_summarize` assistant final answers and persist summary.

    This function is intentionally best-effort; failures are logged but do not raise.
    """
    try:
        from .llm_client import query_model as llm_query
        convo = storage.get_conversation(conversation_id)
        if not convo or not convo.get('messages'):
            return

        # Collect the assistant final answers in order
        finals = [m.get('stage3', {}).get('response') for m in convo.get('messages', []) if m.get('role') == 'assistant' and isinstance(m.get('stage3'), dict) and m.get('stage3', {}).get('response')]
        if not finals or num_to_summarize <= 0 or num_to_summarize > len(finals):
            return

        to_summarize = finals[:num_to_summarize]
        # Build prompt
        summary_prompt = 'Summarize the following previous final answers into a concise paragraph (one paragraph, keep it short):\n\n'
        for i, p in enumerate(to_summarize, start=1):
            summary_prompt += f"Answer {i}: {p}\n\n"

        if not chair:
            return

        resp = await llm_query(chair, [{"role": "user", "content": summary_prompt}], provider=provider)
        if resp is None:
            return
        summary_text = resp.get('content', '').strip()
        if not summary_text:
            return

        # Persist: append a summary assistant message but do NOT remove original messages
        convo = storage.get_conversation(conversation_id)
        if not convo or not convo.get('messages'):
            return
        summary_msg = {
            'role': 'assistant',
            'stage1': [],
            'stage2': [],
            'stage3': {
                'model': chair,
                'response': summary_text,
                'metadata': {
                    'summarized_count': num_to_summarize,
                    'chairman_model': chair,
                    'summary_generated_at': datetime.utcnow().isoformat()
                }
            }
        }
        convo['messages'].append(summary_msg)
        storage.save_conversation(convo)
    except Exception as e:
        print(f"[BACKGROUND_SUMMARY] failed: {e}")

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""

@app.get('/api/ollama/status')
async def ollama_status():
    """Return a small diagnostic about Ollama connectivity and the detected API URL."""
    try:
        url = ollama.get_detected_api_url()
    except Exception:
        url = None
    # Check reachability — try multiple endpoints
    reachable = False
    resolved_endpoint = None
    if url:
        try:
            import httpx
            candidates = [
                url.rstrip('/'),
                url.rstrip('/') + '/api/models',
                url.rstrip('/') + '/models',
                url.rstrip('/') + '/v1/models',
            ]
            for c in candidates:
                try:
                    r = httpx.get(c, timeout=2.0)
                    if r.status_code == 200:
                        reachable = True
                        resolved_endpoint = c
                        break
                except Exception:
                    continue
        except Exception:
            reachable = False

    return {
        'detected_url': url,
        'resolved_endpoint': resolved_endpoint,
        'reachable': reachable,
        'use_cli': ollama.OLLAMA_USE_CLI,
    }
    pass


class SendMessageRequest(BaseModel):
    """Request to send a message in a conversation."""
    content: str
    # Optional provider: 'openrouter' (default) or 'ollama'
    provider: str | None = None
    # Optional flag to skip stages 1 and 2 and chat directly with the Chairman
    skip_stages: bool = False
    # Optional: the response text we are replying to (gets priority in context)
    reply_to_response: str | None = None


class ConversationMetadata(BaseModel):
    """Conversation metadata for list view."""
    id: str
    created_at: str
    title: str
    message_count: int


class Conversation(BaseModel):
    """Full conversation with all messages."""
    id: str
    created_at: str
    title: str
    messages: List[Dict[str, Any]]


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "LLM Council API"}


@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations():
    """List all conversations (metadata only)."""
    try:
        return storage.list_conversations()
    except Exception as e:
        # Defensive: avoid 500 if storage has malformed files; return empty list
        print(f"Error listing conversations: {e}")
        return []


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(request: CreateConversationRequest):
    """Create a new conversation."""
    conversation_id = str(uuid.uuid4())
    conversation = storage.create_conversation(conversation_id)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str):
    """Get a specific conversation with all its messages."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """Delete a conversation."""
    storage.delete_conversation(conversation_id)
    return {"status": "ok"}


@app.post('/api/conversations/{conversation_id}/pending/remove')
async def remove_pending_messages(conversation_id: str, body: Dict[str, Any]):
    """Remove pending user messages for a conversation.

    POST body: {"keep_last": true} (default true)
    Returns: {"removed": <count>}
    """
    keep_last = True
    if isinstance(body, dict) and 'keep_last' in body:
        try:
            keep_last = bool(body.get('keep_last'))
        except Exception:
            keep_last = True

    try:
        removed = storage.remove_pending_user_messages(conversation_id, keep_last=keep_last)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"removed": removed}


@app.post('/api/conversations/{conversation_id}/user-message/status')
async def mark_user_message_status(conversation_id: str, body: Dict[str, Any]):
    """Mark the last user message with a status (e.g., 'failed', 'complete').

    POST body: {"status": "failed"}
    Returns: {"success": true}
    """
    status = body.get('status') if isinstance(body, dict) else None
    if not status:
        raise HTTPException(status_code=400, detail="status is required")

    try:
        success = storage.mark_last_user_message_status(conversation_id, status)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": success}


@app.post('/api/conversations/{conversation_id}/pending/retry')
async def retry_last_pending(conversation_id: str, body: Dict[str, Any]):
    """Retry the last pending or failed user message by re-running the council.

    POST body may include: {"provider": "ollama"}
    Returns the same response shape as `send_message`.
    """
    provider = None
    if isinstance(body, dict):
        provider = body.get('provider')

    # Ensure conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get last user message
    last_user = storage.get_last_user_message(conversation_id)
    if not last_user:
        raise HTTPException(status_code=404, detail="No user message found to retry")

    status = last_user.get('status')
    if status not in ('pending', 'failed'):
        raise HTTPException(status_code=400, detail=f"Last user message status is '{status}', cannot retry")

    content = last_user.get('content')
    if not content:
        raise HTTPException(status_code=400, detail="Last user message has no content")

    # Compute prior assistant final answers (chronological). We'll use up to
    # `IMMEDIATE_CONTEXT_KEEP` previous messages for immediate context.
    prior_list = []
    try:
        convo = storage.get_conversation(conversation_id)
        if convo and convo.get('messages'):
            for m in convo.get('messages', []):
                if m.get('role') == 'assistant' and isinstance(m.get('stage3'), dict):
                    resp = m.get('stage3', {}).get('response')
                    if resp:
                        prior_list.append(resp)
    except Exception:
        prior_list = []

    # Build prior_context similar to send_message
    prior_context = None
    did_sync_summary = False
    if len(prior_list) == 0:
        prior_context = None
    elif len(prior_list) <= IMMEDIATE_CONTEXT_KEEP:
        prior_context = '\n\n'.join(prior_list[-IMMEDIATE_CONTEXT_KEEP:])
    else:
        to_summarize = prior_list[:-IMMEDIATE_CONTEXT_KEEP]
        remaining = prior_list[-IMMEDIATE_CONTEXT_KEEP:]
        summary_prompt = 'Summarize the following previous final answers into a concise paragraph (one paragraph, keep it short):\n\n'
        for i, p in enumerate(to_summarize, start=1):
            summary_prompt += f"Answer {i}: {p}\n\n"

        try:
            from .config_store import get_chairman_model
            from .config import CHAIRMAN_MODEL
            chair = get_chairman_model() or CHAIRMAN_MODEL
        except Exception:
            chair = None

        summary_text = None
        try:
            if chair:
                from .llm_client import query_model as llm_query
                resp = await llm_query(chair, [{"role": "user", "content": summary_prompt}], provider=provider)
                if resp is not None:
                    summary_text = resp.get('content', '').strip()
        except Exception:
            summary_text = None

        if summary_text:
            try:
                convo = storage.get_conversation(conversation_id)
                if convo and convo.get('messages'):
                    summary_msg = {'role': 'assistant', 'stage1': [], 'stage2': [], 'stage3': {'model': chair, 'response': summary_text, 'metadata': {'summarized_count': len(to_summarize), 'chairman_model': chair, 'summary_generated_at': datetime.utcnow().isoformat()}}}
                    convo['messages'].append(summary_msg)
                    storage.save_conversation(convo)
                    prior_context = summary_text + '\n\n' + '\n\n'.join(remaining)
                    did_sync_summary = True
                else:
                    prior_context = '\n\n'.join(remaining)
            except Exception:
                prior_context = '\n\n'.join(remaining)
        else:
            prior_context = '\n\n'.join(remaining)

    # Run the 3-stage council process with prior_context
    try:
        stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
            content,
            provider=provider,
            prior_context=prior_context,
        )

        # Add assistant message with all stages
        storage.add_assistant_message(
            conversation_id,
            stage1_results,
            stage2_results,
            stage3_result
        )

        # Mark the user's last message as complete
        try:
            storage.mark_last_user_message_status(conversation_id, 'complete')
        except Exception:
            pass

    except Exception as e:
        # Mark last user message as failed so UI can offer retry
        try:
            storage.mark_last_user_message_status(conversation_id, 'failed')
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

    # Return the complete response with metadata
    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "metadata": metadata
    }


@app.post('/api/conversations/{conversation_id}/pending/retry/stream')
async def retry_last_pending_stream(conversation_id: str, body: Dict[str, Any]):
    """Retry the last pending/failed user message and stream the 3-stage council SSE.

    POST body may include: {"provider": "ollama", "skip_stages": false}
    """
    provider = None
    skip_stages = False
    if isinstance(body, dict):
        provider = body.get('provider')
        skip_stages = bool(body.get('skip_stages', False))

    # Ensure conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get last user message
    last_user = storage.get_last_user_message(conversation_id)
    if not last_user:
        raise HTTPException(status_code=404, detail="No user message found to retry")

    status = last_user.get('status')
    if status not in ('pending', 'failed'):
        raise HTTPException(status_code=400, detail=f"Last user message status is '{status}', cannot retry")

    content = last_user.get('content')
    if not content:
        raise HTTPException(status_code=400, detail="Last user message has no content")

    async def event_generator():
        try:
            # Start title generation? Only if conversation has no title set or default
            is_first_message = len(conversation.get('messages', [])) == 0
            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(content, provider=provider))

            # Compute prior_list same as send_message_stream
            prior_list = []
            did_sync_summary = False
            try:
                convo = storage.get_conversation(conversation_id)
                if convo and convo.get('messages'):
                    for m in convo.get('messages', []):
                        if m.get('role') == 'assistant' and isinstance(m.get('stage3'), dict):
                            resp = m.get('stage3', {}).get('response')
                            if resp:
                                prior_list.append(resp)
            except Exception:
                prior_list = []

            prior_context = None
            if len(prior_list) == 0:
                prior_context = None
            elif len(prior_list) <= IMMEDIATE_CONTEXT_KEEP:
                prior_context = '\n\n'.join(prior_list[-IMMEDIATE_CONTEXT_KEEP:])
            else:
                to_summarize = prior_list[:-IMMEDIATE_CONTEXT_KEEP]
                remaining = prior_list[-IMMEDIATE_CONTEXT_KEEP:]
                summary_prompt = 'Summarize the following previous final answers into a concise paragraph (one paragraph, keep it short):\n\n'
                for i, p in enumerate(to_summarize, start=1):
                    summary_prompt += f"Answer {i}: {p}\n\n"
                try:
                    from .config_store import get_chairman_model
                    from .config import CHAIRMAN_MODEL
                    chair = get_chairman_model() or CHAIRMAN_MODEL
                except Exception:
                    chair = None
                summary_text = None
                try:
                    if chair:
                        from .llm_client import query_model as llm_query
                        resp = await llm_query(chair, [{"role": "user", "content": summary_prompt}], provider=provider)
                        if resp is not None:
                            summary_text = resp.get('content', '').strip()
                except Exception:
                    summary_text = None

                if summary_text:
                    try:
                        convo = storage.get_conversation(conversation_id)
                        if convo and convo.get('messages'):
                            summary_msg = {'role': 'assistant', 'stage1': [], 'stage2': [], 'stage3': {'model': chair, 'response': summary_text, 'metadata': {'summarized_count': len(to_summarize), 'chairman_model': chair, 'summary_generated_at': datetime.utcnow().isoformat()}}}
                            convo['messages'].append(summary_msg)
                            storage.save_conversation(convo)
                            prior_context = summary_text + '\n\n' + '\n\n'.join(remaining)
                            did_sync_summary = True
                        else:
                            prior_context = '\n\n'.join(remaining)
                    except Exception:
                        prior_context = '\n\n'.join(remaining)
                else:
                    prior_context = '\n\n'.join(remaining)

            # Prepare combined query
            if prior_context:
                combined_query = content + "\n\nFor context, here are previous responses:\n" + prior_context
            else:
                combined_query = content

            # If skip_stages -> direct chairman streaming
            if skip_stages:
                yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
                from .config_store import get_chairman_model
                from .config import CHAIRMAN_MODEL
                from .llm_client import query_model
                chairman = get_chairman_model() or CHAIRMAN_MODEL

                if provider and provider.lower() in ('ollama', 'local'):
                    from . import ollama
                    installed = await ollama.list_models()
                    if chairman not in installed and installed:
                        chairman = installed[0]

                messages = [{"role": "user", "content": combined_query}]
                stage3_result = None
                accumulated_response = ""
                generator = await query_model(chairman, messages, provider=provider, stream=True)
                async for chunk in generator:
                    if chunk.get('type') == 'chunk':
                        content_chunk = chunk.get('content', '')
                        accumulated_response += content_chunk
                        yield f"data: {json.dumps({'type': 'stage3_chunk', 'content': content_chunk, 'model': chairman})}\n\n"
                    elif chunk.get('type') == 'done':
                        stage3_result = {"model": chairman, "response": accumulated_response}
                    elif chunk.get('type') == 'error':
                        stage3_result = {"model": chairman, "response": f"Error: {chunk.get('message', 'Unable to generate response.')}"}
                        break

                if stage3_result is None:
                    if accumulated_response:
                        stage3_result = {"model": chairman, "response": accumulated_response}
                    else:
                        stage3_result = {"model": chairman, "response": "Error: No response generated from chairman."}

                yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"
            else:
                # Reuse the same 3-stage streaming flow as send_message_stream
                # Stage 1
                yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
                stage1_results = []
                generator = await stage1_collect_responses(content, provider=provider, prior_context=prior_context, stream=True)
                model_responses = {}
                async for model, chunk in generator:
                    if chunk.get('type') == 'start':
                        yield f"data: {json.dumps({'type': 'stage1_model_start', 'model': model})}\n\n"
                        continue
                    if chunk.get('type') == 'chunk':
                        content_chunk = chunk.get('content', '')
                        if model not in model_responses:
                            model_responses[model] = ""
                        model_responses[model] += content_chunk
                        yield f"data: {json.dumps({'type': 'stage1_chunk', 'model': model, 'content': content_chunk})}\n\n"
                for model, text in model_responses.items():
                    stage1_results.append({'model': model, 'response': text})
                yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"

                # Stage 2
                yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
                stage2_results = []
                label_to_model = {}
                generator = await stage2_collect_rankings(combined_query, stage1_results, provider=provider, stream=True)
                model_rankings = {}
                async for item in generator:
                    if isinstance(item, tuple) and item[0] == 'metadata':
                        label_to_model = item[1].get('label_to_model', {})
                        yield f"data: {json.dumps({'type': 'stage2_metadata', 'data': {'label_to_model': label_to_model}})}\n\n"

                        continue
                    model, chunk = item
                    if chunk.get('type') == 'start':
                        yield f"data: {json.dumps({'type': 'stage2_model_start', 'model': model})}\n\n"
                        continue
                    if chunk.get('type') == 'chunk':
                        content_chunk = chunk.get('content', '')
                        if model not in model_rankings:
                            model_rankings[model] = ""
                        model_rankings[model] += content_chunk
                        yield f"data: {json.dumps({'type': 'stage2_chunk', 'model': model, 'content': content_chunk})}\n\n"

                for model, text in model_rankings.items():
                    stage2_results.append({'model': model, 'ranking': text})
                aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
                yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

                # Stage 3
                yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
                generator = await stage3_synthesize_final(combined_query, stage1_results, stage2_results, provider=provider, stream=True)
                async for chunk in generator:
                    if chunk.get('type') == 'chunk':
                        yield f"data: {json.dumps({'type': 'stage3_chunk', 'content': chunk.get('content', ''), 'model': chunk.get('model')})}\n\n"

                    elif chunk.get('type') == 'done':
                        stage3_result = {'model': chunk.get('model'), 'response': chunk.get('response', '')}
                        yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"
                    elif chunk.get('type') == 'error':
                        stage3_result = {'model': chunk.get('model', 'unknown'), 'response': f"Error: {chunk.get('message', 'Unknown error') }"}
                        yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

            # Update title if generated
            if title_task:
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            # Save assistant message
            storage.add_assistant_message(
                conversation_id,
                stage1_results,
                stage2_results,
                stage3_result
            )

            # Mark last user message as complete
            try:
                storage.mark_last_user_message_status(conversation_id, 'complete')
            except Exception:
                pass

            # Schedule background summarization if needed
            try:
                if not did_sync_summary:
                    convo = storage.get_conversation(conversation_id)
                    if convo and convo.get('messages'):
                        finals = [m for m in convo.get('messages', []) if m.get('role') == 'assistant' and isinstance(m.get('stage3'), dict) and m.get('stage3', {}).get('response')]
                        count = len(finals)
                        if count > SUMMARY_RETENTION:
                            num_to_summarize = count - SUMMARY_RETENTION
                            try:
                                from .config_store import get_chairman_model
                                from .config import CHAIRMAN_MODEL
                                chair = get_chairman_model() or CHAIRMAN_MODEL
                            except Exception:
                                chair = None
                            if chair:
                                asyncio.create_task(_background_summarize_and_persist(conversation_id, num_to_summarize, chair, provider))
            except Exception:
                pass

            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except Exception as e:
            try:
                storage.mark_last_user_message_status(conversation_id, 'failed')
            except Exception:
                pass
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type='text/event-stream')


@app.get("/api/available-models")
async def available_models(provider: str = "ollama"):
    """Return a list of available models for the selected provider.

    Query parameter `provider` can be 'ollama' or 'openrouter'.
    """
    if provider and provider.lower() in ("ollama", "local"):
        models = await ollama.list_models()
        return {"provider": "ollama", "models": models}

    # Default to returning configured council models for OpenRouter
    from .config import COUNCIL_MODELS
    return {"provider": "openrouter", "models": COUNCIL_MODELS}

@app.get('/api/ollama/registry')
async def ollama_registry(query: str):
    """Return remote registry model names for a family or query string."""
    res = await ollama.search_registry(query)
    return {"query": query, "models": res}


@app.get('/api/council-config')
async def get_council_config():
    conf = config_store.get_config()
    # Add recommended list to payload, picking size variants based on machine specs
    from .config import RECOMMENDED_OLLAMA_MODELS_MAP

    async def _machine_specs():
        # Try to gather RAM (bytes) and CPU count; best-effort (psutil if available)
        import os
        specs = {'cpus': os.cpu_count() or 1, 'ram_bytes': None}
        try:
            import psutil
            specs['cpus'] = psutil.cpu_count(logical=False) or specs['cpus']
            specs['ram_bytes'] = psutil.virtual_memory().total
        except Exception:
            try:
                # Fallback for POSIX: use sysconf
                if hasattr(os, 'sysconf'):
                    pages = os.sysconf('SC_PHYS_PAGES')
                    page_size = os.sysconf('SC_PAGE_SIZE')
                    specs['ram_bytes'] = pages * page_size
            except Exception:
                specs['ram_bytes'] = None
        return specs

    def _pick_variant(variants, specs):
        # Simple heuristic: choose small/medium/large based on RAM
        ram = specs.get('ram_bytes') or 0
        # thresholds in bytes: 24GB (small), 48GB (medium), 96GB (large)
        if ram >= 96 * 1024 ** 3:
            preferred = list(reversed(variants))  # prefer largest
        elif ram >= 48 * 1024 ** 3:
            preferred = variants[-2:] + variants[:1]
        else:
            preferred = variants[:2]

        # Return first variant that is in preferred order (may not be installed)
        return preferred[0] if preferred else (variants[0] if variants else None)

    specs = await _machine_specs()
    # Build a prioritized recommended list of concrete model names
    recommended = []
    for family, variants in RECOMMENDED_OLLAMA_MODELS_MAP.items():
        chosen = _pick_variant(variants, specs)
        if chosen:
            recommended.append(chosen)

    # Query installed models from Ollama to pick accurate names when available
    try:
        installed = await ollama.list_models()
    except Exception:
        installed = []

    recommended_objs = []
    installed_lc = [m.lower() for m in installed]
    def gen_candidate_names(family, variants):
        candidates = []
        # variants are full model names
        candidates.extend(variants)
        # add family itself and family:latest if not already
        if family not in candidates:
            candidates.append(family)
        latest = f"{family}:latest"
        if latest not in candidates:
            candidates.append(latest)
        # dedupe preserving order
        seen = []
        out = []
        for c in candidates:
            if c not in seen:
                seen.append(c)
                out.append(c)
        return out

    for family, variants in RECOMMENDED_OLLAMA_MODELS_MAP.items():
        chosen = _pick_variant(variants, specs)
        candidates_for_family = gen_candidate_names(family, variants)

        # Try to find an installed variant that matches family or variant token
        found = None
        for cand in installed:
            low = cand.lower()
            if chosen and chosen.lower() in low:
                found = cand
                break
            if family.lower() in low:
                found = cand
                break
            for v in variants:
                if v.lower() in low:
                    found = cand
                    break
            if found:
                break

        if found:
            recommended_objs.append({'family': family, 'installed': True, 'name': found, 'candidates': [found] + [c for c in candidates_for_family if c != found]})
        else:
            suggested_name = chosen or (variants[0] if variants else family)
            recommended_objs.append({'family': family, 'installed': False, 'name': suggested_name, 'candidates': candidates_for_family})

    out = dict(conf)
    out['recommended_ollama_models'] = recommended_objs
    out['machine_specs'] = {'cpus': specs.get('cpus'), 'ram_bytes': specs.get('ram_bytes')}
    out['installed_models'] = installed
    return out


@app.post('/api/council-config')
async def set_council_config(body: Dict[str, Any]):
    # Validate and update persisted config
    provider = body.get('provider')
    council_models = body.get('council_models')
    chairman_model = body.get('chairman_model')

    conf = config_store.get_config()
    if provider:
        conf['provider'] = provider
    if isinstance(council_models, list):
        conf['council_models'] = council_models
    if chairman_model:
        conf['chairman_model'] = chairman_model

    config_store.save_config(conf)
    return conf


@app.post('/api/ollama/install')
async def ollama_install(body: Dict[str, Any]):
    model = body.get('model')
    if not model:
        raise HTTPException(status_code=400, detail='model required')
    result = await ollama.install_model(model)
    return result


@app.post('/api/ollama/install/stream')
async def ollama_install_stream(body: Dict[str, Any]):
    """Stream `ollama pull` output as Server-Sent Events (SSE).

    Clients should POST JSON {model: 'name'} and will receive SSE events
    of the form: data: {type: 'install_log', line: '...'} and a final
    {type:'install_complete', success: bool, output: '...'}
    """
    model = body.get('model')
    if not model:
        raise HTTPException(status_code=400, detail='model required')

    async def event_gen():
        try:
            async for ev in ollama.install_model_stream(model):
                # map local event types to SSE payloads with structured per-attempt info
                t = ev.get('type')
                if t == 'attempt_start':
                    payload = {'type': 'install_attempt_start', 'candidate': ev.get('candidate')}
                    yield f"data: {json.dumps(payload)}\n\n"
                elif t == 'attempt_log':
                    payload = {'type': 'install_attempt_log', 'candidate': ev.get('candidate'), 'line': ev.get('line')}
                    yield f"data: {json.dumps(payload)}\n\n"
                elif t == 'attempt_complete':
                    payload = {
                        'type': 'install_attempt_complete',
                        'candidate': ev.get('candidate'),
                        'success': ev.get('success'),
                        'output': ev.get('output'),
                        'returncode': ev.get('returncode')
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                elif t == 'complete':
                    payload = {'type': 'install_complete', 'success': ev.get('success'), 'output': ev.get('output')}
                    # include attempted candidate or attempts list if present
                    if ev.get('attempted'):
                        payload['attempted'] = ev.get('attempted')
                    if ev.get('attempts'):
                        payload['attempts'] = ev.get('attempts')
                    yield f"data: {json.dumps(payload)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post('/api/ollama/uninstall')
async def ollama_uninstall(body: Dict[str, Any]):
    model = body.get('model')
    if not model:
        raise HTTPException(status_code=400, detail='model required')
    result = await ollama.uninstall_model(model)
    return result


@app.post('/api/ollama/uninstall/stream')
async def ollama_uninstall_stream(body: Dict[str, Any]):
    model = body.get('model')
    if not model:
        raise HTTPException(status_code=400, detail='model required')

    async def event_gen():
        try:
            async for ev in ollama.uninstall_model_stream(model):
                if ev.get('type') == 'line':
                    payload = {'type': 'uninstall_log', 'line': ev.get('line')}
                    yield f"data: {json.dumps(payload)}\n\n"
                elif ev.get('type') == 'complete':
                    payload = {'type': 'uninstall_complete', 'success': ev.get('success'), 'output': ev.get('output')}
                    yield f"data: {json.dumps(payload)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_gen(), media_type='text/event-stream')


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and run the 3-stage council process.
    Returns the complete response with all stages.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    # Add user message (marked pending)
    storage.add_user_message(conversation_id, request.content)

    # If this is the first message, generate a title
    if is_first_message:
        title = await generate_conversation_title(request.content, provider=request.provider)
        storage.update_conversation_title(conversation_id, title)

    # Compute prior assistant final answers (chronological). We'll use up to
    # `IMMEDIATE_CONTEXT_KEEP` previous messages for immediate context.
    prior_list = []
    try:
        convo = storage.get_conversation(conversation_id)
        if convo and convo.get('messages'):
            for m in convo.get('messages', []):
                if m.get('role') == 'assistant' and isinstance(m.get('stage3'), dict):
                    resp = m.get('stage3', {}).get('response')
                    if resp:
                        prior_list.append(resp)
    except Exception:
        prior_list = []

    # If we have more than `IMMEDIATE_CONTEXT_KEEP` prior finals, ask the
    # chairman to summarize the older ones, then keep the most recent
    # `IMMEDIATE_CONTEXT_KEEP` in the immediate prompt and persist the
    # summary as an assistant final message.
    prior_context = None
    did_sync_summary = False
    if len(prior_list) == 0:
        prior_context = None
    elif len(prior_list) <= IMMEDIATE_CONTEXT_KEEP:
        # use last up to IMMEDIATE_CONTEXT_KEEP
        prior_context = '\n\n'.join(prior_list[-IMMEDIATE_CONTEXT_KEEP:])
    else:
        # summarize the older ones (all except the last IMMEDIATE_CONTEXT_KEEP)
        to_summarize = prior_list[:-IMMEDIATE_CONTEXT_KEEP]
        remaining = prior_list[-IMMEDIATE_CONTEXT_KEEP:]
        # Build summarization prompt
        summary_prompt = 'Summarize the following previous final answers into a concise paragraph (one paragraph, keep it short):\n\n'
        for i, p in enumerate(to_summarize, start=1):
            summary_prompt += f"Answer {i}: {p}\n\n"

        # Choose chairman model
        try:
            from .config_store import get_chairman_model
            from .config import CHAIRMAN_MODEL
            chair = get_chairman_model() or CHAIRMAN_MODEL
        except Exception:
            chair = None

        summary_text = None
        try:
            if chair:
                # Use llm_client to call the chairman for summarization
                from .llm_client import query_model as llm_query
                resp = await llm_query(chair, [{"role": "user", "content": summary_prompt}], provider=request.provider)
                if resp is not None:
                    summary_text = resp.get('content', '').strip()
        except Exception:
            summary_text = None

        if summary_text:
            # Persist the summary as an assistant final message but keep originals
            try:
                convo = storage.get_conversation(conversation_id)
                if convo and convo.get('messages'):
                    summary_msg = {'role': 'assistant', 'stage1': [], 'stage2': [], 'stage3': {'model': chair, 'response': summary_text, 'metadata': {'summarized_count': len(to_summarize), 'chairman_model': chair, 'summary_generated_at': datetime.utcnow().isoformat()}}}
                    convo['messages'].append(summary_msg)
                    storage.save_conversation(convo)
                    prior_context = summary_text + '\n\n' + '\n\n'.join(remaining)
                    did_sync_summary = True
                else:
                    prior_context = '\n\n'.join(remaining)
            except Exception:
                prior_context = '\n\n'.join(remaining)
            else:
                # summarization failed; fall back to using the most recent
                # IMMEDIATE_CONTEXT_KEEP responses
                prior_context = '\n\n'.join(remaining)

    # Run the 3-stage council process with prior_context
    try:
        stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
            request.content,
            provider=request.provider,
            prior_context=prior_context,
        )

        # Add assistant message with all stages
        storage.add_assistant_message(
            conversation_id,
            stage1_results,
            stage2_results,
            stage3_result
        )

        # Mark the user's last message as complete
        try:
            storage.mark_last_user_message_status(conversation_id, 'complete')
        except Exception:
            pass

    except Exception as e:
        # Mark last user message as failed so UI can offer retry
        try:
            storage.mark_last_user_message_status(conversation_id, 'failed')
        except Exception:
            pass
        raise

    # Return the complete response with metadata
    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "metadata": metadata
    }


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and stream the 3-stage council process.
    Returns Server-Sent Events as each stage completes.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    async def event_generator():
        try:
            # Add user message
            storage.add_user_message(conversation_id, request.content)

            # Start title generation in parallel (don't await yet)
            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(request.content, provider=request.provider))

            # Compute prior assistant final answers (chronological). We'll use up to
            # `IMMEDIATE_CONTEXT_KEEP` previous messages for immediate context.
            prior_list = []
            did_sync_summary = False
            try:
                convo = storage.get_conversation(conversation_id)
                if convo and convo.get('messages'):
                    for m in convo.get('messages', []):
                        if m.get('role') == 'assistant' and isinstance(m.get('stage3'), dict):
                            resp = m.get('stage3', {}).get('response')
                            if resp:
                                prior_list.append(resp)
            except Exception:
                prior_list = []

            # If we have more than `IMMEDIATE_CONTEXT_KEEP` prior finals, ask the
            # chairman to summarize the older ones, then persist the summary and
            # keep the last `IMMEDIATE_CONTEXT_KEEP` items as immediate context.
            prior_context = None
            if len(prior_list) == 0:
                prior_context = None
            elif len(prior_list) <= IMMEDIATE_CONTEXT_KEEP:
                prior_context = '\n\n'.join(prior_list[-IMMEDIATE_CONTEXT_KEEP:])
            else:
                to_summarize = prior_list[:-IMMEDIATE_CONTEXT_KEEP]
                remaining = prior_list[-IMMEDIATE_CONTEXT_KEEP:]
                summary_prompt = 'Summarize the following previous final answers into a concise paragraph (one paragraph, keep it short):\n\n'
                for i, p in enumerate(to_summarize, start=1):
                    summary_prompt += f"Answer {i}: {p}\n\n"
                try:
                    from .config_store import get_chairman_model
                    from .config import CHAIRMAN_MODEL
                    chair = get_chairman_model() or CHAIRMAN_MODEL
                except Exception:
                    chair = None
                summary_text = None
                try:
                    if chair:
                        from .llm_client import query_model as llm_query
                        resp = await llm_query(chair, [{"role": "user", "content": summary_prompt}], provider=request.provider)
                        if resp is not None:
                            summary_text = resp.get('content', '').strip()
                except Exception:
                    summary_text = None

                if summary_text:
                        try:
                            convo = storage.get_conversation(conversation_id)
                            if convo and convo.get('messages'):
                                summary_msg = {'role': 'assistant', 'stage1': [], 'stage2': [], 'stage3': {'model': chair, 'response': summary_text, 'metadata': {'summarized_count': len(to_summarize), 'chairman_model': chair, 'summary_generated_at': datetime.utcnow().isoformat()}}}
                                convo['messages'].append(summary_msg)
                                storage.save_conversation(convo)
                                prior_context = summary_text + '\n\n' + '\n\n'.join(remaining)
                                did_sync_summary = True
                            else:
                                prior_context = '\n\n'.join(remaining)
                        except Exception:
                            prior_context = '\n\n'.join(remaining)
                else:
                    prior_context = '\n\n'.join(remaining)

            # Prepare combined query - prioritize reply_to_response, then user's message, then context
            if request.reply_to_response:
                # When replying to a specific message, give it highest priority
                combined_query = f"The user is replying to this previous response:\n\n\"{request.reply_to_response}\"\n\nUser's reply: {request.content}"
                if prior_context:
                    combined_query += "\n\nAdditional context from earlier in the conversation:\n" + prior_context
            elif prior_context:
                combined_query = request.content + "\n\nFor context, here are previous responses:\n" + prior_context
            else:
                combined_query = request.content

            # Check if we should skip stages 1 and 2
            if request.skip_stages:
                # Skip directly to Chairman - simple direct query without council synthesis role
                stage1_results = []
                stage2_results = []
                label_to_model = {}
                aggregate_rankings = []
                
                # Direct Chairman response with streaming
                yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
                
                # Get chairman model
                from .config_store import get_chairman_model
                from .config import CHAIRMAN_MODEL
                from .llm_client import query_model
                chairman = get_chairman_model() or CHAIRMAN_MODEL
                
                # For Ollama provider, ensure chairman is installed
                if request.provider and request.provider.lower() in ('ollama', 'local'):
                    from . import ollama
                    installed = await ollama.list_models()
                    if chairman not in installed and installed:
                        chairman = installed[0]
                
                # Simple direct query with streaming
                messages = [{"role": "user", "content": combined_query}]
                
                stage3_result = None
                accumulated_response = ""
                # Note: query_model is async, so we await it to get the generator
                generator = await query_model(chairman, messages, provider=request.provider, stream=True)
                async for chunk in generator:
                    if chunk.get('type') == 'chunk':
                        content = chunk.get('content', '')
                        accumulated_response += content
                        # Send each chunk to the frontend as it arrives
                        yield f"data: {json.dumps({'type': 'stage3_chunk', 'content': content, 'model': chairman})}\n\n"
                    elif chunk.get('type') == 'done':
                        stage3_result = {
                            "model": chairman,
                            "response": accumulated_response
                        }
                    elif chunk.get('type') == 'error':
                        stage3_result = {
                            "model": chairman,
                            "response": f"Error: {chunk.get('message', 'Unable to generate response.')}"
                        }
                        break
                
                # If stage3_result was not set, create fallback
                if stage3_result is None:
                    if accumulated_response:
                        stage3_result = {
                            "model": chairman,
                            "response": accumulated_response
                        }
                    else:
                        stage3_result = {
                            "model": chairman,
                            "response": "Error: No response generated from chairman."
                        }
                
                yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"
            else:
                # Normal 3-stage process
                # Stage 1: Collect responses (include prior_context as extra context if present)
                yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
                
                stage1_results = []
                # Use streaming for Stage 1
                # When replying to a specific message, use combined_query which includes the reply context
                # Otherwise use the original content with prior_context
                stage1_query = combined_query if request.reply_to_response else request.content
                stage1_context = None if request.reply_to_response else prior_context
                # Note: stage1_collect_responses is async, so we await it to get the generator
                generator = await stage1_collect_responses(stage1_query, provider=request.provider, prior_context=stage1_context, stream=True)
                
                # We need to aggregate results for Stage 2
                model_responses = {} # model -> accumulated text
                
                async for model, chunk in generator:
                    if chunk.get('type') == 'start':
                        # Notify frontend that this specific model has started producing output
                        yield f"data: {json.dumps({'type': 'stage1_model_start', 'model': model})}\n\n"
                        continue
                    if chunk.get('type') == 'chunk':
                        content = chunk.get('content', '')
                        if model not in model_responses:
                            model_responses[model] = ""
                        model_responses[model] += content
                        yield f"data: {json.dumps({'type': 'stage1_chunk', 'model': model, 'content': content})}\n\n"
                    elif chunk.get('type') == 'complete':
                        # Model finished
                        pass
                    elif chunk.get('type') == 'error':
                        # Handle error
                        pass
                
                # Construct stage1_results from accumulated responses
                for model, text in model_responses.items():
                    stage1_results.append({'model': model, 'response': text})
                
                yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"

                # Stage 2: Collect rankings
                yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
                
                stage2_results = []
                label_to_model = {}
                
                # Use streaming for Stage 2
                generator = await stage2_collect_rankings(combined_query, stage1_results, provider=request.provider, stream=True)
                
                model_rankings = {} # model -> accumulated text

                async for item in generator:
                    # Check if it's metadata or chunk
                    if isinstance(item, tuple) and item[0] == 'metadata':
                        label_to_model = item[1].get('label_to_model', {})
                        yield f"data: {json.dumps({'type': 'stage2_metadata', 'data': {'label_to_model': label_to_model}})}\n\n"
                        continue

                    model, chunk = item
                    if chunk.get('type') == 'start':
                        # Per-model started event
                        yield f"data: {json.dumps({'type': 'stage2_model_start', 'model': model})}\n\n"
                        continue
                    if chunk.get('type') == 'chunk':
                        content = chunk.get('content', '')
                        if model not in model_rankings:
                            model_rankings[model] = ""
                        model_rankings[model] += content
                        yield f"data: {json.dumps({'type': 'stage2_chunk', 'model': model, 'content': content})}\n\n"
                    elif chunk.get('type') == 'complete':
                        pass
                
                # Construct stage2_results
                for model, text in model_rankings.items():
                    stage2_results.append({'model': model, 'ranking': text})
                    
                aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
                yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

                # Stage 3: Synthesize final answer with streaming
                yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
                
                # Stream the chairman's response using stream=True parameter
                stage3_result = None
                # Note: stage3_synthesize_final is async, so we await it to get the generator
                generator = await stage3_synthesize_final(combined_query, stage1_results, stage2_results, provider=request.provider, stream=True)
                async for chunk in generator:
                    if chunk.get('type') == 'chunk':
                        # Send each chunk to the frontend as it arrives
                        yield f"data: {json.dumps({'type': 'stage3_chunk', 'content': chunk.get('content', ''), 'model': chunk.get('model')})}\n\n"
                    elif chunk.get('type') == 'done':
                        # Final complete response
                        stage3_result = {
                            'model': chunk.get('model'),
                            'response': chunk.get('response', '')
                        }
                        yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"
                    elif chunk.get('type') == 'error':
                        # Error occurred
                        stage3_result = {
                            'model': chunk.get('model', 'unknown'),
                            'response': f"Error: {chunk.get('message', 'Unknown error')}"
                        }
                        yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

            # Wait for title generation if it was started
            if title_task:
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            # Save complete assistant message
            storage.add_assistant_message(
                conversation_id,
                stage1_results,
                stage2_results,
                stage3_result
            )

            # Schedule background summarization if we did not already summarize synchronously
            try:
                if not did_sync_summary:
                    convo = storage.get_conversation(conversation_id)
                    if convo and convo.get('messages'):
                        finals = [m for m in convo.get('messages', []) if m.get('role') == 'assistant' and isinstance(m.get('stage3'), dict) and m.get('stage3', {}).get('response')]
                        count = len(finals)
                        if count > SUMMARY_RETENTION:
                            num_to_summarize = count - SUMMARY_RETENTION
                            try:
                                from .config_store import get_chairman_model
                                from .config import CHAIRMAN_MODEL
                                chair = get_chairman_model() or CHAIRMAN_MODEL
                            except Exception:
                                chair = None
                            if chair:
                                asyncio.create_task(_background_summarize_and_persist(conversation_id, num_to_summarize, chair, request.provider))
            except Exception:
                pass

            # Send completion event
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except Exception as e:
            # Mark last user message as failed so UI can offer retry
            try:
                storage.mark_last_user_message_status(conversation_id, 'failed')
            except Exception:
                pass
            # Send error event
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
