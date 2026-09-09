"""Depsgraph handlers that react to the retopo's EDIT → OBJECT transition.

Two independent, mutually-exclusive-by-state behaviors live here:

1. Auto-bind (3D state): repair freshly-created verts the moment Edit Mode
   ends. When the user adds vertices in 3D Edit Mode (extrude, knife, fill,
   …) on the retopo mesh, Blender's shape-key custom-data interpolation fills
   the new verts' NON-active key layers (here: Basis = the 2D layout) with
   copied / interpolated / sometimes garbage coordinates. The instant Edit
   Mode ends and the viewport switches back to the
   `Basis + value × (Key − Basis)` mix, those verts visibly fly away or
   collapse — before any Sync button is pressed.

   The addon cannot stop Blender from writing those coords, but it can repair
   them immediately after: this handler runs core.run_bind_new_verts via a
   deferred timer, which binds every status==none vert (neighbour-hinted,
   2D-coherent — see attachment.bind_new_verts_3d) and rewrites both its Basis
   (2D) and AC9_3D_Project (3D) coordinates consistently.

2. Auto-refresh (2D state): re-run Refresh 3D Mirror the moment Edit Mode
   ends on the 2D retopo, so the mirror never goes stale while editing.
   Skipped when the mirror currently holds un-applied "Apply 3D Edits > 2D"
   moves (see mirror.mirror_has_unsynced_edits) — refreshing would silently
   discard them.

Both watch the same mode transition but are gated on opposite ShapeKey
states (3D vs 2D), so they never fire on the same transition.

Cheap when idle: the handler body is a few attribute reads per depsgraph tick
and only schedules work on an actual mode transition.
"""

import contextlib

import bpy
from bpy.app.handlers import persistent

# Last seen mode per retopo object name — used to detect the EDIT → OBJECT
# transition (the depsgraph handler fires on every update, not just mode
# changes, so we must diff against previous state ourselves).
_last_mode: dict = {}

# Re-entry guards: the bind/refresh itself writes mesh data, which re-fires
# the depsgraph handler; and a timer must not be scheduled twice.
_timer_scheduled = False
_binding = False
_refresh_timer_scheduled = False
_refreshing = False
_suppressed = False


@contextlib.contextmanager
def suppressed():
    """Disable auto-bind scheduling for the duration of the block.

    Used by operators that perform their own Edit↔Object round-trip AND their
    own complete binding (e.g. Subdivide Retopo): their mode transitions must
    not schedule a redundant auto-bind pass on top of the binding they already
    did themselves.
    """
    global _suppressed
    prev = _suppressed
    _suppressed = True
    try:
        yield
    finally:
        _suppressed = prev


@persistent
def _on_depsgraph_update(scene, depsgraph):
    global _timer_scheduled, _refresh_timer_scheduled
    if _binding or _refreshing:
        return
    top = getattr(scene, "ac9_cloth_retopo", None)
    if top is None:
        return
    retopo = top.retopo_obj
    if retopo is None or retopo.type != "MESH":
        return

    mode = retopo.mode
    prev = _last_mode.get(retopo.name)
    _last_mode[retopo.name] = mode
    if prev != "EDIT" or mode != "OBJECT":
        return
    # Suppression check AFTER recording the mode so a suppressed transition is
    # consumed (not deferred to the next depsgraph tick).
    if _suppressed:
        return

    from . import core
    sk = retopo.data.shape_keys
    in_3d = (
        sk is not None
        and core.SHAPEKEY_NAME in sk.key_blocks
        and sk.key_blocks[core.SHAPEKEY_NAME].value > 0.5
    )

    if in_3d:
        # Only meaningful in the 3D state: the bind reads each new vert's 3D
        # position. In the 2D state new verts are handled by Sync 2D > 3D.
        if not getattr(top.proj, "auto_bind_new_verts", False):
            return
        if _timer_scheduled:
            return
        _timer_scheduled = True
        # Defer the actual work out of the depsgraph callback — writing mesh
        # data from inside the handler is unsafe (recursive evaluation).
        bpy.app.timers.register(_deferred_bind, first_interval=0.0)
    else:
        # 2D state: this is the retopo's own authoritative edit surface, so a
        # Mirror Refresh is what "the mirror never goes stale" means here.
        if not getattr(top.proj, "live_update", False):
            return
        if _refresh_timer_scheduled:
            return
        _refresh_timer_scheduled = True
        bpy.app.timers.register(_deferred_refresh, first_interval=0.0)


