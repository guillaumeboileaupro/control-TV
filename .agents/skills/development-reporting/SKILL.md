---
name: development-reporting
description: The standard end-of-mission report and handoff for this repository - results, numbers that separate local from CI, risks, measured time and the updated remaining estimate. Use when finishing any implementation, fix, review or audit mission.
---

# Development report

Answer in the owner's language, concise, with these sections (omit the ones that do not apply):

1. Objective and outcome (one paragraph: done, partial or blocked, and why).
2. Root causes (fixes) or architecture (features).
3. Changes: files/modules, interfaces, decisions; what was deliberately not changed.
4. Tests: added, reproduced-before/passing-after evidence, exact commands.
5. Results: **local** and **CI** numbers kept apart (passed/skipped, coverage, run id, SHA).
6. Hardware/platform: what ran on real hardware, what is simulated only, what is not run.
7. Residual risks, open questions, contract gaps for other components.
8. Git: branch, final SHA, PR URL, CI state; "not merged".
9. Cleanup: removed outputs, remaining project-owned footprint, retained artifacts and why.
10. Time: measured time for this mission; updated remaining estimate for the milestone.

Never claim a check that did not run; say "not run" and why. Use measured numbers only.
