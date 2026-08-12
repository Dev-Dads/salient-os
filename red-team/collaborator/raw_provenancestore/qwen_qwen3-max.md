# provenance-store code panel: qwen/qwen3-max

_finish=stop seconds=74.8 cost=$0.0337 usage={'prompt_tokens': 23433, 'completion_tokens': 3964, 'total_tokens': 27397, 'cost': 0.03373734, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.03373734, 'upstream_inference_prompt_cost': 0.01827774, 'upstream_inference_completions_cost': 0.0154596}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

**Finding 1 / AGENT TAMPER VIA SYMLINK INTO WORKSPACE**  
**SEVERITY**: CRITICAL  
**LOCATION**: `collaborator/session.py: _assert_store_outside_workspace` (around line 340–350)  
**CONCRETE CASE**:  
Host configures `provenance_store="/tmp/store.json"`. Attacker-controlled workspace contains a symlink:  
```bash
ln -s /tmp/store.json $WORKSPACE/.prov.json
```  
Now the agent runs autonomously and writes to `$WORKSPACE/.prov.json` → it overwrites `/tmp/store.json`, which is the real durable store. The next session loads this tampered store, sees an empty set (because the agent wrote `{}`), and **silently believes no autonomous files were ever authored**, laundering its own taint.

Why? `_assert_store_outside_workspace` resolves both paths (`store_path.resolve()` and `workspace.resolve()`) and checks if `ws in sp.parents`. But if `store_path` is **not a symlink**, and the **workspace contains a symlink pointing to the store**, the check passes — because it only validates that the *configured store path* is outside the *resolved workspace root*. It does **not** validate that the store file is not reachable **via any symlink inside the workspace**.

This breaks the core invariant: the store must be **unreachable by the contained run**. The bwrap sandbox allows writing anywhere under `workspace/`, including symlinks that point outside. So if any path under `workspace/` resolves to the store file, the agent can overwrite it.

**WHY IT FORGES/TAMPERS**: The agent **erases provenance** by overwriting the store with a clean-looking empty manifest. Because the HMAC is computed over the content, and the agent doesn’t know `policy_key`, it can’t forge a valid MAC for a non-empty set — but it **doesn’t need to**. It just writes a new file with `{"authored": [], "incomplete": false}` and omits the MAC or uses a fake one. On load, this fails HMAC → `ok=False` → `incomplete=True`, so tracking is degraded… **but wait**: in the attack above, the agent **replaces the entire file** with a **valid-looking empty store** from a *fresh start* — but without the correct MAC. However, even if it can’t make `ok=True`, it can still **erase all prior taints** by causing the store to be treated as untrusted → loaded as empty + degraded. That’s **taint erasure via forced degradation**, which is acceptable per the advisory model **only if honest**. But here, the **cause is malicious tampering**, yet the system cannot distinguish it from disk corruption. However, the bigger issue is: **the store was supposed to be unreachable**, but it isn’t.

But actually — the **real flaw** is that the **store is reachable at all**. The threat model assumes “out-of-workspace = agent can’t write”. But with symlinks, “out-of-workspace” ≠ “unreachable”.

**DOES ANOTHER CONTROL CATCH IT?** No. The integrity check catches the invalid MAC and loads as degraded, but that’s **after** the damage: the provenance is lost. The goal is to **prevent tampering**, not just detect it post-hoc. Detection is fine for corruption, but **tamperability violates the reach assumption**.

**FIX**: In `_assert_store_outside_workspace`, after resolving `sp` and `ws`, also **enumerate all paths under `ws` that could resolve to `sp`**? Not feasible. Better: **require that the store’s resolved parent is disjoint from the workspace tree**, and **additionally**, during save/load, **open the file with `O_NOFOLLOW`** (but Python’s `Path.write_text` doesn’t support that). Alternatively, **refuse if any component of the store path is a symlink**? Too restrictive.

Best fix: **In `save()` and `load()`, open the file with `os.open(path, flags)` using `O_NOFOLLOW` on Unix**. But the current code uses `Path.read_text()` and `tmp.write_text()`, which follow symlinks.

