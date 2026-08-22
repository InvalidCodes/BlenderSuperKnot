"""Export a rope curve from a ``.blend`` file to an MJCF (MuJoCo XML) model.

    blender --background build/trefoil.blend \
        --python superknot/blender/export_mjcf.py -- --out build/trefoil.xml

The rope becomes a chain of capsule bodies: ``seg_000`` carries a freejoint and
is parented to the world, and every later segment is a child of the previous
one connected by a ball joint.  Each segment gets two geoms — a collision
capsule that is never drawn, and a slightly fatter textured visual capsule that
never collides — so contact behaviour and appearance can be tuned separately.

Both endpoints are welded to mocap bodies; :mod:`superknot.sim.tighten` moves
those to pull the rope.  A static table plane is placed just under the rope's
lowest point so it neither falls from mid-air nor starts interpenetrating.
"""

import argparse
import os
import pathlib
import sys

# Blender puts the script's own directory on sys.path, not the repository root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import bpy
from mathutils import Vector

from superknot.blender import _bootstrap

DEFAULT_TEXTURE_CANDIDATES = (
    "Industrial_rope_01.png",
    "IndustrialRope_02.png",
)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="export_mjcf", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--out", required=True, help="output .xml path")
    parser.add_argument(
        "--object", default=None, help="name of the rope object (default: auto-detect)"
    )

    geom = parser.add_argument_group("geometry (defaults come from the asset)")
    geom.add_argument("--radius", type=float, default=None, help="rope radius, metres")
    geom.add_argument(
        "--segment", type=float, default=None, help="target capsule length, metres"
    )
    geom.add_argument(
        "--max-segments", type=int, default=None, help="cap on exported capsules"
    )

    phys = parser.add_argument_group("physics")
    phys.add_argument("--rope-friction", default="0.8 0.02 0.001")
    phys.add_argument("--table-friction", default="0.9 0.02 0.001")
    phys.add_argument("--joint-damping", type=float, default=0.05)
    phys.add_argument("--joint-stiffness", type=float, default=0.002)
    phys.add_argument("--rope-density", type=float, default=1000.0)
    phys.add_argument("--table-clearance", type=float, default=0.0005)

    look = parser.add_argument_group("appearance (no effect on physics)")
    look.add_argument("--visual-radius-scale", type=float, default=1.0)
    look.add_argument("--visual-overlap-frac", type=float, default=0.02)
    look.add_argument("--texture-repeat-u", type=float, default=200.0)
    look.add_argument("--texture-repeat-v", type=float, default=20.0)
    look.add_argument(
        "--texture",
        default="auto",
        help="'auto' picks a bundled rope texture, '' uses the procedural checker",
    )

    return parser.parse_args(_bootstrap.script_args(argv))


# ---------------------------------------------------------------------------
# Centerline extraction
# ---------------------------------------------------------------------------


def _order_component(indices, adj):
    """Order one connected component's vertices into a walkable polyline."""
    if not indices:
        return []
    idx_set = set(indices)
    deg = {i: len([n for n in adj[i] if n in idx_set]) for i in idx_set}
    endpoints = [i for i, d in deg.items() if d == 1]
    start = endpoints[0] if endpoints else indices[0]
    ordered = [start]
    prev, cur = -1, start
    visited = {start}
    while True:
        nxt = next(
            (n for n in adj[cur] if n in idx_set and n != prev and n not in visited),
            None,
        )
        if nxt is None:
            break
        ordered.append(nxt)
        visited.add(nxt)
        prev, cur = cur, nxt
        if len(ordered) > 200000:
            break
    return ordered


def _mesh_polylines_world(mesh, world_matrix):
    """Split a mesh into one ordered world-space polyline per component."""
    verts = [world_matrix @ Vector(v.co) for v in mesh.vertices]
    if not verts:
        return []
    adj = [[] for _ in mesh.vertices]
    for edge in mesh.edges:
        i, j = edge.vertices
        adj[i].append(j)
        adj[j].append(i)

    visited = set()
    polylines = []
    for i in range(len(mesh.vertices)):
        if i in visited:
            continue
        comp, queue = [], [i]
        visited.add(i)
        while queue:
            u = queue.pop()
            comp.append(u)
            for v in adj[u]:
                if v not in visited:
                    visited.add(v)
                    queue.append(v)
        polylines.append([verts[k] for k in _order_component(comp, adj)])
    return polylines


