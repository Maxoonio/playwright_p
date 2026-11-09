import sys
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
from content_catalog import (
    NAD_C3_CONTENT_SOURCE,
    NAD_CTF_CONTENT_SOURCE,
)
from content_loader import (
    load_nad_c3_content,
    load_nad_ctf_content,
    parse_nad_c3_content,
    parse_nad_ctf_content,
)


def test_gitlab_content_is_cached_for_the_pytest_session() -> None:
    assert load_nad_c3_content.cache_parameters() == {
        "maxsize": 1,
        "typed": False,
    }
    assert load_nad_ctf_content.cache_parameters() == {
        "maxsize": 1,
        "typed": False,
    }


def test_nad_c3_catalog_points_to_remote_gitlab_files() -> None:
    source = NAD_C3_CONTENT_SOURCE

    assert source.case_id == "c-3"
    assert source.exercises_path.endswith("/nad/cases/c-3/doc/exercises.json")
    assert source.sections_path.endswith("/nad/cases/c-3/doc/sections.json")
    assert "/api/v4/projects/" in source.exercises_url
    assert settings.CONTENT_GITLAB_REF == "dev_trainer"
    assert "ref=dev_trainer" in source.exercises_url
    assert "ref=dev_trainer" in source.sections_url
    assert "/blob/dev_trainer/" in source.exercises_web_url
    assert "/blob/dev_trainer/" in source.sections_web_url


