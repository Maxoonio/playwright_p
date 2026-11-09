from __future__ import annotations

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

import content_loader
import settings
from content_catalog import SANDBOX_C1_CONTENT_SOURCE, SANDBOX_CTF_CONTENT_SOURCE
from content_loader import (
    load_sandbox_c1_content,
    load_sandbox_ctf_content,
    parse_sandbox_c1_content,
    parse_sandbox_ctf_content,
)
from models import (
    BasicModule,
    BasicScenarioContent,
    CtfQuestion,
    CtfScenarioContent,
    CtfStage,
)
from scenarios.runner import _validate_sandbox_content_contract


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


def _contract_content() -> tuple[BasicScenarioContent, CtfScenarioContent]:
    basic = BasicScenarioContent(
        source=SANDBOX_C1_CONTENT_SOURCE,
        modules=(
            _module(1, "О Тест-драйве", "theory"),
            _module(2, "О PT Matrix", "theory"),
            _module(3, "О PT Sandbox", "ex"),
            _module(
                5,
                "Главное меню",
                "ex",
                filename="11560197387_ru.html",
            ),
            _module(
                8,
                "Страница Объекты",
                "ex",
                required_for_ctf=True,
            ),
            _module(
                10,
                "Страница Источники",
                "ex",
                required_for_ctf=True,
            ),
            _module(
                17,
                "Статический анализ",
                "ex",
                filename="11583685387_ru.html",
                required_for_ctf=True,
            ),
            _module(
                18,
                "Поведенческий анализ",
                "ex",
                required_for_ctf=True,
            ),
            _module(
                21,
                "Анализ письма с вредоносным архивом",
                "ex",
                filename="11583691147_ru.html",
                required_for_ctf=True,
            ),
            _module(
                161,
                "Комплексный сценарий",
                "theory",
                filename="33_common_td_complex_ru.html",
            ),
        ),
        ctf_case_id="c-10",
    )
    ctf = CtfScenarioContent(
        source=SANDBOX_CTF_CONTENT_SOURCE,
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


def _basic_payloads() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    exercises = [
        {
            "scenario_step": 3,
            "order": 1,
            "type": "checkbox",
            "question_ru": "Каких модулей нет?",
            "questions_ru": ["Первый", "Второй", "Третий"],
            "answer_ru": ["Первый", "Третий"],
        },
        {
            "scenario_step": 3,
            "order": 2,
            "type": "radio",
            "question_ru": "Что происходит с архивом?",
            "questions_ru": ["Удаление", "Подбор пароля"],
            "answer_ru": "Подбор пароля",
        },
    ]
    sections = [
        {
            "id": 1,
            "title_ru": "Введение",
            "steps": [
                {
                    "scenario_step_id": 3,
                    "scenario_step_title_ru": "О PT Sandbox",
                    "type": "ex",
                    "content_ru": {
                        "type": "html",
                        "filename": "11538304011_ru.html",
                    },
                }
            ],
        },
        {
            "id": 5,
            "title_ru": "Поиск флага. Анализ вредоносных объектов",
            "ctf_id": "c-10",
        },
    ]
    return exercises, sections


def test_sandbox_catalog_points_to_testdrive_gitlab_files() -> None:
    basic = SANDBOX_C1_CONTENT_SOURCE
    ctf = SANDBOX_CTF_CONTENT_SOURCE

    assert (basic.product_slug, basic.case_id) == ("sandbox", "c-1")
    assert basic.exercises_path.endswith("/sandbox/cases/c-1/doc/exercises.json")
    assert basic.sections_path.endswith("/sandbox/cases/c-1/doc/sections.json")
    assert "ref=testdrive" in basic.exercises_url
    assert "ref=testdrive" in basic.sections_url

    assert (ctf.product_slug, ctf.case_id, ctf.git_ref) == (
        "sandbox",
        "c-10",
        "testdrive",
    )
    assert ctf.exercises_path.endswith("/sandbox/cases/c-10/doc/exercises.json")
    assert "ref=testdrive" in ctf.exercises_url


def test_sandbox_parsers_keep_sources_and_supported_answers() -> None:
    exercises, sections = _basic_payloads()
    basic = parse_sandbox_c1_content(
        exercises_payload=exercises,
        sections_payload=sections,
    )
    ctf = parse_sandbox_ctf_content(
        [
            {
                "order": 1,
                "scenario_step": 1,
                "answer_ru": "flag",
                "points": 15,
                "duration": 120,
            }
        ]
    )

    assert basic.source is SANDBOX_C1_CONTENT_SOURCE
    assert basic.ctf_case_id == "c-10"
    assert [item.question_type for item in basic.exercise_modules[0].exercises] == [
        "checkbox",
        "radio",
    ]
    assert basic.exercise_modules[0].exercises[0].answers == (
        "Первый",
        "Третий",
    )
    assert ctf.source is SANDBOX_CTF_CONTENT_SOURCE
    assert ctf.total_questions == 1
    assert ctf.stages[0].questions[0].answer == "flag"


def test_sandbox_content_never_reads_or_writes_persistent_cache(
    monkeypatch,
) -> None:
    exercises, sections = _basic_payloads()
    ctf_payload = [
        {
            "order": 1,
            "scenario_step": 1,
            "answer_ru": "flag",
            "points": 15,
        }
    ]

    def persistent_cache_used(*_args, **_kwargs):
        raise AssertionError("Sandbox не должен использовать дисковый content-cache")

    monkeypatch.setattr(settings, "CONTENT_CACHE_ENABLED", False)
    monkeypatch.setattr(content_loader, "_load_content_cache", persistent_cache_used)
    monkeypatch.setattr(content_loader, "_save_content_cache", persistent_cache_used)
    monkeypatch.setattr(
        content_loader,
        "_load_basic_remote_payload",
        lambda *_args, **_kwargs: (exercises, sections, "test"),
    )
    monkeypatch.setattr(
        content_loader,
        "_load_ctf_remote_payload",
        lambda *_args, **_kwargs: (ctf_payload, "test"),
    )
    load_sandbox_c1_content.cache_clear()
    load_sandbox_ctf_content.cache_clear()
    try:
        assert load_sandbox_c1_content().ctf_case_id == "c-10"
        assert load_sandbox_ctf_content().total_questions == 1
    finally:
        load_sandbox_c1_content.cache_clear()
        load_sandbox_ctf_content.cache_clear()


def test_sandbox_loaders_cache_only_inside_current_python_process() -> None:
    assert load_sandbox_c1_content.cache_parameters() == {
        "maxsize": 1,
        "typed": False,
    }
    assert load_sandbox_ctf_content.cache_parameters() == {
        "maxsize": 1,
        "typed": False,
    }
    assert settings.CONTENT_CACHE_ENABLED is False
    assert settings.CONTENT_FORCE_REFRESH is True


def test_sandbox_contract_matches_navigation_and_required_modules() -> None:
    basic, ctf = _contract_content()

    _validate_sandbox_content_contract(basic, ctf)


def test_sandbox_contract_rejects_changed_required_for_ctf() -> None:
    basic, ctf = _contract_content()
    modules = tuple(
        replace(module, required_for_ctf=False) if module.step_id == 10 else module
        for module in basic.modules
    )

    try:
        _validate_sandbox_content_contract(replace(basic, modules=modules), ctf)
    except AssertionError as exc:
        assert "required_for_ctf" in str(exc)
    else:
        raise AssertionError("Изменение required_for_ctf не обнаружено")
