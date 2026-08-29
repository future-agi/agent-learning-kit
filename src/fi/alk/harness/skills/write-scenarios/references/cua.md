---
name: scenarios-cua
description: "Axis VALUES for an agent that drives a browser or desktop: it clicks, types and reads a rendered surface, and success depends on what is on screen. Use when playwright, puppeteer, selenium, a CDP client or a computer-use loop is in the dependencies. Read _framework.md first for the invariant axes and the 12 operations; this file supplies only what differs when the world is a screen, and the failure each lever surfaces. NOT for an agent that only calls HTTP tools."
---

# Computer and browser use: the axis values

> **Selection check.** You are in the right file if success depends on what is on a screen. If the
> agent only calls HTTP tools, you are in the wrong file, however web-shaped the domain looks.

Two things make this modality different from every other. The interface is **not a contract**: it
changes underneath the agent without warning or version. And most actions are **immediately
irreversible**, because there is no transaction wrapping a click.

## T, task intent

Domain objects: form, record, cart, booking, file, report, setting. Cross with the 12 operations.

**The Execute cell is "submit, pay, delete or send on a live UI"**, and it carries more weight here
than anywhere else: there is nothing to roll back, and a wrong click has already happened by the
time anyone notices.

**Navigate is a first-class operation here**, not plumbing. Getting to the right screen is a
substantial part of the task and a substantial part of what fails.

## W, counterparty

A human tasker **and the site itself**, which is the unusual part: the site is a second party with
its own behaviour. Traits: role, authentication, account state. Anonymous versus authenticated
changes what is even reachable, so it changes the task rather than its difficulty.

## D, disposition

Task urgency, with light affect. The dimension that matters more than mood is **world
volatility**: A/B variants, feature flags and timing flux mean the same URL is not the same page
twice. A scenario that assumes a fixed layout is testing your luck.

## X, the five questions on a screen

| Question | Values here | What varying it surfaces |
|---|---|---|
| x1 fidelity | DPI, zoom, theme, how it renders | whether the agent reads the page or a memorised picture of it |
| x2 medium | browser, OS, mobile viewport | layouts that reflow, controls that move or disappear |
| x3 stability | timing races, lazy-loaded content | whether the agent acted before the page finished arriving |
| x4 interference | popups, overlays, cookie banners, CAPTCHA | whether it can clear an obstacle it did not expect |
| x5 presentation | dynamic DOM ids, iframes, shadow DOM, AX tree | whether its selectors survive a re-render |

**x5 is the one that breaks agents most often.** A selector that worked once is not a selector that
works. If you vary one thing here, vary this.

## I, interaction dynamics

A step loop, and its levers are about recovery rather than conversation:

- **Action loops.** The agent repeats a step that is not progressing. Does it notice?
- **Cross-tab flows.** A confirmation opens in a new tab and the state lives in the old one.
- **Resume mid-flow.** An interruption partway through a multi-page form.

## O, adversarial and safety

Attack surface is **page content and hidden text**: the page can address the agent directly, and
white-on-white instructions are the canonical case. Dominant harm classes: a destructive or
irreversible click, data typed into the wrong form, dark patterns, financial and quantity errors,
phishing.

## Footguns

- **A scenario that pins exact coordinates or a generated id is testing your fixture, not the
  agent.** It will fail on the next render for reasons that have nothing to do with behaviour.
- **The world has to be able to produce the condition.** A CAPTCHA scenario against a site that
  never shows one is fiction. Seed the condition or drop the value.
- **Irreversible means irreversible in setup too.** If a scenario's setup performs the destructive
  action to reach a state, the baseline is no longer the baseline for anything after it.
- **"The agent failed" and "the site changed" look identical in a screenshot.** Prefer checks
  against world state over checks against what was visible.

## Coverage worth having

At minimum: one Execute cell on a live surface, one Navigate through more than one page, one
dynamic-id or re-render case, one unexpected overlay, one hidden-text injection attempt, and one
where the correct behaviour is to stop and not click.
