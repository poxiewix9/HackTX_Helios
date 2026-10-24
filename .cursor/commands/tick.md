You are the ORCHESTRATOR (read `.cursor/rules/roles/orchestrator.mdc` and `.cursor/rules/00-global.mdc`). Run one orchestration tick now:

1. Read `PROJECT_SPEC.md`, `TASKS.md`, and `HANDOFFS.md`.
2. For each IN_REVIEW task, either run the Reviewer checklist yourself or state that the Reviewer must run `/review`.
3. For each BLOCKED task, diagnose and either split it smaller, reassign it, or file a contract request to the Architect in `HANDOFFS.md`.
4. Check phase gates. If a gate passes, flip the next phase's tasks from gated to TODO.
5. Output: (a) a status table of all tasks, (b) one ready-to-paste kickoff prompt per role agent that has an unblocked TODO task, (c) risks.

Edit only `TASKS.md`, `HANDOFFS.md`, and `docs/`.
