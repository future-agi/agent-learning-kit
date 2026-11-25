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
| `banking` | V01 Banking | 17 + Gmail 8 + Drive 5 | 100 | 5/5 | Wave A increment 2 done: linked mailbox (21 threads) and policy library (15 files), 100 scenarios across 8 jobs, positive and negative runs in `tests/harness/test_banking_validation.py`; live sample-agent and E2B runs are increment 11 |
| `appointment_booking` | V02 Clinic scheduling | 9, `sql` and `filesystem` attached | 10 | 1/5 (S03, S05 partial) | No referral data or slot hold; red-flag and no-medical-advice rules unscored |
| `hr` | V13 HR | 16 + Slack 7 + Drive 5 | 100 | 5/5 | Wave A increment 4: linked Slack workspace and HR policy library, 100 scenarios across 8 jobs, `tests/harness/test_hr_validation.py`; 37 scenarios use Slack or Drive against the plan's estimate of 48 |
| `retail` | V15 Retail | 17 + Gmail 8 + Drive 5 | 100 | 5/5 | Wave A increment 3: linked support mailbox and policy library, 100 scenarios across 8 jobs, `tests/harness/test_retail_validation.py` |
| `saas_subscriptions` | V16 SaaS subscriptions | 18 + Gmail 8 + Slack 7 | 100 | 5/5 | Wave A increment 6: Gmail and Slack in one world, 100 scenarios across 8 jobs, `tests/harness/test_saas_subscriptions_validation.py` |
| `internal_it` | V20 Internal IT | 17 + Slack 7 + Drive 5 | 100 | 5/5 | Wave A increment 5: linked #it-access workspace and access-policy library, 100 scenarios across 8 jobs, `tests/harness/test_internal_it_validation.py`; 37 scenarios use Slack or Drive against the plan's estimate of 42 |
| `clinic_scheduling` | V02 Clinic scheduling | 18 + Calendar 8 + Gmail 8 | 100 | 5/5 | Wave A increment 7: providers' calendars as the availability source, bookings write the record and the event together, 100 scenarios across 8 jobs, `tests/harness/test_clinic_scheduling_validation.py`; `appointment_booking` stays as the clinic reference |
| `service_scheduling` | V12 Service scheduling | 18 + Calendar 8 + Gmail 8 | 100 | 5/5 | Wave A increment 8: two time zones and a clock change, holds, waitlist and confirmations after commit, 100 scenarios across 8 jobs, `tests/harness/test_service_scheduling_validation.py` |
| `sales_intake` | V17 Sales intake | 19 + Gmail 8 + Calendar 8 + Slack 7 | 100 | 5/5 | Wave A increment 9: three capabilities in one world, rubric, territories with a fallback queue, demos on the owner's calendar, 100 scenarios across 8 jobs, `tests/harness/test_sales_intake_validation.py` |
| `internal_knowledge` | V11 Internal knowledge | 15 + Drive 5 + Slack 7 | 100 | 5/5 | Wave A increment 10: the corpus is the business system, versions, precedence and Slack decisions, 100 scenarios across 8 jobs, `tests/harness/test_internal_knowledge_validation.py` |

### Workbook cases

A vertical's workbook case `Vxx-S0y` is its scenario named `vxx_s0y_...`:

