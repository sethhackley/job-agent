"""Turn what the user typed ("android dev", "something in games") into a broad search plan."""
from __future__ import annotations

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


def expand_intent(llm: LLM, profile: dict, user_intent: str) -> dict:
    summary = (
        f"Headline: {profile.get('headline', '')}\n"
        f"Summary: {profile.get('summary', '')}\n"
        f"Titles held: {', '.join(profile.get('titles', []))}\n"
        f"Skills: {', '.join(profile.get('skills', []))}\n"
        f"Years of experience: {profile.get('years_experience', 0)}"
    )
    plan = llm.chat(INTENT_SYSTEM,
                    f"They are looking for: {user_intent}\n\nTheir profile:\n{summary}",
                    schema=INTENT_SCHEMA, temperature=0.3)
    plan["query"] = user_intent
    return plan
