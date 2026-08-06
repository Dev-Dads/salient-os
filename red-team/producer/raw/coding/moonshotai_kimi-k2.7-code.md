# Red-team (producer, pass=coding): moonshotai/kimi-k2.7-code

_finish=length seconds=399.5 usage={'prompt_tokens': 16941, 'completion_tokens': 12000, 'total_tokens': 28941, 'cost': 0.06409395, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.06409395, 'upstream_inference_prompt_cost': 0.01609395, 'upstream_inference_completions_cost': 0.048}, 'completion_tokens_details': {'reasoning_tokens': 11999, 'image_tokens': 0, 'audio_tokens': 0}}_

We need red-team the implementation. Need produce findings with ID/Title/Severity/Location/Trigger/Why/Fix, then steelman and verdict. We need concrete defects. Let's inspect code thoroughly.

We have observer module, __init__ wrapping, lifecycle.py, model_tools.py, run_agent.py, turn_context.py, tests. Need focus on guarantees:
1. Produce-only / never crash host. Escape paths for BaseException/SystemExit/KeyboardInterrupt from host API observer calls. The three containment layers catch except Exception: _safe_observe in observability/__init__.py catches Exception. salience_observer.observe_lifecycle catches Exception and SystemExit (not KeyboardInterrupt). lifecycle.invoke_hook outer catches Exception. Need find BaseException escaping beyond those. SystemExit caught in observer. KeyboardInterrupt not caught intentionally. Are there paths where KeyboardInterrupt escapes? The user wants specifically BaseException/SystemExit/KeyboardInterrupt path from host API observer calls escaping guards — beyond already-fixed get_config_value. Need check host APIs called by observer: config read (read_raw_config_readonly), get_hermes_home, mkdir/open (pathlib.Path.mkdir/open), salienceos calls. If one raises SystemExit? It would be caught by observer's except SystemExit. If one raises KeyboardInterrupt? observer doesn't catch it; it would propagate through lifecycle.invoke_hook's except Exception no; propagate to host. But is KeyboardInterrupt from those host APIs realistic? In Python, open/mkdir can raise KeyboardInterrupt only if a signal delivered during system call; Python raises KeyboardInterrupt on SIGINT. This can happen in any blocking I/O. Should observer swallow KeyboardInterrupt? It currently intentionally doesn't: "KeyboardInterrupt is deliberately NOT caught: the user's interrupt must reach the host." But guarantee #1 says never lets a BaseException like SystemExit reach the host, includes BaseException. SystemExit caught. KeyboardInterrupt not caught. However user explicitly says KeyboardInterrupt is deliberately not caught. Is that a defect under guarantee #1? It says NEVER BREAKS THE HOST, never lets an exception (including BaseException like SystemExit) reach the host. It specifically says hunt for BaseException/SystemExit/KeyboardInterrupt path. But also says "KeyboardInterrupt is deliberately NOT caught: the user's interrupt must reach the host." Hmm contradictory? Maybe KeyboardInterrupt reaching host is acceptable? In host, KeyboardInterrupt is fine because it interrupts program. But guarantee "never breaks the host" maybe an exception that changes agent behavior; KeyboardInterrupt is user-driven. Yet the prompt says hunt for BaseException/SystemExit/KeyboardInterrupt path from host API observer calls that escapes guards. Since observer explicitly doesn't catch KeyboardInterrupt, that's a path. But they say deliberate? Need assess if it's a valid finding. The prompt says "The three containment layers all catch `except Exception`, so hunt specifically for a BaseException/SystemExit/KeyboardInterrupt path from a host API the observer calls... beyond the already-fixed get_config_value." This suggests we should report if such path exists. However note observer catches SystemExit; KeyboardInterrupt not caught. The outer lifecycle.invoke_hook catches Exception. So KeyboardInterrupt from any host API call in observer (config read, get_hermes_home, mkdir/open, salienceos) can escape to host. Is that a defect? The docstring says deliberate. But guarantee says never let BaseException reach host. In practice, KeyboardInterrupt is user SIGINT; maybe allowed. But given explicit instruction to hunt, maybe report. But severity? It could abort a turn/session on user Ctrl-C during I/O. Maybe MEDIUM/LOW. However prompt wants attack hardest in order; PRODUCE-ONLY #1 top. A KeyboardInterrupt escape is real but maybe intentional. Need decide. Also any BaseException other than Exception/SystemExit/KeyboardInterrupt? GeneratorExit? Not likely.

But maybe there is more concrete SystemExit path: import of salience_observer could fail due to SystemExit? Actually `import hermes_cli.observability.salience_observer` may call module-level `os.urandom` etc. The import is wrapped in try/except Exception in __init__, so SystemExit from import could propagate? In __init__, `from . import salience_observer` is not inside a try/except? Actually:
```
try:
    from . import salience_observer
except Exception:
    logger.warning(...); return
```
This catches Exception, not BaseException. If salience_observer import raises SystemExit (e.g., due to some top-level code? module top-level has `os.urandom` no), it escapes. But top-level code doesn't call sys.exit except import of salienceos which catches Exception; `from salienceos.interpreter...` could raise? If missing module raises ImportError (Exception). SystemExit unlikely. But host APIs? Not.

Also lifecycle.py invokes `observe_lifecycle` inside try/except Exception. But `has_hook` path catches Exception. Could SystemExit from `handles_hook` propagate? In lifecycle.has_hook: try/except Exception around handles_hook call. If handles_hook raises SystemExit, not caught. handles_hook calls salience_enabled -> _config_flag -> read_raw_config_readonly. If config read raises SystemExit, it escapes lifecycle.has_hook. Could cause host has_hook to crash and tool emit gate to fail? In model_tools, has_hook used to decide whether to emit. If has_hook raises SystemExit, that's a crash. But observer's salience_enabled uses _config_flag with try/except Exception returning False; but if read_raw_config_readonly raises SystemExit, not caught. The already-fixed get_config_value was a SystemExit path. They replaced with read_raw_config_readonly, but that could still raise SystemExit if implemented as CLI helper. _config_flag catches Exception not SystemExit. salience_enabled catches Exception around product_identity import, but not around _config_flag. handles_hook doesn't catch. lifecycle.has_hook catches Exception only. So SystemExit from read_raw_config_readonly or product_identity import (if module calls sys.exit) can escape to host. Is that a concrete path? We can trigger by monkeypatching read_raw_config_readonly to raise SystemExit; test in test_systemexit covers get_hermes_home. But config read SystemExit path exists. Severity HIGH? It would crash host when checking has_hook or enabled. It's a host API observer calls. However the prompt says "beyond the already-fixed get_config_value" and "hunt specifically for a BaseException/SystemExit/KeyboardInterrupt path from a host API the observer calls (config read, get_hermes_home, mkdir/open, the salienceos calls)". So this is exactly a finding: config read still can raise SystemExit and escape because _config_flag catches only Exception. Also get_hermes_home in _bus_for can raise SystemExit; observe_lifecycle catches SystemExit. But salience_enabled / handles_hook don't. In handles_hook, if gate check calls _config_flag with SystemExit, it escapes. In lifecycle.has_hook, if handles_hook raises SystemExit, escapes. So enabling check can crash. Also in _safe_observe, callback is salience_observer.observe_lifecycle, which catches SystemExit internally. But lifecycle.invoke_hook outer catches Exception; observe_lifecycle catches SystemExit. So okay for observe. But has_hook is vulnerable.

