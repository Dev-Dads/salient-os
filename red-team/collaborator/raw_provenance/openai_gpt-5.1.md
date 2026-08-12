# provenance code panel: openai/gpt-5.1

_finish=stop seconds=37.3 cost=$0.0720 usage={'prompt_tokens': 34272, 'completion_tokens': 2915, 'total_tokens': 37187, 'cost': 0.07199, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.07199, 'upstream_inference_prompt_cost': 0.04284, 'upstream_inference_completions_cost': 0.02915}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID 1 / TITLE: Autonomous contained run can write unrecorded files via snapshot failure / SEVERITY: MEDIUM  
LOCATION: collaborator/governance.py:~640-660 (execute_and_verify run_command exit branch; `_pre` / `_post` + `_record_autonomous_authorship`)  

CONCRETE CASE  
- An autonomous run_command executes on the contained path (require_code_protection=True), writes a new file `drop.sh` in the workspace, but `snapshot_tree(session.workspace)` or `observed_write_set(_pre, _post)` fails or misses it.  
- Example:  
  1) `_pre = snapshot_tree(session.workspace)` succeeds.  
  2) `execute_tool(... require_code_protection=True)` runs and `wrap_contained` drops `drop.sh`.  
  3) In the recording block, `snapshot_tree(session.workspace)` raises (e.g. transient filesystem error, race with workspace deletion) or `observed_write_set(_pre, _post)` raises.  
  4) The except-swallow (`except Exception: pass`) means `_record_autonomous_authorship` is never called.  
  5) Later a human-held `run_command` with `command="sh drop.sh"` has `_references_autonomous` see an empty `_autonomous_authored` set and returns `""`, so there is no `provenance_touch` in the HELD preview.  

WHY IT FAILS ITS OWN GOAL / INVARIANT  
- The module contract says: “This set records the workspace-relative posix paths an autonomous action AUTHORED; … the seam surfaces … so the provenance is VISIBLE at the moment of the hand” and the tests pin “contained run_command recording” as part of the guarantee.  
- Here there is a legitimate autonomous contained run that drops a runnable workspace file, but any snapshot failure or bug in `snapshot_tree`/`observed_write_set` silently degrades to “no recording at all.” That is worse than the documented, token-level POROUS recognizer; it loses provenance for the entire run rather than just for paths reached by non-literal forms.  
- The control promises TOTAL-ness in the recognizer but does not acknowledge that the *recording* is best-effort and may wholly fail. That makes the recall boundary less honest than documented for the contained path.  

WHETHER ANOTHER CONTROL CATCHES IT  
- No other provenance or authorship mechanism will mark these files:  
  - `execution.write_set` is empty for run_command (by design) so the artifact branch never sees this path.  
  - The F2 manifest is the sole source of “autonomous contained run authored this file.”  
- The human run still executes under full, uncontained reach; only the advisory flag is missing. No other tag (code_touch, secret_touch, etc.) substitutes for provenance.  

FIX  
- Make the “no recording” failure mode explicit and narrower:  
  - Record *partial* results whenever possible, and surface when the snapshot diff failed, so the operator can treat the provenance as incomplete rather than empty.  
  - For example:  
    ```python
    _authored_now = []
    if _pre is not None:
        try:
            _post = snapshot_tree(session.workspace)
            changed = observed_write_set(_pre, _post)
        except Exception:
            changed = None
        if changed is not None:
            _authored_now = [...]
            _record_autonomous_authorship(session, _authored_now)
        else:
            # Optional: set a Decision flag like provenance_incomplete=True
    ```  
- And document in the module docstring that for contained runs provenance is “best-effort and may be absent if workspace snapshotting fails,” aligning the stated goal with the actual failure mode.  

---

ID 2 / TITLE: Malicious Session can suppress or forge autonomous-authorship state / SEVERITY: LOW  
LOCATION: collaborator/session.py:~145-184 (`_autonomous_authored`, `note_autonomous_authorship`, `clear_autonomous_authorship`); collaborator/governance.py:~350-380 (`_record_autonomous_authorship`, `_clear_autonomous_authorship`, `_references_autonomous`)  