def _curve_polylines_world(obj):
    """Sample a curve's centerline, temporarily removing its bevel."""
    data = obj.data
    bevel_backup = getattr(data, "bevel_depth", None)
    extrude_backup = getattr(data, "extrude", None)
    if bevel_backup is not None:
        data.bevel_depth = 0.0
    if extrude_backup is not None:
        data.extrude = 0.0

    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
    except Exception:
        depsgraph = None
    eval_obj = obj.evaluated_get(depsgraph) if depsgraph else obj
    temp_mesh = eval_obj.to_mesh()

    if bevel_backup is not None:
        data.bevel_depth = bevel_backup
    if extrude_backup is not None:
        data.extrude = extrude_backup

    if temp_mesh is None:
        return []
    try:
        return _mesh_polylines_world(temp_mesh, obj.matrix_world)
    finally:
        eval_obj.to_mesh_clear()


def _resample(points, step_len):
    """Resample a polyline to a uniform arc-length step."""
    if len(points) < 2 or step_len <= 0:
        return points
    res = [points[0].copy()]
    acc = 0.0
    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        seg = b - a
        seg_len = seg.length
        if seg_len <= 1e-9:
            continue
        dirv = seg / seg_len
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


def _downsample(points, target):
    if len(points) <= target or target < 2:
        return points
    last = len(points) - 1
    return [points[round(i * last / (target - 1))].copy() for i in range(target)]


def find_rope_object(name=None):
    """Locate the rope: explicit name, then selection, then the only curve."""
    if name:
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise SystemExit(f"No object named {name!r} in this .blend")
        return obj
    selected = [o for o in bpy.context.selected_objects if o.type in {"CURVE", "MESH"}]
    if selected:
        return selected[0]
    curves = [o for o in bpy.data.objects if o.type == "CURVE"]
    if curves:
        return curves[0]
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if meshes:
        return meshes[0]
    raise SystemExit("No CURVE or MESH object found to export")


def polylines_for(obj):
    if obj.type == "CURVE":
        return _curve_polylines_world(obj)
    if obj.type == "MESH":
        print("NOTE: exporting a MESH; a CURVE gives a smoother centerline.")
        return _mesh_polylines_world(obj.data, obj.matrix_world)
    raise SystemExit(f"Object {obj.name!r} is a {obj.type}, expected CURVE or MESH")


# ---------------------------------------------------------------------------
# MJCF generation
# ---------------------------------------------------------------------------


def build_rope_bodies(points, rope_r, args, name_prefix=""):
    """Emit the nested body chain, its self-contact exclusions and endpoints.

    Returns ``(body_xml, start_pos, end_pos, last_body_name, contact_xml)``.
    """
    n = len(points)
    if n < 2:
        raise SystemExit("Rope needs at least two points")

    if name_prefix and not name_prefix.endswith("_"):
        name_prefix += "_"

    vis_r = rope_r * max(1.0, args.visual_radius_scale)
    overlap_frac = max(0.0, min(0.10, args.visual_overlap_frac))

    def capsule_block(name, next_vec, endpoint, is_first, indent):
        if next_vec is None or next_vec.length <= 1e-9:
            # Degenerate segment: fall back to spheres.
            block = (
                f'{indent}<geom name="{name}_col" class="rope_col" type="sphere" '
                f'size="{rope_r:.4f}" />\n'
                f'{indent}<geom name="{name}_vis" class="rope_vis" type="sphere" '
                f'size="{vis_r:.4f}" />\n'
            )
        else:
            # `fromto` capsules take their length from the endpoints, so `size`
            # only carries the radius.
            block = (
                f'{indent}<geom name="{name}_col" class="rope_col" type="capsule" '
                f'fromto="0 0 0 {next_vec.x:.4f} {next_vec.y:.4f} {next_vec.z:.4f}" '
                f'size="{rope_r:.4f}" />\n'
            )
            # The visual skin extends slightly backwards to hide the seam with
            # the previous segment.
            seg_len = float(next_vec.length)
            overlap = overlap_frac * seg_len
            if not is_first and overlap > 1e-9:
                start = -(next_vec / seg_len) * overlap
            else:
                start = Vector((0.0, 0.0, 0.0))
            block += (
                f'{indent}<geom name="{name}_vis" class="rope_vis" type="capsule" '
                f'fromto="{start.x:.4f} {start.y:.4f} {start.z:.4f} '
                f'{next_vec.x:.4f} {next_vec.y:.4f} {next_vec.z:.4f}" '
                f'size="{vis_r:.4f}" />\n'
            )
        if endpoint:
            block += (
                f'{indent}<site name="anchor_{name}" pos="0 0 0" '
                f'size="{rope_r * 1.5:.4f}" rgba="0 1 0 1" />\n'
            )
        return block

    identity_quat = "1.0000 0.0000 0.0000 0.0000"
    start_pos, end_pos = points[0].copy(), points[-1].copy()

    indent = "\t\t"
    body_xml = (
        f'{indent}<body name="{name_prefix}seg_000" '
        f'pos="{start_pos.x:.4f} {start_pos.y:.4f} {start_pos.z:.4f}" '
        f'quat="{identity_quat}">\n'
        f'{indent}\t<freejoint name="{name_prefix}free_000"/>\n'
    )
    body_xml += capsule_block(
        f"{name_prefix}seg_000", points[1] - points[0], True, True, indent + "\t"
    )

    for i in range(1, n):
        offset = points[i] - points[i - 1]
        indent += "\t"
        body_xml += (
            f'{indent}<body name="{name_prefix}seg_{i:03d}" '
            f'pos="{offset.x:.4f} {offset.y:.4f} {offset.z:.4f}" '
            f'quat="{identity_quat}">\n'
            f'{indent}\t<joint name="{name_prefix}joint_{i - 1:03d}" '
            f'type="ball" pos="0 0 0" />\n'
        )
        next_vec = points[i + 1] - points[i] if i < n - 1 else None
        body_xml += capsule_block(
            f"{name_prefix}seg_{i:03d}", next_vec, i == n - 1, False, indent + "\t"
        )

    for i in reversed(range(n)):
        body_xml += "\t\t" + "\t" * i + "</body>\n"

    # Neighbouring and next-to-neighbouring capsules belong to the same local
    # stretch of rope; their rounded caps overlap on tight bends and must not
    # register as self-contact.  Segments 3 or more apart still collide, which
    # is what makes the knot hold.
    contact_xml = ""
    for gap in (1, 2):
        for i in range(n - gap):
            contact_xml += (
                f'\t\t<exclude body1="{name_prefix}seg_{i:03d}" '
                f'body2="{name_prefix}seg_{i + gap:03d}"/>\n'
            )

    return body_xml, start_pos, end_pos, f"{name_prefix}seg_{n - 1:03d}", contact_xml


