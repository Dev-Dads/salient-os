# ADR 0004 design panel: x-ai/grok-4.5

_finish=stop seconds=145.7 cost=$0.1086 usage={'prompt_tokens': 37387, 'completion_tokens': 5676, 'total_tokens': 43063, 'cost': 0.1086124, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1086124, 'upstream_inference_prompt_cost': 0.0745564, 'upstream_inference_completions_cost': 0.034056}, 'completion_tokens_details': {'reasoning_tokens': 96, 'image_tokens': 0, 'audio_tokens': 0}}_

# ADR 0004 — Adversarial Design Review

## Findings

### F-01 / Anchor provisioning is de-facto minting on a single node / CRITICAL / trust-root

**Scenario.** Operator generates Ed25519 keypair, sets `session.offense_trust_anchors = {"me": pk}`, signs a `ScopeArtifact` for `evil.com` with sk, attaches it to `session.scope_artifacts`. Gate opens. No second party ever existed.

**Why it breaks the decision.** ADR 0003’s invariant (§170–178) is not “ceremony higher than typing a cap string.” It is: *an operator-minted “I authorize X” is manufacturing third-party consent from inside the trust domain and must never be accepted.* ADR 0004’s enforced claim (“rooted in a key the operator does not hold”) is false on the only deployment topology the system has. Choosing the anchor set *is* choosing who may mint; on one node the chooser and the minter are the same principal. The TLS-CA analogy fails: a TLS client does not provision the WebPKI roots it relies on for *its own* authorization decisions about third-party consent; here the operator both provisions anchors and consumes the grant to act against a third party.

**Already scoped?** Yes — “Honest residual” § admits exactly this and equates it to ADR 0002. **Attack on that scoping:** stating it does not convert a violated invariant into a raised bar. ADR 0003 forbade laundering-with-ceremony; ADR 0004 ships laundering-with-Ed25519-ceremony and relabels it residual. That makes the “prohibited class” claim hollow: Tier 3 is a door with a posted sign, not a locked door. The residual is fatal to the *claim*, not merely a documented limit of enforcement.

**Fix.** Either (a) refuse to unlock Tier 3 until a second trust domain exists (anchor set provisioned/signed by a process whose key the operator runtime does not hold — offline ceremony, remote signer, or target-owner challenge), or (b) rename the guarantee to what it actually is (“out-of-band ceremony + asymmetric non-forgeability by the HMAC path / model”) and **stop claiming** ADR 0003’s trust-root invariant is honored. Do not ship under the current claim text.

---

### F-02 / Model-indirect paths to anchors/artifacts not closed in the seam / HIGH / trust-root

**Scenario.** Session fields are “host-provisioned, not model-reachable” by convention (constructor args, like `egress_credentials`). The model already has `run_command` (human-gated but approvable) and `write_file`. Concrete paths:
1. Propose `write_file` of a systemd unit / shell rc / collaborator launch script that re-invokes the process with new constructor kwargs or env that the host loader turns into anchors.
2. Propose `run_command` that patches a YAML/TOML session profile on disk the operator reloads.
3. If any future control-plane API reflects session config into the workspace or an admin tool, a held proposal mutates `offense_trust_anchors` or appends to `scope_artifacts` without a clear “this is Tier-3 trust root surgery” UX distinct from ordinary config.

ADR asserts: “nothing reachable from `run_turn` touches anchors, artifacts, or a signer.” That is a **non-interference claim about the Python object graph**, not about the operator+model loop that provisions the process.

**Why it breaks.** The “not model-reachable” guarantee is load-bearing for “enforced structural.” It is not enforced by capability, immutability, or a separate trust boundary — only by “constructor fields.” ADR 0003 learned this lesson for caps (`enforce_caps` sticky, signed grant, never mutable `session.capabilities` for high-stakes lifts). Anchors/artifacts get none of that sticky/signed/load-time-attested treatment in the wiring sketch.

**Already scoped?** Partially (“host-provisioned, not model-reachable”) — not as an adversarial surface. Residual laundering discussion only covers *human* self-issuance, not *model-influenced provisioning*.

**Fix.** Freeze anchors at construction (`MappingProxyType` / frozen bytes); refuse runtime mutation; load anchors only from an operator-signed bundle verified under a key *distinct* from day-to-day ops (or require restart + out-of-band attestation). Audit any change to anchor set as a first-class Tier-3 trust event. Treat “model proposes config that becomes anchors” as in-scope red-team, not out of scope.

---

### F-03 / `offense:*` presence via gate side-door — capability never in PolicyCaps but gate may not use PolicyCaps / HIGH / gate/P-01

