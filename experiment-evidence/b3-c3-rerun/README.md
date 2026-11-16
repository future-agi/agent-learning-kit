# ALK Week 1 B3 / C3 controlled rerun evidence

ALK sandbox experiment using public APIs — not the production Future AGI platform.

Product commit: `023abdf3655435a4b63177b3e24ee4c14df874ca`.
The product branch is `codex/alk-w1-defect-fixes`. All SDK experiments and
prerequisite checks ran with HEAD exactly at that commit, before creating the
separate evidence branch. No product code was edited. No PR was opened.

## Scope and results

- Prerequisite scoped suite: **200 passed, 2 deselected**, 6 warnings.
- Scoped Ruff on the four files changed by the product commit: **passed**.
- B3: **41/41 assertions passed**. Both `AgentEnvironmentSpec` construction and
  `compile_environment_inputs` are inside the error-catching boundary. The 26
  malformed document/artifact shapes raise typed `source_mapping_invalid`
  errors. Valid mapping/list controls compile, duplicate identifier collisions
  are typed, and ordinary serialized-envelope tamper is rejected.
- C3: **15/15 assertions passed**. The missing target reference fixture keeps
  three drafts and all projections aligned, with one blocked and two reviewable.
  Full draft-set `source_cases` declares 3 and covers 2; the overall obligation
  rate is 0.857143. Public-only structural coverage is 1.0 and is intentionally
  not a readiness gate. An all-blocked fixture has no tool obligations and rate
  0. Evaluator-only blocking changes trusted readiness from 1 to 0 while public
  payloads remain byte-for-byte identical.

## Exact prerequisite commands

Run from the product checkout. Setup used Python 3.12.12 and the frozen lockfile.

```sh
python3 -m pip install --user 'uv==0.9.10'
/home/ubuntu/.local/bin/uv sync --frozen --python 3.12 --extra environment-inputs
.venv/bin/python -m pytest tests/test_environment_inputs.py tests/test_source_scenarios.py tests/test_vendored_simulate_engine.py tests/test_phase7_persona_studio.py -k 'not test_pull_personas_from_stub_server and not test_pull_flagged_quarantined_and_scenarios_sdk_pull' -ra
.venv/bin/ruff check src/fi/alk/studio/_coverage.py src/fi/alk/studio/_environment_inputs.py tests/test_environment_inputs.py tests/test_source_scenarios.py
```

The only excluded tests are `test_pull_personas_from_stub_server` and
`test_pull_flagged_quarantined_and_scenarios_sdk_pull`. The requester reports
that both reproducibly fail against baseline and fixed commits because of
configured authenticated routing. This rerun did not repeat or independently
establish that baseline comparison. The logs show exactly two deselections.

## Exact SDK experiment commands

These are the commands used, from `/home/ubuntu/repos/agent-learning-kit`:

```sh
.venv/bin/python /home/ubuntu/alk-rerun/run_experiments.py b3 --output-dir /home/ubuntu/alk-rerun/b3
.venv/bin/python /home/ubuntu/alk-rerun/run_experiments.py c3 --output-dir /home/ubuntu/alk-rerun/c3
```

`run_experiments.py` is included here as test-only evidence. It asserts the
exact product HEAD before calling public SDK APIs. To reproduce elsewhere,
use a checkout at the product commit, place this script outside the product
tree, and substitute the evidence/output paths in the two commands. Do not
run it from the evidence branch HEAD, which intentionally differs from the
product commit.

The dashboards present the recorded SDK experiment results. Browser interaction
validates the evidence presentation; the browser is not the Python SDK executor.
The recording automation, exact browser/media commands, ffprobe output, complete
ffmpeg decode logs, and media validation are included beside the experiment data.
MP4s come from continuous native Playwright browser capture, transcoded to H.264.
No screenshot slideshow or synthetic cursor is used.

## Limitations and fixture setup notes

- An unkeyed envelope digest is not an authenticity mechanism. Rewriting
  reviewer metadata and recomputing that digest is accepted; this is an explicit
  trusted-storage authenticity limitation, not a failed source-content integrity
  assertion. Altering source text without rehashing, or rehashing only the outer
  envelope while retaining the trusted source digest, is rejected.
- Coverage of the complete `GeneratedScenarioDraftSet` measures trusted draft
  readiness. Coverage of `generated.public_scenarios` measures structure only.
  Neither is executed agent success or production platform E2E evidence.
- Earlier harness setup attempts are retained in `prerequisites/`: evidence
  serialization initially tried to deepcopy an immutable mapping proxy; a
  public-prose all-blocked fixture also implicitly declared retrieval tools.
  The evidence serializer was corrected, and the no-tool fixture now uses
  evaluator-only reference prose. These were evidence harness/fixture issues;
  no product change was made. Final results and videos use the corrected harness.
- SHA256SUMS exclude their own file. Top-level checksums cover all evidence
  payloads and the nested checksum files. Raw GitHub downloads are verified
  against local bytes after pushing; verification results are also delivered in
  the session because a commit cannot contain a record of its own final SHA.

[Written by Devin](https://app.devin.ai/sessions/f74456d7dd8447d2a768111d87d50e57)
