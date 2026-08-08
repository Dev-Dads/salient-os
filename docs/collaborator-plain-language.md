# SalienceOS — the Collaborator

*Written for Josh, in plain language. This is the layer you approve and steer.
The technical spec underneath it is Claude's to maintain and is answerable to
this document — not the other way around. Same format as
`ROADMAP-plain-language.md`; this is the plan for the next thing we build.*

---

## Where this fits

The roadmap has five parts: the **judgment system** (built), the **hands** (your
quorum-agent fork, running today as the test rig), the **mind** (an off-the-shelf
model), the **memory**, and the **chassis** (last). It also left you an open
decision — **#4, "the hands, long-term":** keep evolving the quorum fork as the
OS's worker, or eventually **rebuild a leaner one to the OS's own standards.**

This plan answers #4, early, and on purpose. **The Collaborator is the hands,
rebuilt lean and native — a worker we own, built to be governed from the first
line instead of governed after the fact.** It's the first corner of the two-layer
picture we settled: a familiar computer you *operate* directly, wrapped in a
governed layer you *steer*. The Collaborator is that governed layer's engine —
and the chat-window box from Stage 3 goes back to being what it always was, a
demo surface, not the product.

Why build it now, ahead of the chassis? Three reasons, all real:

1. **It needs no desktop to exist.** It's software that runs on the computer you
   already have. No "doorway to an empty room."
2. **It closes a gap we hit for real.** On the box, some models *say* a tool call
   in plain text and the system never runs it — then the model cheerfully reports
   a result that never happened. The judgment system caught the lie every time,
   which is the point — but the hands were unreliable. Owning the loop fixes that
   at the root.
3. **It's the half that "grows alongside you."** The judgment system decides what
   matters; the Collaborator is the thing that then *does* something about it,
   under your eye. Without it, the judgment system is a mind with no hands it can
   trust.

---

## What it is, in one breath

**One presence you can talk to, task, and steer — and that talks back — where
every action it takes on the system is governed, visible, and held to a leash you
keep in your hand.**

Not a chatbot with a shell attached. The difference is the whole point: a normal
agent, when you tell it to do something, just goes and does it and tells you it
worked. The Collaborator, before it acts, shows you **the governed task it's about
to run** — what it understood you to mean, how far that reaches, how much trust
and compute it will use — and then acts *inside* that envelope while the judgment
system watches every step and writes it down. Importance can buy it more scrutiny
and more compute; it can never buy it more permission. That rule is not a feature
you can turn off — it *is* the Collaborator.

It meets you at whatever depth the moment needs:

- **A glance** — you type a one-line instruction, it shows you the task it will
  create, you confirm, it runs.
- **A conversation** — you plan or think something through together, in language,
  because planning *is* language.
- **It comes to you** — it notices something worth doing and brings you a
  proposal, rather than waiting to be asked. (This last one is the heart of "grows
  with you," and it's deliberately the most governed.)

---

## What it's made of

Four parts. None of them is a model — the model is the mind we rent; these are the
things that make the mind's intentions safe to run.

**1. A loop we own.** The turn-by-turn cycle — read you, think, act, report — is
ours, not borrowed. That's what lets the judgment system sit *inside* the cycle
instead of alongside it, and it's what lets us decide what "the model did X" is
actually allowed to mean.

**2. Tool-reading we control.** When the mind wants to use a tool, we read that
intent ourselves — whether the model emits it the clean structured way or buries it
in plain text (the case that silently failed on the box). Only a tool call in a
strict, unambiguous form is ever run; anything ambiguous — a command shown as an
example in a sentence, a half-formed call — fails closed to "not a tool call" and is
surfaced to you, never guessed into running. One honest path in, and it errs toward
doing nothing.

**3. A small, honest toolset, fenced to a workspace.** Read a file, write a file,
run a command — a deliberately short list to start, all of it confined to a declared
workspace folder. The Collaborator's *own* wiring — the config that holds your trust
and leash settings, the key that signs its policy, the audit trail itself — lives
*outside* that folder, where no tool can reach it, so the hands can never rewrite the
rules they run under. "Run a command" is governed as one whole action (we don't
pretend to police what happens inside a shell script), so it starts on the strictest
leash. Each tool reports what it truly did; a tool that didn't run says so.

**4. The governance seam.** This is the novel part. **Every tool call is its own
governed action** — even when the model asks for three at once, and even when it
retries, each is judged separately, never averaged into one "turn." Before an action
happens, the judgment system interprets it: is this the kind of thing the standing
**policy** permits at all — the *capability gate*, the one authority check the core
itself enforces — and how much scrutiny and compute does its importance earn? Then
the Collaborator applies a second, softer control that *it* enforces — the **leash** —
and only then runs, holds, or refuses. Nothing is re-decided by the hands; they
*obey the recorded decision*, and every step is written to an audit trail you can read.

And it **fails closed.** If the judgment system is unreachable, errors, or comes back
saying "not permitted" or "couldn't decide," the action is **denied and logged — never
run anyway to keep the chat moving.** Safe is the default the whole thing falls back to.

Two dials, and only one of them opens doors:

- **What it may touch at all** — the *capability* — comes from policy you set, checked
  by the judgment core on every action. Importance cannot add to it; the model cannot
  talk its way past it.
- **How it may act within that** — the *leash* — is yours too, set per task:
  - **act-then-report** — do it, tell me after (low-stakes work whose effects are easy
    to see and correct).
  - **propose-first** — show me the plan, wait for my yes (the default for anything that
    reaches outside itself, and for running commands).
  - **notify-only** — don't act; just tell me it's worth doing.

Importance (salience) moves only the *scrutiny* and *compute* dials. Capability and
leash are **policy and config inputs** — never chosen by the model's output, never
raised by how important something feels. That's P-01, made physical in the one place
it matters most: the hands.

---

## The path, in steps

Each step ends in something you can *see working*, described here in advance. None
requires Sparky, though we'll test against a real model on it and in Docker where
it proves more.

**Step 0 — the governed loop exists and obeys. (This is what I build first.)**
A working session, start to finish: you give a plain instruction; the Collaborator
shows you the governed task it will run (what it understood, its reach, its trust
and leash); on your confirm it executes through its own loop while the judgment
system records and governs each tool action. The proof is four things happening
in one run:
- a model that emits a tool call as *plain text* actually gets its action run
  (the box gap, closed);
- a **low-stakes** action on `act-then-report` runs, and you see the tool's **real
  result** — the exit code, the output, the actual change — reported straight from the
  tool, not just the model's say-so (so a step that *failed* can't be narrated as a
  success);
