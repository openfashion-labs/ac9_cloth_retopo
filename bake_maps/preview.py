"""Bake Preview Plane — a 1x1m plane in the Guide Maps panel that shows one
of the baked map images (Residual / Sag / Drape) as an Emission-shaded
material, so the map can be inspected without an Image Editor open.

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
# retopo; below it, the retopo stays the thing you touch and the map shows
# through X-Ray. 5 mm is invisible from the top view and clear of any
# Solidify thickness on the Guide.
PLANE_DROP = 0.005
PREVIEW_MAT_NAME = "AC9_BakePreview"
_TEX_NODE_NAME = "AC9_PreviewTex"

_IMAGE_BY_KEY = {
    'RESIDUAL': core.RESIDUAL_IMAGE,
    'SAG': core.SAG_IMAGE,
    'DRAPE': core.DRAPE_IMAGE,
}


def image_name_for(map_key: str):
    """The fixed bake image name for a 'RESIDUAL'/'SAG'/'DRAPE' key, or
    None for an unrecognized key."""
    return _IMAGE_BY_KEY.get(map_key)


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


def _apply_image(tex_node, map_key: str):
    name = image_name_for(map_key)
    tex_node.image = bpy.data.images.get(name) if name else None


def sync_preview_image(map_key: str):
    """Point the preview material's Image Texture node at the image for
    `map_key`, or clear it when that image does not exist yet.

    No-op when the preview material has not been created yet (the plane was
    never baked/made) — used both by the preview_map property's update
    callback and, on success, by the three bake operators.
    """
    mat = bpy.data.materials.get(PREVIEW_MAT_NAME)
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return
    tex = mat.node_tree.nodes.get(_TEX_NODE_NAME)
    if tex is None:
        return
    _apply_image(tex, map_key)


def _ensure_material(map_key: str):
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
    _apply_image(tex, map_key)
    return mat


def get_or_create_plane(context, map_key: str):
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

    mat = _ensure_material(map_key)
    me = obj.data
    if len(me.materials) == 0:
        me.materials.append(mat)
    else:
        me.materials[0] = mat
    return obj


def set_material_shading(context) -> bool:
    """Make the plane's map visible in every VIEW_3D area of the current
    screen without leaving Solid shading: Solid's colour source becomes
    Texture (Viewport Shading popover > Color > Texture), which draws the
    active Image Texture node of each object's material, and X-Ray goes on
    so the retopo above the plane does not hide it. Areas already in
    Material / Rendered shading are left alone. Returns True if anything was
    changed (for the operator's report)."""
    screen = getattr(context, "screen", None)
    if screen is None:
        return False
    changed = False
    for area in screen.areas:
        if area.type != 'VIEW_3D':
            continue
        for space in area.spaces:
            if space.type != 'VIEW_3D':
                continue
            sh = space.shading
            if sh.type != 'SOLID':
                continue
            if sh.color_type != 'TEXTURE':
                sh.color_type = 'TEXTURE'
                changed = True
            # The plane is below the retopo, so the retopo has to be
            # see-through for the map to show at all.
            if not sh.show_xray:
                sh.show_xray = True
                changed = True
    return changed
