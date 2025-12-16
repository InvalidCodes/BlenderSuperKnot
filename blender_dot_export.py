import bpy
import os
import sys
from math import sqrt
from mathutils import Vector

print("USING NEW EXPORTER: generate_mjcf_from_polyline")  # 便于确认 Blender 正在运行此脚本

# --- 配置参数 ---
# 输出路径优先级：命令行 --out PATH > 环境变量 OUTPUT_MJCF > 默认 /tmp/knot_model.xml
OUTPUT_MJCF_PATH = os.environ.get("OUTPUT_MJCF", "tmp/knot_model.xml")
# 允许命令行覆盖自动估计的尺寸
CLI_RADIUS = None
CLI_SEGMENT = None
CLI_MAX_SEGMENTS = None


def _parse_cli_overrides():
    """解析 Blender -- 之后的脚本参数，支持 --out/--radius/--segment"""
    global OUTPUT_MJCF_PATH, CLI_RADIUS, CLI_SEGMENT, CLI_MAX_SEGMENTS, MAX_EXPORT_SEGMENTS
    try:
        argv = sys.argv
        if "--" not in argv:
            return
        extra = argv[argv.index("--") + 1:]
        i = 0
        while i < len(extra):
            arg = extra[i]
            if arg == "--out" and i + 1 < len(extra):
                OUTPUT_MJCF_PATH = extra[i + 1]
                i += 2
            elif arg == "--radius" and i + 1 < len(extra):
                CLI_RADIUS = float(extra[i + 1])
                i += 2
            elif arg in ("--segment", "--seglen") and i + 1 < len(extra):
                CLI_SEGMENT = float(extra[i + 1])
                i += 2
            elif arg in ("--maxseg", "--maxsegments") and i + 1 < len(extra):
                CLI_MAX_SEGMENTS = int(extra[i + 1])
                i += 2
            else:
                i += 1
    except Exception as exc:
        print(f"Argument parsing failed: {exc}")
    if CLI_MAX_SEGMENTS is not None and CLI_MAX_SEGMENTS >= 2:
        MAX_EXPORT_SEGMENTS = CLI_MAX_SEGMENTS
        print(f"CLI override: max segments -> {MAX_EXPORT_SEGMENTS}")


_parse_cli_overrides()

# 使用 "AUTO" 让脚本自动识别前缀；若你已知前缀，也可直接写如 "Mesh." 或 "Capsule."
ROPE_PREFIX = "AUTO"

# 物理参数
ROPE_FRICTION = "0.2 0.005 0.0001"
JOINT_DAMPING = "0.1"
ROPE_DENSITY = "1000"
# 默认段数：更像“绳”且算力不过分（可用环境变量/CLI 覆盖）
MAX_EXPORT_SEGMENTS = int(os.environ.get("MAX_EXPORT_SEGMENTS", "120"))

# 渲染参数（不影响物理）
# 视觉外皮半径倍率：让绳子“看起来更粗”，但碰撞仍用物理半径 rope_r
VISUAL_RADIUS_SCALE = float(os.environ.get("VISUAL_RADIUS_SCALE", "1.25"))
# 贴图：若提供文件路径则使用图片，否则自动尝试使用仓库里的 material/rope_01.png；
# 若找不到则退回 builtin checker
ROPE_TEXTURE_FILE = os.environ.get("ROPE_TEXTURE_FILE", "").strip()
# 纹理重复（越大越像编织纹理；u沿长度，v沿周向）
# 注意：MuJoCo 的纹理坐标是“按每个 geom 重置”的，段与段之间很难做到 100% 连续。
# 默认把频率设得更密一些，用“高频重复”弱化段与段的 UV 重置破绽；你可用环境变量继续调。
ROPE_TEXREPEAT_U = float(os.environ.get("ROPE_TEXREPEAT_U", "200"))
ROPE_TEXREPEAT_V = float(os.environ.get("ROPE_TEXREPEAT_V", "20"))

