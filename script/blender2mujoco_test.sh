#!/bin/bash
set -euo pipefail

# --- 1. 确保 Conda 环境激活 ---
# 这一行通常能解决脚本中 conda activate 失败的问题
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate mujoco

# --- 2. 定义文件和输出 ---
BLENDER_FILE="blender2mujoco_test.blend"
EXPORT_SCRIPT="blender_dot_export.py" # <-- 使用新的导出脚本名称
OUTPUT_MJCF="tmp/knot_model.xml"

# --- 3. 运行 Blender ---
echo "Starting MuJoCo structure export from Blender..."
# 使用你确认可用的 blender 命令
set +e
# 将输出路径通过环境变量与脚本参数同时传递（脚本优先读取 --out）
OUTPUT_MJCF="$OUTPUT_MJCF" blender --background "$BLENDER_FILE" --python "$EXPORT_SCRIPT" -- --out "$OUTPUT_MJCF" --radius 0.01 --segment 0.025
RC=$?
set -e

if [ $RC -ne 0 ]; then
  echo "--- Blender Export FAILED (exit $RC). Check the console output for Python errors. ---"
  exit $RC
fi

# --- 4. 验证导出结果 ---
if [ ! -s "$OUTPUT_MJCF" ]; then
  echo "--- Export did not produce $OUTPUT_MJCF or file is empty. ---"
  exit 2
fi

echo "--- MJCF Export Successful! File created at $OUTPUT_MJCF ---"

# --- 5. 立即做一次 joint 整体性检查 ---
python - "$OUTPUT_MJCF" <<'EOF'
import sys
import mujoco as mj

xml_path = sys.argv[1]
print(f"[CHECK] Inspecting '{xml_path}' for joints...")
m = mj.MjModel.from_xml_path(xml_path)
print("njnt =", m.njnt)
for j in range(m.njnt):
    name = mj.mj_id2name(m, mj.mjtObj.mjOBJ_JOINT, j)
    print(j, name)
if m.njnt <= 1:
    raise SystemExit("ERROR: Only freejoint detected. Exporter did not create ball joints.")
EOF

echo "--- Joint count sanity check passed ---"

echo "--- Preview joints inside $OUTPUT_MJCF ---"
grep -n "<joint" "$OUTPUT_MJCF" || true

# --- 6. 下一步：运行 MuJoCo 仿真验证 ---
python run_mocap.py
