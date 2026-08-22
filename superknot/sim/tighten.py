"""Pull a rope's endpoints in MuJoCo, record a video, and report what happened.

    python -m superknot.sim.tighten --model build/trefoil.xml \
        --video results/trefoil.mp4 --preset tighten

The exported model welds each rope endpoint to a mocap body.  Every step the
mocap targets advance along the rope's outward tangent, dragging the ends
apart.  Two things can stop the pull:

*taut*
    The weld tension, smoothed by an EMA so collision spikes do not trigger it,
    stays above ``taut_force`` for ``taut_hold_steps`` consecutive steps.  This
    is the normal completion condition.

*safety_limit*
    Endpoint tracking error or raw force exceeds a hard ceiling.  The welds are
    compliant, so this guards against a pathological pull; reaching it means
    the rope jammed rather than tightened.

The reported ``arc/chord`` ratio and maximum deviation from the endpoint chord
quantify how straight the rope ended up: a released slip knot approaches 1.0,
a jammed knot stays far above it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

import numpy as np

from superknot import presets

# MuJoCo picks its GL backend at import time, so this has to happen first.
if "MUJOCO_GL" not in os.environ:
    os.environ["MUJOCO_GL"] = "glfw" if os.environ.get("DISPLAY") else "egl"

import mujoco as mj  # noqa: E402


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="superknot.sim.tighten", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--model", required=True, help="MJCF .xml to simulate")
    parser.add_argument("--video", default=None, help="output .mp4 (omit to skip video)")
    parser.add_argument("--metrics-out", default=None, help="output .json with metrics")
    parser.add_argument(
        "--preset",
        default="tighten",
        choices=presets.names(),
        help="named parameter set (default: tighten)",
    )
    parser.add_argument(
        "--viewer",
        action="store_true",
        help="open the interactive viewer instead of rendering offscreen",
    )

    over = parser.add_argument_group("preset overrides")
    over.add_argument("--steps", type=int, default=None)
    over.add_argument("--timestep", type=float, default=None)
    over.add_argument("--pull-speed", type=float, default=None)
    over.add_argument("--pull-distance", type=float, default=None)
    over.add_argument("--max-pull-distance", type=float, default=None)
    over.add_argument("--taut-force", type=float, default=None)
    over.add_argument("--max-pull-force", type=float, default=None)
    over.add_argument("--max-endpoint-error", type=float, default=None)
    over.add_argument("--settle-steps", type=int, default=None)
    over.add_argument("--pre-settle-steps", type=int, default=None)
    over.add_argument("--resolution", default=None)
    over.add_argument("--fps", type=float, default=None)
    over.add_argument(
        "--pull-until-taut",
        dest="pull_until_taut",
        action="store_true",
        default=None,
    )
    over.add_argument(
        "--pull-fixed-distance", dest="pull_until_taut", action="store_false"
    )

    return parser.parse_args(argv)


def config_from_args(args) -> presets.SimConfig:
    preset = presets.get(args.preset)
    return presets.override(
        preset,
        steps=args.steps,
        timestep=args.timestep,
        pull_speed=args.pull_speed,
        pull_distance=args.pull_distance,
        max_pull_distance=args.max_pull_distance,
        taut_force=args.taut_force,
        max_pull_force=args.max_pull_force,
        max_endpoint_error=args.max_endpoint_error,
        settle_steps=args.settle_steps,
        pre_settle_steps=args.pre_settle_steps,
        resolution=args.resolution,
        fps=args.fps,
        pull_until_taut=args.pull_until_taut,
    ).sim


# ---------------------------------------------------------------------------
# Model introspection
# ---------------------------------------------------------------------------


def is_rope_segment(name: str) -> bool:
    """Segment bodies are ``rope0_seg_###``; bare ``seg_###`` is the old form."""
    return bool(name) and (name.startswith("seg_") or "_seg_" in name)