def resolve_texture(spec, out_path):
    """Return a texture path relative to the MJCF, or '' for the checker."""
    if spec == "":
        return ""
    texture_dir = os.path.join(str(_bootstrap.REPO_ROOT), "textures")
    if spec == "auto":
        candidates = [os.path.join(texture_dir, n) for n in DEFAULT_TEXTURE_CANDIDATES]
    else:
        candidates = [spec, os.path.join(texture_dir, spec)]
    found = next((p for p in candidates if os.path.isfile(p)), "")
    if not found:
        return ""
    out_dir = os.path.dirname(os.path.abspath(out_path)) or os.getcwd()
    return os.path.relpath(found, start=out_dir)


def assemble_mjcf(bodies, mocaps, welds, contacts, table_z, texture, args):
    texture_asset = (
        f'<texture name="rope_tex" type="2d" file="{texture}" />'
        if texture
        else (
            '<texture name="rope_tex" type="2d" builtin="checker" '
            'width="1024" height="1024" rgb1="0.62 0.46 0.28" rgb2="0.50 0.37 0.22" />'
        )
    )
    header = f"""<mujoco model="rope_knot">
    <compiler angle="radian" inertiafromgeom="true" />
    <option integrator="Euler" timestep="0.0001" gravity="0 0 -9.81" solver="Newton" iterations="30" tolerance="1e-4">
        <flag contact="enable" />
    </option>

    <visual>
        <global offwidth="1920" offheight="1080" />
        <quality shadowsize="4096" />
        <headlight ambient="0.25 0.25 0.25" diffuse="0.7 0.7 0.7" specular="0.2 0.2 0.2" />
        <rgba haze="0.15 0.15 0.15 1" />
    </visual>

    <asset>
        {texture_asset}
        <material name="rope_mat" texture="rope_tex" texrepeat="{args.texture_repeat_u:.1f} {args.texture_repeat_v:.1f}" texuniform="true"
                  specular="0.06" shininess="0.03" reflectance="0.01" />
    </asset>

    <default>
        <joint damping="{args.joint_damping:.6g}" stiffness="{args.joint_stiffness:.6g}" />

        <!-- Collision layer: physics only, never drawn. -->
        <default class="rope_col">
            <geom type="capsule" mass="0.0020"
                  friction="{args.rope_friction}" density="{args.rope_density:.6g}"
                  contype="1" conaffinity="1"
                  rgba="0 0 0 0"
                  solref="0.02 1" solimp="0.9 0.95 0.001" />
        </default>

        <!-- Visual layer: drawn only, no collision and no mass. -->
        <default class="rope_vis">
            <geom type="capsule" material="rope_mat" rgba="1 1 1 1" contype="0" conaffinity="0" mass="0" />
        </default>
    </default>

    <worldbody>
        <light pos="0 0 1" dir="0 0 -1" directional="true" castshadow="true" />
        <!-- Table auto-placed just below the rope's lowest point. -->
        <geom name="table" type="plane" pos="0 0 {table_z:.6f}"
              size="2 2 0.1" rgba=".55 .32 .16 1"
              friction="{args.table_friction}" contype="2" conaffinity="1"
              solref="0.01 1" solimp="0.95 0.99 0.001" />
"""
    return (
        header
        + "\n".join(mocaps)
        + "".join(bodies)
        + "    </worldbody>\n"
        + f"\n    <contact>\n{''.join(contacts)}    </contact>\n"
        + f"\n    <equality>\n{chr(10).join(welds)}\n    </equality>\n"
        + "\n    <actuator>\n    </actuator>\n</mujoco>\n"
    )