KeyboardInterrupt path: any host API call in salience_enabled/_config_flag/read_raw_config_readonly, get_hermes_home, mkdir/open, salienceos can raise KeyboardInterrupt (SIGINT) and escape all guards (except observer intentionally). Since they explicitly say not caught, maybe not report? But the guarantee includes BaseException. Let's include as a finding maybe LOW because deliberate? Need maybe discuss. We can frame as: `_safe_observe` and `observe_lifecycle` catch Exception and SystemExit but not KeyboardInterrupt; any blocking I/O (mkdir/open/config read) can raise it, and it will propagate through `lifecycle.invoke_hook`/has_hook to host. This violates the stated "never lets a BaseException... reach the host" unless the design intentionally excludes SIGINT. Suggested fix: catch BaseException in the three containment layers, then re-raise KeyboardInterrupt after logging? But prompt says "NEVER-CRASH-THE-HOST: any path where the observer changes what the agent does, or lets an exception reach the host." If we catch KeyboardInterrupt and swallow it, that would prevent user interrupt. But could log and re-raise? That still reaches host. The only way to not reach host is swallow, which may be undesirable. Hmm. The observer's docstring says KeyboardInterrupt deliberately not caught. The prompt's attack order says hunt specifically for BaseException/SystemExit/KeyboardInterrupt path from host API the observer calls. It might be a trap: the deliberate not-caught is not a defect because user interrupt must reach host; but from a strict reading of guarantee #1 it is. We need be rigorous and concrete. Let's note it but severity maybe LOW / INFO? The prompt asks concrete defects; if no concrete trigger => LOW at most. We have concrete trigger: send SIGINT during `Path.mkdir` or `open` in `_bus_for` while `invoke_hook` runs. This will abort host. It's real.

However, the user wants findings in order: 1 PRODUCE-ONLY/NEVER-CRASH, 2 FAIL-CLOSED ATTRIBUTION, 3 AUDIT FENCE, 4 SEAM, 5 CONCURRENCY, 6 TEST HONESTY. We need prioritize accordingly.

Let's enumerate potential issues.

1. SystemExit from config read escapes `salience_enabled`/`handles_hook`/lifecycle.has_hook.
   - Location: salience_observer.py `_config_flag`; maybe `salience_enabled`; `observability/__init__.py handles_hook`? Actually handles_hook imports salience_observer and calls handles_hook; lifecycle.has_hook wraps handles_hook with except Exception. Since _config_flag catches Exception, SystemExit propagates up through salience_observer.handles_hook -> observability.handles_hook -> lifecycle.has_hook, where except Exception fails to catch. So host `has_hook` crashes. Also `salience_enabled` itself catches Exception around product_identity but not around _config_flag? It calls _config_flag at end; if _config_flag raises SystemExit, it propagates. So `handles_hook` raises SystemExit.
   - Trigger: `read_raw_config_readonly()` raises `SystemExit(1)` (e.g., CLI helper missing key). Then `lifecycle.has_hook("post_tool_call")` crashes, causing agent tool dispatch to abort? Actually model_tools `_emit_post_tool_call_hook` catches Exception around has_hook and invoke_hook, so SystemExit from has_hook would not be caught (except Exception). It would propagate. This could abort the turn. Even worse, `lifecycle.has_hook` is called by other places; crash.
   - Why matters: violates produce-only; a config-read failure crashes host and changes behavior.
   - Fix: In `_config_flag`, catch `BaseException` (or at least `SystemExit`) and return fail-closed False. Also in `observability/__init__._safe_observe` and `lifecycle.has_hook/invoke_hook`, catch BaseException? But prompt says three containment layers catch except Exception. Maybe the outer layers should catch BaseException minus KeyboardInterrupt? But we can suggest `_config_flag` catch SystemExit.

2. KeyboardInterrupt from blocking I/O escapes observer to host.
   - Location: `_bus_for` calls `get_hermes_home`, `directory.mkdir`, `open`; `_config_flag` calls `read_raw_config_readonly`; `_close_locked` calls `issue_policy`/`interpret`/`bus.emit` maybe. All can raise KeyboardInterrupt. Containment layers catch Exception; observer catches Exception+SystemExit but not KeyboardInterrupt. lifecycle.invoke_hook catches Exception. So a SIGINT during I/O aborts host. The docstring says intentional. But guarantee #1 says never. Should we report? We can phrase as deviation. Suggested fix: catch KeyboardInterrupt in observer's hook body and log+swallow? But the docstring says user's interrupt must reach the host. Maybe better to wrap file I/O in a try/except KeyboardInterrupt that logs and re-raises? That still reaches host. To never reach host, must swallow. That conflicts. Perhaps the guarantee implicitly excludes user-driven KeyboardInterrupt. So maybe not report, or report as LOW/observation that the code deliberately lets it through. But the prompt includes KeyboardInterrupt in hunt list. I'll report but note it's a deliberate design tension; severity LOW because user interrupt is expected. However the instruction "NEVER-CRASH-THE-HOST" could treat it as a host crash. Hmm.

Let's focus on more concrete bugs.

