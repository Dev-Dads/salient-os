"""Resource-governor live demo — the agent yields real GPU compute to the human.

Drives the REAL governor (box.resource_governor via box.server's built _governor)
against the REAL Sparky GPU (util/power read through the gpu-probe tunnel) and the
REAL lever (ollama model swap + keep_alive:0 unload on the :11500 NVMe instance).

What is REAL here: the tier decision, the GPU measurement, the model swap on the
live agent, and the gpt-oss:120b unload/reload you can watch in `ollama ps` and on
the DGX dashboard. What is the SCENARIO (supplied, not measured): the foreground
flag (stands in for "the human is at the machine / a game is up") and the salience
value (in production it comes from the H3 directive's compute_budget via
normalize_salience; here it is passed directly so each step is reproducible). The
"game" is a second ollama model (qwen2.5:7b) generating in a loop — a genuine GB10
GPU consumer, started/stopped over ssh.

Run from the fork repo root:  python box/demo_resource_governor.py [out.jsonl]
Env (defaults target the Sparky tunnels):
  BOX_MODEL, BOX_FAST_MODEL, BOX_OLLAMA_OPENAI_URL, BOX_OLLAMA_NATIVE_URL,
  BOX_GPU_METRICS_URL, SPARKY_SSH (default chance6706@sparky).
"""

import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import urllib.request

BOX_DIR = pathlib.Path(__file__).resolve().parent
REPO = BOX_DIR.parent
sys.path.insert(0, str(REPO))  # so `import box.server` resolves

# Demo defaults (point the box at the Sparky NVMe ollama + gpu-probe via the tunnels).
os.environ.setdefault("BOX_MODEL", "gpt-oss:120b")
os.environ.setdefault("BOX_FAST_MODEL", "llama3.2:3b")
os.environ.setdefault("BOX_OLLAMA_OPENAI_URL", "http://localhost:11500/v1")
os.environ.setdefault("BOX_OLLAMA_NATIVE_URL", "http://localhost:11500")
os.environ.setdefault("BOX_GPU_METRICS_URL", "http://localhost:11502/gpu")

SPARKY = os.environ.get("SPARKY_SSH", "chance6706@sparky")
GPU_URL = os.environ["BOX_GPU_METRICS_URL"]
NATIVE_URL = os.environ["BOX_OLLAMA_NATIVE_URL"]
FULL_MODEL = os.environ["BOX_MODEL"]
FAST_MODEL = os.environ["BOX_FAST_MODEL"]
GAME_MODEL = os.environ.get("GAME_MODEL", "qwen2.5:7b")

import box.server as bs  # noqa: E402  (env must be set first)


# ---- observation helpers ----------------------------------------------------

def gpu():
    try:
        with urllib.request.urlopen(GPU_URL, timeout=3) as r:
            return json.load(r)
    except Exception as e:
        return {"util": None, "power_w": None, "sm_clock": None, "err": str(e)}


def loaded_models():
    """Names currently resident on the :11500 ollama (GET /api/ps)."""
    try:
        with urllib.request.urlopen(NATIVE_URL.rstrip("/") + "/api/ps", timeout=3) as r:
            data = json.load(r)
        return [m.get("name") or m.get("model") for m in data.get("models", [])]
    except Exception:
        return []


def _ssh(cmd):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", SPARKY, cmd],
                          capture_output=True, text=True, timeout=30)


def game(on):
    """Start/stop the synthetic 'game' GPU load (a qwen2.5:7b generate loop)."""
    if on:
        gen = ('while true; do curl -s http://127.0.0.1:11500/api/generate '
               '-d "{\\"model\\":\\"%s\\",\\"prompt\\":\\"Narrate a long slow count '
               'from 1 to 400 with commentary.\\",\\"stream\\":false,'
               '\\"options\\":{\\"num_predict\\":1024}}" >/dev/null 2>&1; done' % GAME_MODEL)
        _ssh("sudo systemctl reset-failed game-load 2>/dev/null; "
             "sudo systemd-run --unit=game-load --uid=chance6706 "
             "/bin/bash -c '%s'" % gen)
    else:
        _ssh("sudo systemctl stop game-load 2>/dev/null")


def wait_util(pred, timeout=40, label=""):
    """Poll GPU util until pred(util) or timeout; returns the last util seen."""
    end = time.time() + timeout
    u = None
    while time.time() < end:
        g = gpu()
        u = g.get("util")
        if u is not None and pred(u):
            return u
        time.sleep(1.5)
    print(f"    (wait_util timed out {label}: last util={u})")
    return u


RECORDS = []


def snap(step, decision, note):
    g = gpu()
    models = loaded_models()
    rec = {
        "step": step,
        "tier": decision["tier"],
        "pressure": decision["pressure"],
        "salience": decision["salience"],
        "foreground": decision["foreground"],
        "agent_model": decision["model"],
        "pace_seconds": decision["pace_seconds"],
        "gpu_util": g.get("util"),
        "gpu_power_w": g.get("power_w"),
        "gpu_sm_clock": g.get("sm_clock"),
        "loaded_models": models,
        "gpt_oss_loaded": any(FULL_MODEL in (m or "") for m in models),
        "note": note,
    }
    RECORDS.append(rec)
    print(f"  {step:<3} tier={rec['tier']:<5} press={rec['pressure']:<5} "
          f"sal={rec['salience']:<4} model={rec['agent_model']:<14} "
          f"util={rec['gpu_util']}% pow={rec['gpu_power_w']}W "
          f"gpt-oss_loaded={rec['gpt_oss_loaded']}  | {note}")
    return rec


# ---- the trajectory ---------------------------------------------------------

