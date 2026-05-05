# Execution Plans

Use checked-in plans for refactors or features that are too large to keep entirely in the prompt.

## When To Add A Plan

- The change spans multiple domains in [Architecture](../architecture.md).
- The work will likely continue across more than one agent run.
- There are unresolved decisions that future agents must not rediscover from scratch.
- The refactor is intentionally incremental.

## Plan Shape

Plans should be concise and updateable:

- Goal
- Current facts
- Decisions
- Step checklist
- Validation notes

Completed plans can stay here if they explain durable architecture decisions. Delete stale plans once they no longer teach future agents anything.
