# Red-team (H3, pass=coding): moonshotai/kimi-k2.7-code

_finish=length seconds=679.2 usage={'prompt_tokens': 27923, 'completion_tokens': 16000, 'total_tokens': 43923, 'cost': 0.07694225, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.07694225, 'upstream_inference_prompt_cost': 0.02094225, 'upstream_inference_completions_cost': 0.056}, 'completion_tokens_details': {'reasoning_tokens': 18078, 'image_tokens': 0, 'audio_tokens': 0}}_

We need perform red-team adversarial review of PR-H3 implementation. Need produce findings with ID/TITLE/SEVERITY/LOCATION/CONCRETE TRIGGER/WHY/SUGGESTED FIX. Then steelman and verdict. Need be rigorous, concrete. We need analyze code carefully. We must not report already fixed items or out-of-scope. Need focus on changed code: salience_observer.py, tests, turn_context call site, box ferry, config docs. Need find concrete defects under guarantees. Let's reason deeply.

First, understand PR-H3: observer producer synthesizes ATTENTION signal, widens policy window via compute_headroom. Consumer call site captures pristine operator budget once per agent and passes as default to bounded_iterations. Need ensure no ratchet, no compounding, no runaway, fail-open, A4 floor integrity, A3 ordering, audit honesty, doc honesty, test honesty.

Let's inspect salience_observer.py for issues.

Potential issues:

1. _operator_budget precedence: It reads keys: agent.max_iterations, max_iterations, agent.iteration_budget, agent.max_turns, max_turns. But the consumer call site passes default=agent._salience_operator_iterations. In turn_context.py, they capture agent.max_iterations once per agent. However, the host's real operator chain may have resolved agent.max_iterations from max_turns. The doc says _operator_budget now reads agent.max_turns / max_turns after rig keys. Good. But the consumer default is agent.max_iterations (captured). If agent.max_iterations was set from max_turns by cli.py, then default matches. But if config uses max_turns and not max_iterations, the capture at agent creation may be 25? Need see actual agent initialization. But out of scope? We can flag if _operator_budget and consumer default diverge.

Specifically: _operator_budget reads agent.max_turns if no rig keys. The consumer default is captured agent.max_iterations. If the config sets agent.max_turns=40 and no max_iterations, agent.max_iterations may be set to 40 by cli.py? Not sure. If not, default would be 25 fallback, while _operator_budget returns 40. This means produce-path rollover close floors at 40 (durable record), but consumer finalize-on-read floors at captured 25. The doc says produce-path close floors at config-derived operator budget which may differ from live agent's constructor/env value; consumed only via resume path. But if in-process consumer default differs from _operator_budget, then within-process applied budgets use 25 floor while durable records for resume use 40 floor. Is that a defect? Under G1/G3? The consumer default should be the pristine operator budget. If agent.max_iterations not updated to max_turns, the call site captures wrong floor. But is that PR-H3's responsibility? They updated _operator_budget to read max_turns. The call site captures agent.max_iterations. If the host's operator chain sets agent.max_iterations from max_turns elsewhere, fine. If not, the consumer's default is stale. However, the prompt says "the host assigns the governed value back to agent.max_iterations (the conversation loop's guard reads it, so raises must propagate). Flooring the next turn on that SAME value would compound... The call site now captures the operator's PRISTINE budget once per agent (_salience_operator_iterations) and ALWAYS passes it as the consumer's default". It captures agent.max_iterations at first consumer call. If agent.max_iterations was already mutated by e.g. previous turn's bounded_iterations? They guard is None, capture once. But if at first call agent.max_iterations already reflects some config resolution maybe not max_turns. The issue: _operator_budget can return a different floor than the captured default. That means within a process, the applied floor may be 25 while the policy window used for durable records at session end/rollover may be 40. The guarantee G1 says applied budget never exceeds operator + headroom and never falls below operator floor. If operator floor is ambiguous (two sources), could violate? Applied 25 is still >= operator floor? If real operator floor is 40, applied 25 is below real floor, violating A4/G1. But is that concrete? Need know if agent.max_iterations equals max_turns. The code in turn_context doesn't show resolution. The test test_finalize_on_read_floor_is_caller_default uses agent.max_iterations=25 and default=12, expects floor 12. That's fine. test_operator_budget_precedence_table includes agent.max_turns=40 with no max_iterations -> expected 40. But consumer default in real scenario would be _DEFAULT_BUDGET if agent.max_iterations not set. That could cause applied floor 25 vs operator budget 40. However, the test doesn't test consumer call site with max_turns only. The source-text pin test checks default=agent._salience_operator_iterations, not that it equals _operator_budget. This is a potential issue but maybe host ensures agent.max_iterations set. Need decide if concrete enough.

2. _compute_headroom parse: bool guard, int, string. What about subclasses of int? isinstance(value, int) includes bool? They exclude bool first. bool is subclass of int. They check isinstance(value, bool) pass. Good. Negative int -> 0. Float -> 0 even if 3.0? They treat float as 0. That matches docs. But what about string "3.5"? int(value.strip()) raises ValueError -> pass -> 0. Good. What about string with plus sign "+15"? int("+15") works. Fine. What about huge int >1e6 cap. Good.

Potential issue: _HEADROOM_CACHE initialized to 0? Actually global _HEADROOM_CACHE = None. _compute_headroom returns int. If config headroom is 0, caches 0. Fine.

3. Policy window: _issue_template uses floor + _compute_headroom(). If floor is large (e.g. 1e9) and headroom 1e6, max_budget = 1,001,000,000. Fine. If floor is negative? _operator_budget ensures >0. Consumer default validated >0. So min_budget positive.

4. ATTENTION signal: influence = min(1.0, window.events / _ATTENTION_SATURATION_EVENTS). For events large, influence 1.0. But if events=0, no signal. Good.

But there may be issue: _signal uses confidence 1.0 fixed. provenance = _ref("attention", "events:" + str(window.events)). _ref truncates each token to MAX_TOKEN_LEN and filters empty. For events maybe huge number string >128 chars? events is int; str of huge int maybe >128 if events astronomically large. But events count is bounded by actual tool calls in a turn; unrealistic but could be large. _ref truncates to 128. provenance token would be "events:<first 125 digits>" not exact count. The guarantee G5 says provenance stays ref-shaped and exactly ("attention","events:<n>"). Truncation would violate exact provenance if events count exceeds token length. However, is events count bounded? In pathological turn with >10^37 events, token >128. Not realistic? But concrete trigger could be a fuzz/stress test. The code uses _ref which truncates. The guarantee says provenance exactly events:<n>. If n > 128 chars, impossible to fit. But the signal validator requires token <=128. So if they didn't truncate, publish would fail and signal dropped -> floor. Truncation is needed for validity. But it breaks exact provenance. Maybe acceptable because token bound is structural. The doc says provenance stays ref-shaped (<=128-char tokens, no tool payload). It doesn't promise exact count when count exceeds token length. However G5 says "its provenance must stay ref-shaped ... and a failed publish must drop the signal". It also says "the synthesized signal carries confidence 1.0 and exactly ("attention","events:<n>")". For huge n, truncation changes value. But n cannot realistically be that high. Could flag LOW? Need concrete trigger. Maybe not high.

