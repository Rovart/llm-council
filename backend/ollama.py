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
import time

from .config import OLLAMA_API_URL, OLLAMA_USE_CLI, OLLAMA_CLI_PATH

# Detected API URL can be set at runtime if the configured OLLAMA_API_URL is not correct.
_DETECTED_OLLAMA_API_URL: str | None = None


async def _validate_api_url(url: str, timeout: float = 1.0) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            candidates = [
                url.rstrip('/'),
                url.rstrip('/') + '/api/models',
                url.rstrip('/') + '/models',
                url.rstrip('/') + '/v1/models',
            ]
            for c in candidates:
                try:
                    r = await client.get(c)
                    if r.status_code == 200:
                        return True
                except Exception:
                    continue
            return False
    except Exception:
        return False


async def _discover_api_url() -> str:
    """Attempt to discover a working Ollama HTTP API URL.

    Strategy:
    - If `OLLAMA_API_URL` env works, use it.
    - If not, scan a small port range on localhost to find one that responds to `/api/models`.
    - Cache the detected URL in module variable.
    """
    global _DETECTED_OLLAMA_API_URL
    if _DETECTED_OLLAMA_API_URL:
        return _DETECTED_OLLAMA_API_URL

    # 1) try configured env
    try:
        if OLLAMA_API_URL and await _validate_api_url(OLLAMA_API_URL, timeout=0.9):
            _DETECTED_OLLAMA_API_URL = OLLAMA_API_URL.rstrip('/')
            return _DETECTED_OLLAMA_API_URL
    except Exception:
        pass

    # 2) probe common default and nearby ports
    candidates = []
    # prioritize common default
    candidates.append('http://localhost:11434')
    # add a small range in case GUI runner used a different port
    for p in range(11400, 11451):
        candidates.append(f'http://localhost:{p}')

    for c in candidates:
        try:
            if await _validate_api_url(c, timeout=0.6):
                _DETECTED_OLLAMA_API_URL = c.rstrip('/')
                return _DETECTED_OLLAMA_API_URL
        except Exception:
            continue

    # last resort: return configured value (may be wrong)
    _DETECTED_OLLAMA_API_URL = OLLAMA_API_URL.rstrip('/') if OLLAMA_API_URL else 'http://localhost:11434'
    return _DETECTED_OLLAMA_API_URL


def get_detected_api_url() -> str:
    """Return the the last detected Ollama API URL (or the configured OLLAMA_API_URL)."""
    if _DETECTED_OLLAMA_API_URL:
        return _DETECTED_OLLAMA_API_URL
    return OLLAMA_API_URL

# Try multiple endpoints for Ollama generation
OLLAMA_GENERATE_ENDPOINTS = [
    '/api/generate',
    '/v1/generate',
    '/generate',
    '/v1/predict',
    '/api/predict',
    '/v1/completions',
]

# Try CLI subcommands for generation (avoid unsupported commands like 'predict' on some versions)
OLLAMA_CLI_GENERATE_CMDS = ['generate', 'run']
OLLAMA_CLI_UNINSTALL_CMDS = ['rm', 'remove', 'uninstall']


