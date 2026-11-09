from __future__ import annotations

import importlib
import sys
import types
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

import settings
from models import CtfScenarioContent, TestDriveUser
from playwright.async_api import Page
from results import ResultCollector
from utils.logging import compact_exception_message, log_step

_TRAINER_IMPORT_LOCK = Lock()
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_TRAINER_COMMON_ROOT = _REPOSITORY_ROOT / "playwright_trainer" / "ctf" / "common"


class ExistingCtfProgressError(AssertionError):
    """CTF уже содержит завершенные/solution-карточки пользователя."""


class CtfContentDomMismatchError(AssertionError):
    """Количество CTF-вопросов в GitLab и текущем DOM различается."""


@dataclass(frozen=True)
class CtfResumeState:
    completed_questions: int
    total_questions: int
    active_stage: int | None
    resolved_local_questions: tuple[int, ...]


@dataclass(frozen=True)
class TrainerCtfApi:
    run_profile: Any
    matrix_scenario_page: type
    matrix_statistics_page: type
    profile_runtime: type
    blocking_profile_error: type
    scenario_definition: type
    scenario_stage: type
    scenario_question: type
    scenario_hint: type
    test_user: type
    accuracy_percentages_are_compatible: Any


class TrainerResultCollectorAdapter:
    """Переносит результаты профилей тренажера в summary test-drive."""

    def __init__(
        self,
        *,
        collector: ResultCollector,
        user: TestDriveUser,
    ) -> None:
        self._collector = collector
        self._user = user
        self.failed = False

    @property
    def results(self) -> list:
        return []

    def record_pass(self, **payload) -> None:
        self._collector.record_pass(
            username=payload["username"],
            profile=payload["profile"],
            action=payload["action"],
            message=self._message(payload),
            section=settings.CTF_SECTION_ID,
            step=payload.get("stage"),
        )

    def record_fail(self, **payload) -> None:
        self.failed = True
        self._collector.record_fail(
            username=payload["username"],
            profile=payload["profile"],
            action=payload["action"],
            message=self._message(payload),
            section=settings.CTF_SECTION_ID,
            step=payload.get("stage"),
            screenshot=payload.get("screenshot"),
        )

    def record_error(self, **payload) -> None:
        self.failed = True
        self._collector.record_error(
            username=payload["username"],
            profile=payload["profile"],
            action=payload["action"],
            message=self._message(payload),
            section=settings.CTF_SECTION_ID,
            step=payload.get("stage"),
            screenshot=payload.get("screenshot"),
        )

    def record_skip(self, **payload) -> None:
        self._collector.record_skip(
            username=payload["username"],
            profile=payload["profile"],
            action=payload["action"],
            message=self._message(payload),
            section=settings.CTF_SECTION_ID,
            step=payload.get("stage"),
            screenshot=payload.get("screenshot"),
        )

    def has_failures_or_errors(self) -> bool:
        return self.failed

    async def capture_failure_screenshot(
        self,
        *,
        page: Page | None,
        stage: int | None,
        local_question: int | None,
        global_question: int | None,
        action: str,
        status,
    ) -> str | None:
        status_value = getattr(status, "value", str(status))
        return await self._collector.capture_failure_screenshot(
            page=page,
            username=self._user.username,
            action=action,
            status=status_value,
            section=settings.CTF_SECTION_ID,
            step=stage,
        )

    @staticmethod
    def _message(payload: dict) -> str:
        question = payload.get("global_question")
        if question is None:
            question = payload.get("local_question")
        prefix = f"CTF Q{question}: " if question is not None else ""
        return prefix + str(payload["message"])


