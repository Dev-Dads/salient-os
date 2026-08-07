# Red-team (consumer, pass=coding): moonshotai/kimi-k2.7-code

_finish=length seconds=49.4 usage={'prompt_tokens': 25255, 'completion_tokens': 12000, 'total_tokens': 37255, 'cost': 0.05993105, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.05993105, 'upstream_inference_prompt_cost': 0.01793105, 'upstream_inference_completions_cost': 0.042}, 'completion_tokens_details': {'reasoning_tokens': 13370, 'image_tokens': 0, 'audio_tokens': 0}}_

 We need red-team review of PR-H2 implementation. Need produce findings with ID/title/severity/location/concrete trigger/why/fix, then steelman and verdict. Need be rigorous, concrete. Let's analyze code thoroughly.

We have salience_observer.py, tests, vendored APIs, call site. We need find concrete defects under guarantees. Already fixed items listed. Need find missed issues.

Let's inspect code.

Key functions:
- _looks_off, _config_flag: reads config via read_raw_config_readonly; catches Exception, SystemExit. _config_flag returns False on unreadable config (fail-closed). For missing key returns default. For consume_compute default True. Good.
- salience_enabled: catches Exception, SystemExit from product_identity import. Good.
- handles_hook.
- observe_lifecycle: catches Exception, SystemExit. KeyboardInterrupt not caught intentionally. Good.
- _open_window: with _LOCK. If current open and turn_id different, _close_locked(current) (no budget arg => operator budget). Then creates new window if none/closed/different turn. Note: if current exists but closed and same turn_id? It creates new window? Actually condition `if current is None or current.closed or current.turn_id != turn_id:` creates new. If current.closed and same turn_id, it creates new window, replacing closed one. That may be okay? But maybe a closed window for same turn should not be reopened? Not in scope maybe.
- _record: with _LOCK. _bus_for and publish.
- _close_session: with _LOCK. Pops window, closes if not closed. Then pops _BUSES and _LAST_DIRECTIVE.
- _close_locked: if window.closed return. window.closed=True. budget param. If None budget = _operator_budget(). issue_policy, interpret, bus.emit, cache directive. On exception, pop _LAST_DIRECTIVE. Note: if window.closed already true, returns without doing anything. But if window.closed and _LAST_DIRECTIVE maybe stale? Not relevant.
- _bus_for: lazy open. directory.mkdir. get_hermes_home. Creates SalienceBus(path). Catches? Not in observer; called inside _record with try/except around publish only, not _bus_for. In _close_locked no try around _bus_for; _bus_for can raise from get_hermes_home SystemExit? Actually _close_locked catches Exception, SystemExit at bottom, so fine. But _bus_for creates directory and bus; if directory.mkdir raises? caught. If SalienceBus(path) raises? caught. Good.
- _operator_budget: reads config; caches. Called under _LOCK. Good.
- _consume_enabled: calls salience_enabled (catches) and _config_flag (catches). Good.
- _ensure_template_valid: under _LOCK in _resolve_bounded. It calls _operator_budget (safe). It issues policy and verify_policy. Catches Exception, SystemExit. Sets _TEMPLATE_VALIDATED False on failure. But note: if verify_policy raises, _TEMPLATE_VALIDATED False. Good. However, it always runs before _close_locked in _resolve_bounded. The docstring says caller holds _LOCK. Good.
- _directive_budget: handles dict and Directive object. Checks subject/policy_id truthy, budget int not bool and >=1. Good.
- _budget_from_disk: if session_id in _BUSES: return None. Then path.exists check. Then bus = _bus_for(session_id) (constructs and replays+verifies). Then directives = getattr(bus, "_directives", None). If not directives return None. payload = copy.deepcopy(directives[-1][1]). Then _LAST_DIRECTIVE[session_id]=payload. Return _directive_budget(payload).
Potential issues:
  - `if session_id in _BUSES` check before path.exists. If bus cached but no _LAST_DIRECTIVE (failed close), returns None => default. Good.
  - But if session_id not in _BUSES, path.exists check before constructing bus. If path doesn't exist, returns None. Good. No file created.
  - However, `_bus_for` creates directory if not exists. In _budget_from_disk, if path doesn't exist, it returns None before _bus_for, so no directory created. Good.
  - But if path exists, _bus_for creates directory anyway (mkdir exists ok). Fine.
  - Deepcopy of directives[-1][1] is dict. Good.
  - But `_directive_budget` expects dict or Directive. The payload from bus is dict. Good.
  - Potential issue: `_bus_for` caches bus in _BUSES. In _budget_from_disk, after constructing bus, it promotes payload to _LAST_DIRECTIVE and returns budget. Good.
  - But `_budget_from_disk` is called under _LOCK. `_bus_for` may be called under _LOCK. No reentrant lock issue.
