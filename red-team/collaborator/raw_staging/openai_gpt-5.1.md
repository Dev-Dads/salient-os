# Staging subsystem red-team: openai/gpt-5.1

_finish=stop seconds=36.3 cost=$0.0578 usage={'prompt_tokens': 24547, 'completion_tokens': 2711, 'total_tokens': 27258, 'cost': 0.05779375, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.05779375, 'upstream_inference_prompt_cost': 0.03068375, 'upstream_inference_completions_cost': 0.02711}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID 1 / TITLE Ancestor exemption does not hide destructive mutations / SEVERITY LOW / LOCATION salienceos/verifier/observers.py:86-137 / CONCRETE INPUT OR BYPASS See below per sub‑case / WHY IT BREAKS A GUARANTEE None of the probed scenarios actually break the “no undeclared mutation” guarantee; the exemption is correctly bounded to new dirs only and other obligations catch the remaining edges / WHETHER ANOTHER CHECK CATCHES IT Yes, where applicable / FIX Mostly clarifications and maybe extra tests; no logic change strictly required.

I’ll walk the requested attack surface for the entailed‑ancestor exemption and spell out where a real gap would be, and why it isn’t present:

1. Pre‑existing ancestor as FILE replaced by dir (file→dir)
   - Concrete attempt: Prestate has `logs/app` as a file; do a `file.write` on `logs/app/run.txt` by:
     - unlinking `logs/app` file
     - creating directory `logs/app/`
     - writing `logs/app/run.txt`
   - Code path:
     - `pre_snapshot["logs/app"]` exists and is not `"dir"`.
     - In `observe_action`, `subjects = ["logs/app/run.txt"]`; iterating `entailed_ancestors("logs/app/run.txt")` includes `"logs/app"`.
     - The exemption condition is `anc not in pre_snapshot and post.get(anc) == "dir"`.
       - Here `anc="logs/app"` *is* in `pre_snapshot`, so it is NOT exempted.
     - `observed_write_set` sees `pre["logs/app"] != post["logs/app"]` (file marker vs `"dir"`) and reports `"logs/app"` as a changed, undeclared path.
   - Verifier result vs reality:
     - Contract write_set expectation is just `["logs/app/run.txt"]`.
     - Observed write_set contains at least `["logs/app", "logs/app/run.txt"]`.
     - Boundary obligation fails: undeclared change.
   - Independent checks:
     - `artifact_hash` for `logs/app/run.txt` will match (child was written), so only boundary detects the damage, which it does.
   - Finding: no bypass; the *transition* guard (`anc not in pre_snapshot`) is doing the right thing. Tests explicitly pin this (tests/test_verifier_nested_write.py::ExemptionByTransitionNotName.test_file_to_dir_ancestor_replacement_is_caught).

2. Ancestor swapped for a symlink, or symlink→dir
   - Concrete attempt A: Prestate has `a` as `symlink:target`; executor removes it and creates `a/` as a real dir to house `a/b/c.txt`.
     - `pre_snapshot["a"]` exists and is `"symlink:..."`.
     - Ancestor `"a"` fails the `anc not in pre_snapshot` check; not exempted.
     - Boundary sees `"a"` transition `symlink:...` → `"dir"`; undeclared change; obligation fails.
   - Concrete attempt B: Prestate has real dir `a/`; executor replaces it with symlink `a -> somewhere` while also writing `a/b/c.txt` via some trick.
     - Snapshot_tree walks dirs with `followlinks=False`; a symlinked `a` is recorded as `symlink:...` but not recursed into.
     - To get `a/b/c.txt` as a real file, the symlink must exist *before* the snapshot (then `post["a"]` is `symlink:...`, not `"dir"`) or be created after writing the file, in which case `post["a"]` is `symlink:..` and `post` has no `a/b/c.txt`.
     - Either way: `a` is present in `pre` or `post` but never eligible for the `not in pre_snapshot and post == "dir"` exemption, so type change is visible.
   - Independent checks:
     - `rehash` explicitly treats a symlink at the declared **leaf** path as `"absent"`; you can’t turn the declared write target into a symlink and still verify.
   - Finding: attacks that rely on ancestor type changes are blocked by the transition guard and/or `rehash`.

3. Pre‑existing ancestor FILE deleted/overwritten as a side effect
   - Concrete attempt: Have `a/b/c.txt` as *file*, then issue `file.write` to `a/b/c.txt/child.txt` by:
     - `rm a/b/c.txt`
     - `mkdir -p a/b/c.txt`
     - `echo "x" > a/b/c.txt/child.txt`
   - Effect:
     - `pre_snapshot["a/b/c.txt"]` exists as a file hash.
     - `post` records `"a/b/c.txt": "dir"` and `"a/b/c.txt/child.txt": <hash>`.
     - `entailed_ancestors("a/b/c.txt/child.txt")` includes `"a/b/c.txt"`, but again `anc in pre_snapshot`, so not exempt.
   - Verifier:
     - Declared path is `"a/b/c.txt/child.txt"`; boundary sees `"a/b/c.txt"` changed file→dir, undeclared; fails.
   - Independent checks:
     - As before, only write_set boundary sees it, and it does.
   - Finding: no hole: transition guard prevents masking destruction of pre‑existing files in ancestor positions.

