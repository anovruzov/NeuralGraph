"""Adapter normalization, ATS detection and deduplication."""
from __future__ import annotations

import httpx
import pytest

from job_harness.ats import detect as ats
from job_harness.database.models import Job
from job_harness.discovery.ashby import AshbyAdapter
from job_harness.discovery.base import html_to_text, parse_salary
from job_harness.discovery.dedupe import Deduplicator
from job_harness.discovery.engine import ADAPTERS, DiscoveryEngine
from job_harness.discovery.generic_url import GenericUrlAdapter
from job_harness.discovery.greenhouse import GreenhouseAdapter
from job_harness.discovery.lever import LeverAdapter
from job_harness.discovery.workday import WorkdayAdapter

GREENHOUSE = {"jobs": [{
    "id": 101, "title": "AI Engineer",
    "absolute_url": "https://boards.greenhouse.io/acme/jobs/101",
    "updated_at": "2026-09-04T10:00:00Z", "location": {"name": "Remote - US"},
    "company_name": "Acme AI",
    "content": "<p>Build <b>LLM</b> agents.</p><ul><li>2+ years</li></ul><p>$150,000 - $200,000</p>",
}]}
LEVER = [{
    "id": "abc-1", "text": "Research Engineer",
    "hostedUrl": "https://jobs.lever.co/frontier/abc-1", "createdAt": 1757000000000,
    "categories": {"location": "San Francisco", "commitment": "Full-time"},
    "description": "<p>Post-training research.</p>", "lists": [], "additional": "",
}]
ASHBY = {"jobs": [{
    "id": "ash-1", "title": "LLM Engineer",
    "applyUrl": "https://jobs.ashbyhq.com/nova/ash-1/application",
    "jobUrl": "https://jobs.ashbyhq.com/nova/ash-1", "publishedAt": "2026-09-05T00:00:00Z",
    "location": "Remote", "isRemote": True, "organizationName": "Nova",
    "descriptionPlain": "Build retrieval systems.",
    "compensation": {"compensationTiers": [{"minValue": 160000, "maxValue": 210000}]},
}]}
WORKDAY = {"total": 1, "jobPostings": [{
    "title": "Machine Learning Engineer", "externalPath": "/job/Austin/ML-Eng_R-1",
    "locationsText": "Austin, TX", "postedOn": "Posted 2 Days Ago", "bulletFields": ["R-1"],
}]}


def mock_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "greenhouse" in url:
            return httpx.Response(200, json=GREENHOUSE)
        if "lever" in url:
            return httpx.Response(200, json=LEVER)
        if "ashby" in url:
            return httpx.Response(200, json=ASHBY)
        if "workday" in url:
            return httpx.Response(200, json=WORKDAY)
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_greenhouse_normalization(config):
    jobs = list(GreenhouseAdapter(config.discovery, mock_client()).discover("acme"))
    assert len(jobs) == 1
    job = jobs[0]
    assert job.company == "Acme AI" and job.title == "AI Engineer"
    assert job.remote_status == "remote"
    assert job.salary_min == 150000 and job.salary_max == 200000
    assert job.ats_type == "greenhouse" and job.ats_job_key == "greenhouse:acme:101"
    assert "<p>" not in job.description and "LLM agents" in job.description


def test_lever_normalization(config):
    job = list(LeverAdapter(config.discovery, mock_client()).discover("frontier"))[0]
    assert job.title == "Research Engineer"
    assert job.employment_type == "Full-time"
    assert job.ats_type == "lever" and job.posted_date is not None


def test_ashby_normalization(config):
    job = list(AshbyAdapter(config.discovery, mock_client()).discover("nova"))[0]
    assert job.company == "Nova" and job.remote_status == "remote"
    assert job.salary_min == 160000 and job.salary_max == 210000


def test_workday_normalization(config):
    job = list(WorkdayAdapter(config.discovery, mock_client()).discover("bigco/Careers"))[0]
    assert job.title == "Machine Learning Engineer"
    assert job.posted_date is not None
    assert job.canonical_apply_url.endswith("/job/Austin/ML-Eng_R-1")


