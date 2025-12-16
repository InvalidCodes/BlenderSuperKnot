bl_info = {
    "name": "Blender Knot Tight Plugin",
    "description": """Creates 3D meshes of knots from simple ASCII art descriptions.""",
    "author": "Yunfei Ge & John H. Williamson",
    "version": (0, 0, 7),
    "blender": (4, 0, 0),
    "location": "3D View > Sidebar > Knot", 
    "warning": "",
    "wiki_url": "https://github.com/johnhw/blender_knots/wiki",
    "tracker_url": "https://github.com/johnhw/blender_knots/issues",
    "category": "Object"
}

### Knot parsing
from collections import defaultdict
import bpy
from bpy.props import (StringProperty,
                        BoolProperty,
                        IntProperty,
                        FloatProperty,
                        EnumProperty,
                        PointerProperty,
                        )
from bpy.types import (Panel,
                        Operator,
                        PropertyGroup,
                        )

from bpy_extras.object_utils import AddObjectHelper, object_data_add
from mathutils import Vector
import bmesh
import json


char_dirs = {"^":(0,-1), "V":(0,1), ">":(1,0), "<":(-1,0), "O":(0,0)}
inv_dirs = {v:k for k,v in char_dirs.items()}

def nonempty(kmap, x, y):
        return [(x_off, y_off) 
                for x_off,y_off in char_dirs.values()
                if (x+x_off, y+y_off) in kmap]
    
table = """
* O ^ V > <
O   C # # # #
.   # . . . .
^   ^ ^ # U U
V   V # V U U
v   V # V U U
>   > U U > #
<   < U U # <
-   # U U > <
|   # ^ V U U
/   # > < ^ V
\   # < > V ^
+   # C C C C       
L   # L L L L
"""

# compute the table of possibilities
follow_map = {}
for line in table.splitlines():
    base_dirs = "O^V><"
    if len(line)>1:         
        chars = line.split()
        in_char,out_chars = chars[0], chars[1:]
        for i, c in enumerate(base_dirs):
            follow_map[(in_char, base_dirs[i])] = out_chars[i]
            
class KnotException(Exception):
    pass
    
class Knot:
    def parse_map(self, s):
        self.map = {}
        self.inv_map = defaultdict(list)
        self.labels = {}        
        self.crossovers = []
        self.lead_map = defaultdict(list)
        
        class Label:
            def __init__(self):
                self.label = ""
            def append(self, c):
                self.label += c             
                
        def mark_label(x,y):
            self.map[(x,y)] = 'L'
            self.inv_map['L'].append((x,y))
            self.labels[(x,y)] = label
                
        for y, line in enumerate(s.splitlines()):
            mark_invalid = False
            for x, char in enumerate(line):
                
                if not mark_invalid:
                    if char=='[':
                        mark_invalid = True
                        label = Label()              
                        mark_label(x,y)                     
                    elif not char.isspace():
                        self.map[(x,y)] = char
                        self.inv_map[char].append((x,y))
                else:                       
                    if char==']':
                        mark_invalid = False
                        mark_label(x,y)
                    else:
                        mark_label(x,y)
                        label.append(char)
                        
    def choose(self, x, y, dx, dy):
        neighbours = nonempty(self.map, x, y)         
        valid = [(vx, vy) for vx,vy in neighbours if not(vx==-dx and vy==-dy) and not(vx==0 and vy==0)]  
        
        if len(valid)<1:
            self.raise_error(x,y, "No neighbour to turn to")             
        if len(valid)>1:
            self.raise_error(x,y,"Ambiguous neighbour")          
        return valid[0]
    
    def find_heads(self):
        heads = []
        head_dirs = dict(char_dirs)
        head_dirs.update({str(d):(0,0) for d in range(10)})
        head_dirs["v"] = head_dirs["V"]
        
        for char, (x_off,y_off) in head_dirs.items():
            locations = self.inv_map[char]
            for x,y in locations:                           
                if x_off==0 and y_off==0:
                    x_off, y_off = self.choose(x,y,0,0)
                    if char.isdigit():
                        heads.append((x,y,x_off,y_off,0,char))
                    else:
                        heads.append((x,y,x_off,y_off,0,""))
                else:
                    prev_lead = self.map.get((x-x_off, y-y_off), None)                     
                    if prev_lead is None:                          
                        heads.append((x,y,x_off,y_off,0,""))
                        
        return sorted(heads, key=lambda x:(x[1], x[0]))
        
    def raise_error(self, x, y, msg=""):
        k = 3
        n = 6
        str_lines = [msg.center(n*2)]
        
        for i in range(-n,n+1):
            line = []
            for j in range(-n, n+1):
                if (abs(j)==k and abs(i)<k) or (abs(j)<k and abs(i)==k):
                    line.append("@")
                else:
                    char = self.map.get((x+j,y+i))
                    char = char or " "
                    line.append(char)
            str_lines.append("".join(line) + "\n")
            
        raise KnotException("".join(str_lines))
    
    def trace_leads(self):
        heads = self.find_heads()       
        self.leads = []
        self.over_map = defaultdict(list)
        ix = 0
        
        for head in heads:
            lead = []
            x,y,dx,dy,z,name = head
                
            lead.append((x,y,dx,dy,z,name))
            x,y = x+dx, y+dy
            while (x,y) in self.map:                          
                char = self.map.get((x,y))                       
                dir_char = inv_dirs[(dx,dy)]
                
                action = follow_map.get((char, dir_char))
                
                if action in char_dirs:
                    dx, dy = char_dirs[action]
                    z = 0
                elif action=='L':
                    name = self.labels[(x,y)].label
                elif action=='U':
                    z = -1
                    self.crossovers.append((x,y))
                elif action=='C':
                    dx, dy = self.choose(x,y,dx,dy)
                    z = 0
                elif action=='.':
                    break
                elif action=='#':
                    self.raise_error(x,y, "Invalid direction") 
                elif action is None:
                    self.raise_error(x,y,"Character %s unexpected"%char)
                    
                lead.append((x,y,dx,dy,z,name))
                self.over_map[(x,y)].append((ix, dx, dy, z))
                self.lead_map[(x,y)].append((lead, ix))
                ix += 1
                x, y = x+dx, y+dy
            self.leads.append(lead)
                
            
    def __init__(self, s):
        self.parse_map(s)
        self.trace_leads()
            
    def is_crossing(self, x, y):
        cross = self.over_map.get((x,y))
        return cross is not None and len(cross)>1
    
        
