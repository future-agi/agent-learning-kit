# Browser/CUA Probe Research Note

Date: 2026-06-08

## Why This Exists

Browser/CUA manifests already exercise local browser replay environments with
snapshots, selector drift, screenshots, storage, runtime, network, mutation
packs, and prompt-injection surfaces. They still need the same cheap preflight
path as framework, memory, multi-agent, and realtime stacks: a user should be
able to pass local browser/CUA fixtures, prove refreshed perception plus safe
action replay locally, then promote the selected bundle into the normal
`agent-learning.run.v1` simulation path.

## Current Browser-Agent Signals

- OpenAI's computer-use tool loop is built around screenshots, model-selected
  computer actions, and follow-up environment screenshots. Probe implication:
  the local fixture must preserve screenshot/DOM snapshots, action replay, and
  state changes as structured evidence rather than only final text. Source:
  https://platform.openai.com/docs/guides/tools-computer-use
- Anthropic's computer-use docs emphasize a sandboxed computer environment,
  screenshots, coordinate actions, and display-size/coordinate scaling caveats.
  Probe implication: CUA replay needs coordinate-region grounding, refreshed
  snapshot checks, and explicit rejection of live targets unless requested.
  Source:
  https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/computer-use-tool
- Playwright Trace Viewer records action timeline, screenshots, DOM snapshots,
  console, network, and source information for browser tests. Probe implication:
  browser/CUA candidates should expose trace-like action, visual, runtime, and
  network evidence before optimizer search accepts them. Source:
  https://playwright.dev/docs/trace-viewer
- Browser Use describes browser automation agents that connect LLM decisions to
  browser actions and page state. Probe implication: local CUA preflight should
  test browser actions, state updates, and safety checks as one bundle instead
  of treating browser setup as a prompt-only adapter. Source:
  https://docs.browser-use.com/introduction

## Implementation Rule

Keep browser/CUA support local-first:

- Accept manifest-style `browser` and `browser_cua` environment bundles.
- Reject explicit HTTP/HTTPS probe targets and remote trace sources by default.
- Allow HTTPS URLs inside local fixtures, because the checkout replay uses
  `https://shop.example.test/...` without contacting a live service.
- For framework adapter outputs, normalize explicit browser/computer-use
  actions, DOM/screenshot snapshots, Playwright-like trace fields,
  storage/runtime/network logs, mutation packs, screenshot diffs, and
  prompt-injection surfaces into `browser_cua` state, browser trace/screenshot
  artifacts, browser events, and browser tool calls before promotion. Generated
  adapter-probe eval configs should derive `required_browser_trace`,
  `expected_browser_actions`, `expected_browser_regions`,
  `browser_mutation_resilience`, and prompt-injection avoidance gates from the
  selected output and weight `browser_trace_coverage`,
  `browser_action_outcome`, `browser_grounding_quality`, and
  `browser_mutation_resilience`.
- Require refreshed snapshot evidence, safe selector fallback, coordinate-region
  grounding, action replay, mutation-pack and screenshot-diff evidence,
  storage/runtime/performance/network verification, layout-shift evidence, and
  prompt-injection-surface avoidance before a probe is considered closed.
- Use `simulate.run_browser_cua_probe()` or
  `optimize.optimize_browser_cua_probe()` to select among local browser/CUA
  candidates cheaply, then use
  `optimize.build_browser_cua_run_manifest_from_probe_optimization()` when the
  selected browser bundle should become a normal evaluated CUA simulation.
