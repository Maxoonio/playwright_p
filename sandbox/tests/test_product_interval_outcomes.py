import pytest
from models import CheckStatus
from models import TestDriveUser as DriveUser
from pages.basic_modules_page import ProductUnavailableError
from results import ResultCollector
from scenarios.runner import _run_sandbox_product_interval_check


def _user() -> DriveUser:
    return DriveUser(
        id="perfect",
        username="playwright_user_1",
        password="secret",
        enabled=True,
        test_profile="perfect",
    )


@pytest.mark.asyncio
async def test_unavailable_sandbox_product_is_skipped_and_matrix_flow_continues() -> (
    None
):
    collector = ResultCollector(scenario_label="Sandbox interval")

    async def unavailable() -> None:
        raise ProductUnavailableError("product DOM is unavailable")

    should_continue = await _run_sandbox_product_interval_check(
        collector=collector,
        user=_user(),
        page=None,  # type: ignore[arg-type]
        operation=unavailable,
        section=1,
        step=3,
    )

    assert should_continue is True
    assert collector.results[0].status is CheckStatus.SKIPPED
    assert collector.results[0].action == "SANDBOX_PRODUCT_INTERVAL_CONFIRMED"
    assert "product DOM is unavailable" in collector.results[0].message


@pytest.mark.asyncio
async def test_sandbox_interval_mismatch_is_failed_without_raising() -> None:
    collector = ResultCollector(scenario_label="Sandbox interval")

    async def mismatch() -> None:
        raise AssertionError("CTF interval differs from product")

    should_continue = await _run_sandbox_product_interval_check(
        collector=collector,
        user=_user(),
        page=None,  # type: ignore[arg-type]
        operation=mismatch,
        section=5,
        step=1,
        action="CTF_SANDBOX_PRODUCT_INTERVAL_CONFIRMED",
    )

    assert should_continue is False
    result = collector.results[0]
    assert result.status is CheckStatus.FAILED
    assert result.action == "CTF_SANDBOX_PRODUCT_INTERVAL_CONFIRMED"
    assert "CTF interval differs from product" in result.message


@pytest.mark.asyncio
async def test_sandbox_matching_interval_is_passed() -> None:
    collector = ResultCollector(scenario_label="Sandbox interval")

    async def matches() -> None:
        return None

    succeeded = await _run_sandbox_product_interval_check(
        collector=collector,
        user=_user(),
        page=None,  # type: ignore[arg-type]
        operation=matches,
        section=1,
        step=3,
    )

    assert succeeded is True
    assert collector.results[0].status is CheckStatus.PASSED