- _resolve_bounded: with _LOCK. _ensure_template_valid(). floor = default if int>0 else _operator_budget(). Then window = _WINDOWS.get(session_id); if window not closed, _close_locked(window, budget=floor). Then directive = _LAST_DIRECTIVE.get(session_id); if directive not None return _directive_budget(directive); else return _budget_from_disk(session_id).
Potential issues:
  - If _close_locked succeeds and caches directive in _LAST_DIRECTIVE. Good.
  - If _close_locked fails (exception), it pops _LAST_DIRECTIVE. Then directive is None, falls to _budget_from_disk. But if bus was cached? If window existed and not closed, _close_locked failure could be due to _bus_for? It will pop _LAST_DIRECTIVE. Then _budget_from_disk sees session_id maybe in _BUSES (if bus existed before or created during _close_locked before failure). It returns None. So default. Good.
  - But what if _close_locked fails AFTER emitting directive but before caching? Actually emit and cache in same try block; if emit succeeds and cache assignment fails? unlikely. If exception after emit, _LAST_DIRECTIVE popped. The directive is on bus but not in cache. Then _budget_from_disk? Since session_id in _BUSES (bus created), returns None. So default. The emitted directive remains on bus for next cold restart. Fine.
  - A3: At call site :491, before pre_llm_call opens turn N window. At that moment, _WINDOWS[session_id] is turn N-1 window (opened at previous turn's pre_llm_call and not yet closed). _resolve_bounded closes it with floor=default (this turn's budget). Good. Then reads _LAST_DIRECTIVE. Good.
  - But what about the first turn? No window, _LAST_DIRECTIVE empty, _budget_from_disk maybe returns from previous session file if exists? But session may be new. If file exists from previous session with same session_id (hashed), cold recovery returns budget. Is that intended? The guarantee says restart fallback recovers last budget from session JSONL when in-memory cache empty (fresh process over existing session). For a new session with same id? Session IDs are presumably unique; but if reused, file exists. Could apply stale budget to new session. Out-of-scope? ADR 0002 exclusions maybe tail-truncation-across-reopen. Not sure.
  - The `floor = default if ... else _operator_budget()`. If default is non-int or bool, floor becomes operator budget. This means _close_locked floors at operator budget, not caller's default. In v0 pinned window, directive compute_budget equals floor (min=max). So if caller passes e.g. bool default, the close uses operator budget, and the returned budget is operator budget. But bounded_iterations returns the bad default unchanged at top (if not isinstance(default,int) or isinstance(default,bool): return default). So it won't reach _resolve_bounded for bool default. For non-int string, returns default. For float? not int => returns default. So floor only used for int defaults. Good.
  - However, if default is 0 or negative, floor = _operator_budget(). The consumer returns default (0) because budget from directive is >=1, but _resolve_bounded returns budget or None; bounded_iterations returns budget if not None else default. Since directive budget >=1, it will return positive budget, ignoring caller's 0. The docstring says default non-positive out of contract; consumer never returns <1 when directive present. That seems okay? But guarantee says never return budget <1. Good. But could it return a budget > default when default is 0? Yes. Not a defect.
- bounded_iterations: checks default int not bool; if not consume_enabled or not session_id returns default. Then _resolve_bounded. Returns budget or default. Catches Exception, SystemExit. Good.
Potential issue: It does not catch BaseException except SystemExit. KeyboardInterrupt propagates (intentional). Good.
Potential issue: If _resolve_bounded returns a non-int? _directive_budget returns int or None. So returns int. Good.
Potential issue: If default is a numpy int? isinstance(default, int) false for numpy.int64? Actually numpy int is not subclass of Python int? It is not a subclass of builtins.int? numpy.int64 is not subclass of int? Let's check: isinstance(np.int64(5), int) returns False? Actually numpy scalar types are not subclasses of Python int? I think np.int64 is not subclass of int; isinstance returns False. So bounded_iterations returns it unchanged. The host may pass numpy int? Not likely.
Potential issue: _consume_enabled returns _config_flag which returns default True for missing consume_compute. Good.

Now tests. Need identify mutation-blind tests, vacuous tests, circular tests. Also identify issues in tests.

