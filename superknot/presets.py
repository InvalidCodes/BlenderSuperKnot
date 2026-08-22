"""Named parameter sets for the export and simulation stages.

A preset holds everything that is a *property of the experiment* — friction,
solver timestep, how hard to pull, how long to record.  Everything that is a
property of the *knot* — rope radius, capsule length, segment budget — lives on
the asset instead (see :mod:`superknot.knots.base`).  Keeping the two apart is
what lets any knot run under any preset.

Add an experiment by adding one :class:`Preset` to :data:`PRESETS`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Dict, List

__all__ = ["ExportConfig", "SimConfig", "Preset", "PRESETS", "get", "names", "for_asset"]


@dataclass(frozen=True)
class ExportConfig:
    """Physics and material parameters baked into the generated MJCF."""

    #: MuJoCo ``friction`` triplet (sliding, torsional, rolling).
    rope_friction: str = "0.8 0.02 0.001"
    table_friction: str = "0.9 0.02 0.001"
    joint_damping: float = 0.05
    joint_stiffness: float = 0.002
    rope_density: float = 1000.0
    #: Gap between the rope's lowest point and the table plane, in metres.
    table_clearance: float = 0.0005
    #: Visual-only: skin radius multiplier and seam overlap fraction.
    visual_radius_scale: float = 1.0
    visual_overlap_frac: float = 0.02
    texture_repeat_u: float = 200.0
    texture_repeat_v: float = 20.0
    #: Rope texture; empty string selects the built-in procedural checker.
    texture: str = "auto"


@dataclass(frozen=True)
class SimConfig:
    """How the rope is pulled and how the run is recorded."""

    #: Physics timestep in seconds.
    timestep: float = 0.0005
    #: Hard cap on simulation steps; the run also stops on its own criteria.
    steps: int = 50000
    #: Steps of free settling before pulling starts.
    pre_settle_steps: int = 0
    #: Steps of free settling after pulling stops.
    settle_steps: int = 2000

    #: Stop pulling when the rope is taut, rather than after a fixed distance.
    pull_until_taut: bool = True
    #: Fixed pull distance per endpoint, metres.  Used when not pulling to taut.
    pull_distance: float = 1.20
    #: Runaway guard in taut mode: never pull further than this per endpoint.
    max_pull_distance: float = 1.70
    #: Metres the mocap target advances per step.
    pull_speed: float = 0.00030

    #: Taut when the filtered weld tension holds above this for N steps.
    taut_force: float = 10.0
    taut_hold_steps: int = 20
    #: EMA factor applied to weld tension; small values reject collision spikes.
    taut_force_ema_alpha: float = 0.02

    #: Safety limits.  Deliberately above `taut_force`: these protect the
    #: flexible weld, they are not the normal completion condition.
    max_pull_force: float = 30.0
    max_endpoint_error: float = 0.04
    force_limit_hold_steps: int = 20
    #: Stop pulling every rope as soon as any one of them goes taut.
    stop_all_on_tension: bool = True

    #: Optional vertical lift before pulling, metres (0 disables).
    lift_z: float = 0.0
    lift_steps: int = 1000

    #: Video output.
    resolution: str = "960x540"
    fps: float = 60.0
    #: Skip lift and pre-settle frames so the clip starts at the pull.
    hide_setup: bool = True


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    export: ExportConfig = field(default_factory=ExportConfig)
    sim: SimConfig = field(default_factory=SimConfig)
    #: Asset families this preset is the natural default for.
    default_for: tuple = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "export": asdict(self.export),
            "sim": asdict(self.sim),
        }


# Frictions tuned so strands slide against each other instead of locking up on
# first contact; shared by the two release experiments.
_LOW_FRICTION = ExportConfig(
    rope_friction="0.35 0.01 0.001",
    table_friction="0.25 0.01 0.001",
    joint_stiffness=0.001,
)


PRESETS: Dict[str, Preset] = {
    p.name: p
    for p in [
        Preset(
            name="tighten",
            description="Pull both ends until the knot is taut, then let it settle",
            default_for=("ascii", "misc"),
        ),
        Preset(
            name="slip_release",
            description="Short, gentle pull that lets a slip knot run out straight",
            export=_LOW_FRICTION,
            sim=SimConfig(
                timestep=0.0005,
                steps=6000,
                pre_settle_steps=500,
                settle_steps=1000,
                pull_until_taut=True,
                max_pull_distance=1.30,
                pull_speed=0.00030,
                taut_force=3.0,
                taut_hold_steps=50,
                max_pull_force=20.0,
                resolution="640x360",
                fps=30.0,
            ),
            default_for=("slip",),
        ),
        Preset(
            name="topology_control",
            description=(
                "Fixed-distance endpoint pull with a high force ceiling; the "
                "ablation protocol for the topology-control family"
            ),
            export=_LOW_FRICTION,
            sim=SimConfig(
                timestep=0.0005,
                steps=9000,
                pre_settle_steps=500,
                settle_steps=1000,
                pull_until_taut=False,
                pull_distance=1.20,
                max_pull_distance=1.70,
                pull_speed=0.00030,
                taut_force=3.0,
                taut_hold_steps=50,
                max_pull_force=80.0,
                max_endpoint_error=0.08,
                resolution="640x360",
                fps=30.0,
            ),
            default_for=("topology_controls",),
        ),
        Preset(
            name="smoke",
            description="Fast, low-resolution run for checking the pipeline end to end",
            sim=SimConfig(
                timestep=0.0005,
                steps=1200,
                pre_settle_steps=100,
                settle_steps=100,
                pull_until_taut=True,
                max_pull_distance=0.30,
                pull_speed=0.00050,
                taut_force=3.0,
                taut_hold_steps=20,
                max_pull_force=40.0,
                max_endpoint_error=0.08,
                resolution="320x180",
                fps=15.0,
            ),
        ),
    ]
}


def names() -> List[str]:
    return sorted(PRESETS)


def get(name: str) -> Preset:
    try:
        return PRESETS[name]
    except KeyError:
        raise KeyError(
            f"Unknown preset {name!r}. Available: {', '.join(names())}"
        ) from None


def for_asset(asset) -> Preset:
    """The preset that is the natural default for ``asset``'s family."""
    for preset in PRESETS.values():
        if asset.family in preset.default_for:
            return preset
    return PRESETS["tighten"]


def override(preset: Preset, **sim_overrides) -> Preset:
    """Return a copy of ``preset`` with the given :class:`SimConfig` fields set."""
    clean = {k: v for k, v in sim_overrides.items() if v is not None}
    if not clean:
        return preset
    return replace(preset, sim=replace(preset.sim, **clean))
