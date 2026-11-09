# AI Agent Instructions — tts

Independent project under `~/dev/`. See `~/dev/AGENTS.md` for shared rules.

## On Start
1. Read this file and any project docs (README, `.ai-sync/HANDOFF.md` if present).
2. Match existing code patterns in this project.

## Rules
1. This project is NOT synced to the phone (huge file count/storage) — keep it that way; don't add large binaries unless asked.
2. Run verification (build + typecheck) before stopping.
3. Document decisions; the next agent has zero context.

## .ai-sync
If `.ai-sync/` is missing in this project, create it (HANDOFF.md, PROGRESS.md, PLAN.md, sessions/) before starting work, using the scaffold from `~/dev/AGENTS.md`. Keep it updated on stop.


## Post-Implementation Suggestions
After each implementation, give the user up to 9 NEW potential improvements or next-step suggestions for this project, each with a priority percentage (how critical it is) and a plain-English what/why/how so a non-expert can judge them. Must not repeat already-done/suggested items; mention each for reference. See `~/dev/AGENTS.md` § Post-Implementation Suggestions.
