"""One structured-output call, two providers.

    LLM_PROVIDER=gemini      free tier, for iteration
    LLM_PROVIDER=anthropic   for the final ingest and the recorded demo
    LLM_MODEL=...            override the per-provider default

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
}

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
    return os.environ.get("LLM_PROVIDER", "gemini").lower()


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
    raise ValueError(f"unknown LLM_PROVIDER {name!r} (want anthropic or gemini)")


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
        ),
    )
    usage = response.usage_metadata
    return Result(
        parsed=response.parsed,
        cache_read_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
        provider="gemini",
        model=model,
    )


def parse(system, turns, schema, model=None, name=None, max_tokens=16000):
    """Send one request, get back a validated `schema` instance.

    system: plain string, must be identical across calls to stay cacheable
    turns:  [{"role": "user"|"assistant", "text": str}, ...]
    schema: a pydantic BaseModel subclass
    """
    name = (name or provider()).lower()
    model = model or model_for(name)
    client = _client(name)
    impl = {"anthropic": _anthropic, "gemini": _gemini}[name]
    result = impl(client, model, system, turns, schema, max_tokens)
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
