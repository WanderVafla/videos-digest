# Decisions

Format: Decision / Why / Consequence. Newest last. Read before "simplifying" something.

## D1. Only the video's original-language caption track
- **Decision:** manual subtitles in the video's language, else the `-orig` auto track in the
  video's language; never translated auto-captions, never `-orig` tracks of other languages.
  One caption request per video (`ingest.pick_track`).
- **Why:** requests for translated captions got HTTP 429 from YouTube. Auto-dubbed videos list
  the dub's `-orig` track first (e.g. `en-US-orig` on a Russian video), which gave transcripts in
  the wrong language.
- **Consequence:** videos with only dubbed tracks get `transcript_status: no_transcript`.

## D2. Random delays and stop on the first rate limit
- **Decision:** 10-30 s random pause between videos that need network requests (`--sleep`
  overrides); the run stops on the first HTTP 429 (exit code 2). Cached videos make no request.
- **Why:** the user's IP was blocked by YouTube once.
- **Consequence:** ingest is slow (~20 s per new video); re-running is cheap.

## D3. yt-dlp through the Python API behind `make_ydl()`
- **Decision:** every `YoutubeDL` is built in `ingest.make_ydl(opts)` with `cast("Any", opts)`;
  `extract_info` results are cast to `dict[str, Any]`.
- **Why:** Pyright rejects a plain dict for yt-dlp's `_Params` type.
- **Consequence:** one place to change yt-dlp options; `js_runtimes` is set from `YTDLP_JS_RUNTIME`.

## D4. One `llm()` function with a fallback chain
- **Decision:** all LLM calls go through `llm.llm()`: structured output (strict json_schema with
  OpenRouter `provider.require_parameters`), then json_schema without it, then json_object, then
  plain text with the schema in the prompt; rejected `temperature`/`reasoning` are dropped;
  pydantic validation errors are fed back (2 retries); 429/5xx/connection errors back off
  1, 2, 4, 8 s (5 attempts). Provider at the moment: OpenRouter.
- **Why:** must work with any OpenAI-compatible provider and model.
- **Consequence:** a call may silently degrade to a weaker mode; `LLMResult.mode`/`attempts` show it.

## D5. Cache by transcript, prompt version, model and analysis language
- **Decision:** `analyses.cache_key = sha256(transcript, PROMPT_VERSION, model, analysis language)`.
- **Why:** never pay twice for the same input; any of the four changes the result.
- **Consequence:** changing a prompt requires a new prompt file and `PROMPT_VERSION`; editing the
  prompt in place would serve stale cached results.

## D6. ANALYSIS_LANGUAGE is in the cache key, DIGEST_LANGUAGE is not
- **Decision:** analyses are generated once in the analysis language; text is translated only for
  display (`translate.translate`, own cache table).
- **Why:** changing what the user reads must not re-run the expensive analysis.
- **Consequence:** review files can show translated text while review_model.md and the DB stay
  in the analysis language.

## D7. Both languages default to English
- **Decision:** `ANALYSIS_LANGUAGE=en`, `DIGEST_LANGUAGE=en` by default.
- **Why:** one shared vocabulary for clustering, search and deduplication across Russian and
  English videos; English text is shorter.
- **Consequence:** a Russian-reading user sets `DIGEST_LANGUAGE=ru` and pays for translation at
  display time only.

## D8. Terms are never translated
- **Decision:** names of tools, models, companies, libraries, commands, flags, file names, URLs,
  acronyms and established English terms stay as they are, in Latin script; topic slugs and
  entities are never translated. Written into both prompts.
- **Why:** translated terms break search, deduplication and the glossary.
- **Consequence:** the script check is lenient: non-Cyrillic targets allow < 10% Cyrillic
  letters; Cyrillic targets need some Cyrillic.

## D9. Dynamic topic list, not part of the cache key
- **Decision:** topics live in `topics.txt` and grow during `summarize`; code decides what is new
  (exact, normalized, near-duplicate with difflib ratio >= 0.85); max 2 new per video once the
  list is non-empty.
- **Why:** a fixed vocabulary does not fit a changing field; models create synonyms if unchecked.
- **Consequence:** results depend on processing order; `--force` re-runs use the grown list.

## D10. demo_value from text hints only
- **Decision:** code computes pointer phrases, command mentions, long pauses and title keywords
  (`features.py`) and passes them as weak evidence; `demo_confidence` flags certainty.
- **Why:** transcripts contain no picture.
- **Consequence:** ambiguous cases may later be checked by a multimodal model (features/ideas.md).

## D11. Blind review without model scores
- **Decision:** `data/review_blind.md` shows only title, link, summary and key points;
  model scores go to `data/review_model.md` (git-ignored). review_blind.md is tracked in git.
- **Why:** the user's ratings must not be biased by the model.
- **Consequence:** ratings survive re-exports and are versioned.

## D12. YouTube "{dash}" artifacts are normalized
- **Decision:** captions contain a literal `{dash}`; stage 1 replaces `{dash} {dash}` with `--`
  and `{dash}` with `-` (`ingest.normalize_caption_text`).
- **Why:** it appeared in real captions (spoken CLI flags such as `--ide`).
- **Consequence:** `install {dash} {dash} IDE` becomes `install -- IDE` (space kept).
  Note: an earlier plan was to leave such artifacts as they are; the code normalizes them.

## D13. Long-prompt pricing is not a concern now
- **Decision:** one request per transcript; split into parts only above ~100k estimated tokens
  (`summarize.MAX_PROMPT_TOKENS`, len/3.5).
- **Why:** Claude Haiku 5.5 prices rise for prompts above 100,000 tokens (user-provided, not
  re-checked here). The longest prompt in the current data is ~32k tokens (`rPoGtu6Td8E`,
  1 h 52 min).
- **Consequence:** the split-and-merge path is tested with fakes only.

## D14. Squash-merge PRs; code and spec in separate PRs
- **Decision:** work happens on branches and is squash-merged into `main` by the user; code
  changes and specification changes go in separate PRs. Agents do not push or merge.
- **Why:** a readable `main` history and reviewable spec changes.
- **Consequence:** the sync rule (AGENTS.md) still applies inside a code PR: behavior changes
  update the matching spec file in the same commit.

## Pitfalls

- Requesting a translated caption track (e.g. `en` for a Russian video) -> HTTP 429 and an
  IP block. Only use the track `pick_track` returns.
- The first `-orig` key in `automatic_captions` can be a dub, not the original.
- Editing `summarize_v2.md` in place serves old cached analyses. Create `summarize_v3.md`
  and bump `PROMPT_VERSION`.
- No `max_tokens` is sent: OpenRouter reserves credit for the model's maximum output
  (65,536 for Haiku) and answers 402 if the balance is lower.
- An invalid API key gives 401 for every video; `summarize` does not stop early.
- Typer renders `[...]` in help texts as markup and drops it; write `(default: ...)`.
- `topics.txt` changes during a run; tests must pass `topics_path` to a temp file.
- Near-duplicate matching can merge related but different slugs (`ai-design` -> `ai-designer`);
  typos and plurals are the intended case. Mappings are printed; `topics merge` fixes mistakes.
- Pause detection uses event durations; auto-caption events overlap, so long pauses are rare.
