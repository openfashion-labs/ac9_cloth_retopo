"""GPU seam-line overlay for the CLO Retopo Projector.

Draws UV seam edges as always-on-top white lines in 3D space over the Guide mesh.

Island colouring has moved to a baked Color Attribute on the Guide mesh
(see operators.AC9_OT_BakeIslandColors).  That approach uses Blender's normal
rendering pipeline and therefore preserves lighting / 3-D shading.

The seam lines remain as a GPU overlay because they need to be always-visible
regardless of viewport mode or material settings.

Position tracking
-----------------
_blended_verts_world() blends Basis → flat ShapeKey at whatever value the user
has set, so the seam lines always match the Guide's current viewport display
(3D form at SK=0, flat layout at SK=1, or any blend in between).
The flat-SK value is polled every draw frame so manual slider changes are
picked up automatically.
"""

import bpy
import gpu
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader

_SEAM_COLOR     = (1.0, 1.0, 1.0, 1.0)
_SEAM_WIDTH     = 2.0
_BOUNDARY_COLOR = (0.2, 1.0, 0.35, 1.0)   # bright green
_BOUNDARY_WIDTH = 2.0

_draw_handle = None

_state: dict = {
    "batch_seams":              None,
    "dirty":                    True,
    "jumped_indices":           frozenset(),
    "last_flat_sk_val":         -1.0,
    # boundary-vert visualiser
    "batch_boundary":           None,
    "boundary_dirty":           True,   # True on startup so first draw triggers a build
    "last_boundary_vert_count": -1,     # track retopo vert count to detect topology changes
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API — called by operators
# ─────────────────────────────────────────────────────────────────────────────

def invalidate():
    """Mark the seam overlay as needing a rebuild on the next draw."""
    _state["dirty"] = True
    _tag_redraw_3d()


def invalidate_boundary():
    """Mark the boundary-vert overlay as needing a rebuild on the next draw."""
    _state["boundary_dirty"] = True
    _tag_redraw_3d()


def set_jumped_indices(indices):
    """Store jumped island vertex indices (used for UI warning; no GPU draw)."""
    _state["jumped_indices"] = frozenset(indices)
    _tag_redraw_3d()   # refresh panel so the warning label updates


# ─────────────────────────────────────────────────────────────────────────────
# Position helper
# ─────────────────────────────────────────────────────────────────────────────

def _blended_verts_world(obj, flat_sk_name: str) -> list:
    """World-space vertex positions blending Basis → flat SK at its current value."""
    mesh   = obj.data
    matrix = obj.matrix_world
    n      = len(mesh.vertices)

    from .guide import guide_3d_shapekey
    sk3d = guide_3d_shapekey(mesh)
    if mesh.shape_keys and sk3d in mesh.shape_keys.key_blocks:
        base_data = mesh.shape_keys.key_blocks[sk3d].data
        base = [base_data[i].co for i in range(n)]
    else:
        base = [v.co for v in mesh.vertices]

    blend   = 0.0
    sk_b_co = None
    if flat_sk_name and mesh.shape_keys and flat_sk_name in mesh.shape_keys.key_blocks:
        blend = mesh.shape_keys.key_blocks[flat_sk_name].value
        if blend > 0.001:
            sk_b_co = mesh.shape_keys.key_blocks[flat_sk_name].data

    if sk_b_co and blend > 0.001:
        return [matrix @ base[i].lerp(sk_b_co[i].co, blend) for i in range(n)]
    return [matrix @ co for co in base]


# ─────────────────────────────────────────────────────────────────────────────
# Batch builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_seam_batch(mesh_data, verts_world: list):
    coords = []
    for e in mesh_data.edges:
        if e.use_seam:
            coords.append(verts_world[e.vertices[0]])
            coords.append(verts_world[e.vertices[1]])
    if not coords:
        return None
    shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    return batch_for_shader(shader, "LINES", {"pos": coords})


# ─────────────────────────────────────────────────────────────────────────────
# Lazy rebuild
# ─────────────────────────────────────────────────────────────────────────────

def _rebuild(context):
    top = getattr(getattr(context, "scene", None), "ac9_cloth_retopo", None)
    if top is None or not top.proj.show_seam_lines:
        _state["batch_seams"] = None
        _state["dirty"]       = False
        return

    guide_obj = top.guide_obj
    if guide_obj is None:
        _state["batch_seams"] = None
        _state["dirty"]       = False
        return

    try:
        flat_sk_name = top.guide_flat_shapekey
        verts_world  = _blended_verts_world(guide_obj, flat_sk_name)
        _state["batch_seams"] = _build_seam_batch(guide_obj.data, verts_world)
    except Exception as exc:  # noqa: BLE001
        _state["batch_seams"] = None
        print(f"[AC9 Cloth Retopo] Seam overlay rebuild error: {exc}")
    finally:
        _state["dirty"] = False


def _rebuild_boundary(context):
    """Build LINES batch of small + crosses at every ac9_is_boundary=True vert.

    Positions are read from the retopo Basis ShapeKey (2D layout) in world
    space.  The cross arm length is proportional to the boundary_cross_size
    property, defaulting to 0.008 (roughly 8 mm in the 0-1 m UV grid).
    """
    top = getattr(getattr(context, "scene", None), "ac9_cloth_retopo", None)
    _state["boundary_dirty"] = False
    if top is None:
        _state["batch_boundary"] = None
        return

    retopo = top.retopo_obj
    if retopo is None or retopo.type != "MESH":
        _state["batch_boundary"] = None
        return

    try:
        from .attachment import load_boundary_from_mesh
        from .guide import get_basis_local

        mesh      = retopo.data
        matrix    = retopo.matrix_world
        boundary  = load_boundary_from_mesh(mesh)
        basis_co  = get_basis_local(retopo)
        s         = top.proj.boundary_cross_size

        coords = []
        for i, is_b in enumerate(boundary):
            if not is_b or i >= len(basis_co):
                continue
            p = matrix @ basis_co[i]
            coords += [
                p + type(p)((-s, 0, 0)), p + type(p)((s, 0, 0)),
                p + type(p)((0, -s, 0)), p + type(p)((0, s, 0)),
            ]

        if not coords:
            _state["batch_boundary"] = None
            return
        shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        _state["batch_boundary"] = batch_for_shader(shader, "LINES", {"pos": coords})
    except Exception as exc:  # noqa: BLE001
        _state["batch_boundary"] = None
        print(f"[AC9 Cloth Retopo] Boundary overlay rebuild error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Draw callback (POST_VIEW)
# ─────────────────────────────────────────────────────────────────────────────

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

    show_seams    = top.proj.show_seam_lines
    show_boundary = top.proj.show_boundary_verts

    # Nothing to draw — skip the expensive rebuild / batch work entirely.
    if not show_seams and not show_boundary:
        return

    # Auto-invalidate boundary when retopo mesh topology changes (vert count
    # changes after Edit Mode add/remove → attribute length mismatch → blank batch).
    if show_boundary:
        retopo = top.retopo_obj
        if retopo is not None and retopo.type == "MESH":
            cur_n = len(retopo.data.vertices)
            if cur_n != _state["last_boundary_vert_count"]:
                _state["boundary_dirty"]           = True
                _state["last_boundary_vert_count"] = cur_n

    # Track Guide flat-SK slider changes → auto-rebuild seam positions.
    # Only needed when seam lines are visible.
    if show_seams:
        guide_obj    = top.guide_obj
        flat_sk_name = top.guide_flat_shapekey
        if guide_obj and flat_sk_name:
            sk_data = getattr(guide_obj.data, "shape_keys", None)
            if sk_data and flat_sk_name in sk_data.key_blocks:
                cur_val = sk_data.key_blocks[flat_sk_name].value
                if abs(cur_val - _state["last_flat_sk_val"]) > 0.001:
                    _state["dirty"]            = True
                    _state["last_flat_sk_val"] = cur_val

    if _state["dirty"]:
        _rebuild(context)
    # Only rebuild boundary when it will actually be drawn.
    # Calling _rebuild_boundary while show=False would set batch_boundary=None,
    # and the subsequent toggle-ON draw might not re-trigger the rebuild in time.
    # Guard: also rebuild if the batch was externally cleared while show=True.
    if show_boundary and (_state["boundary_dirty"] or _state["batch_boundary"] is None):
        _rebuild_boundary(context)
    elif not show_boundary:
        # Let the dirty flag accumulate while hidden so the first draw after
        # show=True picks it up.  Only clear it here if no rebuild is pending,
        # preventing a double-rebuild on the next toggle-ON frame.
        pass  # boundary_dirty intentionally left as-is

    region = context.region
    if region is None:
        return
    vp = (float(region.width), float(region.height))

    shader_line = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    gpu.state.depth_test_set("ALWAYS")
    gpu.state.blend_set("ALPHA")

    if show_seams:
        batch_seams = _state["batch_seams"]
        if batch_seams is not None:
            shader_line.bind()
            shader_line.uniform_float("color", _SEAM_COLOR)
            shader_line.uniform_float("lineWidth", _SEAM_WIDTH)
            shader_line.uniform_float("viewportSize", vp)
            batch_seams.draw(shader_line)

    if show_boundary:
        batch_boundary = _state["batch_boundary"]
        if batch_boundary is not None:
            shader_line.bind()
            shader_line.uniform_float("color", _BOUNDARY_COLOR)
            shader_line.uniform_float("lineWidth", _BOUNDARY_WIDTH)
            shader_line.uniform_float("viewportSize", vp)
            batch_boundary.draw(shader_line)

    gpu.state.blend_set("NONE")
    gpu.state.depth_test_set("NONE")


# ─────────────────────────────────────────────────────────────────────────────
# Register / unregister
# ─────────────────────────────────────────────────────────────────────────────

@persistent
def _load_post(_dummy):
    _state["batch_seams"]              = None
    _state["dirty"]                    = True
    _state["jumped_indices"]           = frozenset()
    _state["last_flat_sk_val"]         = -1.0
    _state["batch_boundary"]           = None
    _state["boundary_dirty"]           = True
    _state["last_boundary_vert_count"] = -1


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
    _state["batch_seams"]              = None
    _state["dirty"]                    = True
    _state["jumped_indices"]           = frozenset()
    _state["last_flat_sk_val"]         = -1.0
    _state["batch_boundary"]           = None
    _state["boundary_dirty"]           = True
    _state["last_boundary_vert_count"] = -1
