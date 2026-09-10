"""Quad Fix panel — Edit-Mode tool to repair non-planar quads.

Workflow: enter Edit Mode on the retopo, (optionally select the flagged faces
from your mesh checker), inspect the clean/saddle split, then fix.
"""

import bpy

from .. import ui_common as uic


def draw_quad_fix_tools(layout, context, p):
    """Edit Mode: select the flagged quads by kind, then fix them."""
    col = layout.column(align=True)
    row = uic.labeled_row(col, "Select")
    row.operator("ac9_cloth.select_quads_by_type", text="Clean").kind = "clean"
    row.operator("ac9_cloth.select_quads_by_type", text="Saddle").kind = "saddle"
    row.operator("ac9_cloth.select_quads_by_type", text="All").kind = "nonplanar"
    row = uic.labeled_row(col, "Fix")
    row.scale_y = 1.2
    op = row.operator("ac9_cloth.fix_nonplanar_quads", text="Convex",
                      icon='MOD_TRIANGULATE')
    op.alternate = False
    # A/B preview: the 'wrong' diagonal, to SEE what goes bad while orbiting
    # (Fix, orbit, Undo, Alternate, orbit — see the operator's description).
    op = row.operator("ac9_cloth.fix_nonplanar_quads", text="Alternate",
                      icon='MOD_TRIANGULATE')
    op.alternate = True


def draw_quad_fix_settings(layout, p):
    col = layout.column(align=True)
    col.prop(p, "nonplanar_angle")
    col.prop(p, "flat_angle")
    col.prop(p, "only_selected")
    col.prop(p, "fix_saddles")


class AC9_PT_FaceSettings(bpy.types.Panel):
    """Tuning values for the Faces panel (currently Quad Fix's thresholds)."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Retopo"
    bl_label = "Face Settings"
    bl_parent_id = "AC9_PT_Faces"
    bl_options = {'DEFAULT_CLOSED'}

    # No poll: Preview Fill is a normal feature since 2026-09-09, so this
    # panel always has something to show. Quad Fix and Grid Regions are still
    # Experimental and their sections are gated inside draw() instead - a
    # tuning knob for a feature with no visible button is noise.

    def draw(self, context):
        layout = self.layout
        top = context.scene.ac9_cloth_retopo
        seam = top.seam

        if uic.shapely_available():
            layout.label(text="Preview Fill", icon='MESH_GRID')
            col = layout.column(align=True)
            col.prop(seam, "fill_target_mm")
            col.prop(seam, "fill_margin_factor")

        if not uic.experimental_enabled(context):
            return

        layout.separator()
        layout.label(text="Quad Fix", icon='MOD_TRIANGULATE')
        draw_quad_fix_settings(layout, top.quad_fix)

        layout.separator()
        layout.label(text="Grid Regions", icon='MESH_GRID')
        col = layout.column(align=True)
        col.prop(seam, "grid_target_mm")
        col.prop(seam, "grid_smooth_passes")
        col.prop(seam, "grid_band_fallback")
