from dataclasses import dataclass
from enum import Enum

CTF_PROFILE_NAMES = {
    "answers_and_hints",
    "perfect",
    "question_metrics",
    "solutions",
    "stage_statuses",
}

PERFECT_PROFILE_NAMES = {
    "full_scenario",
    "perfect",
}

SPECIALIZED_CTF_PROFILE_NAMES = CTF_PROFILE_NAMES - {"perfect"}

SCENARIO_PROFILE_NAMES = {
    "full_scenario",
    *CTF_PROFILE_NAMES,
}

SUPPORTED_PROFILES = {
    "introduction",
    "introduction_finish",
    *SCENARIO_PROFILE_NAMES,
}


@dataclass(frozen=True)
class TestDriveUser:
    id: str
    username: str
    password: str
    enabled: bool
    test_profile: str


@dataclass(frozen=True)
class TestDriveContentSource:
    product_slug: str
    case_id: str
    exercises_path: str
    sections_path: str
    exercises_url: str
    sections_url: str
    exercises_web_url: str
    sections_web_url: str


@dataclass(frozen=True)
class BasicExercise:
    step_id: int
    order: int
    question_type: str
    question: str
    options: tuple[str, ...]
    answers: tuple[str, ...]
    answer_mapping: tuple[tuple[str, str], ...] = ()
    hint: str | None = None


@dataclass(frozen=True)
class BasicModule:
    step_id: int
    title: str
    module_type: str
    content_filename: str | None
    ancestor_section_ids: tuple[int, ...]
    ancestor_section_titles: tuple[str, ...]
    required_for_ctf: bool
    exercises: tuple[BasicExercise, ...]


@dataclass(frozen=True)
class BasicScenarioContent:
    source: TestDriveContentSource
    modules: tuple[BasicModule, ...]
    ctf_case_id: str

    @property
    def exercise_modules(self) -> tuple[BasicModule, ...]:
        return tuple(module for module in self.modules if module.module_type == "ex")

    @property
    def total_questions(self) -> int:
        return sum(len(module.exercises) for module in self.exercise_modules)


@dataclass(frozen=True)
class CtfContentSource:
    product_slug: str
    case_id: str
    git_ref: str
    exercises_path: str
    exercises_url: str
    exercises_web_url: str


@dataclass(frozen=True)
class CtfHint:
    order: int
    penalty_points: int
    text: str


@dataclass(frozen=True)
class CtfQuestion:
    stage: int
    local_order: int
    global_order: int
    answer: str
    points: int
    duration_seconds: int | None = None
    wrong_answer: str = ""
    hints: tuple[CtfHint, ...] = ()

    @property
    def local_question(self) -> int:
        """Совместимое имя поля для профилей CTF из тренажера."""

        return self.local_order


@dataclass(frozen=True)
class CtfStage:
    stage: int
    questions: tuple[CtfQuestion, ...]


@dataclass(frozen=True)
class CtfScenarioContent:
    source: CtfContentSource
    stages: tuple[CtfStage, ...]

    @property
    def total_questions(self) -> int:
        return sum(len(stage.questions) for stage in self.stages)


class CheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class CheckResult:
    username: str
    profile: str
    section: int | None
    step: int | None
    action: str
    status: CheckStatus
    message: str
    screenshot: str | None = None
