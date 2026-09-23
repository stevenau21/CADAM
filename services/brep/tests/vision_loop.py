"""The image-diff loop: render -> compare against the source -> revise the plan.

This is the mechanism that makes text/image -> CAD converge instead of taking a
first guess on faith. Each pass:

    1. compile the plan          (POST /plan on the build service)
    2. render it                 (cadgen stl snapshot)
    3. show the model the SOURCE (image and/or written brief) plus its OWN RENDER
       and ask for a concrete difference list
    4. if there are differences, take the revised plan and go again

The model sees what it actually built, next to what was asked for -- the same
feedback a human modeller uses. Stops when the difference list comes back empty
or the iteration budget runs out.

    python vision_loop.py --plan tests/container_plan.json \
        --reference path/to/container.png --iters 3

`--brief "text"` works without an image (the render is still reviewed).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

from PIL import Image

BASE = "http://127.0.0.1:8765"
OLLAMA = "https://ollama.com/v1/chat/completions"
HERE = pathlib.Path(__file__).parent
CADGEN = "F:/projects/3D/text-to-cad/.venv/Scripts/cadgen.exe"

OP_GUIDE = """
sketch.rect    {w, h, r=0, plane=XY|XZ|YZ, at=[x,y,z]}      rounded rectangle profile
sketch.circle  {d, plane, at}                                circle profile
sketch.polygon {points=[[x,y],...], plane, at}               arbitrary closed profile
extrude        {profile, height, taper=0}                    profile -> solid
revolve        {profile, angle=360, axis=x|y|z}              profile about an axis
boolean        {kind=union|cut|intersect, a, b}
translate      {target, x, y, z}
rotate         {target, x, y, z}                             degrees
fillet         {target, radius, select=all|z_max|z_min|vertical|horizontal|circular}
chamfer        {target, length, select=...}
shell          {target, thickness, open=none|z_max|z_min}
hole           {target, d, positions=[[x,y],...], through=true, depth, z}
pattern.linear {target, count, spacing, axis}
pattern.polar  {target, count, axis, angle, center}
""".strip()

SYSTEM = f"""You are a CAD reviewer. You are given a SOURCE (a reference image and/or a
written brief) and a RENDER of the model that was actually built from a plan.

Your job is to compare them and report ONLY concrete geometric differences, then
repair the plan.

Rules:
- Units are mm. Z is up. The build plate is Z = 0.
- Numeric fields accept a number or an arithmetic expression string over the
  plan's symbols, e.g. "body_h - rim_h". Use expressions so derived dimensions
  stay derived; never repeat a value that is computed from others.
- Parameters are user-facing sliders; put anything the user would tweak there.
- Every feature needs a unique "id"; later features refer to earlier ids by name.
- Reply with ONE json object and nothing else:
  {{"differences": ["...", "..."],
    "plan": {{ ...the full corrected plan... }}}}

- If the render already matches the source, return an empty "differences" list
  and repeat the plan unchanged.
