"""Depsgraph handlers that react to the retopo's EDIT → OBJECT transition.

Auto-bind (3D state): repair freshly-created verts the moment Edit Mode
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
(2D) and AC9_3D_Project (3D) coordinates consistently. Only meaningful in
the 3D state; in the 2D state new verts are handled by Sync 2D > 3D.

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

# Re-entry guards: the bind itself writes mesh data, which re-fires the
# depsgraph handler; and a timer must not be scheduled twice.
_timer_scheduled = False
_binding = False
_suppressed = False

# Live hidden-geometry sync (see mirror.sync_hidden). Same shape as the
# auto-bind guards: one pending timer at a time, plus a re-entry flag, because
# the sync writes the mirror's edit mesh and that re-fires this handler.
_hide_sync_scheduled = False
_hide_syncing = False
# Long enough that a drag's worth of depsgraph updates collapses into one pass
# (the scan is per-element Python), short enough to read as immediate.
_HIDE_SYNC_DELAY = 0.15


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
    global _timer_scheduled
    if _binding:
        return
    top = getattr(scene, "ac9_cloth_retopo", None)
    if top is None:
        return
    retopo = top.retopo_obj
    if retopo is None or retopo.type != "MESH":
        return

    # Guide Edit Mode changes the projection surface and/or its flat source.
    # As with Retopo edits, a depsgraph tick may be selection-only; marking a
    # preview stale too often is preferable to silently displaying old data.
    guide_obj = top.guide_obj
    if (guide_obj is not None and guide_obj.type == "MESH"
            and guide_obj.mode == "EDIT"):
        from . import mirror as mirror_mod
        mirror_mod.mark_preview_dirty(retopo)

    # Hiding geometry is an Edit-Mode-only act, and Blender only DRAWS geometry
    # as hidden in Edit Mode, so that is the whole window where a live sync
    # means anything. Scheduling is two attribute reads; the scan happens in
    # the timer, where a burst of updates has already collapsed into one pass.
    if retopo.mode == "EDIT" and not _hide_syncing:
        # Any Edit-mesh depsgraph tick may be a coordinate/topology change.
        # Over-detecting selection-only updates is safe; a stale preview is
        # never silently presented as current.
        from . import mirror as mirror_mod
        mirror_mod.mark_preview_dirty(retopo)
        _schedule_hide_sync()

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

    if not in_3d:
        # 2D state: new verts are handled by Sync 2D > 3D, not this handler.
        return
    # Only meaningful in the 3D state: the bind reads each new vert's 3D
    # position.
    if not getattr(top.proj, "auto_bind_new_verts", False):
        return
    if _timer_scheduled:
        return
    _timer_scheduled = True
    # Defer the actual work out of the depsgraph callback — writing mesh
    # data from inside the handler is unsafe (recursive evaluation).
    bpy.app.timers.register(_deferred_bind, first_interval=0.0)


def _schedule_hide_sync():
    """Queue one deferred hidden-geometry sync (no-op if one is pending)."""
    global _hide_sync_scheduled
    if _hide_sync_scheduled:
        return
    _hide_sync_scheduled = True
    bpy.app.timers.register(_deferred_hide_sync,
                            first_interval=_HIDE_SYNC_DELAY)


def _deferred_hide_sync():
    """Copy the retopo's hidden geometry onto the mirror, out of the depsgraph
    callback (writing mesh data from inside one is unsafe).

    Cheap when nothing moved: sync_hidden writes only a real difference, so a
    pass that finds the two already in agreement ends here without tagging the
    depsgraph — which is what stops this from feeding itself.
    """
    global _hide_sync_scheduled, _hide_syncing
    _hide_sync_scheduled = False

    scene = getattr(bpy.context, "scene", None)
    top = getattr(scene, "ac9_cloth_retopo", None) if scene else None
    if top is None:
        return None
    retopo = top.retopo_obj
    if retopo is None or retopo.type != "MESH" or retopo.mode != "EDIT":
        return None

    from . import mirror as mirror_mod
    _hide_syncing = True
    try:
        changed = mirror_mod.sync_hidden(retopo)
    except Exception as exc:  # never let a timer exception linger silently
        print(f"[AC9 CLO Projector] Hidden-geometry sync failed: {exc}")
        return None
    finally:
        _hide_syncing = False

    if changed:
        screen = getattr(bpy.context, "screen", None)
        for area in getattr(screen, "areas", ()):
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    return None


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


def register():
    if _on_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)


def unregister():
    global _hide_sync_scheduled
    while _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    _last_mode.clear()
    # A pending timer would fire into an unregistered add-on (Reload Scripts,
    # disable): drop it, and clear the flag so a re-register can schedule again.
    if bpy.app.timers.is_registered(_deferred_hide_sync):
        bpy.app.timers.unregister(_deferred_hide_sync)
    _hide_sync_scheduled = False
