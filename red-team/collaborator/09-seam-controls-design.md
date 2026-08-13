# ② Stage C — "the hand on the leash" — technical design spec

*Answerable to `docs/seam-surface-plain-language.md` (Step C). Builds on Stage B
(`surface.py`) and the Stage-A Host controls, both externally certified.*

## The proof this stage must produce

Steer a **running** job entirely from the page — pause it, approve/veto a step it's
holding, tighten a leash — **without typing a sentence.** The controls that were
*shown as state* in Stage B become *buttons you press*.

## What's new (small — it reuses two certified things)

1. **The door is unchanged.** Every control is a `POST /control` behind the *same*
   `_guard_authed(check_origin=True)` (session cookie + `X-Sal-Token` + Host/Origin pins,
   the 5 s timeout, the 64 KiB body cap) that already certified for `/submit`.
2. **The Host controls already exist and are already certified** (Stage A panel C4:
   "controls only restrict or express host config; none grants a capability"). Stage C only
   *exposes* them; it invents no new authority.

So the entire new surface is one route + a fixed dispatch table + buttons.

## The one rule (P-01), re-checked for controls

A control may only **restrict** (pause, decline, veto, tighten) or **express host config**
(a leash, the trust dial) — never **grant**. Verified in the code, not assumed:

- `set_leash` writes `session.leash_overrides[tool]`, but `govern_action`
  (`governance.py:341-344`) applies the override **then** `apply_cap(leash,
  leash_cap(session, tool))` — the **signed grant is the hard ceiling**; a host/view leash
  can tighten within it or loosen only *up to* it, never past it. A `proposed`-source
  action with a loosened leash is still forced to `propose_first` (`governance.py:351-353`).
  So exposing "loosen a leash" over HTTP cannot raise what Sal may autonomously do beyond
  the signed cap.
- `set_proactivity` only changes how often Sal *suggests* (surfacing), never what it may do.
- `approve` / `approve_proposal` **re-gate the capability at run time** on the worker; they
  execute an already-permitted action, they don't grant one.
- `pause` / `resume` / `decline` / `veto` are pure restrict/state-flips.

There is **no `grant`/`mint` method on the Host at all**, so the dispatch table below
*cannot* map to one.

## `POST /control` — one route, a fixed allowlist

Body: `{"action": <name>, ...args}`. Dispatched through a fixed table — `action` →
`(host_method_name, required_str_arg_keys)`. Anything not in the table → `400`. Every arg
is a string, length-capped (≤256). The host method's `bool`/`None` result → `{"ok": bool}`
(`None` from `pause`/`resume` → `ok:true`). A rejected control (unknown tool, invalid
leash/level, task not awaiting, proposal already gone) → the host returns `False` →
`{"ok":false}` — the page just shows nothing changed. Fail-safe by construction.

| action              | host method                       | args                    | kind             |
|---------------------|-----------------------------------|-------------------------|------------------|
| `pause`             | `host.pause()`                    | —                       | restrict         |
| `resume`            | `host.resume()`                   | —                       | restrict (undo)  |
| `set_proactivity`   | `host.set_proactivity(level)`     | `level`                 | express config   |
| `set_leash`         | `host.set_leash(tool, leash)`     | `tool`, `leash`         | express (capped) |
| `approve`           | `host.approve(task_id)`           | `task_id`               | governed approve |
| `decline`           | `host.decline(task_id)`           | `task_id`               | restrict         |
| `approve_proposal`  | `host.approve_proposal(prop_id)`  | `proposal_id`           | governed approve |
| `veto`              | `host.veto(prop_id)`              | `proposal_id`           | restrict         |

Valid `level` ∈ {off, conservative, eager} (Host rejects others). Valid `leash` ∈
{act_then_report, propose_first, notify_only} (Host rejects others).

## The page — buttons where the state allows

Reuses the Stage-B page (same poll loop, same `textContent`-only render). Adds:

- **Header:** a Pause / Resume toggle (reflects `snapshot.paused`).
- **Trust dial:** the proactivity value becomes a `<select>` (off / conservative / eager)
  → `set_proactivity`.
- **Leashes:** each leash chip gets a `<select>` (the three leashes) → `set_leash(tool, …)`.
- **Tasks:** a task in `awaiting_approval` shows **Approve** / **Decline** buttons →
  `approve` / `decline` (the "Stage C adds the button" copy is now the button).
- **Proposals:** each shows **Approve** / **Veto** → `approve_proposal` / `veto`.

Each button `POST`s `/control` with the `X-Sal-Token` header, then re-polls `/state`. All
snapshot-derived strings still go in via `textContent`; action names + IDs the page sends
are its own, and the server re-validates every one.

## Tests — extend `tests/test_collaborator_surface.py`

- Each of the 8 actions reaches its Host method (fake host records the call + args).
- Unknown `action` → 400; a known action with a missing/oversized/non-string arg → 400,
  host **not** called.
- `POST /control` requires cookie + CSRF + same-origin: 403 without each; cross-origin → 403.
- A Host method returning `False` → `{"ok":false}` (no crash).
- **Structural (P-01):** the dispatch table's values map **only** to the eight known Host
  control methods; assert the surface never calls any Host method outside
  `{submit, snapshot, pause, resume, set_proactivity, set_leash, approve, decline,
  approve_proposal, veto}` — and none of those is a grant/mint.

## Live proof — extend `e2e_sparky_page.py`

On Sparky: submit a job whose write is on a `propose_first` leash so it **holds**, then
`POST /control {approve}` and watch it resume to DONE via `/state`; and `POST /control
{pause}` / `{resume}` flips `snapshot.paused` live — steering a real run entirely over the
control route.

## Review calibration

Stage C reuses the **already code-certified door** and the **already-certified Host
controls**, adding one route + a fixed allowlist + buttons. Per "match spend to risk governs
*depth*": **skip the pre-build design panel** (there is no new door and no new authority to
design), **run the mandatory external CODE panel** on the shipped route + dispatch (P-01 is
the claim to hammer). Internal red-team first as always.

## Explicitly deferred

- SSE/websocket push instead of polling — a ③ nicety.
- Any richer per-tool policy UI — Stage B/C keep the three-leash model.
