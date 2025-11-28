"""Simple Ollama client supporting local HTTP API (and CLI fallback).

This module provides `query_model` and `query_models_parallel` with the
same signature shape as the existing OpenRouter client so the rest of the
codebase can switch providers easily.

Notes:
- The Ollama HTTP API is expected at `OLLAMA_API_URL` (default http://localhost:11434).
- If `OLLAMA_USE_CLI` is true, the code will try to call the Ollama CLI binary
  to generate output as a fallback. The CLI invocation is best-effort and may
  require adjustments depending on your Ollama version.
"""

import asyncio
import json
import shlex
from typing import List, Dict, Any, Optional

import httpx

from .config import OLLAMA_API_URL, OLLAMA_USE_CLI, OLLAMA_CLI_PATH


async def _call_ollama_http(model: str, prompt: str, timeout: float = 120.0) -> Optional[Dict[str, Any]]:
    url = OLLAMA_API_URL.rstrip('/') + '/api/generate'
    payload = {
        "model": model,
        "prompt": prompt,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

            # Ollama responses vary by version; attempt to extract text sensibly.
            # Common keys: 'result', 'data', or 'generated'. We'll try a few fallbacks.
            if isinstance(data, dict):
                # If there's a top-level 'result' string
                if 'result' in data and isinstance(data['result'], str):
                    return {'content': data['result']}

                # If 'generated' exists (list of dicts with 'text')
                if 'generated' in data and isinstance(data['generated'], list):
                    texts = []
                    for g in data['generated']:
                        if isinstance(g, dict):
                            text = g.get('text') or g.get('output') or ''
                            texts.append(text)
                        else:
                            texts.append(str(g))
                    return {'content': '\n'.join(texts)}

                # If 'data' contains items
                if 'data' in data:
                    try:
                        return {'content': json.dumps(data['data'])}
                    except Exception:
                        return {'content': str(data['data'])}

            # Fallback: stringify entire response
            return {'content': json.dumps(data)}

    except Exception as e:
        print(f"Ollama HTTP error for model {model}: {e}")
        return None


async def _call_ollama_cli(model: str, prompt: str, timeout: float = 120.0) -> Optional[Dict[str, Any]]:
    """Invoke the Ollama CLI as a fallback. This is a best-effort approach.

    It calls: `ollama generate <model> --prompt '<prompt>' --quiet` and captures stdout.
    """
    try:
        cmd = [OLLAMA_CLI_PATH, 'generate', model, '--prompt', prompt, '--quiet']

        # Use asyncio subprocess to not block the loop
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return None

        if proc.returncode != 0:
            err = stderr.decode(errors='ignore') if stderr else ''
            print(f"Ollama CLI error: returncode={proc.returncode} stderr={err}")
            return None

        text = stdout.decode(errors='ignore')
        return {'content': text}

    except FileNotFoundError:
        print(f"Ollama CLI not found at path: {OLLAMA_CLI_PATH}")
        return None
    except Exception as e:
        print(f"Ollama CLI error for model {model}: {e}")
        return None


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0
) -> Optional[Dict[str, Any]]:
    """Query a model served by Ollama.

    The `messages` list is converted into a single prompt string by joining
    role labels and contents. Ollama models commonly accept a plain prompt.
    """
    # Convert messages into a single prompt string
    prompt = '\n\n'.join([f"[{m.get('role','user')}] {m.get('content','')}" for m in messages])

    # Prefer HTTP API, but allow CLI fallback if configured
    if not OLLAMA_USE_CLI:
        result = await _call_ollama_http(model, prompt, timeout=timeout)
        if result is not None:
            return result

    # Try CLI fallback if HTTP failed or CLI explicitly enabled
    if OLLAMA_USE_CLI:
        result = await _call_ollama_cli(model, prompt, timeout=timeout)
        if result is not None:
            return result

    # If HTTP was allowed but failed, try CLI as a best-effort
    if not OLLAMA_USE_CLI:
        result = await _call_ollama_cli(model, prompt, timeout=timeout)
        return result

    return None


