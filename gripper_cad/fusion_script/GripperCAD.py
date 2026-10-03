"""Builds the two-jaw pivot gripper (base plate + clevis lugs + 2 jaws) and
exports one STL per printable part plus a STEP of the whole assembly.

Every dimension comes from params.json next to this file; the script refuses
to run if any key is missing.
"""
import json
import os
import traceback

import adsk.core
import adsk.fusion

REQUIRED_KEYS = [
    "plate_length", "plate_width", "plate_thickness",
    "center_hole_dia", "mount_hole_dia", "mount_hole_spacing_x", "mount_hole_spacing_y",
    "lug_length", "lug_drop", "pivot_from_end", "pivot_below_plate",
    "pin_hole_dia_lug", "pin_hole_dia_jaw",
    "jaw_width", "jaw_side_clearance", "jaw_head_radius",
    "jaw_inner_gap", "jaw_total_height", "jaw_tip_thickness",
    "output_dir",
]


def mm(v):
    # Fusion's API works in centimetres.
    return v / 10.0


def load_params():
    path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "params.json")
    with open(path, "r", encoding="utf-8") as f:
        p = json.load(f)
    missing = [k for k in REQUIRED_KEYS if k not in p]
    if missing:
        raise ValueError("params.json is missing: " + ", ".join(missing))
    return p


def check_geometry(p):
    slot = p["jaw_width"] + 2 * p["jaw_side_clearance"]
    if slot >= p["plate_width"]:
        raise ValueError("jaw_width + clearance must be narrower than plate_width")
    if p["jaw_head_radius"] >= p["pivot_below_plate"]:
        raise ValueError("jaw_head_radius must be < pivot_below_plate or the jaw hits the plate")
    pivot_x = p["plate_length"] / 2 - p["pivot_from_end"]
    if p["jaw_inner_gap"] / 2 >= pivot_x:
        raise ValueError("jaw_inner_gap is too wide for the pivot position")
    tip_z = p["plate_thickness"] - p["jaw_total_height"]
    if tip_z >= -p["pivot_below_plate"]:
        raise ValueError("jaw_total_height is too short to reach below the pivot")


class Builder:
    def __init__(self, comp):
        self.comp = comp
        self.extrudes = comp.features.extrudeFeatures

    def _xz_sketch(self):
        return self.comp.sketches.add(self.comp.xZConstructionPlane)

    def _pt(self, sketch, x, y, z):
        return sketch.modelToSketchSpace(adsk.core.Point3D.create(mm(x), mm(y), mm(z)))

    def _polygon(self, sketch, pts_xz):
        lines = sketch.sketchCurves.sketchLines
        sp = [self._pt(sketch, x, 0, z) for x, z in pts_xz]
        for i in range(len(sp)):
            lines.addByTwoPoints(sp[i], sp[(i + 1) % len(sp)])

    def _all_profiles(self, sketch):
        coll = adsk.core.ObjectCollection.create()
        for prof in sketch.profiles:
            coll.add(prof)
        return coll

    def _extrude_sym(self, profiles, op, full_width, bodies=None):
        inp = self.extrudes.createInput(profiles, op)
        inp.setSymmetricExtent(adsk.core.ValueInput.createByReal(mm(full_width)), True)
        if bodies is not None:
            inp.participantBodies = bodies
        return self.extrudes.add(inp)

    def xz_polygon_sym(self, pts_xz, op, full_width, bodies=None):
        sk = self._xz_sketch()
        self._polygon(sk, pts_xz)
        return self._extrude_sym(self._all_profiles(sk), op, full_width, bodies)

    def xz_circle_sym(self, cx, cz, r, op, full_width, bodies=None):
        sk = self._xz_sketch()
        sk.sketchCurves.sketchCircles.addByCenterRadius(self._pt(sk, cx, 0, cz), mm(r))
        return self._extrude_sym(self._all_profiles(sk), op, full_width, bodies)

    def plate(self, p):
        sk = self.comp.sketches.add(self.comp.xYConstructionPlane)
        L, W = p["plate_length"], p["plate_width"]
        sk.sketchCurves.sketchLines.addTwoPointRectangle(
            adsk.core.Point3D.create(mm(-L / 2), mm(-W / 2), 0),
            adsk.core.Point3D.create(mm(L / 2), mm(W / 2), 0))
        inp = self.extrudes.createInput(sk.profiles.item(0), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        inp.setDistanceExtent(False, adsk.core.ValueInput.createByReal(mm(p["plate_thickness"])))
        body = self.extrudes.add(inp).bodies.item(0)

        holes = self.comp.sketches.add(self.comp.xYConstructionPlane)
        circles = holes.sketchCurves.sketchCircles
        circles.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), mm(p["center_hole_dia"] / 2))
        hx, hy = p["mount_hole_spacing_x"] / 2, p["mount_hole_spacing_y"] / 2
        for sx in (-1, 1):
            for sy in (-1, 1):
                circles.addByCenterRadius(
                    adsk.core.Point3D.create(mm(sx * hx), mm(sy * hy), 0), mm(p["mount_hole_dia"] / 2))
        inp = self.extrudes.createInput(self._all_profiles(holes), adsk.fusion.FeatureOperations.CutFeatureOperation)
        inp.setDistanceExtent(False, adsk.core.ValueInput.createByReal(mm(p["plate_thickness"])))
        inp.participantBodies = [body]
        self.extrudes.add(inp)
        return body


