"""Vision-diff loop v2: multi-view renders + patch-based revisions.

Two things were wrong with v1, both found by running it:

1. It compared ONE render (the closed assembly) against a source image that
   shows the container OPEN, TRANSPARENT and SECTIONED. The critic could not
   see the funnel, the dish or the spout -- the very features that matter -- and
   said so: "the RENDER shows the container with the lid closed, whereas the
   SOURCE shows it open."  Fixed here by rendering a comparable SET of views:
   the assembly, a mid-plane SECTION, and each component on its own.

2. It asked the model to return the ENTIRE plan (~17 KB, ~4k tokens). Reasoning
   models spend the whole budget thinking and return nothing at all
   (finish=length, content=False, 42k chars of reasoning). Fixed here by asking
   for a DIFF plus a small PATCH, which we apply deterministically ourselves.

    python vision_loop.py --plan tests/container_decomposed_plan.json \
        --reference path/to/container.png --iters 3 --model gemma4:31b
"""
from __future__ import annotations

import argparse
import base64
import copy
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
sketch.rect    {w, h, r=0, plane=XY|XZ|YZ, at=[x,y,z]}
sketch.circle  {d, plane, at}
sketch.polygon {points=[[x,y],...], plane, at}
extrude        {profile, height, taper=0}
revolve        {profile, angle=360, axis=x|y|z}
loft           {profiles=[...], ruled=false}
import.step    {file, at, rotate}
boolean        {kind=union|cut|intersect, a, b}
translate      {target, x, y, z}
rotate         {target, x, y, z}
fillet         {target, radius, select=all|z_max|z_min|vertical|horizontal|circular}
chamfer        {target, length, select=...}
shell          {target, thickness, open=none|z_max|z_min}
hole           {target, d, positions=[[x,y],...], through=true, depth, z}
pattern.linear {target, count, spacing, axis}
pattern.polar  {target, count, axis, angle, center}
""".strip()

SYSTEM_TEMPLATE = """You are a CAD reviewer. You are shown a SOURCE (a reference image
and/or a written brief) and several RENDER views of the model built from a plan:
the assembly, a mid-plane section, and individual components.

Compare them and report ONLY concrete geometric differences, then describe how to
fix the PLAN.

Rules:
- Units are mm. Z is up. The build plate is Z = 0.
- AXES, so orientation is never ambiguous: X = width (left-right), Y = depth
  (front-back), Z = height. A tunnel through "front to back" runs along Y. If a
  requirement uses a word like "front" or "side", map it to an axis yourself and
  state the axis in the difference -- never report an orientation problem twice
  for the same feature set.
- Reply with ONE json object and nothing else, in exactly this shape:

  {"differences": ["specific, geometric, with amounts"],
   "patch": {
     "set_parameters": {"rib_pitch": 4.0},
     "remove_features": ["feature_id"],
     "set_features": [{"op": "...", "id": "...", "...": "..."}],
     "append_features": [{"op": "...", "id": "...", "...": "..."}]
   }}

- Send ONLY what must change. Do NOT restate the whole plan -- the patch is
  applied to the existing plan for you. Empty lists and an empty object are fine.
- A parameter is a slider object: name, value, and optional min/max/step/label.
- Numeric fields accept a number or an arithmetic expression over the plan's
  symbols, e.g. "body_h - rim_h". Prefer expressions so derived dimensions stay
  derived -- never repeat a value that is computed from others.
- Every feature needs a unique id; later features refer to earlier ids by name.
- The section view shows the interior. Judge interior features (funnel, dish,
  spout, wall thickness) from it, not from the assembled exterior.
- If the SOURCE has a section, cutaway or transparent panel, READ it: it is the
  only place wall thickness, cavity depth, bore diameters and internal profiles
  exist. An exterior render cannot answer an interior question, so never report
  an interior difference you did not see in a section.
- If the SOURCE shows the same object more than once, those are views of ONE
  object, not several. Do not report a difference that is really just another
  angle.
