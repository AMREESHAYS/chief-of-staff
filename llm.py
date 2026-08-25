"""One structured-output call, two providers.

    LLM_PROVIDER=ollama      local, unmetered — the default for iteration
    LLM_PROVIDER=gemini      the shipped provider, for real verification runs
    LLM_PROVIDER=anthropic   fallback only
    LLM_MODEL=...            override the per-provider default

Ollama exists here so that developing against the pipeline costs nothing and
hits no daily quota. It is NOT the shipped provider: a 7B local model will
disagree with Gemini on borderline judgement calls, so treat an Ollama run as
proof the plumbing works, never as evidence about classification quality.

Callers pass a plain system string and a list of turns. Provider-specific
shapes — Anthropic's content blocks and cache_control, Gemini's Content/Part
and response_schema — live here and nowhere else.

Caching differs and cannot be papered over: Anthropic needs an explicit
cache_control breakpoint, Gemini caches implicitly. Both report what they
actually reused via Result.cache_read_tokens, so the caller can tell whether
its prefix is stable instead of assuming.
"""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "gemini": "gemini-3.6-flash",
    "ollama": "qwen2.5:7b",
}

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

ENV_FILE = Path(__file__).parent / ".env"


def load_env(path=ENV_FILE):
    """Read KEY=value lines into the environment. Real environment variables
    win, so a shell export can override the file."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@dataclass
class Result:
    parsed: object          # validated instance of the schema
    cache_read_tokens: int  # 0 across a whole run means the prefix is churning
    provider: str
    model: str


def provider():
    # local by default: no key, no quota, no bill
    return os.environ.get("LLM_PROVIDER", "ollama").lower()


def model_for(name):
    return os.environ.get("LLM_MODEL") or DEFAULT_MODELS[name]


@lru_cache(maxsize=2)
def _client(name):
    load_env()
    if name == "anthropic":
        import anthropic

        return anthropic.Anthropic()
    if name == "gemini":
        from google import genai

        return genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    if name == "ollama":
        return OLLAMA_HOST
    raise ValueError(
        f"unknown LLM_PROVIDER {name!r} (want ollama, gemini or anthropic)"
    )


def _anthropic(client, model, system, turns, schema, max_tokens):
    response = client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        # the breakpoint. Everything above it must be byte-stable across calls.
        system=[{"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}}],
        thinking={"type": "adaptive"},
        messages=[{"role": t["role"], "content": t["text"]} for t in turns],
        output_format=schema,
    )
    return Result(
        parsed=response.parsed_output,
        cache_read_tokens=response.usage.cache_read_input_tokens or 0,
        provider="anthropic",
        model=model,
    )


def _gemini(client, model, system, turns, schema, max_tokens):
    from google.genai import types

    response = client.models.generate_content(
        model=model,
        contents=[
            types.Content(
                # Gemini calls the assistant "model"
                role="model" if t["role"] == "assistant" else "user",
                parts=[types.Part(text=t["text"])],
            )
            for t in turns
        ],
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            max_output_tokens=max_tokens,
            # a verdict about someone's contract should not change between two
            # runs of the same thread
            temperature=0,
        ),
    )
    usage = response.usage_metadata
    return Result(
        parsed=response.parsed,
        cache_read_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
        provider="gemini",
        model=model,
    )


def _ollama(host, model, system, turns, schema, max_tokens):
    """Local models via /api/chat. urllib rather than a client library — it is
    one POST and the project does not need another dependency for it."""
    import json
    import urllib.error
    import urllib.request

    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}]
        + [{"role": t["role"], "content": t["text"]} for t in turns],
        "format": schema.model_json_schema(),
        "stream": False,
        "options": {"num_predict": max_tokens, "temperature": 0},
    }
    request = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            body = json.load(response)
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"ollama at {host} unreachable ({e}) — is `ollama serve` running?"
        ) from e

    content = body["message"]["content"]
    try:
        parsed = schema.model_validate_json(content)
    except Exception as e:
        # small models drift off-schema; say so plainly rather than crash deeper
        raise RuntimeError(
            f"ollama/{model} returned output that does not match {schema.__name__}"
            f": {content[:200]}"
        ) from e
    return Result(parsed=parsed, cache_read_tokens=0, provider="ollama",
                  model=model)


# a provider being briefly busy is not a reason to abandon a run half way
# through a thread. These are the transient shapes worth waiting out.
TRANSIENT = ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded",
             "high demand", "Timeout", "Connection")


def _is_transient(error):
    text = f"{type(error).__name__}: {error}"
    return any(marker in text for marker in TRANSIENT)


def parse(system, turns, schema, model=None, name=None, max_tokens=16000,
          attempts=4):
    """Send one request, get back a validated `schema` instance.

    system: plain string, must be identical across calls to stay cacheable
    turns:  [{"role": "user"|"assistant", "text": str}, ...]
    schema: a pydantic BaseModel subclass
    """
    import time

    name = (name or provider()).lower()
    model = model or model_for(name)
    client = _client(name)
    impl = {"anthropic": _anthropic, "gemini": _gemini, "ollama": _ollama}[name]

    for attempt in range(attempts):
        try:
            result = impl(client, model, system, turns, schema, max_tokens)
            break
        except Exception as e:
            if attempt == attempts - 1 or not _is_transient(e):
                raise
            wait = 2 ** attempt * 5          # 5s, 10s, 20s
            print(f"    {name} busy ({type(e).__name__}), retrying in {wait}s",
                  flush=True)
            time.sleep(wait)
    if result.parsed is None:
        raise RuntimeError(
            f"{name}/{model} returned no parseable output — the response did "
            "not match the schema"
        )
    return result


if __name__ == "__main__":
    from pydantic import BaseModel

    class Ping(BaseModel):
        answer: str

    r = parse("Answer in one word.", [{"role": "user", "text": "2+2?"}], Ping)
    print(f"{r.provider}/{r.model} -> {r.parsed.answer!r}")
