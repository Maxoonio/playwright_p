from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import settings
from content_catalog import (
    NAD_C3_CONTENT_SOURCE,
    NAD_CTF_CONTENT_SOURCE,
    SANDBOX_C1_CONTENT_SOURCE,
    SANDBOX_CTF_CONTENT_SOURCE,
    SIEM_C2_CONTENT_SOURCE,
    SIEM_CONTENT_GITLAB_REF,
    SIEM_CTF_CONTENT_SOURCE,
)
from models import (
    BasicExercise,
    BasicModule,
    BasicScenarioContent,
    CtfContentSource,
    CtfHint,
    CtfQuestion,
    CtfScenarioContent,
    CtfStage,
    TestDriveContentSource,
)
from utils.logging import log_step

SUPPORTED_EXERCISE_TYPES = {
    "input",
    "radio",
    "checkbox",
    "drag-n-drop",
}
_CONTENT_CACHE_SCHEMA_VERSION = 1
_NAD_C3_CACHE_NAME = "nad-c3-basic.json"
_NAD_CTF_CACHE_NAME = "nad-c1-ctf.json"
_SIEM_C2_CACHE_NAME = "siem-c2-basic.json"
_SIEM_CTF_CACHE_NAME = "siem-c20-ctf.json"
_SANDBOX_C1_CACHE_NAME = "sandbox-c1-basic.json"
_SANDBOX_CTF_CACHE_NAME = "sandbox-c10-ctf.json"


@lru_cache(maxsize=1)
def load_nad_c3_content() -> BasicScenarioContent:
    return _load_basic_scenario_content(
        source=NAD_C3_CONTENT_SOURCE,
        git_ref=settings.CONTENT_GITLAB_REF,
        cache_name=_NAD_C3_CACHE_NAME,
        content_kind="nad-c3-basic",
        display_name="NAD c-3",
    )


@lru_cache(maxsize=1)
def load_siem_c2_content() -> BasicScenarioContent:
    return _load_basic_scenario_content(
        source=SIEM_C2_CONTENT_SOURCE,
        git_ref=SIEM_CONTENT_GITLAB_REF,
        cache_name=_SIEM_C2_CACHE_NAME,
        content_kind="siem-c2-basic",
        display_name="SIEM c-2",
    )


@lru_cache(maxsize=1)
def load_sandbox_c1_content() -> BasicScenarioContent:
    return _load_basic_scenario_content(
        source=SANDBOX_C1_CONTENT_SOURCE,
        git_ref=settings.CONTENT_GITLAB_REF,
        cache_name=_SANDBOX_C1_CACHE_NAME,
        content_kind="sandbox-c1-basic",
        display_name="Sandbox c-1",
    )


def _load_basic_scenario_content(
    *,
    source: TestDriveContentSource,
    git_ref: str,
    cache_name: str,
    content_kind: str,
    display_name: str,
) -> BasicScenarioContent:
    cache_enabled = getattr(settings, "CONTENT_CACHE_ENABLED", True)
    cached_payload = (
        _load_content_cache(
            cache_name=cache_name,
            content_kind=content_kind,
            git_ref=git_ref,
        )
        if cache_enabled
        else None
    )
    if cached_payload is not None and _use_cache_without_remote_request():
        return _parse_cached_basic_content(
            source=source,
            cached_payload=cached_payload,
            reason="токен не задан",
            display_name=display_name,
        )

    try:
        (
            exercises_payload,
            sections_payload,
            transport,
        ) = _load_basic_remote_payload(
            source,
            git_ref=git_ref,
        )
    except RuntimeError as remote_error:
        if cached_payload is None:
            raise
        return _parse_cached_basic_content(
            source=source,
            cached_payload=cached_payload,
            reason=(f"GitLab временно недоступен: {remote_error}"),
            display_name=display_name,
        )

    content = parse_basic_scenario_content(
        source=source,
        exercises_payload=exercises_payload,
        sections_payload=sections_payload,
    )
    if cache_enabled:
        _save_content_cache(
            cache_name=cache_name,
            content_kind=content_kind,
            git_ref=git_ref,
            payload={
                "exercises": exercises_payload,
                "sections": sections_payload,
            },
        )
    question_count = content.total_questions
    log_step(
        f"[DATA] Свежий {display_name} загружен из GitLab через {transport}: "
        f"модулей={len(content.exercise_modules)}, "
        f"вопросов={question_count}"
    )
    return content


