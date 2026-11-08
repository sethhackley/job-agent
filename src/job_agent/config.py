"""Settings and data locations. Everything stays on this machine."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

DATA_DIR = Path.home() / ".local" / "share" / "job-agent"
SETTINGS_PATH = DATA_DIR / "settings.json"
PROFILE_PATH = DATA_DIR / "profile.json"
TRACKER_PATH = DATA_DIR / "applications.db"
# True facts about you that aren't on the resume, one per line, each naming its job/section,
# e.g. "Acme Corp (QA Analyst): Reproduced reported bugs ...".
# Letters, fit scoring and the fact-check treat these like resume text.
FACTS_PATH = DATA_DIR / "confirmed_facts.txt"
JOBS_INBOX = DATA_DIR / "jobs-inbox"  # drop pasted job descriptions here as .txt files
LETTERS_DIR = DATA_DIR / "letters"

# Company job boards with public read APIs (Greenhouse, Lever, Ashby), checked to respond in
# Nov 2026. Not used yet: reserved for a planned job-listing fetcher.
DEFAULT_COMPANIES = {
    "greenhouse": [
        "airbnb", "asana", "cloudflare", "coinbase", "databricks", "discord",
        "dropbox", "duolingo", "epicgames", "figma", "gitlab", "instacart",
        "lyft", "pinterest", "reddit", "robinhood", "roblox", "squarespace",
        "stripe", "twitch",
    ],
    "lever": ["palantir", "spotify", "zoox"],
    "ashby": [
        "cursor", "elevenlabs", "linear", "notion", "openai", "perplexity",
        "posthog", "ramp", "replit", "snowflake", "supabase",
    ],
}

DEFAULTS = {
    "ollama_url": "http://127.0.0.1:11434",
    "chat_model": "qwen3:14b",
    # Model used only for fact-checking letters. qwen3:32b was tested and caught no more than
    # qwen3:14b while taking ~6.6 min per check (it doesn't fit in 16 GB VRAM).
    "check_model": "qwen3:14b",
    "embed_model": "nomic-embed-text",
    "num_ctx": 16384,
    "companies": DEFAULT_COMPANIES,
}


def load_settings() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings = deepcopy(DEFAULTS)
    if SETTINGS_PATH.exists():
        try:
            settings.update(json.loads(SETTINGS_PATH.read_text()))
        except json.JSONDecodeError:
            pass
    return settings


def save_settings(settings: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2))


def load_facts() -> list[str]:
    if not FACTS_PATH.exists():
        return []
    return [line.strip() for line in FACTS_PATH.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def add_fact(fact: str) -> None:
    """Append a confirmed fact (skips exact duplicates)."""
    fact = " ".join(fact.split())
    if not fact or fact in load_facts():
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with FACTS_PATH.open("a") as f:
        f.write(fact + "\n")
    FACTS_PATH.chmod(0o600)


def load_profile() -> dict | None:
    if PROFILE_PATH.exists():
        try:
            return json.loads(PROFILE_PATH.read_text())
        except json.JSONDecodeError:
            return None
    return None


def save_profile(profile: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(profile, indent=2))
    PROFILE_PATH.chmod(0o600)  # contains contact details and full resume text
