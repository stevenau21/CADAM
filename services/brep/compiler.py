"""ModelPlan -> build123d. Deterministic, and safe.

Two jobs:

1. `safe_eval` -- a whitelisted expression evaluator. Plans need real
   arithmetic ("height": "body_h - neck_z0") so mating dimensions can be
   DERIVED rather than duplicated. It parses with `ast` and walks only node
   types it whitelists: no attribute access, no subscripts, no lambdas, no
   comprehensions, no calls except a small math set. Arbitrary code execution
   is structurally impossible, not merely discouraged.

2. `compile_plan` -- walks the feature list, building each shape and recording
   it under its id, then evaluates the plan's fit CHECKS. A bad plan raises
   `PlanError` naming the feature and field, before the kernel is asked to do
   anything expensive.
"""
from __future__ import annotations

import ast
import math
import operator
from typing import Any

from build123d import (
    Axis,
    Circle,
    Cylinder,
    GeomType,
    Plane,
    Pos,
    RectangleRounded,
    chamfer,
    extrude,
    fillet,
    offset,
)

EPS = 1e-4


class PlanError(Exception):
    """A structural error the author (or the model) can fix."""


# --------------------------------------------------------------------- eval
_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.FloorDiv: operator.floordiv,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "floor": math.floor,
    "ceil": math.ceil,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "radians": math.radians,
    "degrees": math.degrees,
    "pi": lambda: math.pi,
}


def safe_eval(expr: str, names: dict[str, float]) -> float:
    """Evaluate a whitelisted arithmetic expression. Never executes code."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PlanError(f"cannot parse expression {expr!r}: {exc.msg}") from exc

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise PlanError(f"expression {expr!r}: unsupported constant {node.value!r}")
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id in names:
                return float(names[node.id])
            known = ", ".join(sorted(names)) or "(none)"
            raise PlanError(f"expression {expr!r}: unknown name {node.id!r}. known: {known}")
        if isinstance(node, ast.BinOp):
            fn = _BINOPS.get(type(node.op))
            if fn is None:
                raise PlanError(f"expression {expr!r}: operator not allowed")
            return float(fn(ev(node.left), ev(node.right)))
        if isinstance(node, ast.UnaryOp):
            fn = _UNARY.get(type(node.op))
            if fn is None:
                raise PlanError(f"expression {expr!r}: unary operator not allowed")
            return float(fn(ev(node.operand)))
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
                raise PlanError(f"expression {expr!r}: function not allowed")
            if node.keywords:
                raise PlanError(f"expression {expr!r}: keyword arguments not allowed")
            return float(_FUNCS[node.func.id](*[ev(a) for a in node.args]))
        raise PlanError(f"expression {expr!r}: {type(node).__name__} not allowed")

    return ev(tree)


# ---------------------------------------------------------------- selectors
def _edge_z_span(e) -> float:
    bb = e.bounding_box()
    return bb.max.Z - bb.min.Z


def select_edges(shape, which: str):
    edges = shape.edges()
    if which == "all":
        return edges
    if which == "vertical":
        return [e for e in edges if abs(e.tangent_at(0).Z) > 0.99]
    if which == "horizontal":
        return [e for e in edges if _edge_z_span(e) < 1e-6]
    if which == "circular":
        return [e for e in edges if e.geom_type == GeomType.CIRCLE]
    bb = shape.bounding_box()
    if which == "z_max":
        return [e for e in edges if abs(e.bounding_box().max.Z - bb.max.Z) < EPS]
    if which == "z_min":
        return [e for e in edges if abs(e.bounding_box().min.Z - bb.min.Z) < EPS]
    raise PlanError(f"unknown edge selector {which!r}")


def select_faces(shape, which: str):
    if which == "none":
        return None
    faces = shape.faces()
    bb = shape.bounding_box()
    if which == "z_max":
        return [f for f in faces if abs(f.bounding_box().max.Z - bb.max.Z) < EPS]
    if which == "z_min":
        return [f for f in faces if abs(f.bounding_box().min.Z - bb.min.Z) < EPS]
    raise PlanError(f"unknown face selector {which!r}")


# ------------------------------------------------------------------ compile
def compile_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Execute a validated ModelPlan. Returns result shape, stats and checks."""
    env: dict[str, float] = {}

    for param in plan.get("parameters", []):
        name = param["name"]
        if name in env:
            raise PlanError(f"parameter {name!r} declared twice")
        env[name] = _num(param["value"], env, f"parameter {name}")

    for d in plan.get("derived", []):
        name = d["name"]
        if name in env:
            raise PlanError(f"symbol {name!r} declared twice")
        env[name] = safe_eval(d["expr"], env)

    shapes: dict[str, Any] = {}
    profiles: dict[str, Any] = {}

    for index, feat in enumerate(plan["features"]):
        op = feat["op"]
        fid = feat["id"]
        if fid in shapes or fid in profiles:
            raise PlanError(f"feature {index} ({op}): id {fid!r} already defined")
        where = f"feature {index} ({op}, id={fid})"

        def N(key: str, default: float = 0.0, required: bool = True) -> float:
            if key not in feat:
                if required:
                    raise PlanError(f"{where}: missing field {key!r}")
                return default
            return _num(feat[key], env, f"{where}.{key}")

        def ref(key: str, table: dict):
            name = feat.get(key)
            if name not in table:
                raise PlanError(f"{where}: {key}={name!r} does not name a prior feature")
            return table[name]

        try:
            if op == "sketch.rect":
                profiles[fid] = RectangleRounded(N("w"), N("h"), N("r", 0, required=False))
            elif op == "sketch.circle":
                profiles[fid] = Circle(N("d") / 2.0)
            elif op == "extrude":
                prof = ref("profile", profiles)
                taper = N("taper", 0.0, required=False)
                shapes[fid] = extrude(prof, amount=N("height"), taper=taper)
            elif op == "boolean":
                a, b = ref("a", shapes), ref("b", shapes)
                kind = feat["kind"]
                if kind == "union":
                    shapes[fid] = a + b
                elif kind == "cut":
                    shapes[fid] = a - b
                else:
                    shapes[fid] = a & b
            elif op == "translate":
                t = ref("target", shapes)
                shapes[fid] = Pos(
                    N("x", 0.0, required=False),
                    N("y", 0.0, required=False),
                    N("z", 0.0, required=False),
                ) * t
            elif op == "fillet":
                t = ref("target", shapes)
                edges = select_edges(t, feat.get("select", "all"))
                if not edges:
                    raise PlanError(f"{where}: selector {feat.get('select')!r} matched no edges")
                shapes[fid] = fillet(edges, radius=N("radius"))
            elif op == "chamfer":
                t = ref("target", shapes)
                edges = select_edges(t, feat.get("select", "all"))
                if not edges:
                    raise PlanError(f"{where}: selector {feat.get('select')!r} matched no edges")
                shapes[fid] = chamfer(edges, length=N("length"))
            elif op == "shell":
                t = ref("target", shapes)
                shapes[fid] = offset(
                    t, amount=-N("thickness"), openings=select_faces(t, feat.get("open", "none"))
                )
            elif op == "hole":
                t = ref("target", shapes)
                shapes[fid] = _cut_holes(t, feat, N, where)
            elif op == "pattern.linear":
                t = ref("target", shapes)
                count = int(feat["count"])
                spacing = N("spacing")
                axis = feat.get("axis", "x")
                acc = t
                for i in range(1, count):
                    d = [0.0, 0.0, 0.0]
                    d[{"x": 0, "y": 1, "z": 2}[axis]] = i * spacing
                    acc = acc + Pos(*d) * t
                shapes[fid] = acc
            else:
                raise PlanError(f"{where}: unknown op")
        except PlanError:
            raise
        except Exception as exc:  # noqa: BLE001 - kernel errors are structural too
            raise PlanError(f"{where}: {type(exc).__name__}: {exc}") from exc

    result_id = plan.get("result") or plan["features"][-1]["id"]
    if result_id not in shapes:
        raise PlanError(f"result={result_id!r} does not name a solid feature")

    checks = []
    for chk in plan.get("checks", []):
        a, b = chk["a"], chk["b"]
        if a not in shapes or b not in shapes:
            raise PlanError(f"check {chk['kind']}: {a!r}/{b!r} must name solid features")
        if chk["kind"] == "gap":
            value = _gap(shapes[a], shapes[b])
        else:
            inter = shapes[a].intersect(shapes[b])
            value = float(inter.volume) if inter is not None else 0.0
        entry = {"kind": chk["kind"], "a": a, "b": b, "value": value}
        if chk.get("expect") is not None:
            expect = _num(chk["expect"], env, f"check {chk['kind']}.expect")
            tol = _num(chk.get("tolerance", 1e-6), env, f"check {chk['kind']}.tolerance")
            entry["expect"] = expect
            entry["tolerance"] = tol
            entry["pass"] = abs(value - expect) <= tol
        checks.append(entry)

    return {
        "result": shapes[result_id],
        "result_id": result_id,
        "symbols": env,
        "shapes": shapes,
        "checks": checks,
    }