@lru_cache(maxsize=1)
def load_nad_ctf_content() -> CtfScenarioContent:
    return _load_ctf_scenario_content(
        source=NAD_CTF_CONTENT_SOURCE,
        cache_name=_NAD_CTF_CACHE_NAME,
        content_kind="nad-c1-ctf",
        display_name="NAD",
    )


@lru_cache(maxsize=1)
def load_siem_ctf_content() -> CtfScenarioContent:
    return _load_ctf_scenario_content(
        source=SIEM_CTF_CONTENT_SOURCE,
        cache_name=_SIEM_CTF_CACHE_NAME,
        content_kind="siem-c20-ctf",
        display_name="SIEM",
    )


@lru_cache(maxsize=1)
def load_sandbox_ctf_content() -> CtfScenarioContent:
    return _load_ctf_scenario_content(
        source=SANDBOX_CTF_CONTENT_SOURCE,
        cache_name=_SANDBOX_CTF_CACHE_NAME,
        content_kind="sandbox-c10-ctf",
        display_name="Sandbox",
    )


def _load_ctf_scenario_content(
    *,
    source: CtfContentSource,
    cache_name: str,
    content_kind: str,
    display_name: str,
) -> CtfScenarioContent:
    cache_enabled = getattr(settings, "CONTENT_CACHE_ENABLED", True)
    cached_payload = (
        _load_content_cache(
            cache_name=cache_name,
            content_kind=content_kind,
            git_ref=source.git_ref,
        )
        if cache_enabled
        else None
    )
    if cached_payload is not None and _use_cache_without_remote_request():
        return _parse_cached_ctf_content(
            source=source,
            cached_payload=cached_payload,
            reason="токен не задан",
            display_name=display_name,
        )

    try:
        payload, transport = _load_ctf_remote_payload(source)
    except RuntimeError as remote_error:
        if cached_payload is None:
            raise
        return _parse_cached_ctf_content(
            source=source,
            cached_payload=cached_payload,
            reason=(f"GitLab временно недоступен: {remote_error}"),
            display_name=display_name,
        )

    content = parse_ctf_scenario_content(
        payload,
        source=source,
    )
    if cache_enabled:
        _save_content_cache(
            cache_name=cache_name,
            content_kind=content_kind,
            git_ref=source.git_ref,
            payload={"exercises": payload},
        )
    log_step(
        f"[DATA] Свежий CTF {display_name} {source.case_id} "
        f"ref={source.git_ref} "
        f"загружен из GitLab через {transport}: "
        f"этапов={len(content.stages)}, "
        f"вопросов={content.total_questions}"
    )
    return content


def _load_basic_remote_payload(
    source: TestDriveContentSource,
    *,
    git_ref: str,
) -> tuple[object, object, str]:
    try:
        exercises_payload = _load_json_http(
            source.exercises_url,
            source.exercises_web_url,
        )
        sections_payload = _load_json_http(
            source.sections_url,
            source.sections_web_url,
        )
        return exercises_payload, sections_payload, "GitLab API"
    except RuntimeError as http_error:
        try:
            exercises_payload, sections_payload = _load_json_with_git(
                source,
                git_ref,
            )
        except RuntimeError as git_error:
            raise RuntimeError(
                f"{http_error}; Git fallback: {git_error}"
            ) from git_error
        log_step(
            f"[DATA] GitLab API недоступен; контент получен через Git ({http_error})"
        )
        return exercises_payload, sections_payload, "Git"