@lru_cache(maxsize=1)
def load_trainer_ctf_api() -> TrainerCtfApi:
    """Изолированно загружает общий CTF-код, не подменяя test-drive modules."""

    if not _TRAINER_COMMON_ROOT.is_dir():
        raise RuntimeError(f"Не найден общий CTF-код тренажера: {_TRAINER_COMMON_ROOT}")

    with _TRAINER_IMPORT_LOCK:
        saved_modules = {
            name: module
            for name, module in sys.modules.items()
            if name == "common" or name.startswith("common.")
        }
        for name in saved_modules:
            sys.modules.pop(name, None)

        common_package = types.ModuleType("common")
        common_package.__path__ = [str(_TRAINER_COMMON_ROOT)]
        common_package.__package__ = "common"
        common_package.__file__ = str(_TRAINER_COMMON_ROOT)
        sys.modules["common"] = common_package

        try:
            trainer_settings = importlib.import_module("common.settings")
            _configure_trainer_settings(trainer_settings)

            dispatcher = importlib.import_module("common.profiles.dispatcher")
            matrix_page = importlib.import_module("common.pages.matrix_scenario_page")
            statistics_page = importlib.import_module(
                "common.pages.matrix_statistics_page"
            )
            runtime = importlib.import_module("common.services.profile_runtime")
            question_metrics = importlib.import_module(
                "common.profiles.question_metrics"
            )
            question_metrics_service = importlib.import_module(
                "common.services.question_metrics"
            )
            _disable_test_drive_administrator_statistics(question_metrics)
            scenario_models = importlib.import_module("common.models.scenario")
            user_models = importlib.import_module("common.models.user")
            return TrainerCtfApi(
                run_profile=dispatcher.run_profile,
                matrix_scenario_page=matrix_page.MatrixScenarioPage,
                matrix_statistics_page=(statistics_page.MatrixStatisticsPage),
                profile_runtime=runtime.ProfileRuntime,
                blocking_profile_error=runtime.BlockingProfileError,
                scenario_definition=scenario_models.ScenarioDefinition,
                scenario_stage=scenario_models.ScenarioStage,
                scenario_question=scenario_models.ScenarioQuestion,
                scenario_hint=scenario_models.ScenarioHint,
                test_user=user_models.TestUser,
                accuracy_percentages_are_compatible=(
                    question_metrics_service.accuracy_percentages_are_compatible
                ),
            )
        finally:
            for name in tuple(sys.modules):
                if name == "common" or name.startswith("common."):
                    sys.modules.pop(name, None)
            sys.modules.update(saved_modules)


