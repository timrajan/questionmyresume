"""Fetch a job posting URL and extract its readable text."""

import asyncio
import ipaddress
import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
MIN_PAGE_CHARS = 200
BLOCKED_STATUSES = (401, 403, 429, 999)

# Phrases job boards show once a posting is closed (SEEK, Indeed, Greenhouse, Lever, ...).
EXPIRED_PATTERNS = re.compile(
    r"no longer (advertised|available|accepting applications|open)"
    r"|(job|position|posting|listing) (has )?(expired|been filled|been closed|closed)"
    r"|this job is closed",
    re.IGNORECASE,
)

# Anti-bot interstitials (Cloudflare, Akamai, PerimeterX, DataDome) served instead of the real page.
BOT_CHALLENGE_PATTERNS = re.compile(
    r"challenge-platform|cf-chl-|cf_chl_|Just a moment\.\.\.|Attention Required! \| Cloudflare"
    r"|Verify you are human|px-captcha|captcha-delivery\.com|Access Denied</title>",
    re.IGNORECASE,
)


class JobFetchError(Exception):
    def __init__(self, message: str, code: str = "fetch_failed"):
        super().__init__(message)
        # "unreadable" tells the frontend to offer pasting the description instead.
        self.code = code


@dataclass
class JobPage:
    url: str
    title: str
    text: str
    # schema.org JobPosting data, when the site publishes it (most ATSs do).
    structured: dict | None = None


async def fetch_job_page(url: str) -> JobPage:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise JobFetchError("Enter a full URL starting with http:// or https://")

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            event_hooks={"request": [_block_private_hosts]},
        ) as client:
            resp = await client.get(url)
    except JobFetchError:
        raise
    except httpx.TimeoutException:
        raise JobFetchError("The job posting site took too long to respond.")
    except httpx.HTTPError as exc:
        raise JobFetchError(f"Couldn't reach that URL: {exc}")

    if resp.status_code == 404:
        raise JobFetchError("That page doesn't exist (404). The posting may have been taken down.")
    if resp.status_code >= 400 and resp.status_code not in BLOCKED_STATUSES:
        raise JobFetchError(f"The site returned an error (HTTP {resp.status_code}).")
    if "html" not in resp.headers.get("content-type", "html"):
        raise JobFetchError("That URL isn't a web page.")

    html = resp.text
    blocked = resp.status_code in BLOCKED_STATUSES or is_bot_challenge(html)
    page = parse_job_html(str(resp.url), html)
    if not blocked:
        _raise_if_expired(page)

    if blocked or len(page.text) < MIN_PAGE_CHARS:
        # Bot-blocked or built client-side: try again in a real (headless) browser.
        rendered = await _render_with_browser(url)
        if rendered:
            blocked = is_bot_challenge(rendered[1])
            page = parse_job_html(*rendered)
            if not blocked:
                _raise_if_expired(page)

    if blocked:
        raise JobFetchError(
            "This job site blocks automated access from our server. "
            "Paste the job description below instead.",
            code="unreadable",
        )
    if len(page.text) < MIN_PAGE_CHARS:
        raise JobFetchError(
            "Couldn't read the job description from that page (it may need a login). "
            "Paste the job description below instead.",
            code="unreadable",
        )
    return page


def is_bot_challenge(html: str) -> bool:
    return bool(BOT_CHALLENGE_PATTERNS.search(html[:50_000]))


def page_from_text(url: str, text: str) -> JobPage:
    """A job page built from a description the user pasted in."""
    return JobPage(url=url or "(pasted description)", title="", text=text.strip())


def _raise_if_expired(page: JobPage) -> None:
    if not page.structured and EXPIRED_PATTERNS.search(page.text[:2000]):
        raise JobFetchError(
            "This job posting has expired or been closed, so there's no description to compare against. "
            "Try a job that's currently advertised.",
            code="expired",
        )


async def _render_with_browser(url: str) -> tuple[str, str] | None:
    """Load the page in headless Chromium so JavaScript-rendered content appears."""
    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright
    except ImportError:
        return None

    async def guard(route):
        # Same private-address protection as the plain fetch, for navigations and redirects.
        if route.request.is_navigation_request():
            try:
                await _ensure_public_host(urlparse(route.request.url).hostname or "")
            except JobFetchError:
                return await route.abort()
        await route.continue_()

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                tab = await browser.new_page(user_agent=USER_AGENT)
                await tab.route("**/*", guard)
                await tab.goto(url, wait_until="domcontentloaded", timeout=20_000)
                try:
                    await tab.wait_for_load_state("networkidle", timeout=8_000)
                except PlaywrightError:
                    pass  # some sites never go idle; use what has rendered
                return tab.url, await tab.content()
            finally:
                await browser.close()
    except PlaywrightError:
        return None


def parse_job_html(url: str, html: str) -> JobPage:
    soup = BeautifulSoup(html, "html.parser")
    structured = find_job_posting_jsonld(soup)

    title = soup.title.get_text(strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)

    if structured:
        description = BeautifulSoup(structured.get("description", ""), "html.parser").get_text(
            "\n", strip=True
        )
        # Structured data is the cleanest source; lead with it.
        header = "\n".join(
            f"{label}: {value}"
            for label, value in (
                ("Title", structured.get("title")),
                ("Company", _org_name(structured.get("hiringOrganization"))),
            )
            if value
        )
        text = f"{header}\n\n{description}\n\n{text}"

    text = re.sub(r"\n{3,}", "\n\n", text)
    return JobPage(url=url, title=title, text=text.strip(), structured=structured)


def find_job_posting_jsonld(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _walk_jsonld(data):
            types = item.get("@type")
            types = types if isinstance(types, list) else [types]
            if "JobPosting" in types:
                return item
    return None


def _walk_jsonld(data):
    if isinstance(data, list):
        for entry in data:
            yield from _walk_jsonld(entry)
    elif isinstance(data, dict):
        yield data
        if "@graph" in data:
            yield from _walk_jsonld(data["@graph"])


def _org_name(org) -> str | None:
    if isinstance(org, dict):
        return org.get("name")
    return org if isinstance(org, str) else None


async def _block_private_hosts(request: httpx.Request) -> None:
    """Refuse to fetch localhost/internal addresses, including via redirects."""
    await _ensure_public_host(request.url.host)


async def _ensure_public_host(host: str) -> None:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except OSError:
        raise JobFetchError(f"Couldn't find the website '{host}'. Check the URL.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise JobFetchError("That URL points to a private or local address.")
