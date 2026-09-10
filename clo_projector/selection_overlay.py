"""Selection-correspondence overlay for the CLO Retopo Projector.

The retopo (2D) and its AC9_3D_Mirror (3D) share vertex indexing 1:1, but
Blender can only edit ONE object at a time and won't show vertex selection on
the other (object-mode) object.  So instead of fighting native selection, this
overlay draws markers on the *partner* object at the positions corresponding to
whatever is selected in the active edit object:

  * Editing the 2D retopo  → markers light up on the 3D mirror (garment).
  * Editing the 3D mirror   → markers light up on the 2D retopo (flat layout).

This answers "where is this 3D vertex in 2D?" (and vice-versa) at a glance,
with no flipping and no clicking back and forth.

Vertex selection is read regardless of the select mode: selecting an edge or a
face also flags its vertices as selected in bmesh, so edge/face/loop/shortest-
path selections all map across correctly.
"""

import bpy
import gpu
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader

from . import mirror as mirror_mod

_MARKER_COLOR = (1.0, 0.55, 0.05, 1.0)   # orange — distinct from seams/boundary

_draw_handle = None

_state: dict = {
    "batch": None,
    "sig":   None,     # (source_name, target_name, frozenset(selected_indices))
    "dirty": True,     # force rebuild (e.g. after a Refresh moved target verts)
}


def invalidate_selection():
    """Force the correspondence batch to rebuild on the next draw (target vert
    positions changed, e.g. after Refresh)."""
    _state["dirty"] = True
    _tag_redraw_3d()


def _resolve_source_target(context):
    """Return (source_obj, target_obj, target_world_positions) or (None,...).

    source = the active object IF it is in mesh Edit Mode and is either the
    retopo or its mirror; target = the partner object whose corresponding
    vertices we mark.
    """
    top = getattr(getattr(context, "scene", None), "ac9_cloth_retopo", None)
    if top is None:
        return None, None, None
    retopo = top.retopo_obj
    if retopo is None or retopo.type != "MESH":
        return None, None, None

    active = context.object
    if active is None or active.mode != "EDIT" or active.type != "MESH":
        return None, None, None

    mirror = mirror_mod.find_mirror(retopo)

    if active is retopo:
        if mirror is None:
            return None, None, None
        mw = mirror.matrix_world
        target_pos = [mw @ v.co for v in mirror.data.vertices]
        return retopo, mirror, target_pos

    if mirror is not None and active is mirror:
        from .guide import get_basis_local
        mw = retopo.matrix_world
        target_pos = [mw @ co for co in get_basis_local(retopo)]
        return mirror, retopo, target_pos

    return None, None, None


def _selected_indices(edit_obj):
    """Indices of selected vertices in *edit_obj*'s edit bmesh."""
    import bmesh
    try:
        bm = bmesh.from_edit_mesh(edit_obj.data)
    except Exception:  # noqa: BLE001
        return frozenset()
    return frozenset(v.index for v in bm.verts if v.select)


def _draw_callback():
    if bpy.app.background:   # no GPU drawing in headless / background mode
        return
    context = bpy.context
    if context is None:
        return
    top = getattr(getattr(context, "scene", None), "ac9_cloth_retopo", None)
    if top is None:
        return
    if not getattr(top, "show_overlays", True):
        return
    if not getattr(top.proj, "show_selection_link", True):
        return

    source, target, target_pos = _resolve_source_target(context)
    if source is None:
        _state["batch"] = None
        _state["sig"] = None
        return

    selected = _selected_indices(source)
    sig = (source.name, target.name, selected)

    if _state["dirty"] or sig != _state["sig"] or _state["batch"] is None:
        n_target = len(target_pos)
        coords = [target_pos[i] for i in selected if 0 <= i < n_target]
        if coords:
            shader = gpu.shader.from_builtin("UNIFORM_COLOR")
            _state["batch"] = batch_for_shader(shader, "POINTS", {"pos": coords})
        else:
            _state["batch"] = None
        _state["sig"] = sig
        _state["dirty"] = False

    batch = _state["batch"]
    if batch is None:
        return

    size = getattr(top.proj, "selection_point_size", 9.0)
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.depth_test_set("ALWAYS")
    gpu.state.blend_set("ALPHA")
    gpu.state.point_size_set(size)
    shader.bind()
    shader.uniform_float("color", _MARKER_COLOR)
    batch.draw(shader)
    gpu.state.point_size_set(1.0)
    gpu.state.blend_set("NONE")
    gpu.state.depth_test_set("NONE")


@persistent
def _load_post(_dummy):
    _state["batch"] = None
    _state["sig"] = None
    _state["dirty"] = True


def _tag_redraw_3d():
    try:
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass


def register_draw_handler():
    global _draw_handle
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, (), "WINDOW", "POST_VIEW"
        )
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)


def unregister_draw_handler():
    global _draw_handle
    while _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, "WINDOW")
        _draw_handle = None
    _state["batch"] = None
    _state["sig"] = None
    _state["dirty"] = True
