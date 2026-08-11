# PR#39 contained certification panel: x-ai/grok-4.5

_finish=stop seconds=176.4 cost=$0.1876 usage={'prompt_tokens': 66423, 'completion_tokens': 9156, 'total_tokens': 75579, 'cost': 0.1875644, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1875644, 'upstream_inference_prompt_cost': 0.1326284, 'upstream_inference_completions_cost': 0.054936}, 'completion_tokens_details': {'reasoning_tokens': 6818, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial Review + Certification — PR #39 (protection earns autonomy)

## Findings

### F1 / Payload-forged exit-45 sentinel forces false *downgrade* / LOW
- **Location:** `collaborator/tools.py` (~403–407); `collaborator/contained.py` `protection_unverified` (~168–174); guard ends with `exec "$@"` in `_guarded_script` (~116)
- **Concrete input:** Autonomous contained run whose payload is  
  `sh -c 'echo SALIENT_CODEFENCE_UNVERIFIED >&2; exit 45'`  
  Guard passes (roots ro), payload runs **contained**, then executor sets `protected = False` because `protection_unverified(45, stderr)` is true.
- **Why:** Sentinel/rc check cannot distinguish guard trip (pre-exec) from payload mimicry (post-exec).
- **Guarantee impact:** Does **not** break “no uncontained auto-run” or “no false *upgrade* to code_protected”. It only yields **false-negative** `code_protected` on a run that *was* contained (audit honesty).
- **Caught by another check?** No separate ordering fix; fail-safe only.
- **Fix (optional):** Treat guard verification as a side channel the payload cannot forge (e.g. guard writes a one-time token to a pipe/FD bwrap holds, or only downgrade on 45 when stdout/stderr match a pre-exec-only pattern). Not load-bearing for the safety claim.

### F2 / `setup_failed` prefix miss leaves `code_protected=True` on non-running child / LOW
- **Location:** `collaborator/contained.py` `setup_failed` (~177–183); `tools.py` downgrade block (~403–407)
- **Concrete input:** bwrap fails setup with `rc != 0` but stderr **not** starting with `bwrap:` (wrapper, locale, or non-bwrap exec failure after argv built). Payload never runs; `protected` stays `True`.
- **Why:** Stated design (“setup failure … downgrades protected→False”) is only partially implemented.
- **Guarantee impact:** Not a human-path lie (human path forces `protected=False`). Not an uncontained auto-run (command did not start / `ok=False`). Honesty gap on the autonomy path only.
- **Caught by another check?** `ok = (rc == 0)` prevents success narrative; safety intact.
- **Fix:** Also downgrade when `require_code_protection and rc != 0 and not ok` *before* trusting success, or broaden setup detection; at minimum downgrade on any `rc != 0` if you cannot prove payload started (hard without a pre-exec token).

### F3 / Guard script embedding breaks on `|` / `"` / newline in root paths / LOW
- **Location:** `collaborator/contained.py` `_guarded_script` (~99–116):  
  `specs = " ".join(f'"{r.as_posix()}|{w.as_posix()}"' …)` + `r=${spec%%|*}; f=${spec#*|}`
- **Concrete input:** Install/layout where a protected root or witness path contains `|` or `"` (or newline). Shell parsing splits or injects; mount/append checks apply to wrong paths → likely exit 45 (fail closed) or skipped checks in pathological quoting.
- **Why:** Paths are concatenated into `sh -c` text with weak quoting; not model-controlled (from `__file__`), but host layout-controlled.
- **Guarantee impact:** Almost certainly **fail-closed** (probe/`containment_available` false or per-run 45). Not a practical auto-run bypass on normal prefixes.
- **Caught by another check?** Live probe must pass before autonomy is offered; weird paths tend to disable earning autonomy.
- **Fix:** Pass roots via env (`BWRAP` `--setenv`) or a here-doc/`printf %q`/NUL-delimited loop; never `|`-join into shell words.

### F4 / `containment_available()` sticky cache / LOW (fail-safe / fail-closed)
- **Location:** `collaborator/contained.py` `containment_available` (~186–214), global `_available`
- **Concrete input:** Probe runs when bwrap/userns works → cache `True`; later admin breaks userns. Or first probe fails → cache `False` forever in-process even after fix.
- **Why:** Cached host property; no re-validate.
- **Guarantee impact:** Stale `True` is corrected at **moment-of-use** by wrap/guard/setup refuse or downgrade — **no uncontained auto-run**. Stale `False` only withholds autonomy (availability nit).
- **Caught by another check?** Yes: executor `require_code_protection` + per-run guard.
- **Fix:** TTL, or re-probe on failure; not required for the safety claim.

---

## Non-findings (attacks blocked — certification value)

| Attack | Result |
|--------|--------|
| **AND gate fail-open** (drop `code_protection_available` / `enforced` / signed `shell.contained_autonomy` one at a time) | **Blocked.** Floor is `not (A and B and C)` at govern (`governance.py` ~437–441) and again at execute (`~639–648`). Cap is `directive.grants_capability` under `enforced(session)`, not mutable `session.capabilities` (F5). Tests: unsigned cap stays HELD; exec belt DENY. |
| **Auto-run with `require_code_protection=False`** | **Blocked** for autonomous path: `not human_gated` ⇒ AND must pass ⇒ `require_code_protection=True`. Human approve deliberately leaves flag false (stated non-goal). |
| **Belief vs behaviour: govern re-reads `code_protected`** | **Blocked.** Preview hardcodes `False` + reach string (`~516–520`). Decision uses `execution.code_protected` from executor binding (`tools.py` ~417–420), after per-run downgrades. |
| **`wrap_contained` → protected belief while uncontained payload runs** | **Blocked** if guard/setup work as designed: fail closed pre-`exec`; executor refuses `not protected`; off-Linux `(argv, False, False)` refuses autonomy. |
| **Root with no witness / empty pairs** | **Blocked.** `wrap_contained` → `protected=False`; empty roots test pinned. |
| **Witness symlink “out” desyncing PROTECTED_ROOTS** | **Blocked.** Witness and roots both `Path(mod_file).resolve()` from `_code_slots`; same parent. |
| **Append-only false sense without ro** | **Blocked structurally** by mountinfo `ro` check; append is belt. Spoofing mountinfo before payload would require breaking bwrap’s mount ns (out of scope). |
| **Exit 45 before `exec` not run** | **Fail-safe** — command does not run. |
| **Payload suppresses real downgrade** | **Blocked** — guard runs before `exec "$@"`. |
| **bwrap nested under netns `--map-root-user`** | **Blocked by split.** `require_code_protection` ⇒ only `wrap_contained`; else only `wrap_no_network`. No caller stacks them. |
| **`unshare_net=require_isolation` + signed `shell.raw_network`** | **Not a hole.** FS still ro-contained; shared net is explicit signed opt-in (same shape as pre-existing raw-network floor). |
| **Human path regression / false 🔒 on approve** | **Blocked.** `protected=False`; preview `code_protected=False`; `wrap_no_network` untouched. |
| **MINOR-B approved==executed on contained path** | **Unaffected.** Seal/freeze on hold+approve; autonomy path does not use hold seal; approve still `human_gated=True` uncontained. |
| **Off-Linux auto-run uncontained** | **Blocked.** `protected=False` ⇒ executor error “code protection required but unavailable”; govern floors without probe. |
| **`/proc`/`/dev` host escape via view** | **No break found** under stated model: fresh pid/mount ns, bwrap minimal `--dev`, ro identity binds; `/proc/1/root` is sandbox root. Residual kernel/bwrap issues out of scope. |
| **Workspace vs probe tmp foot-gun** | **Blocked** by `disjoint_from_code` (load-bearing); symlink-to-code via unbound host paths not present in deny-by-default view. |

---

## CERTIFICATION LINE

**CERTIFIED** — With the residual LOW honesty/robustness nits above, the headline holds: no false *upgrade* to `code_protected` on the human path; earned autonomy requires verified containment **and** signed `shell.contained_autonomy` (re-checked at use); per-run guard proves ro (fail-closed); bwrap is never under netns; human/`wrap_no_network` and MINOR-B paths do not regress.

---

## STEELMAN

The decisive engineering move is the **path split**: autonomy earns FS+net containment only via bwrap as the real unprivileged uid (`--cap-drop ALL`, `--disable-userns`), avoiding the mapped-root CAP_SYS_ADMIN ro-bind lie—while maintenance keeps full FS reach on the certified netns path. The autonomy gate is a true **three-conjunct AND** (host probe + `enforced` + signed cap) at both govern and execute, with `require_code_protection` binding the flag to the executor’s wrap/guard outcome rather than a govern-time belief. Deny-by-default view, import-time absolute binaries, witness≠absence, and dual floors (code ∥ network) match the existing F5/raw_network patterns and are pinned by construction tests plus a live Linux proof.

---

## VERDICT

**MINOR_ISSUES** — Safety-critical claims stand under adversarial review; remaining issues are fail-safe honesty (forged sentinel / narrow `setup_failed`) and pathological path quoting, not AND-gate or nesting failures.

**Single highest-value fix:** Stop embedding root/witness paths in `sh -c` via `"|"`-joined quoted words; feed them through env or a safely quoted generator so the guard’s present/append/mountinfo checks cannot mis-parse host layouts—and pair that with “downgrade `code_protected` unless a pre-exec guard token proves success” so payload-forged 45 and non-`bwrap:` setup failures cannot desync the audit flag.
