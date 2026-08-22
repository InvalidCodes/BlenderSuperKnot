"""``python -m superknot`` — one entry point for the whole pipeline.

    python -m superknot list                      # every knot and preset
    python -m superknot show trefoil              # diagram + Gauss code, no Blender
    python -m superknot build trefoil             # -> build/trefoil.blend
    python -m superknot export trefoil            # -> build/trefoil.xml
    python -m superknot simulate trefoil          # -> results/trefoil.mp4
    python -m superknot run trefoil               # all three
    python -m superknot run --family topology_controls --preset topology_control

Stages are separate commands on purpose: each one writes a file the next one
reads, so a failed run can be resumed instead of restarted.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from . import knots, presets, topology
from .knots.base import AsciiAsset

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = REPO_ROOT / "build"
RESULTS_DIR = REPO_ROOT / "results"
BLENDER_SCRIPTS = REPO_ROOT / "superknot" / "blender"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_assets(args) -> List:
    if getattr(args, "all", False):
        return [knots.get(n) for n in knots.names()]
    if getattr(args, "family", None):
        selected = knots.family(args.family)
        if not selected:
            raise SystemExit(
                f"No assets in family {args.family!r}. "
                f"Available: {', '.join(knots.families())}"
            )
        return selected
    if not args.asset:
        raise SystemExit("Give an asset name, --family NAME, or --all")
    return [knots.get(name) for name in args.asset]


def _preset_for(args, asset) -> presets.Preset:
    return presets.get(args.preset) if args.preset else presets.for_asset(asset)


def _run(cmd: List[str], log: Optional[Path] = None) -> None:
    printable = " ".join(str(c) for c in cmd)
    print(f"\n$ {printable}")
    if log is None:
        subprocess.run(cmd, check=True)
        return
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as handle:
        handle.write(f"$ {printable}\n\n")
        handle.flush()
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        for line in process.stdout:
            sys.stdout.write(line)
            handle.write(line)
        code = process.wait()
    if code != 0:
        raise SystemExit(f"Command failed (exit {code}); see {log}")


def _blender(args) -> str:
    exe = args.blender or shutil.which("blender")
    if not exe:
        raise SystemExit(
            "Blender not found on PATH. Install it or pass --blender /path/to/blender"
        )
    return exe


def _paths(asset):
    return (
        BUILD_DIR / f"{asset.name}.blend",
        BUILD_DIR / f"{asset.name}.xml",
        RESULTS_DIR / f"{asset.name}.mp4",
        RESULTS_DIR / f"{asset.name}.json",
    )


def _log_path(args, asset, stage: str):
    """Per-stage log file, or None when --log was not given."""
    if not args.log:
        return None
    return RESULTS_DIR / "logs" / f"{asset.name}_{stage}.log"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_list(args) -> int:
    print("Assets:")
    for fam in knots.families():
        print(f"\n  [{fam}]")
        for asset in knots.family(fam):
            print(f"    {asset.summary()}")
    print("\nPresets:")
    for name in presets.names():
        preset = presets.get(name)
        default_for = (
            f"  (default for: {', '.join(preset.default_for)})"
            if preset.default_for
            else ""
        )
        print(f"  {name:<20} {preset.description}{default_for}")
    return 0


def cmd_show(args) -> int:
    for asset in _resolve_assets(args):
        print(f"=== {asset.name} ===")
        print(f"{asset.description}")
        print(f"family           : {asset.family}")
        print(f"rope radius      : {asset.rope_radius} m")
        print(f"segment length   : {asset.segment_length} m")
        print(f"max segments     : {asset.max_segments}")
        if isinstance(asset, AsciiAsset):
            print(f"grid scale       : {asset.scale} m/cell")
            print("\ndiagram:")
            for line in asset.diagram.splitlines():
                print(f"  {line}")
        polylines = asset.build()
        print(f"\ncomponents       : {len(polylines)}")
        print(f"points           : {sum(len(p) for p in polylines)}")
        print("topology:")
        for key, value in sorted(asset.topology.items()):
            print(f"  {key:<24} {value}")
        if isinstance(asset, AsciiAsset):
            sequence = asset.trace().gauss_code().sequences[0]
            dims = {p: topology.coloring_dimension(sequence, p) for p in (3, 5, 7)}
            colorable = [p for p, d in dims.items() if d >= 2]
            verdict = (
                f"{'/'.join(str(p) for p in colorable)}-colourable -> not the unknot"
                if colorable
                else "no p-colouring found for p in 3,5,7 (inconclusive)"
            )
            print(f"  {'fox_colouring':<24} {dims}  {verdict}")
        print()
    return 0


def cmd_build(args) -> int:
    for asset in _resolve_assets(args):
        blend, _, _, _ = _paths(asset)
        blend.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                _blender(args),
                "--background",
                "--factory-startup",
                "--python-exit-code",
                "1",
                "--python",
                str(BLENDER_SCRIPTS / "build_asset.py"),
                "--",
                "--asset",
                asset.name,
                "--out",
                str(blend),
            ],
            _log_path(args, asset, "build"),
        )
        print(f"built {blend}")
    return 0


def cmd_export(args) -> int:
    for asset in _resolve_assets(args):
        blend, xml, _, _ = _paths(asset)
        if not blend.exists():
            raise SystemExit(f"{blend} does not exist; run `build {asset.name}` first")
        cfg = _preset_for(args, asset).export
        _run(
            [
                _blender(args),
                "--background",
                str(blend),
                "--python-exit-code",
                "1",
                "--python",
                str(BLENDER_SCRIPTS / "export_mjcf.py"),
                "--",
                "--out", str(xml),
                "--object", asset.name,
                "--rope-friction", cfg.rope_friction,
                "--table-friction", cfg.table_friction,
                "--joint-damping", str(cfg.joint_damping),
                "--joint-stiffness", str(cfg.joint_stiffness),
                "--rope-density", str(cfg.rope_density),
                "--table-clearance", str(cfg.table_clearance),
                "--visual-radius-scale", str(cfg.visual_radius_scale),
                "--visual-overlap-frac", str(cfg.visual_overlap_frac),
                "--texture-repeat-u", str(cfg.texture_repeat_u),
                "--texture-repeat-v", str(cfg.texture_repeat_v),
                "--texture", cfg.texture,
            ],
            _log_path(args, asset, "export"),
        )
        print(f"exported {xml}")
    return 0


def cmd_simulate(args) -> int:
    for asset in _resolve_assets(args):
        _, xml, video, metrics = _paths(asset)
        if not xml.exists():
            raise SystemExit(f"{xml} does not exist; run `export {asset.name}` first")
        preset = _preset_for(args, asset)
        sim_argv = [
            "--model", str(xml),
            "--preset", preset.name,
            "--metrics-out", str(metrics),
        ]
        if not args.no_video:
            sim_argv += ["--video", str(video)]
        if args.viewer:
            sim_argv.append("--viewer")

        if args.python:
            _run(
                [args.python, "-m", "superknot.sim.tighten", *sim_argv],
                _log_path(args, asset, "simulate"),
            )
        else:
            try:
                from .sim import tighten
            except ImportError as error:
                raise SystemExit(
                    f"Cannot import the MuJoCo stage ({error}). Either install "
                    "'pip install -r requirements.txt' here, or point at another "
                    "interpreter with --python /path/to/env/bin/python"
                ) from None
            tighten.main(sim_argv)
        print(f"simulated {asset.name} -> {video if not args.no_video else metrics}")
    return 0


def cmd_run(args) -> int:
    assets = _resolve_assets(args)
    summary = []
    for asset in assets:
        print(f"\n########## {asset.name} ##########")
        single = argparse.Namespace(**vars(args))
        single.asset = [asset.name]
        single.all = False
        single.family = None
        cmd_build(single)
        cmd_export(single)
        cmd_simulate(single)

        _, _, video, metrics = _paths(asset)
        entry = {"asset": asset.name, "family": asset.family, "video": str(video)}
        if metrics.exists():
            with metrics.open(encoding="utf-8") as handle:
                entry["ropes"] = json.load(handle)["ropes"]
        summary.append(entry)

    if len(assets) > 1:
        index = RESULTS_DIR / "summary.json"
        index.parent.mkdir(parents=True, exist_ok=True)
        with index.open("w", encoding="utf-8") as handle:
            json.dump({"runs": summary}, handle, indent=2)
        print(f"\nWrote {index}")
    return 0


def cmd_presets(args) -> int:
    print(json.dumps({n: presets.get(n).to_dict() for n in presets.names()}, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="superknot",
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run `python -m superknot list` to see every knot and preset.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_selection(sp):
        sp.add_argument("asset", nargs="*", help="asset name(s)")
        sp.add_argument("--family", help="every asset in this family")
        sp.add_argument("--all", action="store_true", help="every registered asset")

    def add_common(sp):
        sp.add_argument("--preset", choices=presets.names(),
                        help="parameter set (default: the asset family's preset)")
        sp.add_argument("--blender", help="path to the Blender executable")
        sp.add_argument("--python",
                        help="interpreter for the MuJoCo stage, e.g. a conda env's python")
        sp.add_argument("--log", action="store_true",
                        help="tee stage output to results/logs/<asset>.log")
        sp.add_argument("--viewer", action="store_true",
                        help="interactive viewer instead of offscreen rendering")
        sp.add_argument("--no-video", action="store_true", help="skip video rendering")

    p = sub.add_parser("list", help="list every asset and preset")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="print a knot's diagram, Gauss code and metadata")
    add_selection(p)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("build", help="asset -> .blend (needs Blender)")
    add_selection(p)
    add_common(p)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("export", help=".blend -> MJCF .xml (needs Blender)")
    add_selection(p)
    add_common(p)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("simulate", help="MJCF -> tightening video (needs MuJoCo)")
    add_selection(p)
    add_common(p)
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("run", help="build + export + simulate")
    add_selection(p)
    add_common(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("presets", help="dump every preset as JSON")
    p.set_defaults(func=cmd_presets)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
