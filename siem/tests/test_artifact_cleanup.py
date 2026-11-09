import sys
from pathlib import Path

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
for import_path in (
    TEST_DRIVE_ROOT / "common",
    TEST_DRIVE_ROOT,
):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from artifact_cleanup import clear_directory_contents, prepare_periodic_cleanup


def test_periodic_cleanup_cleans_before_every_third_run(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "allure-results"
    state_path = tmp_path / "artifacts" / ".allure-run-count"

    for expected_run_number in range(1, 3):
        marker = report_path / f"run-{expected_run_number}.json"
        report_path.mkdir(parents=True, exist_ok=True)
        marker.write_text("result", encoding="utf-8")

        result = prepare_periodic_cleanup(
            target_path=report_path,
            state_path=state_path,
            cleanup_interval=3,
        )

        assert result.run_number == expected_run_number
        assert result.cleaned is False
        assert marker.exists()

    result = prepare_periodic_cleanup(
        target_path=report_path,
        state_path=state_path,
        cleanup_interval=3,
    )

    assert result.run_number == 3
    assert result.cleaned is True
    assert list(report_path.iterdir()) == []
    assert state_path.read_text(encoding="utf-8").strip() == "0"


def test_clear_directory_contents_keeps_target_directory(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "screenshots"
    nested = directory / "run"
    nested.mkdir(parents=True)
    (nested / "failed.png").write_text("png", encoding="utf-8")
    (directory / "result.txt").write_text("text", encoding="utf-8")

    clear_directory_contents(directory)

    assert directory.exists()
    assert list(directory.iterdir()) == []
