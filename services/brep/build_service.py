"""Local B-Rep build service for the CADAM x cadgen fusion.

    GET  /health    -> interpreter + kernel versions
    GET  /ops       -> the ModelPlan operation vocabulary
    POST /validate  -> validate a ModelPlan, no compile
    POST /plan      -> validate + compile a ModelPlan -> STEP/STL/GLB + checks
    POST /build     -> execute a raw build123d script (Phase 0 / advanced path)

Why three ways in:

* `/plan` is the primary door. The model emits a PLAN, never code, so the
  service does not execute arbitrary Python and bad input is rejected
  structurally, before the kernel is asked to do anything expensive.
* `/validate` exists so the caller can gate a plan cheaply.
* `/build` runs authored Python. It is the Phase 0 contract and the future
  "advanced" escape hatch for shapes the IR cannot express.

`stats` are as important as the files: the caller must be able to distinguish
an invalid solid from a good one -- the exact bug class that produced the
original multi-shell, non-watertight V2.

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
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "runner.py")
ARTIFACTS = os.path.join(HERE, "artifacts")
MARKER = "__BREP_RESULT__"

DEFAULT_TIMEOUT_S = 120
MAX_SOURCE_BYTES = 512 * 1024

app = FastAPI(title="brep-build-service", version="0.2.0")


class BuildRequest(BaseModel):
    python: str = Field(..., description="build123d script defining RESULT")
    timeout: int | None = Field(None, ge=1, le=600)
    keep: bool = True


class PlanRequest(BaseModel):
    plan: dict[str, Any] = Field(..., description="a ModelPlan object")
    timeout: int | None = Field(None, ge=1, le=600)
    keep: bool = True


class ServiceResponse(BaseModel):
    ok: bool
    ms: int = 0
    mode: str | None = None
    error: str | None = None
    traceback: str | None = None
    validation_errors: list[dict] | None = None
    stats: dict | None = None
    checks: list[dict] | None = None
    symbols: dict | None = None
    result_id: str | None = None
    stdout: str = ""
    outputs: dict = {}
    files: dict = {}
    artifact_dir: str | None = None
    timed_out: bool = False


def _parse_runner_output(text: str) -> dict | None:
    for line in reversed(text.splitlines()):
        if line.startswith(MARKER):
            try:
                return json.loads(line[len(MARKER):])
            except json.JSONDecodeError:
                return None
    return None


def _validation_errors(raw: dict[str, Any]) -> list[dict] | None:
    """Validate against ModelPlan. Returns None when the plan is good."""
    from plan_schema import ModelPlan  # noqa: PLC0415

    try:
        ModelPlan.model_validate(raw)
        return None
    except Exception as exc:  # pydantic.ValidationError
        errors = getattr(exc, "errors", None)
        if not callable(errors):
            return [{"loc": [], "msg": str(exc)}]
        out = []
        for err in errors():
            loc = [str(p) for p in err.get("loc", ())]
            out.append(
                {
                    "loc": loc,
                    "path": ".".join(loc),
                    "type": err.get("type"),
                    "msg": err.get("msg"),
                    "input": err.get("input") if isinstance(err.get("input"), (int, float, str, bool, type(None))) else None,
                }
            )
        return out


def _run(mode: str, src_path: str, workdir: str, timeout: int) -> tuple[dict | None, str, bool]:
    """Run the isolated runner. Returns (payload, stderr, timed_out)."""
    try:
        proc = subprocess.run(
            [sys.executable, RUNNER, mode, src_path, workdir],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workdir,
        )
        return _parse_runner_output(proc.stdout), proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr or ""
        stdout = exc.stdout or ""
        for value in (stderr, stdout):
            if isinstance(value, bytes):
                value.decode("utf-8", "replace")
        stderr = stderr if isinstance(stderr, str) else stderr.decode("utf-8", "replace")
        stdout = stdout if isinstance(stdout, str) else stdout.decode("utf-8", "replace")
        return _parse_runner_output(stdout), stderr + f"\n[service] killed after {timeout}s", True


def _respond(
    payload: dict | None,
    stderr: str,
    timed_out: bool,
    workdir: str,
    keep: bool,
    mode: str,
    elapsed: int,
) -> ServiceResponse:
    if payload is None:
        tail = (stderr or "").strip().splitlines()[-8:]
        resp = ServiceResponse(
            ok=False,
            ms=elapsed,
            mode=mode,
            timed_out=timed_out,
            error="build failed before producing a result" + (" (timeout)" if timed_out else ""),
            traceback="\n".join(tail),
        )
    else:
        files: dict[str, str] = {}
        for kind, info in (payload.get("outputs") or {}).items():
            path = info.get("path")
            if not path or not os.path.exists(path):
                continue
            try:
                with open(path, "rb") as fh:
                    files[kind] = base64.b64encode(fh.read()).decode("ascii")
            except OSError:
                pass
        extra = payload.get("extra") or {}
        resp = ServiceResponse(
            ok=bool(payload.get("ok")),
            ms=int(payload.get("ms", elapsed)),
            mode=mode,
            error=payload.get("error"),
            traceback=payload.get("traceback"),
            stats=payload.get("stats"),
            checks=extra.get("checks"),
            symbols=extra.get("symbols"),
            result_id=extra.get("result_id"),
            stdout=payload.get("stdout", ""),
            outputs=payload.get("outputs", {}),
            files=files,
            timed_out=timed_out,
        )

    if keep:
        resp.artifact_dir = workdir
    else:
        shutil.rmtree(workdir, ignore_errors=True)
    return resp


def _workdir() -> str:
    workdir = os.path.join(ARTIFACTS, uuid.uuid4().hex[:12])
    os.makedirs(workdir, exist_ok=True)
    return workdir


@app.get("/health")
def health() -> dict:
    import build123d  # noqa: PLC0415

    return {
        "ok": True,
        "service": "brep-build-service",
        "phase": 1,
        "python": sys.version.split()[0],
        "build123d": build123d.__version__,
        "artifacts_dir": ARTIFACTS,
        "modes": ["plan", "build", "validate"],
    }


@app.get("/ops")
def ops() -> dict:
    from plan_schema import ModelPlan, op_names  # noqa: PLC0415

    return {"ops": op_names(), "plan_schema": ModelPlan.model_json_schema()}


@app.post("/validate", response_model=ServiceResponse)
def validate(req: PlanRequest) -> ServiceResponse:
    errors = _validation_errors(req.plan)
    return ServiceResponse(
        ok=errors is None,
        mode="validate",
        validation_errors=errors,
        error=None if errors is None else f"{len(errors)} validation error(s)",
    )


@app.post("/plan", response_model=ServiceResponse)
def plan_endpoint(req: PlanRequest) -> ServiceResponse:
    errors = _validation_errors(req.plan)
    if errors is not None:
        return ServiceResponse(
            ok=False,
            mode="plan",
            validation_errors=errors,
            error=f"plan rejected: {len(errors)} validation error(s)",
        )

    workdir = _workdir()
    plan_path = os.path.join(workdir, "plan.json")
    with open(plan_path, "w", encoding="utf-8") as fh:
        json.dump(req.plan, fh)

    timeout = req.timeout or DEFAULT_TIMEOUT_S
    started = time.time()
    payload, stderr, timed_out = _run("--plan", plan_path, workdir, timeout)
    return _respond(
        payload, stderr, timed_out, workdir, req.keep, "plan", int((time.time() - started) * 1000)
    )


@app.post("/build", response_model=ServiceResponse)
def build(req: BuildRequest) -> ServiceResponse:
    code = req.python or ""
    if len(code.encode("utf-8")) > MAX_SOURCE_BYTES:
        return ServiceResponse(ok=False, mode="build", error="script too large")

    workdir = _workdir()
    code_path = os.path.join(workdir, "user_code.py")
    with open(code_path, "w", encoding="utf-8") as fh:
        fh.write(code)

    timeout = req.timeout or DEFAULT_TIMEOUT_S
    started = time.time()
    payload, stderr, timed_out = _run("--code", code_path, workdir, timeout)
    return _respond(
        payload, stderr, timed_out, workdir, req.keep, "build", int((time.time() - started) * 1000)
    )
