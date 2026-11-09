import asyncio
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
for import_path in (
    TEST_DRIVE_ROOT / "common",
    TEST_DRIVE_ROOT,
):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import pages.nad_scenario_page as nad_scenario_page_module
import scenarios.runner as nad_runner
import settings
import trainer_ctf_bridge
from data_loader import load_test_drive_users
from models import (
    PERFECT_PROFILE_NAMES,
    SPECIALIZED_CTF_PROFILE_NAMES,
)
from pages.nad_scenario_page import NadTestDriveScenarioPage
from scenarios.runner import (
    _allowed_complex_final_statuses,
    _finish_specialized_profile_test_drive,
    _run_full_scenario,
    _run_shared_introduction,
    _run_user,
    _run_user_in_separate_browser,
    run_nad_c3,
)
from trainer_ctf_bridge import (
    load_trainer_ctf_api,
    run_specialized_ctf_profile,
    verify_test_drive_leaderboard,
)
from utils.browser import launch_incognito_context

USERS_FILE = TEST_DRIVE_ROOT / "data" / "users.local.json"


def test_five_users_have_expected_profile_roles() -> None:
    users, errors = load_test_drive_users(USERS_FILE)

    assert errors == []
    assert [(user.username, user.test_profile) for user in users] == [
        ("playwright_user_1", "perfect"),
        ("playwright_user_2", "answers_and_hints"),
        ("playwright_user_3", "solutions"),
        ("playwright_user_4", "stage_statuses"),
        ("playwright_user_5", "question_metrics"),
    ]
    assert PERFECT_PROFILE_NAMES == {"perfect", "full_scenario"}
    assert SPECIALIZED_CTF_PROFILE_NAMES == {
        "answers_and_hints",
        "solutions",
        "stage_statuses",
        "question_metrics",
    }


def test_specialized_profiles_reuse_trainer_dispatcher() -> None:
    api = load_trainer_ctf_api()
    dispatcher_source = inspect.getsource(api.run_profile)
    question_metrics_profile = api.run_profile.__globals__[
        "run_question_metrics_profile"
    ]
    administrator_statistics = question_metrics_profile.__globals__[
        "_verify_administrator_statistics"
    ]

    assert api.run_profile.__module__ == "common.profiles.dispatcher"
    assert api.matrix_scenario_page.__module__ == ("common.pages.matrix_scenario_page")
    assert api.matrix_statistics_page.__module__ == (
        "common.pages.matrix_statistics_page"
    )
    assert administrator_statistics.__name__ == ("skip_administrator_statistics")
    for profile in SPECIALIZED_CTF_PROFILE_NAMES:
        assert f'profile == "{profile}"' in dispatcher_source


def test_ctf_bridge_avoids_initial_scheme_wait_and_admin_login() -> None:
    bridge_source = inspect.getsource(run_specialized_ctf_profile)

    assert 'user.test_profile == "stage_statuses"' in bridge_source
    assert "await super().verify_stage_opened" in bridge_source
    assert "_disable_test_drive_administrator_statistics" in inspect.getsource(
        load_trainer_ctf_api
    )
    assert "_reopen_test_drive_after_statistics_profile" in bridge_source
    assert "async def close_final_dialog" in bridge_source
    assert "handle_final_feedback" in bridge_source
    assert "QUESTION_METRICS_TRUST_DOM_VALUES" in inspect.getsource(
        trainer_ctf_bridge._configure_trainer_settings
    )
    assert "reopen_completed_ctf_from_modules" not in bridge_source
    assert "open_completed_tasks_and_results" not in bridge_source
    assert "LoginPage" not in bridge_source
    assert "CTF_EXISTING_PROGRESS_RESUMED" in bridge_source
    assert "_build_resumed_question_metrics_scenario" in bridge_source


