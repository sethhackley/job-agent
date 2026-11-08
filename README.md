# job-agent

A job-application helper that runs entirely on your own computer. It reads your resume, ranks
job postings against it, and writes tailored cover letters, then **fact-checks every claim in
each letter against your resume** before you see it. Nothing is sent to a cloud AI service:
the language models run locally on your GPU through [Ollama](https://ollama.com).

## Why
Most AI cover letters are generic, and worse, they quietly invent experience: they borrow
duties from the job description and attribute them to you. Sending a letter like that is
worse than sending none. This project treats that as a QA problem: generate, then test the
output against a source of truth, and remove anything that fails.

## What it does
- **Resume parsing:** reads PDF, DOCX, TXT or Markdown into a structured profile (titles,
  skills, education, highlights), cached until the file changes.
- **Search expansion:** turns a loose phrase like "QA tester" into related titles and keywords,
  capped in code to seniority levels your experience supports.
- **Job ranking:** scores a folder of saved job descriptions by embedding similarity to your
  resume and search, flags titles above your level or outside your search, then gives the top
  matches a full fit score (1–10) with matched requirements and gaps.
- **Fact-checked cover letters:** drafts a letter, checks each sentence about you against your
  resume, revises, **deletes sentences that still fail**, and enforces banned phrases and length
  in plain code. Saves `.docx` and `.txt`.
- **Confirmed facts:** true experience that isn't on your resume can be added (tied to a specific
  job) so it isn't stripped from letters.
- **Application tracker:** a local SQLite log of every job scored or applied to (fit, letter,
  status, dates, notes), with duplicate detection.

## Results from testing on a real resume
Hardware: RTX 5080 (16 GB), `qwen3:14b` for writing and checking, `nomic-embed-text` for matching.

| Step | Time |
|---|---|
| Parse resume | ~20–25 s (then cached) |
| Fit score for one job | ~10–15 s |
| Cover letter with fact-check and revisions | ~30–50 s |
| Rank 3 jobs with full fit scores | ~17 s |

**Fact-check example.** On a sample mobile QA posting, the first draft claimed the applicant
"collaborated with developers to reproduce issues" and "maintained test plans": both taken
from the job description, neither on the resume. The checker flagged them and the revision
removed them. On a real Windows endpoint QA posting, it removed a claim of "experience in
Windows environments, endpoint management" the resume didn't support.

**Ranking example.** A sample mobile QA posting scored 9/10, a real Windows endpoint QA posting
6/10 (correctly identifying missing Intune and endpoint-management experience), and a senior
sales role 2/10 with "above your level" and "title matches exclusion 'sales'" flags.

**What didn't work.** The checker still misses some claims that blend two true facts into a
false one, such as tying a tool listed only under Skills to a specific employer. A larger model
(`qwen3:32b`) caught no more of these and took ~6.6 minutes per check, so the fix is structural
(see Known limitations), not a bigger model.

## How it was built
Built with [Claude Code](https://claude.com/claude-code) as an AI pair programmer. I set the
requirements (local-only, honest letters, review before sending), tested it against my own
resume and real job postings, and checked its output claim by claim. Those checks drove most
of the design: the fact-check loop, sentence deletion, code-enforced style rules, the seniority
cap and confirmed facts all came from failures found in testing.

## Setup
1. **Ollama with GPU access.** For example, with podman and the NVIDIA container toolkit:
   ```bash
   podman run -d --name ollama --restart=always --device nvidia.com/gpu=all \
     --security-opt=label=disable -v ollama-models:/root/.ollama \
     -p 127.0.0.1:11434:11434 docker.io/ollama/ollama:latest
   podman exec ollama ollama pull qwen3:14b
   podman exec ollama ollama pull nomic-embed-text
   ```
2. **Python environment** with [uv](https://docs.astral.sh/uv/): `uv sync` installs Python 3.12
   and the dependencies.

Your data (profile, confirmed facts, letters, tracker database) lives in
`~/.local/share/job-agent/`, outside this repository, readable only by your account.

## Usage
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
Save one job description per `.txt` file in `~/.local/share/job-agent/jobs-inbox/`, named
`Company - Role.txt` or starting with `Company:` / `Role:` / `URL:` header lines.
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
```
Run scripts with `uv run python your_script.py` from the project folder.

## Modules (`src/job_agent/`)
| Module | Purpose |
|---|---|
| `config.py` | Settings, data paths, confirmed facts (`add_fact()`, `load_facts()`). |
| `llm.py` | Ollama client: chat with optional JSON schema and per-call model, plus embeddings. |
| `resume.py` | Resume text extraction, `parse_profile()`, cached `load_or_parse()`. |
| `intent.py` | `expand_intent()` search expansion and `cap_seniority()`. |
| `ranking.py` | `load_jobs()` and `rank_jobs()` for saved job descriptions. Nothing is fetched from the web. |
| `letters.py` | `score_fit()`, `write_letter()` with fact-check, revision and deletion, `save_letter()`. |
| `tracker.py` | `Tracker`: SQLite application log with statuses and duplicate detection. |

## Tests
`uv run pytest` runs 47 tests covering style checks, sentence deletion, the full
draft-check-revise-delete loop, letter saving, the seniority cap, profile caching, confirmed
facts, the tracker, job-file parsing and ranking. They use a fake model, so Ollama isn't needed.

## Known limitations
- The fact-check misses some claims that blend true facts into a false one. Planned fix: split
  the resume into sections in code and check each claim against only its matching section.
- No desktop window yet, and no automatic job-listing fetcher (jobs are saved as text files).
- Always read a letter before sending it.