| Package | S01 | S02 | S03 | S04 | S05 |
|---|---|---|---|---|---|
| `banking` (V01) | `happy_path_lock_and_dispute` | `auth_challenge_failure_handoff` | `pending_charge_dispute_declined` | `idempotent_dispute_retry_timeout` | `partial_failure_lock_ok_dispute_failed` |
| `clinic_scheduling` (V02) | `reschedule_within_constraints` | `referral_required` | `slot_taken_during_confirmation` | `urgent_symptom_during_scheduling` | `other_patient_records_withheld` |
| `internal_knowledge` (V11) | `current_hotel_cap_with_citation` | `restricted_salary_document` | `relocation_allowance_japan_not_covered` | `conflicting_client_dinner_caps` | `injected_instruction_in_faq` |
| `service_scheduling` (V12) | `book_spanish_speaker_thursday` | `dst_repeated_hour_clarified` | `last_slot_taken_during_confirmation` | `failed_reschedule_keeps_original` | `late_cancellation_fee_disclosed` |
| `hr` (V13) | `three_working_days_requested` | `calendar_holiday_inside_date_range` | `insufficient_leave_balance` | `outdated_carry_forward_policy` | `authorization_coworker_leave_details` |
| `retail` (V15) | `happy_path_single_item_return` | `final_sale_policy_boundary` | `quantity_already_returned_duplicate` | `discounted_partial_refund_arithmetic` | `service_failure_label_escalation` |
| `saas_subscriptions` (V16) | `schedule_end_of_cycle_downgrade` | `unauthorized_member_mutation_denied` | `downgrade_blocked_seat_limit_exceeded` | `explain_invoice_seat_increase` | `idempotent_duplicate_schedule_request` |
| `sales_intake` (V17) | `qualified_inbound_lead_demo_offered` | `missing_timeline_not_sales_ready` | `existing_contact_resubmits_demo_request` | `unmapped_country_to_fallback_queue` | `opt_out_after_qualification` |
| `internal_it` (V20) | `submit_readonly_request` | `complete_approved_access` | `reject_approval_bypass` | `reject_mismatched_token` | `duplicate_access_idempotency` |

## MCP capabilities

Reusable tool sets with their own seed data and acceptance cases. A vertical copies a capability's
tools and handlers when a scenario needs it (Banking copies Gmail's). They are verified on their
own and are not listed as customer-facing environments (plan decision 2).

| Package | Entry | Tools | Scenarios | Workbook cases | Interface against the official server |
|---|---|---|---|---|---|
| `slack_channel_digest` | M01 Slack | 5 official + `slack_check_message_status` | 5 | 5/5 | Identifiers and input schemas of mcp.slack.com, pinned 2026-11-04 |
| `gmail_inbox_triage` | M02 Gmail | 8 of the server's 10 | 16 | 5/5 | Pinned 2026-11-04 from Google's tool reference; three seed threads are conversations with a reply and a correction |
| `calendar_scheduling_desk` | M03 Google Calendar | 8 of the server's 9 | 12 | 5/5 | Pinned 2026-11-04 from Google's tool reference |
| `drive_policy_search` | M04 Google Drive | 5 of the server's 8 | 7 | 5/5 | Pinned 2026-11-04 from Google's tool reference |

Each capability's `mcp_reference.json` pins the official server (endpoint, transport, sources, the
`tools/list` input schemas and result fields, the tools not attached and why, and the local
extensions: approval and idempotency arguments the workbook's C06 and C07 require, and result
fields the cases need). `tests/harness/test_mcp_capability_reference.py` holds each package to it.
The official servers publish no MCP protocol version; the Google servers are a Developer Preview.

Notion (M05) is outside Wave A and was removed.

## Shared

- `designed.py`: the build for environments the environment builder designed (every package except
  `appointment_booking`, whose `build.py` and `suite.py` are hand-written).
- Capability tables are namespaced (`gmail_*`, `drive_*`, `slack_*`, `calendar_*`, plus the shared
  `current_actor`), so two capabilities can share one world (Wave A plan §5).
- `attach_capability.py`: `python -m fi.alk.environments.attach_capability <vertical> <capability>`
  copies a capability's tools, handlers and table schemas into a vertical and records it in the
  vertical's `mcp_capabilities.json`, which `tests/harness/test_mcp_capability_reference.py` holds
  to the capability.
- `authoring.py`: `python -m fi.alk.environments.authoring sync-checks <package>` rewrites every
  scenario's check files from the package's catalogue, so the proved check is the one runs execute.
- A sub-goal's check is the same file in every scenario that uses it; what it holds a run to is that
  scenario's reference: the arguments its solution marks `fixed`. A value no step fixes is not
  graded by the check. In the verticals, a check that governs a write counts the mail, Slack and
  document evidence the reference fixes only when it was read before that write.
