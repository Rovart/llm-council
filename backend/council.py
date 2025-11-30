"""3-stage LLM Council orchestration."""

from typing import List, Dict, Any, Tuple
from .llm_client import query_models_parallel, query_model, query_models_parallel_stream
from .config import COUNCIL_MODELS, CHAIRMAN_MODEL
from .config_store import get_council_models, get_chairman_model


async def stage1_collect_responses(
    user_query: str,
    provider: str | None = None,
    prior_context: List[Dict[str, str]] | None = None,
    stream: bool = False
):
    """
    Stage 1: Collect individual responses from council members.
    
    Args:
        user_query: The user's query
        provider: Provider to use
        prior_context: Previous conversation history
        stream: If True, returns async generator yielding (model, chunk). If False, returns list of results.
    """
    council_members = get_council_models()
    if not council_members:
        council_members = COUNCIL_MODELS

    # Build the prompt messages. prior_context may be a string (joined finals)
    # or a list of message dicts; handle both cases.
    if prior_context:
        if isinstance(prior_context, str):
            combined = prior_context + "\n\n" + user_query
            messages = [{"role": "user", "content": combined}]
        else:
            # assume list of dict messages; append the user query
            messages = list(prior_context) + [{"role": "user", "content": user_query}]
    else:
        messages = [{"role": "user", "content": user_query}]

    # If Ollama provider, prefer installed models only
    if provider and str(provider).lower() in ('ollama', 'local'):
        try:
            from . import ollama
            installed = await ollama.list_models()
            council_members = [m for m in council_members if m in installed]
        except Exception:
            pass

    # Streaming mode: delegate to llm_client.query_models_parallel_stream
    if stream:
        from .llm_client import query_models_parallel_stream
        # query_models_parallel_stream is an async generator function; return
        # the async generator object without awaiting it (awaiting an
        # async-generator raises "object async_generator can't be used in
        # 'await' expression"). The caller should iterate with `async for`.
        return query_models_parallel_stream(council_members, messages, provider=provider)

    # Non-streaming: query all models in parallel and return formatted results
    from .llm_client import query_models_parallel
    responses = await query_models_parallel(council_members, messages, provider=provider)

    stage1_results = []
    for model, resp in (responses or {}).items():
        if resp is not None:
            stage1_results.append({
                'model': model,
                'response': resp.get('content', '')
            })

    return stage1_results


