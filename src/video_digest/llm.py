"""The single entry point for LLM calls: llm().

Works with any OpenAI-compatible API (OpenAI, OpenRouter, local servers):
- response format: json_schema -> json_object -> plain text with the schema in the prompt;
- parameters the provider rejects (temperature, reasoning, max_tokens, provider routing) are dropped;
- max_tokens = LLM_MAX_OUTPUT_TOKENS; a truncated answer (finish_reason "length") is retried once
  with the limit doubled (cap 65,536), then LLMTruncatedError;
- pydantic validation errors are fed back to the model (up to 2 retries);
- 429/5xx/connection errors are retried with exponential backoff (1, 2, 4, 8 s; 5 attempts);
- 401/402/403 are raised unchanged; is_fatal() tells callers to stop the whole run.
Never logs or prints the API key.
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import openai
from pydantic import BaseModel, ValidationError

from video_digest import config

Mode = Literal["json_schema", "json_object", "text"]
Message = dict[str, str]

BACKOFF_SECONDS = (1, 2, 4, 8)  # sleeps between the 5 attempts
VALIDATION_RETRIES = 2
REJECTED_STATUS = {400, 404, 422}  # provider refused the request shape
FATAL_STATUS = {401, 402, 403}  # bad key, no balance, no model access: every further call fails too
MAX_OUTPUT_TOKENS_CAP = 65_536
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")

_sleep = time.sleep  # patched in tests


class LLMError(Exception):
    pass


class LLMValidationError(LLMError):
    pass


class LLMTruncatedError(LLMError):
    pass


@dataclass
class LLMResult[T: BaseModel]:
    content: str
    parsed: T | None
    model: str
    mode: Mode
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    attempts: list[str] = field(default_factory=list[str])  # request shapes tried, for debugging


@dataclass
class _Shape:
    """What the next request sends; fields are switched off as the provider rejects them."""

    stages: list[tuple[Mode, bool]]  # (response format, require_parameters)
    stage: int = 0
    temperature: bool = True
    reasoning: bool = True
    max_tokens: bool = True

    @property
    def mode(self) -> Mode:
        return self.stages[self.stage][0]

    @property
    def require_parameters(self) -> bool:
        return self.stages[self.stage][1]

    def describe(self) -> str:
        return (
            f"{self.mode} require={self.require_parameters} temp={self.temperature} "
            f"reasoning={self.reasoning} max_tokens={self.max_tokens}"
        )

    def degrade(self, error_text: str, temperature_sent: bool, reasoning_sent: bool, max_tokens_sent: bool) -> bool:
        """Drop the parameter the error points at, else step down the format chain."""
        text = error_text.lower()
        if temperature_sent and "temperature" in text:
            self.temperature = False
        elif reasoning_sent and "reasoning" in text:
            self.reasoning = False
        elif max_tokens_sent and "max_tokens" in text:
            self.max_tokens = False
        elif self.stage + 1 < len(self.stages):
            self.stage += 1
        elif temperature_sent:
            self.temperature = False
        elif reasoning_sent:
            self.reasoning = False
        elif max_tokens_sent:
            self.max_tokens = False
        else:
            return False
        return True


def get_client() -> openai.OpenAI:
    # max_retries=0: retries and backoff are handled in llm()
    return openai.OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY or "missing", max_retries=0)


def default_reasoning_effort(model: str) -> str:
    if model and model == config.LLM_MODEL_SMART:
        return config.LLM_REASONING_EFFORT_SMART
    return config.LLM_REASONING_EFFORT_FAST


def schema_instruction(schema: dict[str, Any]) -> str:
    return (
        "Respond with a single JSON object and nothing else (no prose, no code fences). "
        "It must validate against this JSON Schema:\n" + json.dumps(schema, ensure_ascii=False)
    )


def extract_json(content: str) -> Any:
    text = FENCE_RE.sub("", content.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _status(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def is_fatal(exc: BaseException) -> bool:
    """401/402/403: retrying or moving on to the next video cannot succeed."""
    return _status(exc) in FATAL_STATUS


def _is_transient(exc: BaseException) -> bool:
    status = _status(exc)
    return isinstance(exc, openai.APIConnectionError) or status == 429 or (status is not None and status >= 500)


def _error_text(exc: BaseException) -> str:
    body = getattr(exc, "body", None)
    return f"{exc} {json.dumps(body, default=str) if body else ''}"


def _usage(response: Any) -> tuple[int, int, float | None]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0, None
    cost = getattr(usage, "cost", None)  # OpenRouter extension, in credits (= USD)
    return (
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
        float(cost) if isinstance(cost, int | float) else None,
    )


def _with_schema_prompt(messages: list[Message], schema: dict[str, Any]) -> list[Message]:
    instruction = schema_instruction(schema)
    if messages and messages[0]["role"] == "system":
        first = {"role": "system", "content": messages[0]["content"] + "\n\n" + instruction}
        return [first, *messages[1:]]
    return [{"role": "system", "content": instruction}, *messages]


def _request(
    shape: _Shape,
    messages: list[Message],
    model: str,
    schema_name: str,
    schema: dict[str, Any] | None,
    temperature: float | None,
    reasoning_effort: str,
    max_tokens: int | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"model": model}
    extra_body: dict[str, Any] = {}
    if schema is not None and shape.mode != "json_schema":
        messages = _with_schema_prompt(messages, schema)
    kwargs["messages"] = messages
    if schema is not None and shape.mode == "json_schema":
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": schema, "strict": True},
        }
    elif schema is not None and shape.mode == "json_object":
        kwargs["response_format"] = {"type": "json_object"}
    if shape.require_parameters:
        extra_body["provider"] = {"require_parameters": True}
    if shape.temperature and temperature is not None:
        kwargs["temperature"] = temperature
    if shape.max_tokens and max_tokens:
        kwargs["max_tokens"] = max_tokens
    if shape.reasoning and reasoning_effort:
        extra_body["reasoning"] = {"effort": reasoning_effort}
    if extra_body:
        kwargs["extra_body"] = extra_body
    return kwargs


def llm[T: BaseModel](
    messages: list[Message],
    model: str,
    response_schema: type[T] | None = None,
    temperature: float | None = 0,
    *,
    reasoning_effort: str | None = None,
    max_tokens: int | None = None,
    validation_context: dict[str, Any] | None = None,
    client: openai.OpenAI | Any = None,  # a real client, or a fake with .chat.completions.create
) -> LLMResult[T]:
    """Call the chat API; with response_schema, return a validated pydantic object in .parsed.

    reasoning_effort defaults to the FAST/SMART setting matching `model`; "" disables it.
    max_tokens defaults to LLM_MAX_OUTPUT_TOKENS (reasoning tokens count against it); 0 disables it.
    validation_context is passed to model_validate (e.g. allowed timestamps).
    """
    if not model:
        raise LLMError("no model configured (set LLM_MODEL_FAST / LLM_MODEL_SMART in .env)")
    client = client or get_client()
    effort = default_reasoning_effort(model) if reasoning_effort is None else reasoning_effort
    limit = config.LLM_MAX_OUTPUT_TOKENS if max_tokens is None else max_tokens
    truncation_retried = False
    schema = response_schema.model_json_schema() if response_schema else None
    stages: list[tuple[Mode, bool]] = (
        [("json_schema", True), ("json_schema", False), ("json_object", False), ("text", False)]
        if schema
        else [("text", False)]
    )
    shape = _Shape(stages=stages)
    result: LLMResult[T] = LLMResult(content="", parsed=None, model=model, mode=shape.mode)
    conversation = list(messages)

    validation_attempt = 0
    while True:
        response = _call(client, shape, conversation, model, response_schema, schema, temperature, effort, limit, result)
        tokens_in, tokens_out, cost = _usage(response)
        result.tokens_in += tokens_in
        result.tokens_out += tokens_out
        if cost is not None:
            result.cost_usd = (result.cost_usd or 0.0) + cost
        if getattr(response.choices[0], "finish_reason", None) == "length":
            # a cut-off answer is never parsed; one retry with a doubled limit, if a limit is in effect
            if truncation_retried or not limit or not shape.max_tokens:
                raise LLMTruncatedError(
                    f"answer truncated at the output limit (max_tokens={limit if shape.max_tokens else 'not sent'}, "
                    f"tokens_out={tokens_out}); raise LLM_MAX_OUTPUT_TOKENS or lower the reasoning effort"
                )
            truncation_retried = True
            limit = min(limit * 2, MAX_OUTPUT_TOKENS_CAP)
            continue
        content = response.choices[0].message.content or ""
        result.content, result.mode = content, shape.mode
        if response_schema is None:
            return result
        try:
            result.parsed = response_schema.model_validate(extract_json(content), context=validation_context)
            return result
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            if validation_attempt == VALIDATION_RETRIES:
                raise LLMValidationError(f"invalid output after {VALIDATION_RETRIES} retries: {exc}") from exc
            validation_attempt += 1
            conversation = [
                *conversation,
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": f"Your response failed validation:\n{exc}\n"
                    "Return the complete corrected JSON object only.",
                },
            ]


def _call(
    client: Any,
    shape: _Shape,
    messages: list[Message],
    model: str,
    response_schema: type[BaseModel] | None,
    schema: dict[str, Any] | None,
    temperature: float | None,
    effort: str,
    max_tokens: int | None,
    result: LLMResult[Any],
) -> Any:
    """One logical call: degrades the request shape on rejections, backs off on transient errors."""
    schema_name = response_schema.__name__ if response_schema else "response"
    transient_failures = 0
    while True:
        kwargs = _request(shape, messages, model, schema_name, schema, temperature, effort, max_tokens)
        result.attempts.append(shape.describe())
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as exc:
            if _is_transient(exc):
                if transient_failures >= len(BACKOFF_SECONDS):
                    raise LLMError(f"giving up after {transient_failures + 1} attempts: {exc}") from exc
                _sleep(BACKOFF_SECONDS[transient_failures])
                transient_failures += 1
                continue
            if _status(exc) in REJECTED_STATUS and shape.degrade(
                _error_text(exc),
                "temperature" in kwargs,
                "reasoning" in kwargs.get("extra_body", {}),
                "max_tokens" in kwargs,
            ):
                continue
            raise