async def run_specialized_ctf_profile(
    *,
    page: Page,
    content: CtfScenarioContent,
    ctf_case_id: str,
    user: TestDriveUser,
    collector: ResultCollector,
) -> bool:
    """Запускает один из четырех профилей тренажера внутри CTF test-drive."""

    from pages.ctf_scenario_page import CtfScenarioPage

    api = load_trainer_ctf_api()
    result_adapter = TrainerResultCollectorAdapter(
        collector=collector,
        user=user,
    )
    resumed_stage_card_counts: dict[int, int] = {}

    class TestDriveMatrixScenarioPage(api.matrix_scenario_page):
        async def verify_clean_progress(
            self,
            username: str,
            total_questions: int,
        ) -> None:
            try:
                await super().verify_clean_progress(
                    username,
                    total_questions,
                )
            except AssertionError as exc:
                if "already has progress" not in str(exc):
                    raise
                raise ExistingCtfProgressError(
                    f"Пользователь {username} уже имеет прогресс CTF; "
                    "для профильного прогона требуется чистое состояние"
                ) from exc

            resolved_cards = await self.visible_task_cards().evaluate_all(
                r"""
                cards => cards.filter(card => {
                    const text = (card.innerText || "")
                        .replace(/\u00a0/g, " ");
                    const resolvedState = card.matches(
                        ".completed, .hasError, [data-state='success'], " +
                        "[data-status='success']"
                    ) || Boolean(card.querySelector(
                        ".scenario-tasks-card.completed, " +
                        ".scenario-tasks-card.hasError, " +
                        ".kbq-icon_success, .kbq-icon-success, " +
                        ".kbq-icon-check, [data-state='success'], " +
                        "[data-status='success'], i.kbq-error, i[color='error']"
                    ));
                    const answerShown = /(^|\n)\s*Ответ:\s*\S/im.test(text);
                    const scoreShown = /\b\d+\s*балл(?:а|ов)?\b/i.test(text);
                    return resolvedState || answerShown || scoreShown;
                }).length
                """
            )
            if resolved_cards:
                raise ExistingCtfProgressError(
                    f"Пользователь {username} имеет {resolved_cards} "
                    "resolved/solution-карточек CTF при индикаторе 0%; "
                    "для профильного прогона требуется чистое состояние"
                )

        async def open_tasks_and_results(self) -> None:
            await CtfScenarioPage(self.page).open_tasks()

        async def ensure_tasks_and_results_open(self) -> None:
            if await self.visible_task_cards().count() > 0:
                return
            await self.open_tasks_and_results()

        async def ensure_stage_actionbar_open(self) -> None:
            await CtfScenarioPage(self.page)._ensure_actionbar_open()

        async def verify_stage_opened(
            self,
            stage_number: int,
            expected_questions: int | None = None,
        ) -> None:
            await super().verify_stage_opened(
                stage_number,
                resumed_stage_card_counts.get(
                    stage_number,
                    expected_questions,
                ),
            )
            if user.test_profile == "stage_statuses":
                await self.ensure_stage_actionbar_open()

        async def close_final_dialog(self) -> None:
            if user.test_profile != "question_metrics":
                await super().close_final_dialog()
                return

            await CtfScenarioPage(self.page).handle_final_feedback(
                settings.FINAL_FEEDBACK_TEXT
            )

    trainer_page = TestDriveMatrixScenarioPage(page)
    trainer_user = _build_trainer_user(api, user)
    trainer_scenario = _build_trainer_scenario(
        api,
        content=content,
        scenario_url=page.url,
    )
    runtime = api.profile_runtime(
        user=trainer_user,
        collector=result_adapter,
    )
    ctf_page = CtfScenarioPage(page)
    ctf_was_already_completed = False

    try:
        await ctf_page.open_tasks()
        await _wait_for_ctf_progress_state_stable(
            page=page,
            total_questions=content.total_questions,
        )
        resume_state = await _read_ctf_resume_state(
            page=page,
            total_questions=content.total_questions,
        )
        if resume_state.completed_questions:
            if resume_state.completed_questions >= resume_state.total_questions:
                ctf_was_already_completed = True
                result_adapter.record_pass(
                    username=user.username,
                    profile=user.test_profile,
                    stage=content.stages[-1].stage,
                    local_question=None,
                    global_question=None,
                    action="CTF_ALREADY_COMPLETED_CONFIRMED",
                    message=(
                        "CTF уже полностью завершён: "
                        f"{resume_state.completed_questions} / "
                        f"{resume_state.total_questions}; повторная "
                        "отправка ответов не требуется"
                    ),
                )
            else:
                active_stage = next(
                    stage
                    for stage in trainer_scenario.stages
                    if stage.stage == resume_state.active_stage
                )
                await _open_ctf_stage_for_resume(
                    page=page,
                    stage_number=active_stage.stage,
                    first_global_question=(active_stage.questions[0].global_order),
                )
                resume_state = await _read_ctf_resume_state(
                    page=page,
                    total_questions=content.total_questions,
                )
                trainer_scenario, resumed_stage_card_counts = (
                    _build_resumed_question_metrics_scenario(
                        api=api,
                        scenario=trainer_scenario,
                        state=resume_state,
                    )
                )
                result_adapter.record_pass(
                    username=user.username,
                    profile=user.test_profile,
                    stage=resume_state.active_stage,
                    local_question=None,
                    global_question=None,
                    action="CTF_EXISTING_PROGRESS_RESUMED",
                    message=(
                        "CTF продолжен без сброса: уже завершено "
                        f"{resume_state.completed_questions} / "
                        f"{resume_state.total_questions}, активный этап "
                        f"{resume_state.active_stage}"
                    ),
                )
        else:
            await trainer_page.verify_clean_progress(
                user.username,
                content.total_questions,
            )
            result_adapter.record_pass(
                username=user.username,
                profile=user.test_profile,
                stage=content.stages[0].stage,
                local_question=None,
                global_question=None,
                action="CTF_CLEAN_PROGRESS_CONFIRMED",
                message=(
                    "Перед профильным прохождением CTF подтвержден "
                    f"чистый прогресс 0 / {content.total_questions}"
                ),
            )
        if not ctf_was_already_completed:
            await api.run_profile(
                scenario_page=trainer_page,
                scenario=trainer_scenario,
                runtime=runtime,
            )
    except ExistingCtfProgressError as exc:
        await _record_profile_exception(
            adapter=result_adapter,
            user=user,
            page=trainer_page.page,
            action="CTF_EXISTING_PROGRESS_DETECTED",
            message=str(exc),
            stage=None,
            local_question=None,
            global_question=None,
        )
        return False
    except CtfContentDomMismatchError as exc:
        await _record_profile_exception(
            adapter=result_adapter,
            user=user,
            page=trainer_page.page,
            action="CTF_CONTENT_DOM_MISMATCH",
            message=str(exc),
            stage=None,
            local_question=None,
            global_question=None,
        )
        return False
    except api.blocking_profile_error as exc:
        await _record_profile_exception(
            adapter=result_adapter,
            user=user,
            page=trainer_page.page,
            action=exc.action,
            message=exc.message,
            stage=exc.stage,
            local_question=exc.local_question,
            global_question=exc.global_question,
        )
        return False
    except Exception as exc:
        current_question = runtime.current_question
        await _record_profile_exception(
            adapter=result_adapter,
            user=user,
            page=trainer_page.page,
            action="CTF_PROFILE_UNEXPECTED_ERROR",
            message=compact_exception_message(exc),
            stage=runtime.current_stage,
            local_question=(
                current_question.local_question
                if current_question is not None
                else None
            ),
            global_question=(
                current_question.global_order if current_question is not None else None
            ),
        )
        return False

    result_adapter.record_pass(
        username=user.username,
        profile=user.test_profile,
        stage=(runtime.current_stage or content.stages[-1].stage),
        local_question=None,
        global_question=None,
        action="CTF_TRAINER_PROFILE_COMPLETED",
        message=("CTF завершен по логике одноименного профиля тренажера"),
    )

    feedback_ok = await _handle_profile_final_dialog(
        page=trainer_page.page,
        user=user,
        collector=collector,
    )
    reopened_ok = await _reopen_test_drive_after_statistics_profile(
        page=trainer_page.page,
        ctf_case_id=ctf_case_id,
        user=user,
        collector=collector,
    )
    if result_adapter.failed:
        log_step(
            f"[CTF][PROFILE {user.test_profile}] Отдельные проверки CTF "
            "завершились с FAILED/ERROR; CTF при этом завершен, поэтому "
            "обязательный post-flow test-drive будет продолжен"
        )
    return feedback_ok and reopened_ok


