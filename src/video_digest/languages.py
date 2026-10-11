"""Language codes (ISO 639-1), display names and a lenient script check."""

import re

SOURCE = "source"  # ANALYSIS_LANGUAGE value: the video's own language
CODE_RE = re.compile(r"^[a-z]{2}$")
CYRILLIC_CHAR_RE = re.compile(r"[Ѐ-ӿ]")
MAX_CYRILLIC_SHARE = 0.10  # for non-Cyrillic target languages

LANGUAGE_NAMES = {
    "ar": "Arabic", "be": "Belarusian", "bg": "Bulgarian", "cs": "Czech", "da": "Danish", "de": "German",
    "el": "Greek", "en": "English", "es": "Spanish", "fi": "Finnish", "fr": "French", "he": "Hebrew",
    "hi": "Hindi", "hu": "Hungarian", "id": "Indonesian", "it": "Italian", "ja": "Japanese", "kk": "Kazakh",
    "ko": "Korean", "nl": "Dutch", "no": "Norwegian", "pl": "Polish", "pt": "Portuguese", "ro": "Romanian",
    "ru": "Russian", "sk": "Slovak", "sr": "Serbian", "sv": "Swedish", "tr": "Turkish", "uk": "Ukrainian",
    "vi": "Vietnamese", "zh": "Chinese",
}  # fmt: skip
CYRILLIC_LANGUAGES = {"ru", "uk", "be", "bg", "sr", "mk", "kk", "ky", "tg", "mn"}


def is_valid_code(code: str) -> bool:
    return bool(CODE_RE.match(code))


def base_code(tag: str | None) -> str | None:
    """"en-US" -> "en", "ru-orig" -> "ru"; None or junk -> None."""
    if not tag:
        return None
    code = tag.split("-")[0].lower()
    return code if is_valid_code(code) else None


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, f'the language with ISO 639-1 code "{code}"')


def effective_language(setting: str, source_language: str | None, fallback: str = "en") -> str:
    """ANALYSIS_LANGUAGE setting -> concrete code; "source" uses the video's language."""
    if setting == SOURCE:
        return source_language or fallback
    return setting


def cyrillic_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if CYRILLIC_CHAR_RE.match(c)) / len(letters)


def check_script(text: str, language: str) -> None:
    """Lenient: Cyrillic languages need some Cyrillic; others need < 10% Cyrillic letters."""
    if language in CYRILLIC_LANGUAGES:
        if not CYRILLIC_CHAR_RE.search(text):
            raise ValueError(f"must be written in {language_name(language)} (no Cyrillic letters found)")
    elif cyrillic_share(text) >= MAX_CYRILLIC_SHARE:
        raise ValueError(f"must be written in {language_name(language)}, not in a Cyrillic-script language")