def test_flow_separates_perfect_and_seventy_percent_profiles() -> None:
    flow_source = inspect.getsource(_run_full_scenario)

    assert settings.SPECIALIZED_CTF_ENTRY_PROGRESS_PERCENT == 70
    assert "Scenario content loading failed" in inspect.getsource(run_nad_c3)
    assert "progress_after[0] >= progress_target" in flow_source
    assert "CTF_ENTRY_PROGRESS_TARGET_REACHED" in inspect.getsource(
        sys.modules[_run_full_scenario.__module__]._record_ctf_progress_target
    )
    assert "_run_perfect_ctf" in flow_source
    assert "run_specialized_ctf_profile" in flow_source
    assert "_run_specialized_unfinished_module_transition" not in flow_source
    assert "SPECIALIZED_UNFINISHED_Q1_COMPLETED" not in flow_source
    assert "SPECIALIZED_UNFINISHED_NEXT_DIALOG_CONFIRMED" not in flow_source
    assert flow_source.index("_record_ctf_progress_target") < flow_source.index(
        "CTF_DIRECT_TRANSITION_CONFIRMED"
    )
    assert "TEST_DRIVE_100_PERCENT_CONFIRMED" in flow_source
    assert "if perfect_profile:" in flow_source
    assert "Идеальный профиль" in flow_source


def test_perfect_profile_logs_answer_mismatches_without_stopping() -> None:
    flow_source = inspect.getsource(_run_full_scenario)

    assert "perfect_answer_mismatches += 1" in flow_source
    assert "не может пропустить вопрос" not in flow_source
    assert "техническая операция не выполнилась после" in flow_source
    assert "TEST_DRIVE_100_PERCENT_SKIPPED" in flow_source
    assert "Строгая проверка 100%" in flow_source
    assert "allowed_final_statuses" in flow_source
    assert "ctf_expect_prerequisites_dialog" in flow_source
    assert "perfect_answer_mismatches == 0" in flow_source
    assert "переход в CTF выполнен напрямую или через окно подготовки" in flow_source
    assert _allowed_complex_final_statuses(0) == {"completed"}
    assert _allowed_complex_final_statuses(2) == {
        "completed",
        "skipped_by_bs",
    }


def test_enabled_users_run_in_parallel_incognito_browsers() -> None:
    run_source = inspect.getsource(run_nad_c3)
    user_source = inspect.getsource(_run_user_in_separate_browser)
    browser_source = inspect.getsource(launch_incognito_context)

    assert "asyncio.Semaphore" in run_source
    assert "asyncio.gather" in run_source
    assert "settings.MAX_PARALLEL_BROWSERS" in run_source
    assert "launch_incognito_context(playwright)" in user_source
    assert "PROFILE_UNEXPECTED_ERROR" in user_source
    assert "launch_persistent_context" in browser_source


@pytest.mark.asyncio
async def test_parallel_user_limit_is_enforced(monkeypatch) -> None:
    active = 0
    maximum_active = 0
    completed = 0

    async def fake_user_browser(**_kwargs) -> None:
        nonlocal active, maximum_active, completed
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        completed += 1

    runner_module = sys.modules[_run_user.__module__]
    monkeypatch.setattr(
        runner_module,
        "_run_user_in_separate_browser",
        fake_user_browser,
    )
    semaphore = asyncio.Semaphore(2)

    await asyncio.gather(
        *[
            _run_user(
                playwright=None,
                user=object(),
                collector=None,
                content=None,
                ctf_content=None,
                semaphore=semaphore,
            )
            for _ in range(4)
        ]
    )

    assert completed == 4
    assert maximum_active == 2


def test_only_perfect_profile_runs_sos_pro_and_button_checks() -> None:
    intro_source = inspect.getsource(_run_shared_introduction)
    flow_source = inspect.getsource(_run_full_scenario)

    assert "if _is_perfect_profile(user)" in intro_source
    assert "_run_sos_check" in intro_source
    assert "PRO_VERSION_REQUEST_SUBMITTED" in intro_source
    assert "if perfect_profile and module.step_id == 3" in flow_source
    assert "if perfect_profile:" in flow_source
    assert "COMPLEX_SCENARIO_PRO_REQUEST_SUBMITTED" in flow_source