3. Fail-closed attribution:
   - `_open_window` can open a window for same session+turn twice? It checks current closed/turn_id. If current exists and not closed and same turn_id, it does nothing. Fine.
   - But `_open_window` finalizes previous window if turn_id differs. If a pre_llm_call for turn N arrives while window for turn N is already open, no finalize. Good.
   - However, what about session reset: `_close_session` removes window and bus. But if on_session_reset called, it finalizes and frees. Good.
   - Cross-turn/cross-session: `_record` checks window.turn_id == turn_id. Good.
   - Closed window: checks window.closed. Good.
   - But window.closed flag set in `_close_locked`. If window closed but still in _WINDOWS? In `_close_session`, pops it. In `_open_window`, after closing previous, it replaces. So closed windows not left. In `_record`, guard window.closed is maybe redundant but safe.
   - Mismatched turn_id: `_open_window` finalizes previous and opens new. If pre_llm_call is skipped and a post_tool_call arrives with turn_id when no window? dropped. Good.
   - Concrete sequence with no open window: test covers.
   - Could a signal be recorded against a window after session close? `_record` with same session_id/turn_id after `_close_session`: window popped, dropped. Good.
   - Cross-session leak via subject? Subject uses session_hash + turn_id. Raw session_id not present. Good.
   - Could `_ids` convert non-string session_id/turn_id to str, e.g., None -> "" => fail closed. Good.
   - Durable subject/filename leak raw session_id? Bus filename is session_hash. Subject includes session_hash[:16] not raw. Good.
   - However: `_subject` for long turn_id hashes turn_id, but if turn_id length <= room, includes verbatim. Could a turn_id contain raw session_id? Not relevant; the guarantee is raw session_id must never reach durable record. It doesn't.
   - FAIL-CLOSED ATTRIBUTION subtle issue: `_open_window` opens a window even if gate is off? Wait `observe_lifecycle` for non-close hooks returns early if `not handles_hook(hook_name)`. handles_hook depends on salience_enabled, which reads config each call. If gate flips off mid-session, pre_llm_call not handled. Session close still finalizes. But what if gate was on when pre_llm_call opened window, then operator turns off before post_tool_call. `handles_hook("post_tool_call")` returns False, so `_record` not called. But window remains open. On session close, `_close_session` runs regardless of gate and finalizes. Good. But there could be a window leak if gate turned off before session close and close event not dispatched? But close events run regardless of gate. Fine.
   - Another issue: `_close_session` finalizes window even if it was already closed? It pops window and if not closed calls _close_locked. Good.
   - A3: turn N finalized before turn N+1 accumulates. In `_open_window`, if current exists and not closed and turn_id differs, it closes it, then opens new. Good. But if a new turn starts and pre_llm_call not invoked (some path), no finalize. But guarantee only when window open signal carries. That's by design.
   - Could there be a case where `_open_window` is called with same session_id and same turn_id after some signals, and it reopens? No, it keeps current. Good.

Maybe fail-closed attribution issue: `_record` appends signals to `window.signals` even if `self_bus.publish` raises exception? It catches exception around publish; signal not appended. Good. But if publish succeeds but emit directive later fails, no directive. Fine.

4. Audit fence:
   - `_map_tool_call` uses `tool_name` and `status` only. `tool_name` can be arbitrary string up to MAX_TOKEN_LEN (clamped). `status` also. `_ref` truncates to MAX_TOKEN_LEN and drops empty. Good. No args/result. `error_type` not in provenance; only used to set is_error. Good.
   - `_map_api_error` uses provider only. Good.
   - But `_ref` returns tuple of up to 16 tokens. Fine.
   - However `_subject` can produce a subject that is not a valid ref? It clamps length to MAX_TOKEN_LEN. For short turn_id, subject is head (session_hash[:16]+":") + turn_id. Total <= MAX_TOKEN_LEN. Head length? session_hash[:16] is 16 chars + colon = 17. room = 111. If turn_id length <=111, included. Subject length <=128. Is subject non-empty? yes. Is it a ref token? yes. Good.
   - valid_signal: subject bounded. Good.
   - But `SUBSYSTEM_ID = "quorum.observer"` length ok. Facet strings from Facet constants are ok. Influence/confidence within [0,1]. Provenance tokens bounded. So valid_signal passes.
   - Audit fence issue: `_map_tool_call` could produce a provenance token containing newlines or control chars? `_ref` doesn't sanitize. But valid_signal only checks length and string type, not content. Bus JSONL will contain raw strings. Is that a problem? "unbounded or non-ref content on the bus". Control characters still bounded length. But maybe "non-ref content" includes arbitrary chars. However `valid_signal` accepts any string of bounded length. The prompt says "salienceos valid_signal() / the directive payload fence enforce it structurally; a signal that FAILS valid_signal (noise) is also a bug." Since valid_signal passes, it's structurally okay. Could tool_name contain prompt-sized after encoding? truncated.
   - Another audit fence: `error_type` not recorded, only influences facet. That is acceptable; no unbounded content.
   - What about `_map_api_error`: `provider` can be arbitrary string; `_ref("api_error", "provider:" + provider)` truncates. Good.
   - Is there a way to make `kwargs.get("retryable")` not bool but truthy/falsy; influence computed as 0.5 if retryable is True else 0.8. Fine.
   - Maybe `tool_name` can be a non-string (e.g., object). `str(tool_name)` may produce huge repr? `_ref` truncates. Good. But `lowered = tool_name.lower()` uses original `tool_name` str? Actually `tool_name = str(kwargs.get("tool_name") or "")` so it's string. Good.
   - Could `status` be "error" due to `error_type` truthy even if status empty; provenance "status:". That's empty token dropped. Fine.
   - Any input that makes produced signal FAIL valid_signal? Let's think. `_signal` uses `SalienceSignal(SUBSYSTEM_ID, subject, facet, influence, 1.0, provenance)`. If `provenance` is a tuple of strings <=128. Good. `facet` from Facet constants. `subject` from `_subject`. Good. Influence from _map_tool_call: 0.7,0.6,0.4; _map_api_error 0.5/0.8. Confidence 1.0. So valid_signal passes. No input can make it fail unless subject exceeds? subject clamped. Provenance tokens clamped. So no noise bug.
   - Wait: `_subject` for short turn_id includes raw turn_id. If turn_id contains arbitrary characters (e.g., 100 chars of unicode, length okay), subject includes them. valid_signal accepts. Fine.
   - Maybe `session_id` can be huge; `_session_hash` hashes it. Good.

