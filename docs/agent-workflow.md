# Agent Workflow

This repo is optimized for agent-assisted maintenance. Humans steer intent; agents should make the repository more legible every time they change it.

## Change Loop

1. Read `AGENTS.md`, then follow the relevant links in `docs/`.
2. Inspect existing tests for the behavior being changed.
3. Make the smallest durable code change that satisfies the request.
4. Add or update focused tests for changed behavior.
5. Run `python tools/quality_check.py` and the relevant tests, then the full suite when feasible.
6. Follow the version bump and release instructions in `AGENTS.md`.

## Context Hygiene

- Keep entrypoint instructions short and link to durable docs.
- Promote repeated review feedback into docs or checks.
- Create checked-in plans for multi-step refactors that span turns or require deferred decisions.
- Prefer repo-local examples over external chat context.

## Dashboard-First Work

Runtime behavior that users can tune should be discoverable in the dashboard. When adding config:

- Add the default and schema entry in `runtime_config.py`.
- Add or update the Config dashboard control.
- Validate dashboard POST input at the API boundary.
- Add tests for both valid and invalid payloads.
