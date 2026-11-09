from __future__ import annotations

import sys
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parent
SHARED_ROOT = PRODUCT_ROOT.parent / "common"
PRODUCT_SETTINGS_ROOT = PRODUCT_ROOT / "common"

for import_path in reversed((PRODUCT_SETTINGS_ROOT, SHARED_ROOT, PRODUCT_ROOT)):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from pytest_support import (  # noqa: E402,F401
    pytest_addoption,
    pytest_configure,
    pytest_report_teststatus,
    pytest_runtest_makereport,
    pytest_sessionstart,
)
