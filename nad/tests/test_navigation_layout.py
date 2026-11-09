from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from content_catalog import NAD_C3_CONTENT_SOURCE
from models import BasicExercise, BasicModule, BasicScenarioContent
from pages import nad_navigation
from pages.nad_navigation import bind_content_to_layout


def _content() -> BasicScenarioContent:
    exercise = BasicExercise(12, 1, "input", "Question", (), ("answer",))
    return BasicScenarioContent(
        source=NAD_C3_CONTENT_SOURCE,
        modules=(
            BasicModule(
                1, "О Тест-драйве", "theory", None, (1,), ("Введение",), False, ()
            ),
            BasicModule(
                12,
                "Модуль",
                "ex",
                "module.html",
                (4, 5),
                ("Сценарии использования PT NAD", "Подраздел"),
                True,
                (exercise,),
            ),
            BasicModule(36, "Комплексный сценарий", "theory", None, (), (), False, ()),
        ),
        ctf_case_id="c-1",
    )


@pytest.mark.parametrize("new_structure", (False, True))
def test_runtime_layout_binds_sections_by_stable_module_id(new_structure: bool) -> None:
    content = _content()
    intro_id = 1000 if new_structure else 1
    root_id = 1002 if new_structure else 4
    child_id = 1003 if new_structure else 5
    layout = {
        1: {"title": "О базовой подготовке", "sections": [(intro_id, "Введение")]},
        12: {
            "title": "Модуль",
            "sections": [
                (root_id, "Сценарии использования PT NAD"),
                (child_id, "Подраздел"),
            ],
        },
        36: {"title": "Комплексный сценарий", "sections": []},
    }

    actual = bind_content_to_layout(content, layout)

    assert actual.modules[0].ancestor_section_ids == (intro_id,)
    assert actual.modules[1].ancestor_section_ids == (root_id, child_id)
    assert actual.modules[1].ancestor_section_titles == (
        "Сценарии использования PT NAD",
        "Подраздел",
    )
    assert actual.modules[2].ancestor_section_ids == ()
    assert actual.modules[1].exercises is content.modules[1].exercises
    assert actual.total_questions == content.total_questions
    assert actual.ctf_case_id == content.ctf_case_id
    assert content.modules[1].ancestor_section_ids == (4, 5)


def test_layout_mismatch_reports_missing_and_new_modules_immediately() -> None:
    with pytest.raises(AssertionError, match=r"CONTENT_DOM_MISMATCH.*12.*99"):
        bind_content_to_layout(
            _content(),
            {
                1: {"sections": []},
                36: {"sections": []},
                99: {"sections": []},
            },
        )


def test_layout_updates_are_isolated_between_users() -> None:
    content = _content()
    layout = {
        module.step_id: {
            "sections": list(
                zip(module.ancestor_section_ids, module.ancestor_section_titles)
            ),
        }
        for module in content.modules
    }
    layout[12]["sections"] = [(1002, "Новый раздел")]

    first = bind_content_to_layout(content, layout)
    second = replace(content)

    assert first.modules[1].ancestor_section_ids == (1002,)
    assert second.modules[1].ancestor_section_ids == (4, 5)


def test_perfect_layout_keeps_available_modules_when_content_is_missing():
    content = _content()
    actual = bind_content_to_layout(
        content,
        {
            1: {"sections": [(1000, "Введение")]},
            36: {"sections": []},
            99: {"sections": []},
        },
        allow_missing=True,
    )

    assert [module.step_id for module in actual.modules] == [1, 36]
    assert [module.step_id for module in content.modules] == [1, 12, 36]


@pytest.mark.asyncio
async def test_sidebar_rendering_does_not_click_transient_expand_control(
    monkeypatch,
) -> None:
    mounted = False
    headers = Mock()
    attached_header = object()
    page = Mock()
    page.locator.return_value.first = attached_header

    async def mount_sections(**_):
        nonlocal mounted
        mounted = True

    async def visible_count():
        assert mounted, "Do not decide whether to expand before Angular mounts sections"
        return 1

    headers.count = AsyncMock(side_effect=visible_count)
    headers.filter.return_value.count = AsyncMock(return_value=0)
    rows = Mock()
    rows.evaluate_all = AsyncMock(
        return_value=[
            {
                "step_id": 1,
                "sections": [[1000, "Введение"]],
            }
        ]
    )

    def locate(selector):
        if selector == ".actionbar__section-container:visible":
            return headers
        if selector == "[id^='scenario_step_id_']:visible":
            return rows
        return SimpleNamespace(first=attached_header)

    page.locator.side_effect = locate
    monkeypatch.setattr(
        nad_navigation,
        "expect",
        lambda _: SimpleNamespace(
            to_be_attached=mount_sections,
            to_be_visible=AsyncMock(),
        ),
    )
    click = AsyncMock()
    monkeypatch.setattr(nad_navigation, "safe_click", click)

    layout = await nad_navigation.read_navigation_layout(page)

    assert layout[1]["sections"] == [[1000, "Введение"]]
    click.assert_not_awaited()
