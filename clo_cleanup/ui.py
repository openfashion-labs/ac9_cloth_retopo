"""Prepare panel — the CLO export's own work phase, before it is used as a
bake source.

Rows are numbered in workflow order so the panel reads as the procedure:
1 Flat SK, 2 Folds, 3 Lines, 4 Pieces (Inset Line must run before Inset
Pieces so the line's band still reaches an outline that has not been inset
yet). Flat SK works on the whole selection (a CLO export is usually several
pieces); Folds/Lines/Pieces work on the active object, and need step 1's
ShapeKey to have run on it. Solidify stays a live modifier throughout — the
export is also the retopo Guide, and applying it would break that. Numbers
live in a "... Settings" child panel, as everywhere else in the tab.
"""

import bpy

from .. import ui_common as uic


class AC9_PT_CloCleanup(bpy.types.Panel):
    """Prepare the CLO export for baking: flatten UVs, then clean seams,
    fold lines and rims."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Retopo"
    bl_label = "Prepare"
    bl_order = 0
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        top = context.scene.ac9_cloth_retopo
        p = top.clo

        col = layout.column(align=True)
        row = uic.labeled_row(col, "1 Flat SK")
        row.scale_y = 1.2
        row.operator("ac9_cloth.flatten_uv_to_sk", text="Create Flat SK", icon='MOD_UVPROJECT')

        ob = context.active_object
        if ob is None or ob.type != 'MESH':
            uic.draw_blocker(layout, "Select the CLO mesh (active object)")
            return
        in_edit = ob.mode == 'EDIT'

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
        if not in_edit:
            uic.draw_hint(col, "Show / Untag / Inset Line: Edit Mode")
        uic.draw_hint(col, "Create Flat SK: every selected mesh. Steps 2-4 need it; Basis is left untouched")
        uic.draw_hint(col, "Leave Solidify a live modifier (do not apply it)")

        uic.draw_status(layout, p.status)


class AC9_PT_CloCleanupSettings(bpy.types.Panel):
    """Tuning values for the Prepare panel."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Retopo"
    bl_label = "Prepare Settings"
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