# 视觉遮缝：仅对视觉层 capsule 做轻微后向重叠（1–3% 一般够用）
VISUAL_OVERLAP_FRAC = float(os.environ.get("VISUAL_OVERLAP_FRAC", "0.02"))

# 全局变量用于存储自动计算的尺寸
ROPE_RADIUS = None
ROPE_SEGMENT_LENGTH = None


def _detect_prefix(candidates):
    # 统计每个候选前缀匹配到的数量，选最多的
    all_mesh_objects = [obj for obj in bpy.data.objects if obj.type == 'MESH']
    best_prefix = None
    best_count = 0
    for prefix in candidates:
        count = sum(1 for obj in all_mesh_objects if obj.name.startswith(prefix))
        if count > best_count:
            best_count = count
            best_prefix = prefix
    return best_prefix, best_count


def auto_calculate_dimensions(segment_obj):
    """自动从绳对象的整体尺寸估计半径与段长基准"""
    global ROPE_RADIUS, ROPE_SEGMENT_LENGTH
    dims = segment_obj.dimensions
    sorted_dims = sorted([dims.x, dims.y, dims.z])
    diameter = sorted_dims[0] if sorted_dims[0] > 0 else 0.02
    length = sorted_dims[2] if sorted_dims[2] > 0 else 1.0
    ROPE_RADIUS = diameter / 2.0
    # 默认目标段长：约 2.5 个半径
    ROPE_SEGMENT_LENGTH = max(ROPE_RADIUS * 2.5, 0.02)
    print(f"Auto-Detected: Radius={ROPE_RADIUS:.4f}m, Target Segment Length={ROPE_SEGMENT_LENGTH:.4f}m (rope length≈{length:.4f}m)")
    return ROPE_RADIUS, ROPE_SEGMENT_LENGTH


def get_rope_segments():
    """按名称顺序获取绳索几何体对象"""
    all_mesh_objects = [obj for obj in bpy.data.objects if obj.type == 'MESH']
    all_mesh_names = [obj.name for obj in all_mesh_objects]

    # 初始前缀
    prefix = ROPE_PREFIX
    if prefix == "AUTO":
        # 常见前缀候选
        candidates = [
            "Capsule.",
            "Mesh.",
            "RopeSeg",
            "Segment",
            "Rope",
            "Cube.",
        ]
        prefix, count = _detect_prefix(candidates)
        print(f"AUTO prefix detection: picked '{prefix}' with {count} matches from candidates {candidates}")
        if not prefix or count < 2:
            # 回退策略：寻找名字形如 name.001 的批量对象，选数量最多的 name
            stems = {}
            for name in all_mesh_names:
                if "." in name:
                    stem = name.split(".")[0] + "."
                    stems[stem] = stems.get(stem, 0) + 1
            if stems:
                prefix = max(stems.items(), key=lambda kv: kv[1])[0]
                print(f"Fallback stem detection picked '{prefix}' with {stems[prefix]} matches")

    print("\n--- DEBUG START ---")
    print(f"ALL MESH OBJECTS: {all_mesh_names}")
    print(f"Using ROPE_PREFIX: '{prefix}'")

    # 过滤
    all_segments = [obj for obj in all_mesh_objects if prefix and obj.name.startswith(prefix)]
    found_names = [obj.name for obj in all_segments]
    print(f"DEBUG: Found {len(all_segments)} segments using prefix '{prefix}'. Names: {found_names}")
    print("--- DEBUG END ---\n")

    all_segments.sort(key=lambda x: x.name)
    return all_segments