async def verify_test_drive_leaderboard(
    *,
    page: Page,
    username: str,
    expected_total_tasks: int,
) -> None:
    """Сверяет итоговую статистику всех basic- и CTF-заданий Test Drive."""

    trainer_api = load_trainer_ctf_api()
    statistics_page = trainer_api.matrix_statistics_page(page)
    dialog = await statistics_page.open_leaderboard()
    try:
        leaderboard = await statistics_page.try_read_leaderboard_user(
            username=username,
            dialog=dialog,
            timeout_ms=settings.CTF_METRICS_TIMEOUT_MS,
        )
    finally:
        await statistics_page.close_leaderboard(dialog)

    if leaderboard is None:
        log_step(
            f"[LEADERBOARD] Строка {username} отсутствует; пустой результат допустим"
        )
        return

    await statistics_page.open_profile_statistics()
    profile = await statistics_page.wait_for_profile_statistics(
        leaderboard=leaderboard,
        timeout_ms=settings.CTF_METRICS_TIMEOUT_MS,
    )
    dashboard = await statistics_page.read_dashboard_statistics(
        timeout_ms=settings.CTF_METRICS_TIMEOUT_MS,
    )

    mismatches: list[str] = []
    if leaderboard.rank != profile.rank:
        mismatches.append(f"место: рейтинг={leaderboard.rank}, профиль={profile.rank}")

    dashboard_total_tasks = dashboard.completed_tasks + dashboard.skipped_tasks
    if leaderboard.completed_tasks == dashboard_total_tasks:
        accuracy_compatible = trainer_api.accuracy_percentages_are_compatible(
            completed_tasks=dashboard.completed_tasks,
            skipped_tasks=dashboard.skipped_tasks,
            profile_accuracy_percent=profile.accuracy_percent,
            leaderboard_accuracy_percent=(leaderboard.accuracy_percent),
        )
        if not accuracy_compatible:
            mismatches.append(
                "точность профиля и рейтинга не совпала при "
                "едином знаменателе: "
                f"профиль={profile.accuracy_percent}, рейтинг="
                f"{leaderboard.accuracy_percent}, знаменатель="
                f"{dashboard.completed_tasks}+"
                f"{dashboard.skipped_tasks}={dashboard_total_tasks}"
            )
        else:
            log_step(
                "[LEADERBOARD][ACCURACY] Единая формула подтверждена: "
                f"профиль=рейтинг={profile.accuracy_percent}% = S/("
                f"{dashboard.completed_tasks}+"
                f"{dashboard.skipped_tasks})"
            )
    else:
        mismatches.append(
            "количество учтенных заданий не совпало: "
            f"рейтинг={leaderboard.completed_tasks}, "
            f"профиль={dashboard_total_tasks} "
            f"(выполнено={dashboard.completed_tasks}, "
            f"пропущено={dashboard.skipped_tasks})"
        )
    for title, leaderboard_value, profile_value, dashboard_value in (
        (
            "баллы",
            leaderboard.points,
            profile.points,
            dashboard.points,
        ),
        (
            "скорость",
            leaderboard.speed_percent,
            profile.speed_percent,
            dashboard.speed_percent,
        ),
    ):
        if not (leaderboard_value == profile_value == dashboard_value):
            mismatches.append(
                f"{title}: рейтинг={leaderboard_value}, "
                f"профиль={profile_value}, "
                f"dashboard={dashboard_value}"
            )

    if dashboard_total_tasks != expected_total_tasks:
        mismatches.append(
            "общее количество заданий Test Drive неверно: "
            f"выполнено={dashboard.completed_tasks}, "
            f"пропущено={dashboard.skipped_tasks}, "
            f"всего={dashboard_total_tasks}, "
            f"ожидалось basic+CTF={expected_total_tasks}"
        )
    if mismatches:
        raise AssertionError(
            "Результаты пользователя в рейтинге не совпали с "
            "итоговой статистикой: " + "; ".join(mismatches)
        )

    log_step(
        f"[LEADERBOARD] {username}: "
        f"место={leaderboard.rank}, "
        f"баллы={leaderboard.points}, "
        f"точность рейтинга={leaderboard.accuracy_percent}, "
        f"точность профиля={profile.accuracy_percent}, "
        f"скорость={leaderboard.speed_percent}, "
        f"сценарии={leaderboard.completed_scenarios}, "
        f"задания={leaderboard.completed_tasks} "
        f"(выполнено={dashboard.completed_tasks}, "
        f"пропущено={dashboard.skipped_tasks})"
    )