async def query_models_parallel(models: List[str], messages: List[Dict[str, str]]) -> Dict[str, Optional[Dict[str, Any]]]:
    import asyncio

    tasks = [query_model(model, messages) for model in models]
    responses = await asyncio.gather(*tasks)
    return {model: resp for model, resp in zip(models, responses)}


async def install_model(model: str, timeout: float = 600) -> Dict[str, Any]:
    """Install a model via the Ollama CLI (`ollama pull <model>`).

    Returns dict {'success': bool, 'output':Str}
    """
    import asyncio
    if not OLLAMA_USE_CLI:
        # still allow if CLI path exists - best-effort
        try:
            import shutil
            if not shutil.which(OLLAMA_CLI_PATH):
                return {'success': False, 'output': 'Ollama CLI not installed or OLLAMA_USE_CLI not enabled.'}
        except Exception:
            return {'success': False, 'output': 'Ollama CLI check failed.'}

    cmd = [OLLAMA_CLI_PATH, 'pull', model]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {'success': False, 'output': 'Timeout waiting for model install'}

        if proc.returncode != 0:
            return {'success': False, 'output': stderr.decode(errors='ignore')}

        out = stdout.decode(errors='ignore')
        return {'success': True, 'output': out}
    except FileNotFoundError:
        return {'success': False, 'output': f'Ollama CLI not found at path: {OLLAMA_CLI_PATH}'}
    except Exception as e:
        return {'success': False, 'output': str(e)}


async def list_models(timeout: float = 10.0) -> List[str]:
    """Return a list of available model names from local Ollama.

    Tries the HTTP API first, then falls back to the CLI.
    """
    # Try HTTP API endpoints that Ollama may expose
    candidates = [
        OLLAMA_API_URL.rstrip('/') + '/api/models',
        OLLAMA_API_URL.rstrip('/') + '/models',
        OLLAMA_API_URL.rstrip('/') + '/v1/models',
    ]

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for url in candidates:
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    # data might be a list of names or list of dicts
                    models = []
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, str):
                                models.append(item)
                            elif isinstance(item, dict):
                                # common keys: 'name', 'model', 'id'
                                name = item.get('name') or item.get('model') or item.get('id')
                                if name:
                                    models.append(name)
                    elif isinstance(data, dict):
                        # sometimes models listed under 'models' key
                        m = data.get('models') or data.get('data')
                        if isinstance(m, list):
                            for item in m:
                                if isinstance(item, str):
                                    models.append(item)
                                elif isinstance(item, dict):
                                    n = item.get('name') or item.get('model') or item.get('id')
                                    if n:
                                        models.append(n)

                    if models:
                        # dedupe and return
                        seen = []
                        for x in models:
                            if x not in seen:
                                seen.append(x)
                        return seen
                except Exception:
                    continue
    except Exception:
        pass

    # Fallback: try CLI 'ollama list' or 'ollama ls' and parse output
    try:
        proc = await asyncio.create_subprocess_exec(
            OLLAMA_CLI_PATH, 'list',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return []

        if proc.returncode != 0:
            # try 'ollama ls'
            proc2 = await asyncio.create_subprocess_exec(
                OLLAMA_CLI_PATH, 'ls',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout2, stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc2.kill()
                await proc2.wait()
                return []
            if proc2.returncode != 0:
                return []
            out = stdout2.decode(errors='ignore')
        else:
            out = stdout.decode(errors='ignore')

        models = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            # crude parsing: assume first token is model name
            parts = line.split()
            if parts:
                name = parts[0]
                # skip headers like 'MODEL' or lines starting with '-'
                if name.lower() in ('model', 'models', 'name'):
                    continue
                models.append(name)

        # dedupe
        seen = []
        for x in models:
            if x not in seen:
                seen.append(x)
        return seen
    except FileNotFoundError:
        # CLI not installed
        return []
    except Exception as e:
        print(f"Error listing ollama models: {e}")
        return []
