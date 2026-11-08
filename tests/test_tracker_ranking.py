"""Tests for the application tracker and batch job ranking (no model needed)."""
import numpy as np
import pytest

from job_agent import ranking
from job_agent.ranking import load_jobs, parse_job_file, rank_jobs
from job_agent.tracker import Tracker, job_hash

DESC = "We need a QA engineer to write and run test cases for our mobile apps. " * 5


@pytest.fixture
def tracker(tmp_path):
    t = Tracker(tmp_path / "apps.db")
    yield t
    t.close()


# --- tracker --------------------------------------------------------------

def test_job_hash_ignores_case_and_whitespace():
    assert job_hash("QA  Engineer\nRole") == job_hash("qa engineer role")


def test_record_and_find_by_text_or_company_role(tracker):
    app_id = tracker.record_score("Acme Corp", "QA Engineer", DESC, {"fit": 8, "similarity": 0.7,
                                                                    "summary": "good", "gaps": ["Python"]})
    app = tracker.get(app_id)
    assert (app["status"], app["fit"], app["gaps"]) == ("scored", 8, "Python")
    assert tracker.find(job_text=DESC.upper())["id"] == app_id
    assert tracker.find(company="acme corp.", role="QA engineer")["id"] == app_id
    assert tracker.find(company="Other", role="QA Engineer") is None


def test_rescoring_updates_without_duplicating_or_downgrading(tracker):
    app_id = tracker.record_score("Acme", "QA", DESC, {"fit": 5})
    tracker.set_status(app_id, "applied")
    again = tracker.record_score("Acme", "QA", DESC, {"fit": 9})
    assert again == app_id and len(tracker.list()) == 1
    assert tracker.get(app_id)["fit"] == 9 and tracker.get(app_id)["status"] == "applied"


def test_status_flow_letter_applied_and_notes(tracker):
    app_id = tracker.record_score("Acme", "QA", DESC, {"fit": 7})
    tracker.attach_letter(app_id, "/letters/acme.docx")
    assert tracker.get(app_id)["status"] == "letter_written"
    tracker.set_status(app_id, "applied", "sent via careers page")
    app = tracker.get(app_id)
    assert app["status"] == "applied" and app["applied_on"] and "careers page" in app["notes"]
    tracker.set_status(app_id, "interviewing", "phone screen Tuesday")
    app = tracker.get(app_id)
    assert app["applied_on"]  # kept
    assert app["notes"].count("\n") == 1
    assert tracker.counts() == {"interviewing": 1}


def test_attach_letter_after_applying_keeps_status(tracker):
    app_id = tracker.record_score("Acme", "QA", DESC, {"fit": 7})
    tracker.set_status(app_id, "applied")
    tracker.attach_letter(app_id, "/letters/acme.docx")
    assert tracker.get(app_id)["status"] == "applied"


def test_invalid_status_and_missing_id(tracker):
    app_id = tracker.record_score("Acme", "QA", DESC, {"fit": 7})
    with pytest.raises(ValueError):
        tracker.set_status(app_id, "ghosted")
    with pytest.raises(KeyError):
        tracker.set_status(999, "applied")


def test_tracker_db_is_private(tmp_path):
    t = Tracker(tmp_path / "new.db")
    t.close()
    assert oct((tmp_path / "new.db").stat().st_mode)[-3:] == "600"


# --- job files ------------------------------------------------------------

def test_parse_job_file_with_headers(tmp_path):
    p = tmp_path / "whatever.txt"
    p.write_text(f"Company: Acme Corp\nTitle: QA Engineer\nLink: https://x.test/1\n\n{DESC}")
    job = parse_job_file(p)
    assert (job.company, job.role, job.url) == ("Acme Corp", "QA Engineer", "https://x.test/1")
    assert job.text == DESC.strip()


def test_parse_job_file_from_filename(tmp_path):
    p = tmp_path / "Acme Corp - Mobile QA Tester.txt"
    p.write_text(DESC)
    job = parse_job_file(p)
    assert (job.company, job.role, job.url) == ("Acme Corp", "Mobile QA Tester", "")


def test_load_jobs_skips_short_and_non_text_files(tmp_path):
    (tmp_path / "A - QA.txt").write_text(DESC)
    (tmp_path / "B - QA.md").write_text(DESC)
    (tmp_path / "empty.txt").write_text("hi")
    (tmp_path / "notes.pdf").write_bytes(b"%PDF")
    assert sorted(j.company for j in load_jobs(tmp_path)) == ["A", "B"]


# --- ranking --------------------------------------------------------------

class FakeEmbedLLM:
    """Embeds text by keyword counts so similarity is predictable."""
    WORDS = ["qa", "test", "mobile", "sales", "quota", "android"]

    def embed(self, texts, kind="document"):
        vecs = np.array([[t.lower().count(w) for w in self.WORDS] for t in texts], dtype=float) + 1e-3
        return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def _job(company, role, text):
    return ranking.JobPosting(path="", company=company, role=role, url="", text=text)


def test_rank_jobs_orders_flags_and_records(monkeypatch, tracker):
    fits = {"QA": 9, "Senior QA": 6, "Sales": 2}
    monkeypatch.setattr(ranking, "score_fit",
                        lambda llm, profile, text: {"fit": fits[text.split("|")[0]], "summary": "",
                                                    "gaps": [], "similarity": 0.5})
    monkeypatch.setattr(ranking, "expand_intent",
                        lambda llm, profile, q: {"ideal_job": "qa test mobile android", "exclude": ["Sales"]})
    jobs = [
        _job("C", "Account Executive Sales", "Sales| sales quota sales quota"),
        _job("A", "QA Tester", "QA| qa test mobile android qa test"),
        _job("B", "Senior QA Engineer", "Senior QA| qa test mobile"),
    ]
    profile = {"resume_text": "qa test mobile android", "years_experience": 4}
    ranked, plan = rank_jobs(FakeEmbedLLM(), profile, jobs, query="qa", top_n=2, tracker=tracker)

    assert [j.role for j in ranked] == ["QA Tester", "Senior QA Engineer", "Account Executive Sales"]
    assert ranked[2].fit is None  # outside top_n by match, so not LLM-scored
    assert ranked[1].flags == ["above your level (Senior)"]
    assert ranked[2].flags == ["title matches exclusion 'sales'"]
    assert len(tracker.list()) == 2 and all(j.tracker_id for j in ranked[:2])


def test_rank_jobs_flags_already_applied(monkeypatch, tracker):
    monkeypatch.setattr(ranking, "score_fit",
                        lambda llm, profile, text: {"fit": 7, "summary": "", "gaps": [], "similarity": 0.5})
    text = "QA| qa test mobile"
    app_id = tracker.record_score("A", "QA Tester", text, {"fit": 7})
    tracker.set_status(app_id, "applied")
    ranked, _ = rank_jobs(FakeEmbedLLM(), {"resume_text": "qa"}, [_job("A", "QA Tester", text)],
                          tracker=tracker)
    assert "already applied" in ranked[0].flags
    assert ranked[0].tracker_status == "applied"


def test_rank_jobs_empty():
    assert rank_jobs(FakeEmbedLLM(), {}, []) == ([], None)