def _num(value: Any, env: dict[str, float], where: str) -> float:
    if isinstance(value, bool):
        raise PlanError(f"{where}: expected a number, got a boolean")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return safe_eval(value, env)
    raise PlanError(f"{where}: expected a number or expression string, got {type(value).__name__}")


def _gap(a, b) -> float:
    for name in ("distance_to", "distance"):
        fn = getattr(a, name, None)
        if callable(fn):
            try:
                return float(fn(b))
            except Exception:  # noqa: BLE001 - fall through to the next spelling
                pass
    raise PlanError("could not measure the gap between the two shapes")


def _cut_holes(shape, feat, N, where: str):
    radius = N("d") / 2.0
    if radius <= 0:
        raise PlanError(f"{where}: d must be > 0")
    positions = feat.get("positions") or []
    if not positions:
        raise PlanError(f"{where}: positions must not be empty")

    bb = shape.bounding_box()
    if feat.get("through", True):
        start = bb.min.Z - 1.0
        height = (bb.max.Z - bb.min.Z) + 2.0
    else:
        depth = N("depth")
        if depth <= 0:
            raise PlanError(f"{where}: non-through holes need depth > 0")
        top = N("z", bb.max.Z, required=False) if feat.get("z") is not None else bb.max.Z
        start = top - depth
        height = depth + 1.0

    cutter = None
    for pair in positions:
        if len(pair) != 2:
            raise PlanError(f"{where}: each position must be [x, y]")
        x = _num(pair[0], {}, f"{where}.positions")
        y = _num(pair[1], {}, f"{where}.positions")
        c = Pos(x, y, start + height / 2.0) * Cylinder(radius=radius, height=height)
        cutter = c if cutter is None else cutter + c
    return shape - cutter


__all__ = ["PlanError", "compile_plan", "safe_eval", "select_edges", "select_faces"]