5. Seam safety:
   - Does enabling salience change `invoke_hook` return value? In lifecycle.py, `invoke_hook` calls `observe_lifecycle` first (returns None), then `plugins.invoke_hook` and returns its result. Observer returns None. So no change. Good.
   - Does it change dispatch to relay_shared_metrics? observability/__init__ calls relay first, then salience. So no. Good.
   - Does it change hook ordering? It adds observer after relay. For has_hook, relay checked first. So observer's answer only matters if relay doesn't handle. pre_llm_call is in SALIENCE_HANDLED_HOOKS. But pre_llm_call is also handled by plugins? The emitter in turn_context.py uses `_pre_results = _invoke_hook("pre_llm_call", ...)` then uses results from plugins? Let's see. `invoke_hook` returns `plugins.invoke_hook(...)`. The observer's `observe_lifecycle` for pre_llm_call returns None; lifecycle.invoke_hook returns plugin results. The observer opens window. It doesn't affect return. Good.
   - Does enabling salience change effect of hook? For post_tool_call and api_request_error, enabling observer makes `has_hook` True. Emitters self-gate on has_hook: if no observer, has_hook returns plugins.has_hook. If no plugin handles, emitter returns early without deriving status/result and without invoking hook. Enabling salience flips has_hook True, so emitter will now derive status and invoke_hook. This invokes plugins too (if any). The EFFECT of the hook? For observational hooks only, previously dead by default; now it fires. The guarantee says: "Enabling salience flips has_hook True only for the observational post_tool_call and api_request_error emitters (previously dead by default); ... pre_llm_call's context-injecting return is consumed from PLUGINS only, never from the observer." So enabling salience changes `has_hook` return value for those hooks, causing emitters to fire them. But is that acceptable? The guarantee says it is expected and acceptable. So not a defect.
   - But maybe there is a seam issue: `handles_hook` returns True for `pre_llm_call` when enabled, causing `lifecycle.has_hook("pre_llm_call")` to return True. If any upstream code checks has_hook and changes behavior (e.g., skips plugin context injection if no hook?), then enabling salience could cause plugin context injection path to run when it previously didn't. Need inspect turn_context.py: It unconditionally calls `_invoke_hook("pre_llm_call", ...)`, not gating on has_hook. So no issue. But maybe other emitters gate. model_tools gate on has_hook for post_tool_call; with observer enabled, it will invoke plugins too. That may cause previously-dead plugin hooks to fire. But guarantee accepts that: "Enabling salience flips has_hook True only for the observational post_tool_call and api_request_error emitters (previously dead by default)". Actually it says only for observational emitters; but by making has_hook True, plugin hooks may also fire if registered. Wait: has_hook returns True if any built-in observer OR plugin handles. If observer handles post_tool_call, has_hook returns True. Then model_tools invokes invoke_hook, which calls plugins.invoke_hook too. So any plugin registered for post_tool_call will now fire, even though before observer it would not because has_hook False. Is this a SEAM defect? The guarantee text acknowledges "Enabling salience flips has_hook True only for the observational post_tool_call and api_request_error emitters (previously dead by default); pre_verify and transform_tool_result stay unhandled". It doesn't address plugin side effects. But the general SEAM SAFETY guarantee says "Adding the observer to observe_lifecycle / handles_hook must NOT change invoke_hook's return value, the dispatch to relay_shared_metrics, or the EFFECT of any hook." Changing has_hook can change whether the emitter fires at all, and thus the effect of plugin hooks. However they consider this acceptable because previously dead observational hooks firing is expected. But is it only observational? The emitter passes args/result to plugins too. If a plugin registers for post_tool_call, enabling salience makes it fire. That changes the effect of a hook (plugin side effect). Could be a defect. But the design maybe intentionally uses the existing lifecycle seam; plugins can register. Need think if concrete exploit. An adversary could install a plugin that handles post_tool_call, and enabling salience causes it to receive tool results, potentially exfiltrating. But that's not observer's fault. However from seam safety perspective, enabling observer changes the dispatch decision: has_hook True => emitter derives status and dispatches to plugins. The EFFECT of any hook maybe changed. But the prompt says "not just firing a previously-dead observational hook, which is expected and acceptable." So we need find ways enabling salience changes invoke_hook's return value, relay dispatch, hook ordering, or EFFECT beyond that. Could there be a return value change? Observer returns None. No. Relay dispatch unchanged. Hook ordering unchanged. Effect: only fires dead observational hooks. Acceptable. So maybe no seam issue.

   - Another seam issue: `observe_lifecycle` catches Exception+SystemExit and logs. This can mutate logger state? No. It could log warnings, causing I/O, but that's observational effect. Not a change to agent.

   - Maybe `_safe_observe` in observability/__init__.py catches Exception, but if `relay_shared_metrics.observe_lifecycle` raises SystemExit, it propagates to host. The prompt says three containment layers all catch except Exception. But `_safe_observe` catches Exception. Is there SystemExit path from relay? Not our scope maybe. But the observer's wrapping is safe.

6. Concurrency / resource:
   - `_LOCK` serializes all access. Good.
   - But `_bus_for` is called inside `_record` under _LOCK, and inside `_close_locked` under _LOCK. It lazily opens bus, calls mkdir/open. SalienceBus single-threaded contract respected because only one thread accesses per session under _LOCK. Good.
   - However, `_bus_for` first checks `_BUSES.get(session_id)`, then creates bus and sets. Under lock, no race. Good.
   - Unbounded growth: `_BUSES.pop` on session close. Good. But what if `on_session_end`/`on_session_finalize`/`on_session_reset` not called for a session? Then window+bus remain forever. That's a long-lived host leak. Is that a defect? The design assumes session lifecycle events fire. But if a session crashes or host kills without close, leak. Could there be a way to ensure TTL? Not likely. But the guarantee says freed on session close. It doesn't guarantee if close not called. Maybe not finding.
   - Deadlock? _LOCK is non-reentrant. `_open_window` under lock calls `_close_locked` which doesn't reacquire. Good. `_close_locked` calls `_bus_for` under lock (already held by caller). `_bus_for` doesn't acquire lock. Good. No nested lock.
   - `_close_session` under lock calls `_close_locked` then `_bus_for`? Actually after popping window, it calls `_BUSES.pop`. `_close_locked` calls `_bus_for`, which checks _BUSES dict; under lock. Good.
   - `_record` under lock calls `_bus_for`, which opens file. If file open blocks, lock held; other threads blocked. But single-threaded contract. Not a defect.