def _load_ctf_remote_payload(
    source: CtfContentSource,
) -> tuple[object, str]:
    try:
        payload = _load_json_http(
            source.exercises_url,
            source.exercises_web_url,
        )
        return payload, "GitLab API"
    except RuntimeError as http_error:
        try:
            payload = _load_single_json_with_git(
                source.exercises_path,
                source.exercises_web_url,
                source.git_ref,
            )
        except RuntimeError as git_error:
            raise RuntimeError(
                f"{http_error}; Git fallback: {git_error}"
            ) from git_error
        log_step(
            "[DATA] GitLab API недоступен; "
            f"CTF-контент получен через Git ({http_error})"
        )
        return payload, "Git"


def _use_cache_without_remote_request() -> bool:
    if settings.CONTENT_FORCE_REFRESH:
        return False
    return not (
        os.getenv(settings.CONTENT_GITLAB_TOKEN_ENV, "").strip()
        or os.getenv("CI_JOB_TOKEN", "").strip()
    )


def _parse_cached_basic_content(
    *,
    source: TestDriveContentSource,
    cached_payload: dict,
    reason: str,
    display_name: str,
) -> BasicScenarioContent:
    content = parse_basic_scenario_content(
        source=source,
        exercises_payload=cached_payload.get("exercises"),
        sections_payload=cached_payload.get("sections"),
    )
    question_count = content.total_questions
    log_step(
        f"[DATA][CACHE] {display_name} загружен из постоянного "
        f"локального кэша ({reason}): "
        f"модулей={len(content.exercise_modules)}, "
        f"вопросов={question_count}"
    )
    return content


def _parse_cached_ctf_content(
    *,
    source: CtfContentSource,
    cached_payload: dict,
    reason: str,
    display_name: str,
) -> CtfScenarioContent:
    content = parse_ctf_scenario_content(
        cached_payload.get("exercises"),
        source=source,
    )
    log_step(
        f"[DATA][CACHE] CTF {display_name} {source.case_id} "
        "загружен из постоянного "
        f"локального кэша ({reason}): "
        f"этапов={len(content.stages)}, "
        f"вопросов={content.total_questions}"
    )
    return content


def _load_content_cache(
    *,
    cache_name: str,
    content_kind: str,
    git_ref: str,
) -> dict | None:
    cache_path = settings.CONTENT_CACHE_DIR / cache_name
    if not cache_path.is_file():
        return None
    try:
        envelope = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log_step(
            f"[DATA][CACHE] Кэш {cache_path.name} поврежден "
            f"и будет проигнорирован: {exc}"
        )
        return None

    if not isinstance(envelope, dict):
        return None
    expected_metadata = {
        "schema_version": _CONTENT_CACHE_SCHEMA_VERSION,
        "content_kind": content_kind,
        "project": settings.CONTENT_GITLAB_PROJECT,
        "git_ref": git_ref,
    }
    if any(envelope.get(key) != value for key, value in expected_metadata.items()):
        return None
    payload = envelope.get("payload")
    return payload if isinstance(payload, dict) else None


def _save_content_cache(
    *,
    cache_name: str,
    content_kind: str,
    git_ref: str,
    payload: dict,
) -> None:
    cache_directory = settings.CONTENT_CACHE_DIR
    cache_path = cache_directory / cache_name
    temporary_path: Path | None = None
    try:
        cache_directory.mkdir(parents=True, exist_ok=True)
        envelope = {
            "schema_version": _CONTENT_CACHE_SCHEMA_VERSION,
            "content_kind": content_kind,
            "project": settings.CONTENT_GITLAB_PROJECT,
            "git_ref": git_ref,
            "payload": payload,
        }
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=cache_directory,
            prefix=f".{cache_name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            json.dump(
                envelope,
                temporary_file,
                ensure_ascii=False,
                indent=2,
            )
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, cache_path)
        log_step(
            f"[DATA][CACHE] Обновлен {cache_path}; GitLab-токен в кэш не записывается"
        )
    except (OSError, TypeError, ValueError) as exc:
        log_step(
            f"[DATA][CACHE] Не удалось обновить постоянный кэш {cache_path}: {exc}"
        )
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


