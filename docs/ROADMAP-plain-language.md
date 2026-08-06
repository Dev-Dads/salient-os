# SalienceOS — from here to the machine

*Written for Josh, in plain language. This is the layer you approve and steer.
The technical specs underneath it are Claude's to maintain and are answerable to
this document — not the other way around.*

---

## The destination

You press the power button and the machine boots straight into SalienceOS — the
way Sparky boots into DGX Linux. No desktop you have to get past, no app to
launch. The thing that greets you, runs your work, remembers what matters, and
decides what deserves attention **is** the system. Under the hood it's an adapted
Linux, the same way Ubuntu and Red Hat are adapted Linux. On top, it's yours.

Built for you first; built so it *could* be for everyone if it works.

---

## What a machine like that is made of

Five parts. Three already exist in some form. The plain-language name comes
first; the project name follows in parentheses.

**1. The judgment system** *(the salience control plane — the salient-os repo).*
The part that decides what matters (attention), what that earns (more scrutiny,
more compute, longer memory — never more permission), what actually happened
(verification against the real world, not the model's say-so), and what the
system is allowed to learn from (only verified successes; risky material gets
quarantined as a warning, never learned as a skill).
**Status: built.** Complete and tested — the final piece, the two "obedience"
gates that make memory and learning actually follow its decisions, lands with
the same pull request that carries this document, externally red-teamed at the
design stage and twice internally reviewed as code. Real kernel work for the OS
no matter what else changes.

**2. The hands** *(the agent host — quorum-agent, your fork).*
The part that does things: runs programs, edits files, uses tools, holds
sessions. Today this exists as your quorum-agent repo. On the finished machine,
a descendant of it is the resident worker the judgment system governs.
**Status: exists and runs today.** Its role in the near term is **test rig**:
a living body we plug the judgment system into so we can watch it govern
something real. That's scaffolding for the OS — not the product. This is the
framing that was missing from the earlier plan.

**3. The mind** *(the resident model).*
The model that lives on the machine — your "40B MoE" example. This is commodity
in the good sense: Sparky-class hardware runs models of that size locally today,
and the OS shouldn't marry any one model.
**Status: exists off the shelf.** The interesting work isn't the model; it's
that the judgment system, not the model, holds the keys. (Sparky itself is
occupied — nothing in this roadmap touches it until you say so.)

**4. The memory** *(CDMS and its constellation).*
What gets kept, how strongly, how it fades, what may never fade (warnings), and
what may never happen automatically (deletion — only policy, never salience).
**Status: exists as separate projects;** the judgment system's memory gate (part
of the work that's ready to build) is the connector that will let the OS govern
it.

**5. The chassis** *(the adapted Linux itself).*
The boot process, drivers, and startup services that make "power on → SalienceOS"
true — the same trick DGX Linux does: a Linux base you never see, configured to
start the mind, the hands, and the judgment system automatically and drop you
into the system's own front door instead of a desktop.
**Status: not started — and deliberately last.** It's packaging around the other
four; building it first would be building a doorway to an empty room.

---

## The path, in stages

Each stage ends with something you can *see working*, described here in advance.
No stage requires Sparky.

**Stage 1 — Finish the judgment system. (DONE — this pull request.)** The two
obedience gates (memory and learning) are built, and the promised proof passes:
a real end-to-end test where the *same* risky, important event is **kept** by
the memory channel as a permanent warning and simultaneously **refused** by the
learning channel — the two channels disagreeing on purpose, which is the safety
property the whole design turns on.

**Stage 2 — Put it in the test body.** Wire the judgment system into
quorum-agent so real activity (tool use, errors, approvals) feeds it, and one
real decision (how much compute a task deserves) obeys it. The proof: a session
log showing the system watching real work and governing a real knob —
conservatively, reversibly, with an off switch.
*This is the reviewed plan's second half, now correctly labeled: test rig,
not destination.*

**Stage 3 — SalienceOS-in-a-box.** The first "turn it on and it's SalienceOS"
moment, in miniature: a virtual machine (or any spare box) that boots to a
minimal screen where the whole stack — a small resident model, the hands, the
judgment system — comes up by itself and talks to you. Ugly, small, and real.
The proof: a cold boot, no keyboard intervention, ending in a working session.

**Stage 4 — Move onto real metal.** When Sparky (or a sibling machine) is free:
same system, serious model, real performance numbers — including the review's
open question of what the hardware can genuinely sustain. The proof: Sparky
boots into SalienceOS instead of DGX Linux, and it's *usable*.

**Stage 5 — Make it installable.** Only if stages 3–4 prove out, and only if
"everyone-if-it-works" still feels right: turn the hand-built system into an
installable image someone else could put on their machine.

---

## The decisions that are yours

These are intent decisions, not technical ones. None are urgent; all will be
brought to you in plain language when their stage arrives.

1. **The front door** (stage 3): when the machine comes up, what happens?
   Does it speak first, or wait? Text, voice, a dashboard, a blank prompt?
   This defines what SalienceOS *feels* like more than anything else.
2. **Default trust** (stage 3): what may the resident system touch without
   asking you — files, network, installs? (The judgment system enforces
   whatever you choose; this is choosing.)
3. **Sparky's turn** (stage 4): when it stops being occupied, and whether the
   real-metal build happens there or on other hardware.
4. **The hands, long-term** (after stage 2): keep evolving your quorum-agent
   fork as the OS's worker, or eventually rebuild a leaner one to the OS's own
   standards. The test rig will teach us enough to decide well.
5. **The name on the door** (any time): whether the assembled system carries
   the SalienceOS name from stage 3 onward, and what happens to the separate
   repo identities. Cosmetic technically; not cosmetic for a product.

---

## Where this stands

Stage 1 was approved and is delivered in the pull request that carries this
document. The next thing you'll be asked to approve is **Stage 2** — wiring the
judgment system into the test body — which arrives with its own plain-language
plan and its correct label: test rig, not destination. Every later stage waits
for its own plan in this same format.
