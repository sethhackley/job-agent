# Plan: live web demo

Goal: let approved visitors to the portfolio site upload a resume, paste a job description, and
watch job-agent score the fit and write a fact-checked cover letter, running on the home PC
(RTX 5080, Ollama) rather than in the cloud. A recorded demo page on the portfolio stays as the
always-available fallback.

## Constraints
- The PC is on throughout the day (it also runs Jellyfin), but it can restart or be busy.
  The demo page must handle "offline" gracefully and link to the recorded demo.
- One GPU, shared with Jellyfin transcoding. `qwen3:14b` uses ~10 GB of 16 GB VRAM while
  loaded and unloads after 5 minutes idle. Run at most one model job at a time.
- A fit score takes ~10–15 s and a letter ~30–50 s, so the page needs progress feedback.

## Architecture
```
visitor's browser ── portfolio demo page (Cloudflare Workers static site)
        │  HTTPS
        ▼
Cloudflare Access (login: approved emails only)
        ▼
Cloudflare Tunnel (cloudflared, outbound-only from the PC; no router ports opened)
        ▼
local API server on 127.0.0.1:<port>  ──►  job_agent (resume, letters)  ──►  Ollama 127.0.0.1:11434
```

## Requirements

### Local API server
- Small Python server in this repo, wrapping existing functions only:
  `resume.extract_text()` + `resume.parse_profile()`, `letters.score_fit()`, `letters.write_letter()`.
- Bind to `127.0.0.1` only. Never `0.0.0.0`.
- Must **not** use the owner's saved profile, confirmed facts, letters or tracker in
  `~/.local/share/job-agent/`. Visitor runs use only what the visitor uploads. Pass
  `confirmed_facts=[]` in the profile so `letters.evidence()` doesn't load the owner's facts file.
- Return the intermediate steps the demo shows: fit result, first draft, each fact-check pass's
  flagged sentences, removed sentences, final letter.

### Access and abuse limits
- Cloudflare Access application on the tunnel hostname with an allow-list policy
  (specific emails, one-time-PIN login). The owner adds an interviewer's email when needed.
- The server should still verify the `Cf-Access-Jwt-Assertion` header (Access JWT) so it can't
  be reached by bypassing Access.
- Limits: resume upload ≤ 2 MB, PDF/DOCX/TXT only; job description ≤ 20,000 characters;
  one job running at a time with a short queue (reject when full); per-user rate limit
  (for example 5 runs per hour); hard timeout per request (for example 3 minutes).
- CORS: allow only the portfolio's origin.

### Privacy
- Process uploads in memory or a temp directory deleted after each request. Never write
  visitor resumes, job text or letters to disk permanently, and don't log their contents.
- The demo page states this plainly before upload.

### Safety of model input
- Uploaded resumes and job descriptions are untrusted text. The model can't take actions
  (it only returns text), but outputs must be rendered as text, never as HTML, on the page.

### Demo page (portfolio repo)
- `job-agent-live.html`: upload, paste job, run, show progress, then show results in the
  same layout as the recorded demo (fit, draft with flagged sentences, revision, final letter).
- On network error or Access denial, explain and link to the recorded demo.

### Operations
- `cloudflared` and the API server as systemd user services that start on boot.
- A health endpoint the page checks before enabling the Run button.
- Document start/stop and how to add/remove approved emails.

## Testing
- Unit tests for limits (size, type, queue full, timeout) and for "never touches owner data".
- Manual: Access login flow, upload each file type, offline behaviour, Jellyfin streaming
  during a run.