def parse_nad_c10_content(
    payload: object,
) -> CtfScenarioContent:
    return parse_nad_ctf_content(payload)


def load_nad_c10_content() -> CtfScenarioContent:
    return load_nad_ctf_content()


def parse_nad_ctf_content(
    payload: object,
) -> CtfScenarioContent:
    return parse_ctf_scenario_content(
        payload,
        source=NAD_CTF_CONTENT_SOURCE,
    )


def parse_siem_ctf_content(
    payload: object,
) -> CtfScenarioContent:
    return parse_ctf_scenario_content(
        payload,
        source=SIEM_CTF_CONTENT_SOURCE,
    )


def parse_siem_c20_content(
    payload: object,
) -> CtfScenarioContent:
    return parse_siem_ctf_content(payload)


def load_siem_c20_content() -> CtfScenarioContent:
    return load_siem_ctf_content()


def parse_sandbox_c1_content(
    *,
    exercises_payload: object,
    sections_payload: object,
) -> BasicScenarioContent:
    return parse_basic_scenario_content(
        source=SANDBOX_C1_CONTENT_SOURCE,
        exercises_payload=exercises_payload,
        sections_payload=sections_payload,
    )


def parse_sandbox_ctf_content(
    payload: object,
) -> CtfScenarioContent:
    return parse_ctf_scenario_content(
        payload,
        source=SANDBOX_CTF_CONTENT_SOURCE,
    )


def load_sandbox_c10_content() -> CtfScenarioContent:
    return load_sandbox_ctf_content()


def parse_ctf_scenario_content(
    payload: object,
    *,
    source: CtfContentSource,
) -> CtfScenarioContent:
    if not isinstance(payload, list) or any(
        not isinstance(item, dict) for item in payload
    ):
        raise AssertionError(
            f"Ожидался JSON-массив вопросов в {source.exercises_web_url}"
        )

    raw_by_stage: dict[
        int,
        list[
            tuple[
                int,
                str,
                str,
                int,
                int | None,
                tuple[CtfHint, ...],
            ]
        ],
    ] = {}
    seen_orders: set[int] = set()
    for raw_question in payload:
        stage = _positive_int(
            raw_question.get("scenario_step"),
            field="scenario_step",
        )
        global_order = _positive_int(
            raw_question.get("order"),
            field="order",
        )
        if global_order in seen_orders:
            raise AssertionError(
                f"В CTF exercises.json повторяется order={global_order}"
            )
        seen_orders.add(global_order)
        answer = str(raw_question.get("answer_ru") or "").strip()
        if not answer:
            raise AssertionError(
                f"У CTF-вопроса order={global_order} отсутствует answer_ru"
            )
        points = _positive_int(
            raw_question.get("points"),
            field=f"points вопроса order={global_order}",
        )
        duration_raw = raw_question.get("duration")
        duration_seconds = (
            _positive_int(
                duration_raw,
                field=f"duration вопроса order={global_order}",
            )
            if duration_raw is not None
            else None
        )
        wrong_answer = str(raw_question.get("wrong_answer") or "").strip()
        if not wrong_answer:
            wrong_answer = _make_wrong_answer(answer)

        raw_hints = raw_question.get("hints", [])
        if not isinstance(raw_hints, list) or any(
            not isinstance(item, dict) for item in raw_hints
        ):
            raise AssertionError(
                f"У CTF-вопроса order={global_order} hints должен быть массивом объектов"
            )
        # Matrix индексирует подсказки по order. Если контент содержит
        # дубликат order (так сейчас устроен Q4 SIEM c-20), DOM показывает
        # последнюю запись. Повторяем эту семантику при формировании эталона.
        hints_by_order: dict[int, CtfHint] = {}
        for raw_hint in raw_hints:
            hint_order = _positive_int(
                raw_hint.get("order"),
                field=(f"order подсказки CTF-вопроса order={global_order}"),
            )
            hints_by_order[hint_order] = CtfHint(
                order=hint_order,
                penalty_points=_non_negative_int(
                    raw_hint.get("penalty_points"),
                    field=(
                        f"penalty_points подсказки CTF-вопроса order={global_order}"
                    ),
                ),
                text=str(raw_hint.get("text_ru") or raw_hint.get("text") or "").strip(),
            )
        hints = tuple(
            hints_by_order[hint_order] for hint_order in sorted(hints_by_order)
        )
        raw_by_stage.setdefault(stage, []).append(
            (
                global_order,
                answer,
                wrong_answer,
                points,
                duration_seconds,
                hints,
            )
        )

    stages = tuple(
        CtfStage(
            stage=stage,
            questions=tuple(
                CtfQuestion(
                    stage=stage,
                    local_order=local_order,
                    global_order=global_order,
                    answer=answer,
                    points=points,
                    duration_seconds=duration_seconds,
                    wrong_answer=wrong_answer,
                    hints=hints,
                )
                for local_order, (
                    global_order,
                    answer,
                    wrong_answer,
                    points,
                    duration_seconds,
                    hints,
                ) in enumerate(
                    sorted(questions),
                    start=1,
                )
            ),
        )
        for stage, questions in sorted(raw_by_stage.items())
    )
    if not stages:
        raise AssertionError(f"В {source.exercises_web_url} нет CTF-вопросов")

    return CtfScenarioContent(
        source=source,
        stages=stages,
    )