### END knot parsing

    def compute_gauss_code(self):
        """
        Return Gauss code extracted from the parsed ASCII diagram.
        Each crossing gets a deterministic id; we emit +id for over,
        -id for under along each oriented lead.
        Output: (sequences, crossing_ids, crossing_verts_by_id) where
            sequences: list of lists of signed ints (one per lead)
            crossing_ids: {(x,y): id}
            crossing_verts_by_id: {id: [vertex_ix_over, vertex_ix_under, ...]}
        """
        # Identify crossings and give them stable indices (row-major)
        crossings = [(coord, entries) for coord, entries in self.over_map.items() if len(entries) > 1]
        crossings_sorted = sorted(crossings, key=lambda c: (c[0][1], c[0][0]))
        crossing_ids = {coord: idx + 1 for idx, (coord, _) in enumerate(crossings_sorted)}
        crossing_verts_by_id = {}
        for coord, entries in crossings_sorted:
            cid = crossing_ids[coord]
            # entries are (ix, dx, dy, z); ix corresponds to the vertex index created by add_knot
            crossing_verts_by_id[cid] = [int(e[0]) for e in entries]

        sequences = []
        for lead in self.leads:
            seq = []
            for x, y, dx, dy, z, name in lead:
                cid = crossing_ids.get((x, y))
                if cid is None:
                    continue
                # z == -1 marks the under strand
                sign = -1 if z == -1 else 1
                seq.append(sign * cid)
            if seq:
                sequences.append(seq)

        return sequences, crossing_ids, crossing_verts_by_id

### Blender Python interface

def _on_prop_update(self, context):
    try:
        _apply_settings_to_active(context)
    except Exception:
        pass

class KnotSettings(PropertyGroup):
    
    knot_text: StringProperty(
        name="Knot",
        description="Select knot to choose",
        default=""
    )
    
    scale: FloatProperty(
        name="Scale",
        description="Scaling of knot",
        default=0.3,
        min=0.0,
        max=5.0
    )

    extrude: BoolProperty(
        name="Extrude",
        description="Extrude knot; otherwise no bevel is applied to the curve",
        default=True
    )
        
    extrude_width: FloatProperty(
        name="Width",
        description="Extrusion (bevel) width",
        default=0.3,
        min=0.0,
        max=10.0
    )

    curve: BoolProperty(
        name="Create Curve",
        description="Create curve (otherwise, create a plain mesh)",
        default=True
    )
        
    smoothing: IntProperty(
        name="Smoothing steps",
        description="Number of smoothing steps to apply after knot generated",
        default=5,
        min=0,
        max=30
    )
        
    subdiv: IntProperty(
        name="Subdivision",
        description="Number of subdivisions to apply",
        default=2,
        min=0,
        max=30
    )

    z_depth: FloatProperty(
        name="Depth",
        description="Z shift at crossovers",
        default=1.5,
        min=0,
        max=10
    )
        
    z_bias: FloatProperty(
        name="Bias",
        description="Z bias at crossovers (-1=over rises, 0=split, 1=under lowers)",
        default=0,
        min=-1,
        max=1
    )
    
    # 新增收紧选项
    tighten: BoolProperty(
        name="Auto Tighten",
        description="Apply modifiers to tighten the knot automatically",
        default=False,
        update=_on_prop_update
    )
    
    tighten_strength: FloatProperty(
        name="Tighten Strength",
        description="How much to tighten the knot (higher = tighter)",
        default=0.5,
        min=0.0,
        max=2.0,
        update=_on_prop_update
    )
    
    shrinkwrap_offset: FloatProperty(
        name="Shrinkwrap Offset",
        description="Offset for shrinkwrap effect",
        default=0.1,
        min=-1.0,
        max=1.0,
        update=_on_prop_update
    )
    
    # 物理模拟选项
    use_physics: BoolProperty(
        name="Setup Physics",
        description="Setup collision and soft body physics for realistic tightening",
        default=False,
        update=_on_prop_update
    )
    
    physics_goal: FloatProperty(
        name="Physics Goal",
        description="Soft body goal strength (lower = more flexible)",
        default=0.5,
        min=0.0,
        max=1.0,
        update=_on_prop_update
    )
    
    physics_stiffness: FloatProperty(
        name="Edge Stiffness",
        description="How stiff the knot edges are",
        default=0.5,
        min=0.0,
        max=1.0,
        update=_on_prop_update
    )

    physics_use_cloth: BoolProperty(
        name="Use Cloth Solver",
        description="Use cloth simulation with self-collision instead of soft body",
        default=False,
        update=_on_prop_update
    )
        
    
