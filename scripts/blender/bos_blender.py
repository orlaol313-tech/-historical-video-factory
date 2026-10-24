"""BonnieAndOllieStudio: 3D-рендер серии в Blender (Cycles, процессор, бесплатно).

Запуск (обычно через scripts/build_episode_3d.py):
  blender -b -P scripts/blender/bos_blender.py -- scene.json outdir W H samples start end [step]

Движение берётся из scripts/bos_anim.py (то же, что в 2D-версии).
1 единица Blender = 100 пикселей кадра 1920x1080. Герои стоят в плоскости y=0.
"""
import json
import math
import os
import sys
import time

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
SCENE_JSON, OUT = argv[0], argv[1]
W, H, SAMPLES = int(argv[2]), int(argv[3]), int(argv[4])
F0, F1 = int(argv[5]), int(argv[6])
STEP = int(argv[7]) if len(argv) > 7 else 1

D = json.load(open(SCENE_JSON, encoding="utf-8"))
sys.path.insert(0, D["scripts_dir"])
from bos_anim import Timeline  # noqa: E402

EP = D["episode"]
FPS = EP["format"]["fps"]
GROUND_Z = (540 - EP["stage"]["ground_y"]) / 100.0     # высота земли в единицах
CHAR_SCALE = 1.3                                       # героев крупнее, чем в 2D
YAW = 50.0                                             # поворот героя 3/4 к камере
lin = lambda c: c ** 2.2                               # sRGB -> линейный (приблизительно)


def rgb(hexstr):
    h = hexstr.lstrip("#")
    return tuple(lin(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4))


bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene


# ------------------------------------------------------------------ материалы
def principled(name, color, rough=0.6, **inputs):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    for k, v in inputs.items():
        b.inputs[k.replace("_", " ")].default_value = v
    return m