def generate_mjcf_structure(segments, rope_r, rope_l):
    """基于多个已有段对象导出，返回 (xml, first_pos, last_pos)"""
    xml_body_content = ""
    half_length = rope_l / 2.0
    first_pos = None
    last_pos = None
    for i, obj in enumerate(segments):
        body_name = f"seg_{i:03d}"
        center_pos = obj.matrix_world.translation
        if i == 0:
            first_pos = center_pos.copy()
        last_pos = center_pos.copy()
        quat = obj.matrix_world.to_quaternion()
        xml_body_content += f'\t\t<body name="{body_name}" pos="{center_pos.x:.4f} {center_pos.y:.4f} {center_pos.z:.4f}" quat="{quat.w:.4f} {quat.x:.4f} {quat.y:.4f} {quat.z:.4f}">\n'
        xml_body_content += f'\t\t\t<geom name="{body_name}_geom" type="capsule" pos="0 0 0" size="{rope_r:.4f} {half_length:.4f}" />\n'
        if i < len(segments) - 1:
            xml_body_content += f'\t\t\t<joint name="joint_{i:03d}" type="ball" pos="0 0 {half_length:.4f}" />\n'
        if i == 0 or i == len(segments) - 1:
            xml_body_content += f'\t\t\t<site name="anchor_{body_name}" pos="0 0 0" size="{rope_r*1.5:.4f}" rgba="0 1 0 1" />\n'
        xml_body_content += '\t\t</body>\n'
    return xml_body_content, first_pos, last_pos