7. Test honesty:
   - Need identify tests that would stay green if code sabotaged. Many tests call internal functions directly; they may not test the gate integration. For example, `test_records_only_against_matching_open_window` calls `_record` directly after opening window, not through dispatch. If `observe_lifecycle` were changed to drop all events, test still green. But that's a unit test of internal function. The E2E tests use lifecycle.invoke_hook. Need see if any guarantee's test is mutation-blind.
   - `test_e2e_through_real_tool_dispatch` uses `lifecycle.invoke_hook` and `model_tools._emit_post_tool_call_hook`. It checks path exists, facets, directives, verify_chain. If `_map_tool_call` were sabotaged to always return empty, test would fail because facets missing. If `_subject` leaked session_id, test checks `session_id not in subject`. Good.
   - `test_closed_gate_produces_nothing_through_dispatch`: forces gate closed via `_force_gate` which patches `salience_enabled` AND config. It checks `_WINDOWS == {}` and no directory. If the observer were changed to ignore gate and open windows, test would fail. Good.
   - `test_systemexit_from_host_api_is_contained`: patches `get_hermes_home` to raise SystemExit and calls `observe_lifecycle("post_tool_call"...)`. It asserts no exception. But the code's `observe_lifecycle` catches SystemExit. If the code were changed to catch BaseException, still green. If changed to not catch SystemExit, test red. It is not vacuous. However it only tests post_tool_call path, not has_hook/config path. But okay.
   - `test_close_frees_even_when_gate_flips_off`: tests close event ungated. Good.
   - `test_subject_hashes_long_turn_id_without_aliasing`: deterministic. Good.
   - `test_records_drop_across_sessions`: calls `_record` directly; tests session isolation at `_record` level. If `_open_window` used a global window, test would fail. Good.
   - `test_emitted_directive_binds_operator_budget`: uses internal `_close_session`; checks directive compute_budget. If `_operator_budget` broken, test red. Good.
   - `test_emitted_directive_defaults_budget_when_unconfigured`: similar.
   - `test_mapping_by_facet`: checks facet mapping. Good.
   - `test_only_mapped_hooks_are_handled`: uses `handles_hook`. Good.
   - `test_kill_switch_honors_falsey_values`: good.
   - `test_unreadable_config_fails_closed`: patches read_raw_config_readonly to raise RuntimeError. It asserts False. If _config_flag caught Exception and returned False, good. It doesn't test SystemExit. Good.
   - Potential test honesty issue: `test_e2e_through_real_tool_dispatch` checks `assert bus.verify_chain() is True`. Since the bus is newly created and entries appended, verify_chain always true unless corruption. But it does check chain well-formed. Not mutation-blind.
   - `test_close_locked_is_idempotent`: calls `_close_locked` directly. Good.
   - Are any assertions over-mocked? `_force_gate` patches `salience_enabled` and `read_raw_config_readonly` and `get_hermes_home`. It doesn't patch product_identity. The gate tests restore real salience_enabled and patch product_identity/config. Good.
   - Maybe `test_records_only_against_matching_open_window` checks `len(signals_for(subject)) == 1` after calling `_record` directly. It doesn't go through `_map_tool_call`? It passes mapper `_map_tool_call`. Good.
   - `test_mapping_by_facet`: no dispatch. Fine.
   - Need maybe find a test that is vacuous regarding a guarantee. For guarantee #1 (never crash host), `test_systemexit_from_host_api_is_contained` covers only `observe_lifecycle` path, not `handles_hook`/config path. But not vacuous. Could be enhanced.
   - For guarantee #2 fail-closed attribution, tests cover no ids, wrong turn, cross-session. But do they test recording against a closed window? `test_close_locked_is_idempotent` closes then records? It only calls `_close_locked` twice, not record after close. `test_session_close_frees_registries` records after close and asserts no window. That's closed window drop (actually window removed). Good.
   - For guarantee #6 seam safety, no test directly asserts that enabling salience doesn't change invoke_hook return or relay dispatch. The E2E test doesn't compare with gate off. Could be considered missing coverage, not a finding. But the prompt wants "TEST HONESTY: any guarantee above whose test would stay green if the code were sabotaged". If there is no test for seam safety return value, that's a coverage gap but not a vacuous test. We can report as test honesty finding: no test asserts that `lifecycle.invoke_hook` return value is unaffected by observer; a sabotaged observer that returns a value from observe_lifecycle wouldn't be caught. Actually observe_lifecycle returns None; lifecycle returns plugins result. No test asserts that. But maybe `test_e2e` uses `lifecycle.invoke_hook` and doesn't check return. So if someone modified observer to return something and lifecycle used it, test wouldn't catch. But lifecycle code returns plugins result. The guarantee is about code; test missing. Is that a finding? It could be reported under test honesty as missing test, not mutation-blind existing test. The prompt says "any guarantee above whose test would stay green if the code were sabotaged (mutation-blind, vacuous, over-mocked, or circular)." So we need identify existing tests that are mutation-blind. Could there be one?

