# Agents that drive a browser or a desktop

The world is a site or an application, not a database the agent queries.

`fi/alk/harness/world/kinds.py` already maps `browser`, `computer_use` and `cua` to a browser
world, so start there rather than inventing a kind.

What makes these different:
- State lives in the page, so a check reads the DOM or the application, not a table.
- Reset means the site returns to a known state, which usually means seeding whatever backs it.
- A journey crosses screens. A check that only looks at the final screen will pass a run that took
  a wrong route to the right place.

Build the site or application the agent acts on, seed it, and make sure it can be returned to that
seed between scenarios.