def _deferred_bind():
    global _timer_scheduled, _binding
    _timer_scheduled = False

    scene = bpy.context.scene
    top = getattr(scene, "ac9_cloth_retopo", None)
    if top is None:
        return None
    retopo = top.retopo_obj
    guide_obj = top.guide_obj
    flat_sk = top.guide_flat_shapekey
    if (
        retopo is None or retopo.type != "MESH" or retopo.mode != "OBJECT"
        or guide_obj is None or not flat_sk
    ):
        return None

    from . import core
    # Re-check the 3D-state condition at FIRE time, not just at schedule time:
    # a Sync pressed between the Edit-mode exit and this timer can have flipped
    # the viewport to the 2D state, where a bind pass must not run.
    sk = retopo.data.shape_keys
    if (
        sk is None
        or core.SHAPEKEY_NAME not in sk.key_blocks
        or sk.key_blocks[core.SHAPEKEY_NAME].value <= 0.5
    ):
        return None
    _binding = True
    try:
        result = core.run_bind_new_verts(bpy.context, retopo, guide_obj, flat_sk)
    except Exception as exc:  # never let a timer exception linger silently
        print(f"[AC9 CLO Projector] Auto-bind new verts failed: {exc}")
        return None
    finally:
        _binding = False

    if result.success and result.new_verts_computed:
        print(
            f"[AC9 CLO Projector] Auto-bound {result.new_verts_computed} new "
            f"vert(s) on Edit-mode exit ({result.projected} repaired)."
        )
        # Give the user a clean undo point covering the repair.
        try:
            bpy.ops.ed.undo_push(message="AC9 Bind New Verts")
        except RuntimeError:
            pass
    return None


def _deferred_refresh():
    global _refresh_timer_scheduled, _refreshing
    _refresh_timer_scheduled = False

    scene = bpy.context.scene
    top = getattr(scene, "ac9_cloth_retopo", None)
    if top is None:
        return None
    retopo = top.retopo_obj
    guide_obj = top.guide_obj
    flat_sk = top.guide_flat_shapekey
    if (
        retopo is None or retopo.type != "MESH" or retopo.mode != "OBJECT"
        or guide_obj is None or not flat_sk
    ):
        return None

    from . import core, mirror as mirror_mod
    # Re-check the 2D-state condition at FIRE time — a Sync pressed between
    # the Edit-mode exit and this timer can have flipped the viewport to the
    # 3D state, where a mirror Refresh is meaningless.
    sk = retopo.data.shape_keys
    if (
        sk is not None
        and core.SHAPEKEY_NAME in sk.key_blocks
        and sk.key_blocks[core.SHAPEKEY_NAME].value > 0.5
    ):
        return None
    if mirror_mod.find_mirror(retopo) is None:
        return None  # nothing to refresh yet — first Refresh must be manual

    if mirror_mod.mirror_has_unsynced_edits(retopo, guide_obj, flat_sk):
        print(
            "[AC9 CLO Projector] Auto-refresh skipped: the Mirror holds "
            "un-applied 'Apply 3D Edits > 2D' moves. Press Refresh manually "
            "once you've applied or discarded them."
        )
        return None

    _refreshing = True
    try:
        result, _mirror = mirror_mod.refresh_mirror(bpy.context, retopo, guide_obj, flat_sk)
    except Exception as exc:  # never let a timer exception linger silently
        print(f"[AC9 CLO Projector] Auto-refresh failed: {exc}")
        return None
    finally:
        _refreshing = False

    if not result.success:
        print(f"[AC9 CLO Projector] Auto-refresh failed: {result.error}")
    return None


def register():
    if _on_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)


def unregister():
    while _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    _last_mode.clear()
