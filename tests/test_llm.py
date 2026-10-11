"""llm() with a fake OpenAI client: fallback chain, retries, validation feedback. No network."""

import json
from types import SimpleNamespace
from typing import Any

import httpx2
import openai
import pytest

from video_digest import llm as llm_module
from video_digest.llm import LLMError, LLMValidationError, extract_json, llm
from video_digest.schema import VideoAnalysis

MODEL = "anthropic/claude-haiku-5.5"


def response(content: str, tokens_in: int = 100, tokens_out: int = 20, cost: float | None = None) -> Any:
    usage = SimpleNamespace(prompt_tokens=tokens_in, completion_tokens=tokens_out)
    if cost is not None:
        usage.cost = cost
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage)


def api_error(status: int, message: str) -> openai.APIStatusError:
    request = httpx2.Request("POST", "https://llm.test/v1/chat/completions")
    classes: dict[int, type[openai.APIStatusError]] = {
        400: openai.BadRequestError,
        404: openai.NotFoundError,
        429: openai.RateLimitError,
        500: openai.InternalServerError,
        503: openai.InternalServerError,
    }
    return classes[status](message, response=httpx2.Response(status, request=request), body={"message": message})


class FakeClient:
    """Plays back scripted responses/exceptions and records every request."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr(llm_module, "_sleep", sleeps.append)
    return sleeps


MESSAGES = [{"role": "system", "content": "You analyze videos."}, {"role": "user", "content": "Transcript..."}]


def call(client: FakeClient, **kwargs: Any) -> Any:
    return llm(MESSAGES, MODEL, VideoAnalysis, client=client, reasoning_effort="low", **kwargs)


def test_structured_output_first_try(analysis_data: dict[str, Any]) -> None:
    client = FakeClient([response(json.dumps(analysis_data), 1000, 200, cost=0.0012)])
    result = call(client)
    assert result.parsed is not None and result.parsed.verdict == "summary_enough"
    assert (result.tokens_in, result.tokens_out, result.cost_usd, result.mode) == (1000, 200, 0.0012, "json_schema")
    req = client.requests[0]
    assert req["response_format"]["type"] == "json_schema"
    assert req["response_format"]["json_schema"]["strict"] is True
    assert req["temperature"] == 0
    assert req["extra_body"] == {"provider": {"require_parameters": True}, "reasoning": {"effort": "low"}}
    assert req["messages"] == MESSAGES  # schema not pasted into the prompt in json_schema mode


def test_fallback_chain_to_plain_text(analysis_data: dict[str, Any]) -> None:
    client = FakeClient(
        [
            api_error(404, "No endpoints found that can handle the requested parameters."),
            api_error(400, "response_format json_schema is not supported"),
            api_error(400, "response_format json_object is not supported"),
            response("```json\n" + json.dumps(analysis_data) + "\n```"),
        ]
    )
    result = call(client)
    assert result.parsed is not None
    assert result.mode == "text"
    shapes = [(r.get("response_format", {}).get("type"), "provider" in r.get("extra_body", {})) for r in client.requests]
    assert shapes == [("json_schema", True), ("json_schema", False), ("json_object", False), (None, False)]
    # json_object and text modes carry the schema in the system prompt
    assert "JSON Schema" not in client.requests[1]["messages"][0]["content"]
    assert "JSON Schema" in client.requests[2]["messages"][0]["content"]
    assert '"verdict"' in client.requests[3]["messages"][0]["content"]


def test_rejected_temperature_is_dropped(analysis_data: dict[str, Any]) -> None:
    client = FakeClient(
        [api_error(400, "Unsupported parameter: 'temperature' is not supported with this model."), response(json.dumps(analysis_data))]
    )
    result = call(client)
    assert result.parsed is not None
    assert "temperature" in client.requests[0] and "temperature" not in client.requests[1]
    assert client.requests[1]["response_format"]["type"] == "json_schema"  # format kept


def test_rejected_reasoning_is_dropped(analysis_data: dict[str, Any]) -> None:
    client = FakeClient([api_error(400, "Unrecognized request argument supplied: reasoning"), response(json.dumps(analysis_data))])
    call(client)
    assert "reasoning" in client.requests[0]["extra_body"]
    assert "reasoning" not in client.requests[1]["extra_body"]
    assert client.requests[1]["temperature"] == 0


def test_no_reasoning_when_effort_empty(analysis_data: dict[str, Any]) -> None:
    client = FakeClient([response(json.dumps(analysis_data))])
    llm(MESSAGES, MODEL, VideoAnalysis, client=client, reasoning_effort="")
    assert "reasoning" not in client.requests[0]["extra_body"]


def test_unfixable_rejection_raises() -> None:
    client = FakeClient([api_error(400, "bad request")] * 6)
    with pytest.raises(openai.BadRequestError):
        llm(MESSAGES, MODEL, client=client, reasoning_effort="")  # text mode, then temperature dropped, then give up
    assert len(client.requests) == 2


def test_validation_error_is_fed_back(analysis_data: dict[str, Any]) -> None:
    bad = dict(analysis_data, verdict="maybe")
    client = FakeClient([response(json.dumps(bad), 100, 10), response("not json at all", 100, 10), response(json.dumps(analysis_data), 100, 10)])
    result = call(client)
    assert result.parsed is not None
    assert result.tokens_in == 300 and result.tokens_out == 30  # tokens summed over retries
    retry_messages = client.requests[1]["messages"]
    assert retry_messages[-2] == {"role": "assistant", "content": json.dumps(bad)}
    assert "failed validation" in retry_messages[-1]["content"] and "verdict" in retry_messages[-1]["content"]


def test_validation_gives_up_after_two_retries(analysis_data: dict[str, Any]) -> None:
    bad = json.dumps(dict(analysis_data, verdict="maybe"))
    client = FakeClient([response(bad)] * 3)
    with pytest.raises(LLMValidationError):
        call(client)
    assert len(client.requests) == 3


def test_validation_context_is_used(analysis_data: dict[str, Any]) -> None:
    client = FakeClient([response(json.dumps(analysis_data))] * 3)
    with pytest.raises(LLMValidationError, match="not chunk heading starts"):
        call(client, validation_context={"allowed_timestamps": {0}})


def test_backoff_on_429_and_5xx(analysis_data: dict[str, Any], no_sleep: list[float]) -> None:
    client = FakeClient([api_error(429, "rate limited"), api_error(503, "overloaded"), response(json.dumps(analysis_data))])
    result = call(client)
    assert result.parsed is not None
    assert no_sleep == [1, 2]
    assert client.requests[0] == client.requests[2]  # same request shape retried


def test_connection_error_is_retried(analysis_data: dict[str, Any], no_sleep: list[float]) -> None:
    error = openai.APIConnectionError(request=httpx2.Request("POST", "https://llm.test"))
    client = FakeClient([error, response(json.dumps(analysis_data))])
    call(client)
    assert no_sleep == [1]


def test_backoff_gives_up_after_five_attempts(no_sleep: list[float]) -> None:
    client = FakeClient([api_error(500, "boom")] * 5)
    with pytest.raises(LLMError, match="5 attempts"):
        call(client)
    assert no_sleep == [1, 2, 4, 8]
    assert len(client.requests) == 5


def test_plain_text_call_without_schema() -> None:
    client = FakeClient([response("Привет")])
    result = llm(MESSAGES, MODEL, client=client, temperature=None, reasoning_effort="")
    assert (result.content, result.parsed) == ("Привет", None)
    req = client.requests[0]
    assert "response_format" not in req and "temperature" not in req and "extra_body" not in req


def test_missing_model_raises() -> None:
    with pytest.raises(LLMError, match="no model"):
        llm(MESSAGES, "", client=FakeClient([]))


def test_api_key_is_never_in_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_module.config, "LLM_API_KEY", "sk-secret-123")
    client = FakeClient([api_error(500, "boom")] * 5)
    with pytest.raises(LLMError) as info:
        call(client)
    assert "sk-secret-123" not in str(info.value)
    assert all("sk-secret-123" not in json.dumps(r, default=str) for r in client.requests)


def test_extract_json() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here it is: {"a": {"b": 2}} Hope it helps.') == {"a": {"b": 2}}
    with pytest.raises(json.JSONDecodeError):
        extract_json("no json")
