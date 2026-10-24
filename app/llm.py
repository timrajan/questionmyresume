"""DeepSeek calls (OpenAI-compatible API) for job validation and resume judgement."""

import json

from openai import APIError, AsyncOpenAI

from . import config, prompts
from .job_fetcher import JobPage


class LLMError(Exception):
    pass


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if not config.DEEPSEEK_API_KEY:
        raise LLMError("DEEPSEEK_API_KEY is not set. Add it to your .env file.")
    if _client is None:
        _client = AsyncOpenAI(api_key=config.DEEPSEEK_API_KEY, base_url=config.DEEPSEEK_BASE_URL)
    return _client


async def _chat_json(system: str, user: str, temperature: float) -> dict:
    try:
        resp = await _get_client().chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=temperature,
            max_tokens=4096,
        )
    except APIError as exc:
        raise LLMError(f"DeepSeek API error: {exc.message}") from exc

    content = resp.choices[0].message.content or ""
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError("DeepSeek returned a malformed response. Please try again.") from exc


async def classify_job_posting(page: JobPage) -> dict:
    if page.structured:
        # The site explicitly marks this page as a schema.org JobPosting.
        org = page.structured.get("hiringOrganization")
        return {
            "is_job_posting": True,
            "job_title": page.structured.get("title"),
            "company": org.get("name") if isinstance(org, dict) else org,
            "reason": "The page publishes structured JobPosting data.",
        }

    result = await _chat_json(
        prompts.VALIDATE_SYSTEM,
        prompts.VALIDATE_USER.format(url=page.url, title=page.title, text=page.text[:6000]),
        temperature=0,
    )
    return {
        "is_job_posting": bool(result.get("is_job_posting")),
        "job_title": result.get("job_title"),
        "company": result.get("company"),
        "reason": result.get("reason") or "",
    }


async def judge_resume(resume_text: str, page: JobPage) -> dict:
    result = await _chat_json(
        prompts.JUDGE_SYSTEM,
        prompts.JUDGE_USER.format(
            url=page.url,
            job_text=page.text[: config.MAX_JOB_CHARS],
            resume_text=resume_text[: config.MAX_RESUME_CHARS],
        ),
        temperature=0.3,
    )
    return normalize_judgement(result)


def normalize_judgement(raw: dict) -> dict:
    """Coerce the model's JSON into the exact shape the frontend renders."""

    def score(value) -> int:
        try:
            return max(0, min(100, round(float(value))))
        except (TypeError, ValueError):
            return 0

    def dicts(value) -> list[dict]:
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def strings(value) -> list[str]:
        return [str(item) for item in value if item] if isinstance(value, list) else []

    return {
        "job_title": raw.get("job_title") or "",
        "company": raw.get("company") or "",
        "overall_match": score(raw.get("overall_match")),
        "resume_quality": score(raw.get("resume_quality")),
        "verdict": raw.get("verdict") or "",
        "category_scores": [
            {"name": str(c.get("name", "")), "score": score(c.get("score")), "comment": str(c.get("comment", ""))}
            for c in dicts(raw.get("category_scores"))
        ],
        "rejection_reasons": [
            {key: str(r.get(key, "")) for key in ("title", "job_requires", "resume_shows", "fix")}
            for r in dicts(raw.get("rejection_reasons"))[:3]
        ],
        "rewritten_bullets": [
            {key: str(b.get(key, "")) for key in ("original", "rewritten", "why")}
            for b in dicts(raw.get("rewritten_bullets"))[:3]
        ],
        "missing_keywords": strings(raw.get("missing_keywords")),
        "strengths": strings(raw.get("strengths")),
    }
