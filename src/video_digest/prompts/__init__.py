"""Prompt templates (`=== NAME ===` sections, `{{PLACEHOLDER}}` values) and the glossary."""

from importlib import resources


def _read(name: str) -> str:
    return resources.files(__name__).joinpath(name).read_text(encoding="utf-8")


def load_prompt_sections(version: str) -> dict[str, str]:
    """Split <version>.md into its "=== NAME ===" sections."""
    sections: dict[str, str] = {}
    name: str | None = None
    lines: list[str] = []
    for line in _read(f"{version}.md").splitlines():
        if line.startswith("=== ") and line.endswith(" ==="):
            if name:
                sections[name] = "\n".join(lines).strip()
            name, lines = line[4:-4].strip(), []
        else:
            lines.append(line)
    if name:
        sections[name] = "\n".join(lines).strip()
    return sections


def load_glossary() -> list[str]:
    """Canonical names, one per line."""
    return [line.strip() for line in _read("glossary.txt").splitlines() if line.strip() and not line.startswith("#")]


def fill(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template
