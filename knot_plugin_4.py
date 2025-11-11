bl_info = {
    "name": "Knot generator",
    "description": """Creates 3D meshes of knots from simple ASCII art descriptions.""",
    "author": "John H. Williamson",
    "version": (0, 0, 6),
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
                        PointerProperty,
                        )
from bpy.types import (Panel,
                        Operator,
                        PropertyGroup,
                        )

from bpy_extras.object_utils import AddObjectHelper, object_data_add
from mathutils import Vector


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

### Blender Python interface
class KnotSettings(PropertyGroup):
    # Blender 4.0+ 属性定义方式
    
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
        default=5,
        min=0,
        max=30
    )

    z_depth: FloatProperty(
        name="Depth",
        description="Z shift at crossovers",
        default=1,
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
    
    tighten: BoolProperty(
        name="Tighten Knot",
        description="Apply modifiers to tighten the knot automatically",
        default=False
    )
    
    tighten_strength: FloatProperty(
        name="Tighten Strength",
        description="How much to tighten the knot (higher = tighter)",
        default=0.5,
        min=0.0,
        max=2.0
    )
    
    shrinkwrap_offset: FloatProperty(
        name="Shrinkwrap Offset",
        description="Offset for shrinkwrap effect",
        default=0.1,
        min=-1.0,
        max=1.0
    )
        
    
def add_knot(self, context, knot_string, z_scale, bias, scale, name="Knot"):
    
    ix = 0
    verts = []
    edges = []
    
    knot_obj = Knot(knot_string)
    
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
    object_data_add(context, mesh, operator=self)
    
    
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

        # Convert mesh to curve
        if knottool.curve:
            bpy.ops.object.convert(target="CURVE")
            
            active = context.object
            if active and active.type == 'CURVE':
                curves = active.data
                
                curves.fill_mode = 'FULL'
                curves.bevel_depth = knottool.extrude_width         
                curves.bevel_resolution = 6
                curves.use_uv_as_generated = True
        
        # Modifiers
        if active.type in {'MESH', 'CURVE'}:
            
            if knottool.smoothing > 0:
                bpy.ops.object.modifier_add(type="SMOOTH")
                if 'Smooth' in active.modifiers:
                    active.modifiers['Smooth'].iterations = knottool.smoothing          
                
            if knottool.subdiv > 0 and active.type == 'MESH':
                bpy.ops.object.modifier_add(type="SUBSURF")
                if 'Subdivision' in active.modifiers:
                    active.modifiers['Subdivision'].levels = knottool.subdiv
                    active.modifiers['Subdivision'].render_levels = knottool.subdiv

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
            layout.operator("wm.console_toggle", text="Check Console for Errors")
            return
            
        myknot = scene.knot_tool

        layout.prop(myknot, "scale")
        
        box = layout.box()
        box.label(text="ASCII Knot Definition", icon='TEXT')
        box.prop_search(myknot, 'knot_text', bpy.data, 'texts', text="Text Block")
        
        box = layout.box()
        box.label(text="Over/Under Z shift", icon='DRIVER_DISTANCE')
        box.prop(myknot, "z_depth")
        box.prop(myknot, "z_bias")

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
        
        
classes = [
    KnotSettings,
    OBJECT_PT_KnotPanel,
    KnotOperator
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