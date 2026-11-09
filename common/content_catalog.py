from urllib.parse import quote, urlencode

import settings
from models import CtfContentSource, TestDriveContentSource

SIEM_CONTENT_GITLAB_REF = "testdrive"


def _repository_file_urls(
    relative_path: str,
    *,
    git_ref: str,
) -> tuple[str, str]:
    encoded_project = quote(settings.CONTENT_GITLAB_PROJECT, safe="")
    encoded_path = quote(relative_path, safe="")
    query = urlencode({"ref": git_ref})
    api_url = (
        f"{settings.CONTENT_GITLAB_BASE_URL}/api/v4/projects/{encoded_project}"
        f"/repository/files/{encoded_path}/raw?{query}"
    )
    web_url = (
        f"{settings.CONTENT_GITLAB_BASE_URL}/"
        f"{settings.CONTENT_GITLAB_PROJECT}/-/blob/"
        f"{quote(git_ref, safe='')}/{relative_path}"
        "?ref_type=heads"
    )
    return api_url, web_url


def _basic_scenario_source(
    *,
    product_slug: str,
    case_id: str,
    git_ref: str,
) -> TestDriveContentSource:
    base_path = f"MatrixInjector/Scenarios/{product_slug}/cases/{case_id}/doc"
    exercises_path = f"{base_path}/exercises.json"
    sections_path = f"{base_path}/sections.json"
    exercises_url, exercises_web_url = _repository_file_urls(
        exercises_path,
        git_ref=git_ref,
    )
    sections_url, sections_web_url = _repository_file_urls(
        sections_path,
        git_ref=git_ref,
    )
    return TestDriveContentSource(
        product_slug=product_slug,
        case_id=case_id,
        exercises_path=exercises_path,
        sections_path=sections_path,
        exercises_url=exercises_url,
        sections_url=sections_url,
        exercises_web_url=exercises_web_url,
        sections_web_url=sections_web_url,
    )


def _nad_c3_source() -> TestDriveContentSource:
    return _basic_scenario_source(
        product_slug="nad",
        case_id="c-3",
        git_ref=settings.CONTENT_GITLAB_REF,
    )


NAD_C3_CONTENT_SOURCE = _nad_c3_source()


def _ctf_scenario_source(
    *,
    product_slug: str,
    case_id: str,
    git_ref: str,
) -> CtfContentSource:
    exercises_path = (
        f"MatrixInjector/Scenarios/{product_slug}/cases/{case_id}/doc/exercises.json"
    )
    exercises_url, exercises_web_url = _repository_file_urls(
        exercises_path,
        git_ref=git_ref,
    )
    return CtfContentSource(
        product_slug=product_slug,
        case_id=case_id,
        git_ref=git_ref,
        exercises_path=exercises_path,
        exercises_url=exercises_url,
        exercises_web_url=exercises_web_url,
    )


def _nad_ctf_source() -> CtfContentSource:
    return _ctf_scenario_source(
        product_slug="nad",
        case_id="c-1",
        git_ref=settings.CTF_CONTENT_GITLAB_REF,
    )


NAD_CTF_CONTENT_SOURCE = _nad_ctf_source()


def _siem_c2_source() -> TestDriveContentSource:
    return _basic_scenario_source(
        product_slug="siem",
        case_id="c-2",
        git_ref=SIEM_CONTENT_GITLAB_REF,
    )


SIEM_C2_CONTENT_SOURCE = _siem_c2_source()


def _siem_ctf_source() -> CtfContentSource:
    return _ctf_scenario_source(
        product_slug="siem",
        case_id="c-20",
        git_ref=SIEM_CONTENT_GITLAB_REF,
    )


SIEM_CTF_CONTENT_SOURCE = _siem_ctf_source()


def _sandbox_c1_source() -> TestDriveContentSource:
    return _basic_scenario_source(
        product_slug="sandbox",
        case_id="c-1",
        git_ref=settings.CONTENT_GITLAB_REF,
    )


SANDBOX_C1_CONTENT_SOURCE = _sandbox_c1_source()


def _sandbox_ctf_source() -> CtfContentSource:
    return _ctf_scenario_source(
        product_slug="sandbox",
        case_id="c-10",
        git_ref=settings.CTF_CONTENT_GITLAB_REF,
    )


SANDBOX_CTF_CONTENT_SOURCE = _sandbox_ctf_source()
