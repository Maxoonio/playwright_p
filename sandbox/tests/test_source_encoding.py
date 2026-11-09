from pathlib import Path

SANDBOX_ROOT = Path(__file__).resolve().parents[1]
SCAN_SUFFIXES = {".py", ".md", ".json"}
CYRILLIC_SAMPLE = (
    "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя«»—"
)
BROKEN_UTF8_MARKERS = tuple(
    token
    for encoding in ("cp1251", "cp1252", "latin1")
    for character in CYRILLIC_SAMPLE
    if len(token := character.encode("utf-8").decode(encoding, errors="ignore")) >= 2
)


def test_sandbox_sources_are_valid_utf8_without_broken_text() -> None:
    offenders: list[str] = []
    for path in SANDBOX_ROOT.rglob("*"):
        if (
            path.resolve() == Path(__file__).resolve()
            or not path.is_file()
            or path.suffix not in SCAN_SUFFIXES
            or "__pycache__" in path.parts
            or "allure-results" in path.parts
            or "allure-report" in path.parts
            or "artifacts" in path.parts
        ):
            continue

        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if any(marker in line for marker in BROKEN_UTF8_MARKERS):
                offenders.append(
                    f"{path.relative_to(SANDBOX_ROOT)}:{line_no}:{line.strip()}"
                )

    assert not offenders, "\n".join(offenders[:50])