Let's examine tests:
- test_applies_recorded_budget_verbatim: seeds _LAST_DIRECTIVE directly. Tests consumer reads cache. But doesn't exercise finalize-on-read. It would stay green if _resolve_bounded ignored _LAST_DIRECTIVE and returned default? Actually bounded_iterations returns budget if not None else default. If production line that reads _LAST_DIRECTIVE were sabotaged (e.g., always returns None), test would fail because returns default 10 not 7. So not mutation-blind for that line. But it only seeds cache; doesn't exercise cold recovery or finalize.
- test_no_reclamp_directive_below_default: seeds cache. Good.
- test_no_reclamp_directive_above_default: seeds cache. Good.
- test_directive_budget_guard_shapes: unit test for _directive_budget. Good.
- test_finalize_on_read_closes_prior_window: opens u1, records, then bounded_iterations. Asserts window closed, bus has directive, applied==10. Good. But if _close_locked didn't cache _LAST_DIRECTIVE? bounded_iterations would fall to _budget_from_disk (bus cached => None) => default 10. The assert applied==10 still passes because default==10. Wait! The test uses default 10, and v0 pinned window means directive budget equals floor=default=10. So applied==10 regardless of whether it read cache or returned default. This test cannot distinguish. It asserts window closed and bus directive exists, but applied==10 is vacuous. However, the next test second_read_returns_cached_directive_not_default uses default 20 then 99 to distinguish. Good.
- test_second_read_returns_cached_directive_not_default: first default 20 closes u1 at floor 20, caches budget 20. Second default 99 returns 20. This catches if cache not written. Good.
- test_failed_close_fails_open_not_stale: seeds _LAST_DIRECTIVE with budget 7, opens u2, records, monkeypatches interpret to raise. bounded_iterations should return default 10, not 7. It also catches _LAST_DIRECTIVE.pop in except. Good. But note: They monkeypatch `so.interpret`, not `interpret` imported in salience_observer? In salience_observer.py, `from salienceos.interpreter import interpret` binds name `interpret` in module. Monkeypatching `so.interpret` replaces that name. _close_locked uses `interpret(policy,...)` so uses module attribute. Good.
- test_three_turns_read_prior_not_stale: This is good. It models real cadence. But note: It calls bounded_iterations("s",10) with no window; returns default 10. Then opens u1. Then bounded_iterations("s",20) finalizes u1. Then opens u2. Then bounded_iterations("s",30) finalizes u2. Asserts applied sequence (10,20,30). Also checks directives_for(u1)==20 and u2==30. Good. This catches A3 staleness. However, default values 10,20,30 are also equal to floor; if finalize-on-read didn't happen and instead read stale _LAST_DIRECTIVE? Let's see: after first bounded_iterations, _LAST_DIRECTIVE empty. After open u1, window open. Second bounded_iterations finalizes u1 and caches budget 20. If finalize-on-read missing, _LAST_DIRECTIVE still empty, falls to _budget_from_disk. No bus? Actually _record_write caches bus. But no file? In memory bus exists, _budget_from_disk sees session in _BUSES and returns None => default 20. So applied2 would be 20 anyway. Hmm. But the bus directive for u1 wouldn't exist. The later assertion `directives_for(u1)==20` would fail. So catches missing finalize. For applied3, if it read u1 stale instead of u2: after second bounded_iterations, u1 closed and cached 20. Then open u2, record. Third bounded_iterations: if _resolve_bounded didn't close u2 and just returned _LAST_DIRECTIVE (20), applied3 would be 20 not 30. So catches. Good.
- test_restart_recovers_budget_from_disk: produce, close session, reset, bounded_iterations returns 7. Good.
- test_cold_recovery_is_cached_for_second_read: Good.
- test_cold_recovery_reads_newest_directive: emits two directives directly to bus, reset, bounded_iterations returns 9. Good. But it uses _bus_for before reset; emits directly. Then reset clears _BUSES and _LAST_DIRECTIVE but file remains. Then bounded_iterations cold recovers. Good.
- test_cold_recovery_promote_is_deepcopied_not_aliased: Good.
- test_restart_corrupt_tail_fails_closed_to_default: Good.
- test_restart_with_no_file_returns_default: Good. But it uses _use_config with tmp_path; asserts no salience dir exists. Since bounded_iterations returns default before any _bus_for? It checks not consume_enabled? consume_enabled true. session_id "never-seen". _resolve_bounded: _ensure_template_valid (calls _operator_budget -> read config -> returns default 25? Actually config has no agent.max_iterations, so _operator_budget returns _DEFAULT_BUDGET=25). Then window none. _LAST_DIRECTIVE empty. _budget_from_disk: session_id not in _BUSES, path doesn't exist => returns None. So bounded_iterations returns default 10. No directory created. Good.
- test_restart_nontail_tamper_fails_closed: produce two directives, tamper non-tail hash, reset, bounded_iterations returns default. Good.
- test_restart_fallback_skipped_when_bus_cached: reset, _bus_for (creates bus from file but no _LAST_DIRECTIVE), bounded_iterations returns default. Good. But note: _bus_for replays and verifies file; if file corrupt it would raise? But file is good. Good.
- gating tests: okay.
- test_empty_session_returns_default: session_id "" => returns default. Good.
- test_non_int_default_returned_unchanged: parametrize string, bool, None, float. bounded_iterations returns unchanged. Good.
- test_applied_value_propagates_into_iteration_budget: seeds cache, bounded_iterations returns 7, IterationBudget(7). Good.
- test_call_site_precedes_budget_rebuild: structural adjacency. Good.
- test_consumer_cache_freed_on_session_close: open, record, bounded_iterations caches directive, close_session, assert not in _LAST_DIRECTIVE. Good.
- test_template_validation_flagged_but_consumption_survives: monkeypatch so.verify_policy to False. _ensure_template_valid sets _TEMPLATE_VALIDATED False. But _close_locked uses `interpret` which internally calls `verify_policy` imported from salienceos.interpreter.policy, not so.verify_policy. So real close succeeds. Good. But note: _ensure_template_valid calls `bool(verify_policy(policy, _POLICY_KEY))`. If monkeypatched to False, _TEMPLATE_VALIDATED False. Good.
- test_bounded_iterations_never_raises_on_broken_home: monkeypatch get_hermes_home to SystemExit. bounded_iterations returns default. Let's trace: _consume_enabled true. _resolve_bounded under lock calls _ensure_template_valid -> _operator_budget -> read_raw_config_readonly (mocked returns cfg) no issue. Then _close_locked? window none. directive none. _budget_from_disk: session_id not in _BUSES. Path? It calls get_hermes_home -> SystemExit. This is inside _budget_from_disk, not wrapped in try. It will propagate to bounded_iterations' except (Exception, SystemExit) and return default. Good. But note: _budget_from_disk holds _LOCK; exception propagates out of with _LOCK? The lock will be released by context manager? Actually `with _LOCK:` in _resolve_bounded; if exception propagates, lock released. Good.

Now need find concrete defects. Let's think of subtle issues.

1. `_close_locked` caches directive object directly (not copy) from interpret. interpret returns Directive (frozen dataclass). _LAST_DIRECTIVE holds reference to that object. Later _directive_budget reads attributes. Fine. But in _budget_from_disk, deepcopy of dict. Good.

2. `_directive_budget` for Directive object uses getattr(source, "subject", None). For a real Directive, subject is string. If subject is empty string (hard deny), returns None. Good. But what about a dict with subject key missing? source.get returns None. Good.

3. `_directive_budget` treats bool as malformed. Good.

4. `_directive_budget` does not validate that `subject` and `policy_id` are strings. Could a dict have subject as e.g. list? It checks truthiness only. If subject is non-empty list, `not subject` false, so returns budget. But bus replay validation `_valid_directive_payload` requires strings. So on-disk dicts are validated. In-memory Directive subject is string by construction. So not a real path. But if someone seeds _LAST_DIRECTIVE with a dict with non-string subject, _directive_budget would accept. Not a security issue.

5. `_ensure_template_valid` uses global _TEMPLATE_VALIDATED. It is set to True/False. If verify_policy returns True, _TEMPLATE_VALIDATED = True. If False, False. But what if verify_policy returns non-bool truthy? `bool(...)` ensures bool. Good. However, if `_TEMPLATE_VALIDATED` is False, subsequent calls still run the probe each time? It checks `if _TEMPLATE_VALIDATED is not None: return`. So once set, no rerun. Good.

6. `_operator_budget` caches. It reads config once. If config changes during process, not reflected. Acceptable.

7. `_operator_budget` returns _DEFAULT_BUDGET if config unreadable or no positive int. It catches Exception, SystemExit. But what if read_raw_config_readonly returns a non-dict (e.g., None)? It sets cfg={}. Good.

8. `_operator_budget` iterates paths; if node is bool? `isinstance(node, int) and not isinstance(node, bool)` excludes bool. Good.

9. `_operator_budget` does not validate node >0? It checks node > 0. Good.

10. `_operator_budget` could return a very large int, causing issue_policy to accept? verify_policy checks min_budget int and 0<=min<=max. No upper bound. Could cause huge budget. But that's operator config; not a defect.

11. `_close_locked` with budget floor: issue_policy uses min_budget=budget, max_budget=budget. If budget is huge, fine. interpret with no ATTENTION signals returns min_budget. Good.

12. `_resolve_bounded` floor: default if int>0 else _operator_budget(). If default is a plain int but > max? In v0 min=max=floor, so directive budget = floor. This means if caller passes a default different from operator budget, the directive budget equals default. That's intended (A4). But if default > operator budget? The policy floor is higher than operator budget; but consumer applies verbatim. That's okay. However, the recorded directive now has compute_budget = default, which may be higher than operator config. Is that allowed? The guarantee says A4 binds policy floor to caller-passed default. Yes.

