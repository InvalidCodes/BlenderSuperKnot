"""Blender add-on for authoring knots interactively.

This is the GUI front end to the same registry the command line uses: pick a
knot from the library, or paste your own ASCII diagram into a Text block, and
generate the rope curve with its Gauss code attached.

Scope is deliberately *authoring only*.  Tightening is MuJoCo's job — see
:mod:`superknot.sim.tighten` — so the add-on carries no soft-body, cloth or
hook-based tightening.  Earlier versions did; that path was superseded and
removed.

Installation
------------
The add-on imports the ``superknot`` package from the repository, so link it
rather than copying it into Blender's add-on directory::

    ln -s "$PWD/superknot/blender/addon.py" \
        ~/.config/blender/4.0/scripts/addons/superknot.py

Then enable "Knot: SuperKnot Authoring" in Preferences -> Add-ons.  The panel
appears in the 3D viewport sidebar (``N``) under the "Knot" tab.
"""

import pathlib
import sys

bl_info = {
    "name": "SuperKnot Authoring",
    "author": "BlenderSuperKnot",
    "version": (0, 2, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Knot",
    "description": "Generate rope curves from ASCII knot diagrams, with Gauss codes",
    "category": "Add Mesh",
}

# `resolve()` follows the symlink above back into the repository, so the
# package next to this file is importable.
_REPO_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from superknot import knots
from superknot.ascii_diagram import KnotException
from superknot.blender.curve_builder import (
    create_rope_curve,
    parse_crossing_vertices,
    spline_point_world,
)
from superknot.knots.base import AsciiAsset

SOURCE_LIBRARY = "LIBRARY"
SOURCE_TEXT = "TEXT"


def _asset_items(self, context):
    items = []
    for family in knots.families():
        for asset in knots.family(family):
            items.append((asset.name, asset.name, f"[{family}] {asset.description}"))
    return items or [("", "no assets", "")]


class SUPERKNOT_Settings(PropertyGroup):
    source: EnumProperty(
        name="Source",
        items=[
            (SOURCE_LIBRARY, "Library", "A knot registered in superknot.knots"),
            (SOURCE_TEXT, "Text Block", "An ASCII diagram in a Blender Text block"),
        ],
        default=SOURCE_LIBRARY,
    )
    asset: EnumProperty(name="Knot", items=_asset_items)
    text_block: StringProperty(
        name="Text", description="Text block holding the ASCII diagram", default=""
    )
    scale: FloatProperty(
        name="Scale", description="Metres per grid cell", default=0.02, min=0.0001, max=1.0
    )
    z_depth: FloatProperty(
        name="Depth", description="Vertical separation at crossings", default=1.25,
        min=0.0, max=10.0,
    )
    z_bias: FloatProperty(
        name="Bias", description="-1 raises the over strand, +1 lowers the under strand",
        default=0.0, min=-1.0, max=1.0,
    )
    rope_radius: FloatProperty(
        name="Rope Radius", description="Bevel radius, metres", default=0.008,
        min=0.0001, max=1.0,
    )
    clear_scene: BoolProperty(
        name="Clear Scene", description="Delete existing objects first", default=False
    )


class SUPERKNOT_OT_generate(Operator):
    bl_idname = "superknot.generate"
    bl_label = "Generate Knot"
    bl_description = "Build the rope curve and attach its Gauss code"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.superknot
        try:
            asset = self._asset_from_settings(settings)
        except (KnotException, ValueError, KeyError) as error:
            self.report({"ERROR"}, str(error))
            print(error)
            return {"CANCELLED"}

        if settings.clear_scene:
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete(use_global=False)

        obj = create_rope_curve(asset, asset.build(), context=context)
        crossings = obj.get("gauss_num_crossings", "?")
        self.report({"INFO"}, f"{asset.name}: {crossings} crossings")
        return {"FINISHED"}

    @staticmethod
    def _asset_from_settings(settings):
        if settings.source == SOURCE_LIBRARY:
            if not settings.asset:
                raise ValueError("No knot selected")
            asset = knots.get(settings.asset)
            if isinstance(asset, AsciiAsset):
                # Let the panel's sliders override the stored drawing scale.
                from dataclasses import replace

                return replace(
                    asset,
                    scale=settings.scale,
                    z_depth=settings.z_depth,
                    z_bias=settings.z_bias,
                    rope_radius=settings.rope_radius,
                )
            return asset

        if not settings.text_block:
            raise ValueError("Choose a Text block containing an ASCII diagram")
        if settings.text_block not in bpy.data.texts:
            raise ValueError(f"Text block {settings.text_block!r} not found")
        diagram = bpy.data.texts[settings.text_block].as_string()
        return AsciiAsset(
            name=settings.text_block,
            description="Custom ASCII diagram",
            family="ascii",
            diagram=diagram,
            scale=settings.scale,
            z_depth=settings.z_depth,
            z_bias=settings.z_bias,
            rope_radius=settings.rope_radius,
            # A hand-drawn diagram is whatever it traces to; do not assert.
            expected_crossings=None,
            expected_components=None,
        )


class SUPERKNOT_OT_label_crossings(Operator):
    bl_idname = "superknot.label_crossings"
    bl_label = "Label Crossings"
    bl_description = "Drop an Empty at each Gauss crossing, named C1, C2, ..."
    bl_options = {"REGISTER", "UNDO"}

    size: FloatProperty(name="Label Size", default=0.02, min=0.001, max=10.0)
    clear_existing: BoolProperty(name="Clear Existing", default=True)

    def execute(self, context):
        obj = context.object
        if obj is None or obj.type != "CURVE":
            self.report({"ERROR"}, "Select the generated rope curve first")
            return {"CANCELLED"}

        mapping = obj.get("gauss_crossing_verts", "")
        if not mapping:
            self.report(
                {"ERROR"},
                "This object has no gauss_crossing_verts; regenerate it from a diagram",
            )
            return {"CANCELLED"}

        prefix = f"{obj.name}_C"
        if self.clear_existing:
            for other in list(context.scene.objects):
                if other.get("knot_crossing_label_owner") == obj.name:
                    bpy.data.objects.remove(other, do_unlink=True)

        created = 0
        for crossing_id, vertex_indices in parse_crossing_vertices(mapping):
            positions = [
                p
                for p in (spline_point_world(obj, i) for i in vertex_indices)
                if p is not None
            ]
            if not positions:
                continue
            centroid = sum(positions, positions[0] * 0.0) / len(positions)

            name = f"{prefix}{crossing_id}"
            label = bpy.data.objects.get(name)
            if label is None:
                label = bpy.data.objects.new(name, None)
                label.empty_display_type = "PLAIN_AXES"
                context.collection.objects.link(label)
                created += 1
            label.empty_display_size = float(self.size)
            label.location = centroid
            label["knot_crossing_label"] = int(crossing_id)
            label["knot_crossing_label_owner"] = obj.name

        self.report({"INFO"}, f"Placed {created} crossing labels")
        return {"FINISHED"}


class SUPERKNOT_PT_panel(Panel):
    bl_idname = "SUPERKNOT_PT_panel"
    bl_label = "SuperKnot"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Knot"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.superknot

        column = layout.column(align=True)
        column.prop(settings, "source", expand=True)
        if settings.source == SOURCE_LIBRARY:
            column.prop(settings, "asset")
        else:
            column.prop_search(settings, "text_block", bpy.data, "texts")

        box = layout.box()
        box.label(text="Geometry")
        box.prop(settings, "scale")
        box.prop(settings, "rope_radius")
        box.prop(settings, "z_depth")
        box.prop(settings, "z_bias")
        box.prop(settings, "clear_scene")

        layout.operator(SUPERKNOT_OT_generate.bl_idname, icon="CURVE_DATA")

        obj = context.object
        if obj is not None and "gauss_code" in obj:
            box = layout.box()
            box.label(text="Topology")
            box.label(text=f"crossings: {obj.get('gauss_num_crossings', '?')}")
            box.label(text=f"components: {obj.get('gauss_num_components', '?')}")
            box.prop(obj, '["gauss_code"]', text="Gauss")
            box.operator(SUPERKNOT_OT_label_crossings.bl_idname, icon="EMPTY_AXIS")

        layout.separator()
        layout.label(text="Tightening runs in MuJoCo:", icon="INFO")
        layout.label(text="python -m superknot run <name>")


CLASSES = (
    SUPERKNOT_Settings,
    SUPERKNOT_OT_generate,
    SUPERKNOT_OT_label_crossings,
    SUPERKNOT_PT_panel,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.superknot = bpy.props.PointerProperty(type=SUPERKNOT_Settings)


def unregister():
    if hasattr(bpy.types.Scene, "superknot"):
        del bpy.types.Scene.superknot
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
