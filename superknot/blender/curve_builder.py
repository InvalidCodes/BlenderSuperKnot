"""Turn asset centerlines into a Blender curve object.

Shared by the headless builder (:mod:`superknot.blender.build_asset`) and the
interactive add-on (:mod:`superknot.blender.addon`) so both produce byte-for-byte
comparable geometry and metadata.
"""

from __future__ import annotations

from typing import List, Sequence

import bpy

from ..knots.base import Asset, Point

ROPE_COLOR = (0.52, 0.24, 0.075, 1.0)
BEVEL_RESOLUTION = 5


def clear_scene() -> None:
    """Delete every object and any datablock left without users."""
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.curves, bpy.data.meshes, bpy.data.materials):
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)


def create_rope_curve(
    asset: Asset, polylines: Sequence[Sequence[Point]], context=None
) -> bpy.types.Object:
    """Create one CURVE object with a POLY spline per rope component."""
    context = context or bpy.context

    curve_data = bpy.data.curves.new(asset.name + "_Curve", type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.resolution_u = 1
    curve_data.fill_mode = "FULL"
    curve_data.bevel_depth = asset.rope_radius
    curve_data.bevel_resolution = BEVEL_RESOLUTION

    for points in polylines:
        spline = curve_data.splines.new(type="POLY")
        spline.points.add(len(points) - 1)
        for control_point, xyz in zip(spline.points, points):
            control_point.co = (*xyz, 1.0)
        # Every rope in this pipeline is open: it needs two free ends to pull.
        spline.use_cyclic_u = False

    obj = bpy.data.objects.new(asset.name, curve_data)
    context.collection.objects.link(obj)

    # Geometry the exporter reads back, plus the topology labels.
    obj["rope_radius"] = asset.rope_radius
    obj["segment_length"] = asset.segment_length
    obj["max_segments"] = asset.max_segments
    for key, value in asset.topology.items():
        obj[key] = value

    material = bpy.data.materials.new(asset.name + "_RopeMaterial")
    material.diffuse_color = ROPE_COLOR
    curve_data.materials.append(material)

    # Leave only the rope selected so the exporter picks it without guessing.
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj


def spline_point_world(obj: bpy.types.Object, index: int):
    """World position of the ``index``-th control point, counted across splines.

    The index space matches the order :meth:`Asset.centerlines` emits points,
    which is what ``gauss_crossing_verts`` refers to.
    """
    offset = 0
    for spline in obj.data.splines:
        count = len(spline.points)
        if index < offset + count:
            local = spline.points[index - offset].co
            return obj.matrix_world @ local.to_3d()
        offset += count
    return None


def parse_crossing_vertices(mapping: str) -> List[tuple]:
    """Parse ``"1:3,15;2:7,21"`` into ``[(1, [3, 15]), (2, [7, 21])]``."""
    result = []
    for part in mapping.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        raw_id, raw_verts = part.split(":", 1)
        try:
            crossing_id = int(raw_id)
        except ValueError:
            continue
        verts = [int(t) for t in raw_verts.split(",") if t.strip().lstrip("-").isdigit()]
        if verts:
            result.append((crossing_id, verts))
    return sorted(result)
