import FreeCAD as App
import Import
from pathlib import Path

path = str(Path(__file__).resolve().with_name("ant_bait_closed.step"))
doc = App.newDocument("ValidateAntBaitSTEP")
Import.insert(path, doc.Name)
doc.recompute()
print("Imported object count:", len(doc.Objects))
for obj in doc.Objects:
    shape = getattr(obj, "Shape", None)
    if shape and not shape.isNull():
        print(obj.Label, "valid=", shape.isValid(), "solids=", len(shape.Solids),
              "faces=", len(shape.Faces), "volume_mm3=", round(shape.Volume, 3),
              "bbox=", (round(shape.BoundBox.XLength, 3),
                        round(shape.BoundBox.YLength, 3),
                        round(shape.BoundBox.ZLength, 3)))
