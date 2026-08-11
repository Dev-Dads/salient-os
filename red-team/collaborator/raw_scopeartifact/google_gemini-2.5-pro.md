# ADR 0004 design panel: google/gemini-2.5-pro

_finish=stop seconds=77.6 cost=$0.1200 usage={'prompt_tokens': 39127, 'completion_tokens': 7108, 'total_tokens': 46235, 'cost': 0.11998875, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.11998875, 'upstream_inference_prompt_cost': 0.04890875, 'upstream_inference_completions_cost': 0.07108}, 'completion_tokens_details': {'reasoning_tokens': 4007, 'image_tokens': 0, 'audio_tokens': 0}}_

Excellent. As requested, I will now perform an adversarial design review of ADR 0004. My findings are presented as if I were a senior security architect and applied cryptographer tasked with breaking this proposal before it is built.

***

### Adversarial Design Review: ADR 0004

Here are the findings, ordered by severity and the attack priorities specified.

---

### **ID: F-1**
**TITLE: Indirect Authority Laundering via Configuration Mutation**
**SEVERITY: CRITICAL**
**AREA: trust-root**

**CONCRETE SCENARIO OR BYPASS:**
An attacker gains approval for a seemingly innocuous `run_command` action. The command does not target the collaborator's code (which is protected by F-6 Harm A), but instead targets the *configuration files* used to initialize a `Session` object. The command appends a new public key, for which the attacker holds the private key, to the file that provisions the `session.offense_trust_anchors` dictionary. The next time a `Session` is instantiated, it loads this compromised anchor set. The attacker can now mint valid scope artifacts for any target, having laundered their own key into the trusted set via an ordinary, approved Tier-2 action.

**WHY IT BREAKS THE DECISION:**
This bypasses the ADR's central claim that adding a new authority is a "visible, out-of-normal-flow act." It becomes an *in-band* action achievable via the existing `run_command` capability. The distinction between the operator's HMAC key and the external Ed25519 key becomes meaningless if the former can be used to provision the latter. This completely undermines the "key the operator does not hold" invariant by providing a path for the operator (or a model that convinces the operator to approve a command) to become their own authority indirectly.

**WHETHER THE ADR ALREADY SCOPES IT:**
No. The ADR scopes the *direct* residual: a human operator manually editing the anchor set and colluding with themselves. It entirely misses this *indirect* laundering path where an in-band, governed action (`run_command`) can mutate the anchor set for future sessions.

**FIX:**
The trust anchor set must be made structurally immutable at runtime and provisioned from a source that is outside the write-reach of any in-band tool, including `run_command`. This could involve loading anchors from a read-only volume, a separate configuration management service, or signing the anchor set itself with a key held completely offline. At a minimum, `codefence.py` must be extended to protect collaborator configuration files with the same vigor as its code.

---

### **ID: F-2**
**TITLE: Unchecked Nonce Enables Trivial Artifact Replay**
**SEVERITY: CRITICAL**
**AREA: crypto/artifact**

**CONCRETE SCENARIO OR BYPASS:**
An external authority issues a scope artifact for `target.com` for a one-hour window on Monday, with `nonce: "monday-morning-scan"`. The engagement completes. On Tuesday, an attacker (or a compromised/confused model) re-submits the exact same artifact. The ADR states the nonce is present but "unchecked against anything." The signature is still valid, the `authority_id` is trusted, and if the attacker waits for the same time of day, the `not_before`/`not_after` window will appear valid relative to the current time (if the dates are not absolute). Even with absolute timestamps, if the artifact is valid for a year, it can be replayed at will within that year.

**WHY IT BREAKS THE DECISION:**
This makes the authorization grant reusable, violating the principle of single-use or context-specific authority. A time-bounded window is not a substitute for replay protection. An attacker can repeat an authorized action indefinitely, turning a single approved probe into a persistent scanning capability or re-authorizing an action long after the legitimate engagement has ended. This is a fundamental cryptographic error in an authorization protocol.

**WHETHER THE ADR ALROCKSCOPES IT:**
No. The ADR includes the `nonce` field but fatally admits it is unchecked, demonstrating cryptographic naivete. It mistakes the *presence* of a nonce for the *function* of a nonce.