def _disable_test_drive_administrator_statistics(
    question_metrics_module: Any,
) -> None:
    async def skip_administrator_statistics(**_payload: Any) -> None:
        return None

    question_metrics_module._verify_administrator_statistics = (
        skip_administrator_statistics
    )


def _configure_trainer_settings(trainer_settings: Any) -> None:
    trainer_settings.BASE_URL = settings.BASE_URL
    trainer_settings.LOGIN_URL = settings.LOGIN_URL
    trainer_settings.DEFAULT_TIMEOUT_MS = settings.DEFAULT_TIMEOUT_MS
    trainer_settings.NETWORK_TIMEOUT_MS = settings.NETWORK_TIMEOUT_MS
    trainer_settings.ANSWER_STATE_TIMEOUT_MS = settings.CTF_ANSWER_STATE_TIMEOUT_MS
    trainer_settings.METRICS_UPDATE_TIMEOUT_MS = settings.CTF_METRICS_TIMEOUT_MS
    trainer_settings.STAGE_TRANSITION_TIMEOUT_MS = settings.CTF_TRANSITION_TIMEOUT_MS
    trainer_settings.QUESTION_METRICS_STATE_FILE = settings.QUESTION_METRICS_STATE_FILE
    trainer_settings.QUESTION_METRICS_TRUST_DOM_VALUES = True


def _build_trainer_user(
    api: TrainerCtfApi,
    user: TestDriveUser,
):
    return api.test_user(
        id=user.id,
        username=user.username,
        password=user.password,
        enabled=user.enabled,
        test_profile=user.test_profile,
    )


def _build_trainer_scenario(
    api: TrainerCtfApi,
    *,
    content: CtfScenarioContent,
    scenario_url: str,
):
    stages = tuple(
        api.scenario_stage(
            stage=stage.stage,
            questions=tuple(
                api.scenario_question(
                    local_question=question.local_order,
                    global_order=question.global_order,
                    answer=question.answer,
                    wrong_answer=question.wrong_answer,
                    points=question.points,
                    duration_seconds=question.duration_seconds,
                    hints=tuple(
                        api.scenario_hint(
                            order=hint.order,
                            penalty_points=hint.penalty_points,
                            text=hint.text,
                        )
                        for hint in question.hints
                    ),
                )
                for question in stage.questions
            ),
        )
        for stage in content.stages
    )
    return api.scenario_definition(
        product=settings.PRODUCT_NAME,
        scenario_url=scenario_url,
        case_name=f"{settings.PRODUCT_NAME} {content.source.case_id}",
        stages=stages,
    )


