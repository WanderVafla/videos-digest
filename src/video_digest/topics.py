"""Dynamic topic taxonomy in topics.txt (`slug: description` per line, English).

The model proposes topics; code decides what is new: exact match -> normalized match ->
near-duplicate (difflib ratio >= 0.85) -> otherwise a new topic appended to the file.
The list is mutable state and not part of the cache key.
"""

import difflib
import json
import os
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from video_digest import db
from video_digest.schema import TAG_PATTERN, TopicRef

SLUG_RE = re.compile(TAG_PATTERN)
LINE_RE = re.compile(r"^\s*([^:#\s][^:]*?)\s*:\s*(.*?)\s*$")
MAX_WORDS = 3
MAX_NEW_PER_VIDEO = 2
MAX_IN_PROMPT = 150
NEAR_DUPLICATE_RATIO = 0.85


class Resolution(BaseModel):
    slugs: list[str]  # final slugs for the analysis, in order, unique
    new: dict[str, str]  # slug -> description, to append to the file
    mapped: list[tuple[str, str]]  # (proposed, existing) via normalization or near-duplicate


def load_topics(path: Path) -> dict[str, str]:
    """slug -> description; a missing or empty file is an empty taxonomy. Invalid lines are skipped."""
    if not path.exists():
        return {}
    topics: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = LINE_RE.match(line)
        if m and SLUG_RE.match(m.group(1)):
            topics.setdefault(m.group(1), m.group(2))
    return topics


def save_topics(path: Path, topics: dict[str, str]) -> None:
    """Rewrite the whole file atomically (temp file + rename)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(f"{slug}: {desc}\n" for slug, desc in topics.items()), encoding="utf-8")
    os.replace(tmp, path)


def append_topics(path: Path, new: dict[str, str]) -> None:
    if not new:
        return
    topics = load_topics(path)
    for slug, desc in new.items():
        topics.setdefault(slug, desc)
    save_topics(path, topics)


def normalize(slug: str) -> str:
    """Lowercase, hyphens for separators, strip a trailing plural "s": "AI Agents" -> "ai-agent"."""
    s = re.sub(r"[\s_]+", "-", slug.strip().lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if len(s) > 3 and s.endswith("s") and not s.endswith("ss"):
        s = s[:-1]
    return s


def find_existing(slug: str, existing: Iterable[str]) -> tuple[str | None, str]:
    """(existing slug or None, how): how is "exact", "normalized", "near" or "new"."""
    existing = list(existing)
    if slug in existing:
        return slug, "exact"
    norm = normalize(slug)
    normalized = {normalize(e): e for e in existing}
    if norm in normalized:
        return normalized[norm], "normalized"
    best, best_ratio = None, 0.0
    for e_norm, e in normalized.items():
        ratio = difflib.SequenceMatcher(None, norm, e_norm).ratio()
        if ratio > best_ratio:
            best, best_ratio = e, ratio
    if best is not None and best_ratio >= NEAR_DUPLICATE_RATIO:
        return best, "near"
    return None, "new"


def resolve(refs: list[TopicRef], existing: dict[str, str]) -> Resolution:
    slugs: list[str] = []
    new: dict[str, str] = {}
    mapped: list[tuple[str, str]] = []
    for ref in refs:
        match, how = find_existing(ref.slug, list(existing) + list(new))
        if match is None:
            new[ref.slug] = ref.description.strip()
            match = ref.slug
        elif how in ("normalized", "near"):
            mapped.append((ref.slug, match))
        if match not in slugs:
            slugs.append(match)
    return Resolution(slugs=slugs, new=new, mapped=mapped)


def check_new_topics(refs: list[TopicRef], existing: dict[str, str]) -> None:
    """Rules for topics that are new according to the file (not according to the model).

    The 2-new limit applies once the taxonomy is non-empty; the first video seeds it freely.
    """
    resolution = resolve(refs, existing)
    problems: list[str] = []
    if existing and len(resolution.new) > MAX_NEW_PER_VIDEO:
        problems.append(
            f"{len(resolution.new)} new topics ({', '.join(resolution.new)}); at most {MAX_NEW_PER_VIDEO} "
            "per video, reuse existing slugs from the topic list"
        )
    for slug, desc in resolution.new.items():
        if len(slug.split("-")) > MAX_WORDS:
            problems.append(f"new topic '{slug}' has more than {MAX_WORDS} words")
        if not desc:
            problems.append(f"new topic '{slug}' needs a short English description")
    if len(resolution.slugs) < 3:
        problems.append("after merging duplicates fewer than 3 distinct topics remain; add different topics")
    if problems:
        raise ValueError("topics: " + "; ".join(problems))


def usage_counts(topic_lists: Iterable[list[str]]) -> Counter[str]:
    return Counter(slug for slugs in topic_lists for slug in set(slugs))


def prompt_block(topics: dict[str, str], usage: Counter[str], limit: int = MAX_IN_PROMPT) -> str:
    """Existing topics for the prompt, most used first, at most `limit` entries."""
    if not topics:
        return "(empty: there are no topics yet, invent the first ones)"
    ordered = sorted(topics, key=lambda s: (-usage[s], s))[:limit]
    return "\n".join(f"- {slug} ({usage[slug]}): {topics[slug]}" for slug in ordered)


def merge_in_file(path: Path, old: str, new: str) -> None:
    """Remove OLD; NEW keeps its description, or takes OLD's if NEW was not in the file."""
    topics = load_topics(path)
    old_desc = topics.pop(old, "")
    if new not in topics:
        topics[new] = old_desc
    save_topics(path, topics)


