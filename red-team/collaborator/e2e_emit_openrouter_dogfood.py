"""CAPSTONE live proof: autonomous outbound emission through the governed entry point, end-to-end,
against the REAL OpenRouter host — Josh's motivating case (the Collaborator emitting a red-team
panel call itself).

This is the honest "it actually works" proof for PR A: emit(autonomous=True) drives a genuine
credentialed HTTPS POST to openrouter.ai through the full seam — signed caps ("require both":
net.post:openrouter.ai + net.post.auto:openrouter.ai + net_post act_then_report leash-cap), the
host-injected Authorization credential (NEVER in the model args), the emission floor lifting to
act_then_report, and the body-free audit split — and gets a real model completion back. It also
proves the negatives against the real host: a model-emitted call stays HELD (F1), and the same
call with a missing signal never emits.

Reports API cost (standing request). Small spend (one tiny 1-token-ish completion).

Usage (needs OPENROUTER_API_KEY + network):  python red-team/collaborator/e2e_emit_openrouter_dogfood.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json  # noqa: E402

from collaborator import egress  # noqa: E402
from collaborator.governance import HELD, NOTIFIED, RAN, govern_action  # noqa: E402
from collaborator.loop import emit  # noqa: E402
from collaborator.policycaps import mint, workspace_subject  # noqa: E402
from collaborator.session import Session  # noqa: E402
from collaborator.toolcall import ToolIntent  # noqa: E402

HOST = "openrouter.ai"
URL = "https://openrouter.ai/api/v1/chat/completions"
KEY = os.environ["OPENROUTER_API_KEY"].strip()
CAPS_KEY = b"dogfood-caps-key"
# One cheap model, a minimal prompt, tiny max_tokens — the point is the GOVERNED PATH, not the reply.
BODY = json.dumps({
    "model": "openai/gpt-5.1",
    "messages": [{"role": "user", "content": "Reply with the single word: EMITTED"}],
    "max_tokens": 16, "temperature": 0,
    "usage": {"include": True},
})


def _full_session(tmp):
    """A session whose SIGNED grant fully authorizes autonomous emission to OpenRouter (require both:
    the per-host auto cap AND an explicit net_post act_then_report leash-cap), with the API key
    host-injected — never in the model args."""
    signed = mint((f"net.post:{HOST}", f"net.post.auto:{HOST}"), {"net_post": "act_then_report"},
                  "operator", workspace_subject(tmp), CAPS_KEY)
    return Session(workspace=tmp, policy_caps=signed, caps_key=CAPS_KEY,
                   egress_credentials={HOST: f"Bearer {KEY}"})


def main():
    checks = []
    cost = None
    with tempfile.TemporaryDirectory() as tmp:
        s = _full_session(tmp)

        # (1) THE CAPSTONE — operator-directed autonomous emission actually reaches OpenRouter.
        dec = emit(s, URL, BODY, autonomous=True)
        ran = dec.status == RAN
        checks.append(("emit(autonomous=True) with full grant RUNS autonomously (act_then_report)",
                       ran and dec.leash == "act_then_report"))
        out = (dec.result.output if dec.result else "") or ""
        rec = dec.egress
        # The response is real + UNTRUSTED-tagged; the governed POST reached OpenRouter (200, bytes back).
        checks.append(("got a real OpenRouter response back (HTTP 200, non-empty body)",
                       ran and rec is not None and rec.status == 200 and rec.response_len > 0))
        checks.append(("response is UNTRUSTED-tagged (adversary-influenced input, treated as data)",
                       "UNTRUSTED" in out))
        # Body-free audit for an autonomous emission (no body preview retained).
        checks.append(("autonomous emission is body-free (no request_body_preview retained)",
                       rec is not None and rec.request_body_preview == ""
                       and rec.request_body_len == len(BODY.encode("utf-8"))))
        # The credential was NEVER echoed into any audit/returned field.
        blob = " ".join([out, dec.reason, str(rec.__dict__ if rec else ""), dec.summary()])
        checks.append(("host credential never appears in any returned/audit field",
                       KEY not in blob and "dogfood-caps-key" not in blob))

        if ran:
            try:
                cost = json.loads(out.split("»", 1)[-1]).get("usage", {}).get("cost")
            except Exception:  # noqa: BLE001
                cost = None

        # (2) F1 against the REAL host — a MODEL-emitted net_post (no keyword leash) stays HELD,
        #     nothing leaves, even with the full grant + live credential.
        d_model = govern_action(s, ToolIntent("net_post", {"url": URL, "body": BODY}, "structured"))
        checks.append(("model-emitted net_post to the auto host stays HELD (F1) — nothing emitted",
                       d_model.status == HELD and d_model.leash == "propose_first"))

        # (3) Missing the second signal (net_post leash-cap) → never emits, loud reason (require both).
        signed_partial = mint((f"net.post:{HOST}", f"net.post.auto:{HOST}"), {},  # no leash-cap
                              "operator", workspace_subject(tmp), CAPS_KEY)
        s_partial = Session(workspace=tmp, policy_caps=signed_partial, caps_key=CAPS_KEY,
                            egress_credentials={HOST: f"Bearer {KEY}"})
        d_partial = emit(s_partial, URL, BODY, autonomous=True)
        checks.append(("emit with the auto cap but NO net_post leash-cap does NOT emit (require both)",
                       d_partial.status == NOTIFIED and "requires BOTH" in d_partial.reason))

    npass = sum(1 for _, ok in checks if ok)
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print(f"\nemit() OpenRouter dogfood: {npass}/{len(checks)} PASS")
    print(f"API cost (this dogfood): {('$%.4f' % cost) if isinstance(cost, (int, float)) else 'n/a'}")
    raise SystemExit(0 if npass == len(checks) else 1)


if __name__ == "__main__":
    main()
