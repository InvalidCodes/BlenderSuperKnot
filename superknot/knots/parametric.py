"""Knots defined by control points or closed-form curves.

Use this library when a character grid is the wrong tool: smooth splines
through hand-placed control points, closed-form parametrisations, or rigid
re-projections of an existing curve.  Because there is no diagram to trace,
topology labels are *declared* in ``labels`` rather than derived — keep them
honest.

The ``topology_controls`` family is the paper's ablation set.  The first three
share one planar trefoil shadow and differ only in the over/under height at its
crossings, so appearance is held fixed while topology changes.  The last three
are rigid rotations of the same right-handed trefoil, chosen so the XY
projection shows 4, 5 and 3 crossings respectively — topology held fixed while
appearance changes.
"""

from __future__ import annotations

import math
from typing import List, Tuple

from . import register
from .base import ParametricAsset, Point, catmull_rom, rotate_xyz

__all__ = ["SLIP_KNOT_CONTROL_POINTS", "TOPOLOGY_CONTROLS"]

TWO_PI = 2.0 * math.pi


# ---------------------------------------------------------------------------
# Slip knot: one removable curl, released by pulling both ends.
# ---------------------------------------------------------------------------

# The centerline visits the crossing twice.  The first visit is the overpass
# and the second is the underpass; their 28 mm separation exceeds the intended
# 16 mm rope diameter.
SLIP_KNOT_CONTROL_POINTS: List[Point] = [
    (-0.76, 0.00, 0.294),
    (-0.56, 0.00, 0.294),
    (-0.33, 0.00, 0.294),
    (-0.12, 0.00, 0.314),  # crossing: over
    (0.04, 0.15, 0.302),
    (0.23, 0.31, 0.296),
    (0.43, 0.26, 0.294),
    (0.52, 0.08, 0.294),
    (0.47, -0.12, 0.294),
    (0.29, -0.26, 0.294),
    (0.08, -0.22, 0.292),
    (-0.04, -0.10, 0.290),
    (-0.12, 0.00, 0.286),  # crossing: under
    (0.02, -0.31, 0.290),
    (0.29, -0.42, 0.292),
    (0.59, -0.40, 0.294),
    (0.72, -0.24, 0.294),
    (0.76, -0.06, 0.294),
    (0.84, 0.00, 0.294),
    (1.02, 0.00, 0.294),
]


def _slip_knot(samples_per_span: int = 8) -> List[List[Point]]:
    return [catmull_rom(SLIP_KNOT_CONTROL_POINTS, samples_per_span)]


register(
    ParametricAsset(
        name="slip_knot",
        description="Removable curl; pulling both ends performs a Reidemeister-I release",
        family="slip",
        sampler=_slip_knot,
        rope_radius=0.008,
        segment_length=0.018,
        max_segments=100,
        base_height=None,  # authored at its final height already
        labels={
            "gauss_code": "+1,-1",
            "gauss_num_crossings": 1,
            "gauss_num_components": 1,
            "knot_kind": "slip_release",
            "release_axis": "X",
        },
    )
)


# ---------------------------------------------------------------------------
# Topology controls: trefoil shadow with switched crossings, and rigid
# re-projections of the right-handed trefoil.
# ---------------------------------------------------------------------------

SAMPLES = 420
XY_SCALE = 0.10
Z_SCALE = 0.04
# A tiny cut around t=0 creates two physical endpoints.  It removes none of the
# three diagram crossings and fixes an outside-closure convention for the
# topology labels below.
CUT_HALF_WIDTH = 0.075

# The standard trefoil projection has crossings at these parameter pairs.
# Switching either height pair unknots the diagram.
CROSSING_PARAMETER_PAIRS = (
    (0.270917, 3.917873),
    (1.823478, 4.459707),
    (2.365312, 6.012268),
)


def _periodic_distance(a: float, b: float) -> float:
    return abs((a - b + math.pi) % TWO_PI - math.pi)