- Ignore anything in the SOURCE that is not the part itself: liquid or contents,
  background, floor, shadows, reflections. Never ask for those as geometry.
- You cannot measure from an image, and neither can anyone else. Report
  PROPORTION as a fraction of the whole ("the ribs stop at about 72% of the
  height") rather than inventing millimetres, and let the plan derive real sizes
  from the parameters.
- If a render looks correct, return an empty differences list and an empty patch.
- Be specific: name the part and the amount ("the ribs stop at 60mm but should
  run to 74mm"). Never say vague things like "looks different".

Available operations:
<<OPS>>
"""

# Built by substitution, not an f-string: the prompt contains literal JSON braces,
# and f-string brace escaping is a silent trap (it raised "Format specifier
# missing precision" on the first single-brace line).
SYSTEM = SYSTEM_TEMPLATE.replace("<<OPS>>", OP_GUIDE)


# --------------------------------------------------------------------- util
def encode_image(path: str, max_side: int = 1024) -> str:
    img = Image.open(path).convert("RGB")
    if max(img.size) > max_side:
        scale = max_side / max(img.size)
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


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


def render_stl(stl_path: str, out_png: str) -> bool:
    try:
        proc = subprocess.run(
            [CADGEN, "stl", "snapshot", stl_path, out_png],
            capture_output=True, text=True, timeout=600,
        )
        return proc.returncode == 0 and os.path.exists(out_png)
    except Exception:
        return False


# ------------------------------------------------------------------- views
def section_variant(plan: dict) -> dict | None:
    """Append a cut that removes everything at y > 0, exposing the interior."""
    result_id = plan.get("result") or plan["features"][-1]["id"]
    ids = {f["id"] for f in plan["features"]}
    for probe in ("__sec_sk", "__sec_solid", "__sec_pos", "__sec"):
        if probe in ids:
            return None
    varied = copy.deepcopy(plan)
    varied["features"] += [
        {"op": "sketch.rect", "id": "__sec_sk", "w": 600, "h": 300, "plane": "XY"},
        {"op": "extrude", "id": "__sec_solid", "profile": "__sec_sk", "height": 600},
        {"op": "translate", "id": "__sec_pos", "target": "__sec_solid", "y": 150},
        {"op": "boolean", "id": "__sec", "kind": "cut", "a": result_id, "b": "__sec_pos"},
    ]
    varied["result"] = "__sec"
    varied["checks"] = []
    varied.pop("parts", None)
    return varied


def build_views(plan: dict, run_dir: pathlib.Path) -> list[tuple[str, str]]:
    """Render the assembly, a section, and every named component."""
    views: list[tuple[str, str]] = []

    def shoot(label: str, variant: dict) -> None:
        res = post("/plan", {"plan": variant})
        if not res.get("ok"):
            print(f"    view {label}: FAILED {str(res.get('error'))[:70]}")
            return
        stl = (res.get("outputs") or {}).get("stl", {}).get("path")
        if not stl:
            return
        png = str(run_dir / f"view_{label}.png")
        if render_stl(stl, png):
            views.append((label, png))
            print(f"    view {label}: ok  ({res['stats']['solids']} solids)")

    base = copy.deepcopy(plan)
    base["checks"] = []
    shoot("assembly", base)

    sec = section_variant(plan)
    if sec:
        shoot("section_y0", sec)

    for name, fid in (plan.get("parts") or {}).items():
        only = copy.deepcopy(plan)
        only["result"] = fid
        only["checks"] = []
        only.pop("parts", None)
        shoot(f"part_{name}"[:40], only)

    return views


# ------------------------------------------------------------------- patch
def apply_patch(plan: dict, patch: dict) -> tuple[dict, list[str]]:
    """Apply a small patch deterministically. Returns (new_plan, notes)."""
    out = copy.deepcopy(plan)
    notes: list[str] = []

    for name, value in (patch.get("set_parameters") or {}).items():
        hit = False
        for p in out.get("parameters", []):
            if p["name"] == name:
                p["value"] = value
                hit = True
                break
        notes.append(f"set parameter {name} = {value}" if hit else f"UNKNOWN parameter {name}")

    for fid in patch.get("remove_features") or []:
        before = len(out["features"])
        out["features"] = [f for f in out["features"] if f.get("id") != fid]
        notes.append(f"removed {fid}" if len(out["features"]) < before else f"nothing to remove: {fid}")

    for feat in patch.get("set_features") or []:
        if not isinstance(feat, dict) or "id" not in feat:
            notes.append("skipped a set_feature without an id")
            continue
        for i, existing in enumerate(out["features"]):
            if existing.get("id") == feat["id"]:
                out["features"][i] = feat
                notes.append(f"replaced feature {feat['id']}")
                break
        else:
            out["features"].append(feat)
            notes.append(f"appended feature {feat['id']}")

    for feat in patch.get("append_features") or []:
        if isinstance(feat, dict) and "id" in feat:
            out["features"].append(feat)
            notes.append(f"appended feature {feat['id']}")

    return out, notes


def extract_json(text: str) -> dict | None:
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    for blob in fenced + [text]:
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
                            if isinstance(obj, dict) and "differences" in obj:
                                return obj
                        except json.JSONDecodeError:
                            pass
                        break
            start = blob.find("{", start + 1)
    return None


# -------------------------------------------------------------------- model
def call_model(api_key: str, model: str, content: list, timeout: int = 900) -> tuple[str, dict]:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": content},
        ],
        # Small output now, because we ask for a patch rather than the whole plan.
        "max_tokens": 8000,
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
    had = bool(text.strip())
    if not had:
        text = msg.get("reasoning") or ""
    meta = {
        "finish": choice.get("finish_reason"),
        "had_content": had,
        "reasoning_chars": len(msg.get("reasoning") or ""),
        "tokens": (data.get("usage") or {}).get("completion_tokens"),
    }
    return text, meta


# --------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--reference")
    ap.add_argument("--brief", default="")
    ap.add_argument("--model", default="gemma4:31b")
    ap.add_argument("--iters", type=int, default=3)
    ap.add_argument("--out", default="vision_runs")
    ap.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""))
    ap.add_argument("--views", default="assembly,section,parts")
    args = ap.parse_args()

    if not args.api_key:
        print("no API key: pass --api-key or set OPENAI_API_KEY")
        return 2

    wanted = {v.strip() for v in args.views.split(",") if v.strip()}
    run_dir = pathlib.Path(HERE) / args.out / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    plan = json.loads(pathlib.Path(args.plan).read_text(encoding="utf-8"))
    ref_b64 = None
    if args.reference:
        ref_b64 = encode_image(args.reference)
        print("reference %s" % args.reference)
    print("model     %s   iters %d   views %s   run %s\n"
          % (args.model, args.iters, sorted(wanted), run_dir.name))

    applied: list[str] = []
    history = []
    carry_error = ""
    seen: dict[str, int] = {}
    last_touched: set[str] = set()

    for it in range(1, args.iters + 1):
        print("=" * 70)
        print("ITERATION %d" % it)
        print("=" * 70)
        (run_dir / f"plan_{it:02d}.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")

        if applied:
            print("  applied  " + "; ".join(applied))
        applied = []

        print("  building views...")
        views = build_views(plan, run_dir)
        if not views:
            print("  no views rendered -- stopping")
            break

        content: list = []
        text = []
        if args.brief:
            text.append("REQUIREMENTS (the source brief):\n" + args.brief)
        text.append("CURRENT PLAN (send a patch, do not restate this):\n" + json.dumps(plan))
        if carry_error:
            text.append(
                "Your PREVIOUS patch was rejected and rolled back. Fix this:\n" + carry_error
            )
        text.append(
            "Compare the SOURCE against EVERY render view below and reply with the json "
            "object described: differences plus a patch."
        )
        content.append({"type": "text", "text": "\n\n".join(text)})
        if ref_b64:
            content.append({"type": "text", "text": "SOURCE image:"})
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + ref_b64}})
        for label, png in views:
            content.append({"type": "text", "text": f"RENDER view: {label}"})
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + encode_image(png)}})

        try:
            raw, meta = call_model(args.api_key, args.model, content)
        except Exception as exc:
            print("  model call failed: %s" % str(exc)[:200])
            break

        print("  model    finish=%s content=%s reasoning=%sch tokens=%s"
              % (meta["finish"], meta["had_content"], meta["reasoning_chars"], meta["tokens"]))
        (run_dir / f"model_{it:02d}.txt").write_text(raw, encoding="utf-8")

        parsed = extract_json(raw)
        if parsed is None:
            print("  could not parse a patch from the reply (saved to model_%02d.txt)" % it)
            break

        diffs = parsed.get("differences") or []
        patch = parsed.get("patch") or {}
        print("\n  DIFFERENCES (%d):" % len(diffs))
        for d in diffs:
            print("    - %s" % d)
        history.append({"iteration": it, "differences": diffs})

        # OSCILLATION GUARD. A critic with no ground truth for "front" can report
        # the same orientation problem forever while the geometry flips back and
        # forth. If every difference this pass was already reported, we are in a
        # loop, and another patch will not get us out of it.
        def norm(text: str) -> str:
            return re.sub(r"[^a-z0-9 ]", "", text.lower())[:90]

        fresh = [d for d in diffs if seen.get(norm(d), 0) == 0]
        if diffs and not fresh:
            print("\n  OSCILLATION: every difference repeats a previous pass.")
            print("  Stopping rather than looping. Resolve the ambiguity in the brief")
            print("  (name the axis explicitly) or in the plan, then re-run.")
            break
        for d in diffs:
            seen[norm(d)] = seen.get(norm(d), 0) + 1

        if not diffs and not any(patch.values()):
            print("\n  converged: no differences left.")
            break

        plan_candidate, notes = apply_patch(plan, patch)
        applied = notes
        if not notes:
            print("\n  patch was empty -- nothing to apply, stopping.")
            break

        # A patch that rewrites features it already rewrote is a second signal of
        # oscillation, even when the wording of the differences changed.
        touched = {f.get("id") for f in (patch.get("set_features") or []) if isinstance(f, dict)}
        touched |= set(patch.get("remove_features") or [])
        touched |= {f.get("id") for f in (patch.get("append_features") or []) if isinstance(f, dict)}
        overlap = touched & last_touched
        if overlap:
            print("  note: this patch touches features already changed last pass: %s"
                  % ", ".join(sorted(x for x in overlap if x)))
        last_touched = touched

        # GATE: a revision must still compile AND keep every fit check passing.
        # The model may change geometry; it may not silently break the thing the
        # checks exist to protect. A rejected patch is reverted and its error is
        # handed back on the next pass.
        verdict = post("/plan", {"plan": plan_candidate})
        if not verdict.get("ok"):
            carry_error = str(verdict.get("error") or verdict.get("validation_errors"))[:400]
            print("\n  REJECTED -- the patch does not compile: %s" % carry_error)
            print("  reverted; this error goes back to the model next pass")
            applied = []
            continue
        broken = [c for c in (verdict.get("checks") or []) if c.get("pass") is False]
        if broken:
            carry_error = "; ".join(
                "%s(%s,%s) = %.4f, expected %s"
                % (c["kind"], c["a"], c["b"], c["value"], c.get("expect"))
                for c in broken
            )
            print("\n  REJECTED -- the patch broke %d fit check(s): %s" % (len(broken), carry_error))
            print("  reverted; this goes back to the model next pass")
            applied = []
            continue

        plan = plan_candidate
        carry_error = ""
        print("  accepted patch: compiles, %d fit check(s) still pass" % len(verdict.get("checks") or []))
        (run_dir / f"revised_{it:02d}.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
        print()

    (run_dir / "summary.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    print("run dir: %s" % run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
