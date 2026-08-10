# PR B run_command parity + MINOR-B panel: anthropic/claude-opus-4.1

_finish=stop seconds=91.3 cost=$1.0621 usage={'prompt_tokens': 65859, 'completion_tokens': 989, 'total_tokens': 66848, 'cost': 1.06206, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 1.06206, 'upstream_inference_prompt_cost': 0.987885, 'upstream_inference_completions_cost': 0.074175}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

# Security Review of PR B: B1 Isolation Floor + MINOR-B Seal

## Findings

### F1: netns_available() Cache Poisoning
**ID:** F1  
**TITLE:** Global Cache Allows netns_available() Poisoning  
**SEVERITY:** MEDIUM  
**LOCATION:** collaborator/netns.py:92-93  
**CONCRETE INPUT:** A malicious first caller patches `_netns_ino()` to return None, causing `netns_available()` to cache False permanently. All subsequent `act_then_report` shells run with raw network reach despite actual netns availability.  
**WHY IT BREAKS:** The global `_available` cache is set once and never re-evaluated. A poisoned first probe makes B1 believe isolation is unavailable for the process lifetime.  
**ANOTHER LAYER:** The `shell.raw_network` opt-in requirement still gates autonomy (defense-in-depth), but the "isolation earns autonomy" guarantee is violated.  
**FIX:** Make the cache invalidatable or probe per-session rather than globally.

### F2: freeze_args() Shallow Copy Leaves Nested Mutables
**ID:** F2  
**TITLE:** Nested Mutable Values Bypass freeze_args()  
**SEVERITY:** LOW  
**LOCATION:** collaborator/tools.py:243-256  
**CONCRETE INPUT:** `{"command": ["echo", {"__str__": lambda: "MUTATE"}], "metadata": [[]]}` - the nested list in metadata remains mutable after freeze.  
**WHY IT BREAKS:** `freeze_args()` only freezes top-level command/body/path/content. Other args keys with nested mutables remain shared by reference.  
**ANOTHER LAYER:** The seal only covers consequential fields (command/path/content for SEALED_TOOLS), so non-consequential mutations don't affect execution.  
**FIX:** Deep-copy all args or document that only consequential fields are frozen.

### F3: Tool Rebind With Matching Seal Not Caught
**ID:** F3  
**TITLE:** Tool Identity Binding Insufficient for Empty-Seal Tools  
**SEVERITY:** LOW  
**LOCATION:** collaborator/loop.py:222-233  
**CONCRETE INPUT:** A `read_file` decision with `seal=""` rebound to `web_fetch` (also returns `""` from `held_action_seal`) passes the seal check.  
**WHY IT BREAKS:** Both tools return empty seal, so the rebind isn't detected by seal mismatch.  
**ANOTHER LAYER:** The capability re-gate catches this (different capabilities required), and neither tool is in SEALED_TOOLS.  
**FIX:** Explicitly check tool rebinding before seal verification.

## Certification

**C1 (B1):** **CERTIFIED** - An `act_then_report` run_command with no verified netns and no `shell.raw_network` grant is correctly floored to HELD. The implementation properly checks `enforced(session)` for signed grants, `netns_available()` returns False when verification fails, and the floor composition is monotonic.

**C2 (MINOR-B):** **CERTIFIED** - Held run_command/write_file with mutated args are denied at approval, missing seals fail closed, and the seal framing with length prefixes and tool identity is injective. The freeze_args shallow copy issue (F2) doesn't affect consequential fields.

## Steelman

The B1 floor correctly implements "isolation earns autonomy" with proper verification of actual netns isolation, not just trusting unshare exit codes. The MINOR-B seal extension uniformly closes the by-reference mutation vector across all unbounded-reach tools, with approve() re-freezing as a self-contained boundary. The monotonic floor composition and default-deny design are sound.

## Verdict

**MINOR_ISSUES** - The implementation is fundamentally sound with strong security properties, but the global netns cache poisoning (F1) could cause incorrect autonomy decisions for a process lifetime. **Single highest-value fix:** Make `netns_available()` cache invalidatable or probe per-session to prevent poisoning attacks.
