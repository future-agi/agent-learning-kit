# Voice agents hosted by a platform (Vapi, Retell, and anything like them)

The direction is inverted here. There is no worker to start: their platform runs the agent and
calls you. What you build is something reachable that answers the way their tools expect.

You will usually be given credentials, an assistant or agent id, and a repository holding the tool
implementations their assistant already calls by webhook.

What that means for you:

- Build the tool service from the repository and put a real store under it, the same as any other
  agent. That part does not change.
- The service has to be reachable from their platform, not just from inside this sandbox. Work out
  what the ingress is before you build anything on top of it.
- Their assistant configuration names the webhook. Point it at what you built, or state plainly
  that you cannot and why.
- Their platform holds the conversation, so you do not own turn taking, barge-in or audio.

If you cannot reach their platform from here, say so and stop. A world that looks right but
receives no calls is worse than an honest failure, because the run will look like an agent defect.

ALK's own voice stack is for agents we run ourselves. Most of it does not apply. `world/` and the
stores do.
