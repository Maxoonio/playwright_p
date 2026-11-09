import sys
from dataclasses import replace
from pathlib import Path

TEST_DRIVE_ROOT = Path(__file__).resolve().parents[1]
for import_path in (
    TEST_DRIVE_ROOT / "common",
    TEST_DRIVE_ROOT,
):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from content_catalog import (
    SIEM_C2_CONTENT_SOURCE,
    SIEM_CONTENT_GITLAB_REF,
    SIEM_CTF_CONTENT_SOURCE,
)
from content_loader import (
    load_siem_c2_content,
    load_siem_ctf_content,
    parse_siem_c2_content,
    parse_siem_ctf_content,
)
from models import (
    BasicModule,
    BasicScenarioContent,
    CtfQuestion,
    CtfScenarioContent,
    CtfStage,
)
from scenarios.runner import _validate_siem_content_contract


def _module(
    step_id: int,
    title: str,
    module_type: str,
    *,
    filename: str | None = None,
    required_for_ctf: bool = False,
) -> BasicModule:
    return BasicModule(
        step_id=step_id,
        title=title,
        module_type=module_type,
        content_filename=filename,
        ancestor_section_ids=(1,),
        ancestor_section_titles=("Раздел",),
        required_for_ctf=required_for_ctf,
        exercises=(),
    )


def _contract_content() -> tuple[
    BasicScenarioContent,
    CtfScenarioContent,
]:
    modules = (
        _module(1, "О Тест-драйве", "theory"),
        _module(2, "О PT Matrix", "theory"),
        _module(3, "О MaxPatrol SIEM", "ex"),
        _module(4, "Инфраструктура стенда", "theory"),
        _module(
            5,
            "Работа с PT MC",
            "ex",
            filename="05_mpsiem_td_ptmc_ru.html",
        ),
        _module(
            8,
            "Работа с активами",
            "ex",
            required_for_ctf=True,
        ),
        _module(
            10,
            "Работа с событиями",
            "ex",
            required_for_ctf=True,
        ),
        _module(
            14,
            "Работа с инцидентами",
            "ex",
            required_for_ctf=True,
        ),
        _module(
            17,
            "Закрепление",
            "ex",
            filename="17_mpsiem_td_appendix_persistence_ru.html",
        ),
        _module(
            161,
            "Комплексный сценарий",
            "theory",
            filename="33_common_td_complex_ru.html",
        ),
    )
    basic = BasicScenarioContent(
        source=SIEM_C2_CONTENT_SOURCE,
        modules=modules,
        ctf_case_id="c-20",
    )
    ctf = CtfScenarioContent(
        source=SIEM_CTF_CONTENT_SOURCE,
        stages=(
            CtfStage(
                stage=1,
                questions=(
                    CtfQuestion(
                        stage=1,
                        local_order=1,
                        global_order=1,
                        answer="answer",
                        points=10,
                    ),
                ),
            ),
        ),
    )
    return basic, ctf


def test_siem_catalog_uses_testdrive_sources() -> None:
    basic_source = SIEM_C2_CONTENT_SOURCE
    ctf_source = SIEM_CTF_CONTENT_SOURCE

    assert SIEM_CONTENT_GITLAB_REF == "testdrive"
    assert (basic_source.product_slug, basic_source.case_id) == (
        "siem",
        "c-2",
    )
    assert basic_source.exercises_path.endswith("/siem/cases/c-2/doc/exercises.json")
    assert basic_source.sections_path.endswith("/siem/cases/c-2/doc/sections.json")
    assert "ref=testdrive" in basic_source.exercises_url
    assert "ref=testdrive" in basic_source.sections_url

    assert (ctf_source.product_slug, ctf_source.case_id) == (
        "siem",
        "c-20",
    )
    assert ctf_source.git_ref == "testdrive"
    assert ctf_source.exercises_path.endswith("/siem/cases/c-20/doc/exercises.json")
    assert "ref=testdrive" in ctf_source.exercises_url


def test_siem_loaders_have_independent_session_caches() -> None:
    assert load_siem_c2_content.cache_parameters() == {
        "maxsize": 1,
        "typed": False,
    }
    assert load_siem_ctf_content.cache_parameters() == {
        "maxsize": 1,
        "typed": False,
    }


def test_siem_basic_parser_accepts_ctf_id_without_type() -> None:
    content = parse_siem_c2_content(
        exercises_payload=[
            {
                "scenario_step": 3,
                "order": 1,
                "type": "input",
                "question_ru": "Вопрос",
                "answer_ru": "ответ",
            }
        ],
        sections_payload=[
            {
                "id": 1,
                "title_ru": "Введение",
                "steps": [
                    {
                        "scenario_step_id": 3,
                        "scenario_step_title_ru": "О MaxPatrol SIEM",
                        "type": "ex",
                    }
                ],
            },
            {
                "id": 5,
                "ctf_id": "c-20",
                "title_ru": "CTF",
            },
        ],
    )

    assert content.source is SIEM_C2_CONTENT_SOURCE
    assert content.ctf_case_id == "c-20"
    assert [module.step_id for module in content.exercise_modules] == [3]


def test_siem_ctf_parser_keeps_siem_source() -> None:
    content = parse_siem_ctf_content(
        [
            {
                "order": 1,
                "scenario_step": 1,
                "answer_ru": "answer",
                "points": 10,
                "duration": 120,
            }
        ]
    )

    assert content.source is SIEM_CTF_CONTENT_SOURCE
    assert content.total_questions == 1
    assert content.stages[0].questions[0].answer == "answer"


def test_siem_contract_matches_navigation_and_required_modules() -> None:
    basic, ctf = _contract_content()

    _validate_siem_content_contract(basic, ctf)


def test_siem_contract_rejects_changed_required_for_ctf() -> None:
    basic, ctf = _contract_content()
    modules = tuple(
        replace(module, required_for_ctf=False) if module.step_id == 14 else module
        for module in basic.modules
    )

    try:
        _validate_siem_content_contract(
            replace(basic, modules=modules),
            ctf,
        )
    except AssertionError as exc:
        assert "required_for_ctf" in str(exc)
    else:
        raise AssertionError("Изменение required_for_ctf не обнаружено")
