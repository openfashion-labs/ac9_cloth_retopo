"""CLO Projector's share of the AC9 Cloth Retopo sidebar tab.

Owns the 3D View panel (the Mirror and the Guide's 2D/3D state) and exposes
draw functions the root panels compose:

  draw_faces_object_tools  -> Faces panel   (Subdivide)
  draw_guide_islands       -> Overlays panel (Island Colors on the Guide)
  draw_advanced            -> Advanced panel (legacy flip, options, maintenance)
"""

import bpy

from .. import ui_common as uic
from . import mirror as mirror_mod


class AC9_PT_MirrorView(bpy.types.Panel):
    """The Mirror — the read-only 3D view of the 2D retopo — and the Guide's
    2D/3D toggle. Refresh Mirror also sits in the viewport header."""
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "AC9 Cloth Retopo"
    bl_label       = "3D View"
    bl_order       = 4

    def draw(self, context):
        layout = self.layout
        top = context.scene.ac9_cloth_retopo
        props = top.proj

        if not uic.guide_ready(layout, top):
            return
        if not uic.retopo_ready(layout, top):
            return

        retopo = top.retopo_obj
        mirror = mirror_mod.find_mirror(retopo)

        col = layout.column(align=True)
        row = uic.labeled_row(col, "Mirror")
        row.scale_y = 1.3
        # Works in Object Mode and in the retopo's Edit Mode (live).
        row.operator("ac9_cloth.refresh_mirror", text="Refresh",
                     icon="FILE_REFRESH")
        row = uic.labeled_row(col, "")
        row.prop(props, "live_update", text="Auto-refresh",
                 icon="PLAY" if props.live_update else "PAUSE", toggle=True)
        if mirror is not None:
            # Hide/show rather than Remove: the Mirror sits at the world origin
            # and shows through the flat layout in a top view, so it is often
            # in the way during 2D work — but rebuilding it costs a Refresh.
            row = uic.labeled_row(col, "")
            row.prop(mirror, "hide_viewport", text="Show",
                     invert_checkbox=True, toggle=True,
                     icon='HIDE_OFF' if not mirror.hide_viewport else 'HIDE_ON')
            row.operator("ac9_cloth.remove_mirror", text="", icon="TRASH")

        # Guide 2D/3D, and (in 3D, with a Mirror) which of the two is shown.
        guide = top.guide_obj
        sk = guide.data.shape_keys
        flat_kb = sk.key_blocks.get(top.guide_flat_shapekey) if sk is not None else None
        guide_in_3d = flat_kb is not None and flat_kb.value < 0.5
        col = layout.column(align=True)
        row = uic.labeled_row(col, "Guide")
        row.scale_y = 1.2
        row.operator(
            "ac9_cloth.toggle_guide_3d_view",
            text="To 2D" if guide_in_3d else "To 3D",
            icon="HIDE_OFF" if guide_in_3d else "HIDE_ON",
            depress=guide_in_3d,
        )
        if guide_in_3d and mirror is not None:
            # Which of the two 3D objects is shown: the button names the
            # one you would switch TO.
            guide_vis = not guide.hide_viewport
            row.operator(
                "ac9_cloth.swap_guide_mirror",
                text="Mirror" if guide_vis else "Guide",
                icon="MOD_MIRROR" if guide_vis else "OBJECT_DATA",
            )
        # Self-contact separation of the Guide (guide_separate): it changes
        # which 3D shape everything above reads, so it lives with the Guide.
        if uic.experimental_enabled(context):
            from ..guide_separate import ui as sep_ui
            sep_ui.draw_guide_tools(col, context, top)
        uic.draw_status(layout, top.status_guide)

        # Snap the 2D boundary onto the Guide outline and flag it as boundary.
        col = layout.column(align=True)
        row = uic.labeled_row(col, "Align")
        row.operator("ac9_cloth.align_boundary_to_seams",
                     text="To Outline", icon="SNAP_EDGE")
        row = uic.labeled_row(col, "")
        row.prop(props, "align_boundary_threshold", text="Threshold")

        # The public "done" button: bake Retopo + Guide into a plain mesh.
        layout.separator()
        row = uic.labeled_row(layout, "Finalize")
        row.scale_y = 1.2
        row.operator("ac9_cloth.finalize_retopo", text="Finalize", icon='CHECKMARK')
        uic.draw_hint(layout, "New <Retopo>_Final: 3D shape, UV = 2D layout, no ShapeKeys")


