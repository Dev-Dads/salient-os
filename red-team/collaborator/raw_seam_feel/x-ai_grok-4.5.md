# Seam feel code panel: x-ai/grok-4.5

_cost=$0.1556 finish=stop_

# Adversarial Review — Feel PR (six changes)

Attack posture: author is wrong until the shipped code proves otherwise. Prior door/Host certified; only regressions and claim breaks from this diff.

---

## Findings

### F1 / Resume clobbers global conversation after an intervening submit
| | |
|---|---|
| **SEVERITY** | **MEDIUM** |
| **LOCATION** | `collaborator/host.py:407-414` (`_absorb_result` always assigns `self._history`); `collaborator/host.py:348-386` (`_handle_resume` resumes from `task.history`, then absorb overwrites global) |

**CONCRETE SEQUENCE**
1. `submit("Remember my name is Bjorn")` → runs → `DONE`. `self._history = H1`, `taskA.history = H1` (same list ref).
2. `submit("run rm -rf …")` → `HELD` (`propose_first`). `_absorb_result` sets `self._history = H2`, `taskB.history = H2`.
3. `submit("What is my name?")` → `_handle_turn` uses `self._history=H2` → answers (maybe with Bjorn) → `self._history = H3`.
4. Operator **Approves** task B → `_handle_resume` loads `history = taskB.history` (**H2**, pre-step-3) → `run_turn(..., history=H2)` → `_absorb_result` sets **`self._history = H4` rooted at H2**.
5. Next submit no longer carries the step-3 exchange; depending on what the resume appended, name memory can be diluted or disordered. Worker is serial so there is no race — this is deterministic lifecycle clobber.

**CLAIM** — Weakens **C3** (history does not thread “correctly and safely” across HELD + later submit + resume). Does **not** touch C1: resume note is host-authored; every tool call still hits `govern_action`.

**OTHER LAYER?** No. Surface/task rows still show each task’s own reply; only the Host’s cross-turn memory is wrong.

**FIX** — Pick one:
- Global linear log: on resume, continue from `self._history` (not a stale fork), and treat held approval as a new user/tool-result append on the tip; or
- Pause new `_TurnJob`s while any task is `AWAITING_APPROVAL`/`PAUSED`; or
- Keep per-task histories only and stop using one `self._history` as “the conversation” when multiple non-terminal tasks exist.

Also avoid `task.history = self._history = result.history` sharing one list object; store a copy.

---

### F2 / Unbounded `self._history` growth (context / memory DoS)
| | |
|---|---|
| **SEVERITY** | **LOW** (v0 operational; not P-01) |
| **LOCATION** | `collaborator/host.py:160-166`, `407-414`; `collaborator/loop.py:264+` (append-only) |

**CONCRETE SEQUENCE**  
Long single session: every submit/resume appends user/assistant/tool-result messages. No trim. Model context eventually overflows or slows; Host RSS grows; `/state` task replies already capped, but **in-process history is not**.

**CLAIM** — C3 explicitly asks; this is a real cost/correctness pressure, acceptable as a **stated v0 single-session limit** only if documented. Not an authority break.

**OTHER LAYER?** Model/client token limits fail the turn (`EMPTY` / errors) → honest `FAILED`, but UX dies without a bound.

**FIX** — Cap turns or tokens (e.g. keep system + last N messages / summarized tail); never drop `history[0]` system re-assert path.

---

### F3 / `_fail_active` does not advance memory (nit)
| | |
|---|---|
| **SEVERITY** | **LOW** |
| **LOCATION** | `collaborator/host.py:430-438` vs `407-414` |

**CONCRETE SEQUENCE**  
Worker exception inside `run_turn` after the user message was only on the copied local list → task `FAILED` with `error`, but `self._history` unchanged → next turn does not see the failed user text (UI shows the failed task; model forgets it).

**CLAIM** — Mild C3 consistency gap (FAILED path), not authority.

**OTHER LAYER?** UI still shows the failed task prompt/reply fields if set; model side diverges.

**FIX** — On fail, append a host-authored failure turn to `self._history`, or deliberately document “exception → memory rolled back.”

---

### F4 / `target` may surface credential-shaped command/URL substrings (acceptable under threat model)
| | |
|---|---|
| **SEVERITY** | **LOW** (non-goal / accepted residual) |
| **LOCATION** | `collaborator/view.py:30-45`, `128-136`; render `surface.py` `decisionLi` / `proposalLi` |

**CONCRETE SEQUENCE**  
Held/ran `run_command` with `echo $SECRET` or URL with query token → `_action_target` copies up to 200 chars into snapshot → textContent on auth-walled `/state`.

**CLAIM** — Does **not** break C4 under stated model (operator-only, auth-walled, no new egress, not raw write `content`). Not a silent governance lie.

**OTHER LAYER?** Cookie/CSRF/Host-pin (pre-certified door). Compromised session out of scope.

**FIX** (optional hardening) — Redact URL userinfo/query; prefer argv[0] only for commands; never add `content` (already omitted — good).

---

### Non-findings (attacks blocked — valuable)

| Attack | Result |
|---|---|
| Hostile workspace **filename** → leash/cap/emission | **Blocked.** Name is fenced + `_neutralize`’d proposer DATA only; `govern_action` still sole gate (`propose.py` workspace block; seam unchanged). |
| Hostile model reply echoed in `self._history` next turn → grant power | **Blocked.** History is model context; intents still `parse_message` → `govern_action`. No path into `leash=`, caps, `emit()`, or `source="host"`. |
| Crafted `target` string → authority | **Blocked.** Built only in `JudgmentView._decision/_proposal` for snapshot; never read by Host controls or governance. |
| Clock string → authority | **Blocked.** Concatenated into system prompt grounding only (`loop.py` `_now_line` + re-assert). |
| XSS via reply / held / prompt / target / echo | **Blocked.** `el()` uses `textContent`; `turn`/`salContent` use `createTextNode` / textContent; no `innerHTML` / `insertAdjacentHTML` / dynamic handler strings. `bubbleClass` ∈ {`hold`,`fail`} fixed. `style.background` only from `BADGE` map. |
| `history[0]` drop/duplicate of system grounding | **Blocked.** `run_turn` always builds fresh `sys_msg` and replaces slot 0 if `role==system`, else `insert(0,…)`. Model cannot emit `role=system`. |
| Cross-principal history | **Blocked** by single Host + serial worker (unchanged invariant). |
| Feel change opens egress | **Blocked.** No new network API; grounding is local `Path.iterdir` names; clock is local time. |
| Raw write **content** in `target` | **Blocked** by key allowlist (`path`/`url`/`dest`/`target`/`command` only). |

---

## Claim certification

| Claim | Verdict | One sentence |
|---|---|---|
| **C1** (P-01) | **CERTIFIED** | Threaded history, workspace filenames, `target`, clock, and larger view clips only affect what the model knows or the page shows; `govern_action` remains the sole, unchanged authority boundary with no data→leash/cap/source/emission path. |
| **C2** (XSS / render) | **CERTIFIED** | Every new conversation and `target` path sinks untrusted strings through `textContent` / `createTextNode` only; no HTML/JS sink or attribute injection from snapshot/model data. |
| **C3** (memory soundness) | **NOT-CERTIFIED** | Serial confinement and `history[0]` re-assert hold, but HELD + intervening submit + approve **clobbers** `self._history` from a stale `task.history` fork (F1), and growth is unbounded (F2). |
| **C4** (context surfacing) | **CERTIFIED** | Grounding/`target`/clock add no egress and no raw write-body surfacing; operator-only `/state` may show credential-shaped args (pre-existing class of preview data) without misrepresenting governance status. |

---

## STEELMAN

The six changes correctly treat “feel” as **knowledge + presentation**: conversation memory and clock deepen grounding, workspace listing reduces phantom proposes, `target` and longer replies make the watch surface legible, and the UI rewrite keeps the hard textContent discipline. Authority remains a different door — proposer and doer outputs still die at `govern_action`, and the surface still only `submit`/`snapshot`/allowlisted controls. The real gap is not P-01 but **one global history competing with per-task resume forks**, which is exactly where a “partner memory” abstraction gets subtle under HELD/multi-task.

---

## VERDICT

**MINOR_ISSUES** — P-01 and XSS hold under adversarial trace; certify C1/C2/C4. Do not certify C3 until resume/global-history lifecycle is fixed.

**Single highest-value fix:** On resume, do not rebuild the conversation from a stale `task.history` tip that can rewind past newer turns — either serialize “one open turn at a time” or always append approval/tool-results onto the monotonic `self._history` tip (and copy lists so task/global history never alias).
