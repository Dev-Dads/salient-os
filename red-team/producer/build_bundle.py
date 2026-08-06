"""Assemble the PR-H1 producer red-team bundle: the quorum-agent observer +
seam + tests, the host dispatch context, and the vendored APIs it calls."""

import pathlib

HERE = pathlib.Path(__file__).parent
QA = pathlib.Path(r"D:\Repo\quorum-agent")
SOS = pathlib.Path(r"D:\Repo\salient-os")

# Whole files (the reviewed surface + the vendored APIs the observer calls).
WHOLE = [
    (QA, "hermes_cli/observability/salience_observer.py"),
    (QA, "hermes_cli/observability/__init__.py"),
    (QA, "tests/hermes_cli/test_salience_observer.py"),
    (SOS, "salienceos/interpreter/signal.py"),
    (SOS, "salienceos/interpreter/policy.py"),
    (SOS, "salienceos/interpreter/bus.py"),
]

# Line-range excerpts (1-indexed, inclusive) for host dispatch context.
EXCERPTS = [
    (QA, "hermes_cli/lifecycle.py", 1, 45, "has_hook / invoke_hook (the gate + dispatch)"),
    (QA, "model_tools.py", 1045, 1094, "_emit_post_tool_call_hook (emitter; self-gates on has_hook)"),
    (QA, "run_agent.py", 2810, 2840, "api_request_error emit site"),
    (QA, "agent/turn_context.py", 1050, 1067, "pre_llm_call emit site (window-open)"),
    (QA, "tests/conftest.py", 443, 470, "suite-wide observer disable/reset guard"),
]

CONTEXT = """
PR-H1 wires the SalienceOS judgment system into quorum-agent (a fork of hermes) as a
TEST RIG. It is PRODUCE-ONLY: a first-party observer watches real lifecycle hooks and
records bounded salience signals + one directive/turn on a per-session, hash-named
audit bus under <hermes_home>/salience/. Consuming a directive (the compute budget) is
a LATER PR. The guarantees under review:

1. PRODUCE-ONLY / NEVER BREAKS THE HOST. Never changes what the agent does; never lets
   an exception (including a BaseException like SystemExit) reach the host. Contained by
   _safe_observe at the seam plus the observer's own try/except. (The author already
   caught and fixed one such path: get_config_value() calls sys.exit() on a missing key,
   a SystemExit that would sail past `except Exception` — now uses the programmatic read.)
2. FAIL-CLOSED ATTRIBUTION. A signal is recorded ONLY against an open window whose
   turn_id matches. No / empty / mismatched session_id or turn_id => dropped, never guessed.
3. HASHED IDENTITY (A11 / ADR 0002). The raw session_id must never reach the durable
   record — both the subject token and the bus filename hash it.
4. AUDIT FENCE (Finding G). Only bounded ref tokens reach the bus — never prompts, tool
   args, results, or chain-of-thought. salienceos valid_signal() / the directive payload
   fence enforce it structurally; a signal that FAILS valid_signal (noise) is also a bug.
5. GATING. Quorum Edition + config salience.enabled; default ON with a kill switch;
   absent config => ON, explicit false => OFF, unreadable config => OFF (fail-closed).
6. SEAM SAFETY. Adding the observer to observe_lifecycle / handles_hook must NOT change
   invoke_hook's return value, the dispatch to relay_shared_metrics, or the EFFECT of any
   hook. Enabling salience flips has_hook True only for the observational post_tool_call
   and api_request_error emitters (previously dead by default); pre_verify and
   transform_tool_result stay unhandled; pre_llm_call's context-injecting return is
   consumed from PLUGINS only, never from the observer.
7. A3. Turn N's window is finalized (its directive emitted) before turn N+1 accumulates.
8. A4. The policy's min_budget is bound to the operator's resolved budget; in v0 the
   directive.compute_budget is inert (nothing consumes it — that is PR-H2). min==max in v0.
9. SINGLE-THREADED BUS CONTRACT. All bus + registry (_WINDOWS/_BUSES) access is serialized
   under _LOCK; no per-session leak on a long-lived host (freed on session close).

ALREADY INTERNALLY REVIEWED TWICE (correctness/fail-closed + design/test-honesty).
Accepted findings ALREADY FIXED, do not re-report: a per-session memory leak (now freed
on session close, not on turn rollover), a double config read for the budget (memoized),
and missing directive-content test coverage (added). YOUR VALUE is what those missed.

VENDORED CODE NOTE: salienceos/ inside quorum-agent is a VERBATIM vendored copy of
already-reviewed code — do NOT red-team its internals. signal.py / policy.py / bus.py are
included ONLY so you can verify the OBSERVER calls those APIs correctly. Flag the vendored
code only if the observer misuses it.
"""

parts = [f"\n\n########## CONTEXT ##########\n{CONTEXT}"]
for base, rel in WHOLE:
    text = (base / rel).read_text(encoding="utf-8")
    tag = rel if base is QA else f"[vendored api] {rel}"
    parts.append(f"\n\n########## {tag} ##########\n\n{text}")
for base, rel, a, b, label in EXCERPTS:
    lines = (base / rel).read_text(encoding="utf-8").splitlines()
    excerpt = "\n".join(lines[a - 1:b])
    parts.append(f"\n\n########## {rel} (lines {a}-{b} — {label}) ##########\n\n{excerpt}")

bundle = "".join(parts)
(HERE / "bundle_producer.txt").write_text(bundle, encoding="utf-8")
print(f"bundle: {len(bundle):,} chars (~{len(bundle) // 4:,} tokens)")