async def stage2_collect_rankings(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    provider: str | None = None,
    stream: bool = False
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """
    Stage 2: Each model ranks the anonymized responses.

    Args:
        user_query: The original user query
        stage1_results: Results from Stage 1

    Returns:
        Tuple of (rankings list, label_to_model mapping)
    """
    # Create anonymized labels for responses (Response A, Response B, etc.)
    labels = [chr(65 + i) for i in range(len(stage1_results))]  # A, B, C, ...

    # Create mapping from label to model name
    label_to_model = {
        f"Response {label}": result['model']
        for label, result in zip(labels, stage1_results)
    }

    # Build the ranking prompt
    responses_text = "\n\n".join([
        f"Response {label}:\n{result['response']}"
        for label, result in zip(labels, stage1_results)
    ])

    ranking_prompt = f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

Now provide your evaluation and ranking:"""

    messages = [{"role": "user", "content": ranking_prompt}]

    # Get rankings from all council models in parallel
    council_models = get_council_models()
    if not council_models:
        if provider and provider.lower() in ('ollama', 'local'):
            from . import ollama
            council_models = await ollama.list_models()
        else:
            council_models = COUNCIL_MODELS
    # For Ollama provider, filter to only installed models
    if provider and provider.lower() in ('ollama', 'local'):
        from . import ollama
        installed = await ollama.list_models()
        
        def is_available(m):
            if m in installed: return True
            if f"{m}:latest" in installed: return True
            if m.endswith(":latest") and m[:-7] in installed: return True
            return False
            
        council_models = [m for m in council_models if is_available(m)]
    # If streaming requested, return an async generator that yields metadata
    # and then per-model chunks coming from the llm client stream helper.
    if stream:
        from .llm_client import query_models_parallel_stream

        async def _stream_gen():
            # Send metadata first so caller knows label mapping
            yield ('metadata', {'label_to_model': label_to_model})
            gen = query_models_parallel_stream(council_models, messages, provider=provider)
            async for model_name, chunk in gen:
                yield (model_name, chunk)

        return _stream_gen()

    responses = await query_models_parallel(council_models, messages, provider=provider)

    # Format results
    stage2_results = []
    for model, response in responses.items():
        if response is not None:
            full_text = response.get('content', '')
            parsed = parse_ranking_from_text(full_text)
            stage2_results.append({
                "model": model,
                "ranking": full_text,
                "parsed_ranking": parsed
            })

    return stage2_results, label_to_model


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    chairman_model: str | None = None,
    provider: str | None = None,
    stream: bool = False
):
    """
    Stage 3: Chairman synthesizes final response.

    Args:
        user_query: The original user query
        stage1_results: Individual model responses from Stage 1
        stage2_results: Rankings from Stage 2
        chairman_model: Optional chairman model override
        provider: Provider to use
        stream: If True, returns an async generator yielding chunks. If False, returns complete response dict.

    Returns:
        If stream=False: Dict with 'model' and 'response' keys
        If stream=True: Async generator yielding chunk dicts
    """
    # Build comprehensive context for chairman
    stage1_text = "\n\n".join([
        f"Model: {result['model']}\nResponse: {result['response']}"
        for result in stage1_results
    ])

    stage2_text = "\n\n".join([
        f"Model: {result['model']}\nRanking: {result['ranking']}"
        for result in stage2_results
    ])

    chairman_prompt = f"""You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.

Original Question: {user_query}

STAGE 1 - Individual Responses:
{stage1_text}

STAGE 2 - Peer Rankings:
{stage2_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question. Consider:
- The individual responses and their insights
- The peer rankings and what they reveal about response quality
- Any patterns of agreement or disagreement

Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""

    messages = [{"role": "user", "content": chairman_prompt}]

    # Query the chairman model
    chairman = chairman_model or get_chairman_model() or CHAIRMAN_MODEL
    
    if stream:
        return _stage3_synthesize_final_stream(chairman, messages, provider)
    
    # Non-streaming mode (original implementation)
    response = await query_model(chairman, messages, provider=provider, stream=False)

    if response is None:
        # Fallback if chairman fails
        return {
            "model": chairman or CHAIRMAN_MODEL,
            "response": "Error: Unable to generate final synthesis."
        }

    # Debug: log chairman response preview
    try:
        ctxt = response.get('content', '') or ''
        preview = ctxt if len(ctxt) <= 300 else ctxt[:300] + '...'
        print(f"[COUNCIL][STAGE3] chairman={chairman} preview={preview}")
    except Exception:
        print(f"[COUNCIL][STAGE3] chairman={chairman} preview failed")

    return {
        "model": chairman or CHAIRMAN_MODEL,
        "response": response.get('content', '')
    }


async def _stage3_synthesize_final_stream(chairman, messages, provider):
    """Helper generator for streaming stage3 response."""
    accumulated_response = ""
    # Note: query_model with stream=True returns a generator, so we await it to get the generator
    # then iterate.
    generator = await query_model(chairman, messages, provider=provider, stream=True)
    async for chunk in generator:
        if chunk.get('type') == 'chunk':
            content = chunk.get('content', '')
            accumulated_response += content
            # Yield the chunk for frontend display
            yield {
                'type': 'chunk',
                'content': content,
                'model': chairman
            }
        elif chunk.get('type') == 'done':
            # Yield final complete response
            yield {
                'type': 'done',
                'model': chairman,
                'response': accumulated_response
            }
        elif chunk.get('type') == 'error':
            yield {
                'type': 'error',
                'model': chairman,
                'message': chunk.get('message', 'Unknown error')
            }


def parse_ranking_from_text(ranking_text: str) -> List[str]:
    """
    Parse the FINAL RANKING section from the model's response.

    Args:
        ranking_text: The full text response from the model

    Returns:
        List of response labels in ranked order
    """
    import re

    # Look for "FINAL RANKING:" section
    if "FINAL RANKING:" in ranking_text:
        # Extract everything after "FINAL RANKING:"
        parts = ranking_text.split("FINAL RANKING:")
        if len(parts) >= 2:
            ranking_section = parts[1]
            # Try to extract numbered list format (e.g., "1. Response A")
            # This pattern looks for: number, period, optional space, "Response X"
            numbered_matches = re.findall(r'\d+\.\s*Response [A-Z]', ranking_section)
            if numbered_matches:
                # Extract just the "Response X" part
                return [re.search(r'Response [A-Z]', m).group() for m in numbered_matches]

            # Fallback: Extract all "Response X" patterns in order
            matches = re.findall(r'Response [A-Z]', ranking_section)
            return matches

    # Fallback: try to find any "Response X" patterns in order
    matches = re.findall(r'Response [A-Z]', ranking_text)
    return matches


def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str]
) -> List[Dict[str, Any]]:
    """
    Calculate aggregate rankings across all models.

    Args:
        stage2_results: Rankings from each model
        label_to_model: Mapping from anonymous labels to model names

    Returns:
        List of dicts with model name and average rank, sorted best to worst
    """
    from collections import defaultdict

    # Track positions for each model
    model_positions = defaultdict(list)

    for ranking in stage2_results:
        ranking_text = ranking['ranking']

        # Parse the ranking from the structured format
        parsed_ranking = parse_ranking_from_text(ranking_text)

        for position, label in enumerate(parsed_ranking, start=1):
            if label in label_to_model:
                model_name = label_to_model[label]
                model_positions[model_name].append(position)

    # Calculate average position for each model
    aggregate = []
    for model, positions in model_positions.items():
        if positions:
            avg_rank = sum(positions) / len(positions)
            aggregate.append({
                "model": model,
                "average_rank": round(avg_rank, 2),
                "rankings_count": len(positions)
            })

    # Sort by average rank (lower is better)
    aggregate.sort(key=lambda x: x['average_rank'])

    return aggregate


