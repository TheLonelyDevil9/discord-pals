# Discord Pals Engineering Map

This directory is the repo-local knowledge base for agents and maintainers. Keep `AGENTS.md` and `CLAUDE.md` short; put durable system knowledge here and link to it.

## Start Here

- [Architecture](architecture.md) maps the runtime domains and where code belongs.
- [Quality Guardrails](quality.md) lists mechanical invariants that should stay true as agents refactor.
- [Agent Workflow](agent-workflow.md) describes the expected change loop for Codex-style work.
- [Plans](plans/README.md) explains when to create checked-in execution plans.

## Current Hotspots

The largest modules are intentionally documented because future agents will otherwise pattern-match locally and keep growing them:

- `bot_instance.py` owns Discord event orchestration and response lifecycle.
- `dashboard.py` owns Flask routes and dashboard read/write APIs.
- `memory.py` owns unified memory stores and consolidation behavior.
- `discord_utils.py` owns Discord history, topology, safe JSON helpers, and autonomous channel persistence.

When making a feature, prefer moving reusable boundary parsing or persistence helpers into smaller modules instead of adding another unrelated helper to one of these files.
