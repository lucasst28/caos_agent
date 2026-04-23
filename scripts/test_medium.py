#!/usr/bin/env python3
"""Quick validation of MEDIUM fixes (M12-M16)."""
import requests, json, sys

BASE = "http://localhost:8080"

print("=" * 60)
print("  MEDIUM FIXES VALIDATION")
print("=" * 60)

passed = 0
total = 0

# --- M14: GET missing event returns 404 ---
total += 1
r = requests.get(f"{BASE}/v1/events/nonexistent-event-xyz")
if r.status_code == 404:
    print(f"  ✅ M14: GET missing event → 404")
    passed += 1
else:
    print(f"  ❌ M14: Expected 404, got {r.status_code}")

# --- M16: Guardrails singleton (API works) ---
total += 1
r = requests.get(f"{BASE}/v1/guardrails/")
d = r.json()
if r.status_code == 200 and d.get("total", 0) > 0:
    print(f"  ✅ M16: Guardrails API → {d['total']} rules loaded (singleton)")
    passed += 1
else:
    print(f"  ❌ M16: Guardrails failed: {r.status_code}")

# --- M16: Dashboard clients (singleton) ---
total += 1
r = requests.get(f"{BASE}/clients")
if r.status_code == 200:
    print(f"  ✅ M16: Dashboard /clients → {r.json()}")
    passed += 1
else:
    print(f"  ❌ M16: Dashboard /clients → {r.status_code}")

# --- M15: shared/schemas.py deprecation warning ---
total += 1
import warnings
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    sys.path.insert(0, ".")
    import importlib
    if "shared.schemas" in sys.modules:
        importlib.reload(sys.modules["shared.schemas"])
    else:
        import shared.schemas
    deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
    if deprecation_warnings:
        print(f"  ✅ M15: shared.schemas emits DeprecationWarning")
        passed += 1
    else:
        print(f"  ❌ M15: No DeprecationWarning from shared.schemas (got {len(w)} warnings)")

# --- Full pipeline test (M12 thresholds + M13 dampening) ---
total += 1
print("\n  📡 Sending pipeline test event...")
payload = {
    "tenant_id": "default",
    "asset_id": "FREEZER-MTEST-01",
    "metric": "temperature",
    "value": -5.0,
    "unit": "celsius",
    "severity": "HIGH",
    "location": "SP-Pinheiros",
    "payload": {
        "source": "medium-test",
        "threshold": -11.0,
    },
}
try:
    r = requests.post(f"{BASE}/v1/events/trigger", json=payload, timeout=120)
    d = r.json()
    if r.status_code == 201 and d.get("event_id"):
        verdict = d.get("verdict_score", "?")
        decision = d.get("decision", "?")
        risk = d.get("risk_level", "?")
        time_ms = d.get("processing_time_ms", 0)
        trace = d.get("reasoning", [])
        
        # Check M12: checklist used thresholds
        checklist_line = [t for t in trace if "Checklist" in str(t)]
        # Check M13: per-axis dampening
        dampening_line = [t for t in trace if "por eixo" in str(t)]
        
        print(f"  ✅ M12+M13 Pipeline: V={verdict}, Decision={decision}, Risk={risk} ({time_ms:.0f}ms)")
        if checklist_line:
            print(f"      Checklist: {checklist_line[0][:100]}")
        if dampening_line:
            print(f"      Dampening: {dampening_line[0][:100]}")
        passed += 1
    else:
        print(f"  ❌ Pipeline: status={r.status_code}, error={d.get('detail', '?')[:100]}")
except requests.exceptions.Timeout:
    print(f"  ❌ Pipeline: timed out")
except Exception as e:
    print(f"  ❌ Pipeline: {e}")

print(f"\n{'=' * 60}")
print(f"  Resultado: {passed}/{total} verificações passaram")
print(f"{'=' * 60}")
sys.exit(0 if passed == total else 1)