def add_knot(self, context, knot_string, z_scale, bias, scale, name="Knot"):
    
    ix = 0
    verts = []
    edges = []
    
    knot_obj = Knot(knot_string)
    
    gauss_sequences, crossing_ids, crossing_verts_by_id = knot_obj.compute_gauss_code()

    if len(knot_obj.leads)<1:
        raise KnotException("Warning: no valid knot found")         
    
    for lead in knot_obj.leads:
        prev = None
        for x,y,dx,dy,z,name in lead:
            if knot_obj.is_crossing(x,y):
                z_val = z_scale * (bias+1)/2 if z != -1 else -z_scale * (bias+1)/2
                verts.append(Vector((x, -y, z_val)))
            else:
                verts.append(Vector((x, -y, 0)))
                
            if prev is not None:
                edges.append((prev, ix))
            prev = ix
            ix += 1
                                    
    mesh = bpy.data.meshes.new(name=name)
    mesh.from_pydata(verts, edges, [])    
    mesh.validate(verbose=True)
    obj = object_data_add(context, mesh, operator=self)

    # Store Gauss code as ID properties for downstream export (e.g., MuJoCo)
    if gauss_sequences:
        mesh["gauss_code"] = ";".join(",".join(f"{v:+d}" for v in seq) for seq in gauss_sequences)
    if crossing_ids:
        mesh["gauss_crossings"] = ";".join(f"{cid}:{pos[0]},{pos[1]}" for pos, cid in crossing_ids.items())
        mesh["gauss_num_crossings"] = int(len(crossing_ids))
    if gauss_sequences:
        mesh["gauss_num_components"] = int(len(gauss_sequences))
    if crossing_verts_by_id:
        mesh["gauss_crossing_verts"] = ";".join(
            f"{cid}:" + ",".join(str(ix) for ix in sorted(set(vs)))
            for cid, vs in sorted(crossing_verts_by_id.items(), key=lambda kv: kv[0])
        )
    # Also attach to the created object for convenience
    if obj:
        if gauss_sequences:
            obj["gauss_code"] = mesh["gauss_code"]
        if crossing_ids:
            obj["gauss_crossings"] = mesh["gauss_crossings"]
            obj["gauss_num_crossings"] = int(mesh.get("gauss_num_crossings", 0))
        if gauss_sequences:
            obj["gauss_num_components"] = int(mesh.get("gauss_num_components", 0))
        if crossing_verts_by_id:
            obj["gauss_crossing_verts"] = mesh.get("gauss_crossing_verts", "")
        _append_gauss_label(obj.name, gauss_sequences, crossing_ids)


def _append_gauss_label(obj_name, gauss_sequences, crossing_ids):
    """Persist Gauss code as JSONL inside a Blender Text block for offline labeling."""
    if not gauss_sequences and not crossing_ids:
        return
    payload = {
        "object": obj_name,
        "gauss_code": gauss_sequences,
        "num_crossings": int(len(crossing_ids)),
        "num_components": int(len(gauss_sequences)),
        "crossings": [{"id": cid, "pos": [int(pos[0]), int(pos[1])]} for pos, cid in crossing_ids.items()],
    }
    try:
        txt = bpy.data.texts.get("knot_gauss_labels.jsonl") or bpy.data.texts.new("knot_gauss_labels.jsonl")
        txt.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[Knot] Failed to record Gauss label: {e}")
    
    
# Apply tighten/physics/smoothing/subdiv settings to the active object based on current panel values
# Safe to call from property update callbacks