Let's think of mutations:
- Mutation: `_safe_observe` catches `Exception` but should catch `BaseException`. Existing test `test_systemexit_from_host_api_is_contained` would still pass if `_safe_observe` not changed but observer catches SystemExit. It doesn't test `_safe_observe` from `observability/__init__` because `observe_lifecycle` catches internally. So the test doesn't exercise the outer seam. But it tests guarantee.
- Mutation: `handles_hook` returns True for all hooks when enabled. `test_only_mapped_hooks_are_handled` would fail. Good.
- Mutation: `salience_enabled` always returns True even if not Quorum. `test_not_quorum_edition_is_dark` fails. Good.
- Mutation: `_operator_budget` returns hardcoded 25 always. `test_emitted_directive_binds_operator_budget` fails. Good.
- Mutation: `_subject` includes raw session_id. `test_subject_hashes_session_and_is_bounded` fails. Good.
- Mutation: `_record` doesn't check turn_id. `test_records_only_against_matching_open_window` fails. Good.
- Mutation: `_close_session` doesn't pop _BUSES. `test_session_close_frees_registries` fails. Good.
- Mutation: `_open_window` doesn't finalize previous. `test_new_turn_finalizes_previous` fails. Good.
- Mutation: `_map_tool_call` includes args. Not directly tested? `test_e2e_through_real_tool_dispatch` passes function_args; if observer recorded args, would `valid_signal` fail? Actually args dict str truncated to 128 may still validate. But the test checks bus.signals_for and facets; it doesn't inspect provenance for absence of args. However if `_map_tool_call` included `str(function_args)` in provenance, it would be bounded and pass. No test would fail? `test_mapping_by_facet` calls `_map_tool_call` with only tool_name/status; no args. It wouldn't catch inclusion of args because no args provided. So a mutation adding args to provenance might pass tests. But guarantee #3 audit fence; should test that args don't reach bus. There is no assertion for that in E2E? It checks `session_id not in subject` and facets. It doesn't inspect provenance. So test honesty issue: no assertion that function_args/result/user_message are absent from durable record. This is a concrete test honesty finding. We can report: tests don't verify that `args`, `result`, `user_message`, `error_message`, etc. are absent from bus. A mutation that leaks them could stay green. Suggested fix: add assertions in `test_e2e_through_real_tool_dispatch` and `test_mapping_by_facet` that provenance and subject contain no args/result/error_message.

- Also `test_mapping_by_facet` checks `all(len(p) <= so.MAX_TOKEN_LEN)`, not content. It passes tool_name and status only. If `_map_tool_call` started including `error_type` in provenance, it would still pass (bounded). But error_type is okay ref? It's bounded. But audit fence says only bounded ref tokens; error_type is a ref token. Not necessarily a violation. But prompts/results are.

- Another potential test honesty: `test_e2e_through_real_tool_dispatch` checks `assert bus.verify_chain() is True`. This is always true for freshly written bus. It doesn't catch a sabotage where `SalienceBus._append` writes invalid entries? Actually if invalid, signals_for/directives_for might fail. But not strong. Still, not mutation-blind.

- Test for SystemExit containment only covers `get_hermes_home` in post_tool_call, not config read in `handles_hook` or `salience_enabled`. Could report as test honesty / coverage gap.

Let's also inspect vendored bus.py for misuse by observer:
- `SalienceBus` is single-threaded; observer serializes. Good.
- `_valid_directive_shape` checks subject/policy_id etc bounded. `interpret` returns Directive. Observer's directive subject matches window.subject bounded. Good.
- `emit` uses `directive.reconfigure.value`; Directive class? Not shown but assume enum. Good.
- Observer calls `issue_policy` with `granted_capabilities=()` (tuple). `issue_policy` does `tuple(granted_capabilities)` -> (). Good.
- `issue_policy` signs payload. `interpret` verifies? Good.
- `_operator_budget` returns int. Good.
- `adaptation_max_risk` 0.5 float. Good.
- `allow_immediate_reconfigure=False`. Good.
- `max_retention="semantic"` in RETENTION_ORDER. Good.
- `min_verification=0`, `max_verification=3` (FULL). Good.
- `min_budget=budget`, `max_budget=budget`. Good.
- No misuse.

Now look at `_config_flag` more:
```
def _config_flag(key: str, default: bool) -> bool:
    try:
        from hermes_cli.config import read_raw_config_readonly
        cfg = read_raw_config_readonly() or {}
    except Exception:
        return False
    salience = cfg.get("salience") if isinstance(cfg, dict) else None
    if not isinstance(salience, dict) or key not in salience:
        return default
    return not _looks_off(salience.get(key))
```
If `read_raw_config_readonly` returns a dict subclass that raises SystemExit on `.get`? Unrealistic. But `cfg.get` could raise if `cfg` is not dict? It checks isinstance. If cfg is not dict, returns {}? Actually `cfg = read_raw_config_readonly() or {}`. If cfg is None -> {}. If cfg is e.g. list, `cfg.get` would AttributeError (Exception) -> not caught, propagates? Actually `_config_flag` has try/except around read only, not around cfg.get. If `cfg` is not dict, `cfg.get` raises AttributeError, which is Exception, not caught, propagates to `salience_enabled`. It is caught by `handles_hook`? No, `handles_hook` just returns expression; lifecycle.has_hook catches Exception. But `_config_flag` could raise AttributeError if config returns non-dict. Is that a concrete defect? The config read should return dict; if it returns something else, fail-closed? `_config_flag` doc says unreadable config => fail-closed, but doesn't handle malformed dict. It does `cfg.get("salience") if isinstance(cfg, dict) else None`. Wait it checks isinstance(cfg, dict) before `.get`, so non-dict -> salience=None -> returns default. But `cfg = read_raw_config_readonly() or {}`. If `cfg` is e.g. `[]` (list), `cfg or {}` returns `{}` because list is truthy? Actually `[] or {}` returns `{}`; `[1] or {}` returns `[1]`. So if config returns a list, `cfg` is list, `isinstance(cfg, dict)` false, salience None, return default. So no AttributeError. Good. If `cfg` is a dict subclass with `.get` raising, weird.

`_looks_off`: If value is e.g. empty list? It returns False (not off). Then enabled. Fine.

`read_raw_config_readonly` could raise `SystemExit`. `_config_flag` catches Exception only. So SystemExit escapes. That's a concrete trigger. This is the same as finding #1. Also `product_identity` import in `salience_enabled` is wrapped in try/except Exception. If it raises SystemExit, caught. But config read not.

What about `get_hermes_home` in `_bus_for`: `observe_lifecycle` catches SystemExit for post_tool_call etc. But `_close_session` is called from `observe_lifecycle` close path, which catches SystemExit. So get_hermes_home SystemExit in finalize is caught. Good.

What about `read_raw_config_readonly` in `_operator_budget`: called from `_close_locked` under lock, inside `observe_lifecycle` close path (catches SystemExit) or during `_close_session` which is called from observe_lifecycle close path. So SystemExit from config read in operator budget is caught. But `_operator_budget` itself catches Exception. Not SystemExit. But path is covered by observe_lifecycle.

However `salience_enabled` and `handles_hook` are called by lifecycle.has_hook and model_tools has_hook. Those are not inside observer's SystemExit catch. So SystemExit from config read at gate check is not caught. Concrete.

Should we also flag that `lifecycle.has_hook` catches only Exception, and `model_tools._emit_post_tool_call_hook` catches only Exception. The prompt says three containment layers all catch except Exception. It asks to find paths escaping those guards. We can point to this.

