overall guidline
- Don't rebuild the wheel, search and use safe and mature exisiting lib or project when possible.
- Don't over-design, stay simple and robust.
- Fail fast and explicitly.
- take some and auditable steps.

env and toolin:
- work in virtualenv shredder.
- LLM backend DeepSeek v4 flash by default for extraction, with pro available for harder cases.
- Preferred API key env var is DEEPSEEK_API_KEY; DS_API_KEY is kept as a local fallback.

book keeping
- PROGRESS.md is the only grounth-truth of development status, make sure it is up-to-date at each commit
- don't dump everything in commit in PRGRESS.md, summarize, update, and link to commits.
- distinguish in PROGRESS.md, what is done, what is on-going (goal and gap)
- PROGRESS.md is meant for human and Agent, stay concise.
