import sys
from pathlib import Path

import pytest

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
for import_path in (
    TEST_DRIVE_ROOT / "common",
    TEST_DRIVE_ROOT / "scenarios",
    TEST_DRIVE_ROOT,
):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from runner import run_siem_c2

USERS_FILE = TEST_DRIVE_ROOT / "data" / "users.local.json"


@pytest.mark.asyncio
async def test_siem_c2() -> None:
    await run_siem_c2(users_path=USERS_FILE)
