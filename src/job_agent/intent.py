"""Turn what the user typed ("android dev", "something in games") into a broad search plan."""
from __future__ import annotations

import re

from .llm import LLM

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "titles": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "exclude": {"type": "array", "items": {"type": "string"}},
        "seniority": {"type": "array", "items": {"type": "string"}},
        "ideal_job": {"type": "string"},
    },
    "required": ["titles", "keywords", "exclude", "seniority", "ideal_job"],
}

INTENT_SYSTEM = """You help a job seeker search broadly instead of literally.
Given what they typed and their resume profile, produce a search plan as JSON:
- titles: 10-20 job titles that fit what they asked for AND that their background supports.
  Include synonyms and adjacent roles (e.g. "Android developer" -> Mobile Engineer, Kotlin Developer,
  Software Engineer - Mobile, Mobile App Developer, Android Engineer). Keep each title short.
- keywords: 10-25 skills/technologies/domains that relevant postings would mention.
- exclude: words that mark clearly irrelevant postings in the title (e.g. "Sales", "Recruiter",
  "Counsel" for an engineer). Never exclude anything they asked for.
- seniority: levels that fit their experience (e.g. "Junior", "Mid", "Senior", "Staff").
- ideal_job: one paragraph describing the ideal posting for them, mentioning role, skills and domain.
Interpret intent generously but stay true to what they asked for."""


# Seniority levels in order, with words that mark each level in a job title.
LEVELS = ["Intern", "Junior", "Mid", "Senior", "Staff", "Principal"]
LEVEL_TITLE_WORDS = {
    "Senior": [r"\bsenior\b", r"\bsr\.?(?=\s|$)"],
    "Staff": [r"\bstaff\b", r"\blead\b", r"\barchitect\b", r"\bmanager\b"],
    "Principal": [r"\bprincipal\b", r"\bdirector\b", r"\bhead of\b", r"\bvp\b",
                  r"\bvice president\b", r"\bdistinguished\b", r"\bchief\b"],
}


def allowed_levels(years: float) -> list[str]:
    """Seniority levels a candidate with this much experience can credibly apply for."""
    if years < 1:
        return ["Intern", "Junior"]
    if years < 6:
        return ["Junior", "Mid"]
    if years < 9:
        return ["Mid", "Senior"]
    if years < 12:
        return ["Senior", "Staff"]
    return ["Senior", "Staff", "Principal"]


def title_level(title: str) -> str | None:
    """The highest seniority level a title signals, or None if it doesn't say."""
    lower = title.lower()
    for level in ("Principal", "Staff", "Senior"):
        if any(re.search(p, lower) for p in LEVEL_TITLE_WORDS[level]):
            return level
    return None


def cap_seniority(plan: dict, years: float) -> dict:
    """Drop seniority levels and titles above what the experience supports."""
    allowed = allowed_levels(years)
    top = max(LEVELS.index(lvl) for lvl in allowed)
    plan["seniority"] = [s for s in plan.get("seniority", [])
                         if s.title() in LEVELS and LEVELS.index(s.title()) <= top] or allowed
    kept, dropped = [], []
    for t in plan.get("titles", []):
        lvl = title_level(t)
        (dropped if lvl and LEVELS.index(lvl) > top else kept).append(t)
    plan["titles"], plan["dropped_titles"] = kept, dropped
    return plan


def expand_intent(llm: LLM, profile: dict, user_intent: str) -> dict:
    summary = (
        f"Headline: {profile.get('headline', '')}\n"
        f"Summary: {profile.get('summary', '')}\n"
        f"Titles held: {', '.join(profile.get('titles', []))}\n"
        f"Skills: {', '.join(profile.get('skills', []))}\n"
        f"Years of experience: {profile.get('years_experience', 0)}"
    )
    years = float(profile.get("years_experience") or 0)
    levels = ", ".join(allowed_levels(years))
    plan = llm.chat(INTENT_SYSTEM,
                    f"They are looking for: {user_intent}\n\nTheir profile:\n{summary}\n\n"
                    f"Only suggest these seniority levels: {levels}. Do not suggest titles above "
                    f"that level (e.g. no Senior, Lead, Architect or Manager titles unless listed).",
                    schema=INTENT_SCHEMA, temperature=0.3)
    plan["query"] = user_intent
    return cap_seniority(plan, years)
