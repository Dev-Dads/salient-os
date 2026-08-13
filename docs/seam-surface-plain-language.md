# SalienceOS — the seam, made a place you can stand

*Written for Josh, in plain language. This is the layer you approve and steer. The
technical spec underneath it is Claude's to maintain and is answerable to this
document — same format as `ROADMAP-plain-language.md` and `collaborator-plain-language.md`.
This is the plan for **②**, the next thing we build.*

---

## Where this fits

The Collaborator's parts are all built: a loop we own, tool-reading we control, a
small fenced toolset, the governance seam, a propose channel, and a judgment view.
Each has been proven — but only *one at a time*, by throwaway scripts. Nothing yet
assembles them into a thing you can **sit in front of, watch, and steer**.

That assembly is **②, the seam/partner surface — "Sal."** It's the middle step of the
three we settled: **① make it move** (done — the hands act reliably), **② the seam/
partner surface** (this), **③ the chassis** (last — the machine boots into it). ② is
where "governed, visible, on a leash" stops being an architecture diagram and becomes
something happening on your screen.

We proved this was the right next step the empirical way: we wired the parts together
live for the first time and watched. The propose channel brought real suggestions, the
controls worked, the pause held a live action — and the exercise flushed out three real
reliability bugs in the loop, now fixed. What was missing was never the parts. It was
the *place they live together*.

---

## What it is, in one breath

**One presence you talk to, that talks back, and whose every action you can watch happen
and stop with your hand — in a page open in your browser.** Not a chatbot with a shell:
before Sal acts, the action is a governed step you can see, and the judgment system
decides what it's allowed to be. Importance can buy it more scrutiny and more compute;
it can never buy it more permission. That rule isn't a setting — it *is* Sal.

---

## What you'll be able to do

- **Talk to it.** Type an instruction; watch Sal work through it in small, governed
  steps, each step showing what actually happened — the real result, not the model's
  say-so.
- **Watch it.** A live panel shows what Sal is attending to, running, and proposing,
  with the leashes and the trust dial shown as what they really are right now.
- **Steer it without typing.** Pause it. Tighten a tool's leash. Approve or veto a
  step it's holding for you. All from the page, no sentence required.
- **Let it come to you.** When it's idle, Sal can notice something worth doing and
  bring you a proposal — framed as a governed step you approve or wave off. This is the
  most-governed part on purpose: it can *suggest*, never *self-authorize*.

---

## How we build it — three steps, each something you can see

Each step ends in a working demo, described here in advance. None needs Sparky, though
we test against a real model on it.

**Step A — the engine that holds it together.** One thing (the "Host") that owns the
loop, the propose channel, the view, and the record — so they stop being hand-wired and
start being one governed worker. It runs your task on its own thread while staying
responsive, tracks whether a task is running / waiting on you / done / failed, and can
bring you an idle-time proposal. *The proof: the same live session we ran by hand, now
run by the Host end to end — including a step it holds, you approve, and it finishes.*

**Step B — the page.** A small local web page (served only to your own machine) where
you type to Sal and watch a real task get governed live. *The proof: turn it on, open
the page, give it a job, watch the governed steps happen in front of you.*

**Step C — the hand on the leash.** The controls become real buttons — pause, approve/
veto, tighten a leash. *The proof: you steer a running job entirely from the page,
without typing a sentence.*

---

## The one rule that shapes every choice

The page is a new **way in**, so it must not become a new way to grant **power**. Every
control either *restricts* (pause, tighten) or expresses *your own setting* (a leash,
the trust dial) — none can hand Sal a capability it wasn't granted. The judgment
system stays the single place authority is decided; the page is your hand on the
wheel, not a second wheel. And because it's a door, we treat it like one: it opens
only to your own machine, and only to you.

---

## Honest scope — what ② is and isn't

- **It is** the real assembly: a governed presence you talk to and steer, doing real
  work against a real model, every action recorded.
- **It is not** the finished machine (that's ③, the chassis), a large toolset, or a
  polished product. Small, honest, and real beats broad and leaky.
- **Pause means "after the current step,"** not "kill what's mid-flight" — a running
  command finishes, then the next step is held. We'll say so on the page.
- **It reuses, it doesn't fork.** The judgment core is untouched; Sal is a worker that
  lives beside it and *consumes* its decisions, never reaching inside them.

---

## The decisions that are yours

Intent decisions, not technical ones. None are urgent; each comes to you in plain
language when it matters.

1. **How forward Sal is** (the trust/proactivity dial): how often it should bring you
   idle-time proposals — never, occasionally, or eagerly. You set it; it only changes
   how much Sal *suggests*, never what it's *allowed* to do.
2. **The default leashes**: which tools act-then-tell-you versus wait for your yes.
   (Running a command starts on the strictest leash.)
3. **The look, later**: ② is deliberately plain. Whether Sal's surface eventually
   becomes the machine's "front door" is a Stage-③ decision.