def build(design, p):
    root = design.rootComponent
    b = Builder(root)
    ops = adsk.fusion.FeatureOperations

    L = p["plate_length"]
    W = p["plate_width"]
    pivot_x = L / 2 - p["pivot_from_end"]
    pivot_z = -p["pivot_below_plate"]
    slot_w = p["jaw_width"] + 2 * p["jaw_side_clearance"]
    tip_z = p["plate_thickness"] - p["jaw_total_height"]
    inner_x = p["jaw_inner_gap"] / 2
    head_r = p["jaw_head_radius"]

    base = b.plate(p)
    base.name = "Base"

    for s in (-1, 1):
        x0, x1 = s * (L / 2 - p["lug_length"]), s * (L / 2)
        lug = [(x0, 0), (x1, 0), (x1, -p["lug_drop"]), (x0, -p["lug_drop"])]
        b.xz_polygon_sym(lug, ops.JoinFeatureOperation, W, [base])
        # Open the middle of the lug block so the jaw can swing between the two cheeks.
        pad = 1
        slot = [(x0 - s * pad, 0), (x1 + s * pad, 0),
                (x1 + s * pad, -p["lug_drop"] - pad), (x0 - s * pad, -p["lug_drop"] - pad)]
        b.xz_polygon_sym(slot, ops.CutFeatureOperation, slot_w, [base])
        b.xz_circle_sym(s * pivot_x, pivot_z, p["pin_hole_dia_lug"] / 2, ops.CutFeatureOperation, W + 2, [base])

    jaws = []
    for s, name in ((-1, "Jaw_Left"), (1, "Jaw_Right")):
        outline = [
            (s * inner_x, pivot_z),
            (s * (pivot_x + head_r), pivot_z),
            (s * (inner_x + p["jaw_tip_thickness"]), tip_z),
            (s * inner_x, tip_z),
        ]
        jaw = b.xz_polygon_sym(outline, ops.NewBodyFeatureOperation, p["jaw_width"]).bodies.item(0)
        b.xz_circle_sym(s * pivot_x, pivot_z, head_r, ops.JoinFeatureOperation, p["jaw_width"], [jaw])
        b.xz_circle_sym(s * pivot_x, pivot_z, p["pin_hole_dia_jaw"] / 2, ops.CutFeatureOperation,
                        p["jaw_width"] + 2, [jaw])
        jaw.name = name
        jaws.append(jaw)

    return [base] + jaws


def export(design, bodies, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    em = design.exportManager
    written = []
    for body in bodies:
        path = os.path.join(out_dir, body.name + ".stl")
        opts = em.createSTLExportOptions(body, path)
        opts.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
        em.execute(opts)
        written.append(path)
    step = os.path.join(out_dir, "Gripper.step")
    em.execute(em.createSTEPExportOptions(step, design.rootComponent))
    written.append(step)
    return written


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        p = load_params()
        check_geometry(p)

        doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        design = adsk.fusion.Design.cast(app.activeProduct)
        design.designType = adsk.fusion.DesignTypes.ParametricDesignType

        bodies = build(design, p)
        files = export(design, bodies, p["output_dir"])
        app.activeViewport.fit()
        ui.messageBox("Gripper built. Exported:\n" + "\n".join(files))
    except Exception:
        if ui:
            ui.messageBox("GripperCAD failed:\n{}".format(traceback.format_exc()))
