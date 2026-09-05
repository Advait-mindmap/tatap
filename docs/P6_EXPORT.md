# P6 export

A completed run downloads as a Primavera P6 `.xer` file. This document says what that file is,
what it is proven to be, and what it is not.

## What is proven, and by what

**The structure matches a real P6 export.** Every column list in `backend/app/p6/xer.py` is
transcribed from a genuine P6 5.0 export and compared against it, column for column and in
order, by `test_every_column_list_matches_the_reference_export`. `%R` rows are positional, so
one column out of place would shift every value after it and P6 would read a plausible-looking
file full of wrong data.

**The content survives a round trip.** `backend/tests/test_xer_export.py` runs a real
simulation to completion, exports it, and reads the file back with
[`xer-reader`](https://github.com/jjCode01/xer-reader) — a third-party parser this project does
not control — then compares against the `SimulationOutput` it came from:

| Checked | Test |
| --- | --- |
| Activity count | `test_every_activity_survives_the_round_trip` |
| Every start and finish date | `test_every_date_matches_the_schedule_it_came_from` |
| Every duration | `test_every_duration_matches` |
| Every dependency, with its type and lag | `test_every_dependency_survives_with_its_type_and_lag` |
| Milestones stay milestones | `test_milestones_come_back_as_milestones` |
| The engine's WBS, and each activity's place in it | `test_the_wbs_is_the_engines_own_breakdown`, `test_each_activity_lands_under_its_own_wbs_node` |
| Project end = the computed RFS | `test_the_project_end_matches_the_computed_rfs` |
| Activity codes unique and traceable to their ids | `test_every_activity_code_traces_back_to_its_activity` |

The simulation is real on purpose. A hand-built fixture is a fixture designed to pass: the
previous attempt at this exporter had twelve green tests built entirely on a two-activity
dictionary, never met the output of the engine it was meant to serve, and crashed on its first
real call.

The suite has been checked against eight deliberate mutations of the exporter — codes truncated
to ten characters, a TASK column moved, lag dropped, finish dates off by one, an activity
silently dropped, SS links written as FS, milestones written as tasks, every activity flattened
onto the root WBS. All eight are caught. The first version of the tests caught only seven: the
ten-character truncation survived, because the tests looked activity rows up through the
exporter's *own* code generator and so agreed with it however wrong it was. Identity now travels
on activity names, an independent channel.

## What is NOT proven

**That it opens cleanly in any particular P6 installation.** That depends on the version,
database and configuration at the far end, and cannot be settled without importing it there. The
UI says so on the export panel. Nothing short of a real import into the target environment
answers it.

## What the file contains

Only what the completed run contains. Every activity, duration, logic link and WBS node is the
run's. Nothing is padded out to look like a fuller schedule. Costs, resources and actuals are
absent because the simulation has none — an empty TASKRSRC is honest; a fabricated one would not
be.

**Dates are day offsets counted from a start date the user supplies.** The engine's forward pass
(`backend/app/engine/schedule.py`) computes when each activity starts relative to day 0; it has
no opinion about when day 0 is. So the export panel asks. Given that anchor the dates are exact,
not estimated: start = anchor + `start_day`, finish = anchor + `finish_day`. Nothing about the
schedule is invented at export time; only the anchor comes from outside the run.

P6 works in hours and the engine in whole days, bridged at `HOURS_PER_DAY = 8`, which is also
the `day_hr_cnt` written into the exported calendar so the two agree inside P6.

## Tier-1 safety gates the export

`CLAUDE.md` rule 5 / `DOMAIN_KNOWLEDGE.md` §7. A run carrying Tier-1 safety activities is
refused with `409 tier_1_signoff_required`, listing the activities, until `signed_by` names a
person. The name is written into the exported file, so the export records who released it.

**Gap:** the sign-off is not yet written to the `Signoff` table. That table hangs off a
`project_id` a run has no row for, and inventing one would put a fake foreign key into the audit
trail the admin console is meant to read. It is logged server-side; persisting it properly
belongs with the admin console.

## The reference fixture

Not committed. `CLAUDE.md` and `.gitignore` both forbid committing `/samples` and `*.xer`, and
the public fixture used is **GPL-3.0** (`airport.xer` from
[JaiLaff/XER-Splitter](https://github.com/JaiLaff/XER-Splitter)), so vendoring it would attach a
copyleft licence to this repository. Both are the repo owner's call to reverse.

```sh
curl -L -o samples/reference.xer \
  https://raw.githubusercontent.com/JaiLaff/XER-Splitter/master/airport.xer
```

Expected: 71,429 bytes, export version 5.0, 16 tables.

Two tests skip with an explanatory reason when it is absent and run when it is present, so
**CI does not currently check the column layout** — that check runs only where the file has been
fetched. If the layout is ever changed, fetch the reference and run the whole file.

`xer-reader` is likewise GPL-3.0. It lives in `backend/requirements-dev.txt`, which the
Dockerfile never installs, so no GPL code ships in the deployed image; it parses files at test
time only.

### Structure learned from the reference

XER 5.0. Tables in file order: CURRTYPE, OBS, POBS, UMEASURE, PROJECT, CALENDAR, SCHEDOPTIONS,
PROJWBS, RSRC, ACTVTYPE, RSRCRATE, TASK, ACTVCODE, TASKPRED, TASKRSRC, TASKACTV. Terminated by
`%E`.

Values: dates `YYYY-MM-DD HH:MM`; durations and lag in **hours**; `task_type` one of `TT_Task` /
`TT_Mile` / `TT_FinMile`; `status_code` `TK_NotStart`; `duration_type` `DT_FixedDrtn`;
`complete_pct_type` `CP_Drtn`; `pred_type` `PR_FS` / `PR_SS`; encoding cp1252; the PROJWBS root
carries `proj_node_flag Y` and `status_code WS_Open`.