def merge_slugs(slugs: list[str], old: str, new: str) -> list[str]:
    result: list[str] = []
    for slug in slugs:
        slug = new if slug == old else slug
        if slug not in result:
            result.append(slug)
    return result


# --- stored analyses ---------------------------------------------------------------


def stored_topic(item: Any) -> tuple[str, str] | None:
    """(slug, description) of one stored topic: a {slug, description} object (v2+) or a plain
    string (summarize_v1). Anything else -> None, so legacy or broken rows never crash a reader."""
    if isinstance(item, str):
        return (item, "") if item else None
    if isinstance(item, dict):
        slug: Any = item.get("slug")
        desc: Any = item.get("description")
        if isinstance(slug, str) and slug:
            return slug, desc if isinstance(desc, str) else ""
    return None


def stored_topics(analysis_json: str) -> list[tuple[str, str]]:
    """All readable topics of a stored analysis_json, in order; invalid JSON or shapes -> []."""
    try:
        data = json.loads(analysis_json)
    except json.JSONDecodeError:
        return []
    items: Any = data.get("topics") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [t for t in map(stored_topic, items) if t is not None]


def stored_usage(conn: sqlite3.Connection, prompt_version: str) -> Counter[str]:
    """Usage per slug over the newest analysis of each video for one prompt version."""
    rows = db.latest_analysis_json_per_video(conn, prompt_version)
    return usage_counts([slug for slug, _ in stored_topics(j)] for j in rows)


def merge(conn: sqlite3.Connection, path: Path, old: str, new: str, prompt_version: str) -> int:
    """Rewrite OLD to NEW in the analyses of one prompt version and in topics.txt; returns analyses changed."""
    if not SLUG_RE.match(new) or len(new.split("-")) > MAX_WORDS:
        raise ValueError(f"invalid slug: {new!r}")
    known = load_topics(path)
    new_desc = known.get(new) or known.get(old, "")
    changed = 0
    with conn:
        for row_id, analysis_json in db.analysis_json_rows(conn, prompt_version):
            refs = stored_topics(analysis_json)
            slugs = [slug for slug, _ in refs]
            if old not in slugs:
                continue
            data = json.loads(analysis_json)
            descs = dict(refs)
            descs[new] = new_desc
            data["topics"] = [{"slug": s, "description": descs.get(s, "")} for s in merge_slugs(slugs, old, new)]
            db.update_analysis_json(conn, row_id, json.dumps(data, ensure_ascii=False))
            changed += 1
    merge_in_file(path, old, new)
    return changed
