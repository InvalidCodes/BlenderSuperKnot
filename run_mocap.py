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
sim_timestep = float(os.environ.get("SIM_TIMESTEP", str(model.opt.timestep)))
if sim_timestep <= 0:
    raise ValueError("SIM_TIMESTEP must be positive")
model.opt.timestep = sim_timestep
data = mj.MjData(model)
mj.mj_forward(model, data)

print("Loaded:", XML_PATH)
print("nmocap =", model.nmocap)
print("nbody =", model.nbody)
print("ngeom =", model.ngeom)
print("njnt =", model.njnt)
print("timestep =", model.opt.timestep)
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
        # body_pos 是相对父 body 的局部坐标；绳索是深层嵌套链，必须使用
        # forward 后的世界坐标 xpos 才能得到正确包围盒与相机中心。
        pos = data.xpos[body_id]
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
        normalized = prefix if prefix.endswith("_") else prefix + "_"
        return sorted(n for n in body_names if n.startswith(normalized + "seg_"))
    return sorted(n for n in body_names if n.startswith("seg_"))


def _center_extent_for_rope(prefix: str):
    segs = _segment_names_for_prefix(prefix)
    if not segs:
        return rope_center.copy(), float(rope_extent)
    xs, ys, zs = [], [], []
    for name in segs:
        bid = mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, name)
        pos = data.xpos[bid]
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
        "segments": _segment_names_for_prefix(prefix.rstrip("_")),
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
video_fps = float(os.environ.get("VIDEO_FPS", "60"))
res = os.environ.get("RES", "960x540")
try:
    W, H = map(int, res.lower().split("x"))
except Exception:
    W, H = 960, 540

steps = int(os.environ.get("STEPS", "50000"))
slowdown = float(os.environ.get("SLEEP", "0.0")) # 例如 0.001 可以看到更慢的日志
pull_speed_env = float(os.environ.get("PULL_SPEED", "-1"))
pull_distance_env = float(os.environ.get("PULL_DISTANCE", "-1"))
pull_until_taut = os.environ.get("PULL_UNTIL_TAUT", "1") != "0"
max_pull_distance_env = float(os.environ.get("MAX_PULL_DISTANCE", "-1"))
pull_steps_env = int(os.environ.get("PULL_STEPS", "-1"))
settle_steps_env = int(os.environ.get("SETTLE_STEPS", "2000"))
pre_settle_steps_env = max(0, int(os.environ.get("PRE_SETTLE_STEPS", "0")))
hide_setup_in_video = os.environ.get("HIDE_SETUP_IN_VIDEO", "1") != "0"
taut_force = float(os.environ.get("TAUT_FORCE", "10.0"))
if taut_force <= 0:
    raise ValueError("TAUT_FORCE must be positive")
taut_hold_steps = max(1, int(os.environ.get("TAUT_HOLD_STEPS", "20")))
taut_force_ema_alpha = float(os.environ.get("TAUT_FORCE_EMA_ALPHA", "0.02"))
if not 0 < taut_force_ema_alpha <= 1:
    raise ValueError("TAUT_FORCE_EMA_ALPHA must be in (0, 1]")
max_endpoint_error = float(os.environ.get("MAX_ENDPOINT_ERROR", "0.04"))
if max_endpoint_error <= 0:
    raise ValueError("MAX_ENDPOINT_ERROR must be positive")
max_pull_force = float(os.environ.get("MAX_PULL_FORCE", "30.0"))
if max_pull_force <= 0:
    raise ValueError("MAX_PULL_FORCE must be positive")
force_limit_hold_steps = max(1, int(os.environ.get("FORCE_LIMIT_HOLD_STEPS", "20")))
stop_all_on_tension = os.environ.get("STOP_ALL_ON_TENSION", "1") != "0"
hold_final = os.environ.get("HOLD_FINAL", "1") != "0"  # 收紧结束后保持窗口不退出
freeze_final = os.environ.get("FREEZE_FINAL", "1") != "0"  # 进入 hold 时把速度清零并 forward
lift_height = float(os.environ.get("LIFT_Z", "0.0"))
lift_steps = 0
lift_speed = 0.0
if lift_height > 0:
    lift_steps = max(1, min(steps, int(os.environ.get("LIFT_STEPS", "1000"))))
    lift_speed = lift_height / lift_steps

