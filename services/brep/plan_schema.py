"""ModelPlan: the constrained intermediate representation.

The LLM emits a PLAN (typed operations + numbers), never code. A deterministic
compiler executes it against the kernel. That shrinks the model's job from
"write correct CAD code" to "choose operations and numbers" -- a far smaller,
checkable space -- and it means the service never executes arbitrary Python.

Numeric fields accept a number OR an expression string:

    "height": 58
    "height": "body_h - neck_z0"

Expressions are evaluated by a whitelisted evaluator in `compiler.safe_eval`
(no attribute access, no calls except a small math set, no statements). This is
what lets a plan DERIVE its mating dimensions instead of duplicating them,
which is the whole reason fits can be trusted.

Enum-like fields (`kind`, `select`, `axis`, `open`) are literals, not
expressions, so the two can never be confused.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _reject_bool(value):
    # bool is a subclass of int, so pydantic happily coerces True -> 1.0. Silently
    # turning a boolean into a dimension is precisely the kind of quiet nonsense
    # this IR exists to prevent, so refuse it at the boundary.
    if isinstance(value, bool):
        raise ValueError("expected a number or expression string, got a boolean")
    return value


StrictNumber = Annotated[float, BeforeValidator(_reject_bool)]

# A number, or a string expression evaluated against the plan's symbols.
NumberOrExpr = Union[StrictNumber, str]

SelectEdges = Literal["all", "z_max", "z_min", "vertical", "horizontal", "circular"]
OpenFaces = Literal["none", "z_max", "z_min"]
BoolKind = Literal["union", "cut", "intersect"]


class _Strict(BaseModel):
    # extra="forbid" turns a misspelled or invented field into a structural
    # error the model can fix, instead of silently ignoring it.
    model_config = ConfigDict(extra="forbid")


class Parameter(_Strict):
    """A user-facing slider/switch. First-class in the plan, which is why the
    parameter panel needs no parsing and no AST scraping of source comments."""

    name: str
    value: NumberOrExpr
    min: NumberOrExpr | None = None
    max: NumberOrExpr | None = None
    step: NumberOrExpr | None = None
    label: str | None = None
    unit: str | None = None


class Derived(_Strict):
    """A computed symbol. Not a slider -- used to factor out shared math."""

    name: str
    expr: str


class Check(_Strict):
    """A fit assertion the compiler must evaluate and report.

    `gap`         -> minimum distance between two shapes (expect the clearance)
    `interference`-> boolean-intersection volume (must be 0)
    """

    kind: Literal["gap", "interference"]
    a: str
    b: str
    expect: NumberOrExpr | None = None
    tolerance: NumberOrExpr | None = None


class _Feature(_Strict):
    id: str


class _Sketch(_Feature):
    """A 2D profile. `plane` picks which plane it is drawn on, `at` offsets it
    afterwards -- both needed for solids of revolution, whose profile must sit
    in a plane containing the axis."""

    plane: Literal["XY", "XZ", "YZ"] = "XY"
    at: list[NumberOrExpr] | None = None


class SketchRect(_Sketch):
    op: Literal["sketch.rect"]
    w: NumberOrExpr
    h: NumberOrExpr
    r: NumberOrExpr = 0


class SketchCircle(_Sketch):
    op: Literal["sketch.circle"]
    d: NumberOrExpr


class SketchPolygon(_Sketch):
    """Arbitrary closed profile from points, in the sketch plane's own 2D
    coordinates. This is the op that unlocks bowls, funnels, wedges and arches
    -- anything whose cross-section is not a rectangle or a circle."""

    op: Literal["sketch.polygon"]
    points: list[list[NumberOrExpr]]


class Extrude(_Feature):
    op: Literal["extrude"]
    profile: str
    height: NumberOrExpr
    taper: NumberOrExpr = 0


class Revolve(_Feature):
    """Revolve a profile about X, Y or Z. The profile must lie in a plane that
    contains the axis (e.g. an XZ-plane profile revolved about Z)."""

    op: Literal["revolve"]
    profile: str
    angle: NumberOrExpr = 360
    axis: Literal["x", "y", "z"] = "z"


class Boolean(_Feature):
    op: Literal["boolean"]
    kind: BoolKind
    a: str
    b: str


class Translate(_Feature):
    op: Literal["translate"]
    target: str
    x: NumberOrExpr = 0
    y: NumberOrExpr = 0
    z: NumberOrExpr = 0


class Rotate(_Feature):
    op: Literal["rotate"]
    target: str
    x: NumberOrExpr = 0
    y: NumberOrExpr = 0
    z: NumberOrExpr = 0


class Fillet(_Feature):
    op: Literal["fillet"]
    target: str
    radius: NumberOrExpr
    select: SelectEdges = "all"


class Chamfer(_Feature):
    op: Literal["chamfer"]
    target: str
    length: NumberOrExpr
    select: SelectEdges = "all"


class Shell(_Feature):
    op: Literal["shell"]
    target: str
    thickness: NumberOrExpr
    open: OpenFaces = "none"


class Hole(_Feature):
    op: Literal["hole"]
    target: str
    d: NumberOrExpr
    positions: list[list[NumberOrExpr]]
    through: bool = True
    depth: NumberOrExpr = 0
    z: NumberOrExpr | None = None


class PatternLinear(_Feature):
    op: Literal["pattern.linear"]
    target: str
    # A count may be a literal or an expression, so the number of ribs can be a
    # slider like any other dimension. The compiler requires a whole number.
    count: NumberOrExpr
    spacing: NumberOrExpr
    axis: Literal["x", "y", "z"] = "x"


class PatternPolar(_Feature):
    """Copy a shape N times around an axis -- ribs, spokes, bolt circles."""

    op: Literal["pattern.polar"]
    target: str
    count: NumberOrExpr
    axis: Literal["x", "y", "z"] = "z"
    angle: NumberOrExpr = 360
    center: list[NumberOrExpr] | None = None


Feature = Annotated[
    Union[
        SketchRect,
        SketchCircle,
        SketchPolygon,
        Extrude,
        Revolve,
        Boolean,
        Translate,
        Rotate,
        Fillet,
        Chamfer,
        Shell,
        Hole,
        PatternLinear,
        PatternPolar,
    ],
    Field(discriminator="op"),
]


class ModelPlan(_Strict):
    units: Literal["mm"] = "mm"
    description: str | None = None
    parameters: list[Parameter] = []
    derived: list[Derived] = []
    features: list[Feature] = Field(min_length=1)
    checks: list[Check] = []
    # Which feature id is the model. Defaults to the last feature.
    result: str | None = None


def op_names() -> list[str]:
    """The op vocabulary, for the agent prompt and for GET /ops."""
    return [
        "sketch.rect",
        "sketch.circle",
        "sketch.polygon",
        "extrude",
        "revolve",
        "boolean",
        "translate",
        "rotate",
        "fillet",
        "chamfer",
        "shell",
        "hole",
        "pattern.linear",
        "pattern.polar",
    ]
