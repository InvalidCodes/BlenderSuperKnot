"""Build a deterministic complex ASCII knot in Blender for the MuJoCo pipeline.

Run with:
    blender --background blender2mujoco_test.blend \
        --python generate_complex_ascii_knot.py -- \
        --out complex_ascii_knot.blend
"""

import argparse
import importlib.util
from pathlib import Path
import sys

import bpy


KNOT_NAME = "Complex_ASCII_15X"

# One continuous lead with 15 crossings.  The apparent blank rows/columns are
# intentional: spacing gives the exported capsules room to bend and collide.
KNOT_ASCII = r"""          /-----\
          |     |
  /-------------|---------\
  |       |     |         |
  |       |     | /---------\
  |       |     | |       | |
  |   /-\ |     | |       | |
  |   | | |     | |       | |
/-|-----|-----\ | |       | |
| |   | | |   | | |       | |
| |   | | |   | | |       | |
| |   | | |   | | |       | |
| |   | | |   | | |       \-/
| |   | | |   | | |
| |   | \-|-----|-------<
| |   |   |   | | |
| |   |   |   | | |
| |   |   |   | | |
\-|-------|-----/ |
  |   |   |   |   |
  \---/   |   |   |
          |   |   |
          .   |   |
              |   |
              |   |
              |   |
              \---/"""


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="complex_ascii_knot.blend")
    return parser.parse_args(argv)


def main():
    args = parse_args()
    if not hasattr(bpy.context.scene, "knot_tool"):
        raise RuntimeError("The Knot Generator add-on is not registered in this Blender file")

    # Preserve the source file: remove its generated rope only in the new in-memory copy.
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    text = bpy.data.texts.get(KNOT_NAME) or bpy.data.texts.new(KNOT_NAME)
    text.clear()
    text.write(KNOT_ASCII)

    settings = bpy.context.scene.knot_tool
    settings.knot_text = text.name
    settings.scale = 0.02
    settings.z_depth = 1.25
    settings.z_bias = 0.0
    settings.curve = True
    settings.extrude = False
    settings.extrude_width = 0.007
    # MuJoCo owns the tightening stage; keep the Blender source as a clean curve.
    settings.tighten = False
    settings.use_physics = False

    # Call the repository parser directly.  The globally installed copy still
    # writes Curve.use_uv_as_generated, an API removed in Blender 4.0.
    plugin_path = Path(__file__).resolve().parent / "plugin" / "knot_plugin_4.py"
    spec = importlib.util.spec_from_file_location("local_knot_plugin_4", plugin_path)
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    plugin.add_knot(
        None,
        bpy.context,
        KNOT_ASCII,
        settings.z_depth,
        settings.z_bias,
        settings.scale,
        KNOT_NAME,
    )

    knot = bpy.context.object
    if knot is None or knot.type != "MESH":
        raise RuntimeError("Expected the parser to create a MESH polyline")
    bpy.ops.object.convert(target="CURVE")
    knot = bpy.context.object
    knot.data.dimensions = "3D"
    knot.data.fill_mode = "FULL"
    knot.data.bevel_depth = settings.extrude_width
    knot.data.bevel_resolution = 6
    knot.scale = (settings.scale,) * 3

    if knot is None or knot.type != "CURVE":
        raise RuntimeError("Expected the generated knot to be a CURVE")
    knot.name = KNOT_NAME
    knot.data.name = KNOT_NAME + "_Curve"

    # Keep only the generated curve selected so the exporter chooses it explicitly.
    bpy.ops.object.select_all(action="DESELECT")
    knot.select_set(True)
    bpy.context.view_layer.objects.active = knot

    crossing_count = int(knot.get("gauss_num_crossings", 0))
    component_count = int(knot.get("gauss_num_components", 0))
    if crossing_count != 15 or component_count != 1:
        raise RuntimeError(
            f"Unexpected topology metadata: crossings={crossing_count}, "
            f"components={component_count}"
        )

    bpy.ops.wm.save_as_mainfile(filepath=args.out)
    print(f"ASCII_KNOT_NAME={KNOT_NAME}")
    print(f"ASCII_KNOT_CROSSINGS={crossing_count}")
    print(f"ASCII_KNOT_COMPONENTS={component_count}")
    print(f"ASCII_KNOT_GAUSS={knot.get('gauss_code', '')}")
    print(f"ASCII_KNOT_BLEND={args.out}")


if __name__ == "__main__":
    main()