def parse_nad_c3_content(
    *,
    source: TestDriveContentSource,
    exercises_payload: object,
    sections_payload: object,
) -> BasicScenarioContent:
    return parse_basic_scenario_content(
        source=source,
        exercises_payload=exercises_payload,
        sections_payload=sections_payload,
    )


def parse_siem_c2_content(
    *,
    exercises_payload: object,
    sections_payload: object,
) -> BasicScenarioContent:
    return parse_basic_scenario_content(
        source=SIEM_C2_CONTENT_SOURCE,
        exercises_payload=exercises_payload,
        sections_payload=sections_payload,
    )


def parse_basic_scenario_content(
    *,
    source: TestDriveContentSource,
    exercises_payload: object,
    sections_payload: object,
) -> BasicScenarioContent:
    if not isinstance(exercises_payload, list) or any(
        not isinstance(item, dict) for item in exercises_payload
    ):
        raise AssertionError(
            f"Ожидался JSON-массив вопросов в {source.exercises_web_url}"
        )
    if not isinstance(sections_payload, list) or any(
        not isinstance(item, dict) for item in sections_payload
    ):
        raise AssertionError(f"Ожидался JSON-массив секций в {source.sections_web_url}")

    exercises_by_step: dict[int, list[BasicExercise]] = {}
    seen_question_keys: set[tuple[int, int]] = set()
    for raw_exercise in exercises_payload:
        exercise = _parse_exercise(raw_exercise, source=source)
        key = (exercise.step_id, exercise.order)
        if key in seen_question_keys:
            raise AssertionError(
                "В exercises.json повторяется вопрос "
                f"scenario_step={key[0]}, order={key[1]}"
            )
        seen_question_keys.add(key)
        exercises_by_step.setdefault(exercise.step_id, []).append(exercise)

    for exercises in exercises_by_step.values():
        exercises.sort(key=lambda exercise: exercise.order)

    modules: list[BasicModule] = []
    seen_steps: set[int] = set()
    ctf_case_ids: list[str] = []

    def visit(
        items: list[dict],
        ancestors: tuple[tuple[int, str], ...],
    ) -> None:
        for item in items:
            ctf_case_id = str(item.get("ctf_id") or "").strip()
            if ctf_case_id:
                ctf_case_ids.append(ctf_case_id)
                continue

            raw_step_id = item.get("scenario_step_id")
            if raw_step_id is None and item.get("kind") == "step":
                raw_step_id = _positive_int(item.get("id"), field="step id")
            if raw_step_id is not None:
                step_id = _positive_int(
                    raw_step_id,
                    field="scenario_step_id",
                )
                if step_id in seen_steps:
                    raise AssertionError(
                        f"В sections.json повторяется scenario_step_id={step_id}"
                    )
                seen_steps.add(step_id)
                module_type = str(item.get("type", "")).strip()
                title = str(
                    item.get("scenario_step_title_ru") or item.get("title_ru") or ""
                ).strip()
                if not title:
                    raise AssertionError(
                        f"У шага {step_id} отсутствует русское название"
                    )
                raw_content = item.get("content_ru")
                content_filename = None
                if isinstance(raw_content, dict):
                    content_filename = (
                        str(raw_content.get("filename") or "").strip() or None
                    )
                exercises = tuple(exercises_by_step.get(step_id, ()))
                if module_type == "ex" and not exercises:
                    raise AssertionError(
                        f"Для EX-модуля {step_id} «{title}» нет вопросов"
                    )
                modules.append(
                    BasicModule(
                        step_id=step_id,
                        title=title,
                        module_type=module_type,
                        content_filename=content_filename,
                        ancestor_section_ids=tuple(
                            section_id for section_id, _ in ancestors
                        ),
                        ancestor_section_titles=tuple(
                            section_title for _, section_title in ancestors
                        ),
                        required_for_ctf=bool(item.get("required_for_ctf", False)),
                        exercises=exercises,
                    )
                )
                continue

            if str(item.get("type", "")).strip() == "ctf":
                continue

            child_ancestors = ancestors
            raw_section_id = item.get("id")
            if raw_section_id is not None:
                section_title = str(item.get("title_ru") or "").strip()
                if not section_title:
                    raise AssertionError(
                        f"У раздела {raw_section_id} отсутствует title_ru"
                    )
                child_ancestors = (
                    *ancestors,
                    (
                        _positive_int(
                            raw_section_id,
                            field="section id",
                        ),
                        section_title,
                    ),
                )

            raw_steps = item.get("steps")
            if isinstance(raw_steps, list):
                if any(not isinstance(step, dict) for step in raw_steps):
                    raise AssertionError(
                        "steps в sections.json должен содержать объекты"
                    )
                visit(raw_steps, child_ancestors)

            raw_sections = item.get("sections")
            if isinstance(raw_sections, list):
                if any(not isinstance(section, dict) for section in raw_sections):
                    raise AssertionError(
                        "sections в sections.json должен содержать объекты"
                    )
                visit(raw_sections, child_ancestors)

            if "children" in item:
                raw_children = item["children"]
                if not isinstance(raw_children, list) or any(
                    not isinstance(child, dict) for child in raw_children
                ):
                    raise AssertionError(
                        "children в sections.json должен быть массивом объектов"
                    )
                visit(raw_children, child_ancestors)

    visit(sections_payload, ())

    unknown_steps = sorted(set(exercises_by_step) - seen_steps)
    if unknown_steps:
        raise AssertionError(
            "В exercises.json есть вопросы для отсутствующих в "
            f"sections.json шагов: {unknown_steps}"
        )
    if not ctf_case_ids:
        raise AssertionError("В sections.json не найден CTF-модуль")

    return BasicScenarioContent(
        source=source,
        modules=tuple(modules),
        ctf_case_id=ctf_case_ids[0],
    )


