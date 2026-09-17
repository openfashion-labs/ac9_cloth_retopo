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
# down from above. The retopo is the one that cannot simply be hidden, since
# it is what you are cutting: the ghost material at the bottom of this module
# is the answer for that one. The rest have to be hidden by hand. 5 mm is
# invisible from the top view and clear of any Solidify thickness on the
# Guide.
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

    Neither X-Ray nor the Retopology overlay is turned on; both are taken
    back off. X-Ray makes the whole scene semi-transparent, which is the wrong
    thing to be looking at while cutting. The Retopology overlay replaces the
    edit-mesh face fill with the theme's `face_retopology` colour (measured
    alpha 0.502 on 5.0.1), which means it OVERRIDES the ghost material in Edit
    Mode — with it on, the retopo cannot be made any more transparent than the
    theme says, and the theme is a Preference shared by every file. Clearing
    it is not optional housekeeping: an earlier version of this add-on wrote
    it on, and `show_retopology` is stored per 3D View inside the .blend
    (measured: survives save and reload), so files that version touched still
    carry it and would defeat the ghost.

    Anything else lying flat at z = 0 still covers the plane and has to be
    hidden by hand.
    """
    screen = getattr(context, "screen", None)
    if screen is None:
        return ""
    shading_changed = False
    retopology_cleared = False
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
            # Neither of these is ours to turn on; if an earlier version of
            # this add-on left one on, take it back off.
            if sh.show_xray:
                sh.show_xray = False
                xray_cleared = True
            if space.overlay.show_retopology:
                space.overlay.show_retopology = False
                retopology_cleared = True

    notes = []
    if shading_changed:
        notes.append("Solid colour = Texture")
    if retopology_cleared:
        notes.append("Retopology overlay off")
    if xray_cleared:
        notes.append("X-Ray off")
    return ", ".join(notes)


# ---------------------------------------------------------------------------
# The retopo ghost material
#
# The plane sits BELOW the flat retopo, so the retopo hides it from the top
# view. Making the retopo itself semi-transparent is what lets the map and the
# mesh being cut be read at the same time.
#
# Which knob does that is not obvious, and the two shading modes disagree.
# Measured on 5.0.1 with a blue plane over a red one, sampling the pixel where
# they overlap:
#
#   Workbench (Solid)   diffuse_color alpha 1.0   -> (0.00, 0, 1.00) opaque
#                       diffuse_color alpha 0.367 -> (0.82, 0, 0.64) blended
#                       Principled Alpha 0.367    -> (0.00, 0, 1.00) no effect
#                       blend_method BLEND        -> identical to HASHED
#   EEVEE               diffuse_color alpha 0.367 -> opaque, no effect
#                       Principled Alpha 0.367    -> blended, and blend_method
#                                                    decides dithered vs alpha
#
# So Solid reads ONLY `diffuse_color[3]` and EEVEE reads ONLY the Principled
# Alpha. Dropping the node tree to dodge that does not work either: on 5.0
# `use_nodes = False` does not stick (it reads back True with a tree). Both
# knobs are therefore written together from the panel's one slider, and the
# material tells the same story in Solid and in Material Preview.
# ---------------------------------------------------------------------------

GHOST_MAT_NAME = "AC9_RetopoTransparent"
# What this material was called while the panel's slider was labelled
# "Ghost". The Seam overlays already own that word (Ghost Points / Ghost
# Lines), so the slider is "Alpha" and the material follows the button
# that makes it. Files written before the rename are still found and
# reused under the old name, alpha and all.
GHOST_MAT_NAME_LEGACY = "AC9_RetopoGhost"
GHOST_MAT_NAMES = (GHOST_MAT_NAME, GHOST_MAT_NAME_LEGACY)
GHOST_DEFAULT_ALPHA = 0.35


def find_ghost_material():
    """The file's ghost material, or None.

    Looked up by name and reused rather than re-created, so the alpha the user
    settled on comes back with the file — it is stored on the material, which
    keeps this out of Preferences (the theme's Retopology colour, the only
    other way to make a retopo see-through, is shared by every file).
    """
    for name in GHOST_MAT_NAMES:
        mat = bpy.data.materials.get(name)
        if mat is not None:
            return mat
    return None


def _ghost_bsdf(mat):
    """The material's Principled BSDF node, or None."""
    nt = getattr(mat, "node_tree", None)
    if nt is None:
        return None
    for node in nt.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            return node
    return None


def ghost_material_on(retopo):
    """The transparent working material actually assigned to *retopo*."""
    me = getattr(retopo, "data", None)
    if me is None or not hasattr(me, "materials"):
        return None
    return next((mat for mat in me.materials if _is_ghost(mat)), None)