def main():
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (BOX_DIR / "governor_trajectory.jsonl")
    gov = bs._governor
    assert gov is not None, "governor not built — check settings.yaml resource_governor block"
    agent = bs._get_agent("full")  # real box agent; govern() mutates agent.model in place

    print(f"backend: openai={os.environ['BOX_OLLAMA_OPENAI_URL']} native={NATIVE_URL} gpu={GPU_URL}")
    print(f"models: FULL={FULL_MODEL}  LIGHT(fast)={FAST_MODEL}  game={GAME_MODEL}")
    print(f"policy: light_pressure={gov.cfg.get('light_pressure')} "
          f"pause_pressure={gov.cfg.get('pause_pressure')} "
          f"salience_weight={gov.cfg.get('salience_weight')} "
          f"foreground_min_pressure={gov.cfg.get('foreground_min_pressure')} "
          f"allow_pause={gov.cfg.get('allow_pause')}\n")

    game(False)  # clean start
    print("clearing GPU (stopping any game load); waiting for idle...")
    wait_util(lambda u: u < 30, timeout=20, label="idle")

    # -- S1: agent alone -> FULL. Run a REAL governed turn and sample GPU during it
    #        to show gpt-oss:120b pegging the GPU (Josh's ~95% concern), governed FULL.
    print("\nS1  agent alone -> FULL (real governed turn on gpt-oss:120b; sampling GPU live)")
    bs.set_foreground(False)
    peak = {"util": 0.0, "power": 0.0}
    err = {}

    def run_turn():
        try:
            r = bs.do_chat("In two sentences, explain the rule 'salience influences; policy authorizes'.")
            err["reply"] = (r.get("reply") or "")[:160]
        except Exception as e:  # fall back to a raw generate so S1 still pegs the GPU
            err["exc"] = str(e)
            try:
                body = json.dumps({"model": FULL_MODEL, "prompt": "Write a detailed paragraph about GPUs.",
                                   "stream": False, "options": {"num_predict": 256}}).encode()
                req = urllib.request.Request(NATIVE_URL.rstrip("/") + "/api/generate", data=body,
                                             headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(req, timeout=300).read()
            except Exception as e2:
                err["exc2"] = str(e2)

    t = threading.Thread(target=run_turn)
    t.start()
    while t.is_alive():
        g = gpu()
        if g.get("util") is not None:
            peak["util"] = max(peak["util"], g["util"])
            peak["power"] = max(peak["power"], g.get("power_w") or 0.0)
        time.sleep(1.5)
    t.join()
    print(f"    turn done. peak GPU during turn: util={peak['util']}% power={peak['power']}W  {err}")
    d = gov.last() or gov.govern(agent, bs._current_salience(), False)
    r1 = snap("S1", d, f"agent alone pegs GPU (peak {peak['util']}%/{peak['power']}W); governed FULL")
    r1["peak_util"], r1["peak_power_w"] = peak["util"], peak["power"]

    # -- S2: human present (foreground on), GPU still idle -> LIGHT via the foreground
    #        floor; the big model unloads even before the game spins up.
    print("\nS2  human sits down (foreground=on), GPU idle -> LIGHT (foreground floor), gpt-oss unloads")
    bs.set_foreground(True)
    d = gov.govern(agent, 0.0, True)  # pressure=max(util,0.6)=0.6, salience 0 -> eff .6 -> LIGHT
    time.sleep(2)  # let the unload settle before snapping ollama ps
    snap("S2", d, "presence alone (floor 0.6) -> LIGHT; agent.model->fast, gpt-oss:120b unloaded")

    # -- S3: game now hammering the GPU + HIGH salience -> relief holds LIGHT (not PAUSE).
    print("\nS3  game hammering GPU + HIGH salience (0.9) -> holds LIGHT (relief), not PAUSE")
    game(True)
    u = wait_util(lambda u: u >= 80, timeout=45, label="game peg")
    print(f"    game load up: util={u}%")
    d = gov.govern(agent, 0.9, True)  # pressure~.93, relief .36, eff ~.57 -> LIGHT
    snap("S3", d, "heavy GPU + salience 0.9 (relief .36) -> LIGHT; important work stays alive")

    # -- S4: same heavy GPU + LOW salience -> PAUSE (+unload + heavy pace).
    print("\nS4  same heavy GPU + LOW salience (0.0) -> PAUSE (+ pace), full yield")
    d = gov.govern(agent, 0.0, True)  # eff ~.93 -> PAUSE
    snap("S4", d, "heavy GPU + salience 0.0 -> PAUSE; big model unloaded, turn paced")

    # -- S5: human done -> FULL restored (gpt-oss reselected).
    print("\nS5  human leaves (foreground=off, game stopped) -> FULL restored")
    game(False)
    bs.set_foreground(False)
    wait_util(lambda u: u < 30, timeout=25, label="idle again")
    d = gov.govern(agent, 0.0, False)  # pressure 0 -> FULL, model -> gpt-oss:120b
    snap("S5", d, "pressure gone -> FULL; agent.model restored to gpt-oss:120b")

    out.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n", encoding="utf-8")
    print(f"\nwrote {len(RECORDS)} steps -> {out}")

    # quick self-check of the expected trajectory
    tiers = [r["tier"] for r in RECORDS]
    expected = ["full", "light", "light", "pause", "full"]
    print(f"tiers   = {tiers}")
    print(f"expected= {expected}")
    print("TRAJECTORY OK" if tiers == expected else "TRAJECTORY MISMATCH")


if __name__ == "__main__":
    try:
        main()
    finally:
        game(False)  # never leave the game load running
