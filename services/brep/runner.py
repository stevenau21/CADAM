"""Execute a model in an isolated subprocess and export the result.

Deliberately a SUBPROCESS so that a syntax error, a kernel crash, an infinite
loop or an OOM kills only this process -- never the service.

Usage:
    python runner.py --code <user_code.py> <outdir>
    python runner.py --plan <plan.json>    <outdir>

Protocol: the last stdout line is  __BREP_RESULT__ {json}
Everything the model printed goes back as `stdout`, so a script can report its
own measurements to the caller.

Code mode executes a build123d script (Phase 0, and the future "advanced"
escape hatch). Plan mode executes a validated ModelPlan through the compiler
(Phase 1) -- it never executes authored Python.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import traceback

MARKER = "__BREP_RESULT__"

# cadgen renders a mesh to a PNG. It lives outside this repo, so allow override.
CADGEN = os.environ.get(
    "CADGEN_EXE", "F:/projects/3D/text-to-cad/.venv/Scripts/cadgen.exe"
)


def emit(payload: dict) -> None:
    sys.stdout.write(MARKER + json.dumps(payload) + "\n")
    sys.stdout.flush()


def render_view(outputs: dict, log) -> None:
    """Render the mesh to a PNG and add it to the outputs.

    This is what closes the vision loop: the caller feeds this image back to the
    model so it looks at the geometry it just produced instead of being asked to
    trust it. A missing renderer is not an error -- the build still succeeds.
    """
    stl = (outputs.get("stl") or {}).get("path")
    if not stl or not os.path.exists(stl) or not os.path.exists(CADGEN):
        return
    out = os.path.abspath("view.png")
    try:
        proc = subprocess.run(
            [CADGEN, "stl", "snapshot", stl, out],
            capture_output=True,
            timeout=420,
        )
        if proc.returncode == 0 and os.path.exists(out):
            outputs["render"] = {"path": out, "bytes": os.path.getsize(out)}
        else:
            log.write(f"[render skipped] cadgen exited {proc.returncode}\n")
    except Exception as exc:  # noqa: BLE001 - a render is a bonus, never fatal
        log.write(f"[render failed] {type(exc).__name__}: {exc}\n")


def write_step(part, path) -> str:
    """Write STEP, preferring build123d and falling back to raw OCCT.

    build123d's exporter is strict, and can refuse a shape that OCCT itself will
    write without complaint — notably a solid that was IMPORTED from someone
    else's STEP (image-to-CAD output, a supplier's part). The geometry is fine;
    the convenience wrapper is what objects. Observed concretely: a shape from
    GenCAD renders, meshes to STL, and round-trips through OCCT's writer, while
    build123d's export_step raises 'Failed to write STEP file'.
    """
    try:
        from build123d import export_step  # noqa: PLC0415

        export_step(part, path)
        if os.path.exists(path):
            return "build123d"
    except Exception:  # noqa: BLE001 - fall through to the OCCT writer
        pass

    from OCP.Interface import Interface_Static  # noqa: PLC0415
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer  # noqa: PLC0415

    Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
    writer = STEPControl_Writer()
    writer.Transfer(part.wrapped, STEPControl_AsIs)
    writer.Write(path)
    if not os.path.exists(path):
        raise RuntimeError("STEP write failed with both build123d and OCCT")
    return "occt"


def export_all(part, log):
    """Export each format independently: one failing format must not lose the others."""
    from build123d import export_step, export_stl, export_gltf

    written = {}
    for name, args in (("stl", {"tolerance": 0.02}), ("glb", {})):
        from build123d import export_gltf, export_stl  # noqa: PLC0415

        fn = export_stl if name == "stl" else export_gltf
        path = f"model.{name}"
        try:
            # NOTE: contextlib.redirect_stdout, not `with log:` -- the latter
            # CLOSES the buffer, and every later log.getvalue() then raises.
            with contextlib.redirect_stdout(log):
                fn(part, path, **args)
            if os.path.exists(path) and os.path.getsize(path) > 0:
                written[name] = {
                    "path": os.path.abspath(path),
                    "bytes": os.path.getsize(path),
                }
            elif os.path.exists(path):
                log.write(f"[export {name} empty] build123d wrote no geometry\n")
        except Exception as exc:  # noqa: BLE001 - report, don't abort the build
            log.write(f"[export {name} failed] {type(exc).__name__}: {exc}\n")

    # STEP last, with the two-writer fallback.
    try:
        with contextlib.redirect_stdout(log):
            writer_used = write_step(part, "model.step")
        if os.path.exists("model.step"):
            written["step"] = {
                "path": os.path.abspath("model.step"),
                "bytes": os.path.getsize("model.step"),
            }
            if writer_used == "occt":
                log.write("[export step] build123d refused it; wrote via OCCT\n")
    except Exception as exc:  # noqa: BLE001 - report, don't abort the build
        log.write(f"[export step failed] {type(exc).__name__}: {exc}\n")
    return written


def stats_for(part):
    bb = part.bounding_box()
    out = {
        "bbox": [bb.size.X, bb.size.Y, bb.size.Z],
        "bbox_min": [bb.min.X, bb.min.Y, bb.min.Z],
        "bbox_max": [bb.max.X, bb.max.Y, bb.max.Z],
        "is_valid": None,
        "volume": None,
        "solids": None,
        "faces": None,
    }
    for key, fn in (
        ("is_valid", lambda: bool(part.is_valid)),
        ("volume", lambda: float(part.volume)),
        ("solids", lambda: len(part.solids())),
        ("faces", lambda: len(part.faces())),
    ):
        try:
            out[key] = fn()
        except Exception:  # noqa: BLE001 - stats are best-effort
            pass
    return out


def run_code(path, log):
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    ns = {"__name__": "__main__", "__file__": os.path.abspath(path)}
    with contextlib.redirect_stdout(log):
        exec(compile(src, path, "exec"), ns)

    part = ns.get("RESULT")
    if part is None:
        for alias in ("result", "part", "model"):
            if alias in ns:
                part = ns[alias]
                break
    if part is None:
        raise RuntimeError(
            "the script did not define RESULT (a build123d Part/Solid/Compound)"
        )
    return part, {}, {}


def run_plan(path, log):
    from plan_schema import ModelPlan  # noqa: PLC0415 - subprocess-local import
    from compiler import compile_plan  # noqa: PLC0415
    from build123d import export_gltf  # noqa: PLC0415

    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    plan = ModelPlan.model_validate(raw)
    with contextlib.redirect_stdout(log):
        compiled = compile_plan(plan.model_dump())

    checks = compiled["checks"]
    if checks:
        with contextlib.redirect_stdout(log):
            for c in checks:
                line = "CHECK %s %s<->%s value=%.6f" % (c["kind"], c["a"], c["b"], c["value"])
                if "expect" in c:
                    line += " expect=%.6f tol=%.6f %s" % (
                        c["expect"],
                        c["tolerance"],
                        "PASS" if c["pass"] else "FAIL",
                    )
                print(line)

    # Per-part export, the way Ragnar exports "whole model or part by part".
    # Each component gets its own STEP (real geometry, for CAD) AND its own GLB
    # (for the viewer, where each can be shown or hidden independently).
    parts = {}
    if plan.parts:
        os.makedirs("parts", exist_ok=True)
        with contextlib.redirect_stdout(log):
            for name, fid in plan.parts.items():
                if fid not in compiled["shapes"]:
                    raise RuntimeError(f"parts[{name!r}]={fid!r} does not name a solid feature")
                shape = compiled["shapes"][fid]
                entry = {}
                for kind, out_name in (
                    ("step", f"parts/{name}.step"),
                    ("glb", f"parts/{name}.glb"),
                ):
                    try:
                        if kind == "step":
                            write_step(shape, out_name)
                        else:
                            export_gltf(shape, out_name)
                        # build123d's GLTF exporter silently emits an empty scene
                        # (a ~220 byte header) for a shape it cannot mesh -- most
                        # often a solid IMPORTED from someone else's STEP. Treat
                        # that as "no GLB" so the viewer falls back to the render
                        # instead of showing a blank canvas.
                        if (
                            os.path.exists(out_name)
                            and os.path.getsize(out_name) > 512
                        ):
                            entry[kind] = {
                                "path": os.path.abspath(out_name),
                                "bytes": os.path.getsize(out_name),
                            }
                        elif os.path.exists(out_name):
                            log.write(f"[part {name} {kind} empty] no geometry written\n")
                    except Exception as exc:  # noqa: BLE001 - one format failing is not fatal
                        log.write(f"[part {name} {kind} failed] {type(exc).__name__}: {exc}\n")
                parts[name] = entry
                print(f"PART {name}  <- {fid}")

    symbols = {k: round(v, 6) for k, v in compiled["symbols"].items()}
    extra = {
        "checks": checks,
        "symbols": symbols,
        "result_id": compiled["result_id"],
        "parts": parts,
    }
    return compiled["result"], extra, symbols


def main() -> None:
    argv = sys.argv[1:]
    if len(argv) != 3 or argv[0] not in ("--code", "--plan"):
        emit({"ok": False, "error": "usage: runner.py --code|--plan <file> <outdir>"})
        return

    mode, src_path, outdir = argv
    os.makedirs(outdir, exist_ok=True)
    os.chdir(outdir)

    started = time.time()
    log = io.StringIO()
    payload = {
        "ok": False,
        "error": None,
        "stats": None,
        "outputs": {},
        "stdout": "",
        "extra": {},
    }

    try:
        if mode == "--plan":
            part, extra, _ = run_plan(src_path, log)
        else:
            part, extra, _ = run_code(src_path, log)
        payload["extra"] = extra
        payload["stats"] = stats_for(part)
        payload["outputs"] = export_all(part, log)
        render_view(payload["outputs"], log)
        # fold the per-part files into outputs so the service ships them too
        for name, entry in (extra.get("parts") or {}).items():
            for kind, info in entry.items():
                payload["outputs"][f"{kind}:{name}"] = info
        payload["ok"] = True
    except Exception as exc:  # noqa: BLE001 - everything is reported to the caller
        payload["error"] = f"{type(exc).__name__}: {exc}"
        payload["traceback"] = traceback.format_exc()

    payload["stdout"] = log.getvalue()
    payload["ms"] = int((time.time() - started) * 1000)
    emit(payload)


main()