async def generate_conversation_title(user_query: str, provider: str | None = None) -> str:
    """
    Generate a short title for a conversation based on the first user message.

    Args:
        user_query: The first user message

    Returns:
        A short title (3-5 words)
    """
    title_prompt = f"""Generate a very short title (3-5 words maximum) that summarizes the following question.
The title should be concise and descriptive. Do not use quotes or punctuation in the title.

Question: {user_query}

Title:"""

    messages = [{"role": "user", "content": title_prompt}]

    # Choose a title-generation model appropriate to the provider.
    # For Ollama, prefer an installed local model; for OpenRouter, use gemini-2.5-flash.
    title_model = "google/gemini-2.5-flash"
    if provider and str(provider).lower() in ('ollama', 'local'):
        try:
            from . import ollama
            installed = await ollama.list_models()
            # pick the first installed model if available
            if installed:
                title_model = installed[0]
        except Exception:
            title_model = "google/gemini-2.5-flash"

    response = await query_model(title_model, messages, timeout=30.0, provider=provider)

    if response is None:
        # Fallback to a generic title
        return "New Conversation"

    title = response.get('content', 'New Conversation').strip()

    # Clean up the title - remove quotes, limit length
    title = title.strip('"\'')

    # Truncate if too long
    if len(title) > 50:
        title = title[:47] + "..."

    return title


async def run_full_council(user_query: str, provider: str | None = None, prior_context: str | None = None) -> Tuple[List, List, Dict, Dict]:
    """
    Run the complete 3-stage council process.

    Args:
        user_query: The user's question

    Returns:
        Tuple of (stage1_results, stage2_results, stage3_result, metadata)
    """
    # Combine prior context and user query if prior_context supplied
    combined_query = prior_context + "\n\n" + user_query if prior_context else user_query

    # Stage 1: Collect individual responses (stage1 accepts prior_context as well)
    stage1_results = await stage1_collect_responses(user_query, provider=provider, prior_context=prior_context)

    # If no models responded successfully, return error
    if not stage1_results:
        return [], [], {
            "model": "error",
            "response": "All models failed to respond. Please try again."
        }, {}

    # Stage 2: Collect rankings
    stage2_results, label_to_model = await stage2_collect_rankings(combined_query, stage1_results, provider=provider)

    # Calculate aggregate rankings
    aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)

    # Get and filter chairman model for Ollama
    chairman_model = get_chairman_model() or CHAIRMAN_MODEL
    if provider and provider.lower() in ('ollama', 'local'):
        from . import ollama
        installed = await ollama.list_models()
        if chairman_model not in installed:
            # Use the first council model as chairman if available
            council_models = [r['model'] for r in stage1_results]
            chairman_model = council_models[0] if council_models else None

    # Stage 3: Synthesize final answer
    stage3_result = await stage3_synthesize_final(
        combined_query,
        stage1_results,
        stage2_results,
        chairman_model=chairman_model,
        provider=provider
    )

    # Prepare metadata
    metadata = {
        "label_to_model": label_to_model,
        "aggregate_rankings": aggregate_rankings
    }

    return stage1_results, stage2_results, stage3_result, metadata
