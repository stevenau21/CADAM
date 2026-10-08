"""FreeCAD/MCP image-driven ant-bait recreation.

Reference: F:/projects/3D/image.png. Dimensions are inferred, not measured.
Run in FreeCAD's GUI through the FreeCAD-MCP execute_code tool. Geometry is
built from editable, dimensioned Python source using native OpenCASCADE B-reps.
"""
import FreeCAD as App
import FreeCADGui as Gui
import Part
import Import
import math
import json
from FreeCAD import Vector

OUT = r"F:/projects/3D/freecad-ant-bait-test"
DOC_NAME = "AntBait_Image_Recreation"

# Image-derived design assumptions, mm.
P = {
    "body_width": 82.0,
    "body_depth": 66.0,
    "body_height": 67.0,
    "body_corner_radius": 6.0,
    "wall": 2.8,
    "floor": 3.2,
    "rib_count": 10,
    "rib_start_z": 11.0,
    "rib_pitch": 4.4,
    "rib_height": 1.15,
    "rib_outer_width": 85.2,
    "rib_outer_depth": 69.2,
    "entry_radius": 8.0,
    "entry_center_z": 22.0,
    "entry_x_offset": 24.0,
    "dish_radius": 27.0,
    "dish_height": 17.0,
    "lid_clearance_per_side": 0.5,
    "lid_wall": 2.5,
    "lid_height": 17.0,
    "handle_rise": 13.0,
}


def rounded_prism(width, depth, height, radius, z=0.0, cx=0.0, cy=0.0):
    """Rounded-rectangle vertical prism, made by filleting only vertical edges."""
    solid = Part.makeBox(width, depth, height,
                         Vector(cx - width / 2.0, cy - depth / 2.0, z))
    vertical = []
    for edge in solid.Edges:
        bb = edge.BoundBox
        if bb.ZLength > height - 1e-5 and bb.XLength < 1e-5 and bb.YLength < 1e-5:
            vertical.append(edge)
    if radius > 0 and vertical:
        solid = solid.makeFillet(radius, vertical)
    return solid


def annular_rib(z):
    outer = rounded_prism(P["rib_outer_width"], P["rib_outer_depth"],
                          P["rib_height"], 7.0, z)
    inner_w = P["body_width"] - 0.4
    inner_d = P["body_depth"] - 0.4
    inner = rounded_prism(inner_w, inner_d, P["rib_height"] + 0.4,
                          5.8, z - 0.2)
    return outer.cut(inner)


def make_arch_tool(cx):
    """Arch-shaped tunnel cutter along Y: flat-bottomed with semicircular top."""
    r = P["entry_radius"]
    cz = P["entry_center_z"]
    y0 = -40.0
    length = 80.0
    lower = Part.makeBox(2 * r, length, cz - 7.0,
                         Vector(cx - r, y0, 7.0))
    crown = Part.makeCylinder(r, length, Vector(cx, y0, cz), Vector(0, 1, 0))
    return lower.fuse(crown)


def make_bait_dish_and_tower():
    """Shallow revolved bait bowl fused to a hollow feed tower with outlet."""
    z0 = P["floor"]
    # Closed radial section: substantial floor, sloped bowl, rolled-looking rim.
    profile_rz = [
        (0.0, 0.0), (18.0, 0.0), (25.0, 14.0), (27.0, 17.0),
        (25.0, 17.0), (23.0, 15.0), (18.0, 5.0), (0.0, 5.0), (0.0, 0.0)
    ]
    points = [Vector(r, 0.0, z0 + z) for r, z in profile_rz]
    wire = Part.makePolygon(points)
    dish = Part.Face(wire).revolve(Vector(0, 0, z0), Vector(0, 0, 1), 360)

    # Hollow central feed column with a small side outlet into the dish.
    tower_bottom = z0 + 4.2
    tower_top = 62.0
    tower_outer = rounded_prism(14.0, 16.0, tower_top - tower_bottom,
                                2.0, tower_bottom, 0.0, -1.5)
    cavity_bottom = z0 + 9.0
    tower_inner = rounded_prism(9.0, 11.0, tower_top - cavity_bottom + 1.0,
                                1.2, cavity_bottom, 0.0, -1.5)
    tower = tower_outer.cut(tower_inner)
    outlet = Part.makeCylinder(2.2, 8.0, Vector(0.0, -10.5, z0 + 11.0),
                               Vector(0.0, 1.0, 0.0))
    tower = tower.cut(outlet)
    combined = dish.fuse(tower).removeSplitter()
    return combined


