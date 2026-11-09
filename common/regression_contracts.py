from __future__ import annotations

import asyncio
import inspect
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

import pytest
import scenarios.runner as product_runner
import settings
from ctf_metrics import (
    accuracy_for_attempt,
    aggregate_question_metrics,
    answered_question_metrics,
    round_half_up,
    speed_bonus_for_points,
    speed_for_duration,
)
from data_loader import load_test_drive_users
from models import (
    SCENARIO_PROFILE_NAMES,
    SPECIALIZED_CTF_PROFILE_NAMES,
    SUPPORTED_PROFILES,
    BasicModule,
    BasicScenarioContent,
    CheckStatus,
    CtfScenarioContent,
    CtfStage,
)
from models import TestDriveUser as DriveUser
from pages.basic_modules_page import (
    BasicModulesPage,
    ProductInterval,
    normalize_windows_path_escaping,
)
from results import ResultCollector
from trainer_ctf_bridge import (
    TrainerResultCollectorAdapter,
    _handle_profile_final_dialog,
)
from utils.browser import _browser_args
from utils.logging import (
    bind_log_context,
    compact_exception_message,
    log_step,
    reset_log_context,
)
from utils.network import (
    perform_and_wait_for_dom_with_network_observation,
    response_is_mutation,
    response_path_is,
    same_origin_content_response,
)


@dataclass
class _Request:
    method: str = "GET"
    resource_type: str = "xhr"


@dataclass
class _Response:
    url: str
    request: _Request
    status: int = 200
    ok: bool = True


class _EventPage:
    def __init__(self) -> None:
        self.listeners: list = []

    def on(self, event: str, callback) -> None:
        assert event == "response"
        self.listeners.append(callback)

    def remove_listener(self, event: str, callback) -> None:
        assert event == "response"
        self.listeners.remove(callback)

    def emit_response(self, response: _Response) -> None:
        for listener in tuple(self.listeners):
            listener(response)


class _ProgressExpectation:
    async def to_be_visible(self, **_kwargs) -> None:
        return None


class _ProgressLocator:
    def __init__(self, *, text: str, style: str) -> None:
        self.text = text
        self.style = style

    @property
    def first(self):
        return self

    def locator(self, selector: str):
        if selector == "xpath=..":
            return SimpleNamespace(inner_text=self._inner_text)
        if selector == ".kbq-progress-bar__line_determinate":
            return SimpleNamespace(
                first=SimpleNamespace(get_attribute=self._get_style_attribute)
            )
        raise AssertionError(f"Unexpected progress selector: {selector}")

    async def _inner_text(self) -> str:
        return self.text

    async def _get_style_attribute(self, name: str) -> str:
        assert name == "style"
        return self.style


class _ProgressPage:
    def __init__(self, *, text: str, style: str = "") -> None:
        self.progress = _ProgressLocator(text=text, style=style)

    def locator(self, selector: str) -> _ProgressLocator:
        assert selector == "kbq-progress-bar.actionbar__progress-bar:visible"
        return self.progress


def _product_scenario_page_class():
    candidates = [
        value
        for name, value in vars(product_runner).items()
        if name.endswith("TestDriveScenarioPage") and isinstance(value, type)
    ]
    assert len(candidates) == 1
    return candidates[0]


