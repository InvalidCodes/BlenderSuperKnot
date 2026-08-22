"""Generate six open-rope topology controls for Blender/MuJoCo.

The first three assets share one planar trefoil shadow and differ only in the
over/under height at its crossings.  The final three are ambient-equivalent
views of the right-handed trefoil selected to have 4, 5, and 3 crossings in
the XY projection respectively.

Run with:
    blender --background blender2mujoco_test.blend \
        --python generate_topology_controls.py -- \
        --out-dir topology_controls
"""

import argparse
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector


TWO_PI = 2.0 * math.pi
SAMPLES = 420
ROPE_RADIUS = 0.008
# Keep the centerline near the length used by the existing release pipeline.
# Crossing clearance remains >2 rope diameters while a 1.5 m/end pull is long
# enough to straighten an unknotted control.
XY_SCALE = 0.10
Z_SCALE = 0.04
BASE_HEIGHT = 0.31
CUT_HALF_WIDTH = 0.075

# The standard trefoil projection has crossings at these parameter pairs.
# Switching either height pair unknots the diagram.
CROSSING_PARAMETER_PAIRS = (
    (0.270917, 3.917873),
    (1.823478, 4.459707),
    (2.365312, 6.012268),
)

VARIANTS = (
    {
        "id": "01_right_trefoil",
        "label": "right_trefoil",
        "operation": "base",
        "crossings": 3,
        "closure_topology": "3_1_right",
    },
    {
        "id": "02_three_crossing_unknot",
        "label": "three_crossing_unknot",
        "operation": "switch_crossing_0",
        "crossings": 3,
        "closure_topology": "0_1",
    },
    {
        "id": "03_left_trefoil",
        "label": "left_trefoil",
        "operation": "mirror_all_crossings",
        "crossings": 3,
        "closure_topology": "3_1_left",
    },
    {
        "id": "04_right_trefoil_r1",
        "label": "right_trefoil_r1",
        "operation": "equivalent_projection_3_to_4",
        "crossings": 4,
        "closure_topology": "3_1_right",
        # Along the linear rotation path from the base view, the detected
        # crossing-count sequence is exactly 3 -> 4.
        "rotation_xyz": (1.186284, 1.073419, -0.283626),
    },
    {
        "id": "05_right_trefoil_r2",
        "label": "right_trefoil_r2",
        "operation": "equivalent_projection_3_to_5",
        "crossings": 5,
        "closure_topology": "3_1_right",
        "rotation_xyz": (1.575283, -2.420636, -3.084554),
    },
    {
        "id": "06_right_trefoil_r3",
        "label": "right_trefoil_r3",
        "operation": "equivalent_projection_3_to_3",
        "crossings": 3,
        "closure_topology": "3_1_right",
        "rotation_xyz": (0.0, 0.30, 0.0),
    },
)


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="topology_controls")
    return parser.parse_args(argv)


def _periodic_distance(a, b):
    return abs((a - b + math.pi) % TWO_PI - math.pi)


def _smooth_bump(t, center, radius=0.24):
    distance = _periodic_distance(t, center)
    if distance >= radius:
        return 0.0
    # Raised cosine: value and first derivative are both zero at the boundary.
    return 0.5 + 0.5 * math.cos(math.pi * distance / radius)


def _base_point(t, operation):
    x = math.sin(t) + 2.0 * math.sin(2.0 * t)
    y = math.cos(t) - 2.0 * math.cos(2.0 * t)
    z = -0.65 * math.sin(3.0 * t)

    if operation == "mirror_all_crossings":
        z = -z
    elif operation == "switch_crossing_0":
        first, second = CROSSING_PARAMETER_PAIRS[0]
        weight = max(_smooth_bump(t, first), _smooth_bump(t, second))
        z *= 1.0 - 2.0 * weight

    return Vector((XY_SCALE * x, XY_SCALE * y, Z_SCALE * z))


def _rotation_matrix(rotation_xyz):
    rx, ry, rz = rotation_xyz
    return (
        Matrix.Rotation(rz, 4, "Z")
        @ Matrix.Rotation(ry, 4, "Y")
        @ Matrix.Rotation(rx, 4, "X")
    )


def _sample_points(variant):
    # The tiny cut around t=0 creates two physical endpoints.  It does not
    # remove any of the three diagram crossings and supplies an outside
    # closure convention for the topology labels.
    start = CUT_HALF_WIDTH
    end = TWO_PI - CUT_HALF_WIDTH
    points = []
    rotation = _rotation_matrix(variant.get("rotation_xyz", (0.0, 0.0, 0.0)))
    for index in range(SAMPLES):
        alpha = index / (SAMPLES - 1)
        t = start + alpha * (end - start)
        point = rotation @ _base_point(t, variant["operation"])
        points.append(point)

    # Put every asset just above its automatically generated MuJoCo table.
    min_z = min(point.z for point in points)
    dz = BASE_HEIGHT - min_z
    return [(point.x, point.y, point.z + dz) for point in points]


def _clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.curves, bpy.data.meshes, bpy.data.materials):
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)


def _create_curve(variant, points):
    name = variant["id"]
    curve_data = bpy.data.curves.new(name + "_Curve", type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.resolution_u = 1
    curve_data.fill_mode = "FULL"
    curve_data.bevel_depth = ROPE_RADIUS
    curve_data.bevel_resolution = 5

    spline = curve_data.splines.new(type="POLY")
    spline.points.add(len(points) - 1)
    for control_point, xyz in zip(spline.points, points):
        control_point.co = (*xyz, 1.0)
    spline.use_cyclic_u = False

    knot = bpy.data.objects.new(name, curve_data)
    bpy.context.collection.objects.link(knot)
    knot["control_family"] = "topology_controls_v1"
    knot["control_label"] = variant["label"]
    knot["diagram_operation"] = variant["operation"]
    knot["closure_topology"] = variant["closure_topology"]
    knot["gauss_num_crossings"] = variant["crossings"]
    knot["gauss_num_components"] = 1
    knot["physical_curve"] = "open"
    knot["closure_convention"] = "connect_endpoints_across_projection_exterior"
    knot["projection_axis"] = "+Z"

    material = bpy.data.materials.new(name + "_RopeMaterial")
    material.diffuse_color = (0.52, 0.24, 0.075, 1.0)
    curve_data.materials.append(material)

    bpy.ops.object.select_all(action="DESELECT")
    knot.select_set(True)
    bpy.context.view_layer.objects.active = knot
    return knot


def main():
    args = parse_args()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "family": "topology_controls_v1",
        "projection": "XY viewed along +Z",
        "physical_curves": "open for endpoint-pull MuJoCo pipeline",
        "topology_convention": "outside closure across the small t=0 cut",
        "variants": [],
    }

    for variant in VARIANTS:
        _clear_scene()
        points = _sample_points(variant)
        knot = _create_curve(variant, points)
        if knot.type != "CURVE" or knot.data.splines[0].use_cyclic_u:
            raise RuntimeError(f"{variant['id']} is not one open curve")
        blend_path = out_dir / f"{variant['id']}.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
        record = dict(variant)
        record["blend"] = str(blend_path)
        record["samples"] = len(points)
        manifest["variants"].append(record)
        print(
            f"TOPOLOGY_CONTROL={variant['id']} crossings={variant['crossings']} "
            f"closure={variant['closure_topology']} blend={blend_path}"
        )

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"TOPOLOGY_CONTROL_MANIFEST={manifest_path}")


if __name__ == "__main__":
    main()