@dataclass
class RopeEnds:
    """One rope's two pullable endpoints and the state of its pull."""

    prefix: str
    left_idx: int
    right_idx: int
    left_body: int
    right_body: int
    segments: List[str]
    dir_left: np.ndarray
    dir_right: np.ndarray
    weld_eq_ids: List[int]

    progress: float = 0.0
    max_endpoint_error: float = 0.0
    max_pull_force: float = 0.0
    pull_force_ema: float = 0.0
    tension_limited_steps: int = 0
    taut_consecutive_steps: int = 0
    limit_consecutive_steps: int = 0
    pull_stopped: bool = False
    stop_reason: str = ""

    @property
    def label(self) -> str:
        return self.prefix or "rope"


def body_names(model) -> List[str]:
    return [
        mj.mj_id2name(model, mj.mjtObj.mjOBJ_BODY, i) or f"body_{i}"
        for i in range(model.nbody)
    ]


def rope_bounds(model, data, names):
    positions = np.array(
        [
            data.xpos[mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, n)]
            for n in names
            if is_rope_segment(n)
        ]
    )
    if positions.size == 0:
        raise SystemExit(
            "No rope segment bodies found; expected 'seg_###' or 'rope0_seg_###'."
        )
    lo, hi = positions.min(axis=0), positions.max(axis=0)
    return (lo + hi) / 2.0, float((hi - lo).max())


def find_rope_ends(model, data, names) -> List[RopeEnds]:
    """Pair up the mocap bodies and derive each rope's outward pull direction."""
    pairs = []
    for left in sorted(n for n in names if n.endswith("_mocap_left")):
        right = left[: -len("mocap_left")] + "mocap_right"
        if right in names:
            pairs.append((left, right))
    if "mocap_left" in names and "mocap_right" in names:
        pairs.append(("mocap_left", "mocap_right"))
    if not pairs:
        raise SystemExit(
            "No mocap bodies found; expected 'mocap_left/right' or 'rope0_mocap_left/right'."
        )

    mocap_map = model.body_mocapid
    ends = []
    for left_name, right_name in pairs:
        prefix = ""
        if left_name.endswith("_mocap_left"):
            prefix = left_name[: -len("_mocap_left")]

        seg_prefix = f"{prefix}_seg_" if prefix else "seg_"
        segments = sorted(n for n in names if n.startswith(seg_prefix))
        if len(segments) < 2:
            raise SystemExit(f"Rope {prefix or 'rope'} has fewer than two segments")

        ids = [mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, n) for n in segments]
        # Pointing from inside the rope towards each free end is exactly the
        # outward tangent the end feels when pulled.
        vec_left = data.xpos[ids[0]] - data.xpos[ids[1]]
        vec_right = data.xpos[ids[-1]] - data.xpos[ids[-2]]

        def unit(vec, fallback):
            norm = np.linalg.norm(vec)
            return vec / norm if norm > 1e-6 else np.array(fallback)

        eq_prefix = f"{prefix}_" if prefix else ""
        ends.append(
            RopeEnds(
                prefix=prefix,
                left_idx=int(mocap_map[mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, left_name)]),
                right_idx=int(mocap_map[mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, right_name)]),
                left_body=ids[0],
                right_body=ids[-1],
                segments=segments,
                dir_left=unit(vec_left, [-1.0, 0.0, 0.0]),
                dir_right=unit(vec_right, [1.0, 0.0, 0.0]),
                weld_eq_ids=[
                    mj.mj_name2id(model, mj.mjtObj.mjOBJ_EQUALITY, eq_prefix + "weld_left"),
                    mj.mj_name2id(model, mj.mjtObj.mjOBJ_EQUALITY, eq_prefix + "weld_right"),
                ],
            )
        )
    return ends


# ---------------------------------------------------------------------------
# The pull
# ---------------------------------------------------------------------------


