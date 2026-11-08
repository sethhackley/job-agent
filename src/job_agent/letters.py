"""Score how well a pasted job description fits the profile, and write a cover letter for it."""
from __future__ import annotations

import re

import numpy as np

from .llm import LLM

FIT_SCHEMA = {
    "type": "object",
    "properties": {
        "fit": {"type": "integer", "minimum": 1, "maximum": 10},
        "matches": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["fit", "matches", "gaps", "summary"],
}

FIT_SYSTEM = """You compare a candidate's resume profile with a job description.
Return JSON:
- fit: 1-10, how strong a candidate they are (10 = meets nearly every requirement).
- matches: specific requirements from the posting that the candidate clearly meets, citing their evidence.
- gaps: requirements they don't show evidence for.
- summary: two sentences an applicant could use to decide whether to apply.
Be honest; do not assume skills that aren't in the profile."""

LETTER_SYSTEM = """You write cover letters for a job applicant.
Rules:
- Use ONLY facts from the applicant's resume. Never invent employers, numbers, certifications, skills
  or duties. Do not borrow duties from the job description and attribute them to the applicant.
- Connect 2-3 of their most relevant experiences to specific needs in the job description.
- Structure: a greeting line, then 4 paragraphs (opening, two experience paragraphs, closing),
  then "Sincerely," and the applicant's name.
- Length: 280-330 words. Plain professional tone, no clichés like "I am writing to express my interest".
- No placeholders or brackets.
Output only the letter text."""

CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sentence": {"type": "string"},
                    "supported": {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
                "required": ["sentence", "supported", "evidence"],
            },
        },
    },
    "required": ["claims"],
}

CHECK_SYSTEM = """You fact-check a cover letter against the applicant's resume.
List every sentence in the letter that states something about the applicant (their experience,
duties, tools, employers, results, certifications, education). Skip greetings, sign-offs and
sentences only about the company or the role. Copy each sentence exactly as written.
For each: supported = true only if the resume clearly states it (a reasonable paraphrase is fine,
but the specific duty, tool, employer and any numbers must all appear in the resume).
Context rules:
- The resume is divided into sections (each job, each project, education/training, skills).
  Read each letter sentence together with its paragraph: if the paragraph is about a particular
  employer or project, every duty in it must appear under THAT section of the resume.
  Something done in a training program or a different job, placed in another employer's
  paragraph, is NOT supported.
- A tool that only appears in a skills list supports "I have experience with X", but NOT any
  specific duty performed with it (e.g. "I maintained test plans in X") or any employer it was used at.
evidence = the exact resume text that supports it, including which section it is under, or "" if none.
Return JSON."""

# Phrases the letter must never contain (checked in code, not by the model).
BANNED_PHRASES = [
    r"i am writing to express",
    r"i am writing to apply",
    r"i am excited to apply",
    r"to whom it may concern",
    r"explicitly mentioned in the job description",
    r"as mentioned in the job (description|posting)",
    r"\[[^\]]*\]",  # leftover placeholders like [Company]
]
MIN_WORDS, MAX_WORDS = 260, 360

STYLE_SYSTEM = """You edit a cover letter's wording without changing its facts.
Fix exactly the problems listed. Do not add any new claims about the applicant except true facts
taken from the resume when you need more length. Keep the greeting and sign-off.
Output only the revised letter text."""

REVISE_SYSTEM = """You revise a cover letter so every statement about the applicant is backed by their resume.
Rewrite or remove each flagged sentence using only facts from the resume. Keep the greeting,
4-paragraph structure, sign-off and the 280-330 word length; replace removed content with other
true, relevant facts from the resume. Output only the revised letter text."""


def _profile_brief(profile: dict) -> str:
    return (
        f"Name: {profile.get('name', '')}\n"
        f"Headline: {profile.get('headline', '')}\n"
        f"Summary: {profile.get('summary', '')}\n"
        f"Titles: {', '.join(profile.get('titles', []))}\n"
        f"Skills: {', '.join(profile.get('skills', []))}\n"
        f"Education: {'; '.join(profile.get('education', []))}"
    )


def similarity(llm: LLM, profile: dict, job_text: str) -> float:
    """Embedding cosine similarity between the resume and the job, 0-1."""
    resume = profile.get("resume_text") or _profile_brief(profile)
    q = llm.embed([resume[:6000]], kind="query")
    d = llm.embed([job_text[:6000]], kind="document")
    return float(np.clip(q @ d.T, 0, 1)[0, 0])


def score_fit(llm: LLM, profile: dict, job_text: str) -> dict:
    result = llm.chat(FIT_SYSTEM,
                      f"Candidate profile:\n{_profile_brief(profile)}\n\n"
                      f"Full resume:\n{profile.get('resume_text', '')}\n\n"
                      f"Job description:\n{job_text}",
                      schema=FIT_SCHEMA, temperature=0.2)
    result["similarity"] = round(similarity(llm, profile, job_text), 3)
    return result


def style_problems(letter: str) -> list[str]:
    """Problems detectable without the model: banned phrases and length."""
    problems = []
    lower = letter.lower()
    for pattern in BANNED_PHRASES:
        m = re.search(pattern, lower)
        if m:
            problems.append(f'Remove the phrase "{letter[m.start():m.end()]}" and rephrase that sentence.')
    words = len(letter.split())
    if words < MIN_WORDS:
        problems.append(f"The letter is {words} words; expand it to {MIN_WORDS + 20}-{MAX_WORDS - 30} words.")
    elif words > MAX_WORDS:
        problems.append(f"The letter is {words} words; shorten it to under {MAX_WORDS - 30} words.")
    return problems