CONCRETE CASE  
- A hostile or buggy host mutates the session object between actions, e.g.:  
  ```python
  s = Session(workspace=tmp)
  s._autonomous_authored = "not-a-set"
  ```  
- Later, `_references_autonomous(session, command)` runs:  
  ```python
  authored = getattr(session, "_autonomous_authored", None)
  if not authored:
      return ""
  ```  
  Since `"not-a-set"` is truthy, `authored` is the string; then `provenance.references_autonomous_file` does `set(authored or ())` which yields a set of single-character paths and effectively destroys any previous legitimate manifest.  
- Conversely, the host can replace `note_autonomous_authorship` with a stub that raises, and `_record_autonomous_authorship` will silently `pass`, resulting in all future autonomous writes being unrecorded.  

WHY IT FAILS ITS OWN GOAL / INVARIANT  
- The docstring for Session: “Everything here is AUTHORITY or configuration, set by you (the host), never chosen by salience or the model,” but the provenance invariants implicitly assume `_autonomous_authored` behaves like a set of normalized rel-paths and that note/clear succeed.  
- While a malicious host is out of scope, the *stated* provenance goal is “session-lived set … records workspace-relative posix paths an autonomous action authored.” With the current getattr/try/except patterns, a misconfiguration or innocuous bug in the host code can silently degrade the manifest to junk or no-op, with no signal to the operator that provenance is now absent. That’s worse than the documented porousness (token matching): it silently disables the entire feature.  

WHETHER ANOTHER CONTROL CATCHES IT  
- No. All use sites are getattr-guarded and swallow exceptions to preserve totality, so there is no logging or failure that would reveal the provenance system is now nonfunctional. Other security controls (caps, leashes, code_protected, etc.) do not compensate for missing provenance.  

FIX  
- Harden the manifest shape while keeping totality:  
  - On Session `__init__`, make `_autonomous_authored` a private set and avoid exposing it for reassignment (hosts should use the note/clear APIs).  
  - In `_record_autonomous_authorship` / `_clear_autonomous_authorship`, detect non-set state and self-heal rather than silently pass:  
    ```python
    if not isinstance(getattr(session, "_autonomous_authored", None), set):
        setattr(session, "_autonomous_authored", set())
    ```  
  - Optionally, emit a best-effort audit log when a recovery happens, so a misbehaving host is visible without breaking the totality promise.  

---

ID 3 / TITLE: Autonomous write_file provenance lost if `execution.write_set` under-reports changed paths / SEVERITY: LOW  
LOCATION: collaborator/governance.py:~725-745 (artifact branch `execute_and_verify`, autonomous write_file recording); collaborator/tools.py:~290-340 (`_exec_write`)  

CONCRETE CASE  
- `execute_and_verify` after a successful autonomous write_file does:  
  ```python
  if cleared and getattr(tool, "op", "") == "file.write":
      if human_gated:
          _clear_autonomous_authorship(session, execution.write_set)
      else:
          _record_autonomous_authorship(session, execution.write_set)
  ```  
- `_exec_write` currently sets `write_set=(rel,)`, but imagine a future change or platform quirk where the supervised writer creates an additional in-workspace script or helper file (`rel + ".tmp"` or similar) or writes through a symlink that causes `observed_write_set` to see more real writes than `execution.write_set`.  
- In that case, `observed_write_set` is still the verification boundary, but the provenance manifest only holds `rel`: the additional autonomously-created file is runnable but never recorded, and a later human run against it gets no warning.  

WHY IT FAILS ITS OWN GOAL / INVARIANT  
- The commentary in governance: “an autonomous write_file is already a workspace dropper; … recorded from execution.write_set on a CLEARED (verified) write … so the recorded path is verifiably present.” That implies that autonomous write_file authorship for runnable workspace files is tracked via that path list.  
- If the executor ever diverges (or already does, on some platforms) and produces more in-workspace files than are listed in `execution.write_set`, the provenance story degrades silently: verification still fails on undeclared writes, but provenance no longer reflects all autonomously-authored runnable files. That is a provenance-hole strictly wider than “argv-token porous,” and it is not documented.  

