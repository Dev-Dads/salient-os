# ADR 0004 design panel: qwen/qwen3-max

_finish=stop seconds=50.3 cost=$0.0748 usage={'prompt_tokens': 35777, 'completion_tokens': 2438, 'total_tokens': 38215, 'cost': 0.07482852, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.07482852, 'upstream_inference_prompt_cost': 0.05581212, 'upstream_inference_completions_cost': 0.0190164}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

**ADVERSARIAL REVIEW OF ADR 0004**

---

### FINDING 1  
**ID**: TR-01  
**TITLE**: Operator anchor provisioning = de facto minting of Tier-3 authority  
**SEVERITY**: CRITICAL  
**AREA**: trust-root  
**CONCRETE SCENARIO**: An operator registers their own Ed25519 public key as `authority_id="pentest-team"` in `session.offense_trust_anchors`, signs a scope artifact for `target=evil.com` with the corresponding private key, and runs `probe_target`. The system accepts it as legitimate external authorization.  
**WHY IT BREAKS THE DECISION**: The core invariant of ADR 0003 is that Tier-3 actions must be "rooted in a key the operator does not hold." But if the operator controls both the anchor set *and* can generate keys, they can trivially self-issue. This isn’t just a residual—it **collapses the second trust domain into the first**, making the “external” key indistinguishable from an HMAC cap in practice. The design admits this but treats it as acceptable because it’s “out-of-normal-flow.” Yet nothing prevents automation (e.g., a script that auto-registers a keypair on session init), turning this into a repeatable, in-band path to mint `offense:<target>`.  
**ADR ALREADY SCOPES IT?**: Yes — explicitly as the “honest residual.”  
**FIX**: Require that `offense_trust_anchors` be provisioned via a **separate, non-collaborator process** (e.g., a signed config file from a distinct authority, or a hardware-bound root). At minimum, enforce that any anchor added at runtime cannot be used until after a human confirmation *outside the session* (e.g., CLI approval with audit trail).

---

### FINDING 2  
**ID**: CA-01  
**TITLE**: Scope artifact lacks binding to engagement context; replay across sessions/targets possible  
**SEVERITY**: HIGH  
**AREA**: crypto/artifact  
**CONCRETE SCENARIO**: An attacker obtains a valid scope artifact for `target=victim.com`, `engagement_id=E1`, `not_after=+1h`. They replay it against `target=other-victim.com` by manipulating DNS so that `other-victim.com` resolves to the same IP as `victim.com`. Since the artifact only binds `canonical_host` (a name), and the probe uses raw IP connect (like Tier-1 egress), the probe succeeds against the wrong target. Alternatively, they reuse the artifact in a new session where the operator has changed workspace or policy—no session/subject binding exists.  
**WHY IT BREAKS THE DECISION**: The artifact claims to authorize offense against a specific third party, but without binding to **network-layer identity (IP + port)** or **session context**, it fails to prevent cross-target or cross-engagement misuse. The nonce is present but unused—it doesn’t tie to session, action_id, or destination IP. This violates the principle that authorization must bind *exactly* what is executed.  
**ADR ALREADY SCOPES IT?**: No. The ADR assumes canonical_host parity suffices, but ignores IP-level targeting and session drift.  
**FIX**: Include `resolved_ip` and `port` in the signed scope artifact (derived at time of issuance by the authority). Also bind `engagement_id` to a session/workspace subject or require per-action nonce verification.

---

### FINDING 3  
**ID**: CA-02  
**TITLE**: Canonicalization ambiguity enables field injection or signature malleability  
**SEVERITY**: HIGH  
**AREA**: crypto/artifact  
**CONCRETE SCENARIO**: The artifact uses JSON with `separators=(",",":")` and sorted keys. However, JSON allows duplicate keys; if the parser retains the *last* occurrence (as Python’s `json.loads` does), an attacker could craft a serialized artifact where a malicious `target` appears twice—first as a benign host, then as `evil.com`. If the signing implementation uses a dict (which drops duplicates), but the verifier reconstructs from raw bytes differently, a mismatch occurs. Alternatively, Unicode normalization differences (e.g., `é` vs `e\u0301`) in `target` could cause the signed string to differ from the canonicalized host used in capability derivation.  
**WHY IT BREAKS THE DECISION**: The claim that “signature binds exactly the fields” is false if canonicalization isn’t injective and deterministic across all implementations. Ed25519 is not malleable, but **input malleability** breaks binding.  
**ADR ALREADY SCOPES IT?**: Partially—it cites `policycaps._canonical`, but that code uses `json.dumps(sorted(...))`, which is vulnerable to duplicate-key collapse and doesn’t enforce UTF-8 NFC normalization.  
**FIX**: Use a stricter serialization: CBOR or length-prefixed UTF-8 strings with explicit field ordering and duplicate rejection. Enforce NFC normalization on `target` before signing and before capability derivation.

---