def make_bait_gel():
    """Amber annular pool following the bowl, clear of the central tower."""
    outer = Part.makeCylinder(16.0, 1.4, Vector(0.0, 0.0, 9.0))
    inner = Part.makeCylinder(11.4, 1.8, Vector(0.0, 0.0, 8.8))
    return outer.cut(inner)


def make_lid():
    """Clearance-fit cap with a raised U-loop carry handle."""
    peak_w = P["rib_outer_width"]
    peak_d = P["rib_outer_depth"]
    clearance = P["lid_clearance_per_side"]
    wall = P["lid_wall"]
    inner_w = peak_w + 2.0 * clearance
    inner_d = peak_d + 2.0 * clearance
    outer_w = inner_w + 2.0 * wall
    outer_d = inner_d + 2.0 * wall
    z_base = P["body_height"] - 4.0
    outer = rounded_prism(outer_w, outer_d, P["lid_height"], 8.5, z_base)
    cavity_h = P["lid_height"] - wall
    cavity = rounded_prism(inner_w, inner_d, cavity_h + 1.0,
                           6.0, z_base - 0.1)
    cap = outer.cut(cavity)

    roof_z = z_base + P["lid_height"]
    y = -2.5
    rise = P["handle_rise"]
    spring_z = roof_z
    outer_r = 8.0
    inner_r = 5.0
    points = [Vector(-outer_r, y, spring_z),
              Vector(-outer_r, y, spring_z + 5.0)]
    # Smooth polygonal semicircular outer crown, left to right.
    for i in range(1, 33):
        a = math.pi - math.pi * i / 32.0
        points.append(Vector(outer_r * math.cos(a), y,
                            spring_z + 5.0 + outer_r * math.sin(a)))
    points.extend([Vector(outer_r, y, spring_z),
                   Vector(inner_r, y, spring_z),
                   Vector(inner_r, y, spring_z + 5.0)])
    # Inner boundary is traversed right-to-left.
    for i in range(1, 33):
        a = math.pi * i / 32.0
        points.append(Vector(inner_r * math.cos(a), y,
                            spring_z + 5.0 + inner_r * math.sin(a)))
    points.append(Vector(-inner_r, y, spring_z))
    points.append(points[0])
    handle_wire = Part.makePolygon(points)
    handle = Part.Face(handle_wire).extrude(Vector(0.0, 5.0, 0.0))
    return cap.fuse(handle).removeSplitter()


# Start cleanly, without disturbing any other FreeCAD document.
try:
    App.closeDocument(DOC_NAME)
except Exception:
    pass
doc = App.newDocument(DOC_NAME)

# Ribbed hollow station body.
body_outer = rounded_prism(P["body_width"], P["body_depth"],
                           P["body_height"], P["body_corner_radius"])
for i in range(P["rib_count"]):
    rib = annular_rib(P["rib_start_z"] + i * P["rib_pitch"])
    body_outer = body_outer.fuse(rib)
body_outer = body_outer.removeSplitter()
inner_w = P["body_width"] - 2.0 * P["wall"]
inner_d = P["body_depth"] - 2.0 * P["wall"]
interior = rounded_prism(inner_w, inner_d, P["body_height"] + 4.0,
                         P["body_corner_radius"] - P["wall"], P["floor"])
body = body_outer.cut(interior)
for cx in (-P["entry_x_offset"], P["entry_x_offset"]):
    body = body.cut(make_arch_tool(cx))
body = body.removeSplitter()

# Functional interior components and the removable cover.
dish_tower = make_bait_dish_and_tower()
gel = make_bait_gel()
lid = make_lid()


