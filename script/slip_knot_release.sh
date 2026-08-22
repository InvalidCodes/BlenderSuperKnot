#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

OUTPUT_BLEND="${OUTPUT_BLEND:-slip_knot.blend}"
OUTPUT_XML="${OUTPUT_XML:-tmp/slip_knot.xml}"
OUTPUT_VIDEO="${OUTPUT_VIDEO:-artifacts/slip_knot_release_straight.mp4}"

mkdir -p tmp artifacts

blender --background blender2mujoco_test.blend \
    --python generate_slip_knot.py -- \
    --out "$OUTPUT_BLEND"

OUTPUT_MJCF="$OUTPUT_XML" \
ROPE_FRICTION="${ROPE_FRICTION:-0.35 0.01 0.001}" \
TABLE_FRICTION="${TABLE_FRICTION:-0.25 0.01 0.001}" \
JOINT_STIFFNESS="${JOINT_STIFFNESS:-0.001}" \
blender --background "$OUTPUT_BLEND" \
    --python blender_dot_export.py -- \
    --out "$OUTPUT_XML" --radius 0.008 --segment 0.018 --maxseg 100

MUJOCO_GL="${MUJOCO_GL:-egl}" \
OUTPUT_MJCF="$OUTPUT_XML" \
OUTPUT_VIDEO="$OUTPUT_VIDEO" \
RES="${RES:-640x360}" \
VIDEO_FPS="${VIDEO_FPS:-30}" \
SIM_TIMESTEP="${SIM_TIMESTEP:-0.0005}" \
STEPS="${STEPS:-6000}" \
PRE_SETTLE_STEPS="${PRE_SETTLE_STEPS:-500}" \
PULL_UNTIL_TAUT=1 \
MAX_PULL_DISTANCE="${MAX_PULL_DISTANCE:-1.30}" \
PULL_SPEED="${PULL_SPEED:-0.00030}" \
TAUT_FORCE="${TAUT_FORCE:-3.0}" \
TAUT_HOLD_STEPS="${TAUT_HOLD_STEPS:-50}" \
SETTLE_STEPS="${SETTLE_STEPS:-1000}" \
MAX_PULL_FORCE="${MAX_PULL_FORCE:-20}" \
HIDE_SETUP_IN_VIDEO=1 \
LIFT_Z=0 \
HOLD_FINAL=0 \
conda run --no-capture-output -n mujoco python run_mocap.py

echo "Slip-knot release video: $OUTPUT_VIDEO"
