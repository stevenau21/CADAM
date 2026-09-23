"""Run a plan file through the service and print everything worth seeing.

    python tests/run_plan.py tests/container_plan.json
"""
import json
import pathlib
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"


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
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


def main() -> int:
    plan_path = pathlib.Path(sys.argv[1])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    v = post("/validate", {"plan": plan})
    print("VALIDATE ok=%s" % v["ok"])
    for e in v.get("validation_errors") or []:
        print("   %-46s %s" % (e["path"], e["msg"]))
    if not v["ok"]:
        return 1

    d = post("/plan", {"plan": plan})
    print("\nPLAN ok=%s  %d ms  result_id=%s" % (d["ok"], d["ms"], d.get("result_id")))
    if d.get("error"):
        print("   error:", d["error"])
    if d.get("traceback"):
        print(d["traceback"])

    if d.get("stdout"):
        print("\n--- model stdout ---")
        print(d["stdout"].rstrip())

    for c in d.get("checks") or []:
        print("  CHECK %-12s %s<->%s value=%.4f pass=%s"
              % (c["kind"], c["a"], c["b"], c["value"], c.get("pass")))

    if d.get("stats"):
        s = d["stats"]
        print("\n--- stats ---")
        print("  bbox   %.2f x %.2f x %.2f" % tuple(s["bbox"]))
        print("  valid  %s   solids %s   faces %s" % (s["is_valid"], s["solids"], s["faces"]))
        print("  volume %.1f mm3" % (s["volume"] or 0))

    if d.get("outputs"):
        print("\n--- artifacts ---")
        for kind, info in sorted(d["outputs"].items()):
            print("  %-4s %9d bytes" % (kind, info["bytes"]))
    if d.get("artifact_dir"):
        print("\n  dir    %s" % d["artifact_dir"])

    return 0 if d["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
