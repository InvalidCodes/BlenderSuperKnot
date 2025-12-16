import os
import time
import numpy as np
# import warnings # 移除 warnings 导入，除非确实需要
if "MUJOCO_GL" not in os.environ:
    os.environ["MUJOCO_GL"] = "glfw"
# warnings.filterwarnings("ignore") # 移除警告过滤

import mujoco as mj
from mujoco import viewer

XML_PATH = os.environ.get("OUTPUT_MJCF", "tmp/knot_model.xml") # 恢复默认 XML 路径
model = mj.MjModel.from_xml_path(XML_PATH)
data = mj.MjData(model)

print("Loaded:", XML_PATH)
print("nmocap =", model.nmocap)
print("nbody =", model.nbody)
print("ngeom =", model.ngeom)
print("njnt =", model.njnt)
if model.njnt <= 1:
    raise RuntimeError("Only detected a freejoint; rope segments are not chained. Re-run exporter.")
for j in range(model.njnt):
    name = mj.mj_id2name(model, mj.mjtObj.mjOBJ_JOINT, j)
    print(f"  joint[{j}] = {name}")

def is_rope_segment_body(name: str) -> bool:
    # 新 exporter 会生成 rope0_seg_### / rope1_seg_###；旧版是 seg_###
    return bool(name) and (name.startswith("seg_") or "_seg_" in name)


# 计算绳结的边界框（适配多绳命名）
x_coords = []
y_coords = []
z_coords = []
for i in range(model.nbody):
    name = mj.mj_id2name(model, mj.mjtObj.mjOBJ_BODY, i)
    if name and is_rope_segment_body(name):
        body_id = mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, name)
        pos = model.body_pos[body_id]
        x_coords.append(float(pos[0]))
        y_coords.append(float(pos[1]))
        z_coords.append(float(pos[2]))

if not x_coords:
    raise RuntimeError("No rope segment bodies found. Expected names like 'seg_###' or 'rope0_seg_###'.")

# 计算中心点
center_x = (max(x_coords) + min(x_coords)) / 2
center_y = (max(y_coords) + min(y_coords)) / 2
center_z = (max(z_coords) + min(z_coords)) / 2
rope_center = np.array([center_x, center_y, center_z])

# 计算合适的相机距离
rope_extent = max(
    max(x_coords) - min(x_coords),
    max(y_coords) - min(y_coords),
    max(z_coords) - min(z_coords)
)
print(f"Rope center: {rope_center}")
print(f"Rope extent: {rope_extent}")

# mocap 索引解析（保持不变）
body_names = []
for i in range(model.nbody):
    name = mj.mj_id2name(model, mj.mjtObj.mjOBJ_BODY, i) or f"body_{i}"
    body_names.append(name)

seg_body_names = sorted([name for name in body_names if is_rope_segment_body(name)])

mocap_map = getattr(model, "body_mocapid", None)
if mocap_map is None:
    raise RuntimeError("Cannot resolve body_mocapid")

def mocap_index_for(body_name: str) -> int:
    bid = mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, body_name)
    if bid < 0:
        raise ValueError(f"Body '{body_name}' not found")
    mid = int(mocap_map[bid])
    if mid < 0:
        raise ValueError(f"Body '{body_name}' is not a mocap body")
    return mid

if model.nmocap < 2:
    raise RuntimeError("Need at least 2 mocap bodies")

rope_index = int(os.environ.get("ROPE_INDEX", "0"))
pull_all = os.environ.get("PULL_ALL", "1") != "0"  # 默认拉所有绳子的端点

def _find_mocap_pairs():
    """返回 [(left_name,right_name), ...]，优先 rope*_mocap_left/right，其次旧命名 mocap_left/right。"""
    pairs = []
    # 多绳命名
    lefts = sorted([n for n in body_names if n.endswith("_mocap_left")])
    for l in lefts:
        prefix = l[:-len("mocap_left")]
        r = prefix + "mocap_right"
        if r in body_names:
            pairs.append((l, r))
    # 兼容旧命名
    if "mocap_left" in body_names and "mocap_right" in body_names:
        pairs.append(("mocap_left", "mocap_right"))
    return pairs


def _segment_names_for_prefix(prefix: str):
    # prefix 例如 "rope0_"；返回属于该 rope 的所有 segment body 名称
    if prefix:
        return [n for n in body_names if n.startswith(prefix + "seg_")]
    return [n for n in body_names if n.startswith("seg_")]


