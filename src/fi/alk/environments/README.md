# Prebuilt environments

Sealed worlds (tools, rules, data, handlers) with a base suite of scenarios, committed as source.
Each builds with `python -m fi.alk.environments.<name>.build --out DIR [--postgres-dsn DSN]` and
rebuilds to the same digests; sealing proves every scenario (reference solution holds, doing
nothing fails), on PostgreSQL when a DSN is given.

The environment is the vertical: Banking is the environment, and "lock a lost card and open a
dispute" is one scenario in it (Wave A plan, `internal-docs`
`rl-environment/environment-builder/wave-a-vertical-environments-plan.md`). The phase-1 workbook's
cases are required examples within a vertical's coverage, renamed to their IDs (`v01_s01_...`).
Each package's `README.md` lists its tools, scenarios, coverage map and known gaps, and
`sample_agent.md` holds the representative agent's instructions.

## Vertical environments

| Package | Entry | Tools | Scenarios | Workbook cases | Known gaps |
|---|---|---|---|---|---|
| `banking` | V01 Banking | 8 + Gmail 7 | 18 | 5/5 | Bank mailbox not yet linked to the banking records (stage 1) |
| `appointment_booking` | V02 Clinic scheduling | 9, `sql` and `filesystem` attached | 10 | 1/5 (S03, S05 partial) | No referral data or slot hold; red-flag and no-medical-advice rules unscored |
| `hr` | V13 HR | 7 | 18 | 5/5 | No C15 failure cases |
| `retail` | V15 Retail | 8 | 12 | 5/5 | No C15 timeout-after-commit case |
| `saas_subscriptions` | V16 SaaS subscriptions | 8 | 10 | 5/5 | No C15 failure cases |
| `internal_it` | V20 Internal IT | 9 | 12 | 5/5 | No C15 failure cases |

Not yet built: V11 Internal knowledge, V12 Service scheduling, V17 Sales intake (plan stage 4).

## MCP capabilities

Reusable tool sets with their own seed data and acceptance cases. A vertical copies a capability's
tools and handlers when a scenario needs it (Banking copies Gmail's). They are verified on their
own and are not listed as customer-facing environments (plan decision 2).

| Package | Entry | Tools | Scenarios | Workbook cases | Interface check against the official server |
|---|---|---|---|---|---|
| `slack_channel_digest` | M01 Slack | 5 (`slack_*`, our own names) | 5 | 5/5 | Not yet (stage 3) |
| `gmail_inbox_triage` | M02 Gmail | 7 | 15 | 5/5 | Names match the workbook; arguments and results not yet |
| `calendar_scheduling_desk` | M03 Google Calendar | 8 | 12 | 5/5 | Names match the workbook; arguments and results not yet |
| `drive_policy_search` | M04 Google Drive | 5 | 7 | 5/5 | Names match the workbook; arguments and results not yet |

Notion (M05) is outside Wave A and was removed.

## Shared

- `designed.py`: the build for environments the environment builder designed (every package except
  the clinic, whose `build.py` and `suite.py` are hand-written).
- Every suite in this folder has the grading weakness recorded in the plan (§7, decision 8): a
  sub-goal's check is the same file in every scenario that uses it, so a wrong target can pass.
  The fix is in the builder; the suites are re-sealed after it.
