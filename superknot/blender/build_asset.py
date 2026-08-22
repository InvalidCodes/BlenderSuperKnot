"""Build any registered knot asset into a ``.blend`` file.

    blender --background --python superknot/blender/build_asset.py -- \
        --asset trefoil --out build/trefoil.blend

The scene is created from scratch, so no template ``.blend`` is required.  The
generated object is a single CURVE holding one POLY spline per rope component,
carrying the asset's topology metadata as custom properties for the exporter
and for downstream labelling.
"""

import argparse
import json
import pathlib
import sys

# Blender puts the script's own directory on sys.path, not the repository root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import bpy

from superknot.blender import _bootstrap
from superknot.blender.curve_builder import clear_scene, create_rope_curve

from superknot import knots


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="build_asset", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--asset", required=True, help="registered asset name")
    parser.add_argument("--out", required=True, help="output .blend path")
    parser.add_argument(
        "--metadata-out", default=None, help="optional JSON dump of the asset metadata"
    )
    return parser.parse_args(_bootstrap.script_args(argv))


def main():
    args = parse_args()
    asset = knots.get(args.asset)

    clear_scene()
    polylines = asset.build()
    obj = create_rope_curve(asset, polylines)

    if obj.type != "CURVE" or any(s.use_cyclic_u for s in obj.data.splines):
        raise RuntimeError(f"{asset.name}: expected open curve splines")

    bpy.ops.wm.save_as_mainfile(filepath=args.out)

    metadata = {
        "asset": asset.name,
        "family": asset.family,
        "description": asset.description,
        "components": len(polylines),
        "points": [len(line) for line in polylines],
        "rope_radius": asset.rope_radius,
        "segment_length": asset.segment_length,
        "blend": args.out,
        "topology": {k: v for k, v in asset.topology.items()},
    }
    if args.metadata_out:
        with open(args.metadata_out, "w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2)

    print(f"BUILD_ASSET={asset.name}")
    print(f"BUILD_COMPONENTS={len(polylines)}")
    print(f"BUILD_POINTS={sum(len(line) for line in polylines)}")
    print(f"BUILD_CROSSINGS={asset.topology.get('gauss_num_crossings', 'n/a')}")
    print(f"BUILD_GAUSS={asset.topology.get('gauss_code', 'n/a')}")
    print(f"BUILD_BLEND={args.out}")


if __name__ == "__main__":
    main()
