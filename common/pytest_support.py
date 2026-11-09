import os
import platform
import sys
from pathlib import Path

import pytest
from exceptions import ScenarioChecksErrored, ScenarioChecksFailed


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("PT Matrix test-drive")
    group.addoption(
        "--headless",
        action="store_true",
        dest="test_drive_headless",
        help="Запустить браузеры test-drive без графического интерфейса.",
    )
    group.addoption(
        "--headed",
        action="store_true",
        dest="test_drive_headed",
        help="Принудительно запустить видимые браузеры test-drive.",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    import settings

    headless = config.getoption("test_drive_headless")
    headed = config.getoption("test_drive_headed")
    if headless and headed:
        raise pytest.UsageError(
            "Параметры --headless и --headed нельзя использовать вместе."
        )

    if headless:
        settings.HEADLESS = True
    elif headed:
        settings.HEADLESS = False
    else:
        env_headless = os.getenv("PT_TEST_DRIVE_HEADLESS", "").casefold()
        if env_headless in {"1", "true", "yes"}:
            settings.HEADLESS = True
        elif env_headless in {"0", "false", "no"}:
            settings.HEADLESS = False

    if (
        _is_live_scenario_session(config)
        and hasattr(config.option, "allure_report_dir")
        and not config.option.allure_report_dir
    ):
        config.option.allure_report_dir = str(settings.ALLURE_RESULTS_DIR)


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    if not _is_live_scenario_session(session.config):
        # Unit tests import the product conftest too. They must not increment
        # live-run counters or erase screenshots/Allure evidence that the
        # developer is currently investigating.
        return

    import settings
    from artifact_cleanup import prepare_periodic_cleanup

    screenshots_result = prepare_periodic_cleanup(
        target_path=settings.FAILURE_SCREENSHOTS_DIR,
        state_path=getattr(
            settings,
            "SCREENSHOTS_CLEANUP_STATE_FILE",
            settings.ALLURE_CLEANUP_STATE_FILE.with_name(".screenshots-run-count"),
        ),
        cleanup_interval=getattr(
            settings,
            "SCREENSHOTS_CLEANUP_INTERVAL",
            settings.ALLURE_RESULTS_CLEANUP_INTERVAL,
        ),
    )
    if screenshots_result.cleaned:
        print(
            "[REPORT] Запуск "
            f"{screenshots_result.run_number}/"
            f"{screenshots_result.cleanup_interval}: "
            f"старые скриншоты очищены из "
            f"{settings.FAILURE_SCREENSHOTS_DIR}"
        )
    else:
        print(
            "[REPORT] Запуск "
            f"{screenshots_result.run_number}/"
            f"{screenshots_result.cleanup_interval}: "
            f"скриншоты сохраняются в "
            f"{settings.FAILURE_SCREENSHOTS_DIR}"
        )

    report_directory = getattr(
        session.config.option,
        "allure_report_dir",
        None,
    )
    if not report_directory:
        return

    report_path = Path(report_directory)
    if not report_path.is_absolute():
        report_path = session.config.invocation_params.dir / report_path
    report_path = report_path.resolve()
    _prepare_periodic_allure_cleanup(report_path)
    report_path.mkdir(parents=True, exist_ok=True)

    environment = {
        "PT Matrix URL": settings.BASE_URL,
        "Scenario": settings.SCENARIO_LABEL,
        "Content GitLab project": settings.CONTENT_GITLAB_PROJECT,
        "Content GitLab ref": settings.CONTENT_GITLAB_REF,
        "CTF GitLab ref": settings.CTF_CONTENT_GITLAB_REF,
        "Headless": settings.HEADLESS,
        "Locale": settings.LOCALE,
        "Python": sys.version.split()[0],
        "Platform": platform.platform(),
        "Parallel browsers": (settings.MAX_PARALLEL_BROWSERS or "all enabled users"),
    }
    content = "\n".join(
        f"{_allure_property(name)}={_allure_property(value)}"
        for name, value in environment.items()
    )
    (report_path / "environment.properties").write_text(
        content + "\n",
        encoding="utf-8",
    )


def _is_live_scenario_session(config: pytest.Config) -> bool:
    """Return true only for an explicitly selected browser scenario path."""

    for raw_argument in config.args:
        argument = str(raw_argument).split("::", maxsplit=1)[0]
        normalized = argument.replace("\\", "/").casefold().rstrip("/")
        if normalized.endswith("/scenarios"):
            return True
        if "/scenarios/" in normalized and normalized.endswith("_scenario_test.py"):
            return True
    return False


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    if call.excinfo and call.excinfo.errisinstance(
        (ScenarioChecksFailed, ScenarioChecksErrored)
    ):
        report.scenario_failed = True
        report.longrepr = None


def pytest_report_teststatus(report, config):
    if getattr(report, "scenario_failed", False):
        return "scenario_failed", "", ""


def _allure_property(value: object) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace("=", "\\=")
        .replace(":", "\\:")
    )


def _prepare_periodic_allure_cleanup(report_path: Path) -> None:
    import settings
    from artifact_cleanup import prepare_periodic_cleanup

    if report_path != settings.ALLURE_RESULTS_DIR.resolve():
        return

    result = prepare_periodic_cleanup(
        target_path=report_path,
        state_path=settings.ALLURE_CLEANUP_STATE_FILE,
        cleanup_interval=settings.ALLURE_RESULTS_CLEANUP_INTERVAL,
    )
    if result.cleaned:
        print(
            "[ALLURE] Запуск "
            f"{result.run_number}/{result.cleanup_interval}: "
            f"старые результаты очищены из {report_path}"
        )
        return

    print(
        "[ALLURE] Запуск "
        f"{result.run_number}/{result.cleanup_interval}: "
        f"результаты сохраняются в {report_path}"
    )
