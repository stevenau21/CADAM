"""Phase 1 checkpoint: the ModelPlan IR.

Passes only if:
  * the V2 model compiles FROM A PLAN to the same numbers as the code build
  * the plan's own fit CHECKS pass (gap == clearance, interference == 0)
  * a malformed plan is rejected structurally, BEFORE the kernel runs
  * the Phase 0 code path still works (no regression)

The last two are the point of the whole phase: "the model wrote bad code" becomes
"field features.3.height failed validation".
"""
import json
import pathlib
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"
HERE = pathlib.Path(__file__).parent


def post(path: str, payload: dict, timeout: int = 900) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:  # a 422 is still a structured answer
        return json.loads(exc.read().decode("utf-8"))


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=60) as resp:
        return json.load(resp)


def main() -> int:
    checks: list[tuple[str, bool]] = []

    health = get("/health")
    print("SERVICE  python %s | build123d %s | modes %s"
          % (health["python"], health["build123d"], health["modes"]))

    ops = get("/ops")
    print("OPS      %s" % ", ".join(ops["ops"]))

    plan = json.loads((HERE / "v2_plan.json").read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- validate
    v = post("/validate", {"plan": plan})
    print("\nVALIDATE ok=%s errors=%s" % (v["ok"], v.get("validation_errors")))
    checks.append(("well-formed plan validates", v["ok"] is True))

    # -------------------------------------------------------------------- plan
    d = post("/plan", {"plan": plan})
    print("\nPLAN     ok=%s  %d ms  result_id=%s" % (d["ok"], d["ms"], d.get("result_id")))
    if d.get("error"):
        print("         error:", d["error"])
    if d.get("traceback"):
        print(d["traceback"])
    print("\n--- script stdout ---")
    print((d.get("stdout") or "").rstrip())

    sym = d.get("symbols") or {}
    print("\n--- key derived symbols ---")
    for k in ("cav_x", "cav_y", "skirt_in_x", "skirt_in_y", "lid_out_x", "lid_out_y", "skirt_h"):
        if k in sym:
            print("  %-12s %.4f" % (k, sym[k]))

    print("\n--- checks (evaluated by the compiler) ---")
    for c in d.get("checks") or []:
        print("  %-12s %s<->%s  value=%.6f  expect=%s  pass=%s"
              % (c["kind"], c["a"], c["b"], c["value"], c.get("expect"), c.get("pass")))

    print("\n--- stats ---")
    print(json.dumps(d.get("stats"), indent=2))
    print("\n--- artifacts ---")
    for kind, info in sorted((d.get("outputs") or {}).items()):
        print("  %-4s %9d bytes" % (kind, info["bytes"]))

    stats = d.get("stats") or {}
    outs = d.get("outputs") or {}
    plan_checks = {c["kind"]: c for c in (d.get("checks") or [])}

    def close(a, b, tol=1e-3):
        try:
            return abs(float(a) - float(b)) <= tol
        except (TypeError, ValueError):
            return False

    checks += [
        ("plan compiled", d["ok"] is True),
        ("result is the declared feature", d.get("result_id") == "assembly"),
        ("gap check PASSES and equals the clearance parameter", bool(plan_checks.get("gap", {}).get("pass")) and close(plan_checks["gap"]["value"], 0.4, 1e-4)),
        ("interference check PASSES and is 0", bool(plan_checks.get("interference", {}).get("pass")) and close(plan_checks["interference"]["value"], 0.0, 1e-6)),
        ("derived lid_out_x == 83.00 (from derivation, not typed)", close(sym.get("lid_out_x"), 83.0)),
        ("derived lid_out_y == 67.00", close(sym.get("lid_out_y"), 67.0)),
        ("derived cav_x == 72.20", close(sym.get("cav_x"), 72.2)),
        ("assembly bbox matches the code build (83 x 67 x 70.4)",
         close(stats.get("bbox", [0, 0, 0])[0], 83.0) and close(stats.get("bbox", [0, 0, 0])[1], 67.0) and close(stats.get("bbox", [0, 0, 0])[2], 70.4)),
        ("solid is valid", stats.get("is_valid") is True),
        ("two solids", stats.get("solids") == 2),
        ("STEP produced", "step" in outs),
        ("STL produced", "stl" in outs),
        ("GLB produced", "glb" in outs),
    ]

    # ------------------------------------------------------- negative: schema
    print("\n--- negative: structural validation errors ---")
    bad_type = json.loads(json.dumps(plan))
    bad_type["features"][3]["height"] = True          # boolean where a number belongs
    r1 = post("/validate", {"plan": bad_type})
    locs1 = [e["path"] for e in (r1.get("validation_errors") or [])]
    msgs1 = [e.get("msg", "") for e in (r1.get("validation_errors") or [])]
    print("  boolean-as-number ->", locs1[:3])
    named = [p for p in locs1 if "features.3" in p and "height" in p]
    checks.append(("a boolean in a numeric field is rejected, naming the field and why",
                   r1["ok"] is False and bool(named) and any("boolean" in m for m in msgs1)))

    bad_op = json.loads(json.dumps(plan))
    bad_op["features"][0]["op"] = "sketch.hexagon"    # invented op
    r2 = post("/validate", {"plan": bad_op})
    print("  invented op ->", r2.get("error"))
    checks.append(("an invented op is rejected", r2["ok"] is False))

    bad_field = json.loads(json.dumps(plan))
    bad_field["features"][0]["width"] = 10            # typo'd field name
    r3 = post("/validate", {"plan": bad_field})
    print("  typo'd field ->", [e["path"] for e in (r3.get("validation_errors") or [])][:3])
    checks.append(("a typo'd field is rejected (extra=forbid)", r3["ok"] is False))

    # ------------------------------------------- negative: compile-time errors
    print("\n--- negative: compile-time structural errors ---")
    bad_expr = json.loads(json.dumps(plan))
    bad_expr["derived"].append({"name": "oops", "expr": "__import__('os').system('echo pwned')"})
    r4 = post("/plan", {"plan": bad_expr})
    print("  unsafe expression ->", r4.get("error"))
    checks.append(("arbitrary code in an expression is refused", r4["ok"] is False))
    checks.append(("the refusal names the offending expression",
                   "not allowed" in (r4.get("error") or "") or "not allowed" in (r4.get("traceback") or "")))

    bad_ref = json.loads(json.dumps(plan))
    bad_ref["features"][1]["profile"] = "no_such_profile"
    r5 = post("/plan", {"plan": bad_ref})
    print("  dangling reference ->", r5.get("error"))
    checks.append(("a dangling feature reference is refused", r5["ok"] is False))

    bad_sel = json.loads(json.dumps(plan))
    bad_sel["features"].append({"op": "fillet", "id": "f1", "target": "body", "radius": 1, "select": "z_nonexistent"})
    r6 = post("/plan", {"plan": bad_sel})
    print("  bad selector ->", r6.get("error"))
    checks.append(("an unknown selector is refused", r6["ok"] is False))

    # -------------------------------------------------- regression: phase 0
    print("\n--- regression: Phase 0 code path ---")
    code = (HERE / "v2_pair.py").read_text(encoding="utf-8")
    r7 = post("/build", {"python": code})
    print("  /build ok=%s  %d ms" % (r7["ok"], r7["ms"]))
    checks.append(("Phase 0 /build still works", r7["ok"] is True))
    checks.append(("Phase 0 still reports gap 0.4000", "GAP 0.4000" in (r7.get("stdout") or "")))

    # ---------------------------------------------------------------- report
    print("\n=== CHECKS ===")
    failed = 0
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
        failed += 0 if ok else 1
    print("\nPHASE 1:", "ALL PASS" if failed == 0 else "%d FAILED" % failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
