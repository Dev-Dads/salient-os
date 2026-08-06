"""Assemble the consumers red-team bundle: changed source + tests + invariants."""

import pathlib
import subprocess

HERE = pathlib.Path(__file__).parent
REPO = HERE.parent.parent

FILES = [
    # The reviewed surface (branch feat/consumer-gates vs main).
    "salienceos/interpreter/directive.py",
    "salienceos/interpreter/interpreter.py",
    "salienceos/interpreter/bus.py",
    "salienceos/control/outcome.py",
    "salienceos/control/govern.py",
    "salienceos/consumers/__init__.py",
    "salienceos/consumers/handoff.py",
    "salienceos/consumers/adaptation.py",
    "salienceos/consumers/memory.py",
    "salienceos/consumers/consume.py",
    # Unchanged context the reviewers need to check claims against.
    "salienceos/interpreter/policy.py",
    "salienceos/interpreter/signal.py",
    "salienceos/verifier/verdict.py",
    "salienceos/verifier/signing.py",
    # The tests (honesty is in scope).
    "tests/test_interpret.py",
    "tests/test_control.py",
    "tests/test_consumers.py",
    "tests/test_consumers_e2e.py",
    "tests/test_bus.py",
    "tests/test_discipline.py",
]

CONTEXT = """
INVARIANTS UNDER REVIEW (from the operative design review + design doc):
- P-01: salience influences; policy authorizes. High salience buys scrutiny,
  compute, retention, verification — never a capability. grants_capability()
  is the only capability accessor.
- Finding C: memory and weight-adaptation channels strictly separate AND able
  to DISAGREE — high-salience high-risk content is a memory RETAIN (pinned,
  never-decaying inhibitor) and a weight HARD BLOCK simultaneously.
- Finding D: the consumer gates CONSUME the recorded decision, never re-derive
  it — no gate reads verdict.status or raw salience; the nomination predicate
  is exactly bool(outcome.adaptation_allowed).
- docx 4.4: an ASSERTED over-cap risk (RISK_EXCEEDED) is the ONLY inhibitor
  trigger; RISK_UNKNOWN (absent/uninformative risk) blocks eligibility but is
  never an incident.
- docx 4.5: decay applies to the derived retrieval weight only, never the
  event record; inhibitors are exempt from decay (the pinning primitive).
- docx 3.1: deletion/tombstone is policy's alone — no memory record carries a
  delete, tombstone, or scope field.
- docx 13: the adaptation ceiling is CANDIDATE (offline review); no promote or
  apply surface exists.
- Discipline: stdlib-only allowlist (dataclasses, enum, hashlib, hmac, json,
  os, pathlib, subprocess, salienceos), no clock (now_days injected), no
  async, frozen dataclasses, fail-closed everywhere ("a crash is not a deny").
- ADR 0001 scope (unchanged): the bus hash chain detects accidental
  corruption; consistent malicious rewrite AND tail-truncation-across-reopen
  need an externally anchored head, which is deferred.

WHAT'S NEW ON THIS BRANCH (already internally reviewed twice; an 18,816-case
differential sweep proved the interpreter refactor behavior-preserving):
1. AdaptationRationale recorded by interpret() (priority chain, order pinned).
2. GovernedOutcome self-describing: decide() stamps the BOUND directive +
   subject, withholds both on every unbound/invalid path; _valid_directive
   validates the rationale (enum + ELIGIBLE<=>CANDIDATE coherence).
3. salienceos/consumers/: nominate() (weight gate), retain()/effective_weight()
   (memory governor), InhibitorHandoff (attribution-validated), consume().
4. Bus: directives_for() reader (deep copies), replay-on-open (verifying,
   fail-closed, exact key-set fence).
"""

parts = [f"\n\n########## CONTEXT ##########\n{CONTEXT}"]
diff = subprocess.run(["git", "diff", "main...HEAD", "--stat"],
                      cwd=REPO, capture_output=True, text=True).stdout
parts.append(f"\n\n########## BRANCH DIFFSTAT (feat/consumer-gates vs main) ##########\n\n{diff}")
for rel in FILES:
    text = (REPO / rel).read_text(encoding="utf-8")
    parts.append(f"\n\n########## {rel} ##########\n\n{text}")

bundle = "".join(parts)
(HERE / "bundle_consumers.txt").write_text(bundle, encoding="utf-8")
print(f"bundle: {len(bundle):,} chars (~{len(bundle) // 4:,} tokens)")
