"""Every project file has a row in the spec code map; every relative link in the docs resolves."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC_README = ROOT / "spec" / "README.md"
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE_RE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
CODE_RE = re.compile(r"`([^`]+)`")


def project_files() -> list[str]:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"git ls-files failed: {result.stderr.strip()}")
    # --cached also lists files deleted in the working tree but not yet staged
    return sorted(f for f in result.stdout.splitlines() if f and (ROOT / f).exists())


def code_map_paths() -> list[str]:
    """Paths in the first column of the "Code map" table in spec/README.md."""
    section = SPEC_README.read_text(encoding="utf-8").split("## Code map", 1)[1].split("\n## ", 1)[0]
    paths: list[str] = []
    for line in section.splitlines():
        cells = line.split("|")
        if line.startswith("|") and len(cells) > 2:
            paths += CODE_RE.findall(cells[1])
    return paths


def covered(path: str, entries: list[str]) -> bool:
    return any(path == e or (e.endswith("/") and path.startswith(e)) for e in entries)


def test_code_map_has_rows() -> None:
    entries = code_map_paths()
    assert "src/video_digest/llm.py" in entries
    assert "tests/" in entries


def test_code_map_entries_exist() -> None:
    missing = [e for e in code_map_paths() if not (ROOT / e).exists()]
    assert not missing, f"code map rows in spec/README.md name paths that do not exist: {missing}"


def test_every_file_is_in_the_code_map() -> None:
    entries = code_map_paths()
    missing = [f for f in project_files() if not covered(f, entries)]
    assert not missing, (
        "files without a row in the code map of spec/README.md (add an exact path or a directory row): "
        + ", ".join(missing)
    )


def doc_files() -> list[Path]:
    return [ROOT / "AGENTS.md", *sorted((ROOT / "spec").rglob("*.md"))]


def test_relative_links_resolve() -> None:
    broken: list[str] = []
    for doc in doc_files():
        text = FENCE_RE.sub("", doc.read_text(encoding="utf-8"))
        for target in LINK_RE.findall(text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                continue  # URL, mailto: or an anchor in the same file
            path = target.split("#", 1)[0]
            if not (doc.parent / path).exists():
                broken.append(f"{doc.relative_to(ROOT)} -> {target}")
    assert not broken, "broken relative links: " + "; ".join(broken)