async def _call_ollama_http(model: str, prompt: str, timeout: float = 120.0) -> Optional[Dict[str, Any]]:
    base = (await _discover_api_url()).rstrip('/')
    # Try payload variants to support different Ollama API versions
    payload_variants = [
        {"model": model, "prompt": prompt},
        {"model": model, "input": prompt},
        {"model": model, "messages": [{"role": "user", "content": prompt}]},
    ]
    payload = {
        "model": model,
        "prompt": prompt,
    }

    try:
        data = None
        resp = None
        start = time.time()
        print(f"[OLLAMA][HTTP] start model={model} url_base={base} timeout={timeout}")
        async with httpx.AsyncClient(timeout=timeout) as client:
            for endpoint in OLLAMA_GENERATE_ENDPOINTS:
                url = base + endpoint
                for payload in payload_variants:
                    try:
                        resp = await client.post(url, json=payload)
                        resp.raise_for_status()
                        # Response can be JSON or text, parse after
                        # We'll attempt to parse JSON below
                        break
                    except Exception:
                        resp = None
                        continue
                if resp is not None:
                    try:
                        # Debug: log status and a short preview of body length
                        try:
                            body_preview = resp.text[:400]
                        except Exception:
                            body_preview = '<unreadable>'
                        print(f"[OLLAMA][HTTP] got response status={resp.status_code} body_len={len(resp.text or '')} preview={body_preview}")
                        data = resp.json()
                        break
                    except ValueError:
                        # not JSON: try to parse NDJSON or fallback to raw text
                        text = resp.text
                        lines = [l for l in text.splitlines() if l.strip()]
                        # Accumulate streaming NDJSON fragments into a single response
                        fragments = []
                        for line in lines:
                            try:
                                obj = json.loads(line)
                                if isinstance(obj, dict):
                                    # prefer 'result' or 'generated' if present
                                    if 'result' in obj and isinstance(obj['result'], str):
                                        fragments.append(obj['result'])
                                        continue
                                    if 'generated' in obj and isinstance(obj['generated'], list):
                                        for g in obj['generated']:
                                            if isinstance(g, dict):
                                                fragments.append(g.get('text') or g.get('output') or '')
                                            else:
                                                fragments.append(str(g))
                                        continue
                                    if 'response' in obj and isinstance(obj['response'], str):
                                        fragments.append(obj['response'])
                                        continue
                                    if 'data' in obj:
                                        try:
                                            fragments.append(json.dumps(obj['data']))
                                        except Exception:
                                            fragments.append(str(obj['data']))
                                        continue
                            except Exception:
                                continue
                        if fragments:
                            combined = ''.join(fragments)
                            return {'content': combined}
                        # fallback: set data from raw text to be stringified by caller
                        # If no JSON and raw text present, capture it
                        data = text
                        print(f"[OLLAMA][HTTP] raw text length={len(text)} preview={text[:400]}")
                        break
            if data is None and resp is None:
                raise Exception("No Ollama generate endpoint accepted our request")
            # if data is already a dict (json), we've got it; if data is text, we'll stringify

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
            out = {'content': json.dumps(data)}
            dur = time.time() - start
            # Log a truncated preview of the content for debugging
            try:
                preview = out.get('content', '')
                if isinstance(preview, str) and len(preview) > 400:
                    preview = preview[:400] + '...'
            except Exception:
                preview = '<unprintable>'
            print(f"[OLLAMA][HTTP] finish model={model} duration={dur:.2f}s success=True preview={preview}")
            return out
    except Exception as e:
        dur = time.time() - start if 'start' in locals() else 0.0
        print(f"[OLLAMA][HTTP] error model={model} duration={dur:.2f}s error={e}")
        return None


