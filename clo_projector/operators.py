"""CLO Projector operators. Thin wrappers around the shared pipeline in core.py."""

import colorsys

import bpy

from . import core
from . import mirror as mirror_mod
from .. import ui_common as uic

# Attribute / material names used by the island-colour bake feature.
_ISLAND_COLOR_ATTR = "AC9_Island_Color"
_ISLAND_MAT_NAME   = "AC9_Island_Colors"


def _get_guide(context):
    """Return (guide_obj, flat_sk) from the scene props, or (None, None)."""
    top = getattr(getattr(context, "scene", None), "ac9_cloth_retopo", None)
    if top is None:
        return None, None
    props = top.proj
    return top.guide_obj, top.guide_flat_shapekey


class _ModeSwitchMixin:
    """Auto-switch to Object Mode for mesh-data writes (ShapeKey / vertex group
    / vertex.co), restoring the original mode on exit.
    """

    def _run_in_object_mode(self, context, fn):
        prev_mode = context.object.mode if context.object is not None else "OBJECT"
        if prev_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        try:
            return fn()
        finally:
            if prev_mode != "OBJECT":
                try:
                    bpy.ops.object.mode_set(mode=prev_mode)
                except RuntimeError:
                    pass


class AC9_OT_CreateProjection(bpy.types.Operator, _ModeSwitchMixin):
    bl_idname = "ac9_cloth.create_projection"
    bl_label = "Sync 2D > 3D"
    bl_description = (
        "Re-project every 2D retopo vertex onto Guide 3D (Basis) and write the "
        "result to the AC9_3D_Project ShapeKey. Press whenever you've edited the "
        "2D layout. Counterpart of 'Sync 3D > 2D'"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not uic.experimental_enabled(context):
            return False
        # See AC9_OT_ReverseProjection.poll: the Edit→Object→Edit round-trip
        # around a heavy mesh rewrite crashes on large guides. Object Mode only.
        if context.mode != "OBJECT":
            cls.poll_message_set("Exit Edit Mode first (Tab) — Sync runs in Object Mode.")
            return False
        return True

    def execute(self, context):
        top = context.scene.ac9_cloth_retopo
        props = top.proj
        retopo = top.retopo_obj
        guide_obj = top.guide_obj
        flat_sk = top.guide_flat_shapekey

        if retopo is None or retopo.type != "MESH":
            self.report({"ERROR"}, "Retopo must be a mesh object.")
            return {"CANCELLED"}

        wm = context.window_manager
        with uic.ProgressScope(wm) as prog:
            def go():
                return core.run_forward_projection(
                    context,
                    retopo,
                    guide_obj,
                    flat_sk,
                    overwrite_shapekey=props.overwrite_shapekey,
                    clear_failed_group=props.clear_failed_group,
                    select_failed=props.select_failed,
                    incremental=False,
                    progress=uic.ProgressThrottle(wm, lo=0, hi=95),
                )

            result = self._run_in_object_mode(context, go)
            prog.update(1.0)

        if not result.success:
            self.report({"ERROR"}, result.error or "Projection failed.")
            return {"CANCELLED"}

        # Switch to AC9_3D_Project at full value so user sees the 3D result.
        shape_keys = retopo.data.shape_keys
        if shape_keys is not None and core.SHAPEKEY_NAME in shape_keys.key_blocks:
            idx = shape_keys.key_blocks.find(core.SHAPEKEY_NAME)
            retopo.active_shape_key_index = idx
            shape_keys.key_blocks[core.SHAPEKEY_NAME].value = 1.0

        # Auto-set Guide flat SK to 0.0 → viewport shows 3D form.
        if guide_obj and flat_sk:
            sk_data = getattr(guide_obj.data, "shape_keys", None)
            if sk_data and flat_sk in sk_data.key_blocks:
                sk_data.key_blocks[flat_sk].value = 0.0

        # Rebuild island overlay (no jumps on forward projection).
        from . import gpu_overlay as proj_overlay
        proj_overlay.set_jumped_indices(())
        proj_overlay.invalidate()
        if "ac9_cloth_retopo_island_jumped" in context.scene:
            del context.scene["ac9_cloth_retopo_island_jumped"]

        self.report(
            {"INFO"},
            f"Projected: {result.projected} / {result.total}  "
            f"Failed: {result.failed}  "
            f"ShapeKey: {core.SHAPEKEY_NAME}",
        )
        return {"FINISHED"}


class AC9_OT_RefreshMirror(bpy.types.Operator, _ModeSwitchMixin):
    bl_idname = "ac9_cloth.refresh_mirror"
    bl_label = "Refresh Mirror"
    bl_description = (
        "Rebuild the Mirror (the read-only AC9_3D_Mirror object) from the "
        "current 2D retopo layout, projected onto the Guide surface. The retopo "
        "stays flat (2D) and editable; the Mirror shows the 3D result on the "
        "garment. Press after editing the 2D layout — works in Object and Edit "
        "Mode. The Mirror is a viewer: any edits made to it are overwritten on "
        "the next Refresh"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        # Works in Object Mode and in Edit Mode (including BOTH retopo + mirror
        # in multi-object Edit Mode). The edit path reads the retopo's live
        # edit-mesh and writes ONLY the mirror (its edit bmesh when the mirror
        # is itself in Edit Mode), never the retopo mesh — so it avoids the
        # mode-switch crash the old Sync operators hit. Active object can be
        # either one; we always read the retopo specifically.
        top = getattr(context.scene, "ac9_cloth_retopo", None)
        retopo = top.retopo_obj if top is not None else None
        if retopo is None or retopo.type != "MESH":
            cls.poll_message_set("Set the Retopo first.")
            return False
        if context.mode not in {"OBJECT", "EDIT_MESH"}:
            cls.poll_message_set("Refresh runs in Object or Edit Mode.")
            return False
        return True

    def execute(self, context):
        top = context.scene.ac9_cloth_retopo
        retopo = top.retopo_obj
        guide_obj = top.guide_obj
        flat_sk = top.guide_flat_shapekey

        if retopo is None or retopo.type != "MESH":
            self.report({"ERROR"}, "Retopo must be a mesh object.")
            return {"CANCELLED"}

        # Visibility is the View row's (see _apply_view_state); the only
        # visibility decision Refresh makes is how a Mirror that did not exist
        # yet comes into the world, so remember that fact before building.
        existed_before = mirror_mod.find_mirror(retopo) is not None

        wm = context.window_manager
        prog = uic.ProgressThrottle(wm)
        with uic.ProgressScope(wm):
            if context.mode == "EDIT_MESH":
                # Live refresh from the edit-mesh — retopo stays in Edit Mode.
                result, mirror = mirror_mod.refresh_mirror_editmode(
                    context, retopo, guide_obj, flat_sk,
                    progress=prog,
                )
            else:
                def go():
                    return mirror_mod.refresh_mirror(
                        context, retopo, guide_obj, flat_sk,
                        progress=prog,
                    )

                result, mirror = self._run_in_object_mode(context, go)

                # Keep the retopo displayed flat (2D): the mirror carries the 3D view.
                if result.success:
                    shape_keys = retopo.data.shape_keys
                    if shape_keys is not None:
                        retopo.active_shape_key_index = 0
                        if core.SHAPEKEY_NAME in shape_keys.key_blocks:
                            shape_keys.key_blocks[core.SHAPEKEY_NAME].value = 0.0
            wm.progress_update(100)

        if not result.success:
            self.report({"ERROR"}, result.error or "Refresh failed.")
            return {"CANCELLED"}

        # Refresh owns geometry only. A Mirror the user hid (View → Guide)
        # stays hidden, one they show stays shown, and the Guide's Flat SK /
        # visibility are never written here (on a six-figure-vertex Guide
        # every such write is a depsgraph re-evaluation plus a GPU re-upload,
        # and it used to yank the flat layout out from under the user
        # mid-edit).
        #
        # The one decision made: a Mirror that did NOT exist before is SHOWN,
        # by moving the View row to Mirror. This used to be "born matching the
        # View state it was built in" instead, which sounds like the same
        # restraint but could not be: before the very first Refresh there IS
        # no Mirror, so the state read back can only be Guide — the absence of
        # a Mirror, not a choice the user made about one — and the rule
        # therefore only ever produced an invisible new Mirror. You pressed
        # Refresh and nothing happened on screen (reported 2026-09-09).
        # Mirror is also the state that leaves the Guide's Flat SK at 1, so
        # the 2D layout the retopo is snapped against is not pulled away.
        # Deleting the Mirror by hand and pressing Refresh again counts as
        # "did not exist" too, which is the intended reading: the button was
        # pressed to get a Mirror, so the Mirror is what it shows.
        # A new object is in no Local View, so it stays out of the 2D
        # viewport by itself.
        if mirror is not None and not existed_before:
            if _flat_keyblock(top) is not None:
                _apply_view_state(context, top, VIEW_MIRROR)
            else:
                # No resolvable Flat SK (nothing else here needs one): at
                # least do not leave the new Mirror hidden.
                set_hide_if_changed(mirror, False)
        tag_redraw_3d(context)

        from . import gpu_overlay as proj_overlay
        proj_overlay.invalidate_boundary()
        from . import selection_overlay
        selection_overlay.invalidate_selection()

        # Also refresh the UV-seam ghost overlay so it tracks the new 2D layout
        # without a separate button press. Best-effort: rebuild_ghosts is a
        # no-op when no seam pairs are cached, and any failure here must never
        # break the mirror refresh.
        try:
            from ..uv_seam_guide.gpu_overlay import rebuild_ghosts, _tag_redraw_3d
            rebuild_ghosts(top.seam, retopo)
            _tag_redraw_3d(context)
        except Exception:
            pass

        if result.failed:
            # NOT "fell outside the Guide": a vert outside the pattern outline
            # is snapped to the nearest boundary and projects fine, so that
            # wording never described a real case. What actually fails is a
            # vert the Guide cannot map at all — state the consequence (it
            # stays at its 2D position, which is what the user sees) and put
            # the indices in the console, since those are what any
            # investigation needs first.
            if result.failed_indices:
                print(
                    f"[AC9 CLO Projector] Refresh: could not map "
                    f"{result.failed} vertex/vertices — indices: "
                    f"{list(result.failed_indices)}"
                )
            self.report(
                {"WARNING"},
                f"Refreshed mirror: {result.projected}/{result.total} projected. "
                f"{result.failed} vertex/vertices could not be mapped onto the "
                f"Guide and were left at their 2D position (see the "
                f"AC9_Project_Failed group; indices in the console).",
            )
        else:
            self.report(
                {"INFO"},
                f"Refreshed mirror: {result.projected}/{result.total} projected.",
            )
        return {"FINISHED"}


class AC9_OT_RemoveMirror(bpy.types.Operator):
    bl_idname = "ac9_cloth.remove_mirror"
    bl_label = "Remove Mirror"
    bl_description = (
        "Delete the Mirror (the read-only AC9_3D_Mirror object) for the current "
        "retopo. Rebuild it any time with 'Refresh Mirror'"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        top = context.scene.ac9_cloth_retopo
        retopo = top.retopo_obj
        if retopo is None or retopo.type != "MESH":
            self.report({"ERROR"}, "Retopo must be a mesh object.")
            return {"CANCELLED"}
        removed = mirror_mod.remove_mirror(retopo)
        self.report(
            {"INFO"},
            "Removed Mirror." if removed else "No Mirror to remove.",
        )
        return {"FINISHED"}


class AC9_OT_SyncMirrorTo2D(bpy.types.Operator, _ModeSwitchMixin):
    bl_idname = "ac9_cloth.sync_mirror_to_2d"
    bl_label = "Apply 3D Edits → 2D"
    bl_description = (
        "Take the moves you made to existing vertices on the Mirror, snap "
        "them onto the Guide surface, and rewrite the 2D retopo layout to match "
        "(boundary verts stay pinned to the CLO outline). Move-only: do NOT add "
        "verts or cut on the mirror — new geometry must be made in 2D. The "
        "mirror is rebuilt to the clean snapped result afterwards"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not uic.experimental_enabled(context):
            return False
        if context.mode != "OBJECT":
            cls.poll_message_set("Exit Edit Mode first (Tab) — runs in Object Mode.")
            return False
        top = getattr(context.scene, "ac9_cloth_retopo", None)
        retopo = top.retopo_obj if top is not None else None
        if retopo is None or retopo.type != "MESH":
            cls.poll_message_set("Set the Retopo first.")
            return False
        if mirror_mod.find_mirror(retopo) is None:
            cls.poll_message_set("No Mirror — press 'Refresh Mirror' first.")
            return False
        return True

    def execute(self, context):
        top = context.scene.ac9_cloth_retopo
        retopo = top.retopo_obj
        guide_obj = top.guide_obj
        flat_sk = top.guide_flat_shapekey

        mirror = mirror_mod.find_mirror(retopo)
        if mirror is None:
            self.report({"ERROR"}, "No Mirror found.")
            return {"CANCELLED"}
        if len(mirror.data.vertices) != len(retopo.data.vertices):
            self.report(
                {"ERROR"},
                "Mirror topology differs from the retopo — 'Apply 3D Edits' is "
                "move-only. Do cuts / new verts in 2D, then Refresh.",
            )
            return {"CANCELLED"}

        # Read the mirror's current (user-moved) 3D world positions.
        mw_m = mirror.matrix_world
        points_3d_world = [mw_m @ v.co for v in mirror.data.vertices]

        def go():
            return core.run_mirror_move_to_2d(
                context, retopo, guide_obj, flat_sk, points_3d_world
            )

        result = self._run_in_object_mode(context, go)

        if result is None:
            self.report({"ERROR"}, "Could not stage the mirror positions.")
            return {"CANCELLED"}
        if not result.success:
            # "No 3D edits detected" is informational, not an error.
            if result.error and "No 3D edits" in result.error:
                self.report({"INFO"}, "No mirror edits to apply.")
                return {"FINISHED"}
            self.report({"ERROR"}, result.error or "Apply failed.")
            return {"CANCELLED"}

        # Rebuild the mirror to the clean, surface-snapped result and keep the
        # retopo displayed flat (2D).
        mirror_mod.refresh_mirror(context, retopo, guide_obj, flat_sk)
        shape_keys = retopo.data.shape_keys
        if shape_keys is not None:
            retopo.active_shape_key_index = 0
            if core.SHAPEKEY_NAME in shape_keys.key_blocks:
                shape_keys.key_blocks[core.SHAPEKEY_NAME].value = 0.0

        from . import gpu_overlay as proj_overlay
        proj_overlay.invalidate_boundary()
        from . import selection_overlay
        selection_overlay.invalidate_selection()

        if result.island_jumped > 0:
            self.report(
                {"WARNING"},
                f"Applied {result.projected}/{result.total} — "
                f"{result.island_jumped} vertex/vertices flagged as possible "
                f"UV-island mismatches (auto-repaired where 2D neighbours agreed).",
            )
        else:
            self.report(
                {"INFO"},
                f"Applied 3D edits to 2D: {result.projected}/{result.total} "
                f"(failed {result.failed}).",
            )
        return {"FINISHED"}


class AC9_OT_ReverseProjection(bpy.types.Operator, _ModeSwitchMixin):
    bl_idname = "ac9_cloth.reverse_projection"
    bl_label = "Sync 3D > 2D"
    bl_description = (
        "Take the retopo's current 3D ShapeKey state, snap each vertex to "
        "the nearest point on Guide 3D (Basis), and rewrite the 2D Basis "
        "layout to match. Both Basis and ShapeKey are updated so they stay "
        "consistent"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not uic.experimental_enabled(context):
            return False
        # Running the Sync round-trip from Edit Mode forces an Edit→Object→Edit
        # mode switch around a heavy mesh-data rewrite, which crashes Blender on
        # large guides. Require Object Mode so the edit-mesh is never torn down
        # mid-operation.
        if context.mode != "OBJECT":
            cls.poll_message_set("Exit Edit Mode first (Tab) — Sync runs in Object Mode.")
            return False
        return True

    def execute(self, context):
        top = context.scene.ac9_cloth_retopo
        props = top.proj
        retopo = top.retopo_obj
        guide_obj = top.guide_obj
        flat_sk = top.guide_flat_shapekey

        if retopo is None or retopo.type != "MESH":
            self.report({"ERROR"}, "Retopo must be a mesh object.")
            return {"CANCELLED"}

        wm = context.window_manager
        with uic.ProgressScope(wm) as prog:
            def go():
                return core.run_reverse_projection(
                    context, retopo, guide_obj, flat_sk,
                    progress=uic.ProgressThrottle(wm, lo=0, hi=95),
                )

            result = self._run_in_object_mode(context, go)
            prog.update(1.0)

        if not result.success:
            self.report({"ERROR"}, result.error or "Reverse projection failed.")
            return {"CANCELLED"}

        # Switch back to Basis so user sees the updated 2D layout.
        shape_keys = retopo.data.shape_keys
        if shape_keys is not None:
            retopo.active_shape_key_index = 0
            if core.SHAPEKEY_NAME in shape_keys.key_blocks:
                shape_keys.key_blocks[core.SHAPEKEY_NAME].value = 0.0

        # Auto-set Guide flat SK to 1.0 → viewport shows 2D (flat) form to
        # match the retopo that just returned to Basis/2D space.
        if guide_obj and flat_sk:
            sk_data = getattr(guide_obj.data, "shape_keys", None)
            if sk_data and flat_sk in sk_data.key_blocks:
                sk_data.key_blocks[flat_sk].value = 1.0

        # Update island overlay.
        from . import gpu_overlay as proj_overlay
        proj_overlay.set_jumped_indices(result.island_jumped_indices)
        proj_overlay.invalidate()
        if result.island_jumped > 0:
            context.scene["ac9_cloth_retopo_island_jumped"] = result.island_jumped
        elif "ac9_cloth_retopo_island_jumped" in context.scene:
            del context.scene["ac9_cloth_retopo_island_jumped"]

        if result.island_jumped > 0:
            self.report(
                {"WARNING"},
                f"Synced {result.projected}/{result.total} — "
                f"{result.island_jumped} vertex/vertices flagged as possible "
                f"UV-island mismatches (auto-repaired where 2D neighbours agreed).",
            )
        else:
            self.report(
                {"INFO"},
                f"Synced: {result.projected} / {result.total}  "
                f"Failed: {result.failed}",
            )
        return {"FINISHED"}


class AC9_OT_BindNewVerts(bpy.types.Operator):
    bl_idname = "ac9_cloth.bind_new_verts"
    bl_label = "Bind New 3D Verts"
    bl_description = (
        "Bind vertices that were created in 3D Edit Mode (their stored "
        "attachment is missing or inconsistent with their 2D position) to the "
        "Guide surface, and repair their 2D Basis position. Uses the "
        "neighbouring verts' attachments to stay on the correct fold side of "
        "the fabric. Runs automatically on Edit Mode exit when 'Auto Bind New "
        "Verts' is on"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not uic.experimental_enabled(context):
            return False
        if context.mode != "OBJECT":
            cls.poll_message_set("Exit Edit Mode first (Tab).")
            return False
        return True

    def execute(self, context):
        top = context.scene.ac9_cloth_retopo
        retopo = top.retopo_obj
        guide_obj = top.guide_obj
        flat_sk = top.guide_flat_shapekey

        if retopo is None or retopo.type != "MESH":
            self.report({"ERROR"}, "Retopo must be a mesh object.")
            return {"CANCELLED"}

        result = core.run_bind_new_verts(context, retopo, guide_obj, flat_sk)

        if not result.success:
            self.report({"ERROR"}, result.error or "Bind failed.")
            return {"CANCELLED"}
        if result.new_verts_computed == 0:
            self.report({"INFO"}, "No unbound vertices found — nothing to do.")
            return {"FINISHED"}

        self.report(
            {"INFO"},
            f"Bound {result.projected}/{result.new_verts_computed} new vert(s) "
            f"to the Guide (2D Basis repaired).",
        )
        return {"FINISHED"}


class AC9_OT_AlignBoundaryToSeams(bpy.types.Operator, _ModeSwitchMixin):
    bl_idname = "ac9_cloth.align_boundary_to_seams"
    bl_label = "Align Boundary to Outline"
    bl_description = (
        "For each retopo vertex within 'Align Threshold' of the Guide's pattern "
        "outline (a UV seam edge) in 2D, snap its Basis (2D) position onto that "
        "edge and rebuild its attachment so one barycentric coordinate is "
        "exactly 0. Flags the vertex as boundary so the legacy 'Sync 3D > 2D' "
        "pins it perfectly to the outline. Run once after 'Sync 2D > 3D'"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .attachment import (
            align_boundary_to_seams,
            load_attachments_from_mesh,
            load_boundary_from_mesh,
            save_attachments_to_mesh,
            save_boundary_to_mesh,
        )
        from .guide import extract_points_world, set_basis_local, validate_guide

        top = context.scene.ac9_cloth_retopo
        props = top.proj
        retopo = top.retopo_obj
        guide_obj = top.guide_obj
        flat_sk = top.guide_flat_shapekey

        if retopo is None or retopo.type != "MESH":
            self.report({"ERROR"}, "Retopo must be a mesh object.")
            return {"CANCELLED"}

        err = validate_guide(guide_obj, flat_sk)
        if err is not None:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}

        def go():
            retopo_mesh = retopo.data
            n = len(retopo_mesh.vertices)
            if n == 0:
                return 0, 0
            retopo_inv = retopo.matrix_world.inverted()

            cached = core._get_guide_cache(guide_obj, flat_sk)
            tris_2d   = cached["tris_2d"]
            seam_mask = cached["seam_mask"]

            retopo_points_world  = extract_points_world(retopo)
            existing_attachments = load_attachments_from_mesh(retopo_mesh)
            existing_boundary    = load_boundary_from_mesh(retopo_mesh)

            new_points_world, new_attachments, new_boundary, aligned = (
                align_boundary_to_seams(
                    retopo_points_world,
                    existing_attachments,
                    existing_boundary,
                    tris_2d,
                    seam_mask,
                    props.align_boundary_threshold,
                )
            )

            if not aligned:
                return 0, n

            # Write 2D Basis only — caller's intent is that the 3D form
            # (RetopoPlanes + Seam connect) is already correct; only the 2D
            # layout needs to land exactly on the CLO pattern outline.
            new_local_2d = [retopo_inv @ p for p in new_points_world]
            set_basis_local(retopo, new_local_2d)

            save_attachments_to_mesh(retopo_mesh, new_attachments)
            save_boundary_to_mesh(retopo_mesh, new_boundary)
            return len(aligned), n

        aligned_count, total = self._run_in_object_mode(context, go)

        from . import gpu_overlay
        gpu_overlay.invalidate_boundary()

        if aligned_count == 0:
            self.report(
                {"INFO"},
                f"No retopo vertices within {props.align_boundary_threshold:.4f} m "
                f"of any Guide seam edge.",
            )
        else:
            self.report(
                {"INFO"},
                f"Aligned {aligned_count}/{total} vertex/vertices to Guide UV seams "
                f"(threshold {props.align_boundary_threshold:.4f} m).",
            )
        return {"FINISHED"}


class AC9_OT_InvalidateGuideCache(bpy.types.Operator):
    bl_idname = "ac9_cloth.invalidate_guide_cache"
    bl_label  = "Clear Guide Cache"
    bl_description = (
        "Discard the cached triangle lists and 2D BVH for the current Guide "
        "mesh.  The cache is rebuilt automatically on the next Sync operation.  "
        "Run this after editing Guide mesh geometry (vertex positions) without "
        "replacing the data-block — e.g. after sculpting or vertex-level edits "
        "on the CLO export"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        top = context.scene.ac9_cloth_retopo
        guide_obj = top.guide_obj
        core.invalidate_guide_cache(guide_obj)
        # The seam-line and boundary overlays keep their own caches, and used
        # to be marked dirty only by the legacy Sync operators. Re-flattening
        # a Guide and clearing its cache therefore left the overlay drawing
        # the OLD seam lines with no way to refresh them short of a Sync.
        from . import gpu_overlay as proj_overlay
        proj_overlay.invalidate()
        proj_overlay.invalidate_boundary()
        name = guide_obj.name if guide_obj else "all"
        self.report({"INFO"}, f"Guide cache and overlays cleared ({name}).")
        return {"FINISHED"}


class AC9_OT_ClearAttachments(bpy.types.Operator):
    bl_idname = "ac9_cloth.clear_attachments"
    bl_label = "Clear Stored Attachments"
    bl_description = (
        "Remove the per-vertex attachment data (ac9_tri_idx / ac9_bary_u / "
        "ac9_bary_v / ac9_status) from the retopo. Useful before rebinding "
        "to a different Guide"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .attachment import clear_attachments_on_mesh

        top = context.scene.ac9_cloth_retopo
        retopo = top.retopo_obj
        if retopo is None or retopo.type != "MESH":
            self.report({"ERROR"}, "Retopo must be a mesh object.")
            return {"CANCELLED"}
        clear_attachments_on_mesh(retopo.data)
        self.report({"INFO"}, "Cleared stored attachments on retopo.")
        return {"FINISHED"}


class AC9_OT_BakeIslandColors(bpy.types.Operator):
    bl_idname = "ac9_cloth.bake_island_colors"
    bl_label  = "Bake Island Colors"
    bl_description = (
        "Detect UV islands on the Guide mesh and write per-loop colours to a "
        "Color Attribute ('" + _ISLAND_COLOR_ATTR + "'). Also creates a simple "
        "material ('" + _ISLAND_MAT_NAME + "') that displays these colours. "
        "Switch to Material Preview — or Solid > Color > Material — to view. "
        "Re-run whenever you edit seam edges on the Guide"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .islands import detect_triangle_islands

        top       = context.scene.ac9_cloth_retopo
        guide_obj = top.guide_obj

        if guide_obj is None or guide_obj.type != "MESH":
            self.report({"ERROR"}, "Guide Object must be set to a mesh.")
            return {"CANCELLED"}

        mesh = guide_obj.data
        if not mesh.polygons:
            self.report({"WARNING"}, "Guide mesh has no polygons.")
            return {"CANCELLED"}

        wm = context.window_manager
        with uic.ProgressScope(wm) as prog:
            island_ids = detect_triangle_islands(guide_obj)
            if not island_ids:
                self.report({"WARNING"}, "No UV islands detected (add seam edges first).")
                return {"CANCELLED"}

            prog.update(0.5)

            n_islands = max(island_ids) + 1

            # ── Generate one distinct colour per island ───────────────────────
            colors_rgb = []
            for i in range(n_islands):
                h = i / n_islands
                s = 0.55 + 0.25 * (i % 2)   # alternate saturation for contrast
                r, g, b = colorsys.hsv_to_rgb(h, s, 0.90)
                colors_rgb.append((r, g, b))

            # ── Create / replace Color Attribute (CORNER = per loop) ─────────
            # CORNER domain gives sharp face boundaries: each face-vertex owns its
            # own colour so adjacent polygons from different islands don't bleed.
            if _ISLAND_COLOR_ATTR in mesh.color_attributes:
                mesh.color_attributes.remove(mesh.color_attributes[_ISLAND_COLOR_ATTR])

            color_attr = mesh.color_attributes.new(
                name=_ISLAND_COLOR_ATTR,
                type="BYTE_COLOR",
                domain="CORNER",
            )

            n_polys = len(mesh.polygons)
            tick = uic.ProgressThrottle(wm, lo=50, hi=95)
            for i, poly in enumerate(mesh.polygons):
                iid = island_ids[poly.index] if poly.index < len(island_ids) else -1
                r, g, b = colors_rgb[iid] if 0 <= iid < len(colors_rgb) else (0.5, 0.5, 0.5)
                for loop_idx in poly.loop_indices:
                    color_attr.data[loop_idx].color = (r, g, b, 1.0)
                tick((i + 1) / n_polys)

            # Set as active → "Solid > Color > Vertex" shows it without a material
            mesh.color_attributes.active_color = color_attr

            # ── Create / update material ──────────────────────────────────────
            alpha = top.proj.island_bake_alpha

            mat = bpy.data.materials.get(_ISLAND_MAT_NAME)
            if mat is None:
                mat = bpy.data.materials.new(name=_ISLAND_MAT_NAME)

            mat.use_nodes = True
            # Alpha blend so transparency actually renders in Material Preview.
            mat.blend_method = "BLEND"
            # Material.shadow_method is gone as of Blender 4.2 (EEVEE Next) —
            # setting it raised AttributeError here and killed the bake.
            if hasattr(mat, "shadow_method"):
                mat.shadow_method = "NONE"

            nodes = mat.node_tree.nodes
            links = mat.node_tree.links
            nodes.clear()

            out_node  = nodes.new("ShaderNodeOutputMaterial")
            bsdf_node = nodes.new("ShaderNodeBsdfPrincipled")
            # ShaderNodeAttribute reads any named mesh attribute by name.
            attr_node = nodes.new("ShaderNodeAttribute")
            attr_node.attribute_name = _ISLAND_COLOR_ATTR
            attr_node.attribute_type = "GEOMETRY"

            links.new(attr_node.outputs["Color"], bsdf_node.inputs["Base Color"])
            links.new(bsdf_node.outputs["BSDF"],  out_node.inputs["Surface"])

            # Alpha: set as a constant on the BSDF input (no extra node needed).
            bsdf_node.inputs["Alpha"].default_value = alpha

            out_node.location  = (400, 0)
            bsdf_node.location = (100, 0)
            attr_node.location = (-250, 0)

            # Assign to slot 0, preserving any extra slots.
            if not mesh.materials:
                mesh.materials.append(mat)
            else:
                mesh.materials[0] = mat

            alpha_pct = int(round(alpha * 100))
            self.report(
                {"INFO"},
                f"Baked {n_islands} island colour(s)  alpha={alpha_pct}%. "
                "Switch to Material Preview to view.",
            )
            return {"FINISHED"}


class AC9_OT_ClearIslandColors(bpy.types.Operator):
    bl_idname = "ac9_cloth.clear_island_colors"
    bl_label  = "Clear Island Colors"
    bl_description = (
        "Remove the '" + _ISLAND_COLOR_ATTR + "' Color Attribute and the "
        "'" + _ISLAND_MAT_NAME + "' material slot from the Guide mesh"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        top       = context.scene.ac9_cloth_retopo
        guide_obj = top.guide_obj

        if guide_obj is None or guide_obj.type != "MESH":
            self.report({"ERROR"}, "Guide Object must be set to a mesh.")
            return {"CANCELLED"}

        mesh    = guide_obj.data
        changed = False

        if _ISLAND_COLOR_ATTR in mesh.color_attributes:
            mesh.color_attributes.remove(mesh.color_attributes[_ISLAND_COLOR_ATTR])
            changed = True

        for i, m in enumerate(mesh.materials):
            if m is not None and m.name == _ISLAND_MAT_NAME:
                mesh.materials.pop(index=i)
                changed = True
                break

        msg = "Cleared island colors from Guide." if changed else "Nothing to clear."
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class AC9_OT_SubdivideRetopo(bpy.types.Operator):
    bl_idname = "ac9_cloth.subdivide_retopo"
    bl_label = "Subdivide Retopo"
    bl_description = (
        "Subdivide the retopo in 2D (simple/linear), snap new boundary verts to "
        "the Guide seam lines, then re-project to 3D. Because the new verts are "
        "projected onto the Guide surface, the result follows the garment shape "
        "— no smoothing needed. Destructive: bumps resolution permanently. "
        "Runs in the 2D state only — press 'Sync 3D > 2D' first if you are in 3D"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if context.mode != "OBJECT":
            cls.poll_message_set("Exit Edit Mode first (Tab).")
            return False
        # 2D state only.  Subdividing while the AC9_3D_Project ShapeKey is the
        # displayed state destroyed layouts in interactive sessions in ways we
        # could never reproduce in scripted runs — the safe, sufficient rule is
        # to subdivide the 2D layout and re-project.
        top = getattr(context.scene, "ac9_cloth_retopo", None)
        retopo = top.retopo_obj if top is not None else None
        sk = retopo.data.shape_keys if (retopo is not None and retopo.type == "MESH") else None
        if (
            sk is not None
            and core.SHAPEKEY_NAME in sk.key_blocks
            and sk.key_blocks[core.SHAPEKEY_NAME].value > 0.5
        ):
            cls.poll_message_set(
                "Subdivide runs in the 2D state only — press 'Sync 3D > 2D' first."
            )
            return False
        return True

    levels: bpy.props.IntProperty(
        name="Levels",
        description="How many times to subdivide (each level doubles edge resolution)",
        default=1, min=1, max=4,
    )
    snap_boundary: bpy.props.BoolProperty(
        name="Snap New Boundary to Seam",
        description=(
            "Move new boundary verts onto the nearest Guide seam line so the CLO "
            "outline stays crisp on curves. Only midpoints born on an edge whose "
            "both ends already sit on the seam are snapped — boundaries without "
            "a seam of their own (e.g. an island's symmetry centre-line) are "
            "never touched. Requires 'Analyze Seams' to have run"
        ),
        default=True,
    )
    snap_distance: bpy.props.FloatProperty(
        name="Boundary Snap Distance",
        description="Max distance a new boundary vert may move to reach a seam line, in the cloth's real dimensions (converted into the flat layout)",
        default=0.02, min=0.0, soft_max=0.1,
        unit='LENGTH', precision=4,
    )

    def execute(self, context):
        top = context.scene.ac9_cloth_retopo
        retopo = top.retopo_obj
        guide_obj = top.guide_obj
        flat_sk = top.guide_flat_shapekey

        from .guide import validate_guide
        if retopo is None or retopo.type != "MESH":
            self.report({"ERROR"}, "Retopo must be a mesh object.")
            return {"CANCELLED"}
        err = validate_guide(guide_obj, flat_sk)
        if err is not None:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}

        # Remember context so we can restore it.
        prev_active = context.view_layer.objects.active
        prev_mode = context.object.mode if context.object is not None else "OBJECT"
        prev_selected = [o for o in context.view_layer.objects if o.select_get()]

        wm = context.window_manager
        # Entered by hand rather than with a `with`, so the progress bar closes
        # inside the finally below, after the mode restore — same reason
        # _suppress_ctx just below is driven this way.
        _progress_ctx = uic.ProgressScope(wm)
        _progress_ctx.__enter__()

        from . import handlers
        # Suppress the Edit-exit auto-bind for the whole operator: the
        # subdivide round-trip below would otherwise schedule a redundant
        # bind pass on top of the full re-projection this operator does.
        _suppress_ctx = handlers.suppressed()
        _suppress_ctx.__enter__()
        try:
            if context.object is not None and context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            for o in context.view_layer.objects:
                o.select_set(False)
            context.view_layer.objects.active = retopo
            retopo.select_set(True)

            n_old = len(retopo.data.vertices)

            # Record which boundary edges already hug a seam BEFORE subdividing.
            # Only midpoints born on those edges may snap afterwards — snapping
            # is meant to keep an outline that IS on the seam crisp on curves,
            # not to drag unrelated boundaries (e.g. an island's symmetry
            # centre-line, which has no seam of its own but can pass within the
            # snap distance of someone else's outline segment).
            parent_segments = []
            if self.snap_boundary:
                parent_segments = self._collect_snappable_parent_edges(retopo)

            # ── Subdivide the 2D base mesh (simple = linear, no smoothing) ──
            # Edit-mode subdivide interpolates all shape keys; we re-project the
            # 3D ShapeKey afterwards so it conforms to the surface anyway.
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            for _ in range(self.levels):
                bpy.ops.mesh.subdivide(number_cuts=1, smoothness=0.0)
            bpy.ops.object.mode_set(mode="OBJECT")
            wm.progress_update(15)

            n_new = len(retopo.data.vertices)

            # ── Snap new boundary verts onto the seam lines (Basis/2D space) ──
            moved = set()
            if self.snap_boundary and parent_segments:
                moved = self._snap_boundary_to_seam(retopo, n_old, parent_segments)
            snapped = len(moved)
            wm.progress_update(25)

            # ── Re-project to 3D, computing attachments for ONLY the verts that
            # actually changed — new subdivision verts (index >= n_old) plus any
            # boundary vert moved by the snap. Original, unmoved verts keep their
            # attachments (subdivide preserves original vertex data), so we skip
            # an expensive full BVH re-bind. Falls back to full if the stored
            # attachment count doesn't line up.
            incremental_ok = self._mark_dirty_for_incremental(retopo, n_old, n_new, moved)
            result = core.run_forward_projection(
                context, retopo, guide_obj, flat_sk,
                overwrite_shapekey=True, clear_failed_group=True,
                select_failed=False, incremental=incremental_ok,
                progress=uic.ProgressThrottle(wm, lo=25, hi=80),
            )
            wm.progress_update(80)
        finally:
            _suppress_ctx.__exit__(None, None, None)
            if context.object is not None and context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            for o in context.view_layer.objects:
                o.select_set(False)
            for o in prev_selected:
                try:
                    o.select_set(True)
                except Exception:
                    pass
            context.view_layer.objects.active = prev_active
            if prev_active is not None and prev_mode != "OBJECT":
                try:
                    bpy.ops.object.mode_set(mode=prev_mode)
                except RuntimeError:
                    pass
            # Always closed here, even if an exception propagates out of the
            # try above (mode-restore still needs to run first) — the two
            # progress_update-then-report tails below no longer close the
            # progress bar themselves.
            _progress_ctx.__exit__(None, None, None)

        if not result.success:
            self.report({"ERROR"}, result.error or "Re-projection after subdivide failed.")
            return {"CANCELLED"}

        from . import gpu_overlay as proj_overlay
        proj_overlay.invalidate_boundary()

        n = len(retopo.data.vertices)
        snap_msg = f", snapped {snapped} boundary verts" if self.snap_boundary else ""
        msg = (
            f"Subdivided x{self.levels} → {n} verts{snap_msg}; "
            f"re-projected {result.projected}/{result.total}."
        )
        level = {"INFO"}

        # N-gons pass through untouched by design (see core.ngon_stats): an
        # unfinished panel must not stop the user from checking the silhouette
        # and the poly count of everything else. Say so, though — left silent
        # it reads as "the wireframe vanished here", i.e. as a bug.
        n_ngon, _largest_ngon = core.ngon_stats(retopo.data)
        msg += core.ngon_note(retopo.data)
        # The Faces panel keeps the last result line, so the count stays
        # visible after the status bar has moved on (last writer wins).
        ngon_short = f", {n_ngon} n-gon(s) left whole" if n_ngon else ""
        top.status_faces = f"Subdivided x{self.levels} → {n} verts{ngon_short}"

        # Keep the Mirror in sync with the new resolution — used to require a
        # separate manual 'Refresh Mirror' press after every Subdivide. Mirrors
        # AC9_OT_RefreshMirror's own Object-Mode path (refresh, keep the retopo
        # flat, invalidate overlays). Best-effort: a refresh failure downgrades
        # the report but must never turn a successful subdivide into CANCELLED.
        if mirror_mod.find_mirror(retopo) is not None:
            mirror_error = None
            try:
                # No progress callback here: this operator's own progress bar
                # already ran 0-100% and closed above (the scope's exit must
                # run before this, exception-safe, so it lives in that
                # finally).
                mirror_result, _mirror_obj = mirror_mod.refresh_mirror(
                    context, retopo, guide_obj, flat_sk,
                )
                if not mirror_result.success:
                    mirror_error = mirror_result.error or "Mirror refresh failed."
            except Exception as exc:  # noqa: BLE001
                mirror_error = str(exc)

            if mirror_error is not None:
                level = {"WARNING"}
                msg += f"  Mirror refresh failed: {mirror_error}"
            else:
                # Keep the retopo displayed flat (2D): the mirror carries the
                # 3D view, same as AC9_OT_RefreshMirror.
                shape_keys = retopo.data.shape_keys
                if shape_keys is not None:
                    retopo.active_shape_key_index = 0
                    if core.SHAPEKEY_NAME in shape_keys.key_blocks:
                        shape_keys.key_blocks[core.SHAPEKEY_NAME].value = 0.0

                proj_overlay.invalidate_boundary()
                from . import selection_overlay
                selection_overlay.invalidate_selection()
                try:
                    from ..uv_seam_guide.gpu_overlay import rebuild_ghosts, _tag_redraw_3d
                    rebuild_ghosts(top.seam, retopo)
                    _tag_redraw_3d(context)
                except Exception:
                    pass

        self.report(level, msg)
        return {"FINISHED"}

    def _mark_dirty_for_incremental(self, retopo, n_old, n_new, moved):
        """Mark new + moved verts as 'needs compute' so the re-projection only
        re-binds those (incremental). Returns True if incremental is viable.

        Original verts keep their stored attachment (subdivide preserves
        original vertex data); only verts whose 2D position is new/changed need
        a fresh BVH bind. If the stored attachment array length doesn't match
        the new vert count, we bail to a full recompute.
        """
        from .attachment import load_attachments_from_mesh, save_attachments_to_mesh, Attachment

        atts = load_attachments_from_mesh(retopo.data)
        if len(atts) != n_new:
            return False  # caller falls back to incremental=False (full)

        dirty = set(range(n_old, n_new)) | set(moved)
        for i in dirty:
            atts[i] = Attachment.none()
        save_attachments_to_mesh(retopo.data, atts)
        return True

    def _collect_snappable_parent_edges(self, retopo):
        """Boundary edges that RUN ALONG the guide outline, as world-space 2D
        segments (a, b).  Captured before subdividing: midpoints born on these
        edges follow the seam curve; midpoints on any other boundary must not
        snap at all.

        "Runs along" is decided by sampling the edge interior (1/4, 1/2, 3/4)
        and requiring every sample within snap_distance × 0.25 of the outline.
        Endpoint distance alone cannot tell an outline-hugging edge from an
        edge CROSSING a thin island (its endpoints sit on outline corners but
        its interior spans the island — e.g. a symmetry centre-line on a thin
        band; the old behaviour dragged those midpoints sideways onto the
        nearest outline, collapsing the column).  An on-curve edge's chord
        deviates from the outline by its sagitta — sub-millimetre for typical
        pattern curvature — so the tight interior tolerance keeps real outline
        edges while rejecting crossings on any island thicker than half the
        snap distance.
        """
        import bmesh
        from ..uv_seam_guide.gpu_overlay import _cache as seam_cache
        from ..uv_seam_guide.ghost import nearest_segment_point, build_segment_kd
        from .guide import get_basis_local, scene_flat_scale

        # Snap to the FULL guide outline (sewn seams + free edges), not just sewn
        # seam pairs — otherwise new verts on hems / openings have no target.
        segments = seam_cache.get("boundary_segments")
        if not segments:
            self.report({"WARNING"}, "No boundary segments cached — run 'Analyze Seams' first. Boundary snap skipped.")
            return []
        kd = seam_cache.get("boundary_kd")
        if kd is None:
            kd = build_segment_kd(segments)

        mesh = retopo.data
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bm.edges.ensure_lookup_table()
        boundary_edges = [
            (e.verts[0].index, e.verts[1].index) for e in bm.edges if e.is_boundary
        ]
        bm.free()

        basis = get_basis_local(retopo)
        mw = retopo.matrix_world
        # Boundary Snap Distance is real fabric mm; everything here is measured
        # in the flat layout, so convert once (memoised). Unconverted, the
        # default 20 mm meant 60 mm of fabric on a tightly packed layout and
        # this test kept edges that cross a thin island (AUDIT 8-B).
        tight = self.snap_distance * scene_flat_scale() * 0.25

        def _hugs_outline(pa, pb):
            for f in (0.25, 0.5, 0.75):
                s = pa.lerp(pb, f)
                _pt, d = nearest_segment_point(s, segments, kd=kd)
                if d is None or d > tight:
                    return False
            return True

        out = []
        for i, j in boundary_edges:
            pa, pb = mw @ basis[i], mw @ basis[j]
            if _hugs_outline(pa, pb):
                out.append((pa, pb))
        return out

    def _snap_boundary_to_seam(self, retopo, n_old, parent_segments):
        """Snap NEW boundary verts that were born on a seam-hugging parent edge
        onto the nearest guide outline segment.

        A linear subdivide places every new vert exactly on its parent edge, so
        membership is a simple point-on-segment test against the world-space
        segments recorded before subdividing.  Pre-existing verts and new verts
        on non-seam boundaries are never moved.
        """
        import bmesh
        from mathutils import Vector
        from ..uv_seam_guide.gpu_overlay import _cache as seam_cache
        from ..uv_seam_guide.ghost import nearest_segment_point, build_segment_kd
        from .guide import get_basis_local, set_basis_local, scene_flat_scale

        segments = seam_cache.get("boundary_segments")
        if not segments:
            return set()
        kd = seam_cache.get("boundary_kd")
        if kd is None:
            kd = build_segment_kd(segments)

        mesh = retopo.data
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bm.edges.ensure_lookup_table()
        boundary = set()
        for e in bm.edges:
            if e.is_boundary:
                boundary.add(e.verts[0].index)
                boundary.add(e.verts[1].index)
        bm.free()

        def _on_parent_segment(p, eps=1e-5):
            """Is world-space point p on one of the recorded parent edges (XY)?"""
            for a, b in parent_segments:
                ab_x, ab_y = b.x - a.x, b.y - a.y
                L2 = ab_x * ab_x + ab_y * ab_y
                if L2 < 1e-20:
                    continue
                t = ((p.x - a.x) * ab_x + (p.y - a.y) * ab_y) / L2
                if t < -eps or t > 1.0 + eps:
                    continue
                cx, cy = a.x + t * ab_x, a.y + t * ab_y
                if (p.x - cx) ** 2 + (p.y - cy) ** 2 <= eps * eps:
                    return True
            return False

        basis = get_basis_local(retopo)
        mw = retopo.matrix_world
        inv = mw.inverted()
        # Real fabric mm -> flat layout, hoisted out of the per-vertex loop.
        snap = self.snap_distance * scene_flat_scale()
        moved = set()
        for i in boundary:
            if i < n_old:
                continue  # only verts created by this subdivide
            world = mw @ basis[i]
            if not _on_parent_segment(world):
                continue
            pt, d = nearest_segment_point(world, segments, kd=kd)
            if pt is not None and d <= snap:
                lp = inv @ Vector((pt.x, pt.y, world.z))
                basis[i] = lp
                moved.add(i)
        set_basis_local(retopo, basis)
        return moved


def _find_layer_col(root_lc, col_name):
    """Recursive search: return the LayerCollection whose .collection.name
    matches col_name, or None."""
    if root_lc.collection.name == col_name:
        return root_lc
    for child in root_lc.children:
        found = _find_layer_col(child, col_name)
        if found is not None:
            return found
    return None


def _unhide_parent_collections(context, obj):
    """Unhide every LayerCollection ancestor of *obj* that is hidden via the
    Outliner's per-view-layer hide toggle, or excluded from the view layer.

    Guide / Mirror live wherever the Retopo's own collections are, which are
    not always the collection the user is actively working in — this keeps a
    View switch that is supposed to show the object from being silently
    defeated by an unrelated collection-level hide. Both `exclude` and
    `hide_viewport` are checked: `hide_get()`/`hide_set()` are per-View-Layer,
    same as these, and neither one takes effect on an ancestor collection that
    is excluded from the view layer (the object is not even evaluated in it)
    or monitor-hidden. Unhide-only: it never remembers or restores a prior
    hidden state (that save/restore pattern — scene keys like the old
    `_ac9_guide3d_hidden_cols` — is what produced the stale Mirror-visibility
    bug this module's redesign replaces).
    """
    for col in obj.users_collection:
        lc = _find_layer_col(context.view_layer.layer_collection, col.name)
        if lc is None:
            continue
        if lc.exclude:
            lc.exclude = False
        if lc.hide_viewport:
            lc.hide_viewport = False


def set_if_changed(owner, attr, value):
    """Write owner.attr only when it would actually change. Returns True if written.

    Every write in this module goes through here on purpose. The Guide is a
    six-figure-vertex garment mesh; setting its Flat SK value tags the
    depsgraph and throws away the viewport's GPU batches for it even when the
    new value equals the old one, so a redundant write is not free — it is a
    full re-upload of the Guide on every press.
    """
    if getattr(owner, attr) != value:
        setattr(owner, attr, value)
        return True
    return False


def set_hide_if_changed(obj, value):
    """Like set_if_changed, but for the eye icon (Object.hide_get() /
    hide_set(), per-View-Layer) rather than an RNA property — hide_get/set are
    methods, not an attribute getattr/setattr can touch. view_layer is left at
    its default (None), which resolves to bpy.context.view_layer and works
    the same in headless (-b) runs.

    The monitor icon (`hide_viewport`) is deliberately left alone: a user may
    disable an object in viewports on purpose (it is cheaper than hiding),
    and that choice must survive View switches and Refresh. If a Mirror or
    Guide stays invisible after Show, check its monitor icon in the Outliner.

    hide_set() needs *obj* to have a Base in the target view layer — it
    raises RuntimeError if the object is not linked into any collection the
    view layer can see (an orphaned Guide/Mirror datablock, or one whose
    collection was unlinked from the scene entirely, which — unlike a merely
    excluded/monitor-hidden collection — no LayerCollection walk can fix).
    hide_viewport never had that requirement. Caught and ignored: hide_get()
    already reads such an object as visible (there is no Base to flag as
    hidden on), so there is nothing this call could have changed anyway.
    """
    changed = False
    if obj.hide_get() != value:
        try:
            obj.hide_set(value)
            changed = True
        except RuntimeError:
            pass
    return changed


# ── View state: Mirror / Guide / Both ─────────────────────────────────────
# The one row that decides what is visible. Three exclusive states, named
# after what you end up looking at:
#
#   MIRROR  the retopo's 3D form alone   (Guide hidden, laid out flat)
#   GUIDE   the garment alone            (Mirror hidden)
#   BOTH    the retopo on the garment
#
# Plain per-View-Layer visibility (the eye icon), so it means the same thing
# in every viewport and in the Outliner — which is also the escape hatch:
# anything these three do can be undone by hand there, and nothing here is a
# hidden per-viewport mode the next person has to be told about.
#
# Nothing else writes visibility. Refresh Mirror is geometry only; it used to
# write this too, and a separate Show toggle sat in between getting
# overwritten by both, which is why the old panel never stayed where it was
# put.
VIEW_MIRROR = 'VIEW_MIRROR'
VIEW_GUIDE = 'VIEW_GUIDE'
VIEW_BOTH = 'VIEW_BOTH'


def _flat_keyblock(top):
    guide = top.guide_obj
    sk = guide.data.shape_keys if (guide is not None and guide.type == 'MESH') else None
    return sk.key_blocks.get(top.guide_flat_shapekey) if sk is not None else None


def current_view_state(top):
    """MIRROR / GUIDE / BOTH, or None — read back from the two eye icons.

    Visibility is the whole state, so a hide done by hand in the Outliner
    reads back here honestly rather than leaving a button lit that no longer
    describes the screen. Both hidden belongs to no state and lights nothing.
    """
    from . import mirror as mirror_mod

    guide = top.guide_obj
    if guide is None:
        return None
    mirror = mirror_mod.find_mirror(top.retopo_obj) if top.retopo_obj is not None else None
    guide_on = not guide.hide_get()
    mirror_on = mirror is not None and not mirror.hide_get()
    if guide_on and mirror_on:
        return VIEW_BOTH
    if guide_on:
        return VIEW_GUIDE
    if mirror_on:
        return VIEW_MIRROR
    return None


def _apply_view_state(context, top, state):
    """Move the scene to *state*. Writes only what actually changes.

    The Guide's Flat SK follows the state, because the two shapes are what
    the states are for: GUIDE and BOTH want the garment (Flat SK 0), MIRROR
    is the 2D working state and wants the flat layout (Flat SK 1) — so
    un-hiding the Guide by hand there gives back the flat backdrop the retopo
    is snapped against, not the garment.

    Never creates the Mirror; nothing here needs one.
    """
    from . import mirror as mirror_mod

    guide = top.guide_obj
    flat_sk = top.guide_flat_shapekey
    flat_kb = guide.data.shape_keys.key_blocks[flat_sk]
    mirror = mirror_mod.find_mirror(top.retopo_obj) if top.retopo_obj is not None else None

    show_guide = state in {VIEW_GUIDE, VIEW_BOTH}
    if show_guide:
        # Only worth un-hiding an ancestor collection when the Guide is meant
        # to be seen; doing it while hiding would fight the user's own choice.
        _unhide_parent_collections(context, guide)
    set_if_changed(flat_kb, "value", 0.0 if show_guide else 1.0)
    set_hide_if_changed(guide, not show_guide)
    if mirror is not None:
        set_hide_if_changed(mirror, state == VIEW_GUIDE)

    # AC9_Separated (guide_separate) is shown only in 3D and only while it is
    # the selected 3D Source; never on top of the flat.
    from ..guide_separate import core as _sep
    _sep.sync_separated_value(guide, flat_sk, top.guide_3d_source == 'SEPARATED')

    tag_redraw_3d(context)


def tag_redraw_3d(context):
    """Redraw every 3D Viewport of the current screen."""
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


class AC9_OT_SetViewState(bpy.types.Operator):
    bl_idname = "ac9_cloth.set_view_state"
    bl_label = "Set View State"
    bl_description = (
        "What is visible. Mirror: the retopo's 3D form alone, with the Guide "
        "hidden and laid out flat — the 2D working state. Guide: the garment "
        "alone. Both: the retopo on top of the garment. Plain visibility, so "
        "the Outliner undoes any of it; Refresh never changes this"
    )
    bl_options = {"REGISTER", "UNDO"}

    state: bpy.props.EnumProperty(
        name="State",
        items=(
            (VIEW_MIRROR, "Mirror",
             "The Mirror alone: the Guide is hidden and laid out flat "
             "(Flat SK 1), which is the state the 2D retopo is edited in"),
            (VIEW_GUIDE, "Guide",
             "The Guide's 3D garment shape alone (Flat SK 0); the Mirror, "
             "which occupies the same space, is hidden"),
            (VIEW_BOTH, "Both",
             "The Mirror on top of the Guide's 3D garment shape"),
        ),
        default=VIEW_MIRROR,
    )

    @classmethod
    def poll(cls, context):
        top = getattr(getattr(context, "scene", None), "ac9_cloth_retopo", None)
        if top is None:
            return False
        if top.guide_obj is None or not top.guide_flat_shapekey:
            cls.poll_message_set("Set the Guide and Flat SK first.")
            return False
        sk = top.guide_obj.data.shape_keys
        if sk is None or top.guide_flat_shapekey not in sk.key_blocks:
            cls.poll_message_set(f"Flat SK '{top.guide_flat_shapekey}' not found on Guide.")
            return False
        return True

    def execute(self, context):
        _apply_view_state(context, context.scene.ac9_cloth_retopo, self.state)
        return {"FINISHED"}


def _view3d_spaces(context):
    """The SpaceView3D of every 3D Viewport area on the current screen."""
    screen = getattr(context, "screen", None)
    if screen is None:
        return []
    spaces = []
    for area in screen.areas:
        if area.type != 'VIEW_3D':
            continue
        space = area.spaces.active
        if space is not None and getattr(space, "overlay", None) is not None:
            spaces.append(space)
    return spaces


class AC9_OT_ToggleWireframe(bpy.types.Operator):
    bl_idname = "ac9_cloth.toggle_wireframe"
    bl_label = "Toggle Wireframe"
    bl_description = (
        "Turn the Wireframe overlay on or off in EVERY 3D Viewport at once. "
        "Retopo work is usually split across a 2D view and a 3D view, and "
        "wanting the wires in one of them but not the other is not a thing "
        "that happens — so both follow the viewport the button was pressed in"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not _view3d_spaces(context):
            cls.poll_message_set("Needs at least one 3D Viewport.")
            return False
        return True

    def execute(self, context):
        spaces = _view3d_spaces(context)
        if not spaces:
            self.report({"ERROR"}, "No 3D Viewport to toggle.")
            return {"CANCELLED"}
        # The viewport the press came from decides the new value; when the
        # press came from somewhere else (the sidebar of another editor, a
        # search) the first 3D Viewport stands in for it.
        active = context.space_data
        ref = active if (active is not None and getattr(active, "type", None) == 'VIEW_3D'
                         and getattr(active, "overlay", None) is not None) else spaces[0]
        value = not ref.overlay.show_wireframes
        for space in spaces:
            set_if_changed(space.overlay, "show_wireframes", value)
        tag_redraw_3d(context)
        return {"FINISHED"}

