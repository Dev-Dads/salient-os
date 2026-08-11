# ADR 0004 design panel — triage + the decision it drove

External DESIGN panel (pre-build) on the ORIGINAL Tier-3-unlock design (an Ed25519 scope artifact
rooted in an external authority public key, unlocking `offense:<target>` + a minimal TCP probe).
5-vendor OpenRouter panel; runner `redteam_scopeartifact.py`. **Cost $1.05** (4/5; gpt-5.1 hit a JSON
parse error on our side — retryable; the 4 that returned are unanimous).

| vendor | verdict |
|---|---|
| anthropic/claude-opus-4.1 | SERIOUS_FLAWS |
| x-ai/grok-4.5 | SERIOUS_FLAWS |
| google/gemini-2.5-pro | SERIOUS_FLAWS |
| qwen/qwen3-max | SERIOUS_FLAWS |
| openai/gpt-5.1 | (parse error, no verdict) |

## The decisive finding (unanimous)

On a single node the operator provisions the trust-anchor set, so the same principal controls the
anchor registry AND (by generating a keypair) the signing key. "Rooted in a key the operator does not
hold" is **not achieved** — it is operator self-issuance with Ed25519 ceremony. ADR 0003 forbade
laundering-with-ceremony; the artifact design shipped laundering-with-Ed25519-ceremony and relabelled
it a residual. Stating the residual honestly does not convert a violated invariant into a raised bar.
The approach itself (asymmetric external key + new un-grantable namespace + gate) was steelmanned as
sound engineering — but as *incremental ceremony + namespace separation*, NOT as fulfilment of ADR
0003's operator-mint prohibition.

grok's coherence catch (F-13): a **human-approved `run_command` already reaches a third party**
(`nc victim 443`, `/dev/tcp`) with no artifact — so a Tier-3 gate over a single probe adds dual-use
surface without adding real control on one node.

Other concrete gaps (grok, gemini, qwen): model-reachable/mutable anchors; unchecked nonce (replay);
no key_id in the signed payload (authority_id is a nickname); no revocation; probe not IP-pinned /
metadata-blocked / port-bound; the negative `offense:` namespace not enforced in core; first
`cryptography` dependency unpinned.

## The decision (operator, Option 3)

**Do NOT unlock Tier 3 on a single node — it would be ceremony.** Instead do ADR 0003 revisit #4
FIRST: make the prohibited `offense:` namespace **un-expressable by construction in `salienceos/`
core** (`grants_capability` refuses it unconditionally; `issue_policy` strips it; `policycaps.mint`
rejects it loud). Tier 3 stays LOCKED BY CORE, not by the Collaborator declining to mint. No
`cryptography` dependency (stays stdlib-only); no probe/weapon. The scope-artifact design is preserved
as the DEFERRED future path — rooted in a REAL second trust domain (anchors the operator runtime
cannot author), never the core capability path. Recorded in
`docs/adr/0004-tier3-scope-artifact-external-trust-root.md`.

This is the design panel working exactly as intended: it stopped a dishonest unlock *before* a line of
it shipped, and redirected the effort to the honest, structural prerequisite.