Now consider `BaseException` paths from `read_raw_config_readonly` returning a value whose `__bool__` or `__or__` raises? Not relevant.

Now consider `KeyboardInterrupt` path. Since observer intentionally doesn't catch, but lifecycle.has_hook/invoke_hook catch Exception. Any blocking call can be interrupted. This is a real path. We can report under #1. Severity maybe MEDIUM because it can abort a host operation, but KeyboardInterrupt is a user signal. The guarantee says never lets an exception (including BaseException like SystemExit) reach the host. It includes KeyboardInterrupt. The design docstring explicitly exempts it. I think it's a finding but perhaps LOW given accepted design risk. However since user specifically listed it, maybe report as MEDIUM.

But we need not over-report low-quality. Let's find more high-impact issues.

Potential high impact: `_bus_for` lazily creates the directory and bus file outside any exception handling that ensures the lock is released? It is called inside `_record` and `_close_locked` with `with _LOCK`. If `directory.mkdir` or `open` raises `OSError`, it's caught by the try/except around publish? Wait `_record` calls `_bus_for` inside try/except Exception around `self_bus.publish(signal)`? Actually code:
```
for signal in mapper(kwargs, window.subject):
    try:
        self_bus = _bus_for(session_id)
        self_bus.publish(signal)
        window.signals.append(signal)
    except Exception:
        logger.warning(...)
```
So `_bus_for` is inside try/except Exception. If it raises OSError, caught, signal not appended. Good. But in `_close_locked`, `_bus_for` is not inside try/except; the whole `_close_locked` is inside `try/except Exception` in `observe_lifecycle` close path. Also `_close_locked` itself has try/except around policy/interpret/emit, but `_bus_for` is before? Actually `_close_locked`:
```
try:
    budget = _operator_budget()
    policy = issue_policy(...)
    directive = interpret(...)
    _bus_for(window.session_id).emit(directive)
except Exception:
    logger.warning(...)
```
_bus_for is inside try/except Exception. Good.

But `_bus_for` uses `from hermes_constants import get_hermes_home`. Import inside function; if it raises SystemExit, not caught by `_close_locked` except Exception; but caught by observe_lifecycle close path except SystemExit. Good. For `_record`, observe_lifecycle non-close path catches SystemExit. Good.

Now potential issue: `_close_session` calls `_close_locked` then `_BUSES.pop`. If `_close_locked` raises SystemExit? It would propagate and `_BUSES` not popped, causing leak. But observe_lifecycle catches SystemExit. However `_close_session` is called from observe_lifecycle; if `_close_locked` raises SystemExit, observe_lifecycle catches and returns. Window popped already? In `_close_session`, window popped before `_close_locked`. Bus popped after. If SystemExit occurs in `_close_locked`, bus not popped -> leak per session. But SystemExit is rare. Could be a concrete defect: SystemExit from `issue_policy`/`interpret`/`emit` (vendored) in `_close_locked` aborts session cleanup and leaks the bus. However observe_lifecycle catches SystemExit; it doesn't re-raise. But the rest of `_close_session` after `_close_locked` doesn't run. Since SystemExit is caught at observe_lifecycle, `_BUSES.pop(session_id, None)` not executed. Window already popped, so no window leak, but bus remains in _BUSES. On a long-lived host, if config read/SystemExit happens repeatedly during session finalization, _BUSES grows. But only one per session; session close event may not retry. So one leaked bus per session. That's a resource leak. But is it concrete? Need vendored call raising SystemExit. The prompt says hunt for BaseException path from host API, not vendored. Still SystemExit from vendored could leak. But vendored code is already-reviewed; should not raise SystemExit. Maybe not.

What about exceptions in `_bus_for` opening existing file: `SalienceBus.__init__` calls `_replay` which raises ValueError on corrupt file. `_bus_for` doesn't catch; the caller's except catches. On `_record`, signal dropped; on `_close_locked`, finalize fails. Fine. But if corrupt bus file persists, every subsequent event for that session dropped. That's more of a durability issue.

Another issue: `_bus_for` creates `directory` with `Path(get_hermes_home()) / "salience"`. It doesn't resolve absolute path; if get_hermes_home returns relative path, fine. It calls `directory.mkdir(parents=True, exist_ok=True)`. This is okay.

Potential AUDIT FENCE issue: The `post_tool_call` emitter in `model_tools.py` passes `args=function_args, result=result, ...` to `invoke_hook`. The observer's `_map_tool_call` only reads `tool_name`, `status`, `error_type`. But `invoke_hook` also dispatches to plugins. If a plugin is registered, it receives args/result. The observer doesn't put them on bus. That's not a defect. But the audit fence guarantee is about produced signal. The observer respects it.

But the emitter passes `error_message` too; observer doesn't use it. Good.

Potential FAIL-CLOSED ATTRIBUTION issue: `_ids` converts `session_id`/`turn_id` to string. If a caller passes `session_id` as object with `__str__` returning empty, fail closed. Fine.

Potential issue: `_open_window` uses `kwargs.get("task_id") or ""` and includes in window but not in subject. Task_id not used for attribution. Fine.

Potential issue: `_subject` uses `_session_hash(session_id)[:16] + ":"`. For long turn_id, it hashes turn_id and appends. But `_session_hash` returns 64 hex chars; hashed tail length 64. head 17+64=81 <=128. For short turn_id, head+turn. Good. But is there a collision risk? Not relevant.

Potential issue: The `_session_hash` is sha256 of raw session_id. If raw session_id is empty, hash is known constant. Not leak.

Potential issue: `MAX_TOKEN_LEN` mirror but `_ref` clamps tokens to MAX_TOKEN_LEN and `_subject` clamps total. Good.

Potential issue: `_operator_budget` is memoized process-wide. If config changes during process, budget stale. Acceptable per doc.

Potential issue: `_POLICY_KEY = os.urandom(32)` at import. If import fails? In try/except, if import succeeds, key generated. If `/dev/urandom` unavailable, `os.urandom` raises NotImplementedError (Exception). This would be caught by the import try/except? Wait `_POLICY_KEY = os.urandom(32)` is at module top-level, after successful import. If it raises, the module import fails. In `observability/__init__.py`, `from . import salience_observer` is inside try/except Exception, so import failure caught -> observer disabled. In `lifecycle.py`, `from hermes_cli.observability import observe_lifecycle` inside try/except Exception, so also caught. Good. Not a crash.