async def _call_ollama_cli(model: str, prompt: str, timeout: float = 120.0) -> Optional[Dict[str, Any]]:
    """Invoke the Ollama CLI as a fallback. This is a best-effort approach.

    It calls: `ollama generate <model> --prompt '<prompt>' --quiet` and captures stdout.
    """
    try:
        start = time.time()
        print(f"[OLLAMA][CLI] start model={model} timeout={timeout} cmds={OLLAMA_CLI_GENERATE_CMDS}")
        # Try common CLI subcommands in order: generate, run
        last_err = None
        for subcmd in OLLAMA_CLI_GENERATE_CMDS:
            if subcmd == 'run':
                cmd = [OLLAMA_CLI_PATH, 'run', model, prompt, '--format', 'json']
            elif subcmd == 'generate':
                cmd = [OLLAMA_CLI_PATH, 'generate', model, '--prompt', prompt]
            else:
                cmd = [OLLAMA_CLI_PATH, subcmd, model, prompt]

            # Use asyncio subprocess to not block the loop
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError:
                last_err = f"Ollama CLI not found at: {OLLAMA_CLI_PATH}"
                break

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                last_err = 'Timeout waiting for CLI'
                continue
            if proc.returncode == 0:
                text = stdout.decode(errors='ignore')
                # Try parsing as JSON or NDJSON
                try:
                    obj = json.loads(text)
                    # similar parsing as HTTP path
                    if isinstance(obj, dict):
                        if 'result' in obj and isinstance(obj['result'], str):
                            return {'content': obj['result']}
                        if 'generated' in obj and isinstance(obj['generated'], list):
                            texts = []
                            for g in obj['generated']:
                                if isinstance(g, dict):
                                    texts.append(g.get('text') or g.get('output') or '')
                                else:
                                    texts.append(str(g))
                            return {'content': '\n'.join(texts)}
                        if 'response' in obj and isinstance(obj['response'], str):
                            return {'content': obj['response']}
                        if 'data' in obj:
                            return {'content': json.dumps(obj['data'])}
                    # fallback to raw text
                    # Log a truncated preview
                    try:
                        preview = text if len(text) <= 400 else text[:400] + '...'
                    except Exception:
                        preview = '<unprintable>'
                    dur = time.time() - start
                    print(f"[OLLAMA][CLI] finish model={model} duration={dur:.2f}s success=True preview={preview}")
                    return {'content': text}
                except Exception:
                    # Might be ndjson or plain text - split and inspect lines
                    lines = [l for l in text.splitlines() if l.strip()]
                    fragments = []
                    for line in lines:
                        try:
                            obj = json.loads(line)
                            if isinstance(obj, dict):
                                if 'result' in obj and isinstance(obj['result'], str):
                                    fragments.append(obj['result'])
                                    continue
                                if 'generated' in obj and isinstance(obj['generated'], list):
                                    for g in obj['generated']:
                                        if isinstance(g, dict):
                                            fragments.append(g.get('text') or g.get('output') or '')
                                        else:
                                            fragments.append(str(g))
                                    continue
                                if 'response' in obj and isinstance(obj['response'], str):
                                    fragments.append(obj['response'])
                                    continue
                                if 'data' in obj:
                                    try:
                                        fragments.append(json.dumps(obj['data']))
                                    except Exception:
                                        fragments.append(str(obj['data']))
                                    continue
                        except Exception:
                            continue
                    if fragments:
                        combined = ''.join(fragments)
                        return {'content': combined}
                    try:
                        preview = text if len(text) <= 400 else text[:400] + '...'
                    except Exception:
                        preview = '<unprintable>'
                    dur = time.time() - start
                    print(f"[OLLAMA][CLI] finish model={model} duration={dur:.2f}s success=True preview={preview}")
                    return {'content': text}
            else:
                last_err = stderr.decode(errors='ignore')
                # try next subcommand
                continue
        # If we get here, none worked
        dur = time.time() - start
        if last_err:
            print(f"[OLLAMA][CLI] none succeeded model={model} duration={dur:.2f}s last_err={last_err}")
        else:
            print(f"[OLLAMA][CLI] none succeeded model={model} duration={dur:.2f}s")
        return None

    except FileNotFoundError:
        print(f"[OLLAMA][CLI] not found at path: {OLLAMA_CLI_PATH}")
        return None
    except Exception as e:
        print(f"[OLLAMA][CLI] error model={model} error={e}")
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
    # Log the preference used for this call
    print(f"[OLLAMA] query_model: model={model} use_cli={OLLAMA_USE_CLI}")
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

    # Build a prioritized list of candidate names to try for installation.
    async def _build_candidates(name: str) -> List[str]:
        # Try a few heuristic canonicalizations and also consult registry search.
        candidates = []
        seen = set()

        def _add(n: str):
            if not n:
                return
            if n in seen:
                return
            seen.add(n)
            candidates.append(n)

        # If user provided explicit tag (contains ':'), try it first
        if ':' in name:
            _add(name)
            # also try without tag
            base = name.split(':', 1)[0]
            _add(base)
        else:
            _add(name)
            _add(f"{name}:latest")

        # If name looks like 'family-variant' also try 'family:variant' and 'family/variant'
        if '-' in name and ':' not in name:
            family, rest = name.rsplit('-', 1)
            _add(f"{family}:{rest}")
            _add(f"{family}/{rest}")
            _add(f"{family}/{rest}:latest")
            _add(f"{family}:latest")

        # Consult remote registry (best-effort) for additional canonical names
        try:
            regs = await search_registry(name, timeout=5.0)
            for r in regs:
                _add(r)
        except Exception:
            pass

        return candidates

    attempts = await _build_candidates(model)
    attempts_info = []
    # Try each candidate until one succeeds
    for candidate in attempts:
        # First, try HTTP-based pull if the Ollama HTTP API is available
        try:
            base = (await _discover_api_url()).rstrip('/')
            http_candidates = [
                base + '/api/pull',
                base + '/v1/pull',
                base + '/pull',
                base + '/api/models/pull',
            ]
            async with httpx.AsyncClient(timeout=30.0) as client:
                for url in http_candidates:
                    try:
                        resp = await client.post(url, json={'model': candidate})
                        text = resp.text if resp is not None else ''
                        combined_out.append(f"HTTP {url} -> {resp.status_code}\n{text}")
                        if resp is not None and 200 <= resp.status_code < 300:
                            return {'success': True, 'output': '\n'.join(combined_out), 'attempted': candidate}
                    except Exception:
                        # try next HTTP endpoint
                        continue
        except Exception:
            # ignore HTTP discovery errors and fall back to CLI
            pass

        cmd = [OLLAMA_CLI_PATH, 'pull', candidate]
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
                combined_out.append(f"{candidate}: TIMEOUT")
                continue

            out = stdout.decode(errors='ignore') if stdout else ''
            err = stderr.decode(errors='ignore') if stderr else ''
            merged_output = (out or '') + (err or '')
            attempts_info.append({'name': candidate, 'success': proc.returncode == 0, 'output': merged_output, 'returncode': proc.returncode})
            if proc.returncode == 0:
                return {'success': True, 'output': '\n'.join([a.get('output','') for a in attempts_info]), 'attempted': candidate, 'attempts': attempts_info}
            else:
                # continue to next candidate
                continue
        except FileNotFoundError:
            return {'success': False, 'output': f'Ollama CLI not found at path: {OLLAMA_CLI_PATH}'}
        except Exception as e:
            attempts_info.append({'name': candidate, 'success': False, 'output': f'EXCEPTION: {e}', 'returncode': None})
            continue

        # Nothing succeeded
        return {'success': False, 'output': '\n'.join([a.get('output','') for a in attempts_info]), 'attempts': attempts_info}