class Puller:
    """Advances the mocap targets and decides when to stop."""

    def __init__(self, model, data, ends: List[RopeEnds], cfg: presets.SimConfig,
                 distance_cap: float):
        self.model, self.data, self.ends, self.cfg = model, data, ends, cfg
        self.distance_cap = distance_cap
        self.lift_steps = cfg.lift_steps if cfg.lift_z > 0 else 0
        self.lift_speed = cfg.lift_z / self.lift_steps if self.lift_steps else 0.0
        self.start_step = self.lift_steps + cfg.pre_settle_steps

    def weld_force(self, eq_id: int) -> float:
        """Translational weld force in newtons, 0 if the constraint is inactive."""
        if eq_id < 0 or self.data.nefc <= 0:
            return 0.0
        types = self.data.efc_type[: self.data.nefc]
        ids = self.data.efc_id[: self.data.nefc]
        rows = np.flatnonzero(
            (types == mj.mjtConstraint.mjCNSTR_EQUALITY) & (ids == eq_id)
        )
        if rows.size == 0:
            return 0.0
        # A weld contributes six rows; the first three are translation.
        return float(np.linalg.norm(self.data.efc_force[rows[:3]]))

    def step(self, index: int) -> None:
        cfg, data = self.cfg, self.data
        if self.lift_steps and index < self.lift_steps:
            for end in self.ends:
                data.mocap_pos[end.left_idx][2] += self.lift_speed
                data.mocap_pos[end.right_idx][2] += self.lift_speed
            return
        if index < self.start_step:
            return

        for end in self.ends:
            if end.progress >= self.distance_cap or end.pull_stopped:
                continue

            error = max(
                float(np.linalg.norm(data.mocap_pos[end.left_idx] - data.xpos[end.left_body])),
                float(np.linalg.norm(data.mocap_pos[end.right_idx] - data.xpos[end.right_body])),
            )
            force = max(self.weld_force(eq) for eq in end.weld_eq_ids)
            end.max_endpoint_error = max(end.max_endpoint_error, error)
            end.max_pull_force = max(end.max_pull_force, force)
            end.pull_force_ema = (
                (1.0 - cfg.taut_force_ema_alpha) * end.pull_force_ema
                + cfg.taut_force_ema_alpha * force
            )

            if cfg.pull_until_taut and end.pull_force_ema >= cfg.taut_force:
                end.tension_limited_steps += 1
                end.taut_consecutive_steps += 1
                if end.taut_consecutive_steps >= cfg.taut_hold_steps:
                    self._stop(end, "taut")
                    continue
            else:
                end.taut_consecutive_steps = 0

            if error >= cfg.max_endpoint_error or force >= cfg.max_pull_force:
                end.limit_consecutive_steps += 1
                if end.limit_consecutive_steps >= cfg.force_limit_hold_steps:
                    self._stop(end, "safety_limit")
                continue
            end.limit_consecutive_steps = 0

            advance = min(cfg.pull_speed, self.distance_cap - end.progress)
            data.mocap_pos[end.left_idx] += end.dir_left * advance
            data.mocap_pos[end.right_idx] += end.dir_right * advance
            end.progress += advance

    def _stop(self, end: RopeEnds, reason: str) -> None:
        end.pull_stopped = True
        end.stop_reason = reason
        if self.cfg.stop_all_on_tension:
            for other in self.ends:
                other.pull_stopped = True
                if other is not end and not other.stop_reason:
                    other.stop_reason = f"peer_{reason}"

    def finished(self) -> bool:
        return all(
            end.pull_stopped or end.progress >= self.distance_cap - self.cfg.pull_speed
            for end in self.ends
        )


def rope_metrics(model, data, end: RopeEnds) -> Dict[str, float]:
    """Arc length, chord and straightness of one rope in its final pose."""
    points = np.asarray(
        [data.xpos[mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, n)] for n in end.segments]
    )
    arc = float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())
    chord_vec = points[-1] - points[0]
    chord = float(np.linalg.norm(chord_vec))
    if chord <= 1e-9:
        return {"arc_m": arc, "chord_m": chord, "arc_chord_ratio": float("inf"),
                "max_line_deviation_m": float("inf")}
    line_dir = chord_vec / chord
    offsets = points - points[0]
    perpendicular = offsets - np.outer(offsets @ line_dir, line_dir)
    return {
        "arc_m": arc,
        "chord_m": chord,
        "arc_chord_ratio": arc / chord,
        "max_line_deviation_m": float(np.linalg.norm(perpendicular, axis=1).max()),
    }


