# ② Stage C — controls (POST /control) external code panel: disposition

5-vendor OpenRouter panel, $1.0992. The door + Host controls were already certified (Stage A C4,
Stage B); this panel hammered the ONE new claim — a control over HTTP can only RESTRICT or EXPRESS
host config, never GRANT/widen authority (P-01).

## Certifications

| Claim | Result | Notes |
|-------|--------|-------|
| **C1 — no authority via a control** | **CERTIFIED 4/4** (substantive) | Every substantive reporter verified: the fixed `_CONTROLS` table means request data never becomes a method name (`getattr` uses a table-supplied method name), there is no grant/mint method on the Host, and `set_leash` is re-capped by `apply_cap` at `govern_action` (governance.py ~342) so a page leash can't loosen past the signed cap; approve/approve_proposal re-gate at run time. |
| **C2 — door unchanged** | **CERTIFIED 4/4** | `/control` uses the identical `_guard_authed(check_origin=True)` as `/submit`; the shared `_read_json_body` refactor preserved every Stage-B door invariant (bootstrap→cookie, CSRF header, Host/Origin pins, body cap, timeouts). |
| **C3 — dispatch integrity / fail-safe** | **CERTIFIED 3/4** (opus MINOR nit) | Unknown action / bad-missing-oversized-non-string arg → 400 with the Host untouched; a Host rejection → `{"ok":false}`; no crash/partial call. |
| **C4 — scope honesty** | **CERTIFIED 4/4** | Page exposes only the 8 allowlisted controls, each wired 1:1 to its Host method; held/paused tasks → approve/decline, proposals → approve_proposal/veto. |

Verdicts: **SOUND ×3** (gpt-5.1, grok, qwen), **MINOR_ISSUES ×1** (opus), **gemini empty** ($0,
truncated as on prior panels — recorded as errored, no verdict). No CRITICAL/HIGH authority finding.

## The one finding — accepted as a safe, documented non-issue

**opus C3 (LOW): extra/unknown keys in the control body are ignored, not rejected.** opus itself
notes it "causes no harm." Ignoring unknown fields is standard, safe API behavior: `_route_control`
reads ONLY `action` plus the fixed required keys from `_CONTROLS`; any extra key is inert and can
never reach the Host or an authority field. No code change — this is by design (forward-compatible,
and the security boundary is the fixed allowlist, not the shape of the body).

## Confirmations (not findings)

- gpt-5.1 + qwen explicitly noted the leash cap is applied at BOTH `govern_action` and
  `execute_and_verify` (double apply_cap) — the "loosen past the cap" attack is closed at every use
  site, not just once.
- grok: "request data never becomes a method name, args are typed/length-capped strings, and the
  only 'loosen' knob is explicitly ceilinged again at every govern/execute/re-gate point."
- gpt-5.1's suggested "highest-value fix" (add explicit /control body-cap + Upgrade + Origin tests
  mirroring /submit) — the shared guard + body reader already cover these; the control tests pin the
  Origin/CSRF/cookie/405 behavior. Optional extra coverage, not a defect.

Full suite green (37 surface tests incl. 9 Stage-C control tests; 858 total, lone pre-existing
Windows-only netns quirk).