**FIX:**
The `verify_scope_artifact` function must be stateful. The verifier must maintain a persistent, audited log of all consumed `(authority_id, nonce)` pairs. Before checking the signature, it must first check if this pair has been seen before. If it has, verification must fail immediately. This prevents any artifact from being used more than once.

---

### **ID: F-3**
**TITLE: The "Minimal" Probe is a Port Scanner in a Loop**
**SEVERITY: HIGH**
**AREA: dual-use probe**

**CONCRETE SCENARIO OR BYPASS:**
The ADR claims the value is the *authorization*, not a scanner. This is a dangerously academic distinction. A model, tasked with "checking for open web ports on `target.com`," can trivially and legitimately propose a sequence of actions:
1. `probe_target(target="target.com", port=80)`
2. `probe_target(target="target.com", port=443)`
3. `probe_target(target="target.com", port=8080)`
...and so on for the top 1000 ports. Each action is a "single bounded TCP connect" and passes the gate individually, but the aggregate effect is a full port scan. The banner-grabbing feature further allows service enumeration.

**WHY IT BREAKS THE DECISION:**
It makes the "minimal offense action" framing hollow. The design does not introduce a minimal *primitive*; it introduces a scanner-construction-kit. This significantly expands the "blast radius" of a single approved scope artifact from one probe to a full reconnaissance sweep, which may not have been the intent of the human approver or the external authority. The ADR understates the offensive capability being built.

**WHETHER THE ADR ALREADY SCOPES IT:**
No. It explicitly downplays this risk by stating "the value here is the *authorization*, not a scanner," which is a semantic argument that ignores the practical, emergent behavior of looping the tool.

**FIX:**
If the goal is truly a minimal primitive, the system must enforce limits on the *aggregate* use of the tool against a single target within a given window. This requires a budget or quota system (e.g., "max 5 probes per target per hour per artifact") enforced by the governance layer. Alternatively, accept that you are building a scanner and subject it to the appropriate risk analysis and stricter controls.

---

### **ID: F-4**
**TITLE: Revocation is Not a Non-Goal, It is a Requirement**
**SEVERITY: HIGH**
**AREA: missing**

**CONCRETE SCENARIO OR BYPASS:**
An external authority signs a scope artifact for a week-long pentest. On day one, the authority discovers their private Ed25519 key was compromised. Or, the client cancels the engagement due to an emergency. The artifact, however, is still cryptographically valid for six more days. The attacker who stole the key (or a misinformed collaborator) can continue to probe the target under the color of valid authorization.

**WHY IT BREAKS THE DECISION:**
The ADR dismisses revocation, stating "short windows substitute." This is operationally and cryptographically unsound. A "short" window of even one hour is an eternity if a key is compromised. Any system that grants privileges based on external cryptographic artifacts *must* have a mechanism to revoke those privileges before their natural expiry. Without it, the system cannot respond to the most common failures in a PKI-like system: key compromise and change of circumstance.

**WHETHER THE ADR ALREADY SCOPES IT:**
Yes, it explicitly states revocation is a "non-goal." This finding attacks that decision as dangerously wrong, not as something the author missed. Scoping out a core security requirement is a flaw in the decision itself.

**FIX:**
Implement a revocation mechanism. This does not need to be a full-blown CRL/OCSP infrastructure initially. A simple, effective v0 would be for the verifier to periodically fetch a list of revoked `authority_id`s or `(authority_id, nonce)` tuples from a trusted, operator-controlled URL. Verification would then fail for any artifact signed by a revoked key or matching a revoked nonce. The key is to have a channel for invalidation that is faster than the artifact's expiry.

---

### **ID: F-5**
**TITLE: Artifact Forgery via Extraneous Field Injection**
**SEVERITY: MEDIUM**
**AREA: crypto/artifact**

**CONCRETE SCENARIO OR BYPASS:**
The verifier is built today to validate the known fields (`target`, `scope`, etc.). An attacker crafts an artifact with an additional field: `"version": "1.0"`. The current verifier, following the canonicalization rule, includes this in the signature check, which passes. The verifier then ignores the unknown `version` field and grants the capability. A year later, the verifier is updated to support a `version` field, where `"1.0"` implies a weaker, legacy security policy (e.g., allowing a wider range of probes). The old, stored artifact is now replayed and is interpreted by the new verifier as a legacy artifact, potentially downgrading security.