def summarise(model, data, ends: List[RopeEnds], puller: Puller) -> List[Dict]:
    rows = []
    for end in ends:
        if end.stop_reason:
            reason = end.stop_reason
        elif end.progress >= puller.distance_cap - puller.cfg.pull_speed:
            reason = "distance_cap"
        else:
            reason = "step_limit"
        row = {
            "rope": end.label,
            "pulled_m": round(end.progress, 6),
            "peak_force_n": round(end.max_pull_force, 4),
            "force_ema_n": round(end.pull_force_ema, 4),
            "max_endpoint_error_m": round(end.max_endpoint_error, 6),
            "tension_limited_steps": end.tension_limited_steps,
            "stop_reason": reason,
        }
        row.update({k: round(v, 6) for k, v in rope_metrics(model, data, end).items()})
        rows.append(row)
        print(
            f"  {row['rope']}: pulled={row['pulled_m']:.4f}m "
            f"peak_force={row['peak_force_n']:.3f}N "
            f"max_error={row['max_endpoint_error_m']:.6f}m "
            f"reason={reason}"
        )
        print(
            f"    shape: arc={row['arc_m']:.4f}m chord={row['chord_m']:.4f}m "
            f"arc/chord={row['arc_chord_ratio']:.4f} "
            f"max_deviation={row['max_line_deviation_m']:.4f}m"
        )
    return rows


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def make_camera(center, extent):
    cam = mj.MjvCamera()
    cam.type = mj.mjtCamera.mjCAMERA_FREE
    cam.lookat = center
    cam.distance = extent * 3.0
    cam.azimuth = 135.0
    cam.elevation = -20.0
    return cam


def open_video_writer(path: str, fps: float, width: int, height: int):
    """H.264/yuv420p via imageio, falling back to OpenCV where it is missing."""
    try:
        import imageio

        writer = imageio.get_writer(
            path,
            fps=fps,
            quality=7,
            codec="libx264",
            pixelformat="yuv420p",
            macro_block_size=2,
            output_params=["-movflags", "+faststart"],
        )
        print("Video backend: imageio/ffmpeg")
        return writer.append_data, writer.close
    except Exception as imageio_error:
        # Minimal benchmark environments often ship OpenCV but not
        # imageio-ffmpeg.  mp4v is not playable in Chromium-based viewers, so
        # this is a fallback rather than an equal option.
        import cv2

        writer = cv2.VideoWriter(
            path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
        )
        if not writer.isOpened():
            raise SystemExit(f"No usable MP4 backend (imageio: {imageio_error})")
        print(f"Video backend: OpenCV/mp4v (imageio unavailable: {imageio_error})")
        return (
            lambda frame: writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)),
            writer.release,
        )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run(model, data, cfg: presets.SimConfig, video_path: Optional[str],
        use_viewer: bool = False) -> List[Dict]:
    names = body_names(model)
    center, extent = rope_bounds(model, data, names)
    ends = find_rope_ends(model, data, names)
    print(f"Rope center={np.round(center, 4)} extent={extent:.4f}m")
    for end in ends:
        print(f"  {end.label}: {len(end.segments)} segments")

    distance_cap = cfg.max_pull_distance if cfg.pull_until_taut else cfg.pull_distance
    puller = Puller(model, data, ends, cfg, distance_cap)

    # Give the pull enough room to finish before the hard step cap bites.
    required = (
        puller.start_step
        + int(np.ceil(distance_cap / max(cfg.pull_speed, 1e-9)))
        + max(0, cfg.settle_steps)
    )
    steps = cfg.steps
    if steps < required:
        print(f"STEPS={steps} is below the {required} needed to finish; raising it.")
        steps = required

    if cfg.pull_until_taut:
        print(
            f"Pull mode: until taut (EMA force >= {cfg.taut_force:.3f}N for "
            f"{cfg.taut_hold_steps} steps), runaway cap {distance_cap:.3f}m"
        )
    else:
        print(f"Pull mode: fixed distance {distance_cap:.3f}m per endpoint")
    print(
        f"speed={cfg.pull_speed:.6f}m/step dt={model.opt.timestep:g}s "
        f"pre_settle={cfg.pre_settle_steps} settle={cfg.settle_steps}"
    )

    if use_viewer:
        return _run_viewer(model, data, cfg, puller, steps, center, extent, ends)
    return _run_offscreen(
        model, data, cfg, puller, steps, center, extent, ends, video_path
    )