def _parse_exercise(
    raw: dict,
    *,
    source: TestDriveContentSource,
) -> BasicExercise:
    step_id = _positive_int(
        raw.get("scenario_step"),
        field="scenario_step",
    )
    order = _positive_int(raw.get("order"), field="order")
    question_type = str(raw.get("type") or "").strip()
    if question_type not in SUPPORTED_EXERCISE_TYPES:
        raise AssertionError(
            f"Неподдерживаемый type={question_type!r} у шага {step_id}, вопроса {order}"
        )

    question = str(raw.get("question_ru") or "").strip()
    if not question:
        raise AssertionError(f"Нет question_ru у шага {step_id}, вопроса {order}")

    options = _string_tuple(raw.get("questions_ru", ()))
    raw_answer = raw.get("answer_ru")
    answers: tuple[str, ...] = ()
    mapping: tuple[tuple[str, str], ...] = ()

    if isinstance(raw_answer, dict):
        if question_type != "drag-n-drop":
            raise AssertionError(
                f"Словарь answer_ru допустим только для drag-n-drop: "
                f"шаг {step_id}, вопрос {order}"
            )
        pairs: list[tuple[str, str]] = []
        for prompt in options:
            answer = str(raw_answer.get(prompt) or "").strip()
            if not answer:
                raise AssertionError(
                    f"Нет сопоставления для «{prompt}» у шага "
                    f"{step_id}, вопроса {order}"
                )
            pairs.append(
                (
                    prompt,
                    answer,
                )
            )
        mapping = tuple(pairs)
    elif isinstance(raw_answer, list):
        answers = _string_tuple(raw_answer)
    else:
        answer = str(raw_answer or "").strip()
        if not answer:
            raise AssertionError(
                f"Нет answer_ru у шага {step_id}, вопроса {order}: "
                f"{source.exercises_web_url}"
            )
        answers = (answer,)

    if question_type in {"radio", "checkbox"} and not options:
        raise AssertionError(f"Нет questions_ru у шага {step_id}, вопроса {order}")
    if question_type == "drag-n-drop" and not mapping:
        raise AssertionError(
            f"Нет answer_ru-сопоставления у шага {step_id}, вопроса {order}"
        )

    hint = str(raw.get("hint_ru") or "").strip() or None
    return BasicExercise(
        step_id=step_id,
        order=order,
        question_type=question_type,
        question=question,
        options=options,
        answers=answers,
        answer_mapping=mapping,
        hint=hint,
    )


