"""LIGHT external review (proportionate depth) for the max_tokens default bump 4096 -> 16384.

Per the standing rule, a non-doc change gets external eyes; this one is a ceiling-raise with
near-zero correctness surface, so depth is deliberately shallow: 3 cheap-but-capable models, a
tight prompt. Attack the one thing that could matter — that raising the OUTPUT cap introduces a
real regression (context overflow, governance/parse interaction, or an operational footgun).
"""
import concurrent.futures as cf
import json
import os
import pathlib
import time
import urllib.request

KEY = os.environ["OPENROUTER_API_KEY"].strip()
HERE = pathlib.Path(__file__).parent

PROMPT = """You are reviewing a ONE-LINE default change in a governed agent loop (SalienceOS
"Collaborator"). Be terse. The change:

  collaborator/model_client.py OllamaClient.__init__ default: max_tokens 4096 -> 16384

Facts:
- `max_tokens` caps the model's OUTPUT (completion) tokens per reply; it is NOT the context
  window. The model (gpt-oss:120b) context window is 131072 tokens (prompt + output shared).
- Typical prompts here are ~1-2k tokens; a typical turn emits a few hundred output tokens.
- The value was raised to give headroom for a legitimately large single turn (e.g. a big
  write_file whose file content is emitted as tool-call-argument tokens; ~400 lines > 4096).
- The loop parses the reply, then governs EVERY tool intent through a default-deny seam
  (govern_action). max_tokens does not touch that path.
- ScriptedClient (tests) ignores max_tokens. Full test suite is green.

Question: is raising the OUTPUT cap 4096 -> 16384 SAFE, or does it introduce a real regression?
Consider only concrete risks: (a) context overflow / does prompt+16384 fit in 131072 given the
stated prompt sizes; (b) any correctness/governance/parsing interaction; (c) operational footguns
(latency/VRAM of a runaway generation) and whether 16384 is a reasonable bound vs alternatives.
Answer in <=8 lines: VERDICT (SAFE / RISKY + why), any concrete regression, and whether 16384 is
a sensible value. If you find nothing, say so explicitly."""

MODELS = ["openai/gpt-5.1", "x-ai/grok-4.5", "qwen/qwen3-max"]


def call(model):
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": PROMPT}],
                       "temperature": 0.2, "max_tokens": 900, "usage": {"include": True}}).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                                          "X-Title": "SalienceOS max_tokens light review"}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.load(r)
    except Exception as e:  # noqa: BLE001
        return {"model": model, "error": str(e)[:200]}
    ch = (d.get("choices") or [{}])[0]
    msg = (ch.get("message") or {})
    return {"model": model, "seconds": round(time.time() - t0, 1),
            "cost": (d.get("usage") or {}).get("cost"),
            "content": msg.get("content") or msg.get("reasoning") or ""}


def main():
    results = {}
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        for fut in cf.as_completed({ex.submit(call, m): m for m in MODELS}):
            r = fut.result(); results[r["model"]] = r
    total = 0.0
    for m in MODELS:
        r = results.get(m, {})
        c = r.get("cost")
        total += c if isinstance(c, (int, float)) else 0.0
        print(f"\n===== {m}  (${c if isinstance(c,(int,float)) else 'n/a'}) =====")
        print(r.get("content") or r.get("error"))
    print(f"\nTOTAL COST: ${total:.4f}")


if __name__ == "__main__":
    main()