def main():
    args = parse_args()

    obj = find_rope_object(args.object)
    print(f"Exporting {obj.name!r} ({obj.type})")
    polylines = [p for p in polylines_for(obj) if len(p) >= 2]
    if not polylines:
        raise SystemExit("Could not extract a centerline with at least two points")

    # Geometry defaults ride along on the object; CLI flags win when given.
    rope_r = args.radius or float(obj.get("rope_radius", 0.0)) or 0.008
    seg_hint = args.segment or float(obj.get("segment_length", 0.0)) or rope_r * 2.5
    max_segments = args.max_segments or int(obj.get("max_segments", 0)) or 120
    print(
        f"radius={rope_r:.4f}m segment={seg_hint:.4f}m max_segments={max_segments} "
        f"components={len(polylines)}"
    )

    bodies, mocaps, welds, contacts, resampled_all = [], [], [], [], []
    for index, pline in enumerate(polylines):
        prefix = f"rope{index}_"
        base_step = max(seg_hint, rope_r * 2.0)
        resampled = _resample(pline, base_step)
        if len(resampled) < 5:
            resampled = _resample(pline, base_step * 0.5)
        if len(resampled) > max_segments:
            resampled = _downsample(resampled, max_segments)
        resampled_all.append(resampled)

        body_xml, start_pos, end_pos, last_body, contact_xml = build_rope_bodies(
            resampled, rope_r, args, name_prefix=prefix
        )
        joints = body_xml.count('type="ball"')
        print(f"[rope {index}] {len(resampled)} points -> {joints} ball joints")
        if joints < 1:
            raise SystemExit(f"rope {index}: no ball joints generated")

        bodies.append(body_xml)
        contacts.append(contact_xml)
        mocaps.append(
            f"""
        <body name="{prefix}mocap_left" mocap="true" pos="{start_pos.x:.4f} {start_pos.y:.4f} {start_pos.z:.4f}">
            <geom type="sphere" size="0.05" rgba="1 0 0 0.5" contype="0" conaffinity="0" mass="0" />
        </body>
        <body name="{prefix}mocap_right" mocap="true" pos="{end_pos.x:.4f} {end_pos.y:.4f} {end_pos.z:.4f}">
            <geom type="sphere" size="0.05" rgba="1 0 0 0.5" contype="0" conaffinity="0" mass="0" />
        </body>
"""
        )
        welds.append(
            f'        <weld name="{prefix}weld_left" body1="{prefix}mocap_left" '
            f'body2="{prefix}seg_000" solref="0.01 1" solimp="0.95 0.99 0.001"/>'
        )
        welds.append(
            f'        <weld name="{prefix}weld_right" body1="{prefix}mocap_right" '
            f'body2="{last_body}" solref="0.01 1" solimp="0.95 0.99 0.001"/>'
        )

    min_z = min(p.z for line in resampled_all for p in line)
    table_z = min_z - rope_r - max(0.0, args.table_clearance)
    print(f"Table plane at z={table_z:.4f}m (lowest centerline z={min_z:.4f}m)")

    texture = resolve_texture(args.texture, args.out)
    print(f"Rope texture: {texture or 'builtin checker'}")

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(
            assemble_mjcf(bodies, mocaps, welds, contacts, table_z, texture, args)
        )
    print(f"EXPORT_MJCF={args.out}")
    print(f"EXPORT_SEGMENTS={sum(len(r) for r in resampled_all)}")


if __name__ == "__main__":
    main()
