#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

CONTROL_DIR="${CONTROL_DIR:-topology_controls}"
XML_DIR="${XML_DIR:-tmp/topology_controls}"
VIDEO_DIR="${VIDEO_DIR:-artifacts/topology_controls}"
LOG_DIR="${LOG_DIR:-artifacts/topology_controls/logs}"
mkdir -p "$CONTROL_DIR" "$XML_DIR" "$VIDEO_DIR" "$LOG_DIR"

blender --background blender2mujoco_test.blend \
    --python generate_topology_controls.py -- \
    --out-dir "$CONTROL_DIR" \
    2>&1 | tee "$LOG_DIR/generate.log"

for blend_file in "$CONTROL_DIR"/*.blend; do
    control_id="$(basename "$blend_file" .blend)"
    xml_file="$XML_DIR/$control_id.xml"
    video_file="$VIDEO_DIR/$control_id.mp4"

    OUTPUT_MJCF="$xml_file" \
    ROPE_FRICTION="${ROPE_FRICTION:-0.35 0.01 0.001}" \
    TABLE_FRICTION="${TABLE_FRICTION:-0.25 0.01 0.001}" \
    JOINT_STIFFNESS="${JOINT_STIFFNESS:-0.001}" \
    blender --background "$blend_file" \
        --python blender_dot_export.py -- \
        --out "$xml_file" --radius 0.008 --segment 0.022 --maxseg 140 \
        2>&1 | tee "$LOG_DIR/${control_id}_export.log"

    MUJOCO_GL="${MUJOCO_GL:-egl}" \
    OUTPUT_MJCF="$xml_file" \
    OUTPUT_VIDEO="$video_file" \
    RES="${RES:-640x360}" \
    VIDEO_FPS="${VIDEO_FPS:-30}" \
    SIM_TIMESTEP="${SIM_TIMESTEP:-0.0005}" \
    STEPS="${STEPS:-9000}" \
    PRE_SETTLE_STEPS="${PRE_SETTLE_STEPS:-500}" \
    PULL_UNTIL_TAUT="${PULL_UNTIL_TAUT:-0}" \
    MAX_PULL_DISTANCE="${MAX_PULL_DISTANCE:-1.70}" \
    PULL_DISTANCE="${PULL_DISTANCE:-1.20}" \
    PULL_SPEED="${PULL_SPEED:-0.00030}" \
    TAUT_FORCE="${TAUT_FORCE:-3.0}" \
    TAUT_HOLD_STEPS="${TAUT_HOLD_STEPS:-50}" \
    SETTLE_STEPS="${SETTLE_STEPS:-1000}" \
    MAX_PULL_FORCE="${MAX_PULL_FORCE:-80}" \
    MAX_ENDPOINT_ERROR="${MAX_ENDPOINT_ERROR:-0.08}" \
    HIDE_SETUP_IN_VIDEO=1 \
    LIFT_Z=0 \
    HOLD_FINAL=0 \
    conda run --no-capture-output -n mujoco python run_mocap.py \
        2>&1 | tee "$LOG_DIR/${control_id}_release.log"
done

echo "Topology-control assets: $CONTROL_DIR"
echo "Topology-control videos: $VIDEO_DIR"
