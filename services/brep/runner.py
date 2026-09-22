"""Execute a build123d script and export its RESULT.

Deliberately run as a SUBPROCESS so that a syntax error, a kernel crash, an
infinite loop or an OOM kills only this process -- never the service.

Usage:  python runner.py <user_code.py> <outdir>

Protocol: the last stdout line is  __BREP_RESULT__ {json}
Everything the user's script prints goes into the payload as `stdout`, so a
script can report its own measurements back to the caller.
"""
import contextlib
import io
import json
import os
import sys
import time
import traceback

MARKER = "__BREP_RESULT__"


def emit(payload: dict) -> None:
    sys.stdout.write(MARKER + json.dumps(payload) + "\n")
    sys.stdout.flush()


def export_all(part, log):
    """Export each format independently: one failing format must not lose the others."""
    from build123d import export_step, export_stl, export_gltf

    written = {}
    for name, fn, args in (
        ("step", export_step, {}),
        ("stl", export_stl, {"tolerance": 0.02}),
        ("glb", export_gltf, {}),
    ):
        path = f"model.{name}"
        try:
            # NOTE: redirect_stdout, not `with log:` -- the latter CLOSES the
            # buffer on exit and every later log.getvalue() raises.
            with contextlib.redirect_stdout(log):
                fn(part, path, **args)
            if os.path.exists(path):
                written[name] = {"path": os.path.abspath(path), "bytes": os.path.getsize(path)}
        except Exception as exc:  # noqa: BLE001 - report, don't abort the build
            log.write(f"[export {name} failed] {type(exc).__name__}: {exc}\n")
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
    try:
        out["is_valid"] = bool(part.is_valid)
    except Exception:
        pass
    try:
        out["volume"] = float(part.volume)
    except Exception:
        pass
    try:
        out["solids"] = len(part.solids())
    except Exception:
        pass
    try:
        out["faces"] = len(part.faces())
    except Exception:
        pass
    return out


def main() -> None:
    if len(sys.argv) < 3:
        emit({"ok": False, "error": "usage: runner.py <code.py> <outdir>"})
        return

    code_path, outdir = sys.argv[1], sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    os.chdir(outdir)

    started = time.time()
    log = io.StringIO()
    payload = {"ok": False, "error": None, "stats": None, "outputs": {}, "stdout": ""}

    try:
        with open(code_path, "r", encoding="utf-8") as fh:
            src = fh.read()

        ns = {"__name__": "__main__", "__file__": os.path.abspath(code_path)}
        with contextlib.redirect_stdout(log):
            exec(compile(src, code_path, "exec"), ns)

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

        payload["stats"] = stats_for(part)
        payload["outputs"] = export_all(part, log)
        payload["ok"] = True
    except Exception as exc:  # noqa: BLE001 - everything is reported to the caller
        payload["error"] = f"{type(exc).__name__}: {exc}"
        payload["traceback"] = traceback.format_exc()

    payload["stdout"] = log.getvalue()
    payload["ms"] = int((time.time() - started) * 1000)
    emit(payload)


main()