def _center_extent_for_rope(prefix: str):
    segs = _segment_names_for_prefix(prefix)
    if not segs:
        return rope_center.copy(), float(rope_extent)
    xs, ys, zs = [], [], []
    for name in segs:
        bid = mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, name)
        pos = model.body_pos[bid]
        xs.append(float(pos[0])); ys.append(float(pos[1])); zs.append(float(pos[2]))
    center = np.array([(max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, (max(zs) + min(zs)) / 2])
    extent = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    return center, float(extent)


all_pairs = _find_mocap_pairs()
if not all_pairs:
    raise RuntimeError("Cannot find mocap bodies. Expected 'mocap_left/right' or 'rope0_mocap_left/right'.")

if pull_all:
    selected_pairs = all_pairs
else:
    # 调试：只拉某一根 rope 的两端
    preferred_left = f"rope{rope_index}_mocap_left"
    preferred_right = f"rope{rope_index}_mocap_right"
    if (preferred_left, preferred_right) in all_pairs:
        selected_pairs = [(preferred_left, preferred_right)]
    elif ("mocap_left", "mocap_right") in all_pairs:
        selected_pairs = [("mocap_left", "mocap_right")]
    else:
        selected_pairs = [all_pairs[0]]

mocap_pairs = []
for left_name, right_name in selected_pairs:
    left_idx = mocap_index_for(left_name)
    right_idx = mocap_index_for(right_name)
    # prefix: "rope0_" / "rope1_" / ""（旧命名）
    prefix = ""
    if left_name.endswith("_mocap_left"):
        prefix = left_name[:-len("mocap_left")]
        # 规范一下，确保最后一个字符是 '_'，便于拼接 seg_
        if prefix and not prefix.endswith("_"):
            prefix += "_"
    center, extent = _center_extent_for_rope(prefix.rstrip("_"))
    mocap_pairs.append({
        "left_name": left_name,
        "right_name": right_name,
        "left_idx": left_idx,
        "right_idx": right_idx,
        "prefix": prefix.rstrip("_"),  # "rope0" / "rope1" / ""
        "center": center,
        "extent": extent,
        "progress": 0.0,
    })

print("Using mocap pairs:")
for p in mocap_pairs:
    print(f"  - {p['left_name']} / {p['right_name']} (mocap idx {p['left_idx']},{p['right_idx']})")

mj.mj_forward(model, data)

# 可选：检查焊接是否生效
if os.environ.get("DEBUG_WELD") == "1":
    seg0 = data.body(seg_body_names[0]).xpos.copy() if seg_body_names else None
    seg_last = data.body(seg_body_names[-1]).xpos.copy() if seg_body_names else None
    print("DEBUG_WELD ON")
    if mocap_pairs:
        p0 = mocap_pairs[0]
        print("  mocap_left :", data.mocap_pos[p0["left_idx"]])
        print("  mocap_right:", data.mocap_pos[p0["right_idx"]])
    print("  seg_first  :", seg0)
    if seg_last is not None:
        print("  seg_last   :", seg_last)

# 新增：相机和窗口设置
use_viewer = bool(os.environ.get("DISPLAY")) and os.environ.get("MUJOCO_GL", "glfw") != "egl"
output_video = os.environ.get("OUTPUT_VIDEO", "rope_tightening.mp4")
res = os.environ.get("RES", "960x540")
try:
    W, H = map(int, res.lower().split("x"))
except Exception:
    W, H = 960, 540

steps = int(os.environ.get("STEPS", "50000"))
slowdown = float(os.environ.get("SLEEP", "0.0")) # 例如 0.001 可以看到更慢的日志
pull_speed_env = float(os.environ.get("PULL_SPEED", "-1"))
pull_distance_env = float(os.environ.get("PULL_DISTANCE", "-1"))
pull_steps_env = int(os.environ.get("PULL_STEPS", "-1"))
settle_steps_env = int(os.environ.get("SETTLE_STEPS", "2000"))
hold_final = os.environ.get("HOLD_FINAL", "1") != "0"  # 收紧结束后保持窗口不退出
freeze_final = os.environ.get("FREEZE_FINAL", "1") != "0"  # 进入 hold 时把速度清零并 forward
lift_height = float(os.environ.get("LIFT_Z", "0.2"))
lift_steps = 0
lift_speed = 0.0
if lift_height > 0:
    lift_steps = max(1, min(steps, int(os.environ.get("LIFT_STEPS", "1000"))))
    lift_speed = lift_height / lift_steps

for p in mocap_pairs:
    left_start_pos = data.mocap_pos[p["left_idx"]].copy()
    right_start_pos = data.mocap_pos[p["right_idx"]].copy()
    center = p["center"]
    vec_left = left_start_pos - center
    vec_right = right_start_pos - center
    norm_left = np.linalg.norm(vec_left)
    norm_right = np.linalg.norm(vec_right)
    p["dir_left"] = vec_left / norm_left if norm_left > 1e-6 else np.array([-1.0, 0.0, 0.0])
    p["dir_right"] = vec_right / norm_right if norm_right > 1e-6 else np.array([1.0, 0.0, 0.0])

if pull_steps_env <= 0:
    pull_steps_env = max(1, steps - lift_steps)
auto_pull_distance = rope_extent * 0.5
pull_distance = pull_distance_env if pull_distance_env > 0 else auto_pull_distance
if pull_distance <= 0:
    pull_distance = max(0.1, auto_pull_distance)
if pull_speed_env > 0:
    pull_speed = pull_speed_env
else:
    pull_speed = pull_distance / pull_steps_env
pull_speed = max(1e-6, pull_speed)
required_steps = lift_steps + int(np.ceil(pull_distance / pull_speed)) + max(0, settle_steps_env)
if steps < required_steps:
    print(f"STEPS too small ({steps}); bumping to {required_steps} to finish pull and settle.")
    steps = required_steps
print(f"Pull distance target: {pull_distance:.4f} m over {pull_steps_env} steps (step={pull_speed:.6f} m)")
print(f"Settle steps after pull: {max(0, settle_steps_env)}")

for p in mocap_pairs:
    print(f"[{p['left_name']}] dir_left={p['dir_left']}, dir_right={p['dir_right']}")


def apply_mocap_motion(step_index: int):
    if lift_steps > 0 and step_index < lift_steps:
        for p in mocap_pairs:
            data.mocap_pos[p["left_idx"]][2] += lift_speed
            data.mocap_pos[p["right_idx"]][2] += lift_speed
        return
    all_done = True
    for p in mocap_pairs:
        if p["progress"] < pull_distance:
            all_done = False
            step_dist = min(pull_speed, pull_distance - p["progress"])
            data.mocap_pos[p["left_idx"]] += p["dir_left"] * step_dist
            data.mocap_pos[p["right_idx"]] += p["dir_right"] * step_dist
            p["progress"] += step_dist
    if all_done:
        return


if lift_steps > 0:
    print(f"Lift phase: total {lift_height:.3f} m over {lift_steps} steps ({lift_speed:.6f} m/step)")
else:
    print("Lift phase disabled (LIFT_Z <= 0).")
print(f"Pull speed: {pull_speed:.4f} m/step")

if use_viewer:
    try:
        v = viewer.launch_passive(model, data)
        # 将交互式相机指向绳结中心，避免默认相机看向原点导致“什么也看不到”
        v.cam.lookat[:] = rope_center
        v.cam.distance = rope_extent * 3.0
        v.cam.azimuth = 135.0
        v.cam.elevation = -20.0
        print("Simulation start (viewer mode).")
        for t in range(steps):
            apply_mocap_motion(t)
            mj.mj_step(model, data)
            v.sync()
            if slowdown > 0:
                time.sleep(slowdown)
            if (t+1) % 1000 == 0:
                print(f"stepped {t+1}/{steps}")
        print("Tightening done.")
        if hold_final:
            if freeze_final:
                try:
                    data.qvel[:] = 0
                except Exception:
                    pass
                mj.mj_forward(model, data)
            print("Holding final frame (close the window to exit).")
            while v.is_running():
                v.sync()
                time.sleep(0.02)
            # 用户关闭窗口后退出
            raise SystemExit(0)
        else:
            v.close()
            print("Done.")
            raise SystemExit(0)
    except Exception as e:
        print("Viewer launch failed (headless or missing GL):", e)
        use_viewer = False

# 尝试离屏渲染到视频（EGL）
try:
    import imageio
    cam = mj.MjvCamera()
    opt = mj.MjvOption()
    scn = mj.MjvScene(model, maxgeom=20000)
    con = mj.MjrContext(model, mj.mjtFontScale.mjFONTSCALE_150)
    viewport = mj.MjrRect(0, 0, W, H)
    # 简单的相机设置：看向世界原点，距离按模型包围盒估计
    cam.type = mj.mjtCamera.mjCAMERA_FREE
    # 估计一个相机距离
    cam.lookat = rope_center
    cam.distance = rope_extent * 3.0  # 距离设为绳结范围的 3 倍
    cam.azimuth = 135.0
    cam.elevation = -20.0

    writer = imageio.get_writer(output_video, fps=60, quality=7)
    rgb = np.empty((H, W, 3), dtype=np.uint8)
    depth = np.empty((H, W), dtype=np.float32)

    print("Simulation start (offscreen video).")
    for t in range(steps):
        apply_mocap_motion(t)
        mj.mj_step(model, data)

        mj.mjv_updateScene(model, data, opt, None, cam, mj.mjtCatBit.mjCAT_ALL, scn)
        mj.mjr_render(viewport, scn, con)
        mj.mjr_readPixels(rgb, depth, viewport, con)
        writer.append_data(rgb)
        if slowdown > 0:
            time.sleep(slowdown)
        if (t+1) % 1000 == 0:
            print(f"stepped {t+1}/{steps}")
    writer.close()
    print(f"Saved video to {output_video}")
except Exception as e:
    print("Offscreen rendering failed:", e)
    # 纯步进作为兜底
    print("Simulation start (headless fallback).")
    for t in range(steps):
        apply_mocap_motion(t)
        mj.mj_step(model, data)
        if slowdown > 0:
            time.sleep(slowdown)
        if (t+1) % 1000 == 0:
            print(f"stepped {t+1}/{steps}")
    print("Done (stepped without visualization).")
