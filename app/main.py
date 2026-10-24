"""QuestionMyResume web app: validate a job posting URL, then judge a resume against it."""

import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .job_fetcher import JobFetchError, JobPage, fetch_job_page, page_from_text
from .llm import LLMError, classify_job_posting, judge_resume
from .resume_parser import ResumeParseError, extract_text

logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="QuestionMyResume")

# Validated job postings, keyed by job_id, so /api/analyze doesn't re-fetch the page.
JOB_TTL_SECONDS = 60 * 60
_jobs: dict[str, tuple[float, JobPage]] = {}


MIN_PASTED_CHARS = 200


class ValidateJobRequest(BaseModel):
    url: str = ""
    # Used instead of fetching the URL when the page can't be read.
    description: str = ""


@app.post("/api/validate-job")
async def validate_job(req: ValidateJobRequest):
    try:
        if req.description.strip():
            if len(req.description.strip()) < MIN_PASTED_CHARS:
                raise HTTPException(422, "The pasted job description is too short. Paste the full posting.")
            page = page_from_text(req.url, req.description)
        else:
            page = await fetch_job_page(req.url)
        verdict = await classify_job_posting(page)
    except JobFetchError as exc:
        logger.info("Job fetch failed (%s) for %s: %s", exc.code, req.url, exc)
        raise HTTPException(422, {"message": str(exc), "code": exc.code})
    except LLMError as exc:
        raise HTTPException(502, str(exc))

    if not verdict["is_job_posting"]:
        return {"valid": False, "reason": verdict["reason"] or "This doesn't look like a job posting."}

    _prune_jobs()
    job_id = uuid.uuid4().hex
    _jobs[job_id] = (time.time(), page)
    return {
        "valid": True,
        "job_id": job_id,
        "job_title": verdict["job_title"],
        "company": verdict["company"],
        "reason": verdict["reason"],
    }


@app.post("/api/analyze")
async def analyze(resume: UploadFile = File(...), job_id: str = Form(...)):
    if Path(resume.filename or "").suffix.lower() not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(415, "Only PDF, TXT, DOC and DOCX files are allowed.")

    data = await resume.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Resume file is too large (max 5 MB).")

    entry = _jobs.get(job_id)
    if entry is None:
        raise HTTPException(404, "The job posting session expired. Please validate the URL again.")

    try:
        resume_text = extract_text(resume.filename, data)
        return await judge_resume(resume_text, entry[1])
    except ResumeParseError as exc:
        raise HTTPException(422, str(exc))
    except LLMError as exc:
        raise HTTPException(502, str(exc))


def _prune_jobs() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    for key in [k for k, (ts, _) in _jobs.items() if ts < cutoff]:
        del _jobs[key]


app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")
