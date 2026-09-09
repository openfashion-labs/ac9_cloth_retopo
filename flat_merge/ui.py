"""Drape Merge panel — drawn inside the AC9 Cloth Retopo sidebar tab.

A self-contained 2D tool: embed a flat Drape strip mesh into a flat Grid mesh
(drape wins), with hole detection on the result. It is a separate entry point
from the Guide-based boundary workflow, so it has its own two object pickers
and does not read the shared Retopo / Guide.
"""

import bpy

from .. import ui_common as uic


def draw_drape_merge(layout, context, p):
    """Inputs, the action, and the result. Tuning lives in the Settings panel."""
    col = layout.column(align=True)
    col.prop(p, "drape_obj")
    col.prop(p, "grid_obj")

    col = layout.column(align=True)
    row = uic.labeled_row(col, "Merge")
    row.scale_y = 1.3
    row.operator("ac9_cloth.flat_merge", text="Drape → Grid",
                 icon="AUTOMERGE_ON")
    row = uic.labeled_row(col, "Check")
    row.operator("ac9_cloth.select_holes", text="Holes",
                 icon="MOD_EDGESPLIT")

    obj = context.active_object
    if obj is not None and "ac9_hole_count" in obj:
        n = obj["ac9_hole_count"]
        if n:
            uic.draw_blocker(layout, f"Last merge left {n} hole(s)")
        else:
            uic.draw_status(layout, "Last merge: no holes")


def draw_drape_merge_settings(layout, p):
    col = layout.column(align=True)
    col.label(text="Seam Fill", icon='AUTOMERGE_ON')
    col.prop(p, "seam_mode", text="")
    col.prop(p, "fix_tjunctions")
    if p.seam_mode == "QUAD":
        col.prop(p, "carve_cells")
        col.prop(p, "quad_angle")
        col.prop(p, "split_drape")
    else:
        col.prop(p, "carve_margin")
    if p.seam_mode == "TRI":
        col.prop(p, "sliver_factor")

    layout.separator()
    col = layout.column(align=True)
    col.label(text="Output", icon='OUTLINER_OB_MESH')
    col.prop(p, "result_name")
    col.prop(p, "hide_sources")
    col.prop(p, "merge_distance")


class AC9_PT_FlatMerge(bpy.types.Panel):
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "AC9 Cloth Retopo"
    bl_label       = "Drape Merge"
    bl_parent_id   = "AC9_PT_Faces"
    bl_options     = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return uic.experimental_enabled(context)

    def draw(self, context):
        p = context.scene.ac9_cloth_retopo.flat_merge
        draw_drape_merge(self.layout, context, p)


class AC9_PT_FlatMergeSettings(bpy.types.Panel):
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "AC9 Cloth Retopo"
    bl_label       = "Merge Settings"
    bl_parent_id   = "AC9_PT_FlatMerge"
    bl_options     = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return uic.experimental_enabled(context)

    def draw(self, context):
        p = context.scene.ac9_cloth_retopo.flat_merge
        draw_drape_merge_settings(self.layout, p)