def test_basic_preparation_does_not_reuse_test_drive_cache(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(settings, "CONTENT_CACHE_DIR", tmp_path)
    content_loader._save_content_cache(
        cache_name="nad-c3-basic.json",
        content_kind="nad-c3-basic",
        git_ref="testdrive",
        payload={"exercises": [], "sections": []},
    )

    assert (
        content_loader._load_content_cache(
            cache_name="nad-c3-basic.json",
            content_kind="nad-c3-basic",
            git_ref=settings.CONTENT_GITLAB_REF,
        )
        is None
    )


def test_nad_ctf_catalog_and_parser() -> None:
    source = NAD_CTF_CONTENT_SOURCE
    assert source.case_id == "c-1"
    assert source.git_ref == "test"
    assert source.exercises_path.endswith("/nad/cases/c-1/doc/exercises.json")
    assert "ref=test" in source.exercises_url

    content = parse_nad_ctf_content(
        [
            {
                "order": 1,
                "scenario_step": 1,
                "answer_ru": "first",
                "points": 10,
                "duration": 120,
                "wrong_answer": "wrong-first",
                "hints": [
                    {
                        "order": 1,
                        "penalty_points": 2,
                        "text_ru": "Первая подсказка",
                    }
                ],
            },
            {
                "order": 2,
                "scenario_step": 1,
                "answer_ru": "second",
                "points": 15,
                "duration": 180,
            },
            {
                "order": 3,
                "scenario_step": 2,
                "answer_ru": "third",
                "points": 20,
                "duration": 240,
            },
        ]
    )

    assert content.total_questions == 3
    assert [stage.stage for stage in content.stages] == [1, 2]
    assert [question.local_order for question in content.stages[0].questions] == [1, 2]
    assert content.stages[1].questions[0].answer == "third"
    assert content.stages[0].questions[0].points == 10
    assert content.stages[1].questions[0].duration_seconds == 240
    assert content.stages[0].questions[0].wrong_answer == "wrong-first"
    assert content.stages[0].questions[0].hints[0].text == ("Первая подсказка")
    assert content.stages[0].questions[1].wrong_answer != "second"


def test_basic_preparation_parses_kind_children_schema() -> None:
    content = parse_nad_c3_content(
        source=NAD_C3_CONTENT_SOURCE,
        exercises_payload=[
            {
                "scenario_step": 12,
                "order": 1,
                "type": "input",
                "question_ru": "Question",
                "answer_ru": "answer",
            }
        ],
        sections_payload=[
            {
                "kind": "section",
                "id": 1000,
                "title_ru": "Введение",
                "children": [
                    {
                        "kind": "step",
                        "id": 1,
                        "title_ru": "О Базовой подготовке",
                        "type": "theory",
                    },
                ],
            },
            {
                "kind": "section",
                "id": 1002,
                "title_ru": "Сценарии использования",
                "children": [
                    {
                        "kind": "section",
                        "id": 1003,
                        "title_ru": "Подраздел",
                        "children": [
                            {
                                "kind": "step",
                                "id": 12,
                                "title_ru": "Модуль",
                                "type": "ex",
                                "required_for_ctf": True,
                                "content_ru": {"filename": "module.html"},
                            },
                        ],
                    },
                ],
            },
            {"kind": "ctf", "id": 5, "type": "ctf", "ctf_id": "c-10"},
        ],
    )

    assert [m.step_id for m in content.modules] == [1, 12]
    assert content.ctf_case_id == "c-10"
    module = content.exercise_modules[0]
    assert module.ancestor_section_ids == (1002, 1003)
    assert module.ancestor_section_titles == ("Сценарии использования", "Подраздел")
    assert module.title == "Модуль"
    assert module.content_filename == "module.html"
    assert module.required_for_ctf
    assert module.exercises[0].answers == ("answer",)


def test_parse_all_supported_question_types_and_nested_sections() -> None:
    exercises = [
        {
            "scenario_step": 3,
            "order": 1,
            "type": "input",
            "question_ru": "Input?",
            "answer_ru": "answer",
        },
        {
            "scenario_step": 3,
            "order": 2,
            "type": "radio",
            "question_ru": "Radio?",
            "questions_ru": ["one", "two"],
            "answer_ru": "two",
        },
        {
            "scenario_step": 3,
            "order": 3,
            "type": "checkbox",
            "question_ru": "Checkbox?",
            "questions_ru": ["one", "two", "three"],
            "answer_ru": ["one", "three"],
        },
        {
            "scenario_step": 3,
            "order": 4,
            "type": "drag-n-drop",
            "question_ru": "Drag?",
            "questions_ru": ["left one", "left two"],
            "possible_answers_ru": ["right two", "right one"],
            "answer_ru": {
                "left one": "right one",
                "left two": "right two",
            },
        },
    ]
    sections = [
        {
            "id": 4,
            "title_ru": "Root",
            "sections": [
                {
                    "id": 8,
                    "title_ru": "Nested",
                    "steps": [
                        {
                            "scenario_step_id": 3,
                            "scenario_step_title_ru": "Module",
                            "type": "ex",
                            "required_for_ctf": True,
                        }
                    ],
                }
            ],
        },
        {
            "id": 5,
            "type": "ctf",
            "ctf_id": "c-10",
            "title_ru": "CTF",
        },
    ]

    content = parse_nad_c3_content(
        source=NAD_C3_CONTENT_SOURCE,
        exercises_payload=exercises,
        sections_payload=sections,
    )

    assert content.ctf_case_id == "c-10"
    assert len(content.exercise_modules) == 1
    module = content.exercise_modules[0]
    assert module.ancestor_section_ids == (4, 8)
    assert module.ancestor_section_titles == ("Root", "Nested")
    assert module.required_for_ctf is True
    assert [exercise.question_type for exercise in module.exercises] == [
        "input",
        "radio",
        "checkbox",
        "drag-n-drop",
    ]
    assert module.exercises[-1].answer_mapping == (
        ("left one", "right one"),
        ("left two", "right two"),
    )


def test_answer_from_gitlab_is_not_replaced_with_similar_visible_option() -> None:
    exercises = [
        {
            "scenario_step": 3,
            "order": 1,
            "type": "checkbox",
            "question_ru": "Question",
            "questions_ru": [
                "Изменение уровня опасности",
                "Другой вариант",
            ],
            "answer_ru": ["Изменение уровеня опасности"],
        }
    ]
    sections = [
        {
            "id": 1,
            "title_ru": "Section",
            "steps": [
                {
                    "scenario_step_id": 3,
                    "scenario_step_title_ru": "Module",
                    "type": "ex",
                }
            ],
        },
        {
            "id": 5,
            "type": "ctf",
            "ctf_id": "c-10",
            "title_ru": "CTF",
        },
    ]

    content = parse_nad_c3_content(
        source=NAD_C3_CONTENT_SOURCE,
        exercises_payload=exercises,
        sections_payload=sections,
    )

    assert content.exercise_modules[0].exercises[0].answers == (
        "Изменение уровеня опасности",
    )


def test_basic_content_cache_survives_new_python_session(
    monkeypatch,
    tmp_path: Path,
) -> None:
    exercises = [
        {
            "scenario_step": 3,
            "order": 1,
            "type": "input",
            "question_ru": "Question",
            "answer_ru": "answer",
        }
    ]
    sections = [
        {
            "id": 1,
            "title_ru": "Section",
            "steps": [
                {
                    "scenario_step_id": 3,
                    "scenario_step_title_ru": "Module",
                    "type": "ex",
                }
            ],
        },
        {
            "id": 5,
            "type": "ctf",
            "ctf_id": "c-10",
            "title_ru": "CTF",
        },
    ]
    remote_payloads = iter((exercises, sections))

    monkeypatch.setattr(settings, "CONTENT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(settings, "CONTENT_FORCE_REFRESH", False)
    monkeypatch.setenv(
        settings.CONTENT_GITLAB_TOKEN_ENV,
        "test-token-is-never-written-to-cache",
    )
    monkeypatch.setattr(
        content_loader,
        "_load_json_http",
        lambda *_args: next(remote_payloads),
    )
    load_nad_c3_content.cache_clear()
    try:
        first = load_nad_c3_content()
        cache_path = tmp_path / "nad-c3-basic.json"
        assert cache_path.is_file()
        assert "test-token" not in cache_path.read_text(encoding="utf-8")

        load_nad_c3_content.cache_clear()
        monkeypatch.delenv(
            settings.CONTENT_GITLAB_TOKEN_ENV,
            raising=False,
        )
        monkeypatch.delenv("CI_JOB_TOKEN", raising=False)

        def unexpected_remote_request(*_args):
            raise AssertionError("При наличии кэша запрос в GitLab не нужен")

        monkeypatch.setattr(
            content_loader,
            "_load_json_http",
            unexpected_remote_request,
        )
        second = load_nad_c3_content()

        assert second == first
    finally:
        load_nad_c3_content.cache_clear()


def test_basic_content_uses_cache_when_gitlab_returns_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    exercises = [
        {
            "scenario_step": 3,
            "order": 1,
            "type": "input",
            "question_ru": "Question",
            "answer_ru": "answer",
        }
    ]
    sections = [
        {
            "id": 1,
            "title_ru": "Section",
            "steps": [
                {
                    "scenario_step_id": 3,
                    "scenario_step_title_ru": "Module",
                    "type": "ex",
                }
            ],
        },
        {
            "id": 5,
            "type": "ctf",
            "ctf_id": "c-10",
            "title_ru": "CTF",
        },
    ]
    monkeypatch.setattr(settings, "CONTENT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(settings, "CONTENT_FORCE_REFRESH", False)
    monkeypatch.setenv(
        settings.CONTENT_GITLAB_TOKEN_ENV,
        "configured-token",
    )
    payloads = iter((exercises, sections))
    monkeypatch.setattr(
        content_loader,
        "_load_json_http",
        lambda *_args: next(payloads),
    )
    load_nad_c3_content.cache_clear()
    try:
        expected = load_nad_c3_content()
        load_nad_c3_content.cache_clear()

        def unavailable(*_args):
            raise RuntimeError("GitLab HTTP 500")

        monkeypatch.setattr(
            content_loader,
            "_load_json_http",
            unavailable,
        )
        monkeypatch.setattr(
            content_loader,
            "_load_json_with_git",
            unavailable,
        )

        assert load_nad_c3_content() == expected
    finally:
        load_nad_c3_content.cache_clear()


def test_ctf_content_cache_is_reused_without_token(
    monkeypatch,
    tmp_path: Path,
) -> None:
    payload = [
        {
            "order": 1,
            "scenario_step": 1,
            "answer_ru": "answer",
            "points": 10,
            "duration": 120,
        }
    ]
    monkeypatch.setattr(settings, "CONTENT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(settings, "CONTENT_FORCE_REFRESH", False)
    monkeypatch.setenv(
        settings.CONTENT_GITLAB_TOKEN_ENV,
        "configured-token",
    )
    monkeypatch.setattr(
        content_loader,
        "_load_json_http",
        lambda *_args: payload,
    )
    load_nad_ctf_content.cache_clear()
    try:
        first = load_nad_ctf_content()
        assert (tmp_path / "nad-c1-ctf.json").is_file()

        load_nad_ctf_content.cache_clear()
        monkeypatch.delenv(
            settings.CONTENT_GITLAB_TOKEN_ENV,
            raising=False,
        )
        monkeypatch.delenv("CI_JOB_TOKEN", raising=False)

        def unexpected_remote_request(*_args):
            raise AssertionError("CTF должен загружаться из постоянного кэша")

        monkeypatch.setattr(
            content_loader,
            "_load_json_http",
            unexpected_remote_request,
        )

        assert load_nad_ctf_content() == first
    finally:
        load_nad_ctf_content.cache_clear()
