"""Phase 0 checkpoint: reproduce the V2 rebuild through the HTTP service.

Passes only if the numbers match the local build EXACTLY (gap 0.4000 mm,
zero interference) and all three formats are produced -- plus the negative
case: a broken script must come back as a structured failure, not a crash.
"""
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"
HERE = pathlib.Path(__file__).parent


def post(path: str, payload: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=60) as resp:
        return json.load(resp)


def main() -> int:
    health = get("/health")
    print("SERVICE  python %s | build123d %s" % (health["python"], health["build123d"]))

    code = (HERE / "v2_pair.py").read_text(encoding="utf-8")
    d = post("/build", {"python": code})

    print("\nBUILD    ok=%s  %d ms" % (d["ok"], d["ms"]))
    if d.get("error"):
        print("         error:", d["error"])
    if d.get("traceback"):
        print(d["traceback"])

    print("\n--- script stdout ---")
    print((d.get("stdout") or "").rstrip())

    print("\n--- stats ---")
    print(json.dumps(d.get("stats"), indent=2))

    print("\n--- artifacts ---")
    for kind, info in sorted((d.get("outputs") or {}).items()):
        print("  %-4s %9d bytes  %s" % (kind, info["bytes"], info["path"]))

    out = d.get("stdout") or ""
    stats = d.get("stats") or {}
    outs = d.get("outputs") or {}

    def val(label: str):
        m = re.search(rf"^{label} (.+)$", out, re.M)
        return m.group(1).strip() if m else None

    def num(label: str):
        v = val(label)
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    checks = [
        ("gap is exactly 0.4000 mm", val("GAP") == "0.4000"),
        ("interference is exactly 0", num("INTERFERENCE") == 0.0),
        ("engagement is 7.00 mm", val("ENGAGEMENT") == "7.00"),
        ("derived lid outer is 83.00 x 67.00", val("DERIVED") == "lid_outer 83.00 x 67.00 R9.50"),
        ("body bbox 80.00 x 64.00 x 67.00", val("BODY_BBOX") == "80.00 64.00 67.00"),
        ("lid bbox 83.00 x 67.00 x 10.40", val("LID_BBOX") == "83.00 67.00 10.40"),
        ("solid is valid", stats.get("is_valid") is True),
        ("two solids in the result", stats.get("solids") == 2),
        ("STEP produced", "step" in outs),
        ("STL produced", "stl" in outs),
        ("GLB produced", "glb" in outs),
    ]

    print("\n--- broken script (negative case) ---")
    bad = post("/build", {"python": "this is not python(\n"})
    neg_ok = (bad["ok"] is False) and bool(bad.get("error"))
    print("  ok=%s error=%s" % (bad["ok"], bad.get("error")))
    checks.append(("broken script fails cleanly", neg_ok))

    print("\n=== CHECKS ===")
    failed = 0
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
        failed += 0 if ok else 1

    print("\nPHASE 0:", "ALL PASS" if failed == 0 else "%d FAILED" % failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