def test_profile_checks_stop_before_user_5_leaderboard() -> None:
    flow_source = inspect.getsource(_run_full_scenario)
    finish_source = inspect.getsource(_finish_specialized_profile_test_drive)
    leaderboard_source = inspect.getsource(verify_test_drive_leaderboard)

    assert "_finish_specialized_profile_test_drive" in flow_source
    assert flow_source.index(
        "_finish_specialized_profile_test_drive"
    ) < flow_source.rindex('action="COMPLEX_SCENARIO_MODULE_OPENED"')
    assert "TEST_DRIVE_FINISHED_AFTER_CTF" in finish_source
    assert "scenario_page.finish_test_drive" in finish_source
    assert 'user.test_profile == "answers_and_hints"' in finish_source
    assert "SKIPPED_BY_BS_QUESTION_CONFIRMED" in finish_source
    assert "_verify_partial_profile_skipped_question" in finish_source
    assert 'if user.test_profile != "question_metrics":' in finish_source
    assert finish_source.index(
        'if user.test_profile != "question_metrics":'
    ) < finish_source.index('action="LEADERBOARD_RESULTS_CONFIRMED"')
    assert "LEADERBOARD_RESULTS_CONFIRMED" in finish_source
    assert "verify_test_drive_leaderboard" in finish_source
    assert ".open_leaderboard()" in leaderboard_source
    assert ".try_read_leaderboard_user(" in leaderboard_source
    assert ".wait_for_profile_statistics(" in leaderboard_source
    assert ".read_dashboard_statistics(" in leaderboard_source
    assert "leaderboard.completed_tasks" in leaderboard_source
    assert "expected_total_tasks" in leaderboard_source
    assert "dashboard_total_tasks != expected_total_tasks" in leaderboard_source
    assert "dashboard.completed_tasks" in leaderboard_source
    assert "dashboard.skipped_tasks" in leaderboard_source
    assert "leaderboard.completed_scenarios < 1" not in leaderboard_source


@pytest.mark.asyncio
async def test_exhausted_final_feedback_closes_dialog_without_error(
    monkeypatch,
) -> None:
    textarea = object()
    dialog = SimpleNamespace(
        locator=Mock(return_value=SimpleNamespace(first=textarea)),
    )
    page = SimpleNamespace(locator=Mock(return_value=SimpleNamespace(last=dialog)))
    scenario_page = NadTestDriveScenarioPage(page=page)
    scenario_page._close_final_feedback_dialog = AsyncMock(return_value=True)
    monkeypatch.setattr(settings, "FINAL_FEEDBACK_MAX_ATTEMPTS", 0)
    monkeypatch.setattr(
        nad_scenario_page_module,
        "expect",
        lambda _target: SimpleNamespace(to_be_visible=AsyncMock()),
    )

    status = await scenario_page.submit_final_feedback("feedback")

    assert status is None
    scenario_page._close_final_feedback_dialog.assert_awaited_once_with(dialog)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "leaderboard_expected"),
    (
        ("answers_and_hints", False),
        ("solutions", False),
        ("stage_statuses", False),
        ("question_metrics", True),
    ),
)
async def test_only_question_metrics_opens_leaderboard_after_finish(
    monkeypatch,
    profile: str,
    leaderboard_expected: bool,
) -> None:
    run_critical = AsyncMock(return_value=True)
    run_check = AsyncMock(return_value=True)
    monkeypatch.setattr(nad_runner, "_run_critical", run_critical)
    monkeypatch.setattr(nad_runner, "_run_check", run_check)
    user = SimpleNamespace(
        username="playwright_user",
        test_profile=profile,
    )

    result = await _finish_specialized_profile_test_drive(
        scenario_page=SimpleNamespace(
            page=object(),
            finish_test_drive=AsyncMock(),
        ),
        modules_page=object(),
        content=SimpleNamespace(total_questions=20),
        ctf_content=SimpleNamespace(total_questions=9),
        user=user,
        collector=object(),
    )

    actions = [call.kwargs["action"] for call in run_check.await_args_list]
    assert result is True
    assert ("LEADERBOARD_RESULTS_CONFIRMED" in actions) is leaderboard_expected


