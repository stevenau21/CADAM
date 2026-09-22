"""V2 container + lid rebuilt from measured dimensions.

Sets RESULT (the service exports it) and prints its own verification numbers,
which the service captures as `stdout` so the caller can assert on them.
"""
from build123d import RectangleRounded, Pos, extrude

# ------------------------------------------------------------------ parameters
body_x, body_y, body_r = 80.0, 64.0, 8.0
body_h = 67.0
neck_x, neck_y, neck_r = 77.0, 61.0, 6.5
neck_z0 = 58.0
wall_body = 3.9
floor_t = 2.4

clearance = 0.40          # per side, slip fit
lid_wall = 2.6
lid_h = 10.4
lid_z0 = 60.0
skirt_z1 = 67.8
lead_in_h = 0.75
lead_in_extra = 0.30

# --------------------------------------------------------------------- derived
cav_x, cav_y, cav_r = body_x - 2 * wall_body, body_y - 2 * wall_body, body_r - wall_body
skirt_in_x = neck_x + 2 * clearance
skirt_in_y = neck_y + 2 * clearance
skirt_in_r = neck_r + clearance
lid_out_x = skirt_in_x + 2 * lid_wall
lid_out_y = skirt_in_y + 2 * lid_wall
lid_out_r = skirt_in_r + lid_wall
mouth_x = skirt_in_x + 2 * lead_in_extra
mouth_y = skirt_in_y + 2 * lead_in_extra
mouth_r = skirt_in_r + lead_in_extra

# ------------------------------------------------------------------------ body
body = extrude(RectangleRounded(body_x, body_y, body_r), amount=neck_z0)
body += Pos(0, 0, neck_z0) * extrude(
    RectangleRounded(neck_x, neck_y, neck_r), amount=body_h - neck_z0
)
body -= Pos(0, 0, floor_t) * extrude(
    RectangleRounded(cav_x, cav_y, cav_r), amount=body_h
)

# ------------------------------------------------------------------------- lid
lid = Pos(0, 0, lid_z0) * extrude(
    RectangleRounded(lid_out_x, lid_out_y, lid_out_r), amount=lid_h
)
cavity = Pos(0, 0, lid_z0) * extrude(
    RectangleRounded(mouth_x, mouth_y, mouth_r), amount=lead_in_h
)
cavity += Pos(0, 0, lid_z0 + lead_in_h) * extrude(
    RectangleRounded(skirt_in_x, skirt_in_y, skirt_in_r),
    amount=skirt_z1 - (lid_z0 + lead_in_h),
)
lid -= cavity


# --------------------------------------------------- self-reported verification
def gap(a, b):
    for name in ("distance_to", "distance"):
        fn = getattr(a, name, None)
        if callable(fn):
            try:
                return fn(b)
            except Exception:
                pass
    return None


_inter = body.intersect(lid)
_inter_vol = _inter.volume if _inter is not None else 0.0
_bb, _lb = body.bounding_box(), lid.bounding_box()

print("DERIVED lid_outer %.2f x %.2f R%.2f" % (lid_out_x, lid_out_y, lid_out_r))
print("ENGAGEMENT %.2f" % (min(skirt_z1, body_h) - max(lid_z0, neck_z0)))
print("GAP %.4f" % gap(body, lid))
print("INTERFERENCE %.6f" % _inter_vol)
print("BODY_BBOX %.2f %.2f %.2f" % (_bb.size.X, _bb.size.Y, _bb.size.Z))
print("LID_BBOX %.2f %.2f %.2f" % (_lb.size.X, _lb.size.Y, _lb.size.Z))

RESULT = body + lid