**Scenario.** Design: `offense:<target>` is “present” iff `active_scope_grant(...)` succeeds; HMAC PolicyCaps can never mint it. Implementation risk against the real seam (`governance.py` capability block ~egress derivation, then `directive.grants_capability(required_cap)`):

If derivation does:
```text
required_cap = "offense:" + canonical_host(target)
# then only directive.grants_capability(required_cap)  # PolicyCaps path
```
then offense is **permanently denied** (good fail closed) *or* someone “fixes” it by minting `offense:*` into caps (catastrophic).

If derivation does:
```text
if active_scope_grant(...): required_cap = tool.capability  # offense:__derived__ sentinel
# or bypasses grants_capability when grant exists
```
then authority is the collaborator-side grant check — correct only if **every** path (govern, reauthorized_or_denied, execute_and_verify) re-calls `verify_scope_artifact` and never treats artifact bytes on the session as authoritative.

**Concrete bypass.** Artifact attached to session (unverified) → `active_scope_grant` short-circuits on presence/parse without verify → `grants_capability` skipped or fed a sentinel that a buggy static grant includes → probe runs. Or: PolicyCaps accidentally contains `offense:__derived__` or `offense:evil.com` because nothing in `policycaps.mint` **structurally forbids** the `offense:` prefix (ADR 0003 revisit #4 not done; caps are free strings).

**Why it breaks.** “HMAC path cannot mint” is true only if the gate **never** consults HMAC for `offense:` and **rejects** any `offense:` string inside PolicyCaps as malformed. ADR says “never grantable from PolicyCaps” but wiring does not specify a negative namespace check in `mint`/`granted_capabilities`/`directive`. Surfacing “grants no authority” fails if verification is not the sole binder on all three gate moments.

**Already scoped?** Intent yes; mechanism underspecified vs real `govern_action` / `reauthorized_or_denied` / `execute_and_verify` shape.

**Fix.** Specify precisely:
1. `policycaps.mint` rejects any capability with prefix `offense:`.
2. Gate branch: for offense tools, **do not** call `grants_capability("offense:...")`; call `active_scope_grant` which **always** runs full `verify_scope_artifact` (not cached “grant object” without re-verify at approve/use).
3. Moment-of-use re-assert identical to run_command floors (`execute_and_verify`), binding target/port/action_class to frozen args.
4. Tests: caps containing `offense:x` fail mint or are stripped; dropped derivation → sentinel deny.

---

### F-04 / Canonicalization parity authorize-vs-connect not specified for probe / HIGH / crypto/artifact + dual-use probe

**Scenario.** Artifact `target = "example.com"` (canonical). Probe args: `host=example.com.`, `host=EXAMPLE.com`, IDN homograph, `host=127.0.0.1` while artifact says name, or connect-by-IP after artifact bound name. ADR says target uses “egress.canonical_host form — ONE canonicalizer” but `probe_target` is **not** `egress.py`; it is a raw TCP connect. Tier-1 closed authorize-one/connect-another by same parse for capability key **and** socket. ADR 0004 only says the artifact field is canonical form — not that probe executor re-canonicalizes args and refuses mismatch, nor IP-pin, nor redirect/DNS rebind (TCP has no HTTP redirect, but **DNS rebinding / happy-eyeballs to different A record** remains).

**Why it breaks.** Same bug class ADR 0003 closed for egress. Without “canonical(args.host) == grant.target ∧ connect only to pinned IP from resolution at verify/use ∧ block private/metadata,” the artifact authorizes a name and the executor reaches another A/AAAA or a forged resolution.

**Already scoped?** “ONE canonicalizer, parity with Tier 1/2” is named; mediation contract (IP-pin, private block) is **not** carried over to the probe.

**Fix.** Reuse `egress.canonical_host` (or shared `canonical_host` module) for artifact mint/verify **and** probe args; at moment-of-use require equality; resolve once, pin IP, apply same private/CGNAT/metadata denies as egress unless engagement explicitly authorizes a lab net (separate scope flag, default deny). Refuse connect-by-IP unless artifact target was that IP literal in canonical form.

---

### F-05 / Nonce unchecked = replay; no engagement binding to session/workspace / HIGH / crypto/artifact

**Scenario.** Valid artifact for `target=victim.com`, window 30d, nonce=`abc`. Stolen from audit log, ticket system, or prior session; replayed on another workspace/session/operator host that trusts same `authority_id`. Nonce is in the signed payload but ADR never says verify checks nonce uniqueness, binds to `workspace_subject`, or binds to `engagement_id` store.

**Why it breaks.** `not_before`/`not_after` bound time, not *use*. A scope grant for a pentest engagement is replayable across every Collaborator deployment sharing that anchor for the whole window — including after the engagement “ended” operationally but before `not_after`, and on hosts that were never part of the engagement.

**Already scoped?** Nonce field present; semantics missing. Revocation OOS; short windows “substitute” — does not address cross-session replay inside the window.

**Fix.** Require server-side (session or host durable) consume of `(authority_id, engagement_id, nonce)` at first use or at attach; bind artifact to `workspace_subject` (or deployment id) as signed field; verify subject at gate. Engagement_id alone is insufficient if not checked against a registry.

---

### F-06 / Missing algorithm agility is OK; missing key identity binding is not / MEDIUM / crypto/artifact

**Scenario.** `authority_id` is a string selector into a dict of raw public keys. No key fingerprint inside the signed payload. Operator rotates anchor for `authority_id="customer-a"` to a new pk (compromise response). Old signatures still verify if old pk remains, or: attacker replaces dict entry `customer-a` → attacker_pk (F-01/F-02) and mints new artifacts under the trusted name. Signed bytes do not bind `subject_key_id = sha256(pk)`.

**Why it breaks.** Authority_id is a **nickname**, not a cryptographic identity. Rotation/compromise story is undefined (revocation OOS). Confusion between authority-id and key-id is a classic multi-key pitfall.

**Already scoped?** Revocation OOS with short windows — inadequate for key replace vs nickname reuse.

**Fix.** Sign `key_id` (hash of verifying public key) inside the payload; verify `trust_anchors[authority_id]` hash equals payload `key_id`. Rotation = new authority_id or dual-publish with overlap window documented.

---

### F-07 / Canonical JSON over tuples/lists and field set — underspecified vs policycaps / MEDIUM / crypto/artifact

**Scenario.** ADR: “Canonicalization mirrors `policycaps._canonical` (sorted keys, separators, UTF-8)” and `scope: tuple[str]`. `json.dumps` will serialize a tuple as a JSON array; key order is sorted — but **which keys are included**? If verify uses `json.loads` then re-dumps only known fields, extra signed fields are stripped (OK) or if it signs `json.dumps(entire_object)` and verify checks subset, **field injection** at verify (attacker adds `"target":"good.com"` displayed vs different bytes) depends on implementation. Unicode: NFC not specified for `target` before sign (egress canonicalizer does IDNA/NFC/casefold — artifact must use **identical** function, not “form”). Float/int for timestamps: `not_after: 1e12` vs `1000000000000` JSON number games if any non-int slips in.

**Why it breaks.** “Signature binds exactly the fields” requires a single explicit canonical schema: fixed key set, reject unknown keys on verify, `scope` as sorted unique tuple of enum strings, target pre-canonicalized, timestamps strict integers, signature over bytes with **no** JSON number ambiguity (serialize ints as integers only).

**Already scoped?** Mirrors policycaps — policycaps only has four string/tuple fields and sorts capabilities. Incomplete transplant.

**Fix.** Define `canonical_scope_artifact_bytes(fields) -> bytes` with allowlisted keys only, reject unknown keys, normalize target via `canonical_host` before sign/verify, scope frozen enum, int unix ts only, Ed25519 pure (no prehash confusion), `compare` API fail-closed. Prefer signing a **prehash of canonical bytes** with explicit domain separation string `salienceos-scope-artifact-v1`.

---

### F-08 / Clock/skew and `now` injection / MEDIUM / crypto/artifact

**Scenario.** `verify_scope_artifact(..., now=)`. Session already has injectable `now_days` for memory. If `now` is wall clock from the operator host, skew can admit not_before early. If model/host can pass `now`, window check is attacker-controlled. No max lifetime (`not_after - not_before <= MAX`).

**Why it breaks.** Bounded window is mandatory in the ADR but unenforced as a maximum duration; engagement of 10 years is still “bounded.”

**Already scoped?** Window mandatory; max duration and clock source not.

**Fix.** Host-monotonic/`session` clock policy; cap window length (e.g. ≤ 72h v0); reject `not_after - not_before > MAX` and `not_before > now + skew_allow`.

---

### F-09 / Target ownership change (DNS/host reassignment) / MEDIUM / crypto/artifact

**Scenario.** Artifact for `shop.example` valid 30d. Day 10, `shop.example` transfers to a new owner. Probe still “authorized” by old engagement authority signature.

**Why it breaks.** Consent is not a property of a DNS label over time. Short windows mitigate but ADR treats short windows as revocation substitute — not ownership continuity.

**Already scoped?** Implicitly under short windows / non-goals — not named.

**Fix.** Document as residual; prefer shorter max window; optional signed target_owner_id / IP range; operational SOP — not fully solvable in v0, but must be named next to laundering residual.

---

### F-10 / “Single TCP probe” is a scanner primitive + banner channel / HIGH / dual-use probe

**Scenario.** Human approves `propose_first` repeatedly (or future auto path). Model loops `probe_target` across ports 1–65535 and hosts if multiple artifacts or target canonicalization is loose. Banner read = arbitrary attacker-controlled bytes into model context (injection), and a **payload channel** outbound if connect data is not strictly empty / read-only few bytes.

**Why it breaks.** ADR: “no payload, no exploitation, one host, one connect.” One host/one connect **per invocation** is not “not a scanner”; the tool is the atomic scanner step. Tier-3 unlock + loop = authorized port scan of a third party. Banner grab reintroduces untrusted ingress without `_neutralize`/UNTRUSTED tagging specified (egress web path has both).

**Already scoped?** “Not a general offense toolkit” / minimal primitive — does not bound **rate, port range, fan-out, or response handling**.

**Fix.** Hard-bound: single port per artifact scope or port in signed artifact; session quota on offense actions; default ports allowlist in artifact (`ports: (443,)`); banner ≤ N bytes, neutralized + UNTRUSTED; no write of banner to workspace without fence; consider **not shipping executor** — ship verify + gate only with a stub that records “would probe” until policy exists.

---

### F-11 / Private range / metadata reach as footgun / HIGH / dual-use probe

**Scenario.** Artifact for `metadata.google.internal` or a canonical form that resolves in-lab to `169.254.169.254`, or target `10.0.0.5` for “sanctioned internal pentest.” Probe does not use egress private-range block → SSRF-equivalent into cloud metadata or RFC1918 from the Collaborator host, under Tier-3 branding.

**Why it breaks.** ADR 0003 treated private/metadata as hard deny for egress because allowlist-of-names is not ownership of link-local services. Tier-3 probe that **defaults** to allowing RFC1918 turns the Collaborator into an in-network pivot once any artifact exists (including self-issued F-01).

**Already scoped?** No.

**Fix.** Default: same IP blocks as egress. Opt-in signed scope flag `reach_private=true` only for explicit lab engagements; still block link-local/metadata unless further flag. Never treat metadata ranges as valid offense targets in v0.

---

### F-12 / First `cryptography` dependency — supply chain / MEDIUM / missing

**Scenario.** `cryptography` (OpenSSL/rust) enters collaborator via pyproject. Core stays clean; collaborator is still the TCB for Tier-3. Host build compromises wheel → verify always returns True or substitutes attacker code in `scopeartifact.py`.

**Why it breaks.** ADR notes sole import location but no pin/hash policy, no verify isolation, no minimal alternative (`nacl`/stdlib ed25519 debate). Fail-closed on exception is good; malicious module is not an exception path.

**Already scoped?** Import boundary only.

**Fix.** Pin hashes in lockfile; consider pure-verify with audited minimal Ed25519; load anchors in a way that evil verify module still cannot mint HMAC offense caps (defense in depth — F-03 namespace deny helps).

---

### F-13 / Audit provenance underspecified for Tier-3 / MEDIUM / missing

**Scenario.** Decision gains `scope_engagement` field (wiring). Unclear: whether full artifact (replayable secret-ish), signature, authority_id, key_id, engagement_id, verify timestamp, and **who attached** the artifact are logged. Artifact in audit log → F-05 replay feedstock.

**Why it breaks.** Tier-3 is the highest-impact class; audit must answer “which external key, which engagement, which human approval, which target bytes connected.” Logging the verifiable credential itself can increase replay risk.

**Already scoped?** `scope_engagement` named; schema not.

**Fix.** Log authority_id, key_id, engagement_id, target, window, action_class, verify result, approval id; log signature fingerprint not necessarily full reusable blob in world-readable bus; never log as model-visible content without UNTRUSTED handling.

---

### F-14 / Should be core structural un-grantability (revisit #4) not collaborator-only / MEDIUM / missing

**Scenario.** All of Tier-3 lives in collaborator; core `directive.grants_capability` still accepts any string capability the collaborator puts in the policy list. A second collaborator or future core API could mint `offense:x` through ordinary policy issue if someone wires caps wrong.

**Why it breaks.** ADR 0003 revisit #4 explicitly wanted structural un-expressability in core. Shipping Tier-3 unlock **before** that graduation widens the blast radius of any collaborator bug.

**Already scoped?** Core untouched as non-goal/v0 style — tension with unlocking prohibited class.

**Fix.** Highest-value sequencing: implement negative namespace in core policy issue (`offense:` rejected) **before** or **with** unlock; or delay unlock until revisit #4.

---

### F-15 / P-01 framing “artifact influences, external key authorizes” is slick but wrong under F-01 / MEDIUM / gate/P-01

**Scenario.** Artifact is not salience; it is a **credential**. Calling it “influence” borrows P-01 language to sound like the outside case matches the inside case. Under operator-provisioned anchors, the external key is not external.

**Why it breaks.** Category error muddies review and tests: people will check “did salience widen caps?” and miss “did attach-without-verify widen authority?”

**Already scoped?** Rhetorical in Decision §3.

**Fix.** Drop the metaphor. State: “authority = successful asymmetric verify against configured anchors at gate/use; artifact bytes alone are data.”

---

### F-16 / Approval path TOCTOU for artifact expiry / LOW / gate/P-01

**Scenario.** Artifact valid at hold; expired at approve. If `reauthorized_or_denied` only re-checks PolicyCaps-derived caps and not `active_scope_grant`, probe runs expired.

**Why it breaks.** ADR claims re-verify at approval and moment-of-use — must be explicit in `reauthorized_or_denied` sibling branch (today only egress re-derives). Easy to miss in implementation.

**Already scoped?** Claimed in prose; must match seam patch list.

**Fix.** Explicit offense branch in `reauthorized_or_denied` + `execute_and_verify`; tests expire-between-hold-and-approve.

---

## LAUNDERING JUDGMENT

**“Rooted in a key the operator does not hold” is not achieved on a single node.** The admitted residual is not a minor operational caveat; it is the same principal controlling anchor registry and private signing key — i.e. operator-minted third-party consent with extra steps. Honest documentation is necessary but **not sufficient** to carry ADR 0003’s invariant language; under that language the design is an escape hatch. What is actually achieved is: non-forgeability by the model and by the HMAC PolicyCaps cryptosystem, plus deliberate ceremony — a useful control, a different claim.

---

## MISSING

1. **Negative capability namespace** in `policycaps.mint` / core policy (`offense:` rejected).  
2. **Exact gate algorithm** vs `directive.grants_capability` (three call sites).  
3. **Probe mediation contract** (canonical parity, IP-pin, private/metadata deny, port binding in artifact).  
4. **Nonce/engagement/workspace replay** database and signed subject binding.  
5. **key_id in signed payload**; rotation without nickname swap.  
6. **Max window**, clock source, skew.  
7. **Artifact attach API** — who may add artifacts; immutable anchors; no runtime dict poke.  
8. **Rate/fan-out limits** and UNTRUSTED banner handling.  
9. **Audit schema** + replay-safe logging.  
10. **Supply-chain pin** for `cryptography`.  
11. **Ownership-over-time** residual named.  
12. **Whether shipping any offense executor is justified** vs authorization-only.  
13. **Interaction with netns**: probe must not be implementable as `run_command` nc/bash /dev/tcp bypass of Tier-3 (netns helps IP egress; UNIX sockets/local agents remain). Confirm probe is sole intentional path and raw shell still cannot reach third parties without isolation opt-in — else Tier-3 gate is theater while Tier-2 shell does offense.

---

## STEELMAN

The strongest reading: stop pretending HMAC caps can ever represent third-party consent; put a different cryptosystem and verify path in front of a new capability namespace so ordinary grant flows and the model cannot mint offense authority; keep core AST-fenced; exercise the path with a tiny probe; re-gate like egress; admit single-node collusion like ADR 0002. As an **incremental ceremony and namespace separation** design, that is coherent engineering. It is not coherent as fulfillment of ADR 0003’s “must never be accepted as the artifact” operator-mint prohibition.

---

## VERDICT

**SERIOUS_FLAWS** — The trust-root claim does not hold on the deployed trust model, and the probe/gate/canonicalization/replay specs are too incomplete to enforce “structural” Tier-3 even as ceremony.

**Single highest-value change:** Rewrite the guarantee to drop false “external trust root / ADR 0003 invariant honored” language **or** block unlock until anchors are provisioned by a true second domain; in the same patch, specify gate mechanics with mandatory `offense:` rejection in PolicyCaps, full re-verify at all three sites, shared canonical_host + IP-pin/private blocks for probe, and signed `key_id` + workspace/engagement nonce consumption — and seriously consider shipping **authorization only** without a network probe executor in v0.
