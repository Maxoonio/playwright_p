from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PeriodicCleanupResult:
    run_number: int
    cleanup_interval: int
    cleaned: bool


def prepare_periodic_cleanup(
    *,
    target_path: Path,
    state_path: Path,
    cleanup_interval: int,
) -> PeriodicCleanupResult:
    if cleanup_interval < 1:
        raise ValueError("Cleanup interval must be at least 1")

    previous_run_count = _read_run_count(state_path)
    run_number = previous_run_count + 1
    should_clean = run_number >= cleanup_interval

    if should_clean:
        clear_directory_contents(target_path)
        next_run_count = 0
    else:
        next_run_count = run_number

    target_path.mkdir(parents=True, exist_ok=True)
    _write_run_count(state_path, next_run_count)

    return PeriodicCleanupResult(
        run_number=cleanup_interval if should_clean else run_number,
        cleanup_interval=cleanup_interval,
        cleaned=should_clean,
    )


def clear_directory_contents(directory: Path) -> None:
    if not directory.exists():
        return

    for child in directory.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)


def _read_run_count(state_path: Path) -> int:
    try:
        value = int(state_path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ValueError):
        return 0

    return max(value, 0)


def _write_run_count(state_path: Path, value: int) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = state_path.with_name(f"{state_path.name}.tmp")
    temporary_path.write_text(f"{value}\n", encoding="utf-8")
    temporary_path.replace(state_path)