def _mesh_polyline_world(obj):
    """从网格边构建一条最长折线（世界坐标）。简化实现：
    - 选择度为1的端点作为起止；若无端点，用最远点对近似。
    - 贪心沿边前进，生成有序顶点列表。
    """
    me = obj.data
    verts = [obj.matrix_world @ Vector(v.co) for v in me.vertices]
    deg = [0]*len(me.vertices)
    adj = [[] for _ in me.vertices]
    for e in me.edges:
        i,j = e.vertices
        deg[i]+=1; deg[j]+=1
        adj[i].append(j); adj[j].append(i)
    endpoints = [i for i,d in enumerate(deg) if d==1]
    if not endpoints:
        # 取近似最远点对
        if not verts:
            return []
        # 采样法：选取索引步长
        step = max(1, len(verts)//50)
        far_i, far_j, far_d = 0, 0, -1
        for i in range(0, len(verts), step):
            vi = verts[i]
            for j in range(i+step, len(verts), step):
                d = (vi - verts[j]).length
                if d > far_d:
                    far_d = d; far_i = i; far_j = j
        start = far_i
    else:
        start = endpoints[0]
    ordered = [start]
    prev = -1
    cur = start
    visited = {start}
    while True:
        nbrs = [n for n in adj[cur] if n!=prev]
        next_v = None
        # 选未访问邻居；若都访问过，结束
        for n in nbrs:
            if n not in visited:
                next_v = n
                break
        if next_v is None:
            break
        ordered.append(next_v)
        visited.add(next_v)
        prev, cur = cur, next_v
        if len(ordered) > 200000:
            break
    return [verts[i] for i in ordered]


def _order_component(indices, adj):
    """给定连通分量的顶点索引和邻接表，返回有序折线顶点索引列表"""
    if not indices:
        return []
    idx_set = set(indices)
    deg = {i: len([n for n in adj[i] if n in idx_set]) for i in idx_set}
    endpoints = [i for i, d in deg.items() if d == 1]
    start = endpoints[0] if endpoints else indices[0]
    ordered = [start]
    prev = -1
    cur = start
    visited = {start}
    while True:
        nbrs = [n for n in adj[cur] if n in idx_set and n != prev]
        nxt = None
        for n in nbrs:
            if n not in visited:
                nxt = n
                break
        if nxt is None:
            break
        ordered.append(nxt)
        visited.add(nxt)
        prev, cur = cur, nxt
        if len(ordered) > 200000:
            break
    return ordered


def _mesh_polylines_world_from_mesh(mesh, world_matrix):
    """从 mesh 提取多条折线（按连通分量拆分），返回 [ [Vector,...], ... ]"""
    verts = [world_matrix @ Vector(v.co) for v in mesh.vertices]
    if not verts:
        return []
    adj = [[] for _ in mesh.vertices]
    for e in mesh.edges:
        i, j = e.vertices
        adj[i].append(j)
        adj[j].append(i)
    # 连通分量
    visited = set()
    polylines = []
    for i in range(len(mesh.vertices)):
        if i in visited:
            continue
        # BFS 收集分量
        comp = []
        q = [i]
        visited.add(i)
        while q:
            u = q.pop()
            comp.append(u)
            for v in adj[u]:
                if v not in visited:
                    visited.add(v)
                    q.append(v)
        ordered_idx = _order_component(comp, adj)
        polylines.append([verts[k] for k in ordered_idx])
    return polylines


def _curve_polylines_world(obj):
    """基于曲线中心线导出多条折线，按连通分量拆分，避免多绳被混成一条。"""
    data = obj.data
    bevel_backup = getattr(data, "bevel_depth", None)
    extrude_backup = getattr(data, "extrude", None)
    if bevel_backup is not None:
        data.bevel_depth = 0.0
    if extrude_backup is not None:
        data.extrude = 0.0

    depsgraph = None
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
    except Exception:
        pass
    eval_obj = obj.evaluated_get(depsgraph) if depsgraph else obj
    temp_mesh = eval_obj.to_mesh()

    if bevel_backup is not None:
        data.bevel_depth = bevel_backup
    if extrude_backup is not None:
        data.extrude = extrude_backup

    if temp_mesh is None:
        return []
    try:
        world_matrix = obj.matrix_world
        return _mesh_polylines_world_from_mesh(temp_mesh, world_matrix)
    finally:
        eval_obj.to_mesh_clear()


def _resample_polyline(points, step_len):
    if len(points) < 2 or step_len <= 0:
        return points
    res = [points[0].copy()]
    acc = 0.0
    for i in range(1, len(points)):
        a, b = points[i-1], points[i]
        seg = (b - a)
        seg_len = seg.length
        if seg_len <= 1e-9:
            continue
        dirv = seg/seg_len
        while acc + seg_len >= step_len:
            need = step_len - acc
            res.append(a + dirv * need)
            a = a + dirv * need
            seg_len -= need
            acc = 0.0
        acc += seg_len
    if (res[-1] - points[-1]).length > 1e-6:
        res.append(points[-1].copy())
    return res


def _points_from_segments(segments):
    return [obj.matrix_world.translation.copy() for obj in segments]


def _deduplicate_points(points, eps=1e-6):
    if not points:
        return []
    dedup = [points[0].copy()]
    for p in points[1:]:
        if (p - dedup[-1]).length > eps:
            dedup.append(p.copy())
    return dedup


def _estimate_step(points, fallback):
    deltas = []
    for i in range(1, len(points)):
        seg_len = (points[i] - points[i-1]).length
        if seg_len > 1e-6:
            deltas.append(seg_len)
    if deltas:
        avg = sum(deltas) / len(deltas)
        print(f"Adaptive segment length from points: {avg:.4f} m (n={len(deltas)})")
        return avg
    return fallback


def _downsample_points(points, target):
    if len(points) <= target or target < 2:
        return points
    last_index = len(points) - 1
    result = []
    for i in range(target):
        idx = round(i * last_index / (target - 1))
        result.append(points[idx].copy())
    return result


def _average_step(points):
    if len(points) < 2:
        return 0.0
    total = 0.0
    for i in range(1, len(points)):
        total += (points[i] - points[i-1]).length
    return total / (len(points) - 1)


def generate_mjcf_from_polyline(points, rope_r, step_len, name_prefix=""):
    """
    根据中心线点列生成真正的链式绳索结构：
    - seg_000：带 freejoint，附在 worldbody。
    - 其余段作为上一段的子 body，通过 ball 关节与父节点相连，形成树状刚体链接。
    - 每段使用本地坐标的 capsule，端点加 site 标记。
    返回 (body_xml, start_pos, end_pos, equality_xml, contact_xml)。
    """
    n = len(points)
    if n < 2:
        return "", None, None, "", ""

    if name_prefix and not name_prefix.endswith("_"):
        name_prefix = name_prefix + "_"

    start_pos = points[0].copy()
    end_pos = points[-1].copy()
    body_xml = ""

    def _capsule_block(name: str, next_vec: Vector, endpoint: bool, is_first: bool, indent: str) -> str:
        if next_vec is None or next_vec.length <= 1e-9:
            # 极端情况：退化为球。碰撞层不渲染，只渲染视觉层。
            vis_r = rope_r * max(1.0, VISUAL_RADIUS_SCALE)
            block = (
                f'{indent}<geom name="{name}_col" class="rope_col" type="sphere" size="{rope_r:.4f}" />\n'
                f'{indent}<geom name="{name}_vis" class="rope_vis" type="sphere" size="{vis_r:.4f}" />\n'
            )
        else:
            # fromto 胶囊：MuJoCo 的 size 只需要 radius（长度由 fromto 端点决定）
            block = (
                f'{indent}<geom name="{name}_col" class="rope_col" type="capsule" '
                f'fromto="0 0 0 {next_vec.x:.4f} {next_vec.y:.4f} {next_vec.z:.4f}" '
                f'size="{rope_r:.4f}" />\n'
            )
            # 视觉外皮：不参与碰撞/质量，半径略大，配合纹理更像绳子
            vis_r = rope_r * max(1.0, VISUAL_RADIUS_SCALE)
            # 视觉遮缝：从当前点向“后方”轻微延伸，覆盖与上一段的接缝
            if not is_first and VISUAL_OVERLAP_FRAC > 0:
                try:
                    seg_len = float(next_vec.length)
                    overlap = max(0.0, min(0.10, VISUAL_OVERLAP_FRAC)) * seg_len
                    if overlap > 1e-9:
                        dirv = next_vec / seg_len
                        start = -dirv * overlap
                        end = next_vec
                        block += (
                            f'{indent}<geom name="{name}_vis" class="rope_vis" type="capsule" '
                            f'fromto="{start.x:.4f} {start.y:.4f} {start.z:.4f} {end.x:.4f} {end.y:.4f} {end.z:.4f}" '
                            f'size="{vis_r:.4f}" />\n'
                        )
                    else:
                        block += (
                            f'{indent}<geom name="{name}_vis" class="rope_vis" type="capsule" '
                            f'fromto="0 0 0 {next_vec.x:.4f} {next_vec.y:.4f} {next_vec.z:.4f}" '
                            f'size="{vis_r:.4f}" />\n'
                        )
                except Exception:
                    block += (
                        f'{indent}<geom name="{name}_vis" class="rope_vis" type="capsule" '
                        f'fromto="0 0 0 {next_vec.x:.4f} {next_vec.y:.4f} {next_vec.z:.4f}" '
                        f'size="{vis_r:.4f}" />\n'
                    )
            else:
                block += (
                    f'{indent}<geom name="{name}_vis" class="rope_vis" type="capsule" '
                    f'fromto="0 0 0 {next_vec.x:.4f} {next_vec.y:.4f} {next_vec.z:.4f}" '
                    f'size="{vis_r:.4f}" />\n'
                )
        if endpoint:
            block += f'{indent}<site name="anchor_{name}" pos="0 0 0" size="{(rope_r*1.5):.4f}" rgba="0 1 0 1" />\n'
        return block

    indent = '\t\t'
    identity_quat = "1.0000 0.0000 0.0000 0.0000"
    body_xml += (
        f'{indent}<body name="{name_prefix}seg_000" pos="{start_pos.x:.4f} {start_pos.y:.4f} {start_pos.z:.4f}" '
        f'quat="{identity_quat}">\n'
    )
    body_xml += f'{indent}\t<freejoint name="{name_prefix}free_000"/>\n'
    first_vec = points[1] - points[0]
    body_xml += _capsule_block(f'{name_prefix}seg_000', first_vec, True, True, indent + '\t')

    for i in range(1, n):
        prev = points[i - 1]
        cur = points[i]
        offset = cur - prev
        indent += '\t'
        body_xml += (
            f'{indent}<body name="{name_prefix}seg_{i:03d}" pos="{offset.x:.4f} {offset.y:.4f} {offset.z:.4f}" '
            f'quat="{identity_quat}">\n'
        )
        body_xml += f'{indent}\t<joint name="{name_prefix}joint_{i-1:03d}" type="ball" pos="0 0 0" />\n'
        next_vec = points[i + 1] - cur if i < n - 1 else None
        body_xml += _capsule_block(f"{name_prefix}seg_{i:03d}", next_vec, i == n - 1, False, indent + '\t')

    for i in reversed(range(n)):
        indent = '\t\t' + '\t' * i
        body_xml += f'{indent}</body>\n'

    # 邻接碰撞排除：只排除相邻段
    contact_xml = ""
    for i in range(n - 1):
        contact_xml += f'\t\t<exclude body1="{name_prefix}seg_{i:03d}" body2="{name_prefix}seg_{i+1:03d}"/>\n'

    last_body_name = f"{name_prefix}seg_{n-1:03d}"
    return body_xml, points[0], points[-1], last_body_name, "", contact_xml


def _auto_rope_texture_path() -> str:
    """
    返回一个“相对 OUTPUT_MJCF_PATH 的路径”，让 MuJoCo 能正确从生成的 XML 位置加载贴图。
    优先使用仓库 material/rope_01.png；若不存在，返回空字符串表示使用 builtin checker。
    """
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # 优先选“更像绳股方向”的贴图，其次再退到其它工业绳贴图
        candidates = [
            os.path.join(script_dir, "material", "Industrial_rope_01.png"),
            os.path.join(script_dir, "material", "IndustrialRope_02.png"),
            os.path.join(script_dir, "material", "rope_01.png"),
        ]
        repo_tex = next((p for p in candidates if os.path.isfile(p)), "")
        if not repo_tex:
            return ""
        out_dir = os.path.dirname(os.path.abspath(OUTPUT_MJCF_PATH)) or os.getcwd()
        rel = os.path.relpath(repo_tex, start=out_dir)
        return rel
    except Exception as exc:
        print(f"Texture auto-detect failed: {exc}")
        return ""


def main():
    global ROPE_TEXTURE_FILE
    if not ROPE_TEXTURE_FILE:
        ROPE_TEXTURE_FILE = _auto_rope_texture_path()
    if ROPE_TEXTURE_FILE:
        print(f"Using rope texture file: {ROPE_TEXTURE_FILE}")
    else:
        print("Using rope texture: builtin checker (no texture file found / provided).")
    rope_obj = None
    polylines = []

    # A. 用户手动选择的对象优先
    selected = bpy.context.selected_objects
    if selected:
        rope_obj = selected[0]
        print(f"Source: User selected object '{rope_obj.name}' ({rope_obj.type})")
        if rope_obj.type == 'CURVE':
            polylines = _curve_polylines_world(rope_obj)
        elif rope_obj.type == 'MESH':
            print("WARNING: Selected object is a MESH. Prefer selecting the CURVE for smoother centerline.")
            polylines = _mesh_polylines_world_from_mesh(rope_obj.data, rope_obj.matrix_world)
        else:
            print("WARNING: Selected object is neither CURVE nor MESH; attempting fallback.")
            rope_obj = None
            polylines = []

    # B. 自动寻找可用曲线
    if not polylines:
        all_curves = [obj for obj in bpy.data.objects if obj.type == 'CURVE']
        if all_curves:
            rope_obj = all_curves[0]
            print(f"Source: Auto-detected CURVE '{rope_obj.name}'")
            polylines = _curve_polylines_world(rope_obj)

    # C. 使用 Mesh 段或单 Mesh 回退
    if not polylines:
        segments = get_rope_segments()
        if len(segments) >= 2:
            rope_obj = segments[0]
            poly = _deduplicate_points(_points_from_segments(segments))
            polylines = [poly] if poly else []
            print(f"Source: Mesh segments group starting with '{rope_obj.name}'")
        elif len(segments) == 1:
            rope_obj = segments[0]
            print(f"Source: Single mesh fallback '{rope_obj.name}'")
            polylines = _curve_polylines_world(rope_obj) if rope_obj.type == 'CURVE' else _mesh_polylines_world_from_mesh(rope_obj.data, rope_obj.matrix_world)

    # 过滤长度不足的
    polylines = [pline for pline in polylines if len(pline) >= 2]

    if not polylines or rope_obj is None:
        print("ERROR: Could not find valid rope geometry (need at least one polyline with >=2 points).")
        sys.exit(1)

    # 自动尺寸
    rope_r, seg_len_hint = auto_calculate_dimensions(rope_obj)
    if CLI_RADIUS is not None:
        rope_r = CLI_RADIUS
        print(f"CLI override: radius -> {rope_r:.4f}m")
    if CLI_SEGMENT is not None:
        seg_len_hint = CLI_SEGMENT
        print(f"CLI override: segment length -> {seg_len_hint:.4f}m")

    # 重采样并生成 MJCF（支持多条绳）
    body_blocks = []
    mocap_blocks = []
    contact_blocks = []
    equality_welds = []
    for idx, pline in enumerate(polylines):
        name_prefix = f"rope{idx}_"
        base_step = max(seg_len_hint, rope_r * 2.0)
        resampled = _resample_polyline(pline, base_step)
        if len(resampled) < 5:
            base_step *= 0.5
            resampled = _resample_polyline(pline, base_step)
        if len(resampled) > MAX_EXPORT_SEGMENTS:
            resampled = _downsample_points(resampled, MAX_EXPORT_SEGMENTS)
        segment_step = _average_step(resampled)
        if segment_step <= 1e-6:
            segment_step = base_step
        segment_step = max(segment_step, rope_r * 2.0)
        mjcf_body_content, start_pos, end_pos, last_body_name, internal_equality_xml, internal_contact_xml = generate_mjcf_from_polyline(resampled, rope_r, segment_step, name_prefix=name_prefix)
        expected_ball_joints = max(0, len(resampled) - 1)
        actual_ball_joints = mjcf_body_content.count('type="ball"')
        print(f"[rope {idx}] Generated {len(resampled)} points -> expect {expected_ball_joints} joints, got {actual_ball_joints}.")
        body_blocks.append(mjcf_body_content)
        mocap_blocks.append(f"""
        <body name="{name_prefix}mocap_left" mocap="true" pos="{start_pos.x:.4f} {start_pos.y:.4f} {start_pos.z:.4f}">
            <geom type="sphere" size="0.05" rgba="1 0 0 0.5" />
        </body>
        <body name="{name_prefix}mocap_right" mocap="true" pos="{end_pos.x:.4f} {end_pos.y:.4f} {end_pos.z:.4f}">
            <geom type="sphere" size="0.05" rgba="1 0 0 0.5" />
        </body>
""")
        contact_blocks.append(internal_contact_xml)
        equality_welds.append(f'        <weld body1="{name_prefix}mocap_left" body2="{name_prefix}seg_000"/>')
        equality_welds.append(f'        <weld body1="{name_prefix}mocap_right" body2="{last_body_name}"/>')

    # 说明：
    # - 本 exporter 的胶囊 geom 使用 fromto，长度由端点决定，不再依赖 size 的 half_length。
    # - default 里只放“物理参数/材质”，不强行固定尺寸。

    # 5. 组合 XML（插入 mocap 与 weld equality）
    # 渲染/材质：默认用内置 checker 生成“纤维/编织感”，不需要外部图片文件
    MJCF_TEMPLATE_START = f"""
<mujoco model=\"rope_knot\">
    <compiler angle=\"radian\" inertiafromgeom=\"true\" />
    <option integrator=\"Euler\" timestep=\"0.0001\" gravity=\"0 0 -9.81\" solver=\"Newton\" iterations=\"30\" tolerance=\"1e-4\">
        <flag contact=\"enable\" />
    </option>

    <visual>
        <quality shadowsize=\"4096\" />
        <headlight ambient=\"0.25 0.25 0.25\" diffuse=\"0.7 0.7 0.7\" specular=\"0.2 0.2 0.2\" />
        <rgba haze=\"0.15 0.15 0.15 1\" />
    </visual>

    <asset>
        <!-- 绳子纹理：默认程序纹理；若设置环境变量 ROPE_TEXTURE_FILE，则会用 file=... -->
        {(
            f'<texture name="rope_tex" type="2d" file="{ROPE_TEXTURE_FILE}" />'
            if ROPE_TEXTURE_FILE else
            '<texture name="rope_tex" type="2d" builtin="checker" width="1024" height="1024" '
            'rgb1="0.62 0.46 0.28" rgb2="0.50 0.37 0.22" />'
        )}
        <material name=\"rope_mat\" texture=\"rope_tex\" texrepeat=\"{ROPE_TEXREPEAT_U:.1f} {ROPE_TEXREPEAT_V:.1f}\" texuniform=\"true\"
                  specular=\"0.06\" shininess=\"0.03\" reflectance=\"0.01\" />
    </asset>

    <default>
        <joint damping=\"0.05\" />

        <!-- 绳子碰撞层：只参与物理，不渲染（避免与视觉层叠加导致怪异高光/摩尔纹） -->
        <default class=\"rope_col\">
            <geom type=\"capsule\" mass=\"0.0020\"
                  friction=\"{ROPE_FRICTION}\" density=\"{ROPE_DENSITY}\"
                  contype=\"1\" conaffinity=\"1\"
                  rgba=\"0 0 0 0\"
                  solref=\"0.02 1\" solimp=\"0.9 0.95 0.001\" />
        </default>

        <!-- 绳子视觉层：只渲染不碰撞/不计质量 -->
        <default class=\"rope_vis\">
            <geom type=\"capsule\" material=\"rope_mat\" rgba=\"1 1 1 1\" contype=\"0\" conaffinity=\"0\" mass=\"0\" />
        </default>
    </default>

    <worldbody>
        <light pos=\"0 0 1\" dir=\"0 0 -1\" directional=\"true\" castshadow=\"true\" />
        <geom type=\"plane\" size=\"1 1 0.1\" rgba=\".9 0 0 1\" />
"""

    mocap_xml = "\n".join(mocap_blocks)

    contact_xml = f"""
    <contact>
{''.join(contact_blocks)}
    </contact>
"""

    equality_xml = f"""
    <equality>
{chr(10).join(equality_welds)}
    </equality>
"""

    footer_xml = """
    <actuator>
    </actuator>
</mujoco>
"""

    final_mjcf = MJCF_TEMPLATE_START + mocap_xml + "".join(body_blocks) + "    </worldbody>\n" + contact_xml + equality_xml + footer_xml

    # 6. 写入文件
    try:
        with open(OUTPUT_MJCF_PATH, 'w') as f:
            f.write(final_mjcf)
        print(f"Successfully exported MJCF structure to: {OUTPUT_MJCF_PATH}")
        if actual_ball_joints <= 1:
            print("ERROR: Export completed but detected <=1 ball joint; please inspect the input curve/mesh.")
    except Exception as e:
        print(f"Error writing file: {e}")


if __name__ == "__main__":
    main()