for p in mocap_pairs:
    left_start_pos = data.mocap_pos[p["left_idx"]].copy()
    right_start_pos = data.mocap_pos[p["right_idx"]].copy()
    segments = p["segments"]
    if len(segments) >= 2:
        first_id = mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, segments[0])
        second_id = mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, segments[1])
        penultimate_id = mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, segments[-2])
        last_id = mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, segments[-1])
        # 从绳体内部指向自由端，正是端部受拉时的外向切线。
        vec_left = data.xpos[first_id] - data.xpos[second_id]
        vec_right = data.xpos[last_id] - data.xpos[penultimate_id]
        p["left_body_id"] = first_id
        p["right_body_id"] = last_id
    else:
        center = p["center"]
        vec_left = left_start_pos - center
        vec_right = right_start_pos - center
    norm_left = np.linalg.norm(vec_left)
    norm_right = np.linalg.norm(vec_right)
    p["dir_left"] = vec_left / norm_left if norm_left > 1e-6 else np.array([-1.0, 0.0, 0.0])
    p["dir_right"] = vec_right / norm_right if norm_right > 1e-6 else np.array([1.0, 0.0, 0.0])
    p["max_endpoint_error"] = 0.0
    eq_prefix = (p["prefix"] + "_") if p["prefix"] else ""
    p["weld_eq_ids"] = [
        mj.mj_name2id(model, mj.mjtObj.mjOBJ_EQUALITY, eq_prefix + "weld_left"),
        mj.mj_name2id(model, mj.mjtObj.mjOBJ_EQUALITY, eq_prefix + "weld_right"),
    ]
    p["max_pull_force"] = 0.0
    p["pull_force_ema"] = 0.0
    p["tension_limited_steps"] = 0
    p["taut_consecutive_steps"] = 0
    p["limit_consecutive_steps"] = 0
    p["pull_stopped"] = False
    p["stop_reason"] = ""

if pull_steps_env <= 0:
    pull_steps_env = max(1, steps - lift_steps)
auto_pull_distance = rope_extent * 0.5
if pull_until_taut:
    # This is only a runaway guard.  PULL_DISTANCE deliberately does not cap
    # taut mode, otherwise a short legacy value can stop an untightened rope.
    pull_distance = (
        max_pull_distance_env
        if max_pull_distance_env > 0
        else max(1.0, rope_extent * 3.0)
    )
else:
    pull_distance = pull_distance_env if pull_distance_env > 0 else auto_pull_distance
if pull_distance <= 0:
    pull_distance = max(0.1, auto_pull_distance)
if pull_speed_env > 0:
    pull_speed = pull_speed_env
else:
    pull_speed = pull_distance / pull_steps_env
pull_speed = max(1e-6, pull_speed)
required_steps = (
    lift_steps
    + pre_settle_steps_env
    + int(np.ceil(pull_distance / pull_speed))
    + max(0, settle_steps_env)
)
if steps < required_steps:
    print(f"STEPS too small ({steps}); bumping to {required_steps} to finish pull and settle.")
    steps = required_steps
if pull_until_taut:
    print(
        f"Pull mode: until taut (force >= {taut_force:.3f}N for "
        f"{taut_hold_steps} consecutive steps); safety distance={pull_distance:.4f}m, "
        f"step={pull_speed:.6f}m"
    )
else:
    print(f"Pull distance target: {pull_distance:.4f} m over {pull_steps_env} steps (step={pull_speed:.6f} m)")
print(f"Settle steps after pull: {max(0, settle_steps_env)}")
print(f"Pre-settle steps before pull: {pre_settle_steps_env}")

for p in mocap_pairs:
    print(f"[{p['left_name']}] dir_left={p['dir_left']}, dir_right={p['dir_right']}")


def weld_translation_force(eq_id: int) -> float:
    if eq_id < 0 or data.nefc <= 0:
        return 0.0
    types = data.efc_type[:data.nefc]
    ids = data.efc_id[:data.nefc]
    rows = np.flatnonzero(
        (types == mj.mjtConstraint.mjCNSTR_EQUALITY) & (ids == eq_id)
    )
    if rows.size == 0:
        return 0.0
    # weld 前三行对应平移约束，单位为 N；后三行为转动约束。
    return float(np.linalg.norm(data.efc_force[rows[:3]]))


