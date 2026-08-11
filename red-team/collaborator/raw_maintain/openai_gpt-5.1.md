# deputy-seal code panel: openai/gpt-5.1

_finish=stop seconds=16.6 cost=$0.0418 usage={'prompt_tokens': 24469, 'completion_tokens': 1121, 'total_tokens': 25590, 'cost': 0.04179625, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.04179625, 'upstream_inference_prompt_cost': 0.03058625, 'upstream_inference_completions_cost': 0.01121}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID 1 / TITLE Pathname deputy sockets still reachable via host network daemons / SEVERITY HIGH  
LOCATION  
- collaborator/maintain.py:27-40 (`_DEPUTY_SOCKETS`)  
- collaborator/maintain.py:57-81 (`_present_deputy_sockets`)  
- collaborator/netns.py:7-40 (netns docs and scope)  

CONCRETE BYPASS  
Inside the maintenance sandbox the process has:  
- full read–write view of `/` (`--bind / /`),  
- a fresh but routeless netns (`--unshare-net`),  
- only the enumerated deputy path *names* masked to `/dev/null`.

You can still egress IP by talking to a *different* local daemon whose control APIs are on:  

1. A non-masked pathname UNIX socket:  
   - Example: a local HTTP proxy or sidecar at `/run/http-proxy.sock` that can dial arbitrary TCP.  
   - From inside the sandbox:
     ```sh
     # talk to a non-masked forward proxy
     printf 'CONNECT example.com:443 HTTP/1.0\r\n\r\n' \
       | socat - UNIX-CONNECT:/run/http-proxy.sock
     ```
   - Or a DB with `COPY PROGRAM` or an RPC service whose socket path is not in `_DEPUTY_SOCKETS`.

2. An abstract-namespace UNIX socket that controls a network daemon reachable inside this netns.  
   - netns.py only isolates raw/IP and abstract-namespace sockets *between* namespaces; if a daemon has bound an abstract socket inside this netns (e.g. because the parent process already created it before unshare, or a helper was spawned inside the same netns), the sandboxed process can talk to it and ask it to connect out on its behalf.

3. A non-masked TCP loopback listener inside this netns:  
   - Any process the attacker can cause to run inside the same sandbox netns (e.g. via systemd user services or cron configured *before* isolation) that listens on `127.0.0.1:port` can be used as a deputy; bwrap only makes the namespace routeless, not free of other processes.

WHY IT BREAKS THE SEAL OR THE FLAG'S HONESTY  
The ADR describes the deputy seal as closing “known network-capable local daemons reachable by a *pathname* UNIX socket” and suggests “egress.py is the sole IP path off the machine.” In reality, the mask is strictly a static path *blocklist* of a few system sockets. Any other socket-based deputy that can perform IP egress will still happily do so from inside the sandbox. When such a deputy exists, `network_isolated=True` on the Execution object is misleading: the guard’s proof covers only “fresh netns + caps dropped + listed paths masked”, not “no other local egress deputies exist”. That is a semantic gap between the intended “sole IP path” guarantee and the actual implementation.

WHETHER ANOTHER CONTROL CATCHES IT  
No. This is explicitly acknowledged as “BLOCKLIST” residual in the module docstring, so from the author’s point of view it is “known/accepted.” However, the caller-facing `network_isolated` boolean and surrounding ADR text imply a much stronger property (“egress.py is the sole IP-network path”). Nothing in tools.py, maintain.verified_ok, or egressobserver notices that a new deputy appeared at a different pathname. The only mitigation is operator discipline (not exposing such sockets), which is outside this code.

FIX  
- Tighten the public semantics of `network_isolated` to explicitly mean “no raw IP from this process + specific enumerated /run/* deputies sealed” and not “no indirect IP egress possible.”  
- In code/docs, rename the property or add a second flag, e.g. `deputies_sealed`, making clear that the guarantee is *scoped* to `_DEPUTY_SOCKETS`.  
- Optionally, extend `_DEPUTY_SOCKETS` or introduce a configurable blocklist so operators can add additional deputies that exist on their deployment (but the residual class never fully disappears without a very different design, e.g. seccomp AF_UNIX filters or a dedicated minimal filesystem view).

STEELMAN  
The implementation is honest about the blocklist residual in maintain.py’s docstring and never claims to globally eliminate all confused deputies; it aims to close the *known system daemons* that were the concrete risk motivating ADR 0003, without introducing complex new dependencies like LSM or seccomp. The guard/token design cleanly detects regressions in that specific invariant (netns freshness, caps dropped, and those pathnames masked) and refuses to mint the seal if any of those fail, so within its stated scope it behaves robustly.

VERDICT  
SERIOUS_FLAWS – The core “deputy seal” abstraction oversells what is actually enforced; there remain straightforward ways to regain IP egress via non-enumerated local deputies while `network_isolated=True`. The single highest‑value fix is to narrow and clarify the semantics of `network_isolated` and the ADR text to match the blocklist reality, and (if feasible) to expose/parameterise the set of deputies so operators can extend it for their environments.