def draw_faces_object_tools(layout, context, top):
    """Faces panel, Object Mode: real subdivision."""
    from ..uv_seam_guide.gpu_overlay import _cache as seam_cache

    row = uic.labeled_row(layout, "Subdivide")
    row.scale_y = 1.2
    row.operator("ac9_cloth.subdivide_retopo", text="Subdivide",
                 icon="MOD_SUBSURF")

    # Soft prerequisite: Subdivide still runs without the seam analysis, but
    # then it cannot snap the new boundary vertices onto the Guide outline.
    if not seam_cache["boundary_segments"]:
        uic.draw_hint(layout, "No seams: Subdivide skips the snap",
                      "ac9_cloth.analyze_uv_seam_pairs", "Analyze", 'UV_DATA')


def draw_guide_islands(layout, context, top):
    """Overlays panel: per-island colours baked onto the Guide as a material."""
    props = top.proj
    row = layout.row(align=True)
    row.prop(props, "island_bake_alpha", slider=True, text="Islands")
    row.operator("ac9_cloth.bake_island_colors", text="Bake", icon="VPAINT_HLT")
    row.operator("ac9_cloth.clear_island_colors", text="", icon="X")


def draw_advanced(layout, context):
    """Advanced panel: the legacy in-place flip workflow, its options, and
    cache maintenance. The recommended workflow is 2D edit + Refresh Mirror.
    """
    from . import core

    top = context.scene.ac9_cloth_retopo
    props = top.proj
    retopo = top.retopo_obj
    sk = retopo.data.shape_keys if (retopo and retopo.type == "MESH") else None
    has_3d = sk is not None and core.SHAPEKEY_NAME in sk.key_blocks
    in_3d = bool(has_3d and sk.key_blocks[core.SHAPEKEY_NAME].value > 0.5)
    in_edit = context.mode != "OBJECT"

    if uic.experimental_enabled(context):
        col = layout.column(align=True)
        col.label(text="Legacy Flip (retopo morphs 2D/3D itself)", icon='SHAPEKEY_DATA')
        row = uic.labeled_row(col, "Apply")
        row.enabled = not in_edit
        row.operator("ac9_cloth.sync_mirror_to_2d", text="3D → 2D",
                     icon="UV_SYNC_SELECT")
        row = uic.labeled_row(col, "Sync")
        sub = row.row(align=True)
        sub.enabled = (not in_edit) and (not in_3d)
        sub.operator("ac9_cloth.create_projection", text="2D > 3D",
                     icon="SHAPEKEY_DATA")
        sub = row.row(align=True)
        sub.enabled = (not in_edit) and in_3d
        sub.operator("ac9_cloth.reverse_projection", text="3D > 2D",
                     icon="UV_SYNC_SELECT")
        row = uic.labeled_row(col, "Bind")
        sub = row.row(align=True)
        sub.enabled = (not in_edit) and in_3d
        sub.operator("ac9_cloth.bind_new_verts", text="New Verts", icon="SNAP_ON")
        row.prop(props, "auto_bind_new_verts", text="Auto", toggle=True, icon="AUTO")
        jumped = context.scene.get("ac9_cloth_retopo_island_jumped")
        if jumped:
            uic.draw_blocker(col, f"{jumped} vertex/vertices jumped UV island(s)")

        layout.separator()
    col = layout.column(align=True)
    col.label(text="Legacy Flip Options", icon='PREFERENCES')
    col.prop(props, "overwrite_shapekey")
    col.prop(props, "clear_failed_group")
    col.prop(props, "select_failed")

    layout.separator()
    col = layout.column(align=True)
    col.label(text="Maintenance", icon='TOOL_SETTINGS')
    row = uic.labeled_row(col, "Guide Cache")
    row.operator("ac9_cloth.invalidate_guide_cache", text="Clear",
                 icon="FILE_REFRESH")
    row = uic.labeled_row(col, "Attachments")
    row.operator("ac9_cloth.clear_attachments", text="Clear", icon="TRASH")
