"""Checks for the provider shim.

Two providers, one call site. The bugs that matter here are the silent ones:
a cache breakpoint that stops being attached, or a role name that means
something different on the other side.

Run: .venv/bin/python test_llm.py     (no API calls, no framework)
"""
import os
from types import SimpleNamespace

from pydantic import BaseModel

import llm


class Answer(BaseModel):
    answer: str


TURNS = [
    {"role": "user", "text": "first"},
    {"role": "assistant", "text": "reply"},
    {"role": "user", "text": "second"},
]


class FakeAnthropic:
    def __init__(self, cache_reads=0):
        self.kwargs = None
        self.messages = SimpleNamespace(parse=self._parse)
        self._cache_reads = cache_reads

    def _parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            parsed_output=Answer(answer="ok"),
            usage=SimpleNamespace(cache_read_input_tokens=self._cache_reads),
        )


class FakeGemini:
    def __init__(self, cached=0, parsed=Answer(answer="ok")):
        self.call = None
        self.models = SimpleNamespace(generate_content=self._generate)
        self._cached, self._parsed = cached, parsed

    def _generate(self, model, contents, config):
        self.call = SimpleNamespace(model=model, contents=contents, config=config)
        return SimpleNamespace(
            parsed=self._parsed,
            usage_metadata=SimpleNamespace(cached_content_token_count=self._cached),
        )


# --- anthropic rendering -------------------------------------------------

def test_anthropic_attaches_the_cache_breakpoint():
    # the money test: no breakpoint means every call pays full input price
    client = FakeAnthropic()
    llm._anthropic(client, "m", "SYSTEM", TURNS, Answer, 100)
    system_block, = client.kwargs["system"]
    assert system_block["cache_control"] == {"type": "ephemeral"}
    assert system_block["text"] == "SYSTEM"


def test_anthropic_keeps_turn_roles_and_order():
    client = FakeAnthropic()
    llm._anthropic(client, "m", "S", TURNS, Answer, 100)
    msgs = client.kwargs["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert [m["content"] for m in msgs] == ["first", "reply", "second"]


def test_anthropic_reports_cache_reads():
    assert llm._anthropic(FakeAnthropic(1234), "m", "S", TURNS, Answer,
                          100).cache_read_tokens == 1234


# --- gemini rendering ----------------------------------------------------

def test_gemini_renames_assistant_to_model():
    # Gemini rejects role "assistant"; the retry path in ingest sends one
    client = FakeGemini()
    llm._gemini(client, "m", "S", TURNS, Answer, 100)
    assert [c.role for c in client.call.contents] == ["user", "model", "user"]


def test_gemini_puts_system_in_config_not_contents():
    client = FakeGemini()
    llm._gemini(client, "m", "SYSTEM", TURNS, Answer, 100)
    assert client.call.config.system_instruction == "SYSTEM"
    for content in client.call.contents:
        assert "SYSTEM" not in content.parts[0].text


def test_gemini_requests_the_schema():
    client = FakeGemini()
    llm._gemini(client, "m", "S", TURNS, Answer, 100)
    assert client.call.config.response_schema is Answer
    assert client.call.config.response_mime_type == "application/json"


# --- shared contract -----------------------------------------------------

def test_both_providers_return_the_same_shape():
    a = llm._anthropic(FakeAnthropic(), "m", "S", TURNS, Answer, 100)
    g = llm._gemini(FakeGemini(), "m", "S", TURNS, Answer, 100)
    assert a.parsed.answer == g.parsed.answer
    assert {*vars(a)} == {*vars(g)}


def test_unparseable_response_raises():
    # Gemini returns parsed=None when the output misses the schema. Passing
    # that on would surface as an AttributeError three frames away instead.
    original, llm._gemini = llm._gemini, (
        lambda *a: llm.Result(parsed=None, cache_read_tokens=0,
                              provider="gemini", model="m")
    )
    llm._client.cache_clear()
    original_client, llm._client = llm._client, lambda name: object()
    try:
        llm.parse("S", TURNS, Answer, name="gemini", model="m")
    except RuntimeError as e:
        assert "schema" in str(e)
    else:
        raise AssertionError("None was passed through as a result")
    finally:
        llm._gemini, llm._client = original, original_client


def test_unknown_provider_names_the_valid_ones():
    try:
        llm._client("openai")
    except ValueError as e:
        assert "anthropic" in str(e) and "gemini" in str(e)
    else:
        raise AssertionError("unknown provider accepted")


def test_env_file_does_not_override_real_environment():
    os.environ["LLM_TEST_KEY"] = "from-shell"
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        f = Path(d, ".env")
        f.write_text("LLM_TEST_KEY=from-file\n# comment\n\n")
        llm.load_env(f)
    assert os.environ["LLM_TEST_KEY"] == "from-shell"
    del os.environ["LLM_TEST_KEY"]


def test_provider_defaults_to_gemini():
    saved = os.environ.pop("LLM_PROVIDER", None)
    try:
        assert llm.provider() == "gemini", "free tier should be the default"
    finally:
        if saved is not None:
            os.environ["LLM_PROVIDER"] = saved


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nall checks passed")
