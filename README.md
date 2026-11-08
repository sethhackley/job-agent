# job-agent

Local job-application helper. Everything runs on this machine using Ollama.

## Requirements
- Ollama running at `http://127.0.0.1:11434` with `qwen3:14b` and `nomic-embed-text` pulled.
  (Here it runs in a podman container named `ollama` with GPU passthrough.)
- [uv](https://docs.astral.sh/uv/) — `uv sync` installs Python 3.12 and dependencies.

## Modules (`src/job_agent/`)
| Module | What it does |
|---|---|
| `config.py` | Settings and data paths. Data lives in `~/.local/share/job-agent/` (not in this repo). |
| `llm.py` | Ollama client: chat (optional JSON schema, per-call model) and embeddings. |
| `resume.py` | Reads PDF/DOCX/TXT/MD resumes; `parse_profile()` builds a structured profile; `load_or_parse()` caches it and re-parses only when the file changes. |
| `intent.py` | `expand_intent()` turns a search phrase into related titles, keywords, exclusions and an ideal-job description. Seniority levels and titles above the candidate's experience are filtered out in code (`cap_seniority()`). |
| `ranking.py` | `load_jobs()` reads saved job descriptions (one `.txt`/`.md` per job, default folder `~/.local/share/job-agent/jobs-inbox/`, optional `Company:`/`Role:`/`URL:` header lines or a `Company - Role.txt` filename). `rank_jobs()` orders them by embedding match against the resume and expanded search intent, flags titles above your level or matching exclusions, LLM-scores the top N, and records them in the tracker. Nothing is fetched from the web. |
| `tracker.py` | `Tracker` — local SQLite application log (`applications.db`): fit, summary, gaps, letter path, status (`scored`, `letter_written`, `applied`, `interviewing`, `rejected`, `offer`, `skipped`), applied date and dated notes. Detects the same posting by description or company+role; never moves a status backwards when re-scoring. |
| `letters.py` | `score_fit()` rates a pasted job description; `write_letter()` drafts a cover letter, fact-checks it against the resume, revises, deletes sentences that still fail, and enforces banned-phrase/length rules; `save_letter()` writes `.docx` + `.txt`. |

## Example
```python
from job_agent.config import load_settings
from job_agent.llm import LLM
from job_agent.resume import load_or_parse
from job_agent.letters import score_fit, write_letter, save_letter

llm = LLM(load_settings())
profile, _ = load_or_parse(llm, "~/Documents/resume.pdf")
job = open("job.txt").read()
print(score_fit(llm, profile, job))
result = write_letter(llm, profile, job, company="Example Corp", role="QA Engineer")
print(result["letter"], result["removed"], result["unsupported"])
paths = save_letter(result["letter"], "Example Corp", "QA Engineer")
```

### Rank a batch of saved jobs and track them
```python
from job_agent.ranking import load_jobs, rank_jobs
from job_agent.tracker import Tracker

tracker = Tracker()
ranked, plan = rank_jobs(llm, profile, load_jobs(), query="QA tester", top_n=10, tracker=tracker)
for job in ranked:
    print(job.fit["fit"] if job.fit else "-", job.company, job.role, job.flags)

app_id = ranked[0].tracker_id
tracker.attach_letter(app_id, paths["docx"])
tracker.set_status(app_id, "applied", "sent via careers page")
print(tracker.counts())
```
Run with `uv run python your_script.py` from `src/` or with `src` on `PYTHONPATH`.

## Tests
`uv run pytest` — covers the code-only helpers (style checks, sentence removal, letter saving,
seniority cap, profile caching, tracker, job-file parsing and ranking) using a fake model, so Ollama isn't needed.

## Known limitations
- The fact-check misses some claims that attribute a tool or duty to the wrong employer
  (e.g. a tool only listed under Skills). `qwen3:32b` did no better and took ~6.6 min per check.
  Planned fix: split the resume into sections in code and check each claim against one section.
- No UI yet, and no job-listing fetcher.
- Always read a letter before sending it.