async def _read_ctf_resume_state(
    *,
    page: Page,
    total_questions: int,
) -> CtfResumeState:
    payload = await page.evaluate(
        r"""
        (totalQuestions) => {
            const visible = (element) => Boolean(
                element
                && getComputedStyle(element).display !== "none"
                && getComputedStyle(element).visibility !== "hidden"
                && element.getClientRects().length > 0
            );
            const stageRows = Array.from(document.querySelectorAll(
                ".actionbar__step-container"
            )).filter(visible);
            const stages = stageRows.map((row) => {
                const text = row.innerText || "";
                const stageMatch = text.match(/Этап\s*(\d+)/i);
                const counterMatch = text.match(/(\d+)\s*\/\s*(\d+)/);
                return {
                    stage: stageMatch ? Number(stageMatch[1]) : null,
                    completed: counterMatch ? Number(counterMatch[1]) : 0,
                    total: counterMatch ? Number(counterMatch[2]) : 0,
                };
            }).filter((stage) => Number.isInteger(stage.stage));
            const completedQuestions = stages.reduce(
                (sum, stage) => sum + stage.completed,
                0
            );
            const activeStage = stages.find(
                (stage) => stage.completed < stage.total
            )?.stage ?? null;

            const resolved = Array.from(document.querySelectorAll(
                "[id^='matrix_scenarios_task_']"
            )).filter(visible).filter((card) => {
                const root = card.querySelector(
                    ".scenario-tasks-card"
                ) || card;
                const completed = root.classList.contains("completed")
                    || Boolean(card.querySelector(
                        ".scenario-tasks-card.completed, "
                        + "[data-state='success'], "
                        + "[data-status='success'], "
                        + ".kbq-icon_success, .kbq-icon-success, "
                        + ".kbq-icon-check"
                    ));
                const solution = Boolean(card.querySelector(
                    "img[src*='forbidden.svg']"
                ));
                return completed || solution;
            }).map((card) => {
                const match = card.id.match(/_(\d+)$/);
                return match ? Number(match[1]) + 1 : null;
            }).filter(Number.isInteger);

            return {
                completedQuestions,
                activeStage,
                resolvedLocalQuestions: resolved,
            };
        }
        """,
        total_questions,
    )
    return CtfResumeState(
        completed_questions=int(payload["completedQuestions"]),
        total_questions=total_questions,
        active_stage=payload["activeStage"],
        resolved_local_questions=tuple(
            int(value) for value in payload["resolvedLocalQuestions"]
        ),
    )