5. Double-counting / split window: _open_window finalizes previous window if turn_id differs. But if a new turn opens before previous closes? It closes previous. Good. But what about if same session, same turn_id opens again (e.g. pre_llm_call fired twice for same turn)? _open_window: current exists and not closed and turn_id == turn_id -> does nothing (current window remains open). Good. But if pre_llm_call fires for same turn after some tool calls (shouldn't), events continue to accrue. That's okay? Not a defect.

But there is a subtle issue: ordering of consumer vs window open. The consumer bounded_iterations is called before pre_llm_call (which opens turn N window). The consumer finalizes the prior turn's window. In _resolve_bounded, it first calls _ensure_template_valid, then closes window if open, then reads _LAST_DIRECTIVE. However, if there is an open window for the prior turn, closing it with budget=floor (consumer default). Good. But after closing, _LAST_DIRECTIVE is set. Then bounded_iterations returns. Then pre_llm_call opens turn N window. Good.

But what if bounded_iterations is called when there is no open window but _LAST_DIRECTIVE still holds directive from two turns ago because the prior turn aborted before opening window? The doc says normally prior turn, but if prior turn aborted before opening window, earlier turn may be latest. That's accepted. However, A3 says turn N applies most recently recorded turn's directive. If prior turn didn't record, applying earlier is okay. But is there a path where a stale directive persists incorrectly? Let's think.

If a turn opens window (pre_llm_call) but then consumer is not called before next pre_llm_call? In normal flow consumer is called at start. The window from prior turn remains open until consumer finalizes it. If consumer fails or is skipped, the window could remain open into next turn. But consumer is called each turn. If it errors, it returns default and logs; but it might not close window. Actually bounded_iterations catches exceptions and returns default; if an exception occurs before _resolve_bounded completes (e.g. _consume_enabled raises?), the window stays open. Next turn's pre_llm_call will close it via _open_window (rollover close with budget=None, using _operator_budget). Then consumer reads _LAST_DIRECTIVE (the just closed directive) and applies it to next turn? Wait ordering: At next turn start, before consumer, pre_llm_call is not yet fired. The consumer is called first. If prior window still open, _resolve_bounded closes it with floor=default. Good. If consumer errors out before closing, then pre_llm_call fires and _open_window closes prior window with _operator_budget. Then consumer is not applied? Actually consumer already failed. Next turn consumer will read _LAST_DIRECTIVE and apply. So maybe okay.

Potential A3 stale/self-read: The consumer call is before pre_llm_call. The _resolve_bounded function closes any open window (prior turn) and reads _LAST_DIRECTIVE. But if the prior turn's window is still open and the consumer default is passed, it closes it. However, _open_window in pre_llm_call also closes prior window if turn_id changed. Since consumer runs before pre_llm_call, the prior window should already be closed. But if some code path calls pre_llm_call before consumer? The source-text pin ensures consumer before pre_llm_call. Good.

But what about the scenario: Turn N starts; consumer finalizes turn N-1 and returns budget. Then pre_llm_call opens window for turn N. During turn N, tool calls record events. At end of turn N (or next turn start), consumer finalizes turn N. Good.

Potential issue: _close_locked uses _operator_budget() when budget is None (rollover close in _open_window and _close_session). But the consumer's finalize-on-read passes default as floor. The doc says session-end close floors at config-derived operator budget, may differ from live agent's constructor/env value; consumed only via resume path. That's documented. But is there a risk that the last durable directive on disk has a floor from _operator_budget, and on resume the consumer applies it with a different floor? The resume caveat acknowledges this. Not a defect.

6. Audit honesty: The synthesized ATTENTION signal is published to bus and appended to signals list before interpret. If publish fails, signal dropped and not interpreted. Good. But what about the attention signal being appended to window.signals only after publish success. In _close_locked, it creates attention, publishes, if success signals.append(attention). Good. If publish raises, signals remains without attention. Good.

But what about _record: it increments window.events for every attributed event, then maps signals and publishes each mapped signal. If publish fails for a mapped signal, it logs but does NOT append to window.signals. The event still counts. That is intended: events count attributed activity, not recorded signals. But for audit honesty, a signal that informed the directive must be on record. Since failed publish dropped the signal, it didn't inform directive. Good. However, window.events increments even if the event's mapper returns nothing (unmapped). That's fine.

Potential issue: In _record, if mapper returns multiple signals and one publish fails, the others are appended; the failed one is not. That's correct.

But there is subtle issue: The attention signal is synthesized at close and published. If the publish succeeds, the signal is on the bus. But the interpreter is then called with signals tuple including attention. The interpreter's _aggregate uses subject matching and valid_signal. The attention signal's subject matches. Good.

7. _bus_for and _LAST_DIRECTIVE promotion: _budget_from_disk constructs bus (replays and verifies), reads directives[-1][1], deep copies, promotes to _LAST_DIRECTIVE. It runs only when no bus cached. If bus cached but _LAST_DIRECTIVE empty (last close failed), it returns None per comment. Good. But what if _LAST_DIRECTIVE has a directive from previous turn and _BUSES also cached? _budget_from_disk not called. Good.

Potential issue: _budget_from_disk deep-copies payload and caches. But the payload is a dict from bus._directives (the replayed dict). It uses copy.deepcopy. Good.

Potential issue: _resolve_bounded finalizes prior window with floor=default. But if there is no open window, it reads _LAST_DIRECTIVE or disk. If _LAST_DIRECTIVE holds a dict (from disk promotion or prior close), _directive_budget extracts compute_budget. Good.

8. Fail-open: bounded_iterations catches Exception and SystemExit. But it also has a try around import? In turn_context, the import and call are inside try/except Exception. If import raises SystemExit? except Exception won't catch SystemExit. But the import is inside try: from hermes_cli.observability import salience_observer as _salience_observer. If that import raises SystemExit (unlikely), it would propagate. The prompt says KeyboardInterrupt deliberately not caught; SystemExit should be caught? The observer code catches SystemExit in many places. In turn_context, the comment says catches Exception and SystemExit. But code says `except Exception:` only. Actually the snippet: `try: from ... if getattr... agent.max_iterations = ... except Exception: logger.debug(...)`. It catches Exception, not SystemExit. The comment says "self-contains Exception and SystemExit". This is a discrepancy: SystemExit would not be caught at call site. But is SystemExit possible from bounded_iterations? bounded_iterations itself catches (Exception, SystemExit). So if bounded_iterations raises SystemExit, it would be caught inside and return default. The import could raise SystemExit? Possibly if module does sys.exit? Unlikely. But the comment is inaccurate. Could be a doc honesty issue? Not a concrete defect maybe.

But there is a concrete issue: In turn_context, after consumer, `agent.iteration_budget = IterationBudget(agent.max_iterations)`. If bounded_iterations returns a non-int? It returns int or default (int validated). Good. If default invalid, returns default untouched; then IterationBudget may fail. But default is captured pristine int. Good.

9. A4 floor integrity: finalize-on-read floor is default. In _resolve_bounded: floor = default if valid positive int else _operator_budget(). Good. But what if default is a bool? They reject. Captured pristine is int. Good.

Potential issue: The consumer default is agent._salience_operator_iterations, captured once per agent. If agent.max_iterations is later changed by operator config reload? The capture is once per process. The doc says resolved once per process. Good. But if the agent object is reused across config changes, floor stays captured. Acceptable.

Potential issue: The pristine capture uses `agent.max_iterations`. If agent.max_iterations was already mutated by a previous run before first call? At first call, is None guard ensures capture. But if the agent constructor sets max_iterations to a governed value from previous session? No.

10. Ratchet / feedback loop: Need hunt every floor derivation for pollution by previously applied value. The consumer default is pristine. _operator_budget uses config only. _issue_template uses passed floor. The only floor passed as None is in _close_session and _open_window rollover; they use _operator_budget. Good. But what about the box's bounded_iterations call? In server.py maybe it calls bounded_iterations? The prompt mentions "the box's own bounded_iterations call in server.py" as a path to check. We don't have server.py code except box ferry snippet. Need infer. Maybe box/server.py writes config and then calls something? The prompt says hunt feedback paths: "getattr defaults, resume paths, the box's own bounded_iterations call in server.py, _budget_from_disk promotion". We need inspect if box/server.py calls bounded_iterations with default maybe derived from applied value. Not in provided material. But we can note if not shown. However, we should find concrete defects in changed code. We have box/server.py snippet only lines 142-165. It doesn't show bounded_iterations call. Maybe the box uses agent.turn_context which includes consumer. So no separate call. But the prompt warns to hunt it. Maybe there is a call in server.py to bounded_iterations with default=agent.max_iterations (not pristine). Not shown. We cannot report without concrete trigger. But we can mention if missing? Need concrete location. Since material doesn't include, maybe out-of-scope.

11. _operator_budget memoization: It caches the first resolved budget. If config has agent.max_iterations=25 initially, later a test monkeypatches config to max_turns=40, _operator_budget still returns 25 until reset. That's by design (resolved once per process). Tests reset. Fine.

12. _compute_headroom memoization: same.

13. _ensure_template_valid: Called under _LOCK in _resolve_bounded. It uses _issue_template with _operator_budget and _compute_headroom. If headroom huge, policy verifies. Good. If template invalid, _TEMPLATE_VALIDATED False. Then every consumer call logs error. But bounded_iterations still returns default. Good.

14. _directive_budget: It checks subject and policy_id not empty. For a dict from bus, subject and policy_id are strings; if hard deny, subject empty -> returns None. Good. It rejects bool budget. Good.

Potential issue: For a live Directive object, getattr(source, "compute_budget", None). If source is not a Directive but has attribute compute_budget=0? It returns 0 and rejects (<1). Good.

Potential issue: _directive_budget does not validate that budget <= policy max etc. It applies verbatim. That's by design (consumer not decider). But if a bug in interpreter produced budget > max? The interpreter clamps to policy window. So okay.

15. Ordering issue in _resolve_bounded: It finalizes prior window using floor=default. But it also calls _ensure_template_valid before closing. _ensure_template_valid may call _issue_template which uses _operator_budget and _compute_headroom. It doesn't affect window. Good.

But _ensure_template_valid could set _TEMPLATE_VALIDATED False, causing? It just logs. No behavior change.

16. A3 with movement: Since budgets differ, a stale/self-read is real. Need find path where turn N applies turn N's own budget (self-read) or stale budget. The consumer call is before pre_llm_call. But what if a turn's window is opened before the consumer runs? The source-text pin asserts consumer before pre_llm_call. In the provided turn_context.py snippet, consumer is at lines 480-515, pre_llm_call at 1070. So ordering is correct. But is there a path where bounded_iterations is called from somewhere else (e.g. inside pre_llm_call or after window open)? Not shown. We can't report.

But there is a subtle self-read possibility: In _resolve_bounded, if the prior turn's window is still open and we close it with budget=floor. The directive is recorded and cached in _LAST_DIRECTIVE. Then bounded_iterations returns that directive's budget. But the same call also opened? No. It doesn't open a window. So no self-read.

What about if bounded_iterations is called twice in same turn? First call finalizes prior window and returns budget. Second call: window for current turn may be open (if pre_llm_call fired after first). If pre_llm_call opened current turn window, then second bounded_iterations call would close the current turn window (finalize-on-read) and return its own directive (self-read) for the same turn. Is bounded_iterations called only once per turn? The turn_context snippet shows one call. But if host calls it again (e.g. due to retry or other code), it could self-read. The function is idempotent-ish: closing current window would record directive for current turn and then apply it to current turn (self-read). This violates A3. Is there any protection? _resolve_bounded closes any open window regardless. If called again after window opened, it would close current window prematurely. But is that a realistic path? The host should call once. But the function isn't guarded against multiple calls per turn. Could be a defect if host code does. But concrete trigger? A test or host path that calls bounded_iterations twice with an open window. Not in material. Could be LOW? The design assumes once per turn. But we can note that the API is unsafe if called twice. However, the prompt wants concrete triggers in production code. Maybe we can construct a test: after pre_llm_call, call bounded_iterations again. It would close current window and self-read. But production host doesn't. Still, it's a vulnerability.

17. Window open after consumer: The consumer runs before pre_llm_call. But what if the host rebuilds iteration budget after pre_llm_call? The source-text pin ensures before. Good.

18. _open_window finalizes previous window if turn_id differs. But it passes budget=None to _close_locked, so uses _operator_budget floor. However, if a new turn starts without consumer call, the rollover close uses _operator_budget, not the captured default. That could differ from consumer default. But the directive is recorded. Next consumer call will read _LAST_DIRECTIVE and apply it. If _operator_budget floor differs from consumer default, the applied budget may be based on a different floor. But is this a violation? G1 says within process applied budget never exceeds operator + headroom and never falls below operator floor. If operator floor is ambiguous, maybe. But the policy window for that directive is [operator_budget, operator_budget+headroom]. If consumer default is lower, applying a budget based on higher floor could exceed default+headroom? Actually budget = floor + round(...*headroom). If floor is higher (operator budget), budget is higher. It won't exceed floor+headroom. It won't be below floor. So safe relative to that floor. But if applied to a turn whose default is lower, it's still above the real operator floor? The real operator floor might be default. But _operator_budget is supposed to be the operator budget. The divergence is the issue. But maybe host ensures they match.

19. _operator_budget precedence includes agent.max_iterations first, then max_iterations, then agent.iteration_budget, then agent.max_turns, then max_turns. The consumer default is agent.max_iterations (captured). If config has max_turns but not max_iterations, _operator_budget returns max_turns, but consumer default remains default 25 (or whatever agent.max_iterations from constructor). This is a real divergence. Is it a defect? The PR claims _operator_budget now reads max_turns so stock config floors correctly. But the consumer default doesn't read max_turns; it captures agent.max_iterations. If the host's agent.max_iterations is not set from max_turns, then consumer applies wrong floor. However, maybe cli.py sets agent.max_iterations from max_turns. The prompt says "The ``HERMES_MAX_ITERATIONS`` env override is deliberately not read here: this floor only covers rollover / session-end closes, and the consumer's finalize-on-read floors at the caller's resolved ``default`` anyway." It implies the caller's default is the resolved operator budget. But if the caller (turn_context) captures agent.max_iterations, and agent.max_iterations is not the resolved operator budget, then the caller is wrong. But the PR didn't change agent.max_iterations resolution. This could be a missed issue: the no-ratchet fix captures agent.max_iterations, but if the operator's real budget lives in max_turns and agent.max_iterations is a fallback, the consumer floor is wrong. However, the test test_call_site_precedes_budget_rebuild only checks source text, not value. The test_operator_budget_precedence_table tests _operator_budget, not consumer default. So maybe not caught.

But is this within PR-H3 scope? They changed _operator_budget to include max_turns. They also changed call site to capture pristine budget. The pristine capture uses agent.max_iterations. If the host's existing behavior sets agent.max_iterations from max_turns, then fine. But the material doesn't show that. We need be careful: if we report, we need concrete trigger. A config with only max_turns and a call to bounded_iterations where agent.max_iterations=25 (default) would produce default floor 25 while _operator_budget says 40. But we don't know if agent.max_iterations would be 25. We can construct using monkeypatch in test? The real host? Maybe we can flag as MEDIUM if we can show test. But the prompt says concrete triggering input or call sequence; no concrete trigger => LOW at most. We can provide a concrete scenario in tests: set cfg with agent.max_turns=40, no max_iterations; monkeypatch agent.max_iterations=25; call bounded_iterations; it returns 25 instead of 40. But is that a valid host state? The agent object may have max_iterations=25. The PR intended to fix stock config blind spot. If the agent still has 25, the fix is incomplete. This is a genuine concern.

Let's examine cli-config.yaml.example: agent block has max_iterations: 25, max_tokens. It doesn't show max_turns. Box settings.yaml has max_iterations: 25. So typical config uses max_iterations. The _operator_budget precedence keeps rig keys first for back-compat. The max_turns keys are fallback. The consumer default captures agent.max_iterations. If config uses max_turns only, the host likely sets agent.max_iterations from max_turns. So maybe not an issue. But the PR added max_turns to _operator_budget to handle a scenario where agent.max_iterations is not configured. If the host doesn't sync, consumer floor is wrong. But maybe out of scope.

20. Another potential issue: The consumer default is captured once per agent under is-None guard. But if the agent object is reused across sessions (e.g. session_id changes) and the operator budget changes? The capture stays. Not a defect.

21. _salience_operator_iterations attribute is set on agent object. If multiple threads? Not relevant.

22. The `_close_locked` exception handler pops _LAST_DIRECTIVE to prevent stale directive. But if exception occurs after emit but before caching? It would pop, so consumer falls back to default. Good. But the directive was emitted to bus; durable record exists. The in-memory cache is dropped. Next consumer call reads from disk (if bus cached? Actually _BUSES still cached; _LAST_DIRECTIVE popped; _budget_from_disk returns None because bus cached). So it returns default. The durable directive is not consumed in-process, which is correct because close failed at caching step? But emit succeeded; dropping cache means the directive is ignored in-process, though on disk. On resume, it would be applied. Is that intended? The comment says "A failed finalize must fail OPEN, not leave the PRIOR turn's cached directive to be consumed as this turn's decision". If emit succeeded but caching failed (unlikely), dropping cache is okay. But the directive is on disk and could be consumed on resume. That's the resume caveat. Fine.

23. The `_close_locked` exception handler catches SystemExit too. Good. But if SystemExit raised by _bus_for? It would be caught.

24. _bus_for: constructs SalienceBus with path. It may raise on corrupt file. In _close_locked, _bus_for called for publish and emit. If _bus_for raises (e.g. corrupt file), the whole close fails and _LAST_DIRECTIVE popped. But the window is marked closed. If bus was corrupt, subsequent closes also fail. Consumer falls open. Good.

25. _record and _close_locked use _bus_for(window.session_id). If _bus_for returns a bus object that is cached. Good.

26. Potential issue: _open_window uses _close_locked(current) with budget=None when turn_id differs. But if current window has turn_id same as new? It doesn't close. If current window closed? It creates new. Good.

But what if session_id exists in _WINDOWS with a closed window from previous turn, and a tool call for that previous turn arrives late? _record checks window.turn_id != turn_id -> returns. Good.

27. _close_session: pops window, closes if not closed, then pops bus and _LAST_DIRECTIVE. It closes before popping bus, so bus exists. Good. But if _close_locked fails, it still pops bus and _LAST_DIRECTIVE. Good.

28. Potential issue with _LAST_DIRECTIVE after session reset: If on_session_reset closes and frees, then a new turn starts, _LAST_DIRECTIVE popped. Good.

29. Test honesty: Need evaluate tests for mutation blindness. The prompt asks to find tests that stay green when production line sabotaged. Let's inspect tests.

test_headroom_parsing_table: parametrize includes True, False, -3, "15", 3.5, etc. It sets _cfg(headroom=value) and asserts _compute_headroom() == effective. If production code changed to accept float 3.5 as 3 (truncation) instead of 0, test for 3.5 expects 0 -> red. Good. If production accepted negative -3 as 3? test expects 0 -> red. If bool True accepted as 1? test expects 0 -> red. Good. However, it doesn't test that string "True" or "false" are 0? Not needed.

test_headroom_absent_is_zero: tests no salience block. Good.

test_headroom_memoized_and_reset_clears: tests caching and reset. Good.

test_template_pinned_when_headroom_absent: asserts policy min=max=floor. Good.

test_template_widened_and_verifies: asserts min,max and verify_policy True. If production changed max_budget = floor + headroom + 1, verify_policy may still pass? It would be a different shape but verify_policy only checks min<=max and types, not that max equals floor+headroom. The test asserts exact (10,25), so if max wrong, red. Good.

test_template_probe_validates_widened_shape: (a) validates True. (b) monkeypatches _compute_headroom to -1, expects _TEMPLATE_VALIDATED False. But _compute_headroom is monkeypatched after _use_config which set headroom=0. _ensure_template_valid calls _issue_template which calls _compute_headroom -> -1, so max_budget < min_budget (9? Actually floor 10, max -1). verify_policy rejects because min>max -> False. Good. If production probe used its own pinned template, this would catch. Good.

test_budget_moves_and_rounds_half_up: 4 events, headroom 5, floor 10 -> expected 13. If production used Python round (banker's) for 2.5 -> 2, expected 12, test red. If int truncation -> 12. If counted only mapped events (2 events) -> 11. Good.

test_budget_saturates_at_floor_plus_headroom: 50 events, headroom 6, floor 10 -> expected 16. If saturation dropped, influence 50/8=6.25 -> min(1) not enforced; but signal validator would reject influence>1, causing signal drop -> budget 10, not 16. Wait test expects 16. Let's compute: with saturation, influence=1, budget=10+6=16. If production didn't saturate, signal influence 6.25 invalid -> dropped -> budget 10. Test expects 16, so if no saturation, test fails? It would get 10, not 16. But the comment says "Kills: dropping the min(1.0, ...) saturation. (An influence > 1 fails the signal validator and the signal is dropped ⇒ budget 10 ≠ 16 — caught either way.)" Actually if no saturation, the signal is invalid and dropped, budget floor 10, which is not 16, so test red. Good. If saturation implemented as min(1, events/8) but events/8 computed as integer division? In Python3, 50/8=6.25 float. If min(1, events//8) = 6, influence >1 invalid -> drop -> 10. Test red. Good.

test_saturation_boundary_exact: 7 events headroom 8 -> 10 + round(7/8*8)=17; 8 events -> 18. Good.

test_quiet_turn_stays_exactly_at_floor: checks budget 10, directive 10, signals empty. Good. If unconditional synthesis, signals non-empty and maybe budget not floor? If influence 0, budget floor. But test checks signals empty, catching unconditional synthesis. Good.

test_finalize_on_read_floor_is_caller_default: config agent.max_iterations=25, default=12, headroom=10, 8 events -> 22. If production derived floor from _operator_budget (25), budget would be 35; test red. Good.

test_three_turn_cadence_distinct_budgets: checks applied1=10 (no record), applied2=28 (u1 closed at floor 20), applied3=34 (u2 closed at floor 30). This catches deleting finalize-on-read. Good. But note applied2 uses default 20, applied3 default 30. The budgets differ. Good.

test_misattributed_events_do_not_feed_attention: events with wrong turn_id; expects floor 10 and no signals. Good.

test_no_ratchet_across_busy_turns: floor=25, busy -> 40, busy -> 40, quiet -> 25. Also checks durable record u2=40, u3=25. This catches re-floor on prior applied value. Good. But does it catch a consumer change that re-anchors default to agent.max_iterations? In that scenario, first call default=25 returns 25 (no record). Then u1 busy closes at floor 25 -> 40. Next call default=agent.max_iterations which was set to 40 by previous consumer -> floor 40 -> budget 55. Test expects 40, red. Good. But the test passes default=floor each time manually. It doesn't test the actual call site. The source-text pin test covers call site. Good.

test_rollover_close_floors_at_operator_max_turns: config agent.max_turns=40 headroom=8. Open u1, events 8, open u2 (rollover). Rollover close uses _operator_budget -> floor 40 -> budget 48. Good. If _operator_budget didn't read max_turns, floor 25 -> 33, test red. Good.

test_operator_budget_precedence_table: checks precedence and fallback. Good.

test_one_attention_signal_per_window_on_bus: 3 events, close session, reopen bus, check one attention signal with correct influence/confidence/provenance. Good. If per-event attention, len=3, red. If provenance includes tool names? It checks exactly ("attention","events:3"). Good. But what if provenance token truncation? Not tested.

test_no_attention_signal_when_no_activity: checks no signals. Good.

test_attention_publish_failure_falls_back_to_floor: monkeypatches _bus_for to publish fails, emit works. Calls bounded_iterations. It asserts budget=10 and _LAST_DIRECTIVE not None. Wait if publish fails, _close_locked catches exception and logs, then continues? Let's trace: _close_locked creates attention, tries publish -> raises RuntimeError. It catches exception, logs, does NOT append attention. Then it calls _issue_template, interpret, _bus_for again for emit. But _bus_for is monkeypatched to _PublishFails object. When _close_locked calls `_bus_for(window.session_id).emit(directive)`, the monkeypatched object has emit method that calls real_bus.emit. Good. So directive emitted. _LAST_DIRECTIVE set. Budget floor 10. Good. If production didn't catch publish failure, close would fail and _LAST_DIRECTIVE popped -> None. Test asserts _LAST_DIRECTIVE not None, so would fail. Good. If production interpreted attention without publishing, budget would be 15, test red. Good.

test_sentinel_payload_absent_with_headroom_on: records tool with args/result/error_message, checks no sentinel leak. _map_tool_call provenance uses tool_name and status only. Good.

test_bad_headroom_full_path_stays_inert: headroom=bad, events=4, expects budget=10 and directive on bus floor 10. If production raises, budget not returned and maybe no directive. Test checks directive on bus. Good.

test_e2e_headroom_moves_iteration_budget: uses lifecycle hooks and model_tools._emit_post_tool_call_hook. 4 events headroom 15 floor 25 -> expected 33. Wait compute: influence = min(1,4/8)=0.5; headroom 15; scale = 0.5*15=7.5; round_half_up = 8; budget=33. Yes. This tests end-to-end wiring. Good.

Now, are any tests vacuous? Let's think.

test_headroom_parsing_table: It directly calls _compute_headroom. If _compute_headroom memoization bug caused it to always return first value, the parametrized test would fail because each call is separate process? In test, _reset_for_tests clears cache. Good.

test_template_probe_validates_widened_shape part (b): monkeypatches _compute_headroom to -1. But _issue_template uses _compute_headroom() which is monkeypatched. However, _compute_headroom also caches? The monkeypatch replaces function, so no cache. _ensure_template_valid calls _issue_template -> max_budget = floor + (-1). With floor from _operator_budget (maybe 25 default), max=24, verify_policy False. Good. But if _ensure_template_valid cached earlier? _reset_for_tests clears _TEMPLATE_VALIDATED. Good.

Potential test blindness: test_no_ratchet_across_busy_turns manually passes default=floor each call. It doesn't actually simulate the call site feeding back applied value. But source-text pin covers. However, the test's comment says it tests no ratchet under production call site contract. It doesn't catch if _reset_for_tests clears state between calls? It doesn't reset. Good.

Potential test blindness: test_three_turn_cadence_distinct_budgets checks applied values but not durable record for u1? It checks directives_for u1 and u2. Good.

Potential test blindness: test_quiet_turn_stays_exactly_at_floor checks signals empty. Good.

Potential issue: The test `_events` uses tool_name="write_file" which maps to MEMORY signal. The attention signal is synthesized from events. In test_budget_moves_and_rounds_half_up, they use 2 write_file + 2 read_file. read_file is unmapped but counts events. Good.

Potential issue: In test_one_attention_signal_per_window_on_bus, they use tool_name="read_file" (unmapped). They expect only one attention signal. _map_tool_call returns [] for read_file. Good.

Potential issue: The test `test_sentinel_payload_absent_with_headroom_on` uses `so._record(..., so._map_tool_call)` directly with args/result/error_message. The `_map_tool_call` doesn't include those in provenance. But if production changed to include them, raw would leak. Test catches. Good.

Potential issue: `test_attention_publish_failure_falls_back_to_floor` monkeypatches `_bus_for` to return `_PublishFails`. But `_PublishFails.emit` calls `real_bus.emit(directive)`. However, `_close_locked` first calls `_bus_for(window.session_id).publish(attention)` -> raises. It catches. Then calls `_bus_for(window.session_id).emit(directive)` -> calls real_bus.emit. But the `_PublishFails` object is stateless; it doesn't have a real_bus attribute except closure. In the test, `real_bus = so._bus_for("s")` before monkeypatch. Then monkeypatch `_bus_for` to lambda sid: _PublishFails(). The `_PublishFails.emit` references real_bus from closure. Good.

But note: `_close_locked` also calls `_bus_for` inside the `if window.events>0` block and again for emit. The monkeypatched lambda returns new _PublishFails each time. publish raises, emit uses real_bus. Good.

Potential issue: `_PublishFails` doesn't have `directives_for` etc. Not used.

Now, what concrete defects can we report? Need find at least some. The prompt says if nothing above LOW survives honest effort, say so plainly. But likely there are issues.

Let's systematically search for ESCALATION/RUNAWAY paths.

- The policy window is [floor, floor+headroom]. The applied budget is floor + round(attention*headroom). Since attention in [0,1], budget in [floor, floor+headroom]. No compounding because floor is pristine. Good.

- But is there any path where the floor used by consumer is not pristine but a previously applied budget? The call site uses agent._salience_operator_iterations captured once. Good. But if agent.max_iterations is modified by bounded_iterations and then used to capture? The capture is before assignment: `if getattr(agent, "_salience_operator_iterations", None) is None: agent._salience_operator_iterations = agent.max_iterations`. Since at first call agent.max_iterations hasn't been modified by consumer yet (unless previous turns in same process? First call after agent creation). If the agent object is reused across turns, the capture happens once. Good.

- However, what if the first call to bounded_iterations happens after a previous turn already modified agent.max_iterations (e.g. if turn_context is entered multiple times but the import/call is inside the function, and _salience_operator_iterations is None initially). At first entry, agent.max_iterations is whatever the host set (pristine). Good.

- Could there be a path where `agent._salience_operator_iterations` is set to a governed value elsewhere? Not in changed code.

- Could `_operator_budget` be polluted by `_LAST_DIRECTIVE` or disk? It reads config only. Good.

- Could `_compute_headroom` be polluted? Reads config only. Good.

- Could headroom be conjured from a bad config value? The parse is robust. But there is a subtle bug: `_compute_headroom` treats a string "false" as? It tries int("false") -> ValueError -> pass -> 0. Good. A string "True" -> ValueError -> 0. Good. A string "0" -> 0. Good.

- What about `_looks_off` used for enabled flags: It treats empty string as off. For compute_headroom, empty string -> int("") raises ValueError -> 0. Good.

- What about a list or dict as headroom? isinstance not int/str -> ignored -> 0. Good.

- Could a negative headroom string like "-5" be parsed to -5 then clamped to 0. Good.

- The cap is 1e6. Good.

- Could headroom be a boolean subclass? bool excluded. Good.

Now FAIL-OPEN paths.

- bounded_iterations catches (Exception, SystemExit). Good. It returns default if any failure. But if default is invalid, returns default untouched. The caller passes pristine int. Good.

- _resolve_bounded catches? It doesn't catch; it's called inside bounded_iterations try. If _resolve_bounded raises SystemExit (e.g. from _bus_for?), bounded_iterations catches. Good.

- _ensure_template_valid: if verify_policy raises? It catches (Exception, SystemExit) and sets False. Good.

- _close_locked catches (Exception, SystemExit). Good.

- _record catches only publish exception; if mapper raises? The for loop calls mapper; if mapper raises, exception propagates out of _record to observe_lifecycle, which catches and logs. The event count was already incremented. Could that cause attention signal based on an event that caused a crash? But _record is called from observe_lifecycle which catches. If mapper raises, the event count increments but no signal published. The window close still synthesizes attention based on events count. Is that a problem? The event is attributed; the mapper crash is swallowed. The attention signal reflects activity count, not mapped signals. That's intended. But if mapper raises due to malicious kwargs, it could cause attention signal to be synthesized even though no normal signal. Not a fail-open. The observer stays dark for that event's mapped signals. Not a defect.

- _open_window: if _close_locked raises? It's inside try in observe_lifecycle. But _open_window itself doesn't catch. observe_lifecycle catches. Good.

- _bus_for: constructing SalienceBus may raise on corrupt file. In _close_locked, this is caught and _LAST_DIRECTIVE popped. In _record, caught and logged. In _budget_from_disk, caught by caller (bounded_iterations). Good.

- Could `bounded_iterations` return a budget < 1? It returns either default (valid >0) or budget from _directive_budget (rejects <1) or None -> default. So no.

- Could `_directive_budget` accept a bool? It rejects bool. Good.

A4 FLOOR INTEGRITY.

- Quiet turn: events=0 => no attention signal. The interpreter with no ATTENTION signal returns min_budget (floor). _scale with no attention? In interpreter, agg.get(Facet.ATTENTION,0.0) = 0.0, _scale(0, floor, floor+headroom) = floor. Good. So directive exactly floor. Test checks.

- But what if there are other signals (e.g. MEMORY, RISK, VERIFICATION) but no ATTENTION? The budget is still floor. Good.

- Finalize-on-read floor: _resolve_bounded sets floor = default if valid else _operator_budget. The consumer passes pristine default. Good.

- But what about rollover/session-end close? It uses _operator_budget. If _operator_budget differs from consumer default, the durable floor differs. The doc acknowledges. Not a defect under G3? G3 says "the finalize-on-read floor must be the session's pristine operator budget, not a ratcheted or re-derived value." It is the caller's default. Good.

A3 WITH MOVEMENT.

- Need find stale/self-read. The consumer call is before pre_llm_call per source pin. But what about the `_resolve_bounded` function: it closes the prior window and reads _LAST_DIRECTIVE. If there is an open window for the current turn (because pre_llm_call already fired before consumer in some code path), it would close current turn and self-read. The source pin prevents reordering. But is there any path where pre_llm_call fires before consumer? The provided snippets show consumer at lines 480-515 and pre_llm_call at 1070. So no. But if a host refactor moves consumer after pre_llm_call, the source-text test catches. Good.

- However, there is a subtle ordering issue: The consumer in turn_context.py uses `agent.session_id or ""`. If session_id is None, passes empty string. bounded_iterations returns default if not session_id. Good.

- What about `_resolve_bounded` reading `_LAST_DIRECTIVE` after closing window. If the window closed is the same turn as the one being started? Not in normal flow.

AUDIT HONESTY / FENCE.

- The attention signal is published before interpret. If publish fails, dropped. Good.

- Provenance ref-shaped: _ref truncates tokens. The attention provenance is ("attention","events:<n>"). Good. But _ref truncates each part to 128 chars. If n huge, token truncated. However, signal validator requires token <=128. If they didn't truncate, publish would fail and signal dropped, causing floor. That would still be safe but maybe not intended. The guarantee says provenance stays ref-shaped; truncation is needed. The exact count may be lost for huge n. LOW.

- No tool payload: _map_tool_call provenance uses tool_name and status only. _map_api_error uses provider. Good. The attention provenance uses events count. Good.

- Failed publish drops signal: yes.

- Could the attention signal be interpreted off-record if `_bus_for` returns a bus that silently drops publish but doesn't raise? The code appends to `signals` only on successful publish (no exception). If bus.publish returns without raising but doesn't actually append (malicious bus), the signal would be interpreted off-record. But the bus is vendored; out of scope? The rig uses SalienceBus. The bus contract is publish appends. Not a rig defect.

- Could `_record` increment events for a publish failure, causing attention signal at close that wasn't recorded? The attention signal itself is synthesized from events; if at close publish of attention fails, it's dropped. But the events that produced it may have had their own signals dropped earlier. The attention signal's provenance says events count; it doesn't require each individual event signal to be on record. The guarantee says "every signal that informed a directive is on the bus record". The attention signal is the only signal informing budget. It must be on record. If attention publish fails, it's dropped, so directive floor. Good.

HONESTY (docs/config).

- Module docstring: says default compute_headroom 0 = pinned. It says "within a process, never fewer than operator's own budget and never more than floor + headroom". Good. It mentions resume caveat. Good.

- cli-config example: explains formula and caveats. Good.

- Box settings: says budget moves between 25 and 40. Good.

- But there may be an inconsistency: The module docstring says "The observer synthesizes ONE per-turn ATTENTION signal from the window's own attributed activity count (saturating at _ATTENTION_SATURATION_EVENTS events), so a busy turn can buy the NEXT turn up to compute_headroom extra iterations". Good.

- The docstring in _close_locked says "closing turn N-1's window here floors its DURABLE directive at turn N's budget (window [floor, floor + headroom]), so if the operator budget changed between turns the record is floored to the reader's value — still consumed immediately, same semantics with the window widened". This is honest.

- The comment in _operator_budget says HERMES_MAX_ITERATIONS env override deliberately not read. That's honest.

Potential doc overstatement: The module docstring says "within a process the applied budget never exceeds operator + headroom and never falls below the operator floor". This holds if consumer default equals operator budget. If config uses max_turns only and agent.max_iterations not synced, it could fall below real operator floor. But not sure.

Potential doc overstatement: "A quiet turn decays back to the floor". With headroom>0, quiet turn yields floor. Good.

Potential doc understatement? Not obvious.

TEST HONESTY.

Need find tests that would stay green if production line sabotaged.

Let's examine `test_call_site_precedes_budget_rebuild` in test_salience_consumer.py (provided partially). It checks source text for default=agent._salience_operator_iterations and not default=agent.max_iterations. It also checks exactly one assignment guarded by is-None. This is a good test. But could a mutation bypass it while still ratcheting? For example, if code sets `_salience_operator_iterations = agent.max_iterations` every turn (not guarded), the source-text test would see multiple assignments? It asserts exactly one assignment in whole file. Good. If assignment is inside a loop? It would still be one line. But if it's re-assigned every turn via `agent._salience_operator_iterations = agent._salience_operator_iterations`? That would be one assignment line but not ratchet. Not relevant.

Could a mutation set `_salience_operator_iterations = agent.max_iterations` after the rebuild? The test asserts assignment before rebuild? It checks block includes default=... and not default=agent.max_iterations. It doesn't check that the capture is before the consumer call. Actually the block is lines from call-6 to rebuild. It checks default=agent._salience_operator_iterations in block. If capture happens after consumer call but before rebuild, the consumer would use old value (None) and maybe default? Let's see: If `_salience_operator_iterations` is captured after the call, the call's default argument would be `None` (if attribute not set) or previous value. The source test checks the default argument string is `agent._salience_operator_iterations`, not where it's assigned. If assignment is after call, the call uses the attribute from previous turn or None. If None, bounded_iterations would pass default=None? Actually `default=agent._salience_operator_iterations` if attribute is None -> default=None. In bounded_iterations, `if not session_id` returns default; but session_id exists. Then `_resolve_bounded` uses default if valid; None invalid -> uses _operator_budget. That would use config floor, not captured agent.max_iterations. This could reintroduce divergence. The source-text test doesn't check that the assignment precedes the call. It only checks block from call-6 to rebuild includes the assignment. The assignment could be after call but before rebuild and still in block. But the test asserts exactly one assignment line; if placed after call, the call uses None. Is that a concrete defect? The current code likely assigns before call. But the test's "exactly one assignment" and default string check might not catch reassignment every turn or ordering. However, the provided snippet shows assignment before call. So not a defect.

Let's look at the partial source of test_call_site_precedes_budget_rebuild: It checks `block = "\n".join(lines[max(0, call - 6):rebuild])` and asserts "default=agent._salience_operator_iterations" in block and "default=agent.max_iterations" not in block. It also checks assigns = [i for i, ln in enumerate(lines) if "agent._salience_operator_iterations =" in ln]. It doesn't show the rest of assertion but likely asserts len(assigns)==1 and the line before is is-None guard. This is robust.

Now, potential test blindness in `test_no_ratchet_across_busy_turns`: It manually passes `floor` as default each call. It doesn't use the actual agent attribute. If a developer changed the consumer to use `default=agent.max_iterations` at call site, this test would still pass because it doesn't test call site. But source-text test catches. So not a blind test for the overall guarantee, but it tests the observer logic.

Potential test blindness in `test_rollover_close_floors_at_operator_max_turns`: It tests rollover close floor from _operator_budget reading max_turns. But it doesn't test that the consumer default also reads max_turns. If _operator_budget reads max_turns but the consumer default is still 25, the durable record would be 48 but applied budgets would be based on 25. The test only checks durable record. So a bug where consumer default doesn't reflect max_turns wouldn't be caught. But is that a defect? Maybe.

Potential test blindness in `test_operator_budget_precedence_table`: It directly tests _operator_budget. If production changed _operator_budget to always return _DEFAULT_BUDGET, test red. Good. But it doesn't test integration with consumer.

Potential test blindness: `test_budget_moves_and_rounds_half_up` uses default=10. It doesn't test that the floor is the caller's default vs config. If production derived floor from _operator_budget, with config floor maybe 25, the budget would be 28 or 30, not 13, test red. Good.

Potential test blindness: `test_finalize_on_read_floor_is_caller_default` uses default=12. It catches floor derivation from _operator_budget. Good.

Potential issue with tests: They don't test resume path (cold disk read). But that's maybe out of scope.

Now, are there any concrete code defects? Let's search for subtle bugs.

Issue A: `_close_locked` with `budget=None` uses `_operator_budget()`. But `_operator_budget()` caches the first resolved budget. If the config-derived operator budget changes during a session (e.g. operator edits config), the rollover/session-end close will still use the cached value. The consumer default is captured once. So both are stable. That's documented.

Issue B: `_compute_headroom` caches. If config changes, no effect until restart. Documented.

Issue C: `_operator_budget` reads `read_raw_config_readonly` which returns dict. It doesn't deep copy. It mutates? No. Good.

Issue D: `_compute_headroom` reads salience block. If `salience` is not a dict, value = None -> 0. Good.

Issue E: `_issue_template` uses `_compute_headroom()` which returns int capped at 1e6. If floor is huge (e.g. 1e12) and headroom 1e6, max_budget = 1e12+1e6. verify_policy checks min<=max. Good. But is there any risk of overflow in `floor + _compute_headroom()`? Python ints arbitrary precision. Good.

Issue F: `_scale` in interpreter uses `_round_half_up(frac * (hi - lo))`. For huge hi-lo, float multiplication may lose precision. The cap on headroom is 1e6, so hi-lo <= 1e6. frac is float. Multiplication exact for integers up to 2^53 ~ 9e15. 1e6 fine. So no precision issue. But if floor is huge (e.g. 1e15) and headroom 1e6, hi-lo = 1e6, fine. If headroom huge beyond cap prevented. Good.

Issue G: `_round_half_up` for negative x? It says non-negative x. But _scale uses lo<=hi, frac>=0, so non-negative. Good.

Issue H: `interpret` clamps budget to [min_budget, max_budget]. The consumer applies verbatim. Good.

Issue I: `_aggregate` for ATTENTION: if there are multiple ATTENTION signals (shouldn't), it computes confidence-weighted mean. The observer synthesizes one. But other subsystems could publish ATTENTION? The bus is open? In this rig, only observer publishes. But if a malicious or other subsystem publishes ATTENTION with lower confidence, it could dilute the attention. Out of scope? The bus contract allows any subsystem. The observer is sole ATTENTION publisher per comment. Not a defect.

Issue J: `_aggregate` omits facet if weight==0. For ATTENTION, if attention signal has confidence 0 (not the case), it would be omitted and budget floor. Not relevant.

Issue K: `_valid_directive_shape` checks compute_budget is int not bool. Good. But on replay, `_valid_directive_payload` checks int not bool. Good.

Issue L: In `_bus_for`, the bus path is under get_hermes_home() / "salience". If get_hermes_home() returns a Path or string? It uses Path(...). Good.

Issue M: `_session_hash` uses sha256 of session_id. If session_id is empty string, hash still works. _subject uses head + tail. If turn_id too long, hashes. Good.

Issue N: `_subject` truncation: head = hash16 + ":"; room = 128 - len(head). tail = turn_id if len <= room else hash(turn_id). Then `(head + tail)[:MAX_TOKEN_LEN]`. If turn_id hashed, tail is 64 hex chars, head 17, total 81, fine. If turn_id exactly fits, total <=128. Good.

Issue O: `_record` increments `window.events` before mapper. If mapper raises, event counted but no signal. As discussed.

Issue P: `_record` uses `_bus_for(session_id)` which may construct a new bus and replay. If the file is corrupt, constructing bus raises, and the exception is caught in _record (logs). The event is counted but no signal. The window remains open. At close, _bus_for may raise again, causing close failure and _LAST_DIRECTIVE popped. Good.

Issue Q: `_close_session` calls `_close_locked(window)` then pops bus. If `_close_locked` raises, exception propagates to observe_lifecycle, which logs. But `_BUSES.pop` and `_LAST_DIRECTIVE.pop` are not executed, causing leak? Wait `_close_session` is wrapped in `try` by `observe_lifecycle`. If `_close_locked` raises, the `except` catches and logs, but the rest of `_close_session` (popping bus and _LAST_DIRECTIVE) is skipped. That means on session end, if close fails, the bus and last directive are not freed, causing per-session leak. However, `_close_locked` catches its own exceptions and logs; it shouldn't raise to `_close_session`. It catches (Exception, SystemExit). So `_close_session` will proceed. But what if `_bus_for` raises? _close_locked catches. So no leak. Good.

But wait: `_close_locked` catches exceptions and logs, but it also re-raises? No, it catches and doesn't re-raise. So `_close_session` continues. Good.

Issue R: `_open_window` calls `_close_locked(current)` without try. If `_close_locked` raises? It doesn't. Good.

Issue S: `_ensure_template_valid` uses `_issue_template("salience.template.probe", _operator_budget())`. It holds _LOCK. Good.

Issue T: `_resolve_bounded` calls `_ensure_template_valid` then closes window. If `_ensure_template_valid` logs error, no effect. Good.

Issue U: In `_resolve_bounded`, after closing window, it reads `_LAST_DIRECTIVE`. But `_close_locked` sets `_LAST_DIRECTIVE` only on successful emit. If close failed, _LAST_DIRECTIVE popped. Then _budget_from_disk may recover from disk if bus not cached. But if bus is cached and close failed, _budget_from_disk returns None. Good.

But there is a subtle issue: `_close_locked` may fail to emit due to bus error; it pops _LAST_DIRECTIVE. However, the previous _LAST_DIRECTIVE (from two turns ago) might still be present if the prior close also failed? Actually each close replaces _LAST_DIRECTIVE on success. If a close fails, it pops. So no stale. Good.

Issue V: `_budget_from_disk` constructs bus and promotes payload. It sets `_LAST_DIRECTIVE[session_id] = payload`. Then `_resolve_bounded` returns `_directive_budget(payload)`. Good. But if `_directive_budget` rejects the payload (e.g. budget 0), it returns None. However, `_LAST_DIRECTIVE` still holds the rejected payload. Next call to `_resolve_bounded` will read `_LAST_DIRECTIVE` and reject again. Is that a problem? A hard-deny payload has empty subject/policy_id and budget 0. _directive_budget returns None. _LAST_DIRECTIVE holds it. Fine.

Issue W: `_budget_from_disk` deepcopies payload. Good.

Issue X: `bounded_iterations` with invalid default returns default untouched. The caller then uses it. But the caller's `default` is captured pristine int. Good.

Issue Y: The call site in turn_context.py catches `except Exception`, not SystemExit. But bounded_iterations catches SystemExit. The import could raise SystemExit? If import fails with SystemExit, it would propagate. The comment says catches SystemExit but code doesn't. This is a doc/code inconsistency. Is it a concrete defect? If a host API sys.exit()s during import, it would escape. But imports rarely raise SystemExit. Could be LOW. But the prompt says "any path in the CHANGED code that crashes the host turn, escapes the (Exception, SystemExit) containment, or yields an applied budget < 1." The changed code in turn_context.py uses `except Exception`, which would not catch SystemExit. However, the function it calls catches SystemExit. So the only SystemExit path is the import. If `hermes_cli.observability.salience_observer` import raises SystemExit (e.g. due to a sys.exit in module-level code), it would crash the turn. Is that plausible? The module-level code has `try/except` around salienceos import and sets _IMPORT_OK false. It doesn't sys.exit. The `_config_flag` catches SystemExit. So import likely safe. But the discrepancy is a minor issue.

Issue Z: In `turn_context.py`, the consumer call is inside `try: ... except Exception: logger.debug(...)`. If `bounded_iterations` returns a non-int (shouldn't), then `IterationBudget(agent.max_iterations)` may raise. But bounded_iterations returns int or default. Good.

Now, let's think about A3 stale/self-read more concretely. The `_resolve_bounded` function closes the prior window. But what if the prior window was already closed by `_open_window` (rollover) before the consumer runs? In normal flow, pre_llm_call hasn't run yet, so no rollover. But if a tool call from prior turn arrives after pre_llm_call of next turn? The lifecycle dispatch order likely prevents. But if `_record` is called with a stale turn_id after the window was closed/rollover? It returns. Good.

What about the scenario where `bounded_iterations` is called, which closes prior window and caches directive. Then before pre_llm_call, some other code emits a tool call for the prior turn? Not realistic.

What about `_LAST_DIRECTIVE` being read from disk on cold start: `_budget_from_disk` constructs bus, replays, and returns last directive. But it doesn't close any window (there shouldn't be an open window). It returns the last recorded budget. This is the resume caveat. Good.

Now, think about ESCALATION via `_operator_budget` and `_compute_headroom` caches being cleared by `_reset_for_tests`. In production, no reset. Good.

Potential issue: `_compute_headroom` accepts a digit-string but not a string with leading plus or whitespace? It strips. Good.

Potential issue: `_compute_headroom` accepts `value = 0` (int) -> headroom 0. Good. Bool True -> 0. Good.

Potential issue: `_compute_headroom` if value is a numpy int64? isinstance(value, int) false. It would be ignored -> 0. Maybe acceptable. Not a defect.

Potential issue: `_operator_budget` if value is a string "25"? It requires int. Good.

Potential issue: `_operator_budget` if value is a bool? It requires int not bool. Good.

Potential issue: `_operator_budget` precedence includes `max_iterations` at top level. But the consumer default captures `agent.max_iterations`. If config has top-level `max_iterations: 9` and no agent.max_iterations, _operator_budget returns 9, consumer default 25. Divergence. Again, same theme. But maybe agent.max_iterations is set from top-level max_iterations by host. Not shown.

Let's examine the actual agent/turn_context.py snippet: It sets `agent.iteration_budget = IterationBudget(agent.max_iterations)`. The consumer modifies agent.max_iterations. The pristine capture is agent.max_iterations. So the host's operator budget is whatever agent.max_iterations is. The _operator_budget function is used only for produce-path closes (rollover/session-end) and as fallback in _resolve_bounded when default invalid. If _operator_budget reads a different key than the one used to set agent.max_iterations, the durable record floor may differ. But the applied floor is agent.max_iterations. The guarantee G1 is about operator budget; if _operator_budget is the source of truth, then applied could be wrong. But if agent.max_iterations is the source of truth, _operator_budget is wrong for rollover. The PR attempted to align them by adding max_turns to _operator_budget, but didn't ensure consumer default reads the same resolution.

This is likely the most concrete missed issue: **divergence between consumer default and _operator_budget when the operator budget is configured via `agent.max_turns` or top-level `max_iterations` and not via `agent.max_iterations`.** The consumer call site captures `agent.max_iterations`, while `_operator_budget` may resolve `agent.max_turns`. In a stock config that sets only `max_turns`, the consumer would apply a floor of `_DEFAULT_BUDGET` (25) while the producer's rollover/session-end directives would floor at `max_turns` (e.g., 500). This violates G1/G3 within the process: the applied budget could be below the operator's real floor, and the durable record could be above the applied budget. It also creates a resume caveat where the recorded budget uses a different floor.

But is this a PR-H3 defect? The PR claims to fix the stock-config blind spot. The fix in `_operator_budget` reads max_turns, but the consumer default does not. The source-text pin test only checks that default is the captured attribute, not that the attribute equals max_turns. So internal reviews missed that the call site still anchors to `agent.max_iterations`. The PR's own test `test_rollover_close_floors_at_operator_max_turns` passes because it only checks durable record, not applied budget. The consumer default in real host would be wrong if agent.max_iterations not set.

Need decide if this is concrete enough. We can construct a concrete trigger: a config with `agent: {max_turns: 40}` and no `max_iterations`, and an agent whose `max_iterations` is still the fallback 25. Then `bounded_iterations("s", 25)` returns 25 (the captured default), while `_operator_budget()` returns 40. The first rollover close would record a directive with floor 40 + headroom, but the consumer would never apply that floor. However, the consumer default is passed as 25 by the host. If the host passes 25, that's the floor. The host's default is wrong relative to config. The PR could fix by making the consumer default resolve via the same `_operator_budget` path, not just `agent.max_iterations`. But the no-ratchet fix requires capturing once. They could capture the resolved operator budget using `_operator_budget()` at first call, instead of `agent.max_iterations`. But `_operator_budget()` is memoized and reads config. That would align.

Wait, but `_operator_budget()` is intended to be called under _LOCK. At call site, no _LOCK. Could cause race? The cache is global; reading is safe. Calling it without lock could trigger cache write race if multiple agents first-call concurrently. But the existing code calls `_operator_budget` only under _LOCK. If call site calls it outside lock, need ensure thread safety. The cache write to global is not atomic? In CPython, assignment is atomic, but reading config and writing could be racy. However, bounded_iterations itself acquires _LOCK for _resolve_bounded. The call site could call `_salience_observer._operator_budget()` before entering bounded_iterations? But it's not exposed? It is module-level function. Could be called to capture pristine. But the design says capture once per agent using agent.max_iterations to avoid lock. If we instead capture by calling `_operator_budget()`, we'd need to handle concurrency. Maybe that's why they used agent.max_iterations.

But the issue remains: if agent.max_iterations is not the resolved operator budget, the no-ratchet fix is incomplete. The PR's own context says "pre-H3 a stock config floored produce-path closes at the 25 fallback while the real budget was e.g. 500." They fixed produce-path. But the consumer default still uses agent.max_iterations. If the stock config uses max_turns, does the host set agent.max_iterations to max_turns? The context says "_operator_budget now also reads agent.max_turns / max_turns (the host's REAL operator chain) after the rig keys — pre-H3 a stock config floored produce-path closes at the 25 fallback while the real budget was e.g. 500." It implies the host's real budget is in max_turns, and agent.max_iterations may not reflect it. The consumer call site captures agent.max_iterations. So the consumer would still use 25. This is a real missed issue.

But wait, the turn_context.py snippet comment says "PR-H3: govern from the operator's PRISTINE budget, never from the previously governed value. The assignment below feeds agent.max_iterations forward into the next turn, so flooring on it would COMPOUND... The pristine value is captured once per agent". They assume agent.max_iterations IS the pristine operator budget. In the host, maybe agent.max_iterations is set from max_turns at agent construction. The PR didn't need to change that. But the prompt says "the host's REAL operator chain in the host's own order (agent.max_turns before root max_turns — mirrors cli.py's resolution; cli feeds the agent's max_iterations from max_turns)". Actually the comment in _operator_budget says "mirrors cli.py's resolution; cli feeds the agent's max_iterations from max_turns". So maybe cli.py sets agent.max_iterations from max_turns. If so, agent.max_iterations is the resolved value. Then no divergence. The issue is moot.

But the prompt explicitly says "the host's REAL operator chain in the host's own order (agent.max_turns before root max_turns — mirrors cli.py's resolution; cli feeds the agent's max_iterations from max_turns)". So the host does feed agent.max_iterations from max_turns. Thus the consumer default is correct. Good.

However, the source-text pin test doesn't verify that agent.max_iterations is set from max_turns; it just checks the call site. If a future change breaks that, the consumer would be wrong. But not a current defect.

Let's consider other paths: The box ferry `_write_hermes_config` writes a config.yaml with agent.max_iterations and salience.compute_headroom. It doesn't write max_turns. So agent.max_iterations is set. Good.

Now, what about the `_operator_budget` fallback to `_DEFAULT_BUDGET` when no keys. The consumer default is agent.max_iterations. If agent.max_iterations is not