def _apply_settings_to_active(context):
    scene = context.scene
    if not hasattr(scene, 'knot_tool'):
        return
    settings = scene.knot_tool
    obj = context.object or context.view_layer.objects.active
    if obj is None:
        return

    # Ensure we work on mesh when features require it
    if (settings.tighten or settings.use_physics) and obj.type == 'CURVE':
        try:
            bpy.ops.object.convert(target='MESH')
            obj = context.object or obj
        except Exception:
            pass

    # Tighten modifiers
    if obj.type == 'MESH':
        mesh = obj.data
        mesh_has_faces = bool(getattr(mesh, "polygons", None)) and len(mesh.polygons) > 0

        # Simple Deform (TAPER on Z)
        sdef = next((m for m in obj.modifiers if m.type == 'SIMPLE_DEFORM'), None)
        if settings.tighten:
            if sdef is None:
                sdef = obj.modifiers.new(name='SimpleDeform', type='SIMPLE_DEFORM')
            sdef.deform_method = 'TAPER'
            sdef.deform_axis = 'Z'
            sdef.factor = -settings.tighten_strength * 0.3
        elif sdef is not None:
            obj.modifiers.remove(sdef)

        # Smooth modifier for rounding
        sm = next((m for m in obj.modifiers if m.type == 'SMOOTH'), None)
        if sm is None and (settings.smoothing > 0 or settings.tighten):
            sm = obj.modifiers.new(name='Smooth', type='SMOOTH')
        if sm is not None:
            sm.iterations = max(0, int(settings.smoothing))
            sm.factor = min(1.0, 0.5 + settings.tighten_strength * 0.4) if settings.tighten else 0.5
        sm_name = sm.name if sm is not None else None

        # Subdivision
        sub = next((m for m in obj.modifiers if m.type == 'SUBSURF'), None)
        if settings.subdiv > 0:
            if sub is None:
                sub = obj.modifiers.new(name='Subdivision', type='SUBSURF')
            sub.levels = int(settings.subdiv)
            sub.render_levels = int(settings.subdiv)
        elif sub is not None:
            obj.modifiers.remove(sub)
            sub = None
        sub_name = sub.name if sub is not None else None

        # Physics
        if settings.use_physics:
            use_cloth_sim = settings.physics_use_cloth and mesh_has_faces

            # Ensure Collision modifier for volume separation
            col = next((m for m in obj.modifiers if m.type == 'COLLISION'), None)
            if col is None:
                try:
                    bpy.ops.object.modifier_add(type='COLLISION')
                except Exception:
                    pass
            if hasattr(obj, 'collision') and obj.collision:
                obj.collision.thickness_outer = max(0.0001, getattr(settings, 'extrude_width', 0.3) * 0.5)
                # Lower friction/damping so strands can slide and knot migrates
                obj.collision.damping = 0.2
                obj.collision.cloth_friction = 1.0
            cloth_mod = next((m for m in obj.modifiers if m.type == 'CLOTH'), None)
            soft_mod = next((m for m in obj.modifiers if m.type == 'SOFT_BODY'), None)

            if use_cloth_sim:
                if soft_mod is not None:
                    obj.modifiers.remove(soft_mod)
                    soft_mod = None
                if cloth_mod is None:
                    try:
                        bpy.ops.object.modifier_add(type='CLOTH')
                    except Exception:
                        cloth_mod = None
                    else:
                        cloth_mod = next((m for m in obj.modifiers if m.type == 'CLOTH'), None)
                if cloth_mod is not None:
                    cloth_settings = cloth_mod.settings
                    cloth_settings.quality = max(5, int(8 + settings.physics_stiffness * 12))
                    cloth_settings.mass = max(0.002, getattr(settings, 'extrude_width', 0.3) * 0.02)
                    cloth_settings.air_damping = 0.02
                    stiffness = max(5.0, 20.0 * max(settings.physics_stiffness, 0.1))
                    cloth_settings.tension_stiffness = stiffness
                    cloth_settings.compression_stiffness = stiffness
                    cloth_settings.shear_stiffness = stiffness * 0.5
                    cloth_settings.bending_stiffness = 0.5
                    cloth_settings.use_internal_springs = True
                    cloth_settings.internal_friction = 5.0
                    if hasattr(cloth_settings, "pin_stiffness"):
                        cloth_settings.pin_stiffness = max(0.5, settings.physics_stiffness)
                    vg_pin = obj.vertex_groups.get('Cloth_Pin')
                    if vg_pin:
                        if hasattr(cloth_settings, "use_pin_cloth"):
                            cloth_settings.use_pin_cloth = True
                        elif hasattr(cloth_settings, "use_pin"):
                            cloth_settings.use_pin = True
                        if hasattr(cloth_settings, "vertex_group_mass"):
                            cloth_settings.vertex_group_mass = vg_pin.name
                    else:
                        if hasattr(cloth_settings, "use_pin_cloth"):
                            cloth_settings.use_pin_cloth = False
                        elif hasattr(cloth_settings, "use_pin"):
                            cloth_settings.use_pin = False
                        if hasattr(cloth_settings, "vertex_group_mass"):
                            cloth_settings.vertex_group_mass = ""

                    cloth_col = cloth_mod.collision_settings
                    cloth_col.use_self_collision = True
                    cloth_col.self_friction = 5.0
                    cloth_col.self_distance_min = max(0.0005, getattr(settings, 'extrude_width', 0.3) * 0.25)
                    cloth_col.distance_min = max(0.0005, getattr(settings, 'extrude_width', 0.3) * 0.25)
                    cloth_col.collision_quality = max(3, int(4 + settings.physics_stiffness * 6))
            else:
                if cloth_mod is not None:
                    obj.modifiers.remove(cloth_mod)
                # Ensure Soft Body exists and is configured (stretchable, not rigid)
                if 'SOFT_BODY' not in {m.type for m in obj.modifiers}:
                    try:
                        bpy.ops.object.modifier_add(type='SOFT_BODY')
                    except Exception:
                        pass
                if obj.soft_body:
                    sb = obj.soft_body
                    # 保持绳子形态：提升 goal 与刚度
                    stiffness = max(0.65, settings.physics_stiffness)
                    sb.goal_default = max(0.75, settings.physics_goal)
                    sb.goal_min = max(0.6, settings.physics_goal * 0.8)
                    sb.goal_max = 1.0
                    sb.goal_spring = 0.95
                    if hasattr(sb, "goal_friction"):
                        sb.goal_friction = 0.1
                    sb.use_edges = True
                    sb.pull = stiffness
                    sb.push = stiffness * 0.85
                    sb.bend = max(0.75, stiffness)
                    sb.use_self_collision = True
                    if hasattr(sb, "ball_size"):
                        sb.ball_size = max(0.001, getattr(settings, 'extrude_width', 0.3) * 0.4)
                    if hasattr(sb, "ball_stiff"):
                        sb.ball_stiff = 0.9
                    if hasattr(sb, "ball_damp"):
                        sb.ball_damp = 0.5
                    sb.use_goal = True
                    vg_goal = obj.vertex_groups.get('SB_Goal')
                    if vg_goal:
                        sb.vertex_group_goal = vg_goal.name
                    if hasattr(sb, "gravity"):
                        sb.gravity = 0.0

            # Keep viewport density low during simulation
            if sub_name:
                sub_mod = obj.modifiers.get(sub_name)
                if sub_mod:
                    sub_mod.show_viewport = False
                    sub_mod.show_render = True
            if sm_name:
                sm_mod = obj.modifiers.get(sm_name)
                if sm_mod:
                    sm_mod.show_viewport = True
        else:
            # Remove soft body and collision if present
            for m in list(obj.modifiers):
                if m.type in {'SOFT_BODY','COLLISION','CLOTH'}:
                    obj.modifiers.remove(m)
            if sub_name:
                sub_mod = obj.modifiers.get(sub_name)
                if sub_mod:
                    sub_mod.show_viewport = True
                    sub_mod.show_render = True

        # Ensure Smooth/Subsurf ordered after physics modifiers
        ordered_names = [name for name in (sm_name, sub_name) if name and obj.modifiers.get(name)]
        for name in ordered_names:
            idx = obj.modifiers.find(name)
            if idx != -1:
                obj.modifiers.move(idx, len(obj.modifiers) - 1)

    # Tag depsgraph update
    if obj.data:
        obj.data.update()


