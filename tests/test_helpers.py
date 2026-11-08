"""Tests for the parts that don't need a running model."""
import json

import docx
import pytest

from job_agent import config, letters
from job_agent.intent import allowed_levels, cap_seniority, title_level
from job_agent.letters import remove_sentences, save_letter, style_problems
from job_agent.resume import load_or_parse

GOOD_LETTER = "Dear Hiring Team,\n\n" + ("I tested software at a lab. " * 50) + "\n\nSincerely,\nSam"


class FakeLLM:
    """Stands in for LLM: returns canned replies and counts calls."""

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls = 0
        self.check_model = "fake"

    def chat(self, system, user, schema=None, temperature=0.4, model=None):
        self.calls += 1
        return self.replies.pop(0) if self.replies else {}


# --- style_problems -------------------------------------------------------

def test_clean_letter_has_no_style_problems():
    assert style_problems(GOOD_LETTER) == []


@pytest.mark.parametrize("phrase", [
    "I am writing to express my interest",
    "To whom it may concern",
    "Dear [Hiring Manager]",
    "as explicitly mentioned in the job description",
])
def test_banned_phrases_are_flagged(phrase):
    problems = style_problems(GOOD_LETTER + "\n" + phrase)
    assert any("Remove the phrase" in p for p in problems)


def test_banned_phrase_check_ignores_case():
    assert style_problems(GOOD_LETTER + " I AM WRITING TO EXPRESS joy")


def test_short_and_long_letters_are_flagged():
    assert any("expand" in p for p in style_problems("Too short."))
    assert any("shorten" in p for p in style_problems("word " * 500))


# --- remove_sentences -----------------------------------------------------

def test_removes_only_the_target_sentence():
    letter = "Dear Team,\n\nI test apps. I used TestRail for plans. I write docs.\n\nSincerely,\nSam"
    out, missing = remove_sentences(letter, ["I used TestRail for plans."])
    assert out == "Dear Team,\n\nI test apps. I write docs.\n\nSincerely,\nSam"
    assert missing == []


def test_matches_despite_curly_quotes_spacing_and_trailing_period():
    letter = "I’m skilled.  I used  JIRA daily. Done."
    out, missing = remove_sentences(letter, ["I'm skilled"])
    assert out == "I used  JIRA daily. Done."
    assert missing == []


def test_reports_sentences_it_cannot_find():
    letter = "I test apps."
    out, missing = remove_sentences(letter, ["Not in the letter."])
    assert out == letter
    assert missing == ["Not in the letter."]


# --- write_letter (deletion fallback, with a fake model) -----------------

def test_write_letter_deletes_claims_that_never_pass(monkeypatch):
    bad = "I maintained test plans in TestRail."
    draft = GOOD_LETTER.replace("Sincerely", bad + "\n\nSincerely")
    # draft, then two revisions that keep the bad sentence
    llm = FakeLLM([draft, draft, draft])
    monkeypatch.setattr(letters, "fact_check",
                        lambda llm, profile, text: [{"sentence": bad}] if bad in text else [])
    result = letters.write_letter(llm, {"resume_text": "x"}, "job", company="Acme")
    assert bad not in result["letter"]
    assert result["removed"] == [bad]
    assert result["unsupported"] == []
    assert result["revisions"] == 2


# --- save_letter ----------------------------------------------------------

def test_save_letter_writes_docx_and_txt(tmp_path):
    letter = "Dear Team,\n\nFirst.\n\nSecond.\n\nSincerely,\nSam"
    paths = save_letter(letter, "Acme Corp", "QA Engineer, Mobile", out_dir=str(tmp_path))
    assert paths["txt"].endswith("_Acme-Corp_QA-Engineer-Mobile.txt")
    assert open(paths["txt"]).read() == letter + "\n"
    paras = [p.text for p in docx.Document(paths["docx"]).paragraphs]
    assert paras == ["Dear Team,", "First.", "Second.", "Sincerely,\nSam"]


def test_save_letter_never_overwrites(tmp_path):
    a = save_letter("Hi", "Acme", "QA", out_dir=str(tmp_path))
    b = save_letter("Hi again", "Acme", "QA", out_dir=str(tmp_path))
    assert a["txt"] != b["txt"] and b["txt"].endswith("_2.txt")
    assert open(a["txt"]).read() == "Hi\n"


def test_save_letter_handles_empty_company_and_role(tmp_path):
    paths = save_letter("Hi", out_dir=str(tmp_path))
    assert paths["txt"].endswith("_untitled_untitled.txt")


# --- seniority cap --------------------------------------------------------

@pytest.mark.parametrize("years,expected", [
    (0, ["Intern", "Junior"]), (4, ["Junior", "Mid"]), (7, ["Mid", "Senior"]),
    (10, ["Senior", "Staff"]), (15, ["Senior", "Staff", "Principal"]),
])
def test_allowed_levels(years, expected):
    assert allowed_levels(years) == expected


@pytest.mark.parametrize("title,level", [
    ("Senior QA Engineer", "Senior"), ("Sr. Android Developer", "Senior"),
    ("QA Lead", "Staff"), ("Test Architect", "Staff"), ("Director of QA", "Principal"),
    ("QA Tester", None), ("Mobile App Tester", None), ("Sprinter", None),
])
def test_title_level(title, level):
    assert title_level(title) == level


def test_cap_seniority_drops_titles_above_experience():
    plan = {"titles": ["QA Tester", "Senior QA Engineer", "Test Architect", "Test Analyst"],
            "seniority": ["Junior", "Mid", "Senior"]}
    out = cap_seniority(plan, 4)
    assert out["titles"] == ["QA Tester", "Test Analyst"]
    assert out["dropped_titles"] == ["Senior QA Engineer", "Test Architect"]
    assert out["seniority"] == ["Junior", "Mid"]


def test_cap_seniority_keeps_senior_titles_when_experienced():
    out = cap_seniority({"titles": ["Senior QA Engineer"], "seniority": ["Senior"]}, 8)
    assert out["titles"] == ["Senior QA Engineer"] and out["dropped_titles"] == []


# --- profile caching ------------------------------------------------------

@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PROFILE_PATH", tmp_path / "profile.json")
    return tmp_path


def test_profile_is_cached_until_resume_changes(isolated_data, tmp_path):
    resume = tmp_path / "resume.txt"
    resume.write_text("Experienced tester. " * 20)
    llm = FakeLLM([{"name": "Sam"}, {"name": "Sam v2"}])

    p1, cached1 = load_or_parse(llm, resume)
    p2, cached2 = load_or_parse(llm, resume)
    assert (cached1, cached2) == (False, True)
    assert llm.calls == 1 and p2["name"] == "Sam"

    resume.write_text("Experienced tester, now with Python. " * 20)
    p3, cached3 = load_or_parse(llm, resume)
    assert cached3 is False and p3["name"] == "Sam v2" and llm.calls == 2


def test_saved_profile_is_private(isolated_data, tmp_path):
    resume = tmp_path / "resume.txt"
    resume.write_text("Experienced tester. " * 20)
    load_or_parse(FakeLLM([{"name": "Sam"}]), resume)
    saved = isolated_data / "profile.json"
    assert oct(saved.stat().st_mode)[-3:] == "600"
    assert json.loads(saved.read_text())["resume_path"] == str(resume.resolve())