async def _wait_for_ctf_progress_state_stable(
    *,
    page: Page,
    total_questions: int,
) -> None:
    state_handle = await page.wait_for_function(
        r"""
        () => {
            const visible = (element) => Boolean(
                element
                && getComputedStyle(element).display !== "none"
                && getComputedStyle(element).visibility !== "hidden"
                && element.getClientRects().length > 0
            );
            const stageRows = Array.from(document.querySelectorAll(
                ".actionbar__step-container"
            )).filter(visible);
            const counters = stageRows.map((row, index) => {
                const stageMatch = (row.innerText || "").match(
                    /Этап\s*(\d+)/i
                );
                const match = (row.innerText || "").match(
                    /(\d+)\s*\/\s*(\d+)/
                );
                return match
                    ? {
                        stage: stageMatch
                            ? Number(stageMatch[1])
                            : index + 1,
                        completed: Number(match[1]),
                        total: Number(match[2]),
                    }
                    : null;
            }).filter(Boolean);
            if (!counters.length) return false;

            const completed = counters.reduce(
                (sum, counter) => sum + counter.completed,
                0
            );
            const total = counters.reduce(
                (sum, counter) => sum + counter.total,
                0
            );

            const cards = Array.from(document.querySelectorAll(
                "[id^='matrix_scenarios_task_']"
            )).filter(visible);
            if (!cards.length) return false;

            const snapshot = JSON.stringify({
                completed,
                counters,
                cardCount: cards.length,
                cardText: cards.map((card) => (
                    card.innerText || ""
                ).slice(0, 80)),
            });
            const state = window.__ptCtfStableProgress;
            if (!state || state.snapshot !== snapshot) {
                window.__ptCtfStableProgress = {
                    snapshot,
                    since: performance.now(),
                };
                return false;
            }
            if (performance.now() - state.since < 500) return false;
            return {
                completed,
                total,
                counters,
                visibleCardCount: cards.length,
            };
        }
        """,
        timeout=max(settings.DOM_TIMEOUT_MS, 30_000),
    )
    payload = await state_handle.json_value()
    dom_total = int(payload["total"])
    if dom_total == total_questions:
        return

    stage_counts = ", ".join(
        (
            f"этап {int(counter['stage'])}: "
            f"{int(counter['completed'])}/{int(counter['total'])}"
        )
        for counter in payload["counters"]
    )
    raise CtfContentDomMismatchError(
        "Состав CTF в интерфейсе не совпадает со свежим GitLab-контентом: "
        f"GitLab={total_questions} вопросов, DOM={dom_total}; "
        f"{stage_counts}; видимых карточек текущего этапа="
        f"{int(payload['visibleCardCount'])}. "
        "Обновите exercises.json либо стенд, чтобы составы совпали."
    )


async def _open_ctf_stage_for_resume(
    *,
    page: Page,
    stage_number: int,
    first_global_question: int,
) -> None:
    stage_row = (
        page.locator(".actionbar__step-container:visible")
        .filter(has_text=f"Этап {stage_number}")
        .first
    )
    await stage_row.click(timeout=max(settings.CTF_TRANSITION_TIMEOUT_MS, 20_000))
    await page.wait_for_function(
        r"""
        (questionLabel) => Array.from(document.querySelectorAll(
            "[id^='matrix_scenarios_task_']"
        )).some((card) => {
            const style = getComputedStyle(card);
            return style.display !== "none"
                && style.visibility !== "hidden"
                && card.getClientRects().length > 0
                && new RegExp(`\\b${questionLabel}\\b`).test(
                    card.innerText || ""
                );
        })
        """,
        arg=f"Q{first_global_question}",
        timeout=max(settings.CTF_TRANSITION_TIMEOUT_MS, 20_000),
    )
    log_step(f"[CTF][RESUME] Открыт активный этап {stage_number}")


def _build_resumed_question_metrics_scenario(
    *,
    api: TrainerCtfApi,
    scenario: Any,
    state: CtfResumeState,
) -> tuple[Any, dict[int, int]]:
    if state.active_stage is None:
        raise ExistingCtfProgressError(
            "Не удалось определить активный этап CTF для продолжения"
        )

    stages: list[Any] = []
    stage_card_counts: dict[int, int] = {}
    for stage in scenario.stages:
        if stage.stage < state.active_stage:
            continue
        questions = stage.questions
        if stage.stage == state.active_stage:
            stage_card_counts[stage.stage] = len(stage.questions)
            questions = tuple(
                question
                for question in stage.questions
                if question.local_question not in state.resolved_local_questions
            )
        if not questions:
            continue
        stages.append(
            api.scenario_stage(
                stage=stage.stage,
                questions=tuple(questions),
            )
        )

    if not stages:
        raise ExistingCtfProgressError(
            "В активном и последующих этапах CTF нет нерешенных вопросов"
        )

    resumed = api.scenario_definition(
        product=scenario.product,
        scenario_url=scenario.scenario_url,
        case_name=scenario.case_name,
        stages=tuple(stages),
    )
    log_step(
        "[CTF][RESUME] Продолжаем с этапа "
        f"{state.active_stage}; уже resolved в текущем этапе: "
        f"{state.resolved_local_questions or 'нет'}"
    )
    return resumed, stage_card_counts


async def _record_profile_exception(
    *,
    adapter: TrainerResultCollectorAdapter,
    user: TestDriveUser,
    page: Page | None,
    action: str,
    message: str,
    stage: int | None,
    local_question: int | None,
    global_question: int | None,
) -> None:
    screenshot = await adapter.capture_failure_screenshot(
        page=page,
        stage=stage,
        local_question=local_question,
        global_question=global_question,
        action=action,
        status="error",
    )
    adapter.record_error(
        username=user.username,
        profile=user.test_profile,
        stage=stage,
        local_question=local_question,
        global_question=global_question,
        action=action,
        message=message,
        screenshot=screenshot,
    )


