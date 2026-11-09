from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class QuestionMetricsExpectation:
    attempt_count: int
    accuracy_percent: int
    duration_seconds: int
    actual_seconds: int
    speed_percent: int
    base_points: int
    speed_bonus_points: int
    awarded_points: int


@dataclass(frozen=True)
class AggregateMetricsExpectation:
    accuracy_percent: int
    speed_percent: int
    points: int


def round_half_up(value: Decimal | float | int) -> int:
    return int(
        Decimal(str(value)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def accuracy_for_attempt(attempt_count: int) -> int:
    if attempt_count < 1:
        raise ValueError("Номер попытки должен быть положительным")
    if attempt_count == 1:
        return 100
    if attempt_count == 2:
        return 50
    if attempt_count == 3:
        return 30
    return 0


def speed_for_duration(
    *,
    duration_seconds: int,
    actual_seconds: int,
) -> int:
    if duration_seconds <= 0:
        raise ValueError("Среднее время выполнения должно быть положительным")
    if actual_seconds <= 0:
        raise ValueError("Фактическое время выполнения должно быть положительным")
    return min(
        999,
        round_half_up(
            Decimal(100) * Decimal(duration_seconds) / Decimal(actual_seconds)
        ),
    )


def speed_bonus_for_points(
    *,
    base_points: int,
    duration_seconds: int,
    actual_seconds: int,
) -> int:
    if base_points < 0:
        raise ValueError("Базовые баллы не могут быть отрицательными")
    if duration_seconds <= 0 or actual_seconds <= 0:
        raise ValueError("Время выполнения должно быть положительным")
    if actual_seconds >= duration_seconds:
        return 0
    return round_half_up(Decimal(base_points) * Decimal("0.20"))


def answered_question_metrics(
    *,
    attempt_count: int,
    duration_seconds: int,
    actual_seconds: int,
    base_points: int,
) -> QuestionMetricsExpectation:
    speed_bonus = speed_bonus_for_points(
        base_points=base_points,
        duration_seconds=duration_seconds,
        actual_seconds=actual_seconds,
    )
    return QuestionMetricsExpectation(
        attempt_count=attempt_count,
        accuracy_percent=accuracy_for_attempt(attempt_count),
        duration_seconds=duration_seconds,
        actual_seconds=actual_seconds,
        speed_percent=speed_for_duration(
            duration_seconds=duration_seconds,
            actual_seconds=actual_seconds,
        ),
        base_points=base_points,
        speed_bonus_points=speed_bonus,
        awarded_points=base_points + speed_bonus,
    )


def aggregate_question_metrics(
    questions: (
        list[QuestionMetricsExpectation] | tuple[QuestionMetricsExpectation, ...]
    ),
) -> AggregateMetricsExpectation:
    if not questions:
        return AggregateMetricsExpectation(
            accuracy_percent=0,
            speed_percent=0,
            points=0,
        )

    return AggregateMetricsExpectation(
        accuracy_percent=round_half_up(
            Decimal(sum(question.accuracy_percent for question in questions))
            / Decimal(len(questions))
        ),
        speed_percent=speed_for_duration(
            duration_seconds=sum(question.duration_seconds for question in questions),
            actual_seconds=sum(question.actual_seconds for question in questions),
        ),
        points=sum(question.awarded_points for question in questions),
    )