class KnotOperator(Operator, AddObjectHelper):
    bl_idname = "wm.make_knot"
    bl_label = "Make Knot"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        
        if not hasattr(scene, 'knot_tool'):
            self.report({'ERROR'}, "Knot settings not registered. Try re-enabling the addon.")
            return {"CANCELLED"}
            
        knottool = scene.knot_tool
        
        if not knottool.knot_text:
            self.report({'ERROR'}, "Please select a Text Block containing knot definition!")
            return {"CANCELLED"}
        
        if knottool.knot_text not in bpy.data.texts:
            self.report({'ERROR'}, f"Text block '{knottool.knot_text}' not found!")
            return {"CANCELLED"}
            
        knot = bpy.data.texts[knottool.knot_text].as_string()
                
        try:
            add_knot(self, context, knot, knottool.z_depth, knottool.z_bias, knottool.scale, knottool.knot_text)
        except KnotException as ke:
            print(str(ke))
            self.report({'ERROR'}, str(ke))
            return {"CANCELLED"}
            
        active = context.object
        if active is None:
            self.report({'ERROR'}, "Failed to create mesh object.")
            return {"CANCELLED"}

        saved_location = context.scene.cursor.location.copy()
        
        bpy.ops.object.select_all(action='DESELECT')
        active.select_set(True)
        context.view_layer.objects.active = active

        # Convert to curve if requested
        if knottool.curve:
            bpy.ops.object.convert(target="CURVE")
            active = context.object
            if active and active.type == 'CURVE':
                curves = active.data
                curves.fill_mode = 'FULL'
                curves.bevel_depth = knottool.extrude_width         
                curves.bevel_resolution = 6
                curves.use_uv_as_generated = True

        # Ensure mesh for tighten/physics if required
        active = context.object
        if (knottool.tighten or knottool.use_physics) and active and active.type == 'CURVE':
            bpy.ops.object.convert(target='MESH')
            active = context.object

        # Apply settings to active object (adds/updates modifiers and physics)
        _apply_settings_to_active(context)
        
        # 物理模拟设置
        if knottool.use_physics and active.type in {'MESH', 'CURVE'}:
            # 添加Collision修改器
            bpy.ops.object.modifier_add(type="COLLISION")
            if hasattr(active, 'collision'):
                active.collision.thickness_outer = knottool.extrude_width * 0.5
                active.collision.damping = 0.8
            
            # 添加Soft Body物理
            bpy.ops.object.modifier_add(type="SOFT_BODY")
            if active.soft_body:
                sb = active.soft_body
                # Goal设置
                sb.goal_default = knottool.physics_goal
                sb.goal_min = knottool.physics_goal * 0.5
                sb.goal_spring = 0.5
                
                # Edge设置
                sb.use_edges = True
                sb.pull = knottool.physics_stiffness
                sb.push = knottool.physics_stiffness * 0.5
                sb.bend = 0.5
                
                # Collision设置
                sb.use_self_collision = True
                sb.self_collision_friction = 0.5
                
                self.report({'INFO'}, "Physics setup complete! Press SPACEBAR to simulate.")

        # Recenter origin
        bpy.ops.object.mode_set(mode='EDIT')
        if active.type == 'CURVE':
             bpy.ops.curve.select_all(action='SELECT')
        else:
             bpy.ops.mesh.select_all(action='SELECT')
                 
        bpy.ops.view3d.snap_cursor_to_selected()
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
        
        context.scene.cursor.location = saved_location
        
        bpy.ops.object.location_clear()
        bpy.ops.object.rotation_clear()
        bpy.ops.object.scale_clear()
        bpy.ops.transform.resize(value=(knottool.scale, knottool.scale, knottool.scale))

        return {'FINISHED'}


class OBJECT_PT_KnotPanel(Panel):
    bl_idname = "OBJECT_PT_knot"
    bl_label = "Knot Generator"
    bl_space_type = "VIEW_3D"    
    bl_region_type = "UI"    
    bl_category = "Knot"
    bl_context = "objectmode"

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        if not hasattr(scene, 'knot_tool'):
            layout.label(text="Error: Knot Tool not loaded", icon='ERROR')
            return
            
        myknot = scene.knot_tool

        layout.prop(myknot, "scale")
        
        # ASCII定义
        box = layout.box()
        box.label(text="ASCII Knot Definition", icon='TEXT')
        box.prop_search(myknot, 'knot_text', bpy.data, 'texts', text="Text Block")
        
        # Z轴设置
        box = layout.box()
        box.label(text="Over/Under Z shift", icon='DRIVER_DISTANCE')
        box.prop(myknot, "z_depth")
        box.prop(myknot, "z_bias")
        
        # 收紧设置 - 在开启物理时禁用几何收紧
        box = layout.box()
        box.label(text="Tighten Settings", icon='FORCE_MAGNETIC')
        if myknot.use_physics:
            row = box.row()
            row.label(text="Disabled while Physics is ON (use hooks + playback)", icon='INFO')
            box.enabled = False
        box.prop(myknot, "tighten")
        col = box.column(align=True)
        col.prop(myknot, "tighten_strength")
        col.prop(myknot, "shrinkwrap_offset")
        col.enabled = myknot.tighten and not myknot.use_physics
        
        # 物理模拟设置 - 新增！
        box = layout.box()
        box.label(text="Physics Simulation", icon='PHYSICS')
        box.prop(myknot, "use_physics")
        col = box.column(align=True)
        col.prop(myknot, "physics_goal")
        col.prop(myknot, "physics_stiffness")
        col.enabled = myknot.use_physics
        if myknot.use_physics:
            box.prop(myknot, "physics_use_cloth")
        row = box.row(align=True)
        row.operator("knot.add_hooks", icon='HOOK')
        row.operator("knot.add_pull_forces", icon='FORCE_FORCE')
        row.operator("knot.auto_tighten", icon='ANIM')
        if myknot.use_physics:
            col.label(text="Press SPACE to simulate", icon='INFO')

        # 输出选项
        box = layout.box()
        box.label(text="Output Options", icon='OUTLINER_OB_CURVE')
        box.prop(myknot, "curve")
        box.prop(myknot, "extrude")
        
        col = box.column(align=True)
        col.prop(myknot, "extrude_width")
        col.prop(myknot, "smoothing")
        col.prop(myknot, "subdiv")
        col.enabled = myknot.extrude
            
        layout.operator("wm.make_knot", icon='CURVE_DATA', text="Generate Knot")

        # ---- Gauss debug UI for the active object (Level 1: Blender-side labeling) ----
        obj = context.object or context.view_layer.objects.active
        if obj is not None and ("gauss_code" in obj or (getattr(obj, "data", None) and "gauss_code" in obj.data)):
            box = layout.box()
            box.label(text="Gauss Code (Debug)", icon='SORTALPHA')

            # Prefer object properties; fall back to mesh data properties
            gauss_code_key = '["gauss_code"]' if "gauss_code" in obj else None
            gauss_code_mesh_key = '["gauss_code"]' if getattr(obj, "data", None) and "gauss_code" in obj.data else None

            num_crossings = int(obj.get("gauss_num_crossings", getattr(obj.data, "get", lambda *_: 0)("gauss_num_crossings", 0)))
            num_components = int(obj.get("gauss_num_components", getattr(obj.data, "get", lambda *_: 0)("gauss_num_components", 0)))
            row = box.row(align=True)
            row.label(text=f"Crossings: {num_crossings} | Components: {num_components}")

            row = box.row(align=True)
            row.operator("knot.label_crossings", icon='EMPTY_AXIS', text="Label Crossings")

            # Copy-friendly field (ID property string)
            col = box.column(align=True)
            if gauss_code_key:
                col.prop(obj, gauss_code_key, text="gauss_code")
            elif gauss_code_mesh_key:
                col.prop(obj.data, gauss_code_mesh_key, text="gauss_code")
        
        
