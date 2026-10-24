Adopt the role named after this command (e.g. `/work vision` -> `.cursor/rules/roles/vision.mdc`). Valid roles: architect, blackboard, agents, vision, benchmark, simulator, dashboard, mobile, integrator.

1. Read your role file, `.cursor/rules/00-global.mdc`, `PROJECT_SPEC.md`, `TASKS.md`, and `HANDOFFS.md`.
2. Pick the next unblocked TODO task you own (all `depends_on` tasks are DONE). Set it to IN_PROGRESS in `TASKS.md`.
3. Run the loop protocol from `00-global.mdc` (max 6 iterations) until the task's exit command passes. Log each iteration under "Task logs" in `TASKS.md`.
4. On success: set the task to IN_REVIEW, paste the exit command and its last output lines as evidence, append a handoff to `HANDOFFS.md`, commit with `[role][task-id] ...`, and repeat with your next task.
5. On STUCK: set the task to BLOCKED, write what you tried and what you suspect under "BLOCKERS" in `HANDOFFS.md`, and move to your next unblocked task.
6. Stop when you have no unblocked tasks, and post a final handoff summarizing the session.

Only edit files in your OWNED paths.
