"""Local B-Rep build service for the CADAM x cadgen fusion.

Phase 0: accepts a build123d script, executes it in an isolated subprocess, and
returns STEP + STL + GLB plus the solid's stats (bbox, volume, `is_valid`,
solid/face counts). Stats matter as much as the files: the caller must be able
to tell an invalid solid from a good one, which is the bug class that produced
the original multi-shell, non-watertight V2.

Endpoints
    GET  /health   -> interpreter + kernel versions
    POST /build    -> { python, timeout? } -> base64 STEP/STL/GLB + stats + logs

Binding is 127.0.0.1 only. This executes model scripts; it must never be
reachable off the machine.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
import uuid

from fastapi import FastAPI
from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "runner.py")
ARTIFACTS = os.path.join(HERE, "artifacts")
MARKER = "__BREP_RESULT__"

DEFAULT_TIMEOUT_S = 120
MAX_CODE_BYTES = 256 * 1024

app = FastAPI(title="brep-build-service", version="0.1.0")


class BuildRequest(BaseModel):
    python: str = Field(..., description="build123d script defining RESULT")
    timeout: int | None = Field(None, ge=1, le=600)
    keep: bool = Field(True, description="keep artifacts on disk for the viewer")


class BuildResponse(BaseModel):
    ok: bool
    ms: int
    error: str | None = None
    traceback: str | None = None
    stats: dict | None = None
    stdout: str = ""
    outputs: dict = {}
    files: dict = {}
    artifact_dir: str | None = None
    timed_out: bool = False


def _parse_runner_output(text: str) -> dict | None:
    """The runner's last marker line is the payload; everything else is noise."""
    for line in reversed(text.splitlines()):
        if line.startswith(MARKER):
            try:
                return json.loads(line[len(MARKER):])
            except json.JSONDecodeError:
                return None
    return None


@app.get("/health")
def health() -> dict:
    import build123d  # imported here so /health reports the versions actually loaded

    return {
        "ok": True,
        "service": "brep-build-service",
        "phase": 0,
        "python": sys.version.split()[0],
        "build123d": build123d.__version__,
        "artifacts_dir": ARTIFACTS,
    }


@app.post("/build", response_model=BuildResponse)
def build(req: BuildRequest) -> BuildResponse:
    code = req.python or ""
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        return BuildResponse(ok=False, ms=0, error="script too large")

    build_id = uuid.uuid4().hex[:12]
    workdir = os.path.join(ARTIFACTS, build_id)
    os.makedirs(workdir, exist_ok=True)
    code_path = os.path.join(workdir, "user_code.py")

    with open(code_path, "w", encoding="utf-8") as fh:
        fh.write(code)

    timeout = req.timeout or DEFAULT_TIMEOUT_S
    started = time.time()
    timed_out = False

    try:
        proc = subprocess.run(
            [sys.executable, RUNNER, code_path, workdir],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workdir,
        )
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + f"\n[service] killed after {timeout}s"
        stdout = stdout if isinstance(stdout, str) else stdout.decode("utf-8", "replace")
        stderr = stderr if isinstance(stderr, str) else stderr.decode("utf-8", "replace")

    elapsed = int((time.time() - started) * 1000)
    parsed = _parse_runner_output(stdout)

    if parsed is None:
        tail = (stderr or stdout or "").strip().splitlines()[-8:]
        return BuildResponse(
            ok=False,
            ms=elapsed,
            timed_out=timed_out,
            error="build failed before producing a result"
            + (" (timeout)" if timed_out else ""),
            traceback="\n".join(tail),
            artifact_dir=workdir if req.keep else None,
        )

    # Ship the files inline for the client; also report their on-disk paths so the
    # viewer can serve them without a second round trip.
    files: dict[str, str] = {}
    for kind, info in (parsed.get("outputs") or {}).items():
        path = info.get("path")
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as fh:
                files[kind] = base64.b64encode(fh.read()).decode("ascii")
        except OSError:
            pass

    if not req.keep:
        shutil.rmtree(workdir, ignore_errors=True)
        artifact_dir = None
    else:
        artifact_dir = workdir

    return BuildResponse(
        ok=bool(parsed.get("ok")),
        ms=int(parsed.get("ms", elapsed)),
        error=parsed.get("error"),
        traceback=parsed.get("traceback"),
        stats=parsed.get("stats"),
        stdout=parsed.get("stdout", ""),
        outputs=parsed.get("outputs", {}),
        files=files,
        artifact_dir=artifact_dir,
        timed_out=timed_out,
    )