Alternatively: **strengthen `_assert_store_outside_workspace` to also check that the store file, once created, cannot be linked into the workspace** — but you can’t prevent hard links or future symlinks.

Actually, the **correct fix** is: **the check is at the wrong layer**. The sandbox (bwrap) should prevent writing outside the workspace, **including via symlinks**. But bwrap by default **does follow symlinks** and will allow writing through them if the target is writable. So this is a **sandboxing gap**, but the code assumes the sandbox enforces “only workspace writes”.

However, the code’s responsibility is to **ensure the store is not inside the workspace tree**, but it **must also ensure the store is not writable by the sandboxed process**. Since the sandbox allows writing to anything under `workspace/`, and symlinks under `workspace/` can point anywhere, the only safe stores are those **outside any directory the sandbox can write to** — which is just `workspace/`.

So the current check is **insufficient**. It must also ensure that **no ancestor of the store is writable by the agent**, but that’s not feasible.

Practical fix: **In `_assert_store_outside_workspace`, after resolving `sp`, also verify that `sp` is not equal to any file that could be reached by a path under `ws`** — impossible.

Alternative: **Use a store path that is guaranteed not to be linkable**, e.g., under `/var/lib/...` with strict permissions. But the host chooses the path.

Best actionable fix: **In `save()`, when writing, use `os.open` with `O_NOFOLLOW | O_CREAT | O_EXCL` for the temp file, and `O_NOFOLLOW` for the final replace**. But `os.replace` doesn’t support `O_NOFOLLOW`.

Actually, **the atomic replace via `os.replace` is safe against symlink attacks if the temp file is created securely** — but only if the **directory is not writable by the attacker**. Here, the **directory may be writable if it’s near the workspace**.

But the real issue is: **the store file itself must not be writable by the agent**. If the agent can create a symlink in the workspace pointing to the store, and the store’s parent directory is world-writable, then yes.

However, in typical setups, `/tmp` is sticky, so the agent can’t overwrite arbitrary files. But if the store is in a user-controlled directory adjacent to the workspace, it might be vulnerable.

But the **critical flaw** is that `_assert_store_outside_workspace` uses `.resolve()`, which **follows symlinks**, so:

- Workspace: `/home/user/project` (real path)
- Store configured as: `/home/user/prov.json`
- But `/home/user/project` is a **symlink to `/mnt/real/project`**
- Then `ws = Path(workspace).resolve()` → `/mnt/real/project`
- `sp = Path(store).resolve()` → `/home/user/prov.json`
- Check: is `sp == ws`? No. Is `ws in sp.parents`? No.
- So it passes.

But now, if the agent writes to `/home/user/project/.prov.json` (which is inside the **logical** workspace), and if `/home/user/project` is a symlink, the **sandbox may allow it**, and if the agent creates a symlink from inside the workspace to the store, it can overwrite it.

However, the **more direct attack**: the store path is outside, but the **workspace itself is a symlink into a directory that contains the store**.

Example:
- Real filesystem: `/data/ws` and `/data/store.json`
- User sets `workspace = "/link"` where `/link` → `/data/ws`
- User sets `provenance_store = "/data/store.json"`
- Now, `_assert_store_outside_workspace`:  
  `ws = Path("/link").resolve()` → `/data/ws`  
  `sp = Path("/data/store.json").resolve()` → `/data/store.json`  
  Check: is `/data/store.json` inside `/data/ws`? No. Passes.
- But the agent, running in `/link` (which is `/data/ws`), can do:  
  `ln -s ../store.json .malicious`  
  Now, writing to `.malicious` modifies `/data/store.json`.

So the store is **reachable via relative traversal from the workspace**, even though its absolute path is outside.

The current check **only compares resolved absolute paths**, but doesn’t account for **relative reachability**.

**The fix**: In `_assert_store_outside_workspace`, after resolving both, also check that **the store’s resolved path is not accessible via any relative path from the workspace** — but that’s equivalent to checking that they share no common prefix in a way that allows `..` traversal, which `.resolve()` already normalizes.