def apply_mocap_motion(step_index: int):
    if lift_steps > 0 and step_index < lift_steps:
        for p in mocap_pairs:
            data.mocap_pos[p["left_idx"]][2] += lift_speed
            data.mocap_pos[p["right_idx"]][2] += lift_speed
        return
    if step_index < lift_steps + pre_settle_steps_env:
        return
    all_done = True
    for p in mocap_pairs:
        if p["progress"] < pull_distance:
            all_done = False
            if p["pull_stopped"]:
                continue
            # mocap weld 是柔性约束。持续外拉时对 weld 张力做 EMA 滤波：
            # 碰撞尖峰会被滤掉，真正绷紧后持续存在的张力才会结束拉动。
            if "left_body_id" in p and "right_body_id" in p:
                left_error = float(np.linalg.norm(
                    data.mocap_pos[p["left_idx"]] - data.xpos[p["left_body_id"]]
                ))
                right_error = float(np.linalg.norm(
                    data.mocap_pos[p["right_idx"]] - data.xpos[p["right_body_id"]]
                ))
                endpoint_error = max(left_error, right_error)
                p["max_endpoint_error"] = max(p["max_endpoint_error"], endpoint_error)
                pull_force = max(weld_translation_force(eq) for eq in p["weld_eq_ids"])
                p["max_pull_force"] = max(p["max_pull_force"], pull_force)
                p["pull_force_ema"] = (
                    (1.0 - taut_force_ema_alpha) * p["pull_force_ema"]
                    + taut_force_ema_alpha * pull_force
                )
                if pull_until_taut and p["pull_force_ema"] >= taut_force:
                    p["tension_limited_steps"] += 1
                    p["taut_consecutive_steps"] += 1
                    if p["taut_consecutive_steps"] >= taut_hold_steps:
                        p["pull_stopped"] = True
                        p["stop_reason"] = "taut"
                        if stop_all_on_tension:
                            for other in mocap_pairs:
                                other["pull_stopped"] = True
                                if other is not p and not other["stop_reason"]:
                                    other["stop_reason"] = "peer_taut"
                        continue
                else:
                    p["taut_consecutive_steps"] = 0

                # Hard guards are intentionally above the taut threshold. They
                # protect the flexible weld if force/error becomes pathological;
                # they are not the normal tightening completion condition.
                if endpoint_error >= max_endpoint_error or pull_force >= max_pull_force:
                    p["limit_consecutive_steps"] += 1
                    if p["limit_consecutive_steps"] >= force_limit_hold_steps:
                        p["pull_stopped"] = True
                        p["stop_reason"] = "safety_limit"
                        if stop_all_on_tension:
                            for other in mocap_pairs:
                                other["pull_stopped"] = True
                    continue
                p["limit_consecutive_steps"] = 0
            step_dist = min(pull_speed, pull_distance - p["progress"])
            data.mocap_pos[p["left_idx"]] += p["dir_left"] * step_dist
            data.mocap_pos[p["right_idx"]] += p["dir_right"] * step_dist
            p["progress"] += step_dist
    if all_done:
        return


def print_endpoint_summary():
    print("Endpoint integrity summary:")
    for p in mocap_pairs:
        if p["stop_reason"]:
            reason = p["stop_reason"]
        elif p["progress"] >= pull_distance - pull_speed:
            reason = "distance_cap"
        else:
            reason = "step_limit"
        print(
            f"  {p['prefix'] or 'rope'}: pulled={p['progress']:.4f}m, "
            f"max_error={p['max_endpoint_error']:.6f}m, "
            f"peak_force={p['max_pull_force']:.3f}N, force_ema={p['pull_force_ema']:.3f}N, "
            f"limited_steps={p['tension_limited_steps']}, "
            f"stopped={p['pull_stopped']}, reason={reason}"
        )
        if len(p["segments"]) >= 2:
            points = np.asarray([
                data.xpos[mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, name)]
                for name in p["segments"]
            ])
            arc_length = float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())
            chord_vec = points[-1] - points[0]
            chord_length = float(np.linalg.norm(chord_vec))
            if chord_length > 1e-9:
                line_dir = chord_vec / chord_length
                offsets = points - points[0]
                perpendicular = offsets - np.outer(offsets @ line_dir, line_dir)
                max_deviation = float(np.linalg.norm(perpendicular, axis=1).max())
                straightness = arc_length / chord_length
            else:
                max_deviation = float("inf")
                straightness = float("inf")
            print(
                f"    shape: arc={arc_length:.4f}m, chord={chord_length:.4f}m, "
                f"arc/chord={straightness:.4f}, max_line_deviation={max_deviation:.4f}m"
            )


def pulling_has_finished():
    return all(
        p["pull_stopped"] or p["progress"] >= pull_distance - pull_speed
        for p in mocap_pairs
    )


if lift_steps > 0:
    print(f"Lift phase: total {lift_height:.3f} m over {lift_steps} steps ({lift_speed:.6f} m/step)")
else:
    print("Lift phase disabled (LIFT_Z <= 0).")
print(f"Pull speed: {pull_speed:.6f} m/step")

if use_viewer:
    try:
        v = viewer.launch_passive(model, data)
        # 将交互式相机指向绳结中心，避免默认相机看向原点导致“什么也看不到”
        v.cam.lookat[:] = rope_center
        v.cam.distance = rope_extent * 3.0
        v.cam.azimuth = 135.0
        v.cam.elevation = -20.0
        print("Simulation start (viewer mode).")
        pull_completion_step = None
        for t in range(steps):
            apply_mocap_motion(t)
            mj.mj_step(model, data)
            v.sync()
            if pull_completion_step is None and pulling_has_finished():
                pull_completion_step = t
            if (
                pull_completion_step is not None
                and t >= pull_completion_step + max(0, settle_steps_env)
            ):
                print(f"Stopped after taut/distance completion at step {t + 1}/{steps}.")
                break
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