WHETHER ANOTHER CONTROL CATCHES IT  
- The core verifier (`observed_write_set`) will still enforce that undeclared writes cause verification failure; but that only blocks the action from being “cleared.” It does not by itself populate `_autonomous_authored` with the additional paths.  
- Since the provenance manifest is only updated from `execution.write_set`, those extra files never enter the manifest.  

FIX  
- Make the write_file provenance source of truth be the *observed* write-set, not only the executor report, for autonomous runs:  
  - Extend the artifact branch to capture `world` and derive the set of verified writes from there; or  
  - In the write_file case, use `outcome.write_set` (if exposed) or a similar governed outcome that already incorporates `observed_write_set`.  
- At minimum, document that only the declared `path` of a write_file is treated as an authored path for provenance, and that any additional files created as side effects are outside the provenance model, so operators do not over-trust the flag.  

---

ID 4 / TITLE: Manifest poisoning via overly broad clear_autonomous_authorship normalization / SEVERITY: LOW  
LOCATION: collaborator/session.py:~170-184 (`clear_autonomous_authorship`), collaborator/provenance.py:~23-40 (`norm_rel`)  

CONCRETE CASE  
- An autonomous tool legitimately authors `sub/build.sh`, which is recorded as `"sub/build.sh"`.  
- A human, for unrelated reasons, approves a write to `"./sub/../sub/build.sh"` or `"sub/./build.sh"` (legal equivalent paths), which `norm_rel` normalizes to `"sub/build.sh"`.  
- `clear_autonomous_authorship` will discard the taint for `"sub/build.sh"` under the assumption those “bytes are now human-vetted,” but the content could be completely unrelated to the original autonomous drop that triggered concern.  

WHY IT FAILS ITS OWN GOAL / INVARIANT  
- The docs explicitly say: “a HUMAN-approved (human_gated=True) verified write CLEARS the path's taint (those bytes are now human-vetted).” That models clear-ness as “this exact logical path’s current contents were reviewed by a human.”  
- However, with normalization folding many syntactic names into one rel-path, a human-approved write to any alias of that path clears the taint of the original byte sequence, even if the earlier autonomous content was never actually examined and the human’s write was incidental or templated. That is a (small) laundering channel beyond what the ADR text acknowledges.  

WHETHER ANOTHER CONTROL CATCHES IT  
- No; there is no “version” or content hash tie between the original autonomous bytes and the eventual human-vetted bytes.  
- This is likely acceptable by design, but the current description does not call out that *any* human-approved write to the same normalized path clears all prior taint, whether or not it was motivated by reviewing the earlier content.  

FIX  
- Clarify the ADR/inline docs to reflect the actual semantics: “any later human-approved write to the same normalized path clears taint, regardless of whether it was prompted by reviewing the earlier autonomous content.”  
- If tighter semantics are desired, consider only clearing taint when the human-approved write immediately succeeds a HELD preview warning that mentioned that same path (i.e., bind clear to a specific provenance_touch in the approval flow), but that would be a design change and not strictly required for honesty.  

---

STEELMAN (author’s perspective)  
The provenance system is explicitly framed as advisory and porous, with a documented focus on avoiding stale or noisy warnings that would train operators to ignore the flag. The code carefully preserves totality and non-interference with the main governance guarantees: failures in provenance recording or recognition degrade to “no flag” rather than blocking runs or breaking the invariant that govern_action/approve never raise. Given that the autonomous path is already heavily constrained (contained run + signed caps) and all autonomous shell writes are supervised and verified, a best-effort provenance overlay that can occasionally miss a file or lose state under pathological conditions is acceptable as long as it does not misrepresent safety or block maintenance. The tests pin the essential behaviors and clearly distinguish between the model’s porous match predicates and the structural safety boundaries, which remain unchanged.

---

VERDICT: MINOR_ISSUES  
There are no critical model-bypass or totality violations; the main issues are that a few failure modes or edge cases make provenance recall slightly worse or more fragile than the documentation implies. The single highest-value fix is to harden the autonomous contained-run recording path so that snapshot/diff failures cannot silently erase all provenance for that run (e.g., partial recording plus an explicit “provenance incomplete” signal), and update docs to reflect the best-effort nature of that channel.