13. Potential A3 issue: In _open_window, when a new turn starts, it closes current window with _close_locked(current) (no budget arg => operator budget). But the consumer's finalize-on-read at bounded_iterations is supposed to close prior window with floor=default. However, _open_window may close the prior window first if a new turn's pre_llm_call fires before bounded_iterations? Wait ordering: bounded_iterations at :491 before pre_llm_call at :1054. So bounded_iterations runs first. It closes N-1 window with floor=default. Then pre_llm_call opens N window. So _open_window's rollover close is for N-2? Let's examine: At turn N start, _WINDOWS[session_id] is window for turn N-1 (opened at previous pre_llm_call). bounded_iterations closes it. Then pre_llm_call opens turn N window. In _open_window, current = window N-1 (closed). It checks `if current is not None and not current.closed and current.turn_id != turn_id:` — current is closed, so no close. Then creates new window. Good. If somehow bounded_iterations didn't run (consumer kill switch off), pre_llm_call opens N window and _open_window will close N-1 window at operator budget. That's produce path's own cadence. Fine.

But what about `on_session_end` etc. They call _close_session, which closes current window if not closed. Good.

14. Concurrency: _LOCK is non-reentrant. bounded_iterations -> _resolve_bounded acquires _LOCK. Inside, _ensure_template_valid, _close_locked, _bus_for, _budget_from_disk. None of them try to acquire _LOCK. Good. But _close_locked calls _operator_budget, which doesn't acquire lock (assumes held). Good. However, _close_locked calls _bus_for, which doesn't acquire lock. Good. No reentrant deadlock.

But wait: `_reset_for_tests` acquires _LOCK and clears globals. Tests call it. Not production.

15. Resource leak: _LAST_DIRECTIVE freed on session close. Good. But what about _WINDOWS and _BUSES if session close event missing? Not under review.