async def uninstall_model(model: str, timeout: float = 600) -> Dict[str, Any]:
    """Uninstall a model using Ollama CLI (try rm/remove/uninstall)"""
    import asyncio
    try:
        import shutil
        if not shutil.which(OLLAMA_CLI_PATH):
            return {'success': False, 'output': 'Ollama CLI not installed.'}
    except Exception:
        return {'success': False, 'output': 'Ollama CLI check failed.'}

    last_err = None
    for cmd in OLLAMA_CLI_UNINSTALL_CMDS:
        try:
            proc = await asyncio.create_subprocess_exec(
                OLLAMA_CLI_PATH, cmd, model,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                last_err = 'Timeout waiting for CLI'
                continue
            if proc.returncode != 0:
                last_err = stderr.decode(errors='ignore')
                continue
            out = stdout.decode(errors='ignore')
            return {'success': True, 'output': out}
        except FileNotFoundError:
            return {'success': False, 'output': f'Ollama CLI not found at path: {OLLAMA_CLI_PATH}'}
        except Exception as e:
            last_err = str(e)
            continue
    return {'success': False, 'output': last_err or 'Uninstall failed.'}


async def install_model_stream(model: str, timeout: float = 600):
    """Async generator that streams install output lines for `ollama pull <model>`.

    Yields dict events of shape: {'type': 'line', 'line': str} and a final
    {'type': 'complete', 'success': bool, 'output': str}.
    """
    import asyncio
    import re

    _ansi_re = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
    _ctrl_re = re.compile(r"[\x00-\x1F\x7F]+")

    def _clean_line(raw: str) -> str:
        if raw is None:
            return ''
        # Remove common ANSI escape sequences
        s = _ansi_re.sub('', raw)
        # Remove remaining control characters (tabs allowed)
        s = _ctrl_re.sub('', s)
        # Trim whitespace
        s = s.strip()
        # Filter out lines that are just spinner glyphs or very short non-informative
        if not s:
            return ''
        # Some spinner-only lines contain block unicode like ⠋⠙; if line length small and mostly non-ascii, skip
        if len(s) <= 3 and all(ord(ch) >= 0x2500 for ch in s):
            return ''
        return s

    try:
        import shutil
        if not shutil.which(OLLAMA_CLI_PATH):
            yield {'type': 'line', 'line': f'Ollama CLI not found at: {OLLAMA_CLI_PATH}'}
            yield {'type': 'complete', 'success': False, 'output': 'CLI not found'}
            return
    except Exception:
        pass

    # Build candidates to try (reuse logic from install_model)
    async def _build_candidates(name: str) -> List[str]:
        candidates = []
        seen = set()

        def _add(n: str):
            if not n:
                return
            if n in seen:
                return
            seen.add(n)
            candidates.append(n)

        if ':' in name:
            _add(name)
            _add(name.split(':', 1)[0])
        else:
            _add(name)
            _add(f"{name}:latest")

        if '-' in name and ':' not in name:
            family, rest = name.rsplit('-', 1)
            _add(f"{family}:{rest}")
            _add(f"{family}/{rest}")
            _add(f"{family}/{rest}:latest")
            _add(f"{family}:latest")

        try:
            regs = await search_registry(name, timeout=5.0)
            for r in regs:
                _add(r)
        except Exception:
            pass

        return candidates

    attempts = await _build_candidates(model)

    try:
        import shutil
        if not shutil.which(OLLAMA_CLI_PATH):
            yield {'type': 'line', 'line': f'Ollama CLI not found at: {OLLAMA_CLI_PATH}'}
            yield {'type': 'complete', 'success': False, 'output': 'CLI not found'}
            return
    except Exception:
        pass

    # Try candidates sequentially, streaming output for each
    for candidate in attempts:
        yield {'type': 'attempt_start', 'candidate': candidate}
        # First try HTTP-based pull if API available
        try:
            base = (await _discover_api_url()).rstrip('/')
            http_candidates = [
                base + '/api/pull',
                base + '/v1/pull',
                base + '/pull',
                base + '/api/models/pull',
            ]
            async with httpx.AsyncClient(timeout=30.0) as client:
                for url in http_candidates:
                    try:
                        resp = await client.post(url, json={'model': candidate})
                        text = resp.text if resp is not None else ''
                        # stream the HTTP response text lines if present
                        if text:
                            for ln in text.splitlines():
                                cl = _clean_line(ln)
                                if cl:
                                    yield {'type': 'attempt_log', 'candidate': candidate, 'line': cl}
                        # Check if response indicates error
                        has_error = False
                        if text:
                            try:
                                import json
                                lines = [l.strip() for l in text.splitlines() if l.strip()]
                                for line in lines:
                                    obj = json.loads(line)
                                    if isinstance(obj, dict) and 'error' in obj:
                                        has_error = True
                                        break
                            except:
                                pass
                        success = resp is not None and 200 <= resp.status_code < 300 and not has_error
                        yield {'type': 'attempt_complete', 'candidate': candidate, 'success': success, 'output': text, 'returncode': resp.status_code}
                        if success:
                            yield {'type': 'complete', 'success': True, 'output': text, 'attempted': candidate}
                            return
                    except Exception:
                        continue
        except Exception:
            pass

        cmd = [OLLAMA_CLI_PATH, 'pull', candidate]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout = proc.stdout
            stderr = proc.stderr

            # Continuously read until process exits
            while True:
                read = False
                if stdout is not None:
                    try:
                        line = await asyncio.wait_for(stdout.readline(), timeout=0.1)
                        if line:
                            read = True
                            raw = line.decode(errors='ignore')
                            cleaned = _clean_line(raw)
                            if cleaned:
                                yield {'type': 'attempt_log', 'candidate': candidate, 'line': cleaned}
                    except asyncio.TimeoutError:
                        pass
                if stderr is not None:
                    try:
                        line2 = await asyncio.wait_for(stderr.readline(), timeout=0.1)
                        if line2:
                            read = True
                            raw2 = line2.decode(errors='ignore')
                            cleaned2 = _clean_line(raw2)
                            if cleaned2:
                                yield {'type': 'attempt_log', 'candidate': candidate, 'line': cleaned2}
                    except asyncio.TimeoutError:
                        pass
                if not read:
                    if proc.returncode is None:
                        await asyncio.sleep(0.05)
                        continue
                    break

            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                out, err = b'', b''

            success = (proc.returncode == 0)
            output = (out.decode(errors='ignore') if out else '') + (err.decode(errors='ignore') if err else '')
            yield {'type': 'attempt_complete', 'candidate': candidate, 'success': success, 'output': output, 'returncode': (proc.returncode if proc.returncode is not None else -1)}
            if success:
                yield {'type': 'complete', 'success': True, 'output': output, 'attempted': candidate}
                return
            else:
                # continue to next candidate
                continue

        except FileNotFoundError:
            yield {'type': 'attempt_complete', 'candidate': candidate, 'success': False, 'output': 'CLI not found', 'returncode': None}
            yield {'type': 'complete', 'success': False, 'output': 'CLI not found'}
            return
        except Exception as e:
            yield {'type': 'attempt_complete', 'candidate': candidate, 'success': False, 'output': str(e), 'returncode': None}
            # try next candidate
            continue

    # If we get here, none succeeded
    yield {'type': 'complete', 'success': False, 'output': 'All attempts failed', 'attempts': attempts}


async def uninstall_model_stream(model: str, timeout: float = 600):
    """Streamed uninstall `ollama rm <model>` output like install_model_stream"""
    import asyncio
    import re

    _ansi_re = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
    _ctrl_re = re.compile(r"[\x00-\x1F\x7F]+")

    def _clean_line(raw: str) -> str:
        if raw is None:
            return ''
        s = _ansi_re.sub('', raw)
        s = _ctrl_re.sub('', s)
        s = s.strip()
        if not s:
            return ''
        return s

    try:
        proc = await asyncio.create_subprocess_exec(
            OLLAMA_CLI_PATH, 'rm', model,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout = proc.stdout
        stderr = proc.stderr

        while True:
            read = False
            if stdout is not None:
                try:
                    line = await asyncio.wait_for(stdout.readline(), timeout=0.1)
                    if line:
                        read = True
                        cleaned = _clean_line(line.decode(errors='ignore'))
                        if cleaned:
                            yield {'type': 'line', 'line': cleaned}
                except asyncio.TimeoutError:
                    pass
            if stderr is not None:
                try:
                    line2 = await asyncio.wait_for(stderr.readline(), timeout=0.1)
                    if line2:
                        read = True
                        cleaned2 = _clean_line(line2.decode(errors='ignore'))
                        if cleaned2:
                            yield {'type': 'line', 'line': cleaned2}
                except asyncio.TimeoutError:
                    pass
            if not read:
                if proc.returncode is None:
                    await asyncio.sleep(0.05)
                    # poll
                    await asyncio.sleep(0.05)
                    if proc.returncode is None:
                        continue
                break

        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            out, err = b'', b''

        success = proc.returncode == 0
        output = (out.decode(errors='ignore') if out else '') + (err.decode(errors='ignore') if err else '')
        yield {'type': 'complete', 'success': success, 'output': output}
    except FileNotFoundError:
        yield {'type': 'line', 'line': f'Ollama CLI not found at path: {OLLAMA_CLI_PATH}'}
        yield {'type': 'complete', 'success': False, 'output': 'CLI not found'}
    except Exception as e:
        yield {'type': 'line', 'line': str(e)}
        yield {'type': 'complete', 'success': False, 'output': str(e)}


async def list_models(timeout: float = 10.0) -> List[str]:
    """Return a list of available model names from local Ollama.

    Tries the HTTP API first, then falls back to the CLI.
    """
    # Try HTTP API endpoints that Ollama may expose
    base = (await _discover_api_url()).rstrip('/')
    candidates = [
        base + '/api/models',
        base + '/models',
        base + '/v1/models',
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


async def search_registry(query: str, timeout: float = 10.0) -> List[str]:
    """Search remote registry for model names matching `query` using the Ollama CLI.

    Returns a list of candidate model identifiers (best-effort).
    """
    try:
        # If CLI exists, try 'ollama search' or 'ollama ls-remote' depending on version
        proc = await asyncio.create_subprocess_exec(
            OLLAMA_CLI_PATH, 'search', query,
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
            # If 'search' is not supported, try 'ls-remote' if available
            proc2 = await asyncio.create_subprocess_exec(
                OLLAMA_CLI_PATH, 'ls-remote', query,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout2, stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc2.kill()
                await proc2.wait()
                return []
            out = stdout2.decode(errors='ignore')
        else:
            out = stdout.decode(errors='ignore')

        names = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            # output may be 'NAME (ID) SIZE' or 'model-name:tag'
            parts = line.split()
            # prefer tokens that contain ':' or '-'
            candidate = parts[0]
            names.append(candidate)
        # dedupe
        seen = []
        res = []
        for n in names:
            if n not in seen:
                seen.append(n)
                res.append(n)
        return res
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"Error performing registry search: {e}")
        return []