4. Declared path crafted so its “ancestors” are a sensitive existing directory (e.g., `.github`)
   - Concrete attempt: workspace has real `.github/` with workflows. Execute benign `file.write("staged/.github/fake.yml")` and rely on exemption to hide `staged/.github/` creation, or more aggressively try `file.write(".github/workflows/ci.yml")` and hide `.github/` itself.
   - Behavior:
     - If `.github` exists beforehand, `pre_snapshot` contains `.github`; `entailed_ancestors(".github/workflows/ci.yml")` includes `.github`; but `anc in pre_snapshot`, so no exemption. The existing sensitive dir is *not* hidden.
     - For `staged/.github/fake.yml`, entailed ancestors are `["staged", "staged/.github"]`.
       - If `staged` did not exist: `pre_snapshot` has no `"staged"`, and if executor created it as a dir, `post["staged"] == "dir"` → `"staged"` is exempt.
       - `staged/.github` also may be exempt if it was absent and becomes `"dir"`.
       - These are *not* the top‑level `.github`; they are scratch subtrees. Nothing sensitive about them; they do not collide with GitHub CI’s controlled root `.github`. The correct outcome is to *not* treat them as undeclared damage.
   - Independent checks:
     - Controlled‑location enforcement for `.github/**` happens in governance based on the **declared path**, not the write‑set; entailed‑ancestor exemptions don’t affect that check.
   - Finding: this does not create a bypass; only scratch‑namespace ancestors (previously absent) are exempted, and sensitive pre‑existing `.github` is still visible in the diff.

5. Interaction with `dir.make`, `file.delete`, `shell.run` declared_outputs
   - `dir.make`:
     - `builder` for `"dir.make"` sets kind `"path_state"` and subjects `[path]`.
     - observe_action still computes `exempt` based on entailed ancestors, but:
       - Contract write_set expectation for `dir.make("a/b")` is `["a/b"]`.
       - If `a` pre‑exists: `a in pre_snapshot` → not exempt; any change to `a` is visible.
       - If `a` was absent and becomes a dir, `a` can be exempted, which is correct: `dir.make("a/b")` *entails* creating `a/`.
     - There is an explicit test for destructive ancestor removals in the delete case; see below.
   - `file.delete`:
     - builder expects write_set `["a/b/c.txt"]`.
     - observe_action has kind `"path_state"`; entailed ancestors of `"a/b/c.txt"` are `["a", "a/b"]`.
     - Deletion that also `shutil.rmtree("a/b")`:
       - `pre_snapshot["a/b"] == "dir"`.
       - In post, `"a/b"` is missing.
       - Since `"a/b" in pre_snapshot`, it is *never* exempt.
       - Boundary sees undeclared removal of `"a/b"`. Pinned by test_delete_that_removes_an_ancestor_dir_is_caught.
   - `shell.run`:
     - Declared outputs become `subjects` with `kind == "artifact"`.
     - entailed_ancestors exemption only covers ancestor dirs that were *absolutely absent* before and now are `"dir"`.
     - Any undeclared sibling/other mutation is still present in `observed_write_set` (test_unrelated_undeclared_write_is_STILL_caught proves this).
   - Finding: behavior is consistent and correctly bounded; parent dirs auto‑created to satisfy declared paths are exempt iff they’re genuinely new and non‑destructive.

6. Boundedness of exemption to the declared path’s parent chain
   - Concrete attempt: cause the exemption to drop unrelated paths by path‑mangling:
     - Use weird slashes: `"a\\b\\c.txt"` — entailed_ancestors normalizes to `["a", "a/b"]`.
     - That cannot produce ancestors outside the chain of components produced by `.split("/")`.
     - No use of `..` is permitted at this layer because:
       - The executor tool (`write_file`) passes the *raw* `rel` to `snapshot_tree` and `observe_action`, but paths like `"../evil.txt"` are blocked at execution time by `resolve_in_workspace` *before* the write, and `_resolve_within` in observers similarly treats them as escape and returns `None`.
       - entailed_ancestors is string‑based and will produce `".."` and ` "../x"` style components, but those paths will never show up in `pre`/`post` snapshots to begin with, because tools cannot create them outside the workspace root.
   - There is an explicit test verifying that a non‑ancestor changed path (`evil.txt`) is still caught.
   - Finding: the exemption truly is confined to strict ancestors of declared relative paths; it cannot be abused to drop arbitrary unrelated entries.

7. Relationship to `rehash()/path_state()/exit`
   - `rehash`:
     - Ensures the declared leaf path is a regular file under the workspace, not a symlink or escape. Any attempt to pivot the declared path into a symlink or non‑file yields `"absent"`, failing the `artifact_hash` obligation even if write_set boundary passed (it doesn’t).
   - `path_state`:
     - Checks the declared path is `present:dir` or `absent` as expected for `dir.make` / `file.delete`.
     - Ancestors have no separate path_state obligation for `file.write`/`shell.run`, but as argued above, destructive changes to them are detected by write_set and not exempted.
   - `exit`:
     - Gives a supervisor exit code; not directly relevant to hiding undeclared writes, but ensures that we don’t accept a fabricated success after a failing executor.
   - Finding: the write‑set exemption is the only code that could hide mutations on ancestor dirs, and its transition‑based gating prevents that; `rehash` and `path_state` independently guard the declared paths themselves and type/escape shenanigans on the leaf.

Steelman: The exemption is deliberately narrow: it only drops newly‑created parent directories that did not exist at all in the pre‑snapshot and that are still dirs in the post‑snapshot; every pre‑existing file/symlink/dir ancestor, and every removal or type‑change, still shows in the diff. Tests explicitly pin the previously‑found red‑team failures (file→dir replacement and ancestor deletion) and verify that unrelated writes remain detectable, which significantly reduces the risk that a future refactor re‑opens this class of bug. The behavior is somewhat subtle, but the code matches the stated design.

Verdict: SOUND — the entailed‑ancestor exemption is correctly bounded by transition and does not create a real verifier‑bypass; any destructive or undeclared mutation of pre‑existing ancestors still trips the write‑set boundary, and other obligations cover leaf path type/escape.
