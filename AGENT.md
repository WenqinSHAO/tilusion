## overall guidline
- Don't rebuild the wheel, search and use safe and mature exisiting lib or project when possible.
- Don't over-design, stay simple and robust.
- Fail fast and explicitly.
- Take small, auditable steps.
- adress me as 

## env and tooling:
- work in virtualenv shredder at ~/.virtualenvs/shredder
- LLM backend DeepSeek v4 flash by default for extraction, with pro available for harder cases.
- Preferred API key env var is DEEPSEEK_API_KEY; DS_API_KEY is kept as a local fallback.

## book keeping
- Maintain careful commits: each commit should have a focused subject and a body explaining intent, design choices, and verification when relevant.
- Use commit history as the detailed development log. Prefer careful commit bodies over dumping detailed reasoning into PROGRESS.md.
- PROGRESS.md is the compact shared memory for human and agent. Keep it current before finalizing each meaningful commit.
- Do not let PROGRESS.md lag behind major direction changes. If a commit changes project status, next steps, or known gaps, update PROGRESS.md in the same commit unless there is a clear reason not to.
- Keep PROGRESS.md milestone-oriented, not a running changelog. Point to key commits and dedicated docs for detail.
- In PROGRESS.md, clearly distinguish done work from ongoing work, current goal, and known gaps.
- Keep PROGRESS.md concise enough to scan at session start.