Potential issue: `os.urandom` can raise `BlockingIOError`? On Linux /dev/urandom non-blocking. Not.

Potential issue: The `try/except Exception` in module import might swallow SyntaxError? SyntaxError is Exception subclass? Actually SyntaxError inherits Exception. Yes. So import syntax error disables. Good.

Now think about SEAM more deeply. The lifecycle dispatch `invoke_hook` calls `observe_lifecycle` then `plugins.invoke_hook`. The observer's `observe_lifecycle` for `pre_llm_call` opens a window. It does not return context. But the emitter in `turn_context.py` uses `_pre_results = _invoke_hook("pre_llm_call", ...)` and then builds context from plugin results. Since observer returns None, not included. Good.

But `invoke_hook` returns `plugins.invoke_hook(...)` results. If a plugin for pre_llm_call returns context, used. Observer doesn't affect. Good.

What about ordering: observer runs before plugins. If observer crashes (logs), plugins still run. Good.

`has_hook`: `observability.handles_hook` returns True if relay or salience handles. If salience enabled, `pre_llm_call` handled. In `turn_context.py`, they don't gate on has_hook, just invoke. In `model_tools.py`, they gate on has_hook for post_tool_call. If salience enabled, gate opens and plugins get results. Is there a scenario where a plugin registers for post_tool_call but the agent intentionally avoids invoking it when no observer? That's by design of emitters. The guarantee says enabling salience flips has_hook True for observational emitters previously dead by default, and that is expected. So no issue.

But could enabling salience change the return value of `has_hook("pre_verify")`? `handles_hook` returns False for pre_verify. lifecycle.has_hook then checks plugins. So no.

Potential seam issue: `_safe_observe` in observability/__init__.py catches Exception. If `relay_shared_metrics.observe_lifecycle` raises SystemExit, it propagates, crashing host. But not observer's code; the guarantee #1 says "any path where the observer changes what the agent does, or lets an exception reach the host". Relay crash not observer's. But the three containment layers include `_safe_observe`. It catches Exception. Should we report that `_safe_observe` does not catch SystemExit/KeyboardInterrupt? The prompt says "The three containment layers all catch `except Exception`, so hunt specifically for a BaseException/SystemExit/KeyboardInterrupt path from a host API the observer calls". It doesn't ask to red-team relay. But maybe `_safe_observe` is a containment layer and if a future observer callback raises SystemExit, host crashes. However current observer catches SystemExit. We can mention that the outer seam still lets SystemExit through if callback is not the observer (e.g., relay), but not a concrete defect for this PR. Maybe out of scope.

Now consider TEST HONESTY more. Let's map each guarantee to tests:
1. PRODUCE-ONLY / NEVER CRASH: tests `test_systemexit_from_host_api_is_contained`, `test_closed_gate_produces_nothing_through_dispatch`. Does `test_systemexit` stay green if `_safe_observe` (outer) not catching SystemExit? The test calls `so.observe_lifecycle`, not `observability.observe_lifecycle`. It wouldn't catch that. But not necessarily mutation-blind. If `so.observe_lifecycle` didn't catch SystemExit, test red. Good.

2. FAIL-CLOSED ATTRIBUTION: `test_records_only_against_matching_open_window`, `test_no_ids_no_window`, `test_records_drop_across_sessions`, `test_session_close_frees_registries`. These are concrete.

3. HASHED IDENTITY: `test_subject_hashes_session_and_is_bounded`, `test_subject_hashes_long_turn_id_without_aliasing`, `test_e2e_through_real_tool_dispatch` checks raw session not in subject. Bus filename is tested by using `_bus_file`. Good.

4. AUDIT FENCE: `test_mapping_by_facet` checks provenance length and facets; but doesn't test absence of prompts/args/results. `test_e2e` passes args and result but never asserts they're absent. This is a test honesty gap. Also `valid_signal` is called by bus; the tests don't assert all produced signals pass valid_signal. But if invalid, publish would raise and be caught; test would see no signals. So indirectly. But specifically for args/results, no test.

5. GATING: covered.

6. SEAM SAFETY: no direct test. `test_e2e` uses real dispatch but doesn't compare return values or plugin effects. `test_closed_gate...` checks has_hook false. No test that enabling observer doesn't change `invoke_hook` return or relay ordering. Could report as test honesty / missing coverage.

7. A3: `test_new_turn_finalizes_previous`. Good.

8. A4: `test_emitted_directive_binds_operator_budget` etc. Good.

9. SINGLE-THREADED BUS: no explicit test for threading. Not required maybe.

Potential concrete resource issue: `_BUSES.pop` in `_close_session` occurs after `_close_locked`. If `_close_locked` raises an exception (caught inside), `_BUSES.pop` still executes? In `_close_session`:
```
with _LOCK:
    window = _WINDOWS.pop(session_id, None)
    if window is not None and not window.closed:
        _close_locked(window)
    _BUSES.pop(session_id, None)
```
`_close_locked` catches exceptions internally (try/except). So it won't raise. Good. If it did raise, bus not popped; but it doesn't.

Potential issue: `_close_session` finalizes window regardless of gate, but only if window exists. If gate flips off before pre_llm_call, no window. Good.

Potential issue: `_close_session` uses `session_id` from kwargs; if empty, returns early. If session_id empty was used to open window? `_open_window` returns early if session_id empty. So no.

Potential issue: `_open_window` finalizes previous window on turn change, but doesn't remove bus. The bus persists across turns within session. Good.

Potential issue: What if a `pre_llm_call` arrives for the same session with same turn_id after the window was closed (e.g., because session close finalized)? `_close_session` popped window, so `_open_window` sees current None and opens a new window for a turn that may be over. Then a late post_tool_call could be recorded. Is that possible? Session close events are final; after finalize, no more hooks for that session. If a late hook arrives due to async, `_close_session` removed window, so dropped. Good.

Potential issue: `on_session_reset` may occur mid-session without closing bus? `_close_session` finalizes current window and frees bus. If a reset occurs and then a new pre_llm_call, `_bus_for` will reopen a new bus (same filename) and replay existing entries. The new window subject differs (turn_id). Signals/directives appended. That's fine.

Potential issue: `_operator_budget` reads config inside `_close_locked` while holding