### FINDING 4  
**ID**: GP-01  
**TITLE**: Artifact influences gate without being verified at all decision points  
**SEVERITY**: MEDIUM  
**AREA**: gate/P-01  
**CONCRETE SCENARIO**: A refactor introduces a new tool `scan_target` that calls `probe_target` internally. The outer tool derives `offense:<target>` correctly, but the inner call bypasses `govern_action` and directly invokes `_exec_probe_target`. Because the artifact is only checked in `active_scope_grant` during governance, the inner call executes without re-verifying the artifact’s validity (e.g., it may have expired between outer and inner calls).  
**WHY IT BREAKS THE DECISION**: The design claims verification happens at “govern-time, approval re-gate, moment-of-use,” but this relies on **discipline**, not enforcement. Nothing prevents direct executor calls. P-01 requires that influence never widen authority—but here, the artifact’s mere presence in session state could be misused as implicit authority.  
**ADR ALREADY SCOPES IT?**: No. It assumes correct wiring but provides no structural guardrails (e.g., capability tokens that expire or are consumed).  
**FIX**: Require that every use of `offense:<target>` capability be accompanied by a **freshly verified `ScopeGrant` object** passed down the call stack—not just session state. Or, make `probe_target`’s executor require a `ScopeGrant` as input, not just a target string.

---

### FINDING 5  
**ID**: DP-01  
**TITLE**: Bounded TCP probe is dual-use scanner primitive  
**SEVERITY**: MEDIUM  
**AREA**: dual-use probe  
**CONCRETE SCENARIO**: An operator issues a scope artifact for `target=*.example.com` (but wait—they can’t, because `canonical_host` forbids wildcards). However, they instead issue **multiple artifacts** for `host1.example.com`, `host2.example.com`, etc., or loop `probe_target` over ports 1–65535 using a single artifact for `target=scanme.nmap.org`. The probe reads banners, enabling service fingerprinting. Over time, this becomes a full network mapper.  
**WHY IT BREAKS THE DECISION**: The “minimal offense action” is not minimal in aggregate. While one probe is harmless, the design enables **automated enumeration** under a single engagement. Worse, nothing restricts probing of **RFC1918/metadata IPs** if the artifact names them—unlike Tier-1 egress, which blocks private ranges. So an authorized artifact for `169.254.169.254` would allow AWS metadata exfiltration.  
**ADR ALREADY SCOPES IT?**: No. It says “one host, one connect” but doesn’t enforce IP allowlists or rate limits.  
**FIX**: Reuse Tier-1’s **IP pinning and private-range block** in the probe executor. Also enforce that `canonical_host` resolution must yield a **public, non-metadata IP**. Add a per-engagement probe quota.

---

### FINDING 6  
**ID**: MS-01  
**TITLE**: No revocation mechanism; compromised anchors persist until expiry  
**SEVERITY**: HIGH  
**AREA**: missing  
**CONCRETE SCENARIO**: An external authority’s private key is compromised. The operator removes the public key from `offense_trust_anchors`, but existing valid artifacts signed by that key remain usable until `not_after`. During that window, attackers can forge new engagements.  
**WHY IT BREAKS THE DECISION**: Short windows mitigate but don’t solve this. Real pentest engagements may last days. The ADR admits revocation is out of scope, but this creates a **persistent attack vector** once an anchor is ever trusted.  
**ADR ALREADY SCOPES IT?**: Yes—as a non-goal (“short windows substitute”). But this is insufficient for production.  
**FIX**: Add support for **anchor versioning or key IDs**, and allow operators to maintain a **revocation list** (even if just a local denylist of `engagement_id`s or key fingerprints).

---

### LAUNDERING JUDGMENT  
The claim that authority is “rooted in a key the operator does not hold” is **not achieved** on a single node. The admitted residual isn’t just a limitation—it **invalidates the trust-root invariant** because the operator can fully simulate an external authority. Stating it honestly is necessary but **insufficient**: it turns a structural guarantee into a procedural one, which contradicts ADR 0003’s demand for *ungrantability by construction*. This isn’t a “raised bar”—it’s a door with a sign saying “please don’t walk through.”

---

### MISSING  
- **Audit linkage**: No requirement that `Decision` records include `engagement_id` or `authority_id`, making forensic tracing of Tier-3 actions impossible.  
- **Key hygiene**: No guidance on how external authorities should manage keys (e.g., FIDO2, HSMs), increasing risk of compromise.  
- **Canonical_host parity**: The ADR assumes `canonical_host` in artifact matches Tier-1, but doesn’t mandate reuse of the **exact same function** (`egress.canonical_host`)—a divergence could enable authorize-one/probe-another.

---

### STEELMAN  
The strongest version of this design isolates Tier-3 authority in a **separate trust domain**: anchors are provisioned via a signed, immutable config from a distinct system (e.g., an engagement management platform), and artifacts must include cryptographic proof of target ownership (e.g., DNS TXT record signed by the target). The probe is strictly limited to public IPs, with independent IP reconciliation like Tier-1 egress, and every use requires fresh artifact validation tied to the action ID.

---

### VERDICT  
**SERIOUS_FLAWS** — The design fails to enforce its core invariant due to operator-controlled anchor provisioning, and introduces new cryptographic and network-layer risks without adequate mitigations.  

**Highest-value change**: **Decouple anchor provisioning from the operator session**—require that `offense_trust_anchors` be loaded from a **signed, externally managed configuration** that cannot be modified at runtime by the same entity that runs the Collaborator. This restores the second trust domain and makes “key the operator does not hold” meaningful.