@pytest.mark.parametrize("target,expected", [
    ("acme/External", ("acme.wd1.myworkdayjobs.com", "acme", "External")),
    ("https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/x",
     ("acme.wd5.myworkdayjobs.com", "acme", "Careers")),
    ("https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Careers/jobs",
     ("acme.wd1.myworkdayjobs.com", "acme", "Careers")),
])
def test_workday_target_parsing(target, expected):
    assert WorkdayAdapter.parse_target(target) == expected


def test_board_tokens_accept_urls():
    assert GreenhouseAdapter._board_token("https://boards.greenhouse.io/acme/jobs/1") == "acme"
    assert LeverAdapter._board_token("https://jobs.lever.co/frontier/abc") == "frontier"
    assert AshbyAdapter._board_token("https://jobs.ashbyhq.com/nova/x") == "nova"


def test_adapter_failure_is_contained(config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert list(GreenhouseAdapter(config.discovery, client).discover("acme")) == []


@pytest.mark.parametrize("url,expected", [
    ("https://boards.greenhouse.io/acme/jobs/1", "greenhouse"),
    ("https://jobs.lever.co/frontier/abc", "lever"),
    ("https://jobs.ashbyhq.com/nova/x", "ashby"),
    ("https://acme.wd1.myworkdayjobs.com/Careers", "workday"),
    ("https://apply.workable.com/acme/j/1", "workable"),
    ("https://example.com/careers/1", "unknown"),
])
def test_ats_detection_from_url(url, expected):
    assert ats.detect(url).ats_type == expected


def test_ats_detection_from_dom():
    assert ats.detect("https://example.com/x", '<div id="grnhse_app"></div>').ats_type == \
        "greenhouse"


def test_apply_url_derivation():
    assert ats.apply_url_for("https://jobs.lever.co/x/1", "lever").endswith("/apply")
    assert ats.apply_url_for("https://jobs.ashbyhq.com/x/1", "ashby").endswith("/application")
    assert ats.apply_url_for("https://jobs.lever.co/x/1/apply", "lever").endswith("/apply")
    assert ats.apply_url_for("https://boards.greenhouse.io/x/jobs/1", "greenhouse") == \
        "https://boards.greenhouse.io/x/jobs/1"


def test_html_to_text_preserves_structure():
    text = html_to_text("<div><p>Hello &amp; welcome</p><ul><li>One</li><li>Two</li></ul>"
                        "<script>bad()</script></div>")
    assert text == "Hello & welcome\nOne\nTwo"


@pytest.mark.parametrize("text,expected", [
    ("The range is $150,000 - $200,000 per year", (150000, 200000)),
    ("$120k-$160k", (120000, 160000)),
    ("$15 - $25 per hour", (None, None)),
    ("no salary here", (None, None)),
])
def test_salary_parsing(text, expected):
    assert parse_salary(text) == expected


def test_jsonld_job_posting_is_parsed(config):
    html = """<html><head><script type="application/ld+json">
    {"@context":"https://schema.org/","@type":"JobPosting","title":"AI Engineer",
     "description":"<p>Build agents</p>","datePosted":"2026-09-04",
     "hiringOrganization":{"@type":"Organization","name":"Acme AI"},
     "jobLocationType":"TELECOMMUTE","url":"job-1.html",
     "baseSalary":{"@type":"MonetaryAmount","currency":"USD",
       "value":{"@type":"QuantitativeValue","minValue":150000,"maxValue":200000,
                "unitText":"YEAR"}}}
    </script></head><body></body></html>"""
    adapter = GenericUrlAdapter(config.discovery, mock_client())
    jobs = list(adapter._from_jsonld(html, "https://example.com/careers/index.html"))
    assert len(jobs) == 1
    job = jobs[0]
    assert job.company == "Acme AI"
    assert job.remote_status == "remote"
    assert job.salary_min == 150000
    # A relative url must be resolved against the page it was found on.
    assert job.canonical_apply_url == "https://example.com/careers/job-1.html"


def test_hourly_salary_is_not_treated_as_annual(config):
    adapter = GenericUrlAdapter(config.discovery, mock_client())
    assert adapter._salary({"baseSalary": {"value": {"minValue": 50, "maxValue": 80,
                                                     "unitText": "HOUR"}}}) == (None, None)


# ------------------------------------------------------------------- dedupe

def make_job(**kw) -> Job:
    base = dict(company="Acme AI", title="AI Engineer",
                canonical_apply_url="https://boards.greenhouse.io/acme/jobs/1",
                ats_type="greenhouse", ats_job_key="greenhouse:acme:1",
                description="Build LLM agents.")
    base.update(kw)
    return Job(**base)


def test_dedupe_by_canonical_url(db, qwen):
    dedupe = Deduplicator(db, qwen)
    db.upsert_job(make_job())
    verdict = dedupe.check(make_job(
        canonical_apply_url="https://boards.greenhouse.io/acme/jobs/1?utm_source=x",
        ats_job_key="other"))
    assert verdict.is_duplicate and verdict.rule == "canonical_url"


def test_dedupe_by_ats_key(db, qwen):
    dedupe = Deduplicator(db, qwen)
    db.upsert_job(make_job())
    verdict = dedupe.check(make_job(canonical_apply_url="https://example.com/other",
                                    title="AI Engineering"))
    assert verdict.is_duplicate and verdict.rule == "ats_job_key"


def test_dedupe_by_company_and_title(db, qwen):
    dedupe = Deduplicator(db, qwen)
    db.upsert_job(make_job())
    verdict = dedupe.check(make_job(canonical_apply_url="https://jobs.lever.co/acme/2",
                                    ats_type="lever", ats_job_key="lever:acme:2",
                                    company="Acme AI Inc.", title="AI Engineer (Remote)"))
    assert verdict.is_duplicate and verdict.rule == "company_title"


def test_distinct_jobs_are_not_deduplicated(db, qwen):
    dedupe = Deduplicator(db, qwen)
    db.upsert_job(make_job())
    verdict = dedupe.check(make_job(company="Nova", title="Research Engineer",
                                    canonical_apply_url="https://jobs.ashbyhq.com/nova/1",
                                    ats_job_key="ashby:nova:1",
                                    description="Totally different role."))
    assert not verdict.is_duplicate


def test_discovery_engine_ingests_and_deduplicates(config, db, qwen):
    config.discovery.boards = {"greenhouse": ["acme"], "lever": ["frontier"],
                               "ashby": ["nova"], "workday": ["bigco/Careers"]}
    engine = DiscoveryEngine(config, db, qwen, "test-run")
    engine._client = mock_client()
    first = engine.run()
    assert first.discovered == 4
    second = engine.run()
    assert second.discovered == 0 and second.duplicates == 4
    engine.close()


def test_all_adapters_are_registered():
    assert {"greenhouse", "lever", "ashby", "workday", "generic_url"} <= set(ADAPTERS)


# --------------------------------------------------- additional ATS adapters

SMARTRECRUITERS_LIST = {"totalFound": 1, "content": [{
    "id": "744000",
    "name": "AI Engineer",
    "releasedDate": "2026-09-04T09:00:00.000Z",
    "location": {"city": "Austin", "region": "TX", "country": "us", "remote": True},
    "company": {"name": "Acme AI"},
    "department": {"label": "Engineering"},
    "typeOfEmployment": {"label": "Full-time"},
    "applyUrl": "https://jobs.smartrecruiters.com/AcmeAI/744000",
}]}
SMARTRECRUITERS_DETAIL = {"jobAd": {"sections": {
    "jobDescription": {"text": "<p>Build <b>LLM</b> systems.</p>"},
    "qualifications": {"text": "<ul><li>2+ years</li></ul><p>$150,000 - $200,000</p>"},
}}}
WORKABLE = {"jobs": [{
    "title": "Machine Learning Engineer", "shortcode": "ABC123",
    "published_on": "2026-09-05", "company_name": "Nova",
    "location": {"city": "Remote", "country": "United States", "workplace": "remote"},
    "employment_type": "Full-time",
    "description": "<p>Retrieval systems.</p>", "requirements": "<p>2+ years</p>",
    "application_url": "https://apply.workable.com/nova/j/ABC123/",
}]}


def ats_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "smartrecruiters" in url and url.rstrip("/").endswith("744000"):
            return httpx.Response(200, json=SMARTRECRUITERS_DETAIL)
        if "smartrecruiters" in url:
            return httpx.Response(200, json=SMARTRECRUITERS_LIST)
        if "workable" in url:
            return httpx.Response(200, json=WORKABLE)
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_smartrecruiters_normalization(config):
    from job_harness.discovery.smartrecruiters import SmartRecruitersAdapter
    job = list(SmartRecruitersAdapter(config.discovery, ats_client()).discover("AcmeAI"))[0]
    assert job.company == "Acme AI" and job.title == "AI Engineer"
    assert job.location == "Austin, TX, us"
    assert job.remote_status == "remote"
    assert job.employment_type == "Full-time"
    assert job.ats_job_key == "smartrecruiters:AcmeAI:744000"
    assert "LLM systems" in job.description        # detail endpoint was fetched
    assert job.salary_min == 150000


def test_smartrecruiters_skips_detail_for_irrelevant_titles(config):
    from job_harness.discovery.smartrecruiters import SmartRecruitersAdapter
    payload = {"totalFound": 1, "content": [
        {**SMARTRECRUITERS_LIST["content"][0], "name": "Office Manager"}]}

    calls = {"detail": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.rstrip("/").endswith("744000"):
            calls["detail"] += 1
            return httpx.Response(200, json=SMARTRECRUITERS_DETAIL)
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    job = list(SmartRecruitersAdapter(config.discovery, client).discover("AcmeAI"))[0]
    assert job.description == ""
    assert calls["detail"] == 0                    # no request wasted


def test_workable_normalization(config):
    from job_harness.discovery.workable import WorkableAdapter
    job = list(WorkableAdapter(config.discovery, ats_client()).discover("nova"))[0]
    assert job.company == "Nova" and job.title == "Machine Learning Engineer"
    assert job.remote_status == "remote"
    assert job.ats_job_key == "workable:nova:ABC123"
    assert "Retrieval systems" in job.description and "2+ years" in job.description


@pytest.mark.parametrize("target,expected", [
    ("AcmeAI", "AcmeAI"),
    ("https://careers.smartrecruiters.com/AcmeAI", "AcmeAI"),
    ("https://jobs.smartrecruiters.com/AcmeAI/744000", "AcmeAI"),
])
def test_smartrecruiters_board_tokens(target, expected):
    from job_harness.discovery.smartrecruiters import SmartRecruitersAdapter
    assert SmartRecruitersAdapter._board_token(target) == expected


@pytest.mark.parametrize("target,expected", [
    ("nova", "nova"),
    ("https://apply.workable.com/nova/", "nova"),
    ("https://apply.workable.com/nova/j/ABC123/", "nova"),
])
def test_workable_board_tokens(target, expected):
    from job_harness.discovery.workable import WorkableAdapter
    assert WorkableAdapter._board_token(target) == expected


def test_new_adapters_are_registered():
    assert {"smartrecruiters", "workable"} <= set(ADAPTERS)


def test_a_custom_adapter_can_be_registered(config):
    """The extension point advertised in the README."""
    from job_harness.discovery.base import DiscoveryAdapter as Base
    from job_harness.discovery.engine import register_adapter

    class CustomAdapter(Base):
        name = "custom_ats"
        ats_type = "custom_ats"

        def discover(self, target):
            yield Job(company="Custom Co", title="AI Engineer",
                      canonical_apply_url=f"https://custom.example/jobs/{target}",
                      ats_type=self.ats_type, ats_job_key=f"custom:{target}")

    register_adapter("custom_ats", CustomAdapter)
    assert ADAPTERS["custom_ats"] is CustomAdapter
    jobs = list(CustomAdapter(config.discovery, ats_client()).discover("42"))
    assert jobs[0].ats_type == "custom_ats"
    del ADAPTERS["custom_ats"]


# ---------------------------------------------------- semantic title expansion

def test_title_gate_expands_target_roles(config, db, qwen):
    engine = DiscoveryEngine(config, db, qwen, "test-run")
    gate = engine.title_gate()
    assert set(config.discovery.target_roles) <= set(gate)
    assert len(gate) > len(config.discovery.target_roles)
    engine.close()


def test_title_expansion_is_cached_across_engines(config, db, qwen):
    first = DiscoveryEngine(config, db, qwen, "test-run")
    first.title_gate()
    calls = qwen.stats["requests"]
    first.close()

    second = DiscoveryEngine(config, db, qwen, "test-run")
    second.title_gate()
    assert qwen.stats["requests"] == calls          # served from SQLite
    second.close()


def test_title_gate_works_without_a_model(config, db):
    engine = DiscoveryEngine(config, db, qwen=None, run_id="test-run")
    assert engine.title_gate() == config.discovery.target_roles
    engine.close()
