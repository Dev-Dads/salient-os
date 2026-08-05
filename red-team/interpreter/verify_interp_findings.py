"""Empirically classify each interpreter red-team finding against the real code.
Prints CONFIRMED / REJECTED with observed behavior. Run from repo root."""

import sys
sys.path.insert(0, r"D:\Repo\salient-os")

from salienceos.interpreter import (
    AdaptationEligibility, Facet, SalienceSignal, interpret, issue_policy,
    valid_signal, verify_policy,
)

KEY = b"policy-test-key"


def L(msg):
    print("=" * 3, msg)


def pol(**kw):
    d = dict(policy_id="p", subject="req-1", granted_capabilities=("fs.read:project",),
             min_budget=10, max_budget=1000, min_verification=0, max_verification=3,
             max_retention="semantic", allow_adaptation=True, adaptation_min_verification=2,
             adaptation_max_risk=0.4, allow_immediate_reconfigure=False)
    d.update(kw)
    return issue_policy(d["policy_id"], d["subject"], d["granted_capabilities"], d["min_budget"],
                        d["max_budget"], d["min_verification"], d["max_verification"],
                        d["max_retention"], d["allow_adaptation"], d["adaptation_min_verification"],
                        d["adaptation_max_risk"], d["allow_immediate_reconfigure"], KEY)


def S(facet, infl, conf=1.0):
    return SalienceSignal("x", "req-1", facet, infl, conf, ())


# 1. Grok F1 — zero-confidence RISK inverts the absent default (HIGH)
L("Grok F1: zero-confidence RISK -> less caution")
p = pol(min_verification=2, max_verification=3)
sigs = [S(Facet.ADAPTATION, 1.0), S(Facet.RISK, 1.0, conf=0.0)]
d = interpret(p, sigs, KEY)
d_absent = interpret(p, [S(Facet.ADAPTATION, 1.0)], KEY)
print(f"   with conf=0 RISK: verify={d.verification_depth} adapt={d.adaptation_eligibility.value}")
print(f"   absent RISK:      verify={d_absent.verification_depth} adapt={d_absent.adaptation_eligibility.value}")
print("   >>> CONFIRMED inversion" if (d.verification_depth < d_absent.verification_depth
      or d.adaptation_eligibility is not d_absent.adaptation_eligibility) else "   >>> REJECTED")

# 2. Grok F2 / kimi F1 — throwing iterator crashes interpret (MED/HIGH)
L("Grok F2/kimi F1: throwing generator crashes")
def bad():
    yield S(Facet.ATTENTION, 0.5)
    raise RuntimeError("publisher bug")
try:
    interpret(pol(), bad(), KEY)
    print("   >>> REJECTED (no crash)")
except Exception as e:  # noqa: BLE001
    print(f"   >>> CONFIRMED crash: {type(e).__name__}: {e}")

# 3. glm FC-2 — NaN adaptation_max_risk accepted by verify_policy (LOW/MED)
L("glm FC-2: NaN adaptation_max_risk accepted")
pnan = pol(adaptation_max_risk=float("nan"))
print(f"   verify_policy(NaN risk cap) = {verify_policy(pnan, KEY)}")
print("   >>> CONFIRMED accepted" if verify_policy(pnan, KEY) else "   >>> REJECTED")

# 4. glm P-01-1 — adaptation_min_verification may exceed max_verification (LOW)
L("glm P-01-1: adapt_min_v > max_v accepted")
pinc = pol(max_verification=1, adaptation_min_verification=2)
print(f"   verify_policy(adapt_min_v=2 > max_v=1) = {verify_policy(pinc, KEY)}")
print("   >>> CONFIRMED accepted" if verify_policy(pinc, KEY) else "   >>> REJECTED")

# --- REJECTION CHECKS (claims that should NOT reproduce) ---
print()
L("deepseek F1/qwen F3: tampered granted_capabilities bypasses signature")
good = pol()
tampered = type(good)(**{**good.__dict__, "granted_capabilities": ("host_admin",)})
dt = interpret(tampered, [S(Facet.ATTENTION, 1.0)], KEY)
print(f"   caps after tampering granted_capabilities post-sign: {dt.allowed_capabilities}")
print("   >>> REJECTED (hard-deny, tamper caught)" if dt.allowed_capabilities == () else "   >>> CONFIRMED leak")

L("qwen F2: adaptation reachable with allow_adaptation=False")
pna = pol(allow_adaptation=False)
dna = interpret(pna, [S(Facet.ADAPTATION, 1.0), S(Facet.RISK, 0.0), S(Facet.VERIFICATION, 1.0)], KEY)
print(f"   adapt with allow_adaptation=False: {dna.adaptation_eligibility.value}")
print("   >>> REJECTED (NONE)" if dna.adaptation_eligibility is AdaptationEligibility.NONE else "   >>> CONFIRMED")

L("qwen F1/glm P-01-3: capability-shaped facet changes caps")
dfac = interpret(pol(granted_capabilities=()), [S("fs.write:/etc", 1.0), S("host_admin", 1.0)], KEY)
print(f"   caps with capability-shaped facet signals: {dfac.allowed_capabilities}")
print("   >>> REJECTED (caps empty)" if dfac.allowed_capabilities == () else "   >>> CONFIRMED")

L("deepseek F4: inverted budget window accepted")
try:
    pbad = pol(min_budget=100, max_budget=10)
    print(f"   verify_policy(min=100,max=10) = {verify_policy(pbad, KEY)}")
    print("   >>> REJECTED (rejected)" if not verify_policy(pbad, KEY) else "   >>> CONFIRMED accepted")
except Exception as e:  # noqa: BLE001
    print("   error", e)

L("qwen F6: NaN/inf signal influence accepted")
print(f"   valid_signal(inf influence)={valid_signal(S(Facet.ATTENTION, float('inf')))}  "
      f"valid_signal(nan conf)={valid_signal(S(Facet.ATTENTION, 0.5, float('nan')))}")
print("   >>> REJECTED (both invalid)" if not valid_signal(S(Facet.ATTENTION, float('inf'))) else "   >>> CONFIRMED")
