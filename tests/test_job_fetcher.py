import json

import pytest

from app.job_fetcher import JobFetchError, _raise_if_expired, fetch_job_page, parse_job_html


def test_parses_jsonld_job_posting():
    ld = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "Organization", "name": "Acme"},
            {
                "@type": "JobPosting",
                "title": "Data Engineer",
                "hiringOrganization": {"@type": "Organization", "name": "Acme"},
                "description": "<p>Build <b>Spark</b> pipelines.</p>",
            },
        ],
    }
    html = f'<html><head><title>Job</title><script type="application/ld+json">{json.dumps(ld)}</script></head><body>Apply now</body></html>'
    page = parse_job_html("https://acme.com/jobs/1", html)
    assert page.structured["title"] == "Data Engineer"
    assert "Title: Data Engineer" in page.text and "Company: Acme" in page.text
    assert "Build\nSpark\npipelines." in page.text


def test_plain_page_has_no_structured_data():
    page = parse_job_html("https://x.com", "<html><body><nav>Menu</nav><p>Hello</p></body></html>")
    assert page.structured is None
    assert page.text == "Hello"


@pytest.mark.parametrize("url", ["ftp://example.com/job", "not a url", "http://127.0.0.1:8000/", "http://localhost/"])
async def test_rejects_bad_or_private_urls(url):
    with pytest.raises(JobFetchError):
        await fetch_job_page(url)


@pytest.mark.parametrize("text", [
    "AUTOMATION TESTER Job in Melbourne VIC - SEEK\nThis job is no longer advertised",
    "Sorry, this position has been filled.",
    "This job is closed",
])
def test_detects_expired_postings(text):
    page = parse_job_html("https://x.com", f"<html><body><p>{text}</p></body></html>")
    with pytest.raises(JobFetchError) as exc:
        _raise_if_expired(page)
    assert exc.value.code == "expired"


def test_live_posting_is_not_expired():
    page = parse_job_html("https://x.com", "<html><body><p>Senior Engineer. We are hiring!</p></body></html>")
    _raise_if_expired(page)


def test_detects_cloudflare_challenge():
    from app.job_fetcher import is_bot_challenge
    assert is_bot_challenge('<html><head><title>Just a moment...</title></head><script src="/cdn-cgi/challenge-platform/x.js"></script></html>')
    assert not is_bot_challenge("<html><head><title>Software Tester Job - SEEK</title></head><body>Apply</body></html>")
