"""Read a resume file and turn it into a structured profile with the local LLM."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .llm import LLM


def extract_text(path: str | Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    elif suffix == ".docx":
        import docx
        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs]
        for table in d.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        text = "\n".join(parts)
    elif suffix in (".txt", ".md"):
        text = path.read_text(errors="replace")
    else:
        raise ValueError(f"Unsupported resume type: {suffix} (use PDF, DOCX, TXT or MD)")
    text = "\n".join(line.rstrip() for line in text.splitlines())
    if len(text.strip()) < 200:
        raise ValueError("Couldn't read much text from that file. If it's a scanned PDF, "
                         "export a text-based PDF or DOCX instead.")
    return text


PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "email": {"type": "string"},
        "phone": {"type": "string"},
        "location": {"type": "string"},
        "linkedin": {"type": "string"},
        "website": {"type": "string"},
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "years_experience": {"type": "number"},
        "titles": {"type": "array", "items": {"type": "string"}},
        "skills": {"type": "array", "items": {"type": "string"}},
        "industries": {"type": "array", "items": {"type": "string"}},
        "education": {"type": "array", "items": {"type": "string"}},
        "highlights": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "email", "phone", "location", "linkedin", "website", "headline",
                 "summary", "years_experience", "titles", "skills", "industries",
                 "education", "highlights"],
}

PROFILE_SYSTEM = """You extract facts from a resume into JSON.
Rules:
- Use ONLY information present in the resume. Never invent employers, dates, numbers or skills.
- Use an empty string or empty list when something is missing.
- titles: job titles the person has actually held, most recent first.
- skills: concrete technical and professional skills (languages, tools, frameworks, methods).
- highlights: 4-8 specific accomplishments copied or tightly paraphrased from the resume, keeping any numbers exactly.
- summary: 2-3 sentences describing the person's background, written in third person.
- years_experience: your best estimate from the work history dates; 0 if unclear."""


def parse_profile(llm: LLM, resume_text: str) -> dict:
    profile = llm.chat(PROFILE_SYSTEM, f"Resume:\n\n{resume_text}", schema=PROFILE_SCHEMA,
                       temperature=0.1)
    profile["resume_text"] = resume_text
    return profile


def load_or_parse(llm: LLM, path: str | Path, force: bool = False) -> tuple[dict, bool]:
    """Return (profile, was_cached). Re-parses only when the resume's contents change.

    The profile is saved locally at config.PROFILE_PATH with the resume's path and SHA-256.
    """
    from .config import load_profile, save_profile

    path = Path(path).expanduser().resolve()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    cached = load_profile()
    if (not force and cached and cached.get("resume_sha256") == digest
            and cached.get("resume_path") == str(path)):
        return cached, True
    profile = parse_profile(llm, extract_text(path))
    profile["resume_path"] = str(path)
    profile["resume_sha256"] = digest
    save_profile(profile)
    return profile, False