- Be specific in "differences": name the part and the amount
  (e.g. "the ribs stop at 60mm but should run to 74mm", "the lid handle is 30mm
  wide but should be about 40mm"). Do not say vague things like "looks different".
- Keep everything that is already right; change only what is wrong.

Available operations:
{OP_GUIDE}
"""


def encode_image(path: str, max_side: int = 1024) -> str:
    img = Image.open(path).convert("RGB")
    if max(img.size) > max_side:
        scale = max_side / max(img.size)
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii"), img.size


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


def compile_plan(plan: dict) -> dict:
    return post("/plan", {"plan": plan})


def render(stl_path: str, out_png: str) -> bool:
    try:
        proc = subprocess.run(
            [CADGEN, "stl", "snapshot", stl_path, out_png],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0:
            print("   render failed:", proc.stderr.strip()[:200])
            return False
        return os.path.exists(out_png)
    except Exception as exc:  # noqa: BLE001
        print("   render error:", exc)
        return False


def call_model(api_key: str, model: str, content: list, timeout: int = 900) -> tuple[str, dict]:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": content},
        ],
        # Reasoning models spend tokens on `reasoning` before emitting `content`;
        # a tight budget leaves content EMPTY while finish_reason is "length".
        "max_tokens": 32000,
    }
    req = urllib.request.Request(
        OLLAMA,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    choice = data["choices"][0]
    msg = choice["message"]
    text = msg.get("content") or ""
    if not text.strip():
        # Fall back to the reasoning stream rather than failing silently -- it
        # often contains the JSON when `content` came back empty.
        text = msg.get("reasoning") or ""
    meta = {
        "finish_reason": choice.get("finish_reason"),
        "usage": data.get("usage"),
        "had_content": bool((msg.get("content") or "").strip()),
        "reasoning_chars": len(msg.get("reasoning") or ""),
    }
    return text, meta


def extract_json(text: str) -> dict | None:
    """Models wrap JSON in prose or fences; take the last balanced object."""
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = fenced + [text]
    for blob in candidates:
        start = blob.find("{")
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(blob)):
                ch = blob[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(blob[start : i + 1])
                            if isinstance(obj, dict) and "plan" in obj:
                                return obj
                        except json.JSONDecodeError:
                            pass
                        break
            start = blob.find("{", start + 1)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--reference", help="reference image path")
    ap.add_argument("--brief", default="", help="written requirements, used with or without an image")
    ap.add_argument("--model", default="glm-5.3-flash")
    ap.add_argument("--iters", type=int, default=3)
    ap.add_argument("--out", default="vision_runs")
    ap.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""))
    args = ap.parse_args()

    if not args.api_key:
        print("no API key: pass --api-key or set OPENAI_API_KEY")
        return 2

    run_dir = pathlib.Path(HERE) / args.out / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    plan = json.loads(pathlib.Path(args.plan).read_text(encoding="utf-8"))
    ref_b64 = None
    if args.reference:
        ref_b64, ref_size = encode_image(args.reference)
        print("reference %s %sx%s" % (args.reference, ref_size[0], ref_size[1]))
    print("model     %s   iters %d   run %s\n" % (args.model, args.iters, run_dir.name))

    carry_error = ""
    history = []

    for it in range(1, args.iters + 1):
        print("=" * 70)
        print("ITERATION %d" % it)
        print("=" * 70)

        result = compile_plan(plan)
        status = "ok" if result["ok"] else "FAILED: %s" % (result.get("error") or result.get("validation_errors"))
        print("  compile  %s  (%d ms)" % (status, result["ms"]))

        (run_dir / ("plan_%02d.json" % it)).write_text(json.dumps(plan, indent=2), encoding="utf-8")

        render_png = None
        if result["ok"]:
            stl = (result.get("outputs") or {}).get("stl", {}).get("path")
            if stl:
                candidate = str(run_dir / ("render_%02d.png" % it))
                if render(stl, candidate):
                    render_png = candidate
            s = result["stats"]
            print("  bbox     %.2f x %.2f x %.2f   solids %s   valid %s"
                  % (s["bbox"][0], s["bbox"][1], s["bbox"][2], s["solids"], s["is_valid"]))
            for c in result.get("checks") or []:
                print("  check    %-12s value=%.4f pass=%s" % (c["kind"], c["value"], c.get("pass")))
        else:
            carry_error = json.dumps(result.get("validation_errors") or result.get("error"))[:1500]
            print("  error    %s" % carry_error[:300])

        content: list = []
        text = []
        if args.brief:
            text.append("REQUIREMENTS (the source brief):\n" + args.brief)
        text.append("CURRENT PLAN:\n" + json.dumps(plan))
        if carry_error:
            text.append(
                "The previous revision FAILED to compile with this error. Fix it:\n" + carry_error
            )
        text.append(
            "Compare the SOURCE against the RENDER and reply with the json object described."
        )
        content.append({"type": "text", "text": "\n\n".join(text)})
        if ref_b64:
            content.append({"type": "text", "text": "SOURCE image:"})
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + ref_b64}})
        if render_png:
            b64, _ = encode_image(render_png)
            content.append({"type": "text", "text": "RENDER of the current plan:"})
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}})

        try:
            raw, meta = call_model(args.api_key, args.model, content)
        except Exception as exc:  # noqa: BLE001
            print("  model call failed: %s" % str(exc)[:300])
            break

        print("  model    finish=%s  content=%s  reasoning=%dch  tokens=%s"
              % (meta["finish_reason"], meta["had_content"], meta["reasoning_chars"],
                 (meta.get("usage") or {}).get("completion_tokens")))
        (run_dir / ("model_%02d.txt" % it)).write_text(raw, encoding="utf-8")
        parsed = extract_json(raw)
        if parsed is None:
            print("  could not parse a plan from the reply (saved to model_%02d.txt)" % it)
            break

        diffs = parsed.get("differences") or []
        print("\n  DIFFERENCES (%d):" % len(diffs))
        for d in diffs:
            print("    - %s" % d)
        history.append({"iteration": it, "differences": diffs})

        if not diffs:
            print("\n  converged: no differences left.")
            break

        plan = parsed["plan"]
        carry_error = ""
        (run_dir / ("revised_%02d.json" % it)).write_text(json.dumps(plan, indent=2), encoding="utf-8")
        print("  revised plan saved\n")

    (run_dir / "summary.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    print("\nrun dir: %s" % run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