class KNOT_OT_add_hooks(Operator):
    bl_idname = "knot.add_hooks"
    bl_label = "Add Tighten Hooks"
    bl_description = "Create two empties hooked to rope ends, and a Soft Body goal map so pulling ends tightens the knot"
    bl_options = {'REGISTER', 'UNDO'}

    nearest_count: IntProperty(name="End Vert Count", default=30, min=3, max=500)

    def execute(self, context):
        import heapq
        obj = context.object or context.view_layer.objects.active
        if obj is None:
            self.report({'ERROR'}, "Select the knot object first")
            return {'CANCELLED'}

        # Convert to mesh if needed
        if obj.type == 'CURVE':
            bpy.ops.object.convert(target='MESH')
            obj = context.object

        me = obj.data
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()

        ends = [v for v in bm.verts if len(v.link_edges) == 1]
        if len(ends) < 2:
            vs = list(bm.verts)
            ends = [min(vs, key=lambda v: v.co.x), max(vs, key=lambda v: v.co.x)]

        # world coordinates for empties
        def world_co(v):
            return obj.matrix_world @ v.co

        labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        end_pairs = []
        for idx, vert in enumerate(ends[:len(labels)]):
            end_pairs.append((vert, labels[idx]))
        world_coords = [obj.matrix_world @ v.co for v in bm.verts]

        # Build Hook groups and empties at both ends
        seed_indices = set()
        pinned_indices = set()
        for v, tag in end_pairs:
            dists = sorted([(i, (world_coords[i] - world_co(v)).length) for i in range(len(bm.verts))], key=lambda x: x[1])
            picked = [i for i,_ in dists[:self.nearest_count]]
            seed_indices.update(picked)
            pinned_indices.update(picked)

            vg = obj.vertex_groups.new(name=f"Hook_{tag}")
            for i in picked:
                vg.add([i], 1.0, 'REPLACE')

            empty = bpy.data.objects.new(f"Hook_{tag}", None)
            empty.empty_display_size = 0.2
            empty.empty_display_type = 'PLAIN_AXES'
            empty.location = world_co(v)
            context.collection.objects.link(empty)

            mod = obj.modifiers.new(name=f"Hook_{tag}", type='HOOK')
            mod.object = empty
            mod.vertex_group = vg.name

        # Dijkstra (multi-source) from all ends along edges (world-length weights)
        def dijkstra(seeds):
            dist = [1e20]*len(bm.verts)
            h = []
            for i in seeds:
                dist[i] = 0.0
                heapq.heappush(h, (0.0, i))
            while h:
                d,u = heapq.heappop(h)
                if d!=dist[u]:
                    continue
                vu = world_coords[u]
                for e in bm.verts[u].link_edges:
                    v = e.other_vert(bm.verts[u]).index
                    w = (world_coords[v]-vu).length
                    nd = d + w
                    if nd < dist[v]:
                        dist[v] = nd
                        heapq.heappush(h, (nd, v))
            return dist

        seed_sources = set(seed_indices) if seed_indices else {v.index for v,_ in end_pairs}
        if not seed_sources and bm.verts:
            seed_sources = {bm.verts[0].index}
        mind = dijkstra(seed_sources)
        maxd = max(x for x in mind if x < 1e10) or 1.0
        falloff = maxd * 0.35  # 近端权重高，向中部快速衰减

        # Create Soft Body goal group: ends权重≈1，中段≈0.15，可滑动
        vg_goal = obj.vertex_groups.get('SB_Goal') or obj.vertex_groups.new(name='SB_Goal')
        for i, d in enumerate(mind):
            if d >= 1e10:
                w = 0.15
            else:
                w = max(0.15, min(1.0, 1.0 - d/falloff))
            vg_goal.add([i], w, 'REPLACE')

        vg_pin = obj.vertex_groups.get('Cloth_Pin')
        if vg_pin is None:
            vg_pin = obj.vertex_groups.new(name='Cloth_Pin')
        else:
            vg_pin.remove(range(len(bm.verts)))
        for i in pinned_indices:
            vg_pin.add([i], 1.0, 'REPLACE')

        bm.free()

        # Ensure physics present
        if 'SOFT_BODY' not in {m.type for m in obj.modifiers}:
            try:
                bpy.ops.object.modifier_add(type='SOFT_BODY')
            except Exception:
                pass
        if obj.soft_body:
            sb = obj.soft_body
            sb.use_edges = True
            sb.use_self_collision = True
            # 目标权重沿长度分布：端部接近1，中段低
            sb.use_goal = True
            sb.vertex_group_goal = vg_goal.name
            sb.goal_max = 1.0
            sb.goal_min = 0.1
            sb.goal_spring = 0.8
            # 拉伸/压缩刚度（仍可拉伸，非刚体）
            sb.pull = max(0.6, context.scene.knot_tool.physics_stiffness)
            sb.push = 0.5 * max(0.6, context.scene.knot_tool.physics_stiffness)
            sb.bend = 0.5
            # 自碰撞球半径基于绳半径
            try:
                sb.ball_size = max(0.0001, getattr(context.scene.knot_tool, 'extrude_width', 0.3) * 0.5)
                sb.ball_stiff = 0.7
                sb.ball_damp = 0.8
            except Exception:
                pass

        cloth_mod = next((m for m in obj.modifiers if m.type == 'CLOTH'), None)
        if cloth_mod:
            cloth_settings = cloth_mod.settings
            if hasattr(cloth_settings, "use_pin_cloth"):
                cloth_settings.use_pin_cloth = True
            elif hasattr(cloth_settings, "use_pin"):
                cloth_settings.use_pin = True
            if hasattr(cloth_settings, "vertex_group_mass"):
                cloth_settings.vertex_group_mass = vg_pin.name
            if hasattr(cloth_settings, "pin_stiffness"):
                cloth_settings.pin_stiffness = max(0.5, context.scene.knot_tool.physics_stiffness)
            cloth_col = cloth_mod.collision_settings
            cloth_col.use_self_collision = True
            cloth_col.self_friction = 5.0

        # Ensure collision modifier exists and has thickness
        if 'COLLISION' not in {m.type for m in obj.modifiers}:
            try:
                bpy.ops.object.modifier_add(type='COLLISION')
            except Exception:
                pass
        if hasattr(obj, 'collision') and obj.collision:
            obj.collision.thickness_outer = max(0.0001, getattr(context.scene.knot_tool, 'extrude_width', 0.3) * 0.5)
            obj.collision.damping = 0.5
            obj.collision.cloth_friction = 5.0

        self.report({'INFO'}, "Hooks created and Soft Body goal map set. Pull hooks and press Space to simulate.")
        return {'FINISHED'}