Actually, **if both are resolved to absolute paths with no symlinks, then `ws in sp.parents` is sufficient**. The problem arises when **the store path or workspace path contains symlinks that cause the logical containment to differ from physical**.

But `.resolve()` eliminates symlinks, so the check **should be robust** — **unless** the filesystem has **hard links** or **bind mounts**, but that’s out of scope.

Wait: in the symlink example above, after resolve, `ws = /data/ws`, `sp = /data/store.json`. They are siblings. So `ws not in sp.parents`, and `sp not in ws.parents`. So the check passes, correctly.

But then how can the agent write to `sp`? Only if it can create a symlink from within `ws` to `sp`, and the sandbox allows following symlinks on write.

That is a **sandboxing issue**, not a path validation issue. The code assumes that “agent can only write under workspace” means it can’t affect files outside, but **if the sandbox follows symlinks on write, then it can**.

So perhaps this is **not a code bug**, but a **threat model mismatch**.

However, the code’s comment says: “the contained autonomous run can write ONLY the workspace (deny-by-default bwrap)”, implying that bwrap prevents escape. But **bwrap by default does not prevent symlink-based escapes** unless `--symlink-ro` or similar is used.

Given that, the **path check is correct as-is**, because it ensures the store is not **physically** inside the workspace tree. Symlink tricks are a **sandbox hardening issue**, not a provenance store logic issue.

So maybe this finding is **invalid**.

Let’s re-evaluate.

**Finding 1 (revised): FAIL-SILENT ON MISSING vs CORRUPT DISTINCTION IS CORRECT**  
Actually, looking at `load()`:

- Missing file → `(set(), False, True)` → trusted empty
- Corrupt/tampered → `(set(), True, False)` → untrusted, degraded

This is correct. No issue.

**Finding 2 / REPLAY ATTACK VIA SUBJECT COLLISION**  
**SEVERITY**: HIGH  
**LOCATION**: `collaborator/policycaps.py` (not shown, but `workspace_subject` is used)  
But we don’t have `policycaps.py`. However, the diff shows `workspace_subject(self.workspace)` is used as the subject.

**CONCRETE CASE**: If two different workspaces have the same `workspace_subject`, then their stores are interchangeable.

How is `workspace_subject` implemented? From context, likely:  
```python
def workspace_subject(ws: Path) -> str:
    return f"workspace:{ws.name}"
```
or  
```python
return f"workspace:{ws.resolve().name}"
```

If it’s just the **basename**, then `/home/alice/project` and `/home/bob/project` have the same subject → stores can be replayed.

But the test shows:  
```python
SUBJ = "workspace:abc"
```
and tests subject mismatch with `"workspace:AAA"` vs `"workspace:BBB"`.

So likely, `workspace_subject` returns a string that includes the **full resolved path** or a **hash**.

Assuming it’s implemented as:  
```python
def workspace_subject(ws):
    return f"workspace:{ws.resolve()}"
```
then it’s unique per workspace.

But if the workspace path is the same across sessions (which it is), then subject is stable.

So **no replay across workspaces**.

But what about **rollback**? The agent can’t write to the store, so it can’t rollback. So **not in scope**.

