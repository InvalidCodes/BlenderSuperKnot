"""Generate a one-component slip-release knot for the MuJoCo pipeline.

The centerline is an open rope with a single removable curl.  Pulling its two
free ends along their outward tangents performs a Reidemeister-I release: the
loop shrinks, slides out, and the rope becomes straight instead of jamming into
a stopper knot.

Run with:
    blender --background blender2mujoco_test.blend \
        --python generate_slip_knot.py -- --out slip_knot.blend
"""

import argparse
import math
import sys

import bpy


KNOT_NAME = "Slip_Knot_Release"

# The centerline visits the crossing twice.  The first visit is the overpass
# and the second is the underpass; their 28 mm separation is greater than the
# intended 16 mm rope diameter.
CONTROL_POINTS = [
    (-0.76,  0.00, 0.294),
    (-0.56,  0.00, 0.294),
    (-0.33,  0.00, 0.294),
    (-0.12,  0.00, 0.314),  # crossing: over
    ( 0.04,  0.15, 0.302),
    ( 0.23,  0.31, 0.296),
    ( 0.43,  0.26, 0.294),
    ( 0.52,  0.08, 0.294),
    ( 0.47, -0.12, 0.294),
    ( 0.29, -0.26, 0.294),
    ( 0.08, -0.22, 0.292),
    (-0.04, -0.10, 0.290),
    (-0.12,  0.00, 0.286),  # crossing: under
    ( 0.02, -0.31, 0.290),
    ( 0.29, -0.42, 0.292),
    ( 0.59, -0.40, 0.294),
    ( 0.72, -0.24, 0.294),
    ( 0.76, -0.06, 0.294),
    ( 0.84,  0.00, 0.294),
    ( 1.02,  0.00, 0.294),
]


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="slip_knot.blend")
    parser.add_argument("--samples-per-span", type=int, default=8)
    return parser.parse_args(argv)


def _catmull_rom(points, samples_per_span):
    """Sample an interpolating Catmull-Rom curve without external packages."""
    result = []
    padded = [points[0], *points, points[-1]]
    for i in range(1, len(padded) - 2):
        p0, p1, p2, p3 = padded[i - 1 : i + 3]
        for j in range(samples_per_span):
            t = j / samples_per_span
            t2 = t * t
            t3 = t2 * t
            xyz = []
            for axis in range(3):
                value = 0.5 * (
                    2.0 * p1[axis]
                    + (-p0[axis] + p2[axis]) * t
                    + (2.0 * p0[axis] - 5.0 * p1[axis] + 4.0 * p2[axis] - p3[axis]) * t2
                    + (-p0[axis] + 3.0 * p1[axis] - 3.0 * p2[axis] + p3[axis]) * t3
                )
                xyz.append(value)
            if not result or math.dist(result[-1], xyz) > 1e-8:
                result.append(tuple(xyz))
    result.append(points[-1])
    return result


def main():
    args = parse_args()
    if args.samples_per_span < 2:
        raise ValueError("--samples-per-span must be >= 2")

    # Work in a new in-memory copy and preserve the source .blend on disk.
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    points = _catmull_rom(CONTROL_POINTS, args.samples_per_span)
    curve_data = bpy.data.curves.new(KNOT_NAME + "_Curve", type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.resolution_u = 1
    curve_data.fill_mode = "FULL"
    curve_data.bevel_depth = 0.008
    curve_data.bevel_resolution = 5

    spline = curve_data.splines.new(type="POLY")
    spline.points.add(len(points) - 1)
    for point, xyz in zip(spline.points, points):
        point.co = (*xyz, 1.0)
    spline.use_cyclic_u = False

    knot = bpy.data.objects.new(KNOT_NAME, curve_data)
    bpy.context.collection.objects.link(knot)
    knot["knot_kind"] = "slip_release"
    knot["gauss_code"] = "+1,-1"
    knot["gauss_num_crossings"] = 1
    knot["gauss_num_components"] = 1
    knot["release_axis"] = "X"

    bpy.ops.object.select_all(action="DESELECT")
    knot.select_set(True)
    bpy.context.view_layer.objects.active = knot

    if len(curve_data.splines) != 1 or curve_data.splines[0].use_cyclic_u:
        raise RuntimeError("Slip knot must be exactly one open spline")

    bpy.ops.wm.save_as_mainfile(filepath=args.out)
    print(f"SLIP_KNOT_NAME={KNOT_NAME}")
    print(f"SLIP_KNOT_COMPONENTS=1")
    print(f"SLIP_KNOT_CROSSINGS=1")
    print(f"SLIP_KNOT_GAUSS=+1,-1")
    print(f"SLIP_KNOT_POINTS={len(points)}")
    print(f"SLIP_KNOT_BLEND={args.out}")


if __name__ == "__main__":
    main()