def add_feature(name, label, shape, color, transparency=0):
    obj = doc.addObject("Part::Feature", name)
    obj.Label = label
    obj.Shape = shape
    obj.ViewObject.ShapeColor = color
    obj.ViewObject.LineColor = (0.16, 0.19, 0.23)
    obj.ViewObject.DisplayMode = "Shaded"
    obj.ViewObject.LineWidth = 1.0
    obj.ViewObject.Deviation = 0.08
    obj.ViewObject.AngularDeflection = 12.0
    obj.ViewObject.Transparency = transparency
    obj.addProperty("App::PropertyString", "ModelSource", "Design")
    obj.ModelSource = "model_ant_bait.py; image-derived dimensions in P"
    return obj

body_obj = add_feature("RibbedStation", "Ribbed station / arched entries",
                       body, (0.30, 0.40, 0.49), 0)
dish_obj = add_feature("DishFeedTower", "Bait dish + hollow feed tower",
                       dish_tower, (0.80, 0.76, 0.65), 0)
gel_obj = add_feature("BaitGel", "Amber bait pool (visual placeholder)",
                      gel, (1.0, 0.43, 0.08), 0)
lid_obj = add_feature("ClearanceLid", "Clearance-fit lid + loop handle",
                      lid, (0.72, 0.80, 0.86), 0)

# Preserve the exact dimension assumptions in the native FCStd document.
params_obj = doc.addObject("App::FeaturePython", "DesignParameters")
params_obj.Label = "Image-derived design parameters (mm)"
params_obj.addProperty("App::PropertyString", "ParameterJSON", "Design")
params_obj.ParameterJSON = json.dumps(P, indent=2)

# Check per-component topology and the closed-position lid/base fit before export.
doc.recompute()
fit_checks = {
    "closed_lid_vs_body_intersection_mm3": body.common(lid).Volume,
    "dish_vs_body_intersection_mm3": body.common(dish_tower).Volume,
    "bait_vs_dish_tower_intersection_mm3": dish_tower.common(gel).Volume,
}
stats = {}
for obj in (body_obj, dish_obj, gel_obj, lid_obj):
    shape = obj.Shape
    stats[obj.Name] = {
        "valid": bool(shape.isValid()),
        "solids": len(shape.Solids),
        "faces": len(shape.Faces),
        "volume_mm3": round(shape.Volume, 3),
        "bbox_mm": [round(shape.BoundBox.XLength, 3),
                    round(shape.BoundBox.YLength, 3),
                    round(shape.BoundBox.ZLength, 3)],
    }

# Export all components in their CLOSED assembly positions, then the per-part STEP.
Import.export([body_obj, dish_obj, gel_obj, lid_obj], OUT + "/ant_bait_closed.step")
for obj, stem in ((body_obj, "station_base"),
                  (dish_obj, "dish_feed_tower"),
                  (gel_obj, "bait_gel_visual"),
                  (lid_obj, "clearance_lid")):
    Import.export([obj], OUT + "/" + stem + ".step")

# Mesh deliverables for viewing/printing feasibility review.
import Mesh
for obj, stem in ((body_obj, "station_base"),
                  (dish_obj, "dish_feed_tower"),
                  (gel_obj, "bait_gel_visual"),
                  (lid_obj, "clearance_lid")):
    Mesh.export([obj], OUT + "/" + stem + ".stl")

# Save an exploded/open review arrangement with the lid offset beside the station.
lid_obj.Placement.Base = Vector(104.0, 0.0, 0.0)
doc.recompute()
doc.saveAs(OUT + "/ant_bait_open.FCStd")
view = Gui.activeDocument().activeView()
view.setCameraType("Perspective")
view.viewAxonometric()
view.fitAll()
for _ in range(4):
    view.zoomIn()
doc.save()

print("ANT_BAIT_STATS=" + json.dumps({
    "components": stats,
    "fit_checks_mm3": {key: round(value, 6) for key, value in fit_checks.items()},
    "fit_clearance_per_side_mm": P["lid_clearance_per_side"],
    "doc": OUT + "/ant_bait_open.FCStd",
    "step": OUT + "/ant_bait_closed.step",
}, sort_keys=True))
