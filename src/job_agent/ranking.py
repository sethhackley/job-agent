"""Rank a batch of job descriptions you've saved (e.g. pasted from job alert emails).

Each job is a .txt or .md file in a folder (default: config.JOBS_INBOX). Optional header lines
at the top of the file set the details; otherwise the filename "Company - Role.txt" is used:

    Company: Example Corp
    Role: QA Engineer
    URL: https://example.com/jobs/123

    <full job description>
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .intent import LEVELS, allowed_levels, expand_intent, title_level
from .letters import score_fit
from .llm import LLM

HEADER = re.compile(r"^(company|role|title|url|link)\s*:\s*(.+)$", re.I)


@dataclass
class JobPosting:
    path: str
    company: str
    role: str
    url: str
    text: str
    match: float = 0.0
    fit: dict | None = None
    flags: list[str] = field(default_factory=list)
    tracker_id: int | None = None
    tracker_status: str = ""


def parse_job_file(path: str | Path) -> JobPosting:
    path = Path(path)
    lines = path.read_text(errors="replace").splitlines()
    meta, body_start = {}, 0
    for i, line in enumerate(lines):
        m = HEADER.match(line.strip())
        if m:
            key = {"title": "role", "link": "url"}.get(m.group(1).lower(), m.group(1).lower())
            meta[key] = m.group(2).strip()
            body_start = i + 1
        elif line.strip() or meta:
            break
    text = "\n".join(lines[body_start:]).strip()
    stem_company, _, stem_role = path.stem.partition(" - ")
    return JobPosting(
        path=str(path),
        company=meta.get("company") or (stem_company.strip() if stem_role else ""),
        role=meta.get("role") or (stem_role.strip() if stem_role else path.stem),
        url=meta.get("url", ""),
        text=text,
    )


def load_jobs(folder: str | Path | None = None) -> list[JobPosting]:
    if folder is None:
        from .config import JOBS_INBOX
        folder = JOBS_INBOX
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    jobs = [parse_job_file(p) for p in sorted(folder.iterdir())
            if p.suffix.lower() in (".txt", ".md") and p.is_file()]
    return [j for j in jobs if len(j.text) >= 200]  # skip empty/accidental files


def rank_jobs(llm: LLM, profile: dict, jobs: list[JobPosting], query: str = "",
              top_n: int = 10, tracker=None) -> tuple[list[JobPosting], dict | None]:
    """Order jobs best-first. Returns (jobs, search_plan).

    1. Embedding match against the resume (and the expanded search intent, if a query is given).
    2. Flags titles that match the plan's exclusions or sit above the candidate's level.
    3. Full LLM fit score for the top_n by match; those are ordered by fit, the rest by match.
    4. If a tracker is given, records each scored job and notes ones already in it.
    """
    if not jobs:
        return [], None
    plan = expand_intent(llm, profile, query) if query else None
    resume = profile.get("resume_text") or profile.get("summary", "")
    docs = llm.embed([f"{j.role}\n{j.text[:6000]}" for j in jobs], kind="document")
    match = docs @ llm.embed([resume[:6000]], kind="query")[0]
    if plan:
        intent_vec = llm.embed([plan["ideal_job"]], kind="query")[0]
        match = 0.5 * match + 0.5 * (docs @ intent_vec)

    years = float(profile.get("years_experience") or 0)
    top_level = max(LEVELS.index(lvl) for lvl in allowed_levels(years))
    excludes = [e.lower() for e in (plan or {}).get("exclude", []) if e.strip()]
    for job, m in zip(jobs, np.asarray(match, dtype=float)):
        job.match = round(float(m), 3)
        lvl = title_level(job.role)
        if lvl and LEVELS.index(lvl) > top_level:
            job.flags.append(f"above your level ({lvl})")
        hit = next((e for e in excludes if re.search(rf"\b{re.escape(e)}\b", job.role.lower())), None)
        if hit:
            job.flags.append(f"title matches exclusion '{hit}'")
        if tracker:
            existing = tracker.find(job.text, job.company, job.role)
            if existing:
                job.tracker_id, job.tracker_status = existing["id"], existing["status"]
                if existing["status"] not in ("scored", "letter_written"):
                    job.flags.append(f"already {existing['status']}")

    by_match = sorted(jobs, key=lambda j: j.match, reverse=True)
    for job in by_match[:top_n]:
        job.fit = score_fit(llm, profile, job.text)
        if tracker:
            job.tracker_id = tracker.record_score(job.company or "Unknown", job.role, job.text,
                                                  job.fit, url=job.url)
            job.tracker_status = tracker.get(job.tracker_id)["status"]

    scored = sorted((j for j in jobs if j.fit), key=lambda j: (j.fit["fit"], j.match), reverse=True)
    rest = [j for j in by_match if not j.fit]
    return scored + rest, plan