def fur_hair_material(name, color):
    """Волосок темнее у корня и светлее на кончике — даёт объём меху."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    hair = nt.nodes.new("ShaderNodeHairInfo")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.72, 0.72, 0.72, 1)
    ramp.color_ramp.elements[1].color = (1.25, 1.25, 1.25, 1)
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.inputs[0].default_value = 1.0
    mix.inputs[6].default_value = (*color, 1)
    nt.links.new(hair.outputs["Intercept"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], mix.inputs[7])
    nt.links.new(mix.outputs[2], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.8
    return m


def emission_mat(name, color, strength=1.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*color, 1)
    em.inputs["Strength"].default_value = strength
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(em.outputs[0], out.inputs[0])
    return m


# -------------------------------------------------------------------- геометрия
def empty(name, parent=None, loc=(0, 0, 0)):
    o = bpy.data.objects.new(name, None)
    sc.collection.objects.link(o)
    o.parent = parent
    o.location = loc
    return o


def sphere(name, parent, loc, scale, mat, seg=32, rings=16, subsurf=False):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=seg, ring_count=rings)
    o = bpy.context.active_object
    o.name = name
    o.parent = parent
    o.location = loc
    o.scale = scale
    bpy.ops.object.shade_smooth()
    o.data.materials.append(mat)
    return o


def curve(name, parent, loc, pts, bevel, mat):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = bevel
    cu.resolution_u = 10
    sp = cu.splines.new("BEZIER")
    sp.bezier_points.add(len(pts) - 1)
    for bp, p in zip(sp.bezier_points, pts):
        bp.co = p
        bp.handle_left_type = bp.handle_right_type = "AUTO"
    o = bpy.data.objects.new(name, cu)
    sc.collection.objects.link(o)
    o.parent = parent
    o.location = loc
    cu.materials.append(mat)
    return o


def add_fur(o, mat_hair, length, n_long, n_short=0, children=18):
    """Мех: волосы на поверхности. Лицо покрывается короткой шерстью."""
    vg_l, vg_s = o.vertex_groups.new(name="long"), o.vertex_groups.new(name="short")
    for v in o.data.vertices:
        face = v.co.y < -0.45 and -0.7 < v.co.z < 0.45
        (vg_s if face else vg_l).add([v.index], 1.0, "REPLACE")
    o.data.materials.append(mat_hair)
    bpy.context.view_layer.objects.active = o
    for vg, count, ln in (("long", n_long, length), ("short", n_short, length * 0.3)):
        if not count:
            continue
        bpy.ops.object.particle_system_add()
        psys = o.particle_systems[-1]
        psys.vertex_group_density = vg
        ps = psys.settings
        ps.type = "HAIR"
        ps.count = count
        ps.hair_length = ln
        ps.child_type = "INTERPOLATED"
        ps.child_percent = 10
        ps.rendered_child_count = children
        ps.child_radius = 0.05
        ps.clump_factor = 0.12
        ps.roughness_1 = 0.03
        ps.radius_scale = 0.0045
        ps.root_radius = 1.0
        ps.tip_radius = 0.15
        ps.use_close_tip = False
        ps.render_step = 2
        ps.material = len(o.data.materials)


# ------------------------------------------------------------------- персонажи
class Puppet:
    def __init__(self, cdef):
        d = cdef["design"]
        self.id = cdef["id"]
        k = self.k = d["height_px"] / 420.0 * CHAR_SCALE
        sx = self.sx = float(d.get("chubby", 1.0))
        fur_c, accent_c = tuple(min(1, c * 1.25) for c in rgb(d["body_color"])), rgb(d["accent_color"])
        belly_c = rgb(d.get("belly_color", "#F3E6D3"))
        cloth_c = rgb(d.get("outfit_color", "#3F66A8"))
        eye_c, nose_c = rgb(d.get("eye_color", "#5A3A1A")), rgb(d.get("nose_color", "#3B2418"))
        outfit, ears = d.get("outfit", "none"), d.get("ears", "round")
        M = principled
        m_skin = principled(self.id + "_skin", fur_c, 0.9)
        m_belly = principled(self.id + "_belly", belly_c, 0.9)
        m_hair = fur_hair_material(self.id + "_hair", fur_c)
        m_hair_b = fur_hair_material(self.id + "_hairb", belly_c)
        m_in = M(self.id + "_in", accent_c, 0.8)
        m_cloth = M(self.id + "_cloth", cloth_c, 0.85, Sheen_Weight=0.5)
        m_white = M(self.id + "_w", (1, 1, 1), 0.12)
        m_eye = M(self.id + "_e", eye_c, 0.2)
        m_dark = M(self.id + "_d", (0.01, 0.01, 0.01), 0.1)
        m_nose = M(self.id + "_n", nose_c, 0.35)
        m_mouth = M(self.id + "_m", (0.35, 0.05, 0.09), 0.4)
        m_tongue = M(self.id + "_t", (0.9, 0.35, 0.4), 0.5)
        m_cheek = M(self.id + "_c", (1.0, 0.55, 0.55), 0.8)
        m_yel = M(self.id + "_y", (0.9, 0.7, 0.1), 0.35, Metallic=0.6)
        m_hl = emission_mat(self.id + "_hl", (1, 1, 1), 6.0)

        self.root = empty(self.id + "_root")
        self.yaw = empty(self.id + "_yaw", self.root)
        hip_h = 1.13 * k
        # ноги
        self.legs = []
        for side in (-1, 1):
            piv = empty(f"{self.id}_leg{side}", self.yaw, (side * 0.3 * k * sx, 0, hip_h))
            leg = sphere("leg", piv, (0, 0, -0.55 * k), (0.27 * k, 0.27 * k, 0.58 * k), m_skin)
            add_fur(leg, m_hair, 0.05 * k, 450, 0, 12)
            sphere("foot", piv, (0, -0.12 * k, -1.07 * k), (0.33 * k, 0.45 * k, 0.17 * k), m_belly)
            self.legs.append(piv)
        # тело
        self.bpiv = empty(self.id + "_body", self.yaw, (0, 0, hip_h + 0.08 * k))
        body = sphere("body", self.bpiv, (0, 0, 0.95 * k), (0.78 * k * sx, 0.62 * k, 0.98 * k), m_skin, 48, 24)
        add_fur(body, m_hair, 0.09 * k, 3200, 0, 16)
        if outfit != "overalls":
            belly = sphere("belly", self.bpiv, (0, -0.2 * k, 0.78 * k), (0.52 * k * sx, 0.5 * k, 0.62 * k), m_belly, 32, 16)
            add_fur(belly, m_hair_b, 0.06 * k, 1500, 0, 12)
        if outfit == "overalls":
            sphere("shorts", self.bpiv, (0, 0, 0.3 * k), (0.68 * k * sx, 0.54 * k, 0.4 * k), m_cloth)
            sphere("bib", self.bpiv, (0, -0.5 * k, 0.95 * k), (0.5 * k * sx, 0.16 * k, 0.5 * k), m_cloth)
            for side in (-1, 1):
                strap = sphere("strap", self.bpiv, (side * 0.38 * k * sx, -0.44 * k, 1.45 * k), (0.07 * k, 0.07 * k, 0.5 * k), m_cloth)
                strap.rotation_euler = (0, side * 0.35, 0)
                sphere("button", self.bpiv, (side * 0.3 * k * sx, -0.64 * k, 1.28 * k), (0.07 * k, 0.04 * k, 0.07 * k), m_yel)
        elif outfit == "hood":
            sphere("collar", self.bpiv, (0, 0, 1.62 * k), (0.78 * k * sx, 0.6 * k, 0.26 * k), m_cloth)
            for side in (-1, 1):
                cord = sphere("cord", self.bpiv, (side * 0.2 * k * sx, -0.58 * k, 1.35 * k), (0.025 * k, 0.025 * k, 0.28 * k), m_white)
        # голова
        neck = empty(self.id + "_neck", self.bpiv, (0, 0, 1.7 * k))
        self.hpiv = empty(self.id + "_head", neck)
        R = 1.05 * k
        hc = (0, 0, 0.8 * k)
        hs = (R * 1.0, R * 0.92, R * 0.9)
        head = sphere("head", self.hpiv, hc, hs, m_skin, 64, 32)
        add_fur(head, m_hair, 0.08 * R, 3000, 1500, 20)
        if outfit == "hood":
            hood = sphere("hood", self.hpiv, (0, 0.2 * R, hc[2] + 0.02 * R), (R * 1.13, R * 1.0, R * 1.08), m_cloth, 48, 24)
        if ears == "long":
            for side in (-1, 1):
                e = sphere("ear", self.hpiv, (side * 0.48 * k, 0.0, hc[2] + 1.15 * k), (0.26 * k, 0.15 * k, 0.95 * k), m_skin)
                e.rotation_euler = (0, side * 0.15, 0)
                add_fur(e, m_hair, 0.05 * k, 650, 0, 12)
                sphere("ear_in", self.hpiv, (side * 0.49 * k, -0.1 * k, hc[2] + 1.1 * k), (0.14 * k, 0.05 * k, 0.7 * k), m_in).rotation_euler = (0, side * 0.15, 0)
            for i, a in enumerate((-0.3, 0.0, 0.3)):   # хохолок
                t = sphere("tuft", self.hpiv, (a * 0.25 * k, -0.1 * k, hc[2] + 0.92 * R), (0.04 * k, 0.04 * k, 0.2 * k), m_hair_b if False else m_belly)
                t.rotation_euler = (0, a, 0)
        else:
            for side in (-1, 1):
                e = sphere("ear", self.hpiv, (side * 0.7 * R, 0.05 * R, hc[2] + 0.72 * R), (0.36 * R, 0.24 * R, 0.36 * R), m_skin)
                add_fur(e, m_hair, 0.04 * R, 500, 0, 10)
                sphere("ear_in", self.hpiv, (side * 0.7 * R, -0.12 * R, hc[2] + 0.72 * R), (0.2 * R, 0.1 * R, 0.2 * R), m_in)
        # лицо
        eyes_open, eyes_closed = [], []
        self.fx = {"normal": [], "laugh": [], "oh": []}
        for side in (-1, 1):
            ex, ez = side * 0.4 * R, hc[2] + 0.12 * R
            w = sphere("eye", self.hpiv, (ex, -0.86 * R, ez), (0.34 * R, 0.14 * R, 0.42 * R), m_white)
            ir = sphere("iris", self.hpiv, (ex, -0.96 * R, ez - 0.03 * R), (0.25 * R, 0.07 * R, 0.31 * R), m_eye)
            pu = sphere("pupil", self.hpiv, (ex, -1.02 * R, ez - 0.03 * R), (0.14 * R, 0.05 * R, 0.18 * R), m_dark, 24, 12)
            hl = sphere("hl", self.hpiv, (ex - 0.06 * R, -1.08 * R, ez + 0.07 * R), (0.07 * R, 0.02 * R, 0.07 * R), m_hl, 12, 6)
            eyes_open += [w, ir, pu, hl]
            arc = curve("arc", self.hpiv, (ex, -0.97 * R, ez), [(-0.2 * R, 0, -0.06 * R), (0, 0, 0.1 * R), (0.2 * R, 0, -0.06 * R)], 0.025 * R, m_dark)
            eyes_closed.append(arc)
            sphere("cheek", self.hpiv, (side * 0.6 * R, -0.7 * R, hc[2] - 0.3 * R), (0.2 * R, 0.08 * R, 0.16 * R), m_cheek)
        sphere("nose", self.hpiv, (0, -0.98 * R, hc[2] - 0.1 * R), (0.13 * R, 0.09 * R, 0.09 * R), m_nose)
        smile = curve("smile", self.hpiv, (0, -0.95 * R, hc[2] - 0.3 * R), [(-0.2 * R, 0, 0.05 * R), (0, 0, -0.08 * R), (0.2 * R, 0, 0.05 * R)], 0.02 * R, m_dark)
        m_open = sphere("mouth", self.hpiv, (0, -0.9 * R, hc[2] - 0.34 * R), (0.2 * R, 0.07 * R, 0.14 * R), m_mouth)
        tongue = sphere("tongue", self.hpiv, (0, -0.93 * R, hc[2] - 0.4 * R), (0.1 * R, 0.04 * R, 0.06 * R), m_tongue, 16, 8)
        m_oh = sphere("mouth_oh", self.hpiv, (0, -0.93 * R, hc[2] - 0.36 * R), (0.1 * R, 0.05 * R, 0.12 * R), m_mouth, 16, 8)
        self.fx["normal"] = eyes_open + [smile]
        self.fx["laugh"] = eyes_closed + [m_open, tongue]
        self.fx["oh"] = eyes_open + [m_oh]
        self.eyes_open = eyes_open
        # руки
        self.arms = []
        for side in (-1, 1):
            piv = empty(f"{self.id}_arm{side}", self.bpiv, (side * 0.64 * k * sx, 0, 1.4 * k))
            arm = sphere("arm", piv, (0, 0, -0.5 * k), (0.2 * k, 0.2 * k, 0.6 * k), m_skin)
            add_fur(arm, m_hair, 0.05 * k, 450, 0, 12)
            sphere("hand", piv, (0, 0, -1.05 * k), (0.2 * k, 0.2 * k, 0.2 * k), m_belly)
            self.arms.append(piv)
        # мягкая тень под ногами (в плоскости кадра, как на мультяшном фоне)
        bpy.ops.mesh.primitive_circle_add(vertices=48, fill_type="NGON", radius=1)
        sh = bpy.context.active_object
        sh.name = self.id + "_shadow"
        sh.rotation_euler = (math.radians(90), 0, 0)
        sh.data.materials.append(shadow_material())
        self.shadow = sh
        self.cur = None

    def apply(self, tl, t):
        st = tl.actor_state(self.id, t)
        P = tl.actor_pose(st, t)
        k = self.k
        # плавный разворот к направлению движения
        face = sum(tl.actor_state(self.id, max(0, t - 0.4 * i / 4))["face"] for i in range(5)) / 5.0
        self.root.location = ((st["x"] - 960) / 100.0, 0, GROUND_Z - P["dy"] / 100.0 - P["lift"] / 100.0)
        self.root.rotation_euler = (0, -P["char_rot"] * face * math.pi / 180.0, 0)
        self.yaw.rotation_euler = (0, 0, math.radians(YAW) * face)
        r = math.radians
        self.bpiv.rotation_euler = (0, -r(P["body_rot"]), 0)
        self.hpiv.rotation_euler = (0, -r(P["head_rot"]), -r(YAW * 0.8) * face)
        for piv, key in zip(self.legs, ("leg_l", "leg_r")):
            piv.rotation_euler = (-r(P[key]), 0, 0)
        for piv, key in zip(self.arms, ("arm_l", "arm_r")):
            a = P[key]
            m = min(1.0, max(0.0, (abs(a) - 25) / 30.0))
            piv.rotation_euler = (-r(a * (1 - m)), -r(a * m), 0)
        visible = set(self.fx[P["face"]])
        for objs in self.fx.values():
            for o in objs:
                o.hide_render = o not in visible
        # тень
        s = max(0.4, 1 + P["dy"] / 500.0)
        self.shadow.location = ((st["x"] - 960) / 100.0, 0.6, GROUND_Z + 0.02)
        self.shadow.scale = (1.0 * k * s, 1.0, 0.16 * k * s)


def shadow_material():
    m = bpy.data.materials.new("shadow")
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sub = nt.nodes.new("ShaderNodeVectorMath")
    sub.operation = "SUBTRACT"
    sub.inputs[1].default_value = (0, 0, 0)
    ln = nt.nodes.new("ShaderNodeVectorMath")
    ln.operation = "LENGTH"
    ramp = nt.nodes.new("ShaderNodeMapRange")
    ramp.inputs["From Min"].default_value = 0.0
    ramp.inputs["From Max"].default_value = 1.0
    ramp.inputs["To Min"].default_value = 0.42
    ramp.inputs["To Max"].default_value = 0.0
    ramp.clamp = True
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    bk = nt.nodes.new("ShaderNodeEmission")
    bk.inputs["Color"].default_value = (0.02, 0.05, 0.02, 1)
    mx = nt.nodes.new("ShaderNodeMixShader")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(tc.outputs["Object"], sub.inputs[0])
    nt.links.new(sub.outputs[0], ln.inputs[0])
    nt.links.new(ln.outputs["Value"], ramp.inputs["Value"])
    nt.links.new(ramp.outputs["Result"], mx.inputs[0])
    nt.links.new(tr.outputs[0], mx.inputs[1])
    nt.links.new(bk.outputs[0], mx.inputs[2])
    nt.links.new(mx.outputs[0], out.inputs[0])
    return m


# ---------------------------------------------------------------------- сцена
CAM_LENS, SENSOR = 70.0, 36.0
CAM_D = 19.2 * CAM_LENS / SENSOR


def build_backdrop():
    names = sorted({s["bg"] for s in EP["scenes"]})
    order = [s["bg"] for s in EP["scenes"]]
    width = (CAM_D + 6.0) * SENSOR / CAM_LENS
    bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 6.0, 0), rotation=(math.radians(90), 0, 0))
    o = bpy.context.active_object
    o.scale = (width * 1.02, width * 9 / 16 * 1.02, 1)
    m = bpy.data.materials.new("backdrop")
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    texs = {}
    for n in names:
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image = bpy.data.images.load(os.path.join(D["blur_dir"], f"{n}.png"))
        t.image.colorspace_settings.name = "sRGB"
        texs[n] = t
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 1.0
    nt.links.new(em.outputs[0], out.inputs[0])
    prev, mixes = texs[order[0]], []
    seen = {order[0]}
    for n in order[1:]:
        if n in seen:
            continue
        seen.add(n)
        mx = nt.nodes.new("ShaderNodeMix")
        mx.data_type = "RGBA"
        nt.links.new(prev.outputs["Color"] if prev.bl_idname == "ShaderNodeTexImage" else prev.outputs[2], mx.inputs[6])
        nt.links.new(texs[n].outputs["Color"], mx.inputs[7])
        mixes.append((mx, n))
        prev = mx
    nt.links.new(prev.outputs[2] if mixes else prev.outputs["Color"], em.inputs["Color"])
    o.data.materials.append(m)
    return mixes


MIXES = build_backdrop()


def update_backdrop(t):
    for mx, name in MIXES:
        start = next(s["t"][0] for s in EP["scenes"] if s["bg"] == name)
        x = (t - start + 0.25) / 0.5
        mx.inputs[0].default_value = min(1.0, max(0.0, x))


# свет
bpy.ops.object.light_add(type="SUN", location=(-6, -10, 10))
sun = bpy.context.active_object
sun.rotation_euler = (math.radians(55), 0, math.radians(-30))
sun.data.energy = 3.2
sun.data.angle = math.radians(6)
bpy.ops.object.light_add(type="AREA", location=(8, -10, 4))
fill = bpy.context.active_object
fill.data.energy = 900
fill.data.size = 8
fill.rotation_euler = (math.radians(80), 0, math.radians(35))
world = bpy.data.worlds.new("w")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.75, 0.85, 1.0, 1)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.9
sc.world = world

bpy.ops.object.camera_add(location=(0, -CAM_D, 0), rotation=(math.radians(90), 0, 0))
cam = bpy.context.active_object
cam.data.lens, cam.data.sensor_width = CAM_LENS, SENSOR
cam.data.dof.use_dof = True
cam.data.dof.focus_distance = CAM_D
cam.data.dof.aperture_fstop = 2.0
sc.camera = cam

# настройки рендера
sc.render.engine = "CYCLES"
sc.cycles.device = "CPU"
sc.cycles.samples = SAMPLES
sc.cycles.use_adaptive_sampling = True
sc.cycles.adaptive_threshold = 0.03
sc.cycles.use_denoising = True
sc.cycles.max_bounces = 4
sc.cycles.diffuse_bounces = 2
sc.cycles.glossy_bounces = 2
sc.cycles.transmission_bounces = 2
sc.cycles.transparent_max_bounces = 4
sc.render.use_persistent_data = True
sc.view_settings.view_transform = "Standard"
sc.render.resolution_x, sc.render.resolution_y = W, H
sc.render.resolution_percentage = 100
sc.render.image_settings.file_format = "PNG"
sc.render.image_settings.color_mode = "RGB"
sc.render.film_transparent = False

chars = {cid: Puppet(cdef) for cid, cdef in D["characters"].items()}
ck = {cid: p.k for cid, p in chars.items()}
tl = Timeline(EP, ck)

# шарик
balloon = None
if "balloon" in tl.props:
    col = rgb(EP["props"]["balloon"]["color"])
    bm = principled("balloon", col, 0.12, Coat_Weight=0.8)
    balloon = sphere("balloon", None, (0, -0.5, 0), (0.52, 0.52, 0.66), bm, 48, 24)
    knot = sphere("knot", balloon, (0, 0, -1.0), (0.12, 0.12, 0.1), bm, 12, 6)
    cu = bpy.data.curves.new("string", "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = 0.012
    sp = cu.splines.new("POLY")
    sp.points.add(13)
    string = bpy.data.objects.new("string", cu)
    sc.collection.objects.link(string)
    cu.materials.append(principled("string", (0.05, 0.05, 0.05), 0.5))
    string_sp = sp


def px(x, y):
    return ((x - 960) / 100.0, (540 - y) / 100.0)


def apply_balloon(t):
    if t < tl.props["balloon"][0]["t"]:
        balloon.hide_render = True
        string.hide_render = True
        return
    balloon.hide_render = string.hide_render = False
    pos, end, scale = tl.prop_state("balloon", t)
    bx, bz = px(*pos)
    balloon.location = (bx, -0.5, bz)
    balloon.scale = (0.52 * scale, 0.52 * scale, 0.66 * scale)
    sx_, sz_ = px(pos[0], pos[1] + 66 * scale)
    ex, ez = px(*end)
    mx_, mz_ = (sx_ + ex) / 2 + 0.1 * math.sin(t * 3), (sz_ + ez) / 2
    for i in range(14):
        u = i / 13.0
        x = (1 - u) ** 2 * sx_ + 2 * u * (1 - u) * mx_ + u * u * ex
        z = (1 - u) ** 2 * sz_ + 2 * u * (1 - u) * mz_ + u * u * ez
        string_sp.points[i].co = (x, -0.5, z, 1)


# ------------------------------------------------------------------ рендер кадров
os.makedirs(OUT, exist_ok=True)
t_all = time.time()
done = 0
FRAMES = [int(x) for x in os.environ["BOS_FRAMES"].split(",")] if os.environ.get("BOS_FRAMES") else range(F0, F1, STEP)
for i in FRAMES:
    path = os.path.join(OUT, f"frame_{i:05d}.png")
    if os.path.exists(path):
        continue
    t = i / FPS
    for p in chars.values():
        p.apply(tl, t)
    if balloon:
        apply_balloon(t)
    update_backdrop(t)
    sc.render.filepath = path
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    done += 1
    print(f"BOS_FRAME {i} {time.time() - t0:.1f}s", flush=True)
print(f"BOS_DONE frames={done} total={time.time() - t_all:.0f}s", flush=True)