def _run_viewer(model, data, cfg, puller, steps, center, extent, ends):
    from mujoco import viewer

    with viewer.launch_passive(model, data) as handle:
        handle.cam.lookat[:] = center
        handle.cam.distance = extent * 3.0
        handle.cam.azimuth = 135.0
        handle.cam.elevation = -20.0
        _loop(model, data, cfg, puller, steps, on_frame=handle.sync)
    print("Endpoint integrity summary:")
    return summarise(model, data, ends, puller)


def _run_offscreen(model, data, cfg, puller, steps, center, extent, ends, video_path):
    if not video_path:
        _loop(model, data, cfg, puller, steps)
        print("Endpoint integrity summary:")
        return summarise(model, data, ends, puller)

    try:
        width, height = (int(v) for v in cfg.resolution.lower().split("x"))
    except Exception:
        width, height = 960, 540

    out_dir = os.path.dirname(os.path.abspath(video_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    cam = make_camera(center, extent)
    opt = mj.MjvOption()
    # mj.Renderer creates and owns the headless GL context; constructing an
    # MjrContext directly in a windowless process fails with `gladLoadGL error`
    # and then silently degrades to stepping without recording.
    renderer = mj.Renderer(model, height=height, width=width)
    append_frame, close_writer = open_video_writer(video_path, cfg.fps, width, height)

    interval = max(1, int(round(1.0 / (cfg.fps * model.opt.timestep))))
    record_from = puller.start_step if cfg.hide_setup else 0
    print(f"Recording {width}x{height} @ {cfg.fps}fps, one frame every {interval} steps")

    def on_frame(index, finishing):
        offset = index - record_from
        if offset < 0:
            return
        if offset == 0 or (offset + 1) % interval == 0 or index == steps - 1 or finishing:
            renderer.update_scene(data, camera=cam, scene_option=opt)
            append_frame(renderer.render())

    try:
        _loop(model, data, cfg, puller, steps, on_step=on_frame)
    finally:
        close_writer()
        renderer.close()
    print(f"Saved video to {video_path}")
    print("Endpoint integrity summary:")
    return summarise(model, data, ends, puller)


def _loop(model, data, cfg, puller, steps, on_step=None, on_frame=None):
    completed_at = None
    for index in range(steps):
        puller.step(index)
        mj.mj_step(model, data)

        if completed_at is None and puller.finished():
            completed_at = index
            print(f"Pulling finished at step {index + 1}; settling {cfg.settle_steps} steps.")
        finishing = (
            completed_at is not None
            and index >= completed_at + max(0, cfg.settle_steps)
        )

        if on_step is not None:
            on_step(index, finishing)
        if on_frame is not None:
            on_frame()
        if (index + 1) % 1000 == 0:
            print(f"stepped {index + 1}/{steps}")
        if finishing:
            print(f"Stopped after completion at step {index + 1}/{steps}.")
            break


def main(argv=None):
    args = parse_args(argv)
    cfg = config_from_args(args)

    model = mj.MjModel.from_xml_path(args.model)
    if cfg.timestep <= 0:
        raise SystemExit("timestep must be positive")
    model.opt.timestep = cfg.timestep
    data = mj.MjData(model)
    mj.mj_forward(model, data)

    print(f"Loaded {args.model}: nbody={model.nbody} njnt={model.njnt} "
          f"ngeom={model.ngeom} nmocap={model.nmocap}")
    if model.njnt <= 1:
        raise SystemExit(
            "Only a freejoint was found; the rope segments are not chained. "
            "Re-run the exporter."
        )

    rows = run(model, data, cfg, args.video, use_viewer=args.viewer)

    if args.metrics_out:
        payload = {
            "model": args.model,
            "video": args.video,
            "preset": args.preset,
            "config": asdict(cfg),
            "ropes": rows,
        }
        out_dir = os.path.dirname(os.path.abspath(args.metrics_out))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.metrics_out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        print(f"Metrics written to {args.metrics_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
