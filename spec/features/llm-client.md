---
status: done
stage: 2
code: [src/video_digest/llm.py]
---
# LLM client

## Goal
One function, `llm()`, for every LLM call, working with any OpenAI-compatible provider.

## How it works
`llm(messages, model, response_schema=None, temperature=0, *, reasoning_effort=None,
max_tokens=None, validation_context=None, client=None) -> LLMResult`
1. Client: `openai.OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, max_retries=0)`.
2. With a schema the request shape steps down on rejection (HTTP 400/404/422):
   strict `json_schema` + `extra_body.provider.require_parameters` -> `json_schema` -> `json_object`
   -> plain text. Outside json_schema mode the JSON Schema is appended to the system message.
3. An error that names `temperature`, `reasoning` or `max_tokens` drops that parameter first.
   Without such a hint, after the last format stage: temperature, then reasoning, then max_tokens.
4. Reasoning: `extra_body.reasoning.effort`; default from `LLM_REASONING_EFFORT_SMART` when
   `model == LLM_MODEL_SMART`, else `LLM_REASONING_EFFORT_FAST`; `""` = not sent.
5. Output limit: `max_tokens` = argument, else `LLM_MAX_OUTPUT_TOKENS` (default 14000; `0` = not
   sent). Reasoning tokens count as output, so the limit covers reasoning + answer (decision D15).
6. `finish_reason == "length"` (truncated): the partial answer is never parsed or fed back; the same
   request is sent once more with the limit doubled (cap 65,536). Truncated again, or no limit in
   effect (0 or dropped) -> `LLMTruncatedError`. Tokens of the truncated answer are counted.
7. 429, 5xx and connection errors: sleep 1, 2, 4, 8 s; give up after 5 attempts (`LLMError`).
8. 401, 402, 403 are raised unchanged, without retry; `is_fatal(exc)` tells callers to stop the
   run (decision D16).
9. Parse JSON (code fences and surrounding prose tolerated), `model_validate(context=...)`.
   On failure send the answer and the error back; after 2 retries `LLMValidationError`.
10. Tokens and `usage.cost` are summed over all calls of one `llm()` invocation.

## Input and output
`LLMResult`: `content`, `parsed`, `model`, `mode`, `tokens_in`, `tokens_out`, `cost_usd`, `attempts`.

## CLI commands
None directly; used by `summarize` and translation.

## Settings (.env)
`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_FAST`, `LLM_MODEL_SMART`,
`LLM_REASONING_EFFORT_FAST`, `LLM_REASONING_EFFORT_SMART`, `LLM_MAX_OUTPUT_TOKENS`.

## Data
None.

## Edge cases and errors
- Empty model -> `LLMError("no model configured ...")`.
- 401, 402, 403 are raised unchanged (`is_fatal`); other unfixable 4xx are raised unchanged too.
- The API key is never logged or printed.

## Done criteria
`uv run pytest tests/test_llm.py` (fake client: fallback chain, dropped parameters, max_tokens
passed and dropped, truncation retry and failure, fatal statuses, validation feedback, backoff,
no key in errors).

## Working on this code
- No other module may create an OpenAI client or call the API; everything goes through `llm()`.
- New provider-specific parameters go into `extra_body` and must be droppable when rejected
  (`_Shape.degrade`); so must new standard parameters such as `max_tokens`.
- Never parse a response with `finish_reason == "length"`.
- Never log request kwargs or the client; the key lives only in `get_client()`.
- Tests use `FakeClient` and real `openai` exception classes; `llm._sleep` is patched.
- Run: `uv run pytest tests/test_llm.py`.

## Open questions
- None.