16. FAIL-OPEN: Need hunt for BaseException/SystemExit path from host API escaping except (Exception, SystemExit). The code catches (Exception, SystemExit) in many places. Does any host API call not wrapped? Let's list host APIs called:
- read_raw_config_readonly: wrapped in _config_flag, _operator_budget, bounded_iterations? _operator_budget catches. _config_flag catches.
- get_hermes_home: called in _bus_for and _budget_from_disk. _bus_for is called inside _record (wrapped in try around publish only? Actually _record has `with _LOCK:` then `self_bus = _bus_for(session_id)` not wrapped; then `self_bus.publish(signal)` wrapped in try/except. If _bus_for raises SystemExit, it propagates out of _record. observe_lifecycle catches (Exception, SystemExit) around the dispatch, so contained. In _close_locked, _bus_for is called inside try block that catches (Exception, SystemE
xit). In _budget_from_disk, _bus_for called inside function; caller _resolve_bounded is inside bounded_iterations try/except. So contained.
- Path.mkdir: inside _bus_for, contained as above.
- SalienceBus construction: inside _bus_for, contained.
- product_identity import: wrapped.
- os.urandom: not host API, can raise? not relevant.
- copy.deepcopy: can raise? caught in _budget_from_disk by caller? _budget_from_disk not itself try/except; _resolve_bounded calls it inside try? Actually _resolve_bounded doesn't have its own try; bounded_iterations wraps _resolve_bounded in try/except. So deepcopy exception caught.
- issue_policy, interpret, verify_policy: vendored, not host API. They can raise; caught in _close_locked, _ensure_template_valid.
- bus.emit, bus.publish: caught in _close_locked and _record.
- bus.directives_for: not used in consumer except tests.
- bus.verify_chain: tests.

What about `SystemExit` from `read_raw_config_readonly` inside `_operator_budget`? It catches. Good.

What about `BaseException` subclasses like `GeneratorExit`? Not relevant.

What about `KeyboardInterrupt`? Deliberately not caught. Fine.

Potential fail-open: `bounded_iterations` catches Exception, SystemExit. But if `_resolve_bounded` returns a non-integer (e.g., None) and `budget if budget is not None else default` returns default. Good. If it returns a bool? _directive_budget filters bool. Good.

Potential fail-open: `_consume_enabled` returns False if salience_enabled raises? salience_enabled catches. Good. But `_consume_enabled` not wrapped in try in bounded_iterations; if _config_flag raises BaseException? _config_flag catches Exception, SystemExit. Good.

Potential fail-open: `_config_flag` catches Exception, SystemExit from read_raw_config_readonly. But what if `cfg.get("salience")` raises? cfg is dict or {}. If cfg is a custom mapping whose .get raises? Unlikely. If salience is not dict, sets None. Good.

Potential fail-open: `_looks_off` for string uses `.strip().lower()`. If value is a numpy string? Not relevant.

Potential fail-open: `bounded_iterations` returns `default` if not isinstance(default, int) or isinstance(default, bool). If default is a bool, returns True/False. The host may then set max_iterations = True, and IterationBudget(True) treats as 1? Actually bool is subclass of int, IterationBudget(True) sets max_total=True (1). But bounded_iterations returns default unchanged, which is bool. The guarantee says never return budget <1; bool True is 1, okay. But if default False, returns False, max_iterations=0, brick. However the caller is supposed to pass positive int; the function returns default unchanged for non-int; bool is non-int? Actually code: `if not isinstance(default, int) or isinstance(default, bool): return default`. So for bool default, returns default (False). That could brick. But is bool default possible? The host passes agent.max_iterations, which should be int. If config sets max_iterations: false, _operator_budget filters bool and returns default 25. But bounded_iterations's default parameter is agent.max_iterations. If agent.max_iterations is bool False, bounded_iterations returns False. Then agent.max_iterations = False; IterationBudget(False) => max_total=0. This is a path where consumer returns budget <1 (False=0). The guarantee says never return budget <1. The early return intended to leave caller's value unchanged, but if caller's value is malformed bool False, it propagates the brick. Should the consumer treat non-int/bool default as absent and return at least 1? The docstring says default is host's positive iteration budget; non-positive default out of contract. But FAIL-OPEN/NEVER-BRICK says must never return budget <1. If default is bool False, returning default violates. However, is this reachable? Possibly if config parsing sets agent.max_iterations to a bool. The host's config loader may coerce. But the consumer could defensively clamp. The test `test_non_int_default_returned_unchanged` explicitly asserts that bool default returns unchanged. That test codifies a violation of FAIL-OPEN? Let's examine: parametrize bad_default = [ "x", True, None, 3.5 ]. For True, returns True (1) okay. For False would return False (0) brick. They only test True. But the function returns any bool unchanged. The guarantee says never return budget <1. A bool False default would produce 0. Is that a concrete defect? Need concrete trigger: call bounded_iterations("s", False). It returns False. The host would set max_iterations=False, IterationBudget(0). That bricks. But the caller contract says default positive int. The function's early return is meant for non-int defaults; bool is not int in isinstance? Actually `isinstance(True, int)` is True, but `isinstance(True, bool)` is True, so condition `not isinstance(default, int) or isinstance(default, bool)` => not True or True => True, returns default. So bool treated as non-int. For False, returns 0. This is a concrete path to return <1. Should be fixed: if default is bool or not int, return max(default,1)? But docstring says leave caller's value. The guarantee trumps. Maybe treat bool as absent => return default? But default is bool. Better: `if not isinstance(default, int) or isinstance(default, bool): return default if default is True else 1`? But that changes semantics. Or simply: `if not isinstance(default, int) or isinstance(default, bool) or default < 1: return default`? No, that still returns <1. Need ensure return >=1. Could return `default if isinstance(default,int) and not isinstance(default,bool) and default >= 1 else 1`? But then for string default returns 1, altering caller's value. The function signature says returns int. Maybe the host always passes int. But defensive. The test expects non-int defaults returned unchanged. This is a tension. The guarantee says never return <1. For non-int defaults, returning unchanged may be <1 (False) or non-int (string). The host then assigns to max_iterations; IterationBudget expects int but will accept string? It sets max_total = string; later comparisons with int may raise TypeError. But bounded_iterations catches? No, after return. The consumer's job is to return a safe budget. The early return seems wrong for FAIL-OPEN. However, the prompt says "any path where ... returns a budget < 1 / non-int that would set max_iterations to 0 and brick the agent." So returning non-int could cause TypeError later? Actually `agent.max_iterations = _salience_observer.bounded_iterations(..., default=agent.max_iterations)`. If default is string "x", bounded_iterations returns "x". Then `IterationBudget("x")` => max_total="x". consume() compares self._used >= self.max_total; if _used=0 int, comparison int >= str raises TypeError, crashing the turn. So returning non-int is also a fail-open violation (propagates crash). The function catches internal exceptions but returns a value that crashes host. This is a concrete defect. But is it reachable? The host passes agent.max_iterations. If config sets it to a string, maybe host config loader errors earlier. But the function's contract says default is int. The test explicitly tests non-int defaults and expects unchanged. That test is arguably wrong under FAIL-OPEN. But the prompt says "NEVER return a budget < 1 / non-int that would set max_iterations to 0 and brick the agent." So if the function can return non-int, it's a defect. However, maybe the host's `agent.max_iterations` is always int. But the consumer is a choke point; should sanitize output. The early return for non-int default is a deliberate design choice to "leave the caller's value". But it violates guarantee. Need decide if report.

Let's see if there are other concrete defects more severe.

17. `_resolve_bounded` uses `floor = default if (isinstance(default, int) and not isinstance(default, bool) and default > 0) else _operator_budget()`. If default is a large int, floor = default. Good. If default is 0, floor = operator budget. But bounded_iterations will return default (0) if no directive? Actually if directive present, returns budget>=1. If no directive, returns default 0. So brick. But default 0 is out of contract. However, the function could ensure return >=1. But the guarantee says never return <1. If caller passes 0, returning 0 bricks. Should fail-open to at least 1? The guarantee says "MUST return `default` unchanged" for absence etc. But also "must NEVER return a budget < 1". These conflict if default <1. The resolution: if default <1, treat as absent and return at least 1? The current code returns default unchanged for non-int and for int with no directive. For int default <=0, it passes to _resolve_bounded, floor becomes operator budget, directive budget may be operator budget >=1, so returns >=1. But if no directive, returns default 0. So a caller passing default 0 and no prior directive bricks. Is default 0 possible? If config max_iterations=0? Operator budget would be _DEFAULT_BUDGET=25 because _operator_budget filters >0. But the caller's default is agent.max_iterations which might be 0 if config sets it. The host might allow 0? Not likely. But the consumer should not propagate 0. The guarantee says never return <1. So we can report that `bounded_iterations` returns default unchanged even when default is non-positive int, violating NEVER-BRICK. Concrete trigger: fresh session, call bounded_iterations("s", 0). It returns 0. Host sets max_iterations=0. Brick. But test? No test covers default 0. The docstring says non-positive default out of contract. But guarantee is explicit.

However, the prompt's calibration: "A finding needs a CONCRETE triggering input or call sequence; no concrete trigger => LOW at most." We have concrete trigger. Is it a real defect? The function is supposed to fail open. Returning 0 is not fail-open. But the caller contract says default positive. In safety-critical code, defensive clamping is warranted. I'd report as MEDIUM/HIGH? It depends on likelihood. If host config can set max_iterations=0, high. But likely not. Maybe LOW? Let's hold.

18. Another issue: `_consume_enabled` uses `_config_flag("consume_compute", True)`. If `salience_enabled()` is False, returns False. Good. But `_config_flag` for consume_compute reads config. If config has `salience.consume_compute: false`, returns False. Good.

19. A3 staleness: Is there any path where bounded_iterations reads turn N's own directive? At :491, pre_llm_call hasn't opened N window. _resolve_bounded closes N-1 window. If N-1 window already closed (e.g., due to _open_window rollover? Not at this point), reads _LAST_DIRECTIVE. Could _LAST_DIRECTIVE contain a directive from turn N? Only if a window for N was opened and closed earlier within same turn, which doesn't happen. So no.

But what about `_budget_from_disk` after reset? It reads newest directive from file. If the file contains directives from multiple sessions with same hash? The hash is sha256(session_id). If session_id reused, file may contain older directives. It returns newest. Could be from a prior session, but considered same session id. Out-of-scope maybe.

20. DENY-SHAPED / NO-RE-CLAMP: `_directive_budget` treats hard-deny as absent. Good. But what about a directive with subject/policy_id present and compute_budget 0? It returns None. Good. What about negative budget? None. Good.

But `_directive_budget` for a Directive object uses `getattr(source, "compute_budget", None)`. If source is a Directive with compute_budget 0, returns 0 then None. Good.

What about a dict with compute_budget as a numpy int? Not bool, isinstance int? numpy.int64 is instance of int? Actually numpy integer scalar is subclass of np.generic, not Python int. isinstance(np.int64(5), int) returns False? Let's verify mentally: In CPython, np.int64 is not a subclass of int; isinstance(np.int64(5), int) returns False. So _directive_budget returns None. That's safe (absent). Not a defect.

What about a dict with compute_budget as a string "5"? Returns None. Good.

What about a Directive object with compute_budget as bool? Directive dataclass typed int, but Python doesn't enforce. If someone constructs Directive(compute_budget=True), _directive_budget returns None. Good.

No re-clamp: The consumer returns budget verbatim. Good.

21. RESTART-FALLBACK INTEGRITY: `_budget_from_disk` constructs bus via `_bus_for`, which replays+verifies. Good. It reads `bus._directives[-1][1]`. The vendored bus's `_replay` appends directives in order. Good. It deepcopies. Good.

Potential issue: `_budget_from_disk` checks `if session_id in _BUSES: return None`. But what if `_BUSES` has a bus for session_id that was created by `_bus_for` in a prior call but the file has since been modified/corrupted on disk? Since bus is cached, it won't re-verify current file. The guard returns None. That's intended: warm bus means no cold recovery. But if the cached bus is stale (file modified externally), the consumer doesn't use it. Fine.

Potential issue: `_budget_from_disk` uses `path.exists()` before constructing bus. If file exists but is empty, SalienceBus._replay loads no lines, _directives empty, returns None. Good. If file exists but contains only signals no directives, _directives empty, returns None. Good.

Potential issue: `_budget_from_disk` deepcopies payload, but then `_directive_budget` reads it. If deepcopy fails (e.g., circular ref), exception caught by bounded_iterations. Not likely.

Potential issue: `_budget_from_disk` promotes raw dict reference? It deepcopies, so no alias. Good.

Potential issue: TOCTOU between verify and read. The bus replay builds _directives in memory; reading _directives[-1][1] after replay is not subject to file modification. Good.

Potential issue: `_bus_for` creates directory and file path; if `get_hermes_home()` raises SystemExit, caught by caller. But `_budget_from_disk` first computes path using `Path(get_hermes_home()) / "salience" / ...`. If get_hermes_home raises SystemExit, not caught within _budget_from_disk; caught by bounded_iterations. Good. But `_budget_from_disk` also calls `_bus_for(session_id)` which recomputes get_hermes_home; if it raises, caught by bounded_iterations. No TOCTOU issue.

Potential issue: `_budget_from_disk` could create a spurious file? It returns None if path doesn't exist before _bus_for. Good. But if path exists, _bus_for creates directory. Fine. No spurious file.

Potential issue: `_bus_for` is called in `_budget_from_disk` even if `_LAST_DIRECTIVE` empty but bus not cached. It creates bus and caches it. That's intended.

22. CONCURRENCY: Need check if `_LOCK` is non-reentrant and any path re-acquires. `_resolve_bounded` holds lock, calls `_ensure_template_valid` (no lock), `_close_locked` (no lock), `_directive_budget` (no lock), `_budget_from_disk` (no lock, calls _bus_for no lock). Good. But `_close_locked` calls `_operator_budget` which doesn't acquire lock. Good.

But `_reset_for_tests` acquires lock and clears. Not production.

Potential deadlock: bounded_iterations is called from agent turn thread. observe_lifecycle hooks may be called from other threads? The contract says single-threaded bus; all registry access under _LOCK. If observe_lifecycle and bounded_iterations run concurrently on different threads, they serialize on _LOCK. No deadlock. However, if observe_lifecycle calls `_record` which calls `_bus_for` under lock, and bounded_iterations calls `_resolve_bounded` under lock, no nested lock. Good.

Potential issue: `_close_session` holds lock and calls `_close_locked`. _close_locked doesn't acquire lock. Good.

Potential issue: `_open_window` holds lock and calls `_close_locked`. Good.

Potential issue: `_record` holds lock and calls `_bus_for`. Good.

No reentrant deadlock.

Resource: `_LAST_DIRECTIVE` freed on session close. Good. But `_WINDOWS` and `_BUSES` popped. However, if a session close event never fires, they leak. Not under review? The guarantee says must be freed on session close like _BUSES. It is. Good.

23. HONESTY: docstring/config text. Module docstring says consumer is behavior-preserving in v0 because policy pins min==max and ATTENTION unmapped. Good. The cli-config example says same. No overstatement? It says "the directive echoes the operator's own configured budget, so consumption is behavior-preserving". True. It says "the ONLY behavior-changing consumer". True. It says "between-turn only". True.

But the module docstring says "Hard guarantees, by construction: Produce-only observer / no decision-path change." Then says consumer is separate. Fine.

Potential honesty issue: `_ensure_template_valid` docstring says "Caller holds _LOCK". Actually it's called inside _resolve_bounded which holds lock. Good.

Potential honesty issue: `_operator_budget` docstring says "only ever called from _close_locked while _LOCK is held". But _ensure_template_valid also calls _operator_budget under _LOCK. The docstring is slightly incomplete but not misleading.

Potential honesty issue: `_budget_from_disk` docstring says "Runs ONLY when no bus is cached". True. "On the cold path, constructing the bus replays AND verifies the whole chain, raising on a corrupt/tampered tail (caught by the caller ⇒ default)." True. "The recovered directive is deep-copied and promoted into _LAST_DIRECTIVE (a state-mutating side effect) so this once-per-restart cold path need not repeat on a second read." True.

Potential honesty issue: `bounded_iterations` docstring says "Precisely, it applies the most recently RECORDED turn's directive: normally that is the immediately prior turn, but a turn that aborts before opening its window records nothing, so an earlier turn may be the latest." This is accurate.

Potential honesty issue: The comment at call site says "Fails open: the consumer self-contains Exception and SystemExit and returns the operator's own value unchanged when the subsystem is off, kill-switched, or has no prior directive". True.

24. TEST HONESTY: Need identify tests that are mutation-blind, vacuous, circular, or only seed cache.

Let's examine each test for mutation blindness.

- test_applies_recorded_budget_verbatim: seeds _LAST_DIRECTIVE. If production line `_LAST_DIRECTIVE[window.session_id] = directive` in _close_locked is removed, this test still passes because it seeds directly. But it tests the consumer applies recorded value. It's not mutation-blind for the line that reads _LAST_DIRECTIVE? If `_resolve_bounded` ignored _LAST_DIRECTIVE and always returned default, test would fail. So it exercises read path. But it doesn't exercise the produce-close path. That's okay for a consumer unit test. Not mutation-blind for the read.

- test_finalize_on_read_closes_prior_window: As noted, applied==10 is vacuous because default==10 and v0 directive budget==default. But it asserts window closed and bus directive. It catches missing finalize via bus directive. However, if `_close_locked` wrote to bus but didn't cache _LAST_DIRECTIVE, applied would still be 10 (default) because _budget_from_disk sees bus cached and returns None. Wait: after finalize, bus is cached. _LAST_DIRECTIVE empty. _budget_from_disk checks session_id in _BUSES -> True -> returns None. So bounded_iterations returns default 10. The test's applied==10 passes. It does not catch missing cache. The next test catches missing cache. So this test is partially vacuous for applied value, but not for close side effects. It is not mutation-blind for the close side effects.

- test_three_turns_read_prior_not_stale: Good.

- test_second_read_returns_cached_directive_not_default: Good.

- test_failed_close_fails_open_not_stale: Good.

- test_restart_recovers_budget_from_disk: Good.

- test_cold_recovery_is_cached_for_second_read: Good.

- test_cold_recovery_reads_newest_directive: Good.

- test_cold_recovery_promote_is_deepcopied_not_aliased: Good.

- test_restart_corrupt_tail_fails_closed_to_default: Good.

- test_restart_with_no_file_returns_default: Good.

- test_restart_nontail_tamper_fails_closed: Good.

- test_restart_fallback_skipped_when_bus_cached: Good.

- test_consume_kill_switch_leaves_budget_and_window_untouched: Good.

- test_subsystem_off_returns_default: seeds cache. If production ignored cache and returned default, still passes. But it's testing gate off. It would stay green if the gate check were removed? No, gate off returns default; if gate check removed and cache read, returns 7, fails. So catches gate. Good.

- test_real_gate_off_edition_returns_default: Good.

- test_empty_session_returns_default: Good.

- test_non_int_default_returned_unchanged: This test codifies returning non-int defaults unchanged, which is arguably a bug. It would stay green if the early-return line were removed? If line removed, for string default, _resolve_bounded would be called; floor = _operator_budget (since default not int), then no window, _budget_from_disk maybe returns None, bounded_iterations returns default? Wait if early-return removed, the code would proceed: `if not _consume_enabled() or not session_id: return default` (session_id "s", consume enabled true). Then `budget = _resolve_bounded(session_id, default)`. _resolve_bounded floor = _operator_budget (25). No window. _budget_from_disk: session_id not in _BUSES, path doesn't exist => None. budget=None. Return default. So string default still returned unchanged. So test stays green even if early-return removed. For bool True, _resolve_bounded floor=_operator_budget, no directive, returns default True. So test stays green. For None, same. For float 3.5, same. Thus the test is vacuous: it doesn't actually verify the early-return branch; the same result occurs via the normal path because no directive exists. Unless _consume_enabled false? No. So test is mutation-blind for the early-return line. It also codifies unsafe behavior. We can report this as a test honesty issue.

- test_applied_value_propagates_into_iteration_budget: seeds cache. Good.

- test_call_site_precedes_budget_rebuild: structural. Good.

- test_consumer_cache_freed_on_session_close: Good.

- test_template_validation_flagged_but_consumption_survives: Good.

- test_bounded_iterations_never_raises_on_broken_home: Good.

Potential mutation-blind tests:
- `test_finalize_on_read_closes_prior_window` applied==10 is vacuous but side effects catch. Could be stronger by using a default different from operator budget? But fixture config has no agent.max_iterations, operator budget=25. If default=10, directive budget=10. If default were 15, directive budget=15, and returning default vs cached could be distinguished. But they used 10. However, the next test distinguishes. Not a major issue.

- `test_non_int_default_returned_unchanged` is mutation-blind and codifies unsafe behavior. Report.

- `test_applies_recorded_budget_verbatim` only seeds cache; doesn't exercise finalize. But that's okay.

- `test_three_turns_read_prior_not_stale`: The first bounded_iterations default 10 with no window returns default. If _resolve_bounded didn't exist and returned None, still default. Not a defect. The later parts catch.

- `test_cold_recovery_reads_newest_directive`: It emits directives directly to bus using `_make_directive` and `bus.emit`. It doesn't go through produce path. That's fine for testing recovery.

- `test_restart_fallback_skipped_when_bus_cached`: It calls `_bus_for("s")` after reset, which constructs bus and replays file. Then asserts bounded_iterations returns default. Good.

Now, are there any concrete production defects beyond the non-int/bool default return? Let's dig deeper.

Potential issue: `_directive_budget` uses `source.get("compute_budget")` for dict. If the dict has `compute_budget` key with value None, returns None. Good. If missing, None. Good.

Potential issue: `_directive_budget` doesn't check `policy_id` type. If dict has policy_id as non-empty non-string, it accepts. But bus validation ensures string. Not a path.

Potential issue: `_ensure_template_valid` uses a dummy subject "salience.template.probe". It issues policy with min_budget=budget etc. verify_policy checks signature and shape. Good. But it doesn't use the real interpreter? It calls verify_policy only, not interpret. That's fine.

Potential issue: `_ensure_template_valid` catches Exception, SystemExit and sets False. But if it raises SystemExit from _operator_budget? _operator_budget catches. If issue_policy raises? caught. If verify_policy raises? caught. Good.

Potential issue: `_TEMPLATE_VALIDATED` is not freed in `_reset_for_tests`? It is set to None. Good.

Potential issue: `_operator_budget` uses `from hermes_cli.config import read_raw_config_readonly` inside function. If import raises (e.g., circular), caught. Good.

Potential issue: `_config_flag` catches Exception, SystemExit. But `cfg.get("salience")` if cfg is a dict subclass that raises on get? Not relevant.

Potential issue: `_looks_off` for int excludes bool. `isinstance(value, int) and not isinstance(value, bool)`. Good. For string, strips and lowercases. If value is bytes? Not in config. Returns False (treated as on). Fine.

Potential issue: `salience_enabled` catches Exception, SystemExit from `from product_identity import IS_QUORUM_EDITION`. But if `product_identity` module exists and `IS_QUORUM_EDITION` is a property that raises? getattr at module level? Actually `from product_identity import IS_QUORUM_EDITION` can raise if attribute access raises. Caught. Good.

Potential issue: `_consume_enabled` calls `salience_enabled()` which catches. Then `_config_flag`. If `_config_flag` catches, good.

Potential issue: `bounded_iterations` catches Exception, SystemExit. But if `_resolve_bounded` raises a `BaseException` like `SystemExit` from `_bus_for`? Caught. Good. If it raises `KeyboardInterrupt`, not caught, intentional.

Potential issue: `_resolve_bounded` holds lock while calling `_budget_from_disk`, which may call `_bus_for` and replay a large file, blocking all produce hooks. This is a performance issue, not a correctness defect. Could be considered resource/concurrency? Unbounded file growth? The JSONL grows per session; replay on cold start reads whole file. Not unbounded memory under guarantee? The guarantee mentions unbounded growth of _LAST_DIRECTIVE freed on close. The bus file grows indefinitely per session; but out of scope? Not listed. Could mention as LOW.

Potential issue: `_close_locked` catches Exception, SystemExit and pops _LAST_DIRECTIVE. But if the exception occurs during `_bus_for` before any directive emitted, popping is harmless. If exception occurs after emit but before caching, popping removes any prior cached directive. Good. But what if exception occurs after caching? The try block includes cache assignment; if cache assignment succeeds, no exception. So fine.

Potential issue: `_close_locked` sets `window.closed = True` before try. If exception occurs, window remains closed. That's idempotent. Good. But if a prior _LAST_DIRECTIVE existed and close fails, it pops it. Good.

Potential issue: `_open_window` closes current window on turn rollover with `_close_locked(current)` and no budget arg. This uses operator budget as floor. But at turn rollover in produce path (if consumer kill switch off), the prior window is closed at operator budget, not at the next turn's default. That's produce path's own cadence; the directive is recorded with operator budget. The consumer is off, so not used. Fine.

Potential issue: A3 if consumer kill switch off: bounded_iterations returns default without closing prior window. Then pre_llm_call opens N window and closes N-1 at operator budget. The directive for N-1 is recorded but not consumed. Next turn when consumer on, _LAST_DIRECTIVE has N-1 directive. bounded_iterations at turn N+1 closes N window (if open) and reads N-1 directive. Wait if consumer off for turn N, N-1 closed at operator budget by pre_llm_call. _LAST_DIRECTIVE has N-1 directive. Turn N+1 consumer on: _WINDOWS has N window (opened at turn N pre_llm_call). bounded_iterations closes N window with floor=default(N+1), reads _LAST_DIRECTIVE which is N-1 directive. So turn N+1 applies N-1, which is correct (one turn stale because N had no consumption). That's fine.

Potential issue: `_resolve_bounded` finalizes prior window with floor=default. But if the prior window has no signals, interpret returns min_budget=floor. So directive budget=floor. Good.

Potential issue: `_resolve_bounded` calls `_ensure_template_valid` every time, which may log error if template invalid. That's fine.

Potential issue: `_ensure_template_valid` uses `_operator_budget()` which reads config. If config unreadable, returns default 25. Good.

Potential issue: The `_operator_budget` docstring says "PR-H2 owns getting this exactly right (it is the consumer of the budget)." It returns the configured max_iterations. In v0, the produce policy uses this as min/max. The consumer reads directive compute_budget. Good.

Potential issue: The call site comment says "Fails open: the consumer self-contains Exception and SystemExit and returns the operator's own value unchanged when the subsystem is off, kill-switched, or has no prior directive". It doesn't mention returning a non-int default unchanged. Not a bug.

Potential issue: In `agent/turn_context.py`, the call is:
```
agent.max_iterations = _salience_observer.bounded_iterations(
    agent.session_id or "", default=agent.max_iterations
```
If `bounded_iterations` returns a non-int (e.g., string default unchanged), `agent.max_iterations` becomes string, then `IterationBudget(agent.max_iterations)` may crash. This is a concrete fail-open violation. But as noted, host's max_iterations likely int.

Potential issue: The `try` block around import in turn_context.py catches Exception? The excerpt cuts off; likely catches ImportError. Not under review? The call site is included to verify ordering. We can mention if the surrounding try only catches Exception, a SystemExit from the import would not be caught. But the prompt says hunt for BaseException/SystemExit path from a host API the consumer calls. The import of salience_observer in turn_context is a host action; if the module import raises SystemExit (e.g., from hermes_constants get_hermes_home at import time?), the try may not catch. But the module import itself is guarded? The excerpt shows `try: from hermes_cli.observability import salience_observer as _salience_observer`. We don't see except. It likely catches Exception. But SystemExit from import would propagate. However, salience_observer.py's import catches exceptions for vendored import and config? It imports hermes_constants at function level, not top-level. product_identity import in salience_enabled is at function level. So module import unlikely to SystemExit. Not a concrete defect.

Potential issue: `_bus_for` uses `directory.mkdir(parents=True, exist_ok=True)`. If `get_hermes_home()` returns a path that is a file, mkdir raises FileExistsError. Caught by caller. Good.

Potential issue: `_bus_for` uses `SalienceBus(str(path))`. The vendored bus's `
