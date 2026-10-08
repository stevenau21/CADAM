# FreeCAD-MCP ant-bait image reconstruction

A first-pass B-rep reconstruction of an ant-bait station from a product render. The original reference image is not redistributed here. Dimensions are inferred, not taken from a dimensioned drawing.

## Model contents

- Hollow, rounded station shell with horizontal ribs and arched side entries.
- Shallow bait dish fused to a hollow feed tower with a side outlet.
- Separate amber annular bait-pool visual placeholder.
- Clearance-fit removable lid with a raised loop handle.

The main shell is nominally 82 x 66 x 67 mm. The cap has 0.5 mm nominal clearance per side outside the rib peaks. See the parameter dictionary in `model_ant_bait.py`.

## Files

- `ant_bait_open.FCStd` — FreeCAD document with the lid offset for review.
- `ant_bait_closed.step` — closed-position STEP assembly with four solids.
- `station_base.step/.stl`
- `dish_feed_tower.step/.stl`
- `bait_gel_visual.step/.stl`
- `clearance_lid.step/.stl`
- `ant_bait_open.png`, `ant_bait_top.png`, `ant_bait_front.png`, `ant_bait_closed.png` — review views; the open isometric view uses the polished viewport style.
- `model_ant_bait.py` — dimensioned FreeCAD/OpenCASCADE construction script with the presentation style applied by default.
- `run_via_mcp.py` — submits the script as an MCP `tools/call` to a running FreeCAD-MCP server.
- `validate_export.py` — re-imports the STEP using `freecadcmd` and reports topology.

## Presentation setup

The saved FreeCAD document and generator use **Shaded** display mode, a muted blue-gray/cream/amber palette, finer tessellation (`Deviation=0.08`, `AngularDeflection=12°`), a dark gradient background, and an axonometric perspective camera. The current machine also has hardware OpenGL/VBO enabled and a modest anti-aliasing setting. GPU preferences are global FreeCAD settings; display mode, colors, and camera are stored with the document. These choices affect the viewport only, not STEP/STL geometry.

To get a more product-like viewport: avoid `Flat Lines` for the presentation pass, use hardware OpenGL when supported, select Perspective, and set a restrained background and palette. Keep Orthographic available for dimension checks.

## Validation results

All four exported components are valid single solids:

| Component | Faces | Volume (mm³) | Bounding box (mm) |
|---|---:|---:|---|
| Ribbed station | 281 | 67,792.890 | 85.2 x 69.2 x 67.0 |
| Dish + feed tower | 27 | 16,954.206 | 53.945 x 53.972 x 58.8 |
| Bait pool | 4 | 554.353 | 31.979 x 31.989 x 1.4 |
| Lid + handle | 89 | 23,278.360 | 91.2 x 75.2 x 30.0 |

Computed common volumes were zero for lid/body, dish/body, and bait/dish intersections. STEP re-import found four valid solids in the assembly (overall bounds 91.2 x 75.2 x 93.0 mm). FreeCAD-MCP returned no execution error.

## Re-run

Requires FreeCAD with the FreeCAD-MCP addon and its RPC server running, plus the external FreeCAD-MCP repository and its `uv` environment.

```powershell
$env:FREECAD_MCP_REPO = 'C:/path/to/freecad-mcp'
uv run python run_via_mcp.py
```

The helper defaults to `F:/projects/3D/apps/freecad-mcp` if `FREECAD_MCP_REPO` is not set. It targets the FreeCAD-MCP RPC endpoint at `127.0.0.1:9875`; change the server's configuration if your setup uses a different endpoint.

Validate the exported assembly with FreeCAD's command-line executable:

```powershell
& 'C:/path/to/FreeCAD/bin/freecadcmd.exe' validate_export.py
```

## Limitations

This is a visual reconstruction, not a production-ready copy. Dimensions and hidden mechanisms are assumptions. The bait pool is a visual placeholder; the clearance check does not validate lid retention, snap-fit behavior, ant-access behavior, refillability, or printability. The FreeCAD document has named B-rep component objects, and the source script is parameterized, but it does not contain a native PartDesign Pad/Pocket history for every detail. ChatGPT Desktop itself was not connected; the assistant authored the model script and sent it through the MCP bridge.