class CommonTestDriveContract:
    """Regression contract collected independently by every product suite."""

    def test_user_loader_rejects_missing_users_array(self, tmp_path) -> None:
        users_path = tmp_path / "users.local.json"
        users_path.write_text("{}", encoding="utf-8")

        with pytest.raises(AssertionError, match="users"):
            load_test_drive_users(users_path)

    def test_user_loader_accumulates_errors_and_keeps_valid_disabled_user(
        self,
        tmp_path,
    ) -> None:
        users_path = tmp_path / "users.local.json"
        users_path.write_text(
            json.dumps(
                {
                    "users": [
                        "not-an-object",
                        {
                            "id": "missing-password",
                            "username": "broken",
                            "password": "",
                            "enabled": True,
                            "test_profile": "perfect",
                        },
                        {
                            "id": "bad-profile",
                            "username": "broken-profile",
                            "password": "secret",
                            "enabled": True,
                            "test_profile": "unknown-profile",
                        },
                        {
                            "id": "disabled-valid",
                            "username": "playwright_user_5",
                            "password": "secret",
                            "enabled": False,
                            "test_profile": "question_metrics",
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        users, errors = load_test_drive_users(users_path)

        assert [(user.id, user.enabled) for user in users] == [
            ("disabled-valid", False)
        ]
        assert [error["id"] for error in errors] == [
            "user_1",
            "missing-password",
            "bad-profile",
        ]

    def test_profile_sets_keep_all_scenario_roles_supported(self) -> None:
        assert SCENARIO_PROFILE_NAMES <= SUPPORTED_PROFILES
        assert SPECIALIZED_CTF_PROFILE_NAMES <= SCENARIO_PROFILE_NAMES
        assert "perfect" in SCENARIO_PROFILE_NAMES
        assert "perfect" not in SPECIALIZED_CTF_PROFILE_NAMES

    def test_content_models_derive_module_and_question_totals(self) -> None:
        theory = BasicModule(
            step_id=1,
            title="Theory",
            module_type="theory",
            content_filename=None,
            ancestor_section_ids=(),
            ancestor_section_titles=(),
            required_for_ctf=False,
            exercises=(),
        )
        exercise = BasicModule(
            step_id=2,
            title="Exercise",
            module_type="ex",
            content_filename=None,
            ancestor_section_ids=(),
            ancestor_section_titles=(),
            required_for_ctf=True,
            exercises=(object(), object()),  # type: ignore[arg-type]
        )
        basic = BasicScenarioContent(
            source=None,  # type: ignore[arg-type]
            modules=(theory, exercise),
            ctf_case_id="c-1",
        )
        ctf = CtfScenarioContent(
            source=None,  # type: ignore[arg-type]
            stages=(
                CtfStage(stage=1, questions=(object(), object())),  # type: ignore[arg-type]
                CtfStage(stage=2, questions=(object(),)),  # type: ignore[arg-type]
            ),
        )

        assert basic.exercise_modules == (exercise,)
        assert basic.total_questions == 2
        assert ctf.total_questions == 3
        assert basic.total_questions + ctf.total_questions == 5

    def test_final_statistics_count_basic_and_ctf_questions(self) -> None:
        finish_source = inspect.getsource(
            product_runner._finish_specialized_profile_test_drive
        )

        assert "expected_total_tasks=" in finish_source
        assert "content.total_questions + ctf_content.total_questions" in (
            finish_source
        )
        assert "expected_ctf_tasks=" not in finish_source

    @pytest.mark.parametrize(
        ("attempt", "expected"),
        [(1, 100), (2, 50), (3, 30), (4, 0), (99, 0)],
    )
    def test_accuracy_attempt_buckets(self, attempt: int, expected: int) -> None:
        assert accuracy_for_attempt(attempt) == expected

    @pytest.mark.parametrize("attempt", [0, -1])
    def test_accuracy_rejects_non_positive_attempts(self, attempt: int) -> None:
        with pytest.raises(ValueError):
            accuracy_for_attempt(attempt)

    @pytest.mark.parametrize(
        ("duration", "actual"),
        [(0, 1), (-1, 1), (1, 0), (1, -1)],
    )
    def test_speed_rejects_non_positive_times(
        self,
        duration: int,
        actual: int,
    ) -> None:
        with pytest.raises(ValueError):
            speed_for_duration(
                duration_seconds=duration,
                actual_seconds=actual,
            )

    def test_metric_rounding_bonus_boundary_and_speed_cap(self) -> None:
        assert round_half_up(Decimal("2.5")) == 3
        assert round_half_up(Decimal("-2.5")) == -3
        assert speed_for_duration(duration_seconds=100, actual_seconds=1) == 999
        assert (
            speed_bonus_for_points(
                base_points=13,
                duration_seconds=100,
                actual_seconds=99,
            )
            == 3
        )
        assert (
            speed_bonus_for_points(
                base_points=13,
                duration_seconds=100,
                actual_seconds=100,
            )
            == 0
        )

    def test_aggregate_metrics_use_total_duration_not_average_percent(self) -> None:
        fast = answered_question_metrics(
            attempt_count=1,
            duration_seconds=100,
            actual_seconds=50,
            base_points=10,
        )
        slow = answered_question_metrics(
            attempt_count=2,
            duration_seconds=100,
            actual_seconds=200,
            base_points=20,
        )

        aggregate = aggregate_question_metrics((fast, slow))

        assert aggregate.accuracy_percent == 75
        assert aggregate.speed_percent == 80
        assert aggregate.points == fast.awarded_points + slow.awarded_points

    def test_product_interval_requires_exact_dates_and_minute_prefixes(self) -> None:
        page = BasicModulesPage(object())  # type: ignore[arg-type]
        expected = ProductInterval("17.09.2026", "12:30", "17.09.2026", "15:30")

        assert str(expected) == "17.09.2026 12:30 - 17.09.2026 15:30"
        assert page._intervals_match(
            expected,
            ProductInterval(
                "17.09.2026",
                "12:30:59",
                "17.09.2026",
                "15:30:01",
            ),
        )
        assert not page._intervals_match(
            expected,
            ProductInterval(
                "16.09.2026",
                "12:30:59",
                "17.09.2026",
                "15:30:01",
            ),
        )
        assert not page._intervals_match(
            expected,
            ProductInterval(
                "17.09.2026",
                "12:31:00",
                "17.09.2026",
                "15:30:01",
            ),
        )

    @pytest.mark.asyncio
    async def test_hidden_module_navigation_uses_arrows_until_target(
        self,
        monkeypatch,
    ) -> None:
        class _VisibleArrow:
            @property
            def first(self):
                return self

            async def count(self) -> int:
                return 1

            async def is_visible(self) -> bool:
                return True

        page = SimpleNamespace(locator=lambda _selector: _VisibleArrow())
        driver = BasicModulesPage(page)  # type: ignore[arg-type]
        module = BasicModule(
            step_id=7,
            title="Target module",
            module_type="ex",
            content_filename=None,
            ancestor_section_ids=(),
            ancestor_section_titles=(),
            required_for_ctf=False,
            exercises=(),
        )
        active_step_id = 1
        transitions = iter((2, 5, 7))
        confirmed: list[tuple[int, str]] = []

        async def active_id() -> int:
            return active_step_id

        async def is_active(step_id: int, _title: str) -> bool:
            return active_step_id == step_id

        async def click_next_step(**_kwargs) -> int:
            nonlocal active_step_id
            active_step_id = next(transitions)
            return active_step_id

        async def wait_for_active(step_id: int, title: str) -> None:
            confirmed.append((step_id, title))

        monkeypatch.setattr(driver, "_active_module_step_id", active_id)
        monkeypatch.setattr(driver, "_module_is_active", is_active)
        monkeypatch.setattr(driver, "_click_next_step", click_next_step)
        monkeypatch.setattr(driver, "_wait_for_module_active", wait_for_active)

        assert await driver._navigate_hidden_module_with_arrows(module)
        assert active_step_id == module.step_id
        assert confirmed == [(module.step_id, module.title)]

    @pytest.mark.asyncio
    async def test_missing_module_status_node_restores_its_section(
        self,
        monkeypatch,
    ) -> None:
        class _MissingRoot:
            async def count(self) -> int:
                return 0

        driver = BasicModulesPage(object())  # type: ignore[arg-type]
        module = BasicModule(
            step_id=7,
            title="Target module",
            module_type="ex",
            content_filename=None,
            ancestor_section_ids=(2,),
            ancestor_section_titles=("Section",),
            required_for_ctf=False,
            exercises=(),
        )
        root = _MissingRoot()
        restored: list[int] = []

        async def ensure_visible(target: BasicModule) -> None:
            restored.append(target.step_id)

        async def read_status(target) -> str:
            assert target is root
            return "undefined"

        monkeypatch.setattr(driver, "module_step", lambda _step_id: root)
        monkeypatch.setattr(driver, "_ensure_module_visible", ensure_visible)
        monkeypatch.setattr(driver, "_read_status", read_status)

        assert await driver.read_module_status(module) == "undefined"
        assert restored == [module.step_id]

    def test_every_profile_uses_shared_safe_module_navigation(self) -> None:
        open_source = inspect.getsource(BasicModulesPage.open_module)
        answers_source = inspect.getsource(BasicModulesPage.ensure_module_for_answers)
        runner_source = inspect.getsource(product_runner._run_full_scenario)

        assert "_navigate_hidden_module_with_arrows" in open_source
        assert "await self.open_module(module)" in answers_source
        assert "modules_page.go_to_next_module(module)" in runner_source
        assert "not await _record_progress_status_check" not in runner_source
        assert {
            "perfect",
            "answers_and_hints",
            "solutions",
            "stage_statuses",
            "question_metrics",
        } <= set(SCENARIO_PROFILE_NAMES)

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (
                r"C:\\sponge-bob\\exe-zip-injector\\",
                "C:\\sponge-bob\\exe-zip-injector\\",
            ),
            ("C:\\already\\correct\\", "C:\\already\\correct\\"),
            (r"\\server\\share\\folder", r"\\server\\share\\folder"),
            (r"^C:\\regex\\.+$", r"^C:\\regex\\.+$"),
            ("ordinary answer", "ordinary answer"),
        ],
    )
    def test_windows_path_normalization_is_scoped_and_idempotent(
        self,
        source: str,
        expected: str,
    ) -> None:
        normalized = normalize_windows_path_escaping(source)

        assert normalized == expected
        assert normalize_windows_path_escaping(normalized) == expected

    def test_input_value_match_accepts_only_known_trailing_separator_loss(
        self,
    ) -> None:
        matches = BasicModulesPage._text_field_value_matches

        assert matches("C:\\tools\\runner", "C:\\tools\\runner\\")
        assert matches("exact", "exact")
        assert not matches("C:\\tools\\runne", "C:\\tools\\runner\\")
        assert not matches("regular", "regular\\")
        assert not matches("different", "expected")

    @pytest.mark.parametrize(
        "answer",
        ["UA", "ua", "AbC-19", "Ответ-21", "user@example.test"],
    )
    def test_generated_wrong_input_preserves_validation_shape(
        self, answer: str
    ) -> None:
        wrong = BasicModulesPage(object())._wrong_input_value(answer)  # type: ignore[arg-type]

        assert wrong != answer
        assert len(wrong) == len(answer)
        for original, replacement in zip(answer, wrong, strict=True):
            assert original.isalpha() == replacement.isalpha()
            assert original.isdecimal() == replacement.isdecimal()
            if original.isalpha():
                assert original.isupper() == replacement.isupper()
                assert original.islower() == replacement.islower()
            if not original.isalnum():
                assert replacement == original

    def test_response_path_ignores_query_but_checks_method(self) -> None:
        response = _Response(
            url="https://matrix.example/api/basic/exercise/submit_basic/?retry=1",
            request=_Request(method="post"),
        )

        assert response_path_is(
            response,  # type: ignore[arg-type]
            path="/api/basic/exercise/submit_basic/",
            method="POST",
        )
        assert not response_path_is(
            response,  # type: ignore[arg-type]
            path="/api/basic/exercise/submit_basic/",
            method="GET",
        )

    @pytest.mark.parametrize(
        ("method", "resource_type", "expected"),
        [
            ("POST", "xhr", True),
            ("PATCH", "fetch", True),
            ("DELETE", "document", True),
            ("GET", "xhr", False),
            ("POST", "image", False),
        ],
    )
    def test_mutation_response_filter(
        self,
        method: str,
        resource_type: str,
        expected: bool,
    ) -> None:
        response = _Response(
            url="https://matrix.example/api/action",
            request=_Request(method=method, resource_type=resource_type),
        )

        assert response_is_mutation(response) is expected  # type: ignore[arg-type]

    def test_same_origin_filter_checks_scheme_host_port_and_resource(self) -> None:
        base_url = "https://matrix.example:8443/scenarios"
        matching = _Response(
            url="https://matrix.example:8443/media/doc/page.html",
            request=_Request(resource_type="document"),
        )
        wrong_port = _Response(
            url="https://matrix.example/media/doc/page.html",
            request=_Request(resource_type="document"),
        )
        wrong_type = _Response(
            url="https://matrix.example:8443/media/image.png",
            request=_Request(resource_type="image"),
        )

        assert same_origin_content_response(  # type: ignore[arg-type]
            matching,
            base_url=base_url,
        )
        assert not same_origin_content_response(  # type: ignore[arg-type]
            wrong_port,
            base_url=base_url,
        )
        assert not same_origin_content_response(  # type: ignore[arg-type]
            wrong_type,
            base_url=base_url,
        )

    @pytest.mark.asyncio
    async def test_dom_transition_succeeds_without_optional_network_request(
        self,
    ) -> None:
        page = _EventPage()
        calls: list[str] = []

        async def action() -> str:
            calls.append("action")
            return "clicked"

        async def wait_for_dom() -> None:
            calls.append("dom")

        result, response = await perform_and_wait_for_dom_with_network_observation(
            page,  # type: ignore[arg-type]
            action,
            wait_for_dom,
            predicate=lambda _response: True,
            description="SPA transition",
        )

        assert (result, response) == ("clicked", None)
        assert calls == ["action", "dom"]
        assert page.listeners == []

    @pytest.mark.asyncio
    async def test_dom_transition_unsubscribes_and_reports_observed_http_error(
        self,
    ) -> None:
        page = _EventPage()
        response = _Response(
            url="https://matrix.example/api/action",
            request=_Request(method="POST"),
            status=503,
            ok=False,
        )

        async def action() -> None:
            page.emit_response(response)

        async def wait_for_dom() -> None:
            return None

        with pytest.raises(AssertionError, match="503"):
            await perform_and_wait_for_dom_with_network_observation(
                page,  # type: ignore[arg-type]
                action,
                wait_for_dom,
                predicate=lambda observed: observed.status == 503,
                description="failed mutation",
            )

        assert page.listeners == []

    def test_result_collector_counts_statuses_and_returns_defensive_copy(self) -> None:
        collector = ResultCollector(scenario_label="Contract scenario")
        common = {
            "username": "playwright_user_1",
            "profile": "perfect",
            "section": 1,
            "step": 2,
        }
        collector.record_pass(
            **common,
            action="PROFILE_COMPLETED",
            message="done",
        )
        collector.record_fail(**common, action="FAILED_CHECK", message="failed")
        collector.record_error(**common, action="ERROR_CHECK", message="error")
        collector.record_skip(**common, action="SKIPPED_CHECK", message="skipped")

        copied_results = collector.results
        copied_results.clear()

        assert len(collector.results) == 4
        assert collector.get_summary() == {
            "total": 4,
            "passed": 1,
            "failed": 1,
            "errors": 1,
            "skipped": 1,
        }
        assert collector.has_failures_or_errors()
        assert {result.status for result in collector.results} == set(CheckStatus)
        rendered = collector.render_text_summary()
        assert "Contract scenario" in rendered
        assert "Profiles completed:  1" in rendered

    def test_trainer_adapter_preserves_ctf_stage_question_and_failure_state(
        self,
    ) -> None:
        collector = ResultCollector(scenario_label="CTF adapter")
        user = DriveUser(
            id="u3",
            username="playwright_user_3",
            password="secret",
            enabled=True,
            test_profile="solutions",
        )
        adapter = TrainerResultCollectorAdapter(collector=collector, user=user)

        common = {
            "username": user.username,
            "profile": user.test_profile,
            "stage": 2,
            "local_question": 1,
            "global_question": 7,
        }
        adapter.record_pass(**common, action="OPENED", message="opened")
        adapter.record_skip(**common, action="OPTIONAL", message="skipped")
        adapter.record_fail(**common, action="MISMATCH", message="wrong")
        adapter.record_error(**common, action="BROKEN", message="error")

        assert adapter.has_failures_or_errors()
        assert adapter.results == []
        assert [result.status for result in collector.results] == [
            CheckStatus.PASSED,
            CheckStatus.SKIPPED,
            CheckStatus.FAILED,
            CheckStatus.ERROR,
        ]
        assert all(
            result.section == settings.CTF_SECTION_ID for result in collector.results
        )
        assert all(result.step == 2 for result in collector.results)
        assert all(
            result.message.startswith("CTF Q7: ") for result in collector.results
        )

    def test_absent_ctf_final_dialog_does_not_create_skipped_noise(self) -> None:
        source = inspect.getsource(_handle_profile_final_dialog)

        assert "CTF_FINAL_FEEDBACK_NOT_REQUIRED" not in source
        assert "if not await ctf_page.final_dialog_is_visible(timeout=1_000):" in source

    def test_browser_arguments_disable_background_throttling(
        self,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(settings, "HEADLESS", True)
        headless_args = _browser_args()

        assert (
            f"--window-size={settings.VIEWPORT_WIDTH},{settings.VIEWPORT_HEIGHT}"
            in headless_args
        )
        assert "--disable-background-timer-throttling" in headless_args
        assert "--disable-backgrounding-occluded-windows" in headless_args
        assert "--disable-renderer-backgrounding" in headless_args

        monkeypatch.setattr(settings, "HEADLESS", False)
        assert "--start-maximized" in _browser_args()

    @pytest.mark.asyncio
    async def test_log_context_is_isolated_between_async_users(self, capsys) -> None:
        async def write_for_user(username: str, profile: str) -> None:
            token = bind_log_context(username=username, profile=profile)
            try:
                await asyncio.sleep(0)
                log_step("[FLOW] isolated")
            finally:
                reset_log_context(token)

        await asyncio.gather(
            write_for_user("playwright_user_1", "perfect"),
            write_for_user("playwright_user_4", "stage_statuses"),
        )

        output = capsys.readouterr().out
        assert "playwright_user_1 | perfect | [FLOW] isolated" in output
        assert "playwright_user_4 | stage_statuses | [FLOW] isolated" in output

    def test_exception_compaction_drops_playwright_call_log_noise(self) -> None:
        error = RuntimeError(
            "Locator expected to be visible\n"
            "Actual value: hidden\n"
            "Call log:\n"
            "  - waiting for locator"
        )

        assert compact_exception_message(error) == "Locator expected to be visible"

    @pytest.mark.parametrize(
        ("text", "style", "expected"),
        [
            ("Текущий прогресс 73,7%", "", (73.7, "73,7")),
            ("Прогресс без подписи", "width: 42.1%;", (42.1, "42.1")),
        ],
    )
    @pytest.mark.asyncio
    async def test_product_progress_reader_supports_text_and_style_fallback(
        self,
        monkeypatch,
        text: str,
        style: str,
        expected: tuple[float, str],
    ) -> None:
        page_class = _product_scenario_page_class()
        page_module = sys.modules[page_class.__module__]
        monkeypatch.setattr(
            page_module,
            "expect",
            lambda _locator: _ProgressExpectation(),
        )
        scenario_page = page_class(_ProgressPage(text=text, style=style))

        assert await scenario_page.read_progress_percent() == expected

    @pytest.mark.asyncio
    async def test_product_progress_reader_rejects_missing_value(
        self,
        monkeypatch,
    ) -> None:
        page_class = _product_scenario_page_class()
        page_module = sys.modules[page_class.__module__]
        monkeypatch.setattr(
            page_module,
            "expect",
            lambda _locator: _ProgressExpectation(),
        )
        scenario_page = page_class(_ProgressPage(text="no progress", style=""))

        with pytest.raises(AssertionError):
            await scenario_page.read_progress_percent()

    def test_ctf_url_detection_matches_exact_case_parameter(self) -> None:
        page_class = _product_scenario_page_class()

        assert page_class(
            SimpleNamespace(
                url="http://matrix/scenarios?product=demo&scenario=c-10&scenarioType=ctf"
            )
        )._current_url_is_ctf("c-10")
        assert not page_class(
            SimpleNamespace(
                url="http://matrix/scenarios?product=demo&scenario=c-100&scenarioType=ctf"
            )
        )._current_url_is_ctf("c-10")
        assert not page_class(
            SimpleNamespace(url="http://matrix/scenarios?scenario=c-1")
        )._current_url_is_ctf("c-10")

    def test_result_section_uses_deepest_parent_section(self) -> None:
        module = BasicModule(
            step_id=20,
            title="Nested module",
            module_type="ex",
            content_filename=None,
            ancestor_section_ids=(1, 3, 8),
            ancestor_section_titles=("One", "Three", "Eight"),
            required_for_ctf=False,
            exercises=(),
        )

        assert product_runner._result_section(module) == 8

    def test_complex_module_allows_skipped_only_after_answer_mismatch(self) -> None:
        assert product_runner._allowed_complex_final_statuses(0) == {"completed"}
        assert product_runner._allowed_complex_final_statuses(1) == {
            "completed",
            "skipped_by_bs",
        }

    @pytest.mark.asyncio
    async def test_runner_soft_check_records_failure_without_raising(self) -> None:
        collector = ResultCollector(scenario_label="soft check")
        user = DriveUser(
            id="u1",
            username="playwright_user_1",
            password="secret",
            enabled=True,
            test_profile="perfect",
        )

        async def fail() -> None:
            raise RuntimeError("broken assertion\nCall log:\n- noisy details")

        succeeded = await product_runner._run_check(
            collector=collector,
            user=user,
            page=None,  # type: ignore[arg-type]
            action="SOFT_CHECK",
            success_message="ok",
            failure_message="not confirmed",
            operation=fail,
            section=2,
            step=7,
        )

        assert succeeded is False
        assert len(collector.results) == 1
        result = collector.results[0]
        assert result.status is CheckStatus.FAILED
        assert result.section == 2
        assert result.step == 7
        assert result.message == "not confirmed: broken assertion"

    @pytest.mark.asyncio
    async def test_runner_critical_check_records_error_without_raising(self) -> None:
        collector = ResultCollector(scenario_label="critical check")
        user = DriveUser(
            id="u1",
            username="playwright_user_1",
            password="secret",
            enabled=True,
            test_profile="perfect",
        )

        async def fail() -> None:
            raise RuntimeError("browser closed")

        succeeded = await product_runner._run_critical(
            collector=collector,
            user=user,
            page=None,  # type: ignore[arg-type]
            action="CRITICAL_CHECK",
            success_message="ok",
            failure_message="flow stopped",
            operation=fail,
        )

        assert succeeded is False
        assert collector.results[0].status is CheckStatus.ERROR
        assert collector.results[0].message == "flow stopped: browser closed"

    @pytest.mark.asyncio
    async def test_runner_binds_and_resets_context_even_when_user_flow_fails(
        self,
        monkeypatch,
        capsys,
    ) -> None:
        user = DriveUser(
            id="u4",
            username="playwright_user_4",
            password="secret",
            enabled=True,
            test_profile="stage_statuses",
        )

        async def fail_inside_context(**_kwargs) -> None:
            log_step("[INNER] before failure")
            raise RuntimeError("expected test failure")

        monkeypatch.setattr(
            product_runner,
            "_run_user_in_separate_browser",
            fail_inside_context,
        )

        with pytest.raises(RuntimeError, match="expected test failure"):
            await product_runner._run_user(
                playwright=object(),
                user=user,
                collector=ResultCollector(scenario_label="context"),
                content=None,
                ctf_content=None,
                semaphore=asyncio.Semaphore(1),
            )

        log_step("[OUTER] after failure")
        output = capsys.readouterr().out
        assert "playwright_user_4 | stage_statuses | [INNER] before failure" in output
        outer_line = next(line for line in output.splitlines() if "[OUTER]" in line)
        assert "playwright_user_4" not in outer_line
