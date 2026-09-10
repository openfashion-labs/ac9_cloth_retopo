"""Guide Prep panel (formerly "Prepare") — the CLO export's own work phase,
turning it into the Guide before it is used as a bake source.

Rows are numbered in workflow order so the panel reads as the procedure:
1 Flat SK, 2 Folds, 3 Lines, 4 Pieces (Inset Line must run before Inset
Pieces so the line's band still reaches an outline that has not been inset
yet), and 5 UV behind the Experimental switch.
Every step works on the GUIDE (Setup's picker) and on the mode the
GUIDE is in — like every other panel in the tab, and unlike this panel's own
past, where step 1 took the selection and the rest took the active object
(see clo_cleanup.operators._guide). The later steps need step 1's ShapeKey.
Solidify stays a live modifier throughout — the export is also the retopo
Guide, and applying it would break that. Numbers live in a "... Settings"
child panel, as everywhere else in the tab.
"""

import bpy

from .. import ui_common as uic


class AC9_PT_CloCleanup(bpy.types.Panel):
    """Prepare the CLO export for baking: flatten UVs, then clean seams,
    fold lines and rims."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Retopo"
    bl_label = "Guide Prep"
    bl_order = 1          # right after Setup (0)
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        top = context.scene.ac9_cloth_retopo
        p = top.clo

        if not uic.guide_set(layout, top):
            return
        in_edit = uic.guide_mode(top) == 'EDIT'

        col = layout.column(align=True)
        row = uic.labeled_row(col, "1 Flat SK")
        row.scale_y = 1.2
        row.operator("ac9_cloth.flatten_uv_to_sk", text="Create Flat SK", icon='MOD_UVPROJECT')

        row = uic.labeled_row(col, "2 Folds")
        row.scale_y = 1.2
        row.operator("ac9_cloth.clo_tag_by_angle", text="Find Folds", icon='EDGESEL')
        row = uic.labeled_row(col, "")
        row.operator("ac9_cloth.clo_select_tagged", text="Show")
        row.operator("ac9_cloth.clo_tag_selected", text="Mark").kind = 'CREASE'
        row.operator("ac9_cloth.clo_tag_selected", text="Untag").kind = 'NONE'
        row.operator("ac9_cloth.clo_clear_tags", text="", icon='X')
        row = uic.labeled_row(col, "3 Lines")
        row.scale_y = 1.2
        row.operator("ac9_cloth.clo_inset_line", text="Inset Line", icon='MOD_EDGESPLIT')
        row = uic.labeled_row(col, "4 Pieces")
        row.scale_y = 1.2
        row.operator("ac9_cloth.clo_inset", text="Inset Pieces", icon='MOD_SOLIDIFY')
        col.prop(p, "band_width")
        # UV Mirror is Experimental since 2026-09-09: unlike steps 1-4, which
        # are "press the button", step 5 only makes sense in the middle of a
        # hand sequence that happens outside this panel (weld, stitch the
        # dart in the UV editor, unwrap ONE side) — and it has had little
        # testing. Keeping it here as a plain step invited pressing it in
        # order, which is not how it works.
        if uic.experimental_enabled(context):
            row = uic.labeled_row(col, "5 UV")
            row.scale_y = 1.2
            row.operator("ac9_cloth.uv_mirror_make_ref", text="Reference", icon='DUPLICATE')
            row.operator("ac9_cloth.uv_mirror_pairs", text="Pairs", icon='MOD_MIRROR')
            row.operator("ac9_cloth.uv_mirror_self", text="Self")
            uic.draw_hint(col, "UV: Reference first; weld + stitch + unwrap ONE side; Edit Mode: select a vertex on the correct side, then Pairs / Self. Pairs with nothing selected (or Object Mode): all pairs, edited side wins")
        if not in_edit:
            uic.draw_hint(col, "Show / Untag / Inset Line: put the Guide in Edit Mode")
        uic.draw_hint(col, "Every step works on the Guide (Setup). The later steps need step 1; Basis is left untouched")
        uic.draw_hint(col, "Leave Solidify a live modifier (do not apply it)")

        uic.draw_status(layout, p.status)


class AC9_PT_CloCleanupSettings(bpy.types.Panel):
    """Tuning values for the Prepare panel."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Retopo"
    bl_label = "Guide Prep Settings"
    bl_parent_id = "AC9_PT_CloCleanup"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        p = context.scene.ac9_cloth_retopo.clo
        col = layout.column(align=True)
        uic.draw_heading(col, "Find Folds", icon='EDGESEL')
        col.prop(p, "tag_min_angle")
        layout.separator()
        col = layout.column(align=True)
        uic.draw_heading(col, "Inset Line", icon='MOD_EDGESPLIT')
        col.prop(p, "fold_extend")
        col.prop(p, "fold_min_dihedral")
        col.prop(p, "line_profile")