- a **higher-stakes** action on `propose-first` is **held for your approval** instead
  of just happening;
- and every one of these is in the audit trail. To show the governance was
  load-bearing, the proof runs the *same* task twice — once governed, once through a
  bare tool-runner — as a side-by-side. The governance is never a live switch you can
  flip off inside a running session; that would be a door in the wall.

**Step 1 — depth and the two-way channel.** The leash becomes fully per-task and
adjustable mid-flight; the **propose channel** turns on — the Collaborator brings
*you* a proposal it noticed, framed as a governed task you approve or veto. Richer
salience: important work gets visibly more scrutiny and budget, never more reach.
The proof: it interrupts its own idleness to suggest something useful, and you
approve it into existence with one confirmation.

**Step 2 — the judgment view.** The Collaborator's own surface — not a chat box, a
**view of what it's attending to, running, and proposing**, with the trust dial and
the leashes as controls you can put your hand on. (Conversation stays; it's just no
longer the *whole* interface.) The proof: you steer a running job from the view —
tighten its leash, pause it, veto its next step — without typing a sentence.

**Later — it becomes the resident worker.** When the chassis stage arrives, this is
the body that boots with the machine and the body in which "it grows *itself*"
(Stage 4) safely runs. Nothing here forecloses that; it's built to grow into it.

---

## The thread through all of it

Same thread as the whole system: **it grows alongside you, safely.** The
Collaborator is where "safely" stops being architecture and starts being something
you watch happen — a plan you confirm, a reach you can see, a leash in your hand, an
action that gets written down. And it's built on the one honesty the references we
admire quietly skip: the mind is *fallible* (we watched it invent a result), so the
hands must never be trusted on the mind's word alone. Because we own the path between
intent and act — and show you what the tools *actually* did, not only what the model
says — the truth of what happened lives in the audit trail, where the model's prose
can't quietly diverge from it.

---

## Honest scope — what Step 0 is and isn't

- **It is** a real governed agent loop we own, doing real work against a real model,
  with real tool actions mediated and recorded by the judgment system.
- **It is not**, at Step 0, a pretty interface (that's Step 2), the full propose
  channel (Step 1), or a large toolset. Short and honest beats broad and leaky.
- **The trust/leash lives in host config to start** — the principled home (a signed
  policy the judgment system carries) is a deliberate follow-up, the same way the
  resource governor deferred its signed policy field. The behavior is real now; the
  *provenance* of the authority hardens next.
- **It reuses, it doesn't fork.** The judgment system it's governed by is the same
  `salienceos` core, untouched. The Collaborator is a new worker that lives *beside*
  the core, not inside it — the core stays a small, pure, stdlib-only thing you can
  hold in your head; the Collaborator does the messy real-world work (talking to a
  model, running tools) and *consumes* the core's decisions, never reaching inside
  them. Same discipline as everything else that uses the judgment system.
- **It's what makes one of the judgment system's safety gates testable in the wild.**
  The learning gate — where the *same* risky, important event is kept as a permanent
  warning *and* refused as a skill (the two channels disagreeing on purpose, the
  Stage-1 proof) — is proven today only in the library, because nothing has yet run
  real work through it on a policy that allows learning at all. The gate already exists
  and is tested; the second thing I build tonight is *not* a rebuild but the narrow
  wiring that lets a real, risky-and-important Collaborator action trip it — so the
  disagreement fires on live activity instead of a test fixture. That path is
  deliberately precise (a policy that permits adaptation, and a genuinely
  over-the-line risk on a real action), and I'll name exactly what it takes in that
  step's own note.

---

## The decisions that are yours

Intent decisions, not technical ones. None are urgent; each will come to you in
plain language when it matters.

1. **The default leash.** When you give a quick instruction, should the default be
   *propose-first* (safe, asks a lot) or *act-then-report* for clearly reversible
   work (fast, trusts more)? This sets how the Collaborator *feels* day to day.
2. **The toolset's edge.** How far does the starting toolset reach — files and local
   commands only, or network and installs too? (The judgment system will govern
   whatever you choose; this is choosing the outer boundary.)
3. **The propose channel's boldness** (Step 1): how eagerly should it bring you
   unprompted proposals — only when confident, or more freely with a light touch?
   This is the "how proactive" half of the character decision, made concrete.
4. **Where it runs first.** The near-term body is the computer you're on now; when
   Sparky is free and the chassis stage comes, this is the worker that moves onto
   metal. That timing stays yours (roadmap decision #3).

---

*Status: plan written; Step 0 is what gets built and reviewed next. Everything past
Step 0 waits for you.*