async def _handle_profile_final_dialog(
    *,
    page: Page,
    user: TestDriveUser,
    collector: ResultCollector,
) -> bool:
    from pages.ctf_scenario_page import CtfScenarioPage

    ctf_page = CtfScenarioPage(page)
    if user.test_profile == "question_metrics":
        if await ctf_page.final_dialog_is_visible(timeout=1_000):
            try:
                await ctf_page.handle_final_feedback(settings.FINAL_FEEDBACK_TEXT)
            except Exception as exc:
                screenshot = await collector.capture_failure_screenshot(
                    page=page,
                    username=user.username,
                    action="CTF_FINAL_FEEDBACK_HANDLED",
                    status="error",
                    section=settings.CTF_SECTION_ID,
                    step=None,
                )
                collector.record_error(
                    username=user.username,
                    profile=user.test_profile,
                    action="CTF_FINAL_FEEDBACK_HANDLED",
                    message=(
                        "Финальное окно профильного CTF не обработано: "
                        f"{compact_exception_message(exc)}"
                    ),
                    section=settings.CTF_SECTION_ID,
                    step=None,
                    screenshot=screenshot,
                )
                return False
        return True

    if not await ctf_page.final_dialog_is_visible(timeout=1_000):
        return True

    try:
        await ctf_page.handle_final_feedback(settings.FINAL_FEEDBACK_TEXT)
    except Exception as exc:
        screenshot = await collector.capture_failure_screenshot(
            page=page,
            username=user.username,
            action="CTF_FINAL_FEEDBACK_HANDLED",
            status="error",
            section=settings.CTF_SECTION_ID,
            step=None,
        )
        collector.record_error(
            username=user.username,
            profile=user.test_profile,
            action="CTF_FINAL_FEEDBACK_HANDLED",
            message=(
                "Финальное окно профильного CTF не обработано: "
                f"{compact_exception_message(exc)}"
            ),
            section=settings.CTF_SECTION_ID,
            step=None,
            screenshot=screenshot,
        )
        return False

    collector.record_pass(
        username=user.username,
        profile=user.test_profile,
        action="CTF_FINAL_FEEDBACK_HANDLED",
        message="Финальное окно профильного CTF обработано",
        section=settings.CTF_SECTION_ID,
        step=None,
    )
    log_step(f"[CTF][PROFILE {user.test_profile}] Финальное окно обработано")
    return True


async def _reopen_test_drive_after_statistics_profile(
    *,
    page: Page,
    ctf_case_id: str,
    user: TestDriveUser,
    collector: ResultCollector,
) -> bool:
    if user.test_profile != "question_metrics":
        return True

    scenario_page_module = importlib.import_module(settings.SCENARIO_PAGE_MODULE)
    scenario_page_class = getattr(
        scenario_page_module,
        settings.SCENARIO_PAGE_CLASS,
    )

    try:
        await scenario_page_class(page).open()
    except Exception as exc:
        screenshot = await collector.capture_failure_screenshot(
            page=page,
            username=user.username,
            action="CTF_RETURNED_TO_MODULES_AFTER_FINAL_METRICS",
            status="error",
            section=settings.CTF_SECTION_ID,
            step=None,
        )
        collector.record_error(
            username=user.username,
            profile=user.test_profile,
            action="CTF_RETURNED_TO_MODULES_AFTER_FINAL_METRICS",
            message=(
                "После итоговой статистики не удалось вернуться "
                "к модулям test-drive: "
                f"{compact_exception_message(exc)}"
            ),
            section=settings.CTF_SECTION_ID,
            step=None,
            screenshot=screenshot,
        )
        return False

    collector.record_pass(
        username=user.username,
        profile=user.test_profile,
        action="CTF_RETURNED_TO_MODULES_AFTER_FINAL_METRICS",
        message=(
            "После общей и профильной статистики открыт basic-сценарий "
            "test-drive без повторного входа в CTF"
        ),
        section=settings.CTF_SECTION_ID,
        step=None,
    )
    return True