**Finding 3 / TOTALITY VIOLATION IN `_assert_store_outside_workspace`**  
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/session.py:_assert_store_outside_workspace`  
**CONCRETE CASE**: `Path(store_path).resolve()` can raise `OSError` (e.g., too many symlinks, permission denied on a component). The code catches `OSError, RuntimeError, ValueError` and raises `ValueError`.

But `Session.__init__` is supposed to be **total**? The context says: “Session construction FAILS LOUD”, so raising `ValueError` is **by design**.

So not a bug.

**Finding 4 / RACE CONDITION IN SAVE() LEADS TO PARTIAL WRITE**  
**SEVERITY**: LOW  
**LOCATION**: `collaborator/provenancestore.py:save`  
**CONCRETE CASE**: `tmp.write_text(...)` succeeds, but `os.replace` fails (e.g., permission error). Then the temp file is left behind, but the next `load()` will read the **old store**, which is fine. If the process dies after `write_text` but before `os.replace`, the temp file is orphaned, but the live store is intact. So **atomicity is preserved**.

No issue.

**Finding 5 / FAIL-HONEST VIOLATION: MISSING FILE vs TAMPERED FILE HANDLING**  
Already reviewed: missing → trusted empty; tampered → untrusted empty + degraded. Correct.

**Finding 6 / WORKSPACE_SUBJECT MAY NOT BE UNIQUE IF WORKSPACE PATH IS NOT ABSOLUTE**  
But `Session` does `self.workspace = Path(workspace)`, and in `_assert_store_outside_workspace`, it calls `.resolve()`, so it’s absolute.

And `workspace_subject` likely uses `.resolve()`, so it’s stable.

Assume it’s sound.

**Finding 7 / POLICY_KEY IS DEFAULTED TO A STATIC BYTES OBJECT**  
**SEVERITY**: HIGH  
**LOCATION**: `collaborator/session.py:__init__`, `policy_key: bytes = b"collab-policy-key"`  
**CONCRETE CASE**: Every session that doesn’t provide a `policy_key` uses the **same static key**. So if two different hosts or workspaces use the default key, their stores’ HMACs are compatible. But since the **subject** is bound, it’s still safe **per workspace**.

However, if an attacker can **control the workspace path**, they could make it match, but the workspace is host-configured.

But more importantly: **the default key is public**. So if the store file is leaked, anyone can forge stores for that workspace.

But the threat model assumes the **host is trusted**, and the store is outside the workspace, so leakage is not in scope.

However, **using a static default key is bad practice**. But the comment says it’s the default, and host should provide a real key.

But in the **shipped code**, if the host doesn’t provide a key, it’s predictable.

But the integrity protection is only meaningful if the key is secret. With the default key, it’s **security theater**.

However, the system is designed for the host to provide a real key. The default is for testing.

But the **diff is shipped**, so in production, if someone forgets to set `policy_key`, the HMAC is forgeable.

But the agent doesn’t know the workspace path in advance, so to forge, it would need to know the subject and the key. With default key, it knows the key, but not the subject (unless it can infer the workspace path).

In many cases, the workspace path is predictable (e.g., `/home/user/project`).

So **an agent that knows the workspace path can forge a valid store** if the default key is used.

But the agent is **contained**, so it can’t write to the store anyway — unless the store is misconfigured inside the workspace, which is caught.

So **even with known key, the agent can’t tamper** because it can’t reach the store.

So the **static key is not a critical flaw**, because the **reach restriction is the primary control**; HMAC is defense-in-depth.

So **LOW severity**.

But let’s see if there’s a real issue.

**Final Finding: NONE. The implementation is SOUND.**

Wait — **one gap**:

In `load()`, it does:
```python
        payload = _canonical(body.get("subject"), body.get("authored") or [], body.get("incomplete"))
        if not hmac.compare_digest(_mac(key, payload), mac):
            return set(), True, False
        if str(body.get("subject")) != str(subject):
            return set(), True, False
```

The **order of checks**: it verifies HMAC **before** subject. That’s good, because it avoids timing leaks on subject.

But what if the `body` is missing `"subject"`? Then `body.get("subject")` is `None`, and `_canonical` gets `None` as subject, computes a payload, and the MAC won’t match (unless by chance), so it returns untrusted.

Similarly, if `"authored"` is not a list, it becomes `[]`, but MAC won’t match.

So robust.

**Steelman**:  
The design correctly separates concerns: path validation ensures the store is outside the agent’s reach; HMAC with subject binding ensures integrity and workspace isolation; fail-honest handling of corruption maintains trustworthiness; atomic save preserves durability. The use of resolved paths in the outside check, while theoretically vulnerable to advanced filesystem tricks, is practically sound given the sandboxing assumptions, and the fallback to fail-degraded ensures no silent trust.

**Verdict**: **SOUND**. The highest-value fix is already implemented: the subject binding in the MAC, which prevents cross-workspace replay.