def test_question_metrics_reads_each_transition_and_final_dialog() -> None:
    api = load_trainer_ctf_api()
    performance_source = inspect.getsource(
        api.matrix_scenario_page.read_final_performance
    )
    profile = api.run_profile.__globals__["run_question_metrics_profile"]
    profile_source = inspect.getsource(profile)
    final_metrics = profile.__globals__["_handle_final_metrics"]
    final_source = inspect.getsource(final_metrics)

    assert "pending_stage_aggregate" in profile_source
    assert "if pending_stage_aggregate:" in profile_source
    assert "_verify_aggregate_metrics(" in profile_source
    assert "_handle_final_metrics(" in profile_source
    assert "FINAL_DIALOG_POINTS_CONFIRMED" in final_source
    assert "await scenario_page.try_read_final_result()" in final_source
    assert "await scenario_page.read_final_performance()" in final_source
    assert "FINAL_DIALOG_ACCURACY_CONFIRMED" in final_source
    assert "FINAL_DIALOG_SPEED_CONFIRMED" in final_source
    assert "FINAL_DIALOG_POINTS_AND_RANK_NOT_DISPLAYED" not in final_source
    assert "await scenario_page.close_final_dialog()" in final_source
    assert "_verify_aggregate_metrics(" in final_source
    assert "matrix-congratulation:visible label:visible" in (performance_source)
    assert "скорост.*точност" in performance_source


@pytest.mark.asyncio
async def test_leaderboard_comparison_reuses_trainer_dom_reader(
    monkeypatch,
) -> None:
    events: list[str] = []
    leaderboard = SimpleNamespace(
        rank=3,
        username="playwright_user_2",
        points=125,
        accuracy_percent=59,
        speed_percent=95,
        completed_scenarios=1,
        completed_tasks=11,
    )
    profile = SimpleNamespace(
        rank=3,
        points=125,
        accuracy_percent=59,
        speed_percent=95,
    )
    dashboard = SimpleNamespace(
        rank=3,
        points=125,
        accuracy_percent=59,
        speed_percent=95,
        completed_tasks=8,
        skipped_tasks=3,
    )

    class FakeStatisticsPage:
        def __init__(self, page) -> None:
            assert page is page_marker

        async def open_leaderboard(self):
            events.append("open_leaderboard")
            return dialog_marker

        async def try_read_leaderboard_user(self, **payload):
            assert payload["username"] == "playwright_user_2"
            assert payload["dialog"] is dialog_marker
            events.append("read_leaderboard")
            return leaderboard

        async def close_leaderboard(self, dialog) -> None:
            assert dialog is dialog_marker
            events.append("close_leaderboard")

        async def open_profile_statistics(self) -> None:
            events.append("open_profile")

        async def wait_for_profile_statistics(self, **payload):
            assert payload["leaderboard"] is leaderboard
            events.append("read_profile")
            return profile

        async def read_dashboard_statistics(self, **_payload):
            events.append("read_dashboard")
            return dashboard

    page_marker = object()
    dialog_marker = object()
    monkeypatch.setattr(
        trainer_ctf_bridge,
        "load_trainer_ctf_api",
        lambda: SimpleNamespace(
            matrix_statistics_page=FakeStatisticsPage,
            accuracy_percentages_are_compatible=(
                lambda **payload: (
                    payload
                    == {
                        "completed_tasks": 8,
                        "skipped_tasks": 3,
                        "profile_accuracy_percent": 59,
                        "leaderboard_accuracy_percent": 59,
                    }
                )
            ),
        ),
    )

    await verify_test_drive_leaderboard(
        page=page_marker,
        username="playwright_user_2",
        expected_total_tasks=11,
    )

    assert events == [
        "open_leaderboard",
        "read_leaderboard",
        "close_leaderboard",
        "open_profile",
        "read_profile",
        "read_dashboard",
    ]