def _smooth_bump(t: float, center: float, radius: float = 0.24) -> float:
    distance = _periodic_distance(t, center)
    if distance >= radius:
        return 0.0
    # Raised cosine: value and first derivative are both zero at the boundary.
    return 0.5 + 0.5 * math.cos(math.pi * distance / radius)


def _base_point(t: float, operation: str) -> Point:
    x = math.sin(t) + 2.0 * math.sin(2.0 * t)
    y = math.cos(t) - 2.0 * math.cos(2.0 * t)
    z = -0.65 * math.sin(3.0 * t)

    if operation == "mirror_all_crossings":
        z = -z
    elif operation == "switch_crossing_0":
        first, second = CROSSING_PARAMETER_PAIRS[0]
        weight = max(_smooth_bump(t, first), _smooth_bump(t, second))
        z *= 1.0 - 2.0 * weight

    return (XY_SCALE * x, XY_SCALE * y, Z_SCALE * z)


def _make_control_sampler(operation: str, rotation: Tuple[float, float, float]):
    def sampler() -> List[List[Point]]:
        start = CUT_HALF_WIDTH
        end = TWO_PI - CUT_HALF_WIDTH
        points = []
        for index in range(SAMPLES):
            alpha = index / (SAMPLES - 1)
            t = start + alpha * (end - start)
            points.append(rotate_xyz(_base_point(t, operation), rotation))
        return [points]

    return sampler


TOPOLOGY_CONTROLS = (
    dict(
        name="right_trefoil",
        description="Right-handed trefoil, 3 crossings in the XY projection",
        operation="base",
        crossings=3,
        closure_topology="3_1_right",
    ),
    dict(
        name="three_crossing_unknot",
        description="Same shadow as right_trefoil, one crossing switched: unknot",
        operation="switch_crossing_0",
        crossings=3,
        closure_topology="0_1",
    ),
    dict(
        name="left_trefoil",
        description="Left-handed trefoil: every crossing of right_trefoil mirrored",
        operation="mirror_all_crossings",
        crossings=3,
        closure_topology="3_1_left",
    ),
    dict(
        name="right_trefoil_r1",
        description="right_trefoil rotated so the projection shows 4 crossings",
        operation="equivalent_projection_3_to_4",
        crossings=4,
        closure_topology="3_1_right",
        # Along the linear rotation path from the base view the detected
        # crossing-count sequence is exactly 3 -> 4.
        rotation=(1.186284, 1.073419, -0.283626),
    ),
    dict(
        name="right_trefoil_r2",
        description="right_trefoil rotated so the projection shows 5 crossings",
        operation="equivalent_projection_3_to_5",
        crossings=5,
        closure_topology="3_1_right",
        rotation=(1.575283, -2.420636, -3.084554),
    ),
    dict(
        name="right_trefoil_r3",
        description="right_trefoil rotated, projection still shows 3 crossings",
        operation="equivalent_projection_3_to_3",
        crossings=3,
        closure_topology="3_1_right",
        rotation=(0.0, 0.30, 0.0),
    ),
)


for _index, _spec in enumerate(TOPOLOGY_CONTROLS, start=1):
    _operation = _spec["operation"]
    # Rotations only re-project the base curve, so they must not also switch
    # crossings; feed the base operation to the sampler in that case.
    _sampler_op = _operation if _operation in {
        "base",
        "switch_crossing_0",
        "mirror_all_crossings",
    } else "base"
    register(
        ParametricAsset(
            name=_spec["name"],
            description=_spec["description"],
            family="topology_controls",
            sampler=_make_control_sampler(_sampler_op, _spec.get("rotation", (0.0, 0.0, 0.0))),
            rope_radius=0.008,
            segment_length=0.022,
            max_segments=140,
            base_height=0.31,
            labels={
                "control_index": _index,
                "diagram_operation": _operation,
                "closure_topology": _spec["closure_topology"],
                "gauss_num_crossings": _spec["crossings"],
                "gauss_num_components": 1,
                "closure_convention": "connect_endpoints_across_projection_exterior",
                "projection_axis": "+Z",
            },
        )
    )