def _normalise(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("“", '"').replace("”", '"')).strip()


def remove_sentences(letter: str, sentences: list[str]) -> tuple[str, list[str]]:
    """Delete the given sentences from the letter. Returns (new_letter, sentences_not_found)."""
    paragraphs = letter.split("\n")
    not_found = []
    for target in sentences:
        t = _normalise(target).rstrip(".!?")
        hit = False
        for i, para in enumerate(paragraphs):
            parts = re.split(r"(?<=[.!?])\s+", para)
            kept = [p for p in parts if _normalise(p).rstrip(".!?") != t]
            if len(kept) != len(parts):
                paragraphs[i] = " ".join(kept)
                hit = True
                break
        if not hit:
            not_found.append(target)
    text = "\n".join(paragraphs)
    return re.sub(r"\n{3,}", "\n\n", text).strip(), not_found


def fact_check(llm: LLM, profile: dict, letter: str) -> list[dict]:
    """Return the letter's claims about the applicant that the resume doesn't support."""
    result = llm.chat(CHECK_SYSTEM,
                      f"Resume:\n{profile.get('resume_text', '')}\n\nCover letter:\n{letter}",
                      schema=CHECK_SCHEMA, temperature=0.0, model=llm.check_model)
    return [c for c in result.get("claims", []) if not c.get("supported")]


def write_letter(llm: LLM, profile: dict, job_text: str, company: str = "",
                 role: str = "", instructions: str = "", max_revisions: int = 2) -> dict:
    """Draft a letter, then fact-check and revise it.

    Returns {"letter", "removed" (unverifiable sentences deleted), "unsupported" (unverified
    sentences that couldn't be located to delete), "style_problems", "revisions", "words"}.
    Review anything in "unsupported" or "style_problems" before sending.
    """
    greeting = f"Dear Hiring Team at {company}," if company else "Dear Hiring Team,"
    extra = f"\nExtra instructions from the applicant: {instructions}" if instructions else ""
    resume = profile.get("resume_text", "")
    letter = llm.chat(LETTER_SYSTEM,
                      f"Company: {company or 'unknown'}\nRole: {role or 'see description'}\n"
                      f"Start the letter with: {greeting}\n\n"
                      f"Applicant resume:\n{resume}\n\nJob description:\n{job_text}{extra}",
                      temperature=0.6)
    def fix_style(text: str) -> str:
        problems = style_problems(text)
        if not problems:
            return text
        return llm.chat(STYLE_SYSTEM,
                        f"Applicant resume:\n{resume}\n\nLetter:\n{text}\n\nProblems to fix:\n"
                        + "\n".join(f"- {p}" for p in problems),
                        temperature=0.3)

    letter = fix_style(letter)
    unsupported = fact_check(llm, profile, letter)
    revisions = 0
    while unsupported and revisions < max_revisions:
        flagged = "\n".join(f"- {c['sentence']}" for c in unsupported)
        letter = llm.chat(REVISE_SYSTEM,
                          f"Applicant resume:\n{resume}\n\nJob description:\n{job_text}\n\n"
                          f"Current letter:\n{letter}\n\nUnsupported sentences to fix:\n{flagged}",
                          temperature=0.3)
        letter = fix_style(letter)
        revisions += 1
        unsupported = fact_check(llm, profile, letter)

    # Anything still unverified after revising is deleted rather than sent.
    removed, unverified = [], []
    if unsupported:
        letter, not_found = remove_sentences(letter, [c["sentence"] for c in unsupported])
        removed = [c["sentence"] for c in unsupported if c["sentence"] not in not_found]
        unverified = [c for c in unsupported if c["sentence"] in not_found]
    return {"letter": letter, "removed": removed, "unsupported": unverified,
            "style_problems": style_problems(letter), "revisions": revisions,
            "words": len(letter.split())}


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:40] or "untitled"


def save_letter(letter: str, company: str = "", role: str = "",
                out_dir: "str | None" = None) -> dict:
    """Save the letter as .docx and .txt. Returns {"docx": path, "txt": path}.

    Files go to config.LETTERS_DIR by default, named like 2026-11-07_Example-Corp_QA-Engineer.
    An existing file is never overwritten; a counter is added instead.
    """
    from datetime import date
    from pathlib import Path

    import docx

    from .config import LETTERS_DIR

    folder = Path(out_dir) if out_dir else LETTERS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{date.today().isoformat()}_{_slug(company)}_{_slug(role)}"
    n, base = 1, stem
    while (folder / f"{stem}.docx").exists() or (folder / f"{stem}.txt").exists():
        n += 1
        stem = f"{base}_{n}"

    txt_path = folder / f"{stem}.txt"
    txt_path.write_text(letter.strip() + "\n")

    document = docx.Document()
    for block in re.split(r"\n\s*\n", letter.strip()):
        lines = block.split("\n")
        para = document.add_paragraph()
        for i, line in enumerate(lines):
            if i:
                para.add_run().add_break()
            para.add_run(line.strip())
    docx_path = folder / f"{stem}.docx"
    document.save(str(docx_path))
    return {"docx": str(docx_path), "txt": str(txt_path)}
