"""Тест возможностей: пушистая голова зайца в Blender (Cycles, процессор).

Запуск:
  blender -b -P scripts/blender/test_fur.py -- <выходной.png> [ширина высота семплы]
"""
import sys
import time

import bpy

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
out = argv[0] if argv else "/tmp/fur_test.png"
W = int(argv[1]) if len(argv) > 1 else 800
H = int(argv[2]) if len(argv) > 2 else 800
SAMPLES = int(argv[3]) if len(argv) > 3 else 32

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene


def mat(name, color, rough=0.6, sss=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    if sss:
        b.inputs["Subsurface Weight"].default_value = sss
    return m


def add(kind, name, loc, scale, material, subsurf=True, **kw):
    getattr(bpy.ops.mesh, kind)(location=loc, **kw)
    o = bpy.context.active_object
    o.name = name
    o.scale = scale
    bpy.ops.object.shade_smooth()
    if subsurf:
        o.modifiers.new("sub", "SUBSURF").levels = 2
    o.data.materials.append(material)
    return o


fur = mat("fur", (0.62, 0.55, 0.47), 0.85)
pink = mat("pink", (0.9, 0.55, 0.58), 0.7)
white = mat("white", (1, 1, 1), 0.15)
iris = mat("iris", (0.08, 0.35, 0.6), 0.25)
black = mat("black", (0.01, 0.01, 0.01), 0.1)

head = add("primitive_uv_sphere_add", "head", (0, 0, 1.0), (1.0, 0.92, 0.88), fur, radius=1, segments=64, ring_count=32)
ear_l = add("primitive_uv_sphere_add", "ear_l", (-0.45, 0, 2.15), (0.22, 0.12, 0.85), fur, radius=1, segments=32, ring_count=16)
ear_r = add("primitive_uv_sphere_add", "ear_r", (0.45, 0, 2.15), (0.22, 0.12, 0.85), fur, radius=1, segments=32, ring_count=16)
ear_l.rotation_euler = (0, 0.15, 0)
ear_r.rotation_euler = (0, -0.15, 0)
for sx in (-1, 1):
    add("primitive_uv_sphere_add", "inner", (0.45 * sx, -0.09, 2.1), (0.12, 0.05, 0.6), pink, radius=1)
    add("primitive_uv_sphere_add", "eye", (0.38 * sx, -0.78, 1.15), (0.28, 0.12, 0.34), white, radius=1, segments=32, ring_count=16)
    add("primitive_uv_sphere_add", "iris", (0.38 * sx, -0.88, 1.12), (0.19, 0.06, 0.24), iris, radius=1, segments=32, ring_count=16)
    add("primitive_uv_sphere_add", "pupil", (0.38 * sx, -0.93, 1.12), (0.11, 0.04, 0.14), black, radius=1, segments=24, ring_count=12)
    add("primitive_uv_sphere_add", "cheek", (0.6 * sx, -0.62, 0.7), (0.22, 0.1, 0.18), pink, radius=1)
add("primitive_uv_sphere_add", "nose", (0, -0.9, 0.78), (0.12, 0.08, 0.08), pink, radius=1)
ground = add("primitive_plane_add", "ground", (0, 0, -0.1), (12, 12, 1), mat("ground", (0.25, 0.4, 0.18), 0.9), subsurf=False, size=2)

# мех: волосы на голове и ушах
for o in (head, ear_l, ear_r):
    bpy.context.view_layer.objects.active = o
    o.select_set(True)
    bpy.ops.object.particle_system_add()
    ps = o.particle_systems[0].settings
    ps.type = "HAIR"
    ps.count = 3500 if o is head else 900
    ps.hair_length = 0.09
    ps.child_type = "INTERPOLATED"
    ps.child_percent = 10
    ps.rendered_child_count = 25
    ps.child_radius = 0.06
    ps.clump_factor = 0.3
    ps.roughness_1 = 0.05
    ps.material = 1
    o.select_set(False)
for o in (head, ear_l, ear_r):
    o.data.materials[0] = fur

# свет и камера
bpy.ops.object.light_add(type="SUN", location=(3, -4, 6))
sun = bpy.context.active_object
sun.data.energy = 3.5
sun.rotation_euler = (0.9, 0.2, 0.6)
bpy.ops.object.light_add(type="AREA", location=(-3, -4, 3))
bpy.context.active_object.data.energy = 150
bpy.context.active_object.data.size = 4
bpy.ops.object.camera_add(location=(0, -6.2, 1.4), rotation=(1.5, 0, 0))
cam = bpy.context.active_object
cam.data.lens = 85
cam.data.dof.use_dof = True
cam.data.dof.focus_distance = 5.2
cam.data.dof.aperture_fstop = 2.8
sc.camera = cam
world = bpy.data.worlds.new("w")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.55, 0.75, 0.55, 1)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.8
sc.world = world

sc.render.engine = "CYCLES"
sc.cycles.device = "CPU"
sc.cycles.samples = SAMPLES
sc.cycles.use_denoising = True
sc.render.resolution_x, sc.render.resolution_y = W, H
sc.render.filepath = out
sc.render.image_settings.file_format = "PNG"
t0 = time.time()
bpy.ops.render.render(write_still=True)
print(f"RENDER_SECONDS {time.time() - t0:.1f}  size {W}x{H} samples {SAMPLES}")