# 尝试离屏渲染到视频（EGL/OSMesa）。使用 MuJoCo 的 Renderer 创建并管理
# headless OpenGL 上下文；直接实例化 MjrContext 在无窗口进程中会触发
# ``gladLoadGL error``，然后悄悄退化成“只步进、不录制”。
try:
    import imageio
    cam = mj.MjvCamera()
    opt = mj.MjvOption()
    # 简单的相机设置：看向世界原点，距离按模型包围盒估计
    cam.type = mj.mjtCamera.mjCAMERA_FREE
    # 估计一个相机距离
    cam.lookat = rope_center
    cam.distance = rope_extent * 3.0  # 距离设为绳结范围的 3 倍
    cam.azimuth = 135.0
    cam.elevation = -20.0

    output_dir = os.path.dirname(os.path.abspath(output_video))
    os.makedirs(output_dir, exist_ok=True)
    renderer = mj.Renderer(model, height=H, width=W)
    try:
        # H.264 + yuv420p is understood by browser/IDE embedded players.  The
        # OpenCV fallback below commonly emits FMP4/mp4v, which is a valid MP4
        # but is not supported by Chromium-based viewers.
        writer = imageio.get_writer(
            output_video,
            fps=video_fps,
            quality=7,
            codec="libx264",
            pixelformat="yuv420p",
            macro_block_size=2,
            output_params=["-movflags", "+faststart"],
        )

        def append_video_frame(frame):
            writer.append_data(frame)

        def close_video_writer():
            writer.close()
        print("Video backend: imageio/ffmpeg")
    except Exception as imageio_error:
        # 很多最小化 benchmark 环境没有 imageio-ffmpeg，但会随科学计算
        # 环境提供 OpenCV。自动回退，避免仿真成功却无法交付视频。
        import cv2
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        cv_writer = cv2.VideoWriter(output_video, fourcc, video_fps, (W, H))
        if not cv_writer.isOpened():
            raise RuntimeError(
                f"No usable MP4 backend. imageio error: {imageio_error}"
            )

        def append_video_frame(frame):
            cv_writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))

        def close_video_writer():
            cv_writer.release()
        print(f"Video backend: OpenCV/mp4v (imageio unavailable: {imageio_error})")

    render_interval = max(1, int(round(1.0 / (video_fps * model.opt.timestep))))
    record_start_step = (lift_steps + pre_settle_steps_env) if hide_setup_in_video else 0
    record_steps = max(1, steps - record_start_step)
    expected_frames = 1 + (max(0, record_steps - 1) // render_interval)
    if (record_steps - 1) % render_interval:
        expected_frames += 1
    print(
        f"Simulation start (offscreen video): physics dt={model.opt.timestep:g}s, "
        f"render every {render_interval} steps, about {expected_frames} frames."
    )
    try:
        pull_completion_step = None
        for t in range(steps):
            apply_mocap_motion(t)
            mj.mj_step(model, data)

            if pull_completion_step is None and pulling_has_finished():
                pull_completion_step = t
                print(
                    f"Pulling finished at step {t + 1}; "
                    f"settling for {max(0, settle_steps_env)} more steps."
                )
            finish_now = (
                pull_completion_step is not None
                and t >= pull_completion_step + max(0, settle_steps_env)
            )

            record_t = t - record_start_step
            should_render = record_t >= 0 and (
                record_t == 0
                or (record_t + 1) % render_interval == 0
                or t == steps - 1
                or finish_now
            )
            if should_render:
                renderer.update_scene(data, camera=cam, scene_option=opt)
                append_video_frame(renderer.render())
            if slowdown > 0:
                time.sleep(slowdown)
            if (t+1) % 1000 == 0:
                print(f"stepped {t+1}/{steps}")
            if finish_now:
                print(f"Stopped after taut/distance completion at step {t + 1}/{steps}.")
                break
    finally:
        close_video_writer()
        renderer.close()
    print(f"Saved video to {output_video}")
    print_endpoint_summary()
except Exception as e:
    print("Offscreen rendering failed:", e)
    # 纯步进作为兜底
    print("Simulation start (headless fallback).")
    pull_completion_step = None
    for t in range(steps):
        apply_mocap_motion(t)
        mj.mj_step(model, data)
        if pull_completion_step is None and pulling_has_finished():
            pull_completion_step = t
        if (
            pull_completion_step is not None
            and t >= pull_completion_step + max(0, settle_steps_env)
        ):
            print(f"Stopped after taut/distance completion at step {t + 1}/{steps}.")
            break
        if slowdown > 0:
            time.sleep(slowdown)
        if (t+1) % 1000 == 0:
            print(f"stepped {t+1}/{steps}")
    print("Done (stepped without visualization).")
    print_endpoint_summary()