class KNOT_OT_add_pull_forces(Operator):
    bl_idname = "knot.add_pull_forces"
    bl_label = "Add Pull Forces"
    bl_description = "Disable Hook deformation and add Force fields directly on Hook_A/B for physical pulling"
    bl_options = {'REGISTER', 'UNDO'}

    strength: FloatProperty(name="Force Strength", default=200.0, min=0.0, max=100000.0)
    falloff_power: FloatProperty(name="Falloff", default=2.0, min=0.0, max=10.0)

    def _ensure_force(self, context, obj):
        # Robustly add a force field to obj using operator (creates field data if missing)
        prev_active = context.view_layer.objects.active
        prev_sel = [o for o in context.selected_objects]
        try:
            for o in prev_sel:
                o.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
            bpy.ops.object.forcefield_toggle()
        finally:
            obj.select_set(False)
            for o in prev_sel:
                o.select_set(True)
            context.view_layer.objects.active = prev_active
        # Now set settings if field exists
        if getattr(obj, 'field', None) is not None:
            obj.field.type = 'FORCE'
            obj.field.strength = self.strength
            obj.field.falloff_power = self.falloff_power
            obj.field.use_max_distance = False

    def execute(self, context):
        # Find hooks
        hooks = [o for o in context.scene.objects if o.name.startswith("Hook_")]
        if len(hooks) < 2:
            self.report({'ERROR'}, "Create hooks first")
            return {'CANCELLED'}

        # Disable Hook modifiers on the active rope so forces (not direct deformation) drive the motion
        rope = context.object or context.view_layer.objects.active
        if rope and rope.type == 'MESH':
            for m in rope.modifiers:
                if m.type == 'HOOK' and m.name.startswith('Hook_'):
                    m.strength = 1.0

        # Add force fields directly to the hooks (empties)
        for hook in hooks:
            self._ensure_force(context, hook)

        self.report({'INFO'}, f"Added FORCE fields to {len(hooks)} hook empties. Move/animate them to apply forces.")
        return {'FINISHED'}