def _normalize_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split()).casefold()


def _string_tuple(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(value for item in raw if (value := str(item).strip()))


def _positive_int(raw: object, *, field: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise AssertionError(
            f"Поле {field} должно быть целым числом, получено {raw!r}"
        ) from exc
    if value <= 0:
        raise AssertionError(
            f"Поле {field} должно быть положительным, получено {value}"
        )
    return value


def _non_negative_int(raw: object, *, field: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise AssertionError(
            f"Поле {field} должно быть целым числом, получено {raw!r}"
        ) from exc
    if value < 0:
        raise AssertionError(
            f"Поле {field} не может быть отрицательным, получено {value}"
        )
    return value


def _make_wrong_answer(answer: str) -> str:
    """Меняет последний буквенно-цифровой символ, сохраняя формат ответа."""

    for index in range(len(answer) - 1, -1, -1):
        character = answer[index]
        if character.isdigit():
            replacement = str((int(character) + 1) % 10)
        elif "a" <= character <= "z":
            replacement = "x" if character != "x" else "y"
        elif "A" <= character <= "Z":
            replacement = "X" if character != "X" else "Y"
        elif character.isalpha():
            replacement = "Я" if character.isupper() else "я"
            if character == replacement:
                replacement = "Ю" if character.isupper() else "ю"
        else:
            continue
        return f"{answer[:index]}{replacement}{answer[index + 1 :]}"

    return f"{answer}__incorrect"


def _load_json_http(url: str, web_url: str) -> object:
    headers = {
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "User-Agent": "playwright-pt-matrix-test-drive",
    }
    private_token = os.getenv(
        settings.CONTENT_GITLAB_TOKEN_ENV,
        "",
    ).strip()
    job_token = os.getenv("CI_JOB_TOKEN", "").strip()
    if private_token:
        headers["PRIVATE-TOKEN"] = private_token
    elif job_token:
        headers["JOB-TOKEN"] = job_token

    request = Request(url, headers=headers)
    try:
        with urlopen(  # noqa: S310 - URL задан каталогом проекта.
            request,
            timeout=settings.CONTENT_GITLAB_REQUEST_TIMEOUT_SECONDS,
        ) as response:
            raw_content = response.read()
    except HTTPError as exc:
        raise RuntimeError(f"GitLab вернул HTTP {exc.code} для {web_url}") from exc
    except (TimeoutError, URLError) as exc:
        raise RuntimeError(f"Не удалось загрузить {web_url}: {exc}") from exc

    return _decode_json(raw_content, source=web_url)


def _load_json_with_git(
    source: TestDriveContentSource,
    git_ref: str | None = None,
) -> tuple[object, object]:
    resolved_git_ref = git_ref or settings.CONTENT_GITLAB_REF
    with TemporaryDirectory(prefix="pt-test-drive-content-") as temp_dir:
        try:
            clone = subprocess.run(
                [
                    "git",
                    "clone",
                    "--quiet",
                    "--depth",
                    "1",
                    "--branch",
                    resolved_git_ref,
                    "--filter=blob:none",
                    "--no-checkout",
                    settings.CONTENT_GIT_REPOSITORY_URL,
                    temp_dir,
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=settings.CONTENT_GIT_CLONE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "Истек тайм-аут временного клонирования контента test-drive из GitLab"
            ) from exc
        if clone.returncode != 0:
            raise RuntimeError(
                "Не удалось получить контент test-drive из GitLab. "
                f"Задайте {settings.CONTENT_GITLAB_TOKEN_ENV} или "
                "настройте доступ Git Credential Manager. "
                f"Git: {clone.stderr.strip()}"
            )

        exercises = _git_show_json(
            Path(temp_dir),
            source.exercises_path,
            source.exercises_web_url,
        )
        sections = _git_show_json(
            Path(temp_dir),
            source.sections_path,
            source.sections_web_url,
        )
        return exercises, sections


def _load_single_json_with_git(
    relative_path: str,
    web_url: str,
    git_ref: str,
) -> object:
    with TemporaryDirectory(prefix="pt-test-drive-ctf-content-") as temp_dir:
        try:
            clone = subprocess.run(
                [
                    "git",
                    "clone",
                    "--quiet",
                    "--depth",
                    "1",
                    "--branch",
                    git_ref,
                    "--filter=blob:none",
                    "--no-checkout",
                    settings.CONTENT_GIT_REPOSITORY_URL,
                    temp_dir,
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=(settings.CONTENT_GIT_CLONE_TIMEOUT_SECONDS),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "Истек тайм-аут временного клонирования "
                "CTF-контента test-drive из GitLab"
            ) from exc
        if clone.returncode != 0:
            raise RuntimeError(
                "Не удалось получить CTF-контент test-drive "
                "из GitLab. "
                f"Задайте {settings.CONTENT_GITLAB_TOKEN_ENV} или "
                "настройте доступ Git Credential Manager. "
                f"Git: {clone.stderr.strip()}"
            )
        return _git_show_json(
            Path(temp_dir),
            relative_path,
            web_url,
        )


def _git_show_json(
    repository: Path,
    relative_path: str,
    web_url: str,
) -> object:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "show",
                f"HEAD:{relative_path}",
            ],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Истек тайм-аут чтения {web_url} через Git") from exc
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"GitLab не вернул {web_url}: {error}")
    return _decode_json(result.stdout, source=web_url)


def _decode_json(raw_content: bytes, *, source: str) -> object:
    try:
        return json.loads(raw_content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GitLab вернул некорректный JSON: {source}") from exc
