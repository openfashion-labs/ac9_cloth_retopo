"""Bake Preview Plane — a 1x1m plane in the Guide Maps panel that shows one
of the baked map images (Residual / Sag / AO / Curvature / Drape) as an
Emission-shaded material, so a map can be inspected without an Image Editor
open.

Every mode is a single baked image, including the combined Drape map. That is
deliberate: Solid > Texture shading draws a material's ACTIVE Image Texture
node straight to the screen and never evaluates the node tree, so anything
composited with nodes here would be invisible in the one shading mode these
maps are actually looked at in. The Drape composite is therefore done at bake
time (see core._combine_drape) and arrives as an image like the rest.

The plane's local space is built to match the Flat SK (UV-flat) space
exactly: verts at (0,0,0)/(1,0,0)/(1,1,0)/(0,1,0), object at the origin, so
its corners span 0..1 in X and Y — the same convention the retopo's own flat
layout and every baked map's UV already use.
"""

import bpy

from . import core

PLANE_OBJ_NAME = "AC9_BakePreview"
PLANE_MESH_NAME = "AC9_BakePreview"
# The plane sits a little BELOW the flat retopo (z = 0): above it, surface-
# snapping retopo tools would land new faces on the plane instead of the
# retopo. Below it, the retopo stays the thing you touch — at the cost that
# anything else lying flat at z = 0 (the Guide in its Flat SK pose, the flat
# retopo, a hand-made bake board) covers the plane when the viewport looks
# down from above, and has to be hidden by hand. 5 mm is invisible from the
# top view and clear of any Solidify thickness on the Guide.
PLANE_DROP = 0.005
PREVIEW_MAT_NAME = "AC9_BakePreview"
_TEX_NODE_NAME = "AC9_PreviewTex"

def image_name_for(map_key: str, guide_obj):
    """The bake image name for a map key and Guide, or None for an
    unrecognized key / no Guide. Maps are named per Guide so garments worked
    on side by side keep their own sets — see core.image_name."""
    return core.image_name(map_key, guide_obj)


def describe_preview(map_key: str, guide_obj) -> str:
    """Human-readable name of what the plane is showing right now."""
    img = core.find_image(map_key, guide_obj)
    if img is not None:
        return img.name
    if guide_obj is None:
        return "no Guide picked"
    return f"no {core.MAP_LABELS.get(map_key, map_key)} map baked for '{guide_obj.name}'"


def _build_mesh():
    verts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
    faces = [(0, 1, 2, 3)]
    me = bpy.data.meshes.new(PLANE_MESH_NAME)
    me.from_pydata(verts, [], faces)
    me.update()
    uv = me.uv_layers.new(name="UVMap")
    uv_by_vert = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    for loop in me.loops:
        uv.data[loop.index].uv = uv_by_vert[loop.vertex_index]
    return me


def _apply_image(tex_node, map_key: str, guide_obj):
    tex_node.image = core.find_image(map_key, guide_obj)


def sync_preview_image(map_key: str, guide_obj):
    """Point the preview material's Image Texture node at the image for
    `map_key` on `guide_obj`, or clear it when that image does not exist.

    Clearing matters: with one image per Guide, a plane left pointing at the
    previous garment's map would quietly show the wrong garment.

    No-op when the preview material has not been created yet (the plane was
    never baked/made) — used both by the preview_map property's update
    callback and, on success, by the bake operators.
    """
    mat = bpy.data.materials.get(PREVIEW_MAT_NAME)
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return
    tex = mat.node_tree.nodes.get(_TEX_NODE_NAME)
    if tex is None:
        return
    _apply_image(tex, map_key, guide_obj)
    # Solid > Texture draws the ACTIVE image texture node, ignoring links.
    mat.node_tree.nodes.active = tex


def _ensure_material(map_key: str, guide_obj):
    mat = bpy.data.materials.get(PREVIEW_MAT_NAME)
    if mat is None:
        mat = bpy.data.materials.new(PREVIEW_MAT_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    tex = nt.nodes.get(_TEX_NODE_NAME)
    if tex is None:
        nt.nodes.clear()
        out = nt.nodes.new('ShaderNodeOutputMaterial')
        emit = nt.nodes.new('ShaderNodeEmission')
        emit.inputs['Strength'].default_value = 1.0
        tex = nt.nodes.new('ShaderNodeTexImage')
        tex.name = _TEX_NODE_NAME
        tex.interpolation = 'Linear'
        nt.links.new(tex.outputs['Color'], emit.inputs['Color'])
        nt.links.new(emit.outputs[0], out.inputs[0])
    sync_preview_image(map_key, guide_obj)
    return mat


def get_or_create_plane(context, map_key: str, guide_obj):
    """Create the AC9_BakePreview plane, or reuse it if already present.
    Assigns/refreshes its material every call so a later map switch takes
    effect even when the plane already existed."""
    obj = bpy.data.objects.get(PLANE_OBJ_NAME)
    if obj is None or obj.type != 'MESH':
        me = _build_mesh()
        obj = bpy.data.objects.new(PLANE_OBJ_NAME, me)
    obj.location = (0.0, 0.0, -PLANE_DROP)
    obj.hide_render = True

    scene_coll = context.scene.collection
    if scene_coll.objects.get(obj.name) is None:
        scene_coll.objects.link(obj)

    mat = _ensure_material(map_key, guide_obj)
    me = obj.data
    if len(me.materials) == 0:
        me.materials.append(mat)
    else:
        me.materials[0] = mat
    return obj


def set_material_shading(context) -> str:
    """Set every VIEW_3D area of the current screen up to actually show the
    plane, and return a short note of what changed (empty when nothing did).

    Solid's colour source becomes Texture (Viewport Shading popover > Color >
    Texture), which draws the active Image Texture node of each object's
    material. Areas already in Material Preview / Rendered shading are left
    alone — an Emission material shows there already.

    The Retopology overlay goes on rather than X-Ray: X-Ray makes the whole
    retopo semi-transparent, which is the wrong thing to be looking at while
    cutting. Note that Retopology only lifts the mesh being EDITED clear of
    what is behind it — it is not what makes this plane visible. Anything
    lying flat at z = 0 still covers the plane and has to be hidden by hand.
    """
    screen = getattr(context, "screen", None)
    if screen is None:
        return ""
    shading_changed = False
    overlay_changed = False
    xray_cleared = False

    for area in screen.areas:
        if area.type != 'VIEW_3D':
            continue
        for space in area.spaces:
            if space.type != 'VIEW_3D':
                continue
            sh = space.shading
            if sh.type == 'SOLID' and sh.color_type != 'TEXTURE':
                sh.color_type = 'TEXTURE'
                shading_changed = True
            # X-Ray is never ours to turn on; if an earlier version of this
            # addon left it on, take it back off.
            if sh.show_xray:
                sh.show_xray = False
                xray_cleared = True
            if not space.overlay.show_retopology:
                space.overlay.show_retopology = True
                overlay_changed = True

    notes = []
    if shading_changed:
        notes.append("Solid colour = Texture")
    if overlay_changed:
        notes.append("Retopology overlay on")
    if xray_cleared:
        notes.append("X-Ray off")
    return ", ".join(notes)
