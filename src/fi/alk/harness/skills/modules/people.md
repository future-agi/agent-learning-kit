---
name: people
applies_to: conversational=yes
description: How to describe the person an agent is talking to, and how to spread them across a suite. Read alongside the planning instructions whenever the agent under test converses with somebody. An agent with no counterparty never loads this.
---

# Who the person is, and what state they are in

A person is not a label. Two suites that both say "angry caller" can be testing entirely different
things. Describe each one as a short vector over these, and the suite becomes something you can
account for rather than a list of adjectives.

**Who they are.** Six sub-dimensions, each with its own closed levels:

```
life stage      child · young adult · adult · senior
literacy        novice · average · expert            (with this domain, not with technology in general)
language        native · regional accent · non-native · prefers another
expression      clear · some difficulty · marked difficulty
role            themselves · on behalf of somebody · a professional third party · an administrator
standing        anonymous · claims an identity · verified · privileged
```

**What state they are in.** Five more, and these compose rather than exclude:

```
feeling         positive · neutral · negative
urgency         low · moderate · high
clarity         clear · confused · impaired
cooperation     forthcoming · withholding · evasive
direction       steady · getting worse · settling down
```

Most combinations are nonsense and you should not use them: cheerful and impaired and desperately
urgent describes nobody. Pick the handful that are real for this agent and spread them.

**Spread them deliberately, because nobody does it by accident.** Measured on one suite of thirty:
twenty of thirty callers were American and in the United States, against an even spread when the
shares were handed out in the briefs. A suite where everybody sounds the same has tested one
accent. The same goes for state: a suite where everybody is calm and clear has tested the easy
half of the job.

**The shape of the exchange is the last thing to vary, and it is cheap.** Four questions, and the
answers are yours:

```
turn structure  one shot | several straight through | several that branch
continuity      fresh | picking up something earlier | interrupted partway
what they hold  nothing | what was said this session | something from a previous one
pace            steady | they run ahead | they go quiet and return
```

A suite where every scenario is one person asking one thing straight through has tested a shape the
agent will rarely meet. Give some of them a branch, a return, and something to remember.

Where the platform constrains a value, the platform wins. Accents and languages select a real
voice, so only the offered values exist; project the level onto what is offered rather than
inventing a value that cannot be rendered.
