from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, Mock

import pages.ctf_scenario_page as ctf_page_module
import pytest
from pages.ctf_scenario_page import CtfScenarioPage
from trainer_ctf_bridge import (
    CtfContentDomMismatchError,
    _wait_for_ctf_progress_state_stable,
    run_specialized_ctf_profile,
)


class _JsonHandle:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json_value(self) -> dict:
        return self._payload


class _ProgressPage:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.script = ""
        self.timeout = 0

    async def wait_for_function(self, script: str, **kwargs):
        self.script = script
        self.timeout = kwargs["timeout"]
        return _JsonHandle(self._payload)


class _FieldExpectation:
    def __init__(self, field) -> None:
        self.field = field

    async def to_have_value(self, expected: str, **_kwargs) -> None:
        assert self.field.value == expected

    async def to_be_visible(self, **_kwargs) -> None:
        return None


class _AppendingField:
    """Имитирует поле, которое добавляет ввод к уже существующему значению."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.calls: list[str] = []

    async def focus(self) -> None:
        self.calls.append("focus")

    async def scroll_into_view_if_needed(self) -> None:
        self.calls.append("scroll")

    async def click(self, **_kwargs) -> None:
        self.calls.append("click")

    async def select_text(self) -> None:
        self.calls.append("select")

    async def evaluate(self, _script: str, arg=None) -> None:
        if arg is None:
            self.calls.append("clear")
            self.value = ""
        else:
            self.calls.append("native-fill")
            self.value = arg

    async def fill(self, value: str) -> None:
        self.calls.append("fill")
        self.value += value

    async def input_value(self) -> str:
        return self.value


class _Keyboard:
    def __init__(self, field: _AppendingField) -> None:
        self.field = field

    async def insert_text(self, value: str) -> None:
        self.field.calls.append("insert-text")
        self.field.value = value


class _Page:
    def __init__(self, field: _AppendingField) -> None:
        self.keyboard = _Keyboard(field)


@pytest.mark.asyncio
async def test_open_tasks_expands_first_unresolved_card_when_tab_is_already_open(
    monkeypatch,
) -> None:
    ctf_page = CtfScenarioPage(page=object())
    cards_visible = AsyncMock(return_value=True)
    expand_first = AsyncMock()
    monkeypatch.setattr(ctf_page, "_wait_for_task_cards_visible", cards_visible)
    monkeypatch.setattr(
        ctf_page,
        "_ensure_first_unresolved_task_expanded",
        expand_first,
    )

    await ctf_page.open_tasks()

    cards_visible.assert_awaited_once_with(timeout_ms=750)
    expand_first.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_open_question_reuses_already_expanded_target(
    monkeypatch,
) -> None:
    page = Mock()
    page.evaluate = AsyncMock(return_value=123.0)
    ctf_page = CtfScenarioPage(page=page)
    task = object()
    ctf_page._handle_timer_warning = AsyncMock()
    ctf_page.task_card = Mock(return_value=task)
    ctf_page._question_is_expanded_and_ready = AsyncMock(return_value=True)
    ctf_page._collapse_open_question = AsyncMock()
    monkeypatch.setattr(
        ctf_page_module,
        "expect",
        lambda field: _FieldExpectation(field),
    )

    result = await ctf_page.open_question(0)

    assert result is task
    ctf_page._collapse_open_question.assert_not_awaited()


@pytest.mark.asyncio
async def test_ctf_correct_answer_replaces_rejected_value(
    monkeypatch,
) -> None:
    field = _AppendingField("wrong-answer")
    ctf_page = CtfScenarioPage(page=_Page(field))
    monkeypatch.setattr(
        ctf_page_module,
        "expect",
        lambda target: _FieldExpectation(target),
    )

    await ctf_page._replace_answer_field_value(field, "correct")

    assert field.value == "correct"
    assert field.calls == [
        "scroll",
        "click",
        "focus",
        "fill",
        "select",
        "insert-text",
    ]


@pytest.mark.asyncio
async def test_ctf_dom_question_mismatch_fails_immediately_with_stage_counts() -> None:
    page = _ProgressPage(
        {
            "completed": 0,
            "total": 12,
            "counters": [
                {"stage": 1, "completed": 0, "total": 3},
                {"stage": 2, "completed": 0, "total": 5},
                {"stage": 3, "completed": 0, "total": 4},
            ],
            "visibleCardCount": 3,
        }
    )

    with pytest.raises(CtfContentDomMismatchError) as raised:
        await _wait_for_ctf_progress_state_stable(
            page=page,
            total_questions=11,
        )

    message = str(raised.value)
    assert "GitLab=11 вопросов, DOM=12" in message
    assert "этап 1: 0/3" in message
    assert "этап 2: 0/5" in message
    assert "этап 3: 0/4" in message
    assert "видимых карточек текущего этапа=3" in message
    assert "total !== totalQuestions" not in page.script


@pytest.mark.asyncio
async def test_matching_ctf_dom_question_count_allows_profile_to_continue() -> None:
    page = _ProgressPage(
        {
            "completed": 0,
            "total": 11,
            "counters": [
                {"stage": 1, "completed": 0, "total": 3},
                {"stage": 2, "completed": 0, "total": 4},
                {"stage": 3, "completed": 0, "total": 4},
            ],
            "visibleCardCount": 3,
        }
    )

    await _wait_for_ctf_progress_state_stable(
        page=page,
        total_questions=11,
    )

    assert page.timeout == 30_000


def test_ctf_content_mismatch_has_a_dedicated_report_action() -> None:
    source = inspect.getsource(run_specialized_ctf_profile)

    assert "except CtfContentDomMismatchError" in source
    assert 'action="CTF_CONTENT_DOM_MISMATCH"' in source


def test_completed_specialized_ctf_always_allows_mandatory_post_flow() -> None:
    source = inspect.getsource(run_specialized_ctf_profile)

    assert "if result_adapter.failed:" in source
    assert "обязательный post-flow test-drive будет продолжен" in source
    assert "return feedback_ok and reopened_ok" in source
    assert "return not result_adapter.failed" not in source