class KNOT_OT_auto_tighten(Operator):
    bl_idname = "knot.auto_tighten"
    bl_label = "Auto Tighten"
    bl_options = {'REGISTER', 'UNDO'}

    frames: IntProperty(name="Frames", default=120, min=5, max=5000)
    distance: FloatProperty(name="Distance", default=2.0, min=0.0, max=100.0)

    def execute(self, context):
        scene = context.scene

        # Collect hook empties (created by add_hooks)
        hooks = sorted([o for o in scene.objects if o.name.startswith("Hook_")], key=lambda o: o.name)
        if len(hooks) < 2:
            result = bpy.ops.knot.add_hooks('EXEC_DEFAULT')
            if 'FINISHED' not in result:
                self.report({'ERROR'}, "Failed to create hooks")
                return {'CANCELLED'}
            hooks = sorted([o for o in scene.objects if o.name.startswith("Hook_")], key=lambda o: o.name)

        if len(hooks) < 2:
            self.report({'ERROR'}, "No hook empties found")
            return {'CANCELLED'}

        # Try to find the rope object (prefer active object, otherwise search modifiers referencing hooks)
        rope = context.object
        if rope in hooks or rope is None:
            rope = next((obj for obj in scene.objects
                         if any(mod.type == 'HOOK' and mod.object in hooks for mod in obj.modifiers)),
                        None)

        if rope is None:
            self.report({'ERROR'}, "Cannot locate knot object. Select the rope mesh and retry.")
            return {'CANCELLED'}

        if rope.type == 'CURVE':
            try:
                prev_active = context.view_layer.objects.active
                bpy.ops.object.select_all(action='DESELECT')
                rope.select_set(True)
                context.view_layer.objects.active = rope
                bpy.ops.object.convert(target='MESH')
            except Exception:
                self.report({'ERROR'}, "Failed to convert curve to mesh for auto tighten")
                return {'CANCELLED'}
            finally:
                new_active = context.view_layer.objects.active
                rope = new_active if new_active and new_active.type == 'MESH' else rope

        if rope.type != 'MESH':
            self.report({'ERROR'}, "Auto tighten only supports mesh knots")
            return {'CANCELLED'}

        # Ensure physics settings applied if enabled so simulation responds to animation
        _apply_settings_to_active(context)

        # Determine if we should use force-field mode (hook modifiers strength ~0)
        hook_mods = [m for m in rope.modifiers if m.type == 'HOOK' and m.object in hooks]
        force_mode = not any(m.strength > 0.001 for m in hook_mods)
        if force_mode:
            result = bpy.ops.knot.add_pull_forces('EXEC_DEFAULT')
            if 'FINISHED' not in result:
                self.report({'ERROR'}, "Failed to add pull forces")
                return {'CANCELLED'}

        # Compute knot center in world space
        if rope.bound_box:
            center_local = Vector((0.0, 0.0, 0.0))
            for corner in rope.bound_box:
                center_local += Vector(corner)
            center_local /= 8.0
            center = rope.matrix_world @ center_local
        else:
            center = rope.matrix_world.translation.copy()

        start_frame = scene.frame_current
        total_frames = max(1, int(self.frames))
        end_frame = start_frame + total_frames
        mid_frame = start_frame + max(1, total_frames // 2)
        pull_distance = max(0.0, float(self.distance))

        # prepare animation data
        initial_locations = {}
        for hook in hooks:
            initial_locations[hook.name] = hook.location.copy()

        # Keyframe start
        scene.frame_set(start_frame)
        for hook in hooks:
            hook.location = initial_locations[hook.name].copy()
            hook.keyframe_insert(data_path="location", frame=start_frame)

        # Keyframe midpoint (ease-in)
        mid_ratio = 0.6
        scene.frame_set(mid_frame)
        for hook in hooks:
            initial = initial_locations[hook.name]
            direction = (initial - center)
            if direction.length < 1e-5:
                direction = Vector((1.0, 0.0, 0.0))
            direction.normalize()
            hook.location = initial + direction * pull_distance * mid_ratio
            hook.keyframe_insert(data_path="location", frame=mid_frame)

        # Keyframe end (full distance)
        scene.frame_set(end_frame)
        for hook in hooks:
            initial = initial_locations[hook.name]
            direction = (initial - center)
            if direction.length < 1e-5:
                direction = Vector((1.0, 0.0, 0.0))
            direction.normalize()
            hook.location = initial + direction * pull_distance
            hook.keyframe_insert(data_path="location", frame=end_frame)

        # Ensure smooth interpolation
        for hook in hooks:
            if hook.animation_data and hook.animation_data.action:
                for fcurve in hook.animation_data.action.fcurves:
                    for key in fcurve.keyframe_points:
                        key.interpolation = 'BEZIER'
                        key.handle_left_type = 'AUTO'
                        key.handle_right_type = 'AUTO'

        # Adjust timeline to match animation
        scene.frame_start = start_frame
        scene.frame_end = max(scene.frame_end, end_frame)
        scene.frame_set(start_frame)

        mode_label = "force fields" if force_mode else "hook deformation"
        self.report({'INFO'}, f"Auto tighten animation created ({mode_label}) from frame {start_frame} to {end_frame}.")

        return {'FINISHED'}


class KNOT_OT_label_crossings(Operator):
    bl_idname = "knot.label_crossings"
    bl_label = "Label Crossings"
    bl_description = "Create/update Empty labels (C1, C2, ...) at each Gauss crossing position for visual debugging"
    bl_options = {'REGISTER', 'UNDO'}

    size: FloatProperty(name="Label Size", default=0.15, min=0.01, max=10.0)
    clear_existing: BoolProperty(name="Clear Existing", default=False)

    def execute(self, context):
        obj = context.object or context.view_layer.objects.active
        if obj is None or obj.type not in {'MESH', 'CURVE'}:
            self.report({'ERROR'}, "Select the knot object first (mesh preferred).")
            return {'CANCELLED'}

        # Prefer mapping stored on the object; fall back to mesh
        mapping = obj.get("gauss_crossing_verts", "") or (obj.data.get("gauss_crossing_verts", "") if getattr(obj, "data", None) else "")
        if not mapping:
            self.report({'ERROR'}, "No gauss_crossing_verts found. Regenerate the knot to compute crossing labels.")
            return {'CANCELLED'}

        if obj.type != 'MESH':
            self.report({'ERROR'}, "Crossing labels require a mesh object (convert to mesh first).")
            return {'CANCELLED'}

        # Optionally clear old labels for this object
        prefix = f"{obj.name}_C"
        if self.clear_existing:
            for o in list(context.scene.objects):
                if o.name.startswith(prefix) and o.get("knot_crossing_label_owner") == obj.name:
                    try:
                        bpy.data.objects.remove(o, do_unlink=True)
                    except Exception:
                        pass

        # Parse "id:ix,ix;id:ix,ix"
        id_to_verts = {}
        for part in mapping.split(";"):
            part = part.strip()
            if not part or ":" not in part:
                continue
            sid, sverts = part.split(":", 1)
            try:
                cid = int(sid)
            except Exception:
                continue
            verts = []
            for tok in sverts.split(","):
                tok = tok.strip()
                if not tok:
                    continue
                try:
                    verts.append(int(tok))
                except Exception:
                    pass
            if verts:
                id_to_verts[cid] = verts

        if not id_to_verts:
            self.report({'ERROR'}, "Failed to parse gauss_crossing_verts mapping.")
            return {'CANCELLED'}

        me = obj.data
        created = 0
        updated = 0

        for cid, vixs in sorted(id_to_verts.items(), key=lambda kv: kv[0]):
            # Representative position: average of listed vertex coords (local space)
            coords = []
            for ix in vixs:
                if 0 <= ix < len(me.vertices):
                    coords.append(me.vertices[ix].co.copy())
            if not coords:
                continue
            local = Vector((0.0, 0.0, 0.0))
            for c in coords:
                local += c
            local /= float(len(coords))
            world = obj.matrix_world @ local

            label_name = f"{prefix}{cid}"
            label = bpy.data.objects.get(label_name)
            if label is None:
                label = bpy.data.objects.new(label_name, None)
                label.empty_display_type = 'PLAIN_AXES'
                label.empty_display_size = float(self.size)
                context.collection.objects.link(label)
                created += 1
            else:
                updated += 1

            label.location = world
            label["knot_crossing_label"] = int(cid)
            label["knot_crossing_label_owner"] = obj.name
            # Parent for organization (keep world transform)
            try:
                label.parent = obj
                label.matrix_parent_inverse = obj.matrix_world.inverted()
            except Exception:
                pass

        self.report({'INFO'}, f"Crossing labels: created {created}, updated {updated}.")
        return {'FINISHED'}

classes = [
    KnotSettings,
    OBJECT_PT_KnotPanel,
    KnotOperator,
    KNOT_OT_add_hooks,
    KNOT_OT_add_pull_forces,
    KNOT_OT_auto_tighten,
    KNOT_OT_label_crossings,
]
    

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
        
    bpy.types.Scene.knot_tool = PointerProperty(type=KnotSettings)
    print("Knot Generator: Registered successfully")


def unregister():
    if hasattr(bpy.types.Scene, 'knot_tool'):
        del bpy.types.Scene.knot_tool
    
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    print("Knot Generator: Unregistered")


if __name__ == "__main__":
    register()