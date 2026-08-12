# maintfetch CODE panel: x-ai/grok-4.5

_finish=stop seconds=78.6 cost=$0.0925 usage={'prompt_tokens': 34741, 'completion_tokens': 3869, 'total_tokens': 38610, 'cost': 0.0924784, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0924784, 'upstream_inference_prompt_cost': 0.0692644, 'upstream_inference_completions_cost': 0.023214}, 'completion_tokens_details': {'reasoning_tokens': 38, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial Code Review: `maint_fetch` (ADR 0006)

## Findings

### F1 / EXACT-CAP ARTIFACT ACCEPTED, OVER-CAP PARTIAL LEFT ON DISK UNTIL CALLER UNLINKS / LOW–MEDIUM (design-correct; residual on unlink)
**FILE:** `egress.py:fetch_to_file` (cap loop); `tools.py:_exec_maint_fetch` + `_unlink_quiet`

**CONCRETE:**
- Cap check is `if total + len(chunk) > max_bytes` **before** write. Exact-cap (`total + len == max_bytes`) is accepted; over-cap refuses **without** writing the offending chunk. Not off-by-one for “never exceed.”
- On over-cap mid-stream, prior chunks **are** already in the sink. `fetch_to_file` returns non-ok with `response_hash=None`, `response_len=0` (via `_refused`). Executor then `_unlink_quiet(target)` after the `with open(...)` exits — file is closed before unlink on all platforms, including Windows. Tests cover delete-on-non-ok.
- Non-2xx: body drained bounded, never written to sink. Redirect: no body write. Good.
- Empty chunk terminates the loop (`if not chunk: break`). Zero-length spin is not a hang; a stuck socket is bounded by `timeout` on the connection. No infinite empty-read loop against a well-behaved `http.client` response.
- `sink.write` `OSError` is caught by `except (ssl.SSLError, OSError, http.client.HTTPException)` → non-ok; executor unlinks. Good.
- **Hash/len on success** reflect only bytes written (`total` / `h` updated only on the write path). On failure, record does **not** claim written bytes (refused zeros them). Correct for “don’t advertise a partial as complete.”

**Attack “stage over-cap as complete?”** No: `rec.ok` is False; executor deletes; tool result `ok=False`; no `write_set` / success output.

**Residual (not a logic bug):** `_unlink_quiet` swallows `OSError`. If unlink fails (permissions, busy file on a non-POSIX FS edge case, mandatory lock), a partial can remain on disk while the tool reports failure. Another control: human-gated + failure status; no success path. Not “staged as complete,” but “partial may linger.”

**ANOTHER CONTROL:** governance HELD + non-ok execution path; seal doesn’t care about bytes.
**FIX (hardening):** on unlink failure, surface a distinct error / escalate; optionally `os.replace` via temp file then rename only on full success (atomic stage). Not required for the stated ceiling contract if unlink is reliable on the deployment FS.

---

### F2 / OVER-CAP CHECK ALLOWS EXACT max_bytes; NO WRITE OF OVERSIZED CHUNK / NONE (sound)
**FILE:** `egress.py:fetch_to_file`

`>=` vs `>`: using `>` is correct for a ceiling of `max_bytes` inclusive. An artifact of exactly `max_bytes` is complete and ok. An artifact of `max_bytes+1` fails closed. **No defect.**

---

### F3 / net.maint NAMESPACE SEPARATION + SINGLE DERIVATION / NONE (sound)
**FILE:** `egress.py:required_capability`; tool `egress_method="MAINT"`

**CONCRETE:**
- `GET`/`HEAD` → `net.get:`; `POST` → `net.post:`; `MAINT` → `net.maint:`; else `None`.
- Prefixes are distinct strings. A grant of `net.get:H` or `net.post:H` cannot satisfy `net.maint:H` (tests: `test_read_grant_does_not_confer_maint`).
- `canonical_host(url)` is the sole host derivation for the cap key; `fetch_to_file` uses the same `canonical_host` for connect/SNI. authorize == connect.
- One function `required_capability` — hold and approve both call it (per CONTEXT + tests `test_host_removed_between_hold_and_approve_denies`). No second parser at approve-time in the shipped tool path.
- Ineligible URL → `host is None` → `None` → DENY, never bare `net.maint:`.
- `"MAINT"` is not sent on the wire (`putrequest("GET", ...)`). It only selects the cap kind via `tool.egress_method`. No collision with real HTTP method paths in this module; unknown methods return `None` (fail closed), not GET.

**ANOTHER CONTROL:** N/A — primary control is sound.
**FIX:** none.

---

### F4 / SEAL (url, dest) approved==executed / NONE (sound) with one note
**FILE:** `tools.py:SEALED_TOOLS`, `held_action_seal`, `freeze_args`; CONTEXT governance hold ~577 / approve ~217

**CONCRETE:**
- `maint_fetch ∈ SEALED_TOOLS`. Seal branch `b"M"` + url + dest; tool name is first length-prefixed field → Decision.tool rebind cannot replay write_file/run_command seal as maint_fetch (and vice versa). Tested.
- Length-prefixed 8-byte BE framing → injective; url/dest cannot steal each other’s bytes.
- `freeze_args` coerces `url` and `dest` with `str(... or "")` at hold, matching executor `str(args.get(...) or "")`. Drifting `__str__` pinned once.
- `mutating=False` → not emission_seal path; held_action_seal covers it. net_post still uses emission_seal. No gap where this egress tool skips both: it is explicitly in SEALED_TOOLS.
- web_fetch is unsealed (read into model context only) — out of scope; not a maint_fetch gap.

**Note (not a maint_fetch bug):** CONTEXT says hold mints held_action_seal on the else branch when not (egress & mutating). Full `governance.py` / `loop.approve` bodies are only partially in the diff; **tests** `test_seal_mismatch_after_hold_is_denied` and grant-at-approve exercise the real seam. On the evidence of tests + SEALED_TOOLS single source, seal mint/verify is wired. I do not invent a gap without contradicting code.

**ANOTHER CONTROL:** capability re-check at approve.
**FIX:** none for seal framing.

---

### F5 / WORKSPACE FENCE ON dest — SYMLINK FOLLOW ON OPEN / MEDIUM
**FILE:** `tools.py:resolve_in_workspace`, `_exec_maint_fetch`

**CONCRETE:**
```python
target = resolve_in_workspace(workspace, rel)  # resolve() — final path must stay under root
os.makedirs(os.path.dirname(str(target)) or str(workspace), exist_ok=True)
with open(target, "wb") as sink:
```

- `../`, absolute-as-escape, and resolving outside root → `WorkspaceError` (not swallowed in executor; CONTEXT: gate → DENY). Tested `../escape.deb`.
- `Path.resolve()` resolves symlinks. If `dest` is `link_out` and that symlink currently points outside the workspace, `resolve_in_workspace` raises — **escape via pre-existing outbound symlink as the dest path itself is denied.**
- Parent mkdir uses `os.path.dirname(str(target))` where `target` is already the resolved in-workspace path. Parents are under the fence. `or str(workspace)` handles dest at workspace root. Good.
- **Overwrite / last-component symlink (classic TOCTOU):** On platforms where `open(path, "wb")` follows a symlink:
  1. Attacker/prior step places **in-workspace** symlink `drivers/d.deb` → `/etc/something` or path outside workspace.
  2. `resolve_in_workspace(workspace, "drivers/d.deb")`: if the symlink is dangling, behavior depends on `strict` (default `strict=False` in modern pathlib — dangling symlink often resolves to the out-of-workspace target path string). **If the resolved target is outside root → WorkspaceError.** If the symlink points **inside** workspace, open overwrites the in-fence target (intended).
  3. **Race:** between `resolve_in_workspace` and `open`, replace an ordinary file with a symlink pointing out. Then `open("wb")` may follow the new symlink and write **outside** the workspace while the check already passed on the old path.

This is the standard resolve-then-open TOCTOU. `write_file` has a similar pattern (resolve + child write). For maint_fetch the write is large and host-capped but still a mediated byte pipe to whatever `open` follows.

**Does another control catch it?**
- Human sees `dest` in preview and seal binds dest string (not inode). Human-gated reduces likelihood; does **not** close the race or a pre-planted same-path swap after approval before execute.
- No `O_NOFOLLOW` / openat without follow on the final component.
- Workspace disjoint-from-code helps for code roots, not general host paths.

**Severity:** MEDIUM in a threat model with concurrent workspace writers or a malicious prior artifact in-workspace; LOW if single-threaded human-gated sessions with empty/trusted workspace. Real defect class, residual shared with other fenced writers.

**FIX:** open with `O_NOFOLLOW` (or equivalent) on the final component; write to a temp file created with `O_EXCL` inside the fenced dir, fsync, then `rename` over dest; reject dest if any path component is a symlink (lstat walk). Same hardening worth applying to `write_file` for consistency.

---

### F6 / mkdir parent — `os.path.dirname` on Windows roots / LOW
**FILE:** `tools.py:_exec_maint_fetch`

`os.makedirs(os.path.dirname(str(target)) or str(workspace), exist_ok=True)`.

For a normal fenced file path this stays inside. Edge: if `dirname` were empty, falls back to workspace — correct. No `mkdir` of an escaped path because `target` is pre-fenced. **No escape via mkdir.** Minor style risk only if `resolve` ever returned a non-strict oddity; not a demonstrated bug.

---

### F7 / max_bytes HOST-ONLY + LEASH / NONE (sound)
**FILE:** `session.py:Session.__init__`; `governance.py:execute_and_verify` threads `maint_max`; `execute_tool` → `_exec_maint_fetch`; Tool definition

**CONCRETE:**
- `maint_fetch_max_bytes`: None→default; bool rejected; non-int rejected; `<=0` rejected. Good (bool-is-int footgun closed).
- Model args never include max_bytes; only seam-threaded `maint_max_bytes`.
- `default_leash=PROPOSE_FIRST`; no `net.maint.auto` prefix anywhere in egress constants (only `net.post.auto:`). Tests: granted → HELD.
- `mutating=False`: skips emission quota / credential injection / net.post.auto lift paths that key off mutating. Correct: maint_fetch must not get POST credentials or emission auto-lift.
- **Does mutating=False wrongly skip a needed control?** Emission-seal skipped — compensated by held_action_seal. Artifact verify_mode is `egress_log` not full artifact re-hash from disk independent of record — consistent with ADR (channel integrity + hash in record). Human gate is the primary control. **Not a wrong skip** for v0.

**Caveat (LOW residual):**  
`maint_max = getattr(session, "maint_fetch_max_bytes", None) or egress.DEFAULT_MAINT_MAX_BYTES`  
If a broken session ever set `maint_fetch_max_bytes = 0`, `or` would fall back to default (safe). Session ctor prevents 0. If someone set a huge int, that’s operator config. Fine.

**FIX:** none required. Optional: use explicit `is None` instead of `or` for clarity.

---

### F8 / REUSED CONTRACT drift in fetch_to_file / LOW (mostly faithful; one intentional delta)
**FILE:** `egress.py:fetch` vs `fetch_to_file`

**Mirrored correctly:**
- `canonical_host`, target bounds, `_is_clean_request_target`, resolve-once, `is_safe_public_ip`, `_PinnedHTTPSConnection`, HTTPS-only, GET, fixed headers (no model Auth/Cookie), redirect fail-closed, never-raises on junk/resolve/TLS (`except` → `_refused`).

**Intentional deltas (not bugs):**
- Non-2xx: `fetch` may return body with `ok=False`; `fetch_to_file` drains and **does not stage** — stricter, correct for artifacts.
- Streaming cap vs `max_response+1` truncation for fetch.

**Minor residual:**
- Duplicated request setup (copy-paste twin of `fetch`) — future drift risk, not a current defect.
- `connection_factory` / `resolver` injectables same as fetch — test surface only.
- On over-cap return inside `try`, `finally: conn.close()` still runs. Good.
- **Exact-cap / hash:** only written bytes hashed on success. Good.

**Never-raises:** junk URLs return non-ok; no raise across boundary. Executor catches `OSError` on stage. `WorkspaceError` propagates for fence (by design → DENY). Good.

**FIX:** optional extract shared “begin GET” helper to prevent future drift.

---

### F9 / SUCCESS PATH REPORTS MODEL-VISIBLE PATH AS rel NOT resolved path / INFO
**FILE:** `_exec_maint_fetch` output uses `rel` and `write_set=(rel,)`, `artifact_hashes={rel: rec.response_hash}`.

Hash is from streamed bytes (egress record), not a post-close re-read of the file. A replace-after-write TOCTOU could desync disk from claimed hash — same class as other tools; human-gated. Documented residual territory (no independent artifact verifier for maint). **Not CRITICAL.**

---

### F10 / GOVERNANCE DIFF INCOMPLETE IN MATERIAL BUT TESTS BIND BEHAVIOR / NOT A CODE DEFECT
Hold preview adds `dest`; execute threads `maint_max_bytes`. Full `required_capability(..., "MAINT")` at hold/approve is asserted by seam tests (deny without cap, read grant insufficient, revoke between hold/approve). **Treat as verified by tests**, not as an unaudited hole.

---

## Attack-surface checklist (requested 1–6)

| # | Question | Verdict |
|---|----------|---------|
| 1 | Over-cap staged as complete? Off-by-one? Partial delete? Non-2xx staged? Hang? write OSError? hash=written? | **No complete over-cap stage.** Cap `>` correct. Partial deleted after close; unlink best-effort residual. Non-2xx not staged. No empty-read hang. write OSError → non-ok + unlink. Success hash/len = written only. |
| 2 | net.maint separate? same host? one derivation? None deny? MAINT collide? | **Sound.** |
| 3 | Seal url+dest? both seals gap? rebind? freeze? injective? | **Sound** for maint_fetch. |
| 4 | dest fence ../ abs symlink mkdir open follow? | **../ and abs caught.** Final-component symlink TOCTOU / open-follow is the real residual (**F5**). |
| 5 | max_bytes host-only? validation? HELD no auto? mutating=False correct? | **Sound.** |
| 6 | fetch contract mirrored? never-raises? | **Faithful**; non-2xx stricter; never-raises held. |

---

## STEELMAN

The design correctly refuses a privileged CONNECT proxy and reuses egress Tier-1 end-to-end: separate `net.maint:` authority, single `canonical_host`/`required_capability` site, streaming ceiling with fail-closed non-ok + executor unlink, host-only `max_bytes`, PROPOSE_FIRST with no auto-lift, and a proper `held_action_seal`/`freeze_args` binding on `(url, dest)` so maint is not left as the one egress tool with neither emission nor held seal. Tests are adversarial where it matters (namespace, over-cap, redirect, non-2xx, seal swap, revoke-at-approve). Remaining issues are shared workspace open-follow TOCTOU and best-effort unlink — not a broken ceiling or authority story.

---

## VERDICT: **MINOR_ISSUES**

The shipped maint_fetch contract is **sound** on authority, seal, streaming fail-closed ceiling, host leash/cap, and transport reuse; the highest-value change is to **stage via `O_NOFOLLOW`/`O_EXCL` temp + atomic rename (and fail hard if final unlink of a refused partial fails)** so a workspace symlink race cannot turn a human-gated maint fetch into an out-of-fence write and a failed over-cap cannot leave bytes behind silently.
