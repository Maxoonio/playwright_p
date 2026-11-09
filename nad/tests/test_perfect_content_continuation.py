from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import settings
from content_catalog import NAD_C3_CONTENT_SOURCE
from models import BasicExercise, BasicModule, BasicScenarioContent
from pages import basic_modules_page as basic_page_module
from pages.basic_modules_page import BasicModulesPage, QuestionAnswerResult
from results import ResultCollector
from scenarios import runner


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    (
        "https://10.10.202.12/#/alerts/list?from=1793183930275&to=1793194730275&sources=2",
        "https://nad.example.org/#/dashboards/main?from=1000&to=2000",
        "https://192.168.91.12/alerts?from=3000&to=4000",
    ),
)
async def test_nad_accepts_current_matrix_product_url(monkeypatch, url):
    link = SimpleNamespace(get_attribute=AsyncMock(return_value=url))
    monkeypatch.setattr(
        basic_page_module,
        "expect",
        lambda _: SimpleNamespace(
            to_be_visible=AsyncMock(),
        ),
    )

    await BasicModulesPage(object())._verify_product_link_contract(link)


def _module(step_id, orders):
    return BasicModule(
        step_id,
        f"Module {step_id}",
        "ex",
        None,
        (1001,),
        ("Section",),
        False,
        tuple(
            BasicExercise(step_id, order, "input", "Question", (), ("answer",))
            for order in orders
        ),
    )


class _PostFlowReached(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dom_orders,failed_open",
    (
        ([1, 2, 3, 4], False),  # Q4 has no independent expected answer.
        ([1, 3], False),  # Q2 has disappeared from the UI.
        ([1, 2, 3], True),  # One unavailable module must not stop all modules.
    ),
)
async def test_perfect_profile_reaches_next_module_after_missing_content(
    monkeypatch,
    dom_orders,
    failed_open,
):
    modules = Mock(spec=BasicModulesPage)
    current_step = 4

    async def open_module(module):
        nonlocal current_step
        current_step = module.step_id
        if failed_open and module.step_id == 4:
            raise AssertionError("module content is unavailable")

    modules.go_to_next_module.side_effect = open_module
    modules.read_module_status.return_value = "undefined"
    modules.read_module_statuses.return_value = {4: "started", 5: "completed"}
    modules.answer_question_with_retries.return_value = QuestionAnswerResult(
        False, False
    )
    factory = Mock(return_value=modules)
    factory._KNOWN_STATUSES = BasicModulesPage._KNOWN_STATUSES
    monkeypatch.setattr(runner, "BasicModulesPage", factory)
    page = Mock()
    page.locator.return_value.evaluate_all = AsyncMock(
        side_effect=lambda _: dom_orders if current_step == 4 else [1],
    )
    scenario = SimpleNamespace(
        page=page,
        read_progress_percent=AsyncMock(return_value=(2.8, "2.8")),
        wait_for_progress_above=AsyncMock(return_value=(5.6, "5.6")),
        verify_ctf_direct_transition=AsyncMock(side_effect=_PostFlowReached),
    )
    collector = ResultCollector(scenario_label="NAD")
    collector.capture_failure_screenshot = AsyncMock(return_value=None)
    content = BasicScenarioContent(
        NAD_C3_CONTENT_SOURCE,
        (
            _module(4, (1, 2, 3)),
            _module(5, (1,)),
            BasicModule(36, "Complex", "theory", None, (), (), False, ()),
        ),
        "c-10",
    )
    user = SimpleNamespace(username="user", password="secret", test_profile="perfect")

    with pytest.raises(_PostFlowReached):
        await runner._run_full_scenario(
            scenario_page=scenario,
            content=content,
            ctf_content=object(),
            user=user,
            collector=collector,
        )

    answered = [
        (call.args[0].step_id, call.args[1].order)
        for call in modules.answer_question_with_retries.await_args_list
    ]
    assert (5, 1) in answered
    assert (4, 4) not in answered
    if 2 not in dom_orders:
        assert (4, 2) not in answered
    assert collector.get_summary()["failed"] + collector.get_summary()["errors"] > 0
    if not failed_open:
        assert any(
            result.action == "MODULE_4_CONTENT_MISMATCH" for result in collector.results
        )
        # No impossible progress-increase wait for an incomplete module.
        assert scenario.wait_for_progress_above.await_count == 1


def test_nad_product_host_is_resolved_from_matrix():
    assert settings.PRODUCT_HOST == ""


@pytest.mark.asyncio
async def test_missing_ctf_and_complex_content_still_attempts_finish(monkeypatch):
    monkeypatch.setattr(runner, "BasicModulesPage", Mock())
    scenario = SimpleNamespace(
        page=object(),
        verify_ctf_direct_transition=AsyncMock(
            side_effect=AssertionError("CTF link missing")
        ),
        return_from_ctf_to_basic_modules=AsyncMock(),
        finish_test_drive=AsyncMock(),
    )
    collector = ResultCollector(scenario_label="NAD")
    collector.capture_failure_screenshot = AsyncMock(return_value=None)
    content = BasicScenarioContent(NAD_C3_CONTENT_SOURCE, (), "c-10")

    await runner._run_full_scenario(
        scenario_page=scenario,
        content=content,
        ctf_content=object(),
        user=SimpleNamespace(username="user", test_profile="perfect"),
        collector=collector,
    )

    scenario.finish_test_drive.assert_awaited_once()
    assert collector.get_summary()["failed"] == 1
    assert any(
        result.action == "COMPLEX_SCENARIO_CONTENT_MISSING"
        for result in collector.results
    )


@pytest.mark.asyncio
async def test_specialized_profile_keeps_blocking_module_navigation(monkeypatch):
    modules = Mock(spec=BasicModulesPage)
    modules.go_to_next_module.side_effect = AssertionError("missing module")
    monkeypatch.setattr(runner, "BasicModulesPage", Mock(return_value=modules))
    collector = ResultCollector(scenario_label="NAD")
    collector.capture_failure_screenshot = AsyncMock(return_value=None)

    actual = await runner._run_full_scenario(
        scenario_page=SimpleNamespace(page=object()),
        content=BasicScenarioContent(
            NAD_C3_CONTENT_SOURCE, (_module(4, (1,)), _module(5, (1,))), "c-10"
        ),
        ctf_content=object(),
        user=SimpleNamespace(username="user", test_profile="answers_and_hints"),
        collector=collector,
    )

    assert actual is False
    assert modules.go_to_next_module.await_count == 1