def get_ghost_alpha(retopo=None) -> float:
    """How transparent the ghost currently is; the default when there is no
    ghost material in the file yet."""
    mat = ghost_material_on(retopo) if retopo is not None else None
    if mat is None:
        mat = find_ghost_material()
    return GHOST_DEFAULT_ALPHA if mat is None else mat.diffuse_color[3]


def set_ghost_alpha(value: float, retopo=None) -> None:
    """Write one slider to both knobs — see the note above."""
    mat = ghost_material_on(retopo) if retopo is not None else None
    if mat is None:
        mat = find_ghost_material()
    if mat is None:
        return
    r, g, b, _a = mat.diffuse_color
    mat.diffuse_color = (r, g, b, value)
    bsdf = _ghost_bsdf(mat)
    if bsdf is not None:
        bsdf.inputs["Alpha"].default_value = value


def ensure_ghost_material():
    """The file's ghost material, created on first use.

    An existing one is returned untouched: its alpha is the user's setting,
    not something to reset every time the button is pressed.
    """
    mat = find_ghost_material()
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(GHOST_MAT_NAME)
    mat.use_nodes = True
    # Material Preview only; Solid ignores it. Material.shadow_method is gone
    # as of 4.2 (EEVEE Next) and blend_method may follow, so it is guarded.
    if hasattr(mat, "blend_method"):
        mat.blend_method = 'BLEND'
    bsdf = _ghost_bsdf(mat)
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (0.8, 0.8, 0.8, 1.0)
    mat.diffuse_color = (0.8, 0.8, 0.8, GHOST_DEFAULT_ALPHA)
    set_ghost_alpha(GHOST_DEFAULT_ALPHA)
    return mat


def _is_ghost(mat) -> bool:
    return mat is not None and any(
        mat.name == name or mat.name.startswith(name + ".")
        for name in GHOST_MAT_NAMES
    )


def ghost_is_on(retopo) -> bool:
    """True when the retopo is currently carrying the ghost."""
    me = getattr(retopo, "data", None)
    if me is None or not hasattr(me, "materials"):
        return False
    return any(_is_ghost(m) for m in me.materials)


def blocking_material(retopo):
    """The name of something already in the retopo's material slots, or None
    when the ghost can be added safely.

    A working retopo carries no material at all — finalize.py puts it plainly:
    "the retopo is a working mesh nobody shades". When one IS there, the slot
    layout belongs to the user: overwriting slot 0 would hide their shading,
    and appending a slot would leave every polygon still pointing at index 0,
    so the ghost would be added and nothing would change. Rewriting
    material_index across the mesh is not this tool's call. It refuses instead
    and says what is in the way.
    """
    me = getattr(retopo, "data", None)
    if me is None or not hasattr(me, "materials"):
        return None
    for mat in me.materials:
        if not _is_ghost(mat):
            return mat.name if mat is not None else "an empty material slot"
    return None


def add_ghost(retopo):
    """Put the ghost on the retopo. Returns (material, error message)."""
    blocker = blocking_material(retopo)
    if blocker is not None:
        return None, (f"'{retopo.name}' already carries {blocker} — its "
                      f"material slots are left alone. Remove it first, or "
                      f"work without the ghost")
    mat = ensure_ghost_material()
    if ghost_is_on(retopo):
        return mat, None
    # The mesh has no slots at all here, so the appended material lands at
    # index 0 and every polygon's material_index (0 by default) already points
    # at it — measured: 0 slots -> append -> 1 slot, face indices [0].
    retopo.data.materials.append(mat)
    return mat, None


def clear_retopology_overlay() -> int:
    """Switch the Retopology overlay off in EVERY 3D view of every workspace,
    and return how many were on.

    Wider than set_material_shading, which only touches the screen you are
    looking at, and deliberately so: while that overlay is on it repaints the
    edit-mesh faces from the theme and the ghost's alpha does nothing, so a
    file that still carries it in the Modeling or UV Editing workspace would
    look like the ghost is broken as soon as you switch tabs. It is saved per
    3D View inside the .blend (measured: survives save and reload) and older
    versions of this add-on wrote it on, so old files all carry it. X-Ray is
    NOT included here — that one was never ours, and someone may want it on
    in a workspace that has nothing to do with this.
    """
    n = 0
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for space in area.spaces:
                if space.type == 'VIEW_3D' and space.overlay.show_retopology:
                    space.overlay.show_retopology = False
                    n += 1
    return n


def remove_ghost(retopo) -> int:
    """Take the ghost slot back off the retopo, leaving the material itself in
    the file with its alpha. Returns how many slots went."""
    me = getattr(retopo, "data", None)
    if me is None or not hasattr(me, "materials"):
        return 0
    slots = [i for i, m in enumerate(me.materials) if _is_ghost(m)]
    for i in reversed(slots):
        me.materials.pop(index=i)
    return len(slots)
