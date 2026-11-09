from pathlib import Path

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = TEST_DRIVE_ROOT.parent
SCAN_ROOTS = (
    PROJECT_ROOT / "common",
    TEST_DRIVE_ROOT,
)
SCAN_SUFFIXES = {".py", ".md", ".json"}
CYRILLIC_SAMPLE = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя«»"
MOJIBAKE_TOKENS = tuple(
    token
    for token in {
        char.encode("utf-8").decode("cp1251", errors="ignore")
        for char in CYRILLIC_SAMPLE
    }
    if len(token) >= 2
)


def test_runtime_sources_do_not_contain_mojibake() -> None:
    offenders: list[str] = []
    for root in SCAN_ROOTS:
        for path in root.rglob("*"):
            if (
                not path.is_file()
                or path.suffix not in SCAN_SUFFIXES
                or "__pycache__" in path.parts
            ):
                continue

            text = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if any(token in line for token in MOJIBAKE_TOKENS):
                    offenders.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{line_no}:{line.strip()}"
                    )

    assert not offenders, "\n".join(offenders[:50])