**WHY IT BREAKS THE DECISION:**
The signature must bind *exactly* the semantic fields the verifier understands and acts upon, and nothing more. Allowing unknown fields creates a forward-compatibility vulnerability where the meaning of a signed artifact can be changed by a future software update. This violates the principle that the signature should provide unambiguous, long-term integrity for the authorized action.

**WHETHER THE ADR ALREADY SCOPES IT:**
No. It specifies a canonicalization method but not a policy on handling unknown fields, which is a common and subtle cryptographic implementation pitfall.

**FIX:**
The `verify_scope_artifact` function must explicitly reject any artifact containing fields not in the defined specification for the current version. The parser should enforce a strict, closed set of expected keys.

---

### **ID: F-6**
**TITLE: Undefined Policy on Private-Range Probing Creates SSRF/Pivot Risk**
**SEVERITY: MEDIUM**
**AREA: dual-use probe**

**CONCRETE SCENARIO OR BYPASS:**
An external authority, intending to authorize a probe against a public web server at `target.com`, signs an artifact. An attacker compromises the DNS for `target.com` and repoints it to `169.254.169.254`. The collaborator, using the `probe_target` tool, receives the artifact, resolves `target.com` to the metadata IP, and the "minimal probe" now reads data from the cloud provider's metadata service. Alternatively, the artifact could be legitimately issued for a target like `10.1.1.5`, allowing the collaborator to probe inside a private network.

**WHY IT BREAKS THE DECISION:**
The ADR is silent on whether the `probe_target` tool should be subject to the same IP-range blocks as the Tier-1/2 egress client (ADR 0003). This ambiguity is dangerous. If it *is* blocked, its utility for legitimate internal pentesting is limited. If it is *not* blocked, it becomes a powerful SSRF and internal network pivoting tool, far exceeding the "minimal" scope implied. A decision must be made and enforced.

**WHETHER THE ADR ALREADY SCOPES IT:**
No. It fails to consider the interaction between the new offense tool and the existing egress safety rules from ADR 0003.

**FIX:**
The ADR must specify the policy for probing RFC1918, metadata, and other special-use IP ranges. A robust fix would be to make this policy explicit within the scope artifact itself via a boolean field, e.g., `"allow_private_ranges": true/false`, which must be signed by the authority. The default should be `false` (deny private ranges).

---

### **LAUNDERING JUDGMENT**

The claim "rooted in a key the operator does not hold" is not meaningfully achieved on a single node. The operator's ability to provision the trust anchor set makes them the de-facto root of trust; choosing the authorities is functionally equivalent to controlling the authority. The admitted residual is fatal to the claim of a true "prohibited class," reducing it to a procedural hurdle. Stating this honestly is better than hiding it, but it does not make the resulting security boundary strong; it's a well-documented weak fence, not a locked door.

### **MISSING**

Beyond the findings, the ADR is missing a clear plan for **anchor key rotation and compromise**. If an external authority's key is compromised, how is it replaced? The ADR also lacks a discussion of **audit trail requirements for Tier 3 actions**; a forensic investigation would need to link a specific action back to the exact, complete scope artifact that authorized it, requiring the artifact (or its hash) to be stored with the audit record. Finally, the introduction of `cryptography` as a dependency is noted, but the **supply-chain risk management** strategy for this critical component is unaddressed.

### **STEELMAN**

The author's design correctly identifies that third-party authorization requires a fundamentally different trust root than the operator's symmetric keys, rightly forcing the introduction of asymmetric cryptography. By creating a new `offense:` capability namespace gated exclusively by this external artifact, it structurally separates Tier 3 from Tiers 1 and 2, honoring the spirit of ADR 0003. The design is laudably honest about its single-node limitations, choosing to document the residual trust issue rather than claiming a security property it cannot enforce.

### **VERDICT: SERIOUS_FLAWS**

The core architectural direction is sound, but the proposal is undermined by critical cryptographic naivete and a failure to consider indirect bypasses of the trust model.

**The single highest-value change:** Implement a stateful, per-authority, persistent nonce-checking mechanism in `verify_scope_artifact` to prevent trivial replay attacks.
