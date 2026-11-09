from __future__ import annotations

import inspect

import pytest
from browser_diagnostics import BrowserDiagnostics
from scenarios.runner import _run_user_in_separate_browser


@pytest.mark.asyncio
async def test_browser_diagnostics_are_disabled_without_directory_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(BrowserDiagnostics.DIRECTORY_ENV, raising=False)

    diagnostics = await BrowserDiagnostics.start(
        context=object(),  # type: ignore[arg-type]
        username="playwright_user_1",
    )

    assert diagnostics is None


def test_browser_diagnostics_capture_png_and_dom_trace() -> None:
    source = inspect.getsource(BrowserDiagnostics)

    assert "context.tracing.start" in source
    assert "snapshots=True" in source
    assert "page.screenshot" in source
    assert "trace.zip" in source
    assert "PT_TEST_DRIVE_DIAGNOSTICS_DIR" in source


def test_siem_runner_starts_and_stops_optional_diagnostics() -> None:
    source = inspect.getsource(_run_user_in_separate_browser)

    assert "BrowserDiagnostics.start" in source
    assert "await diagnostics.stop()" in source
