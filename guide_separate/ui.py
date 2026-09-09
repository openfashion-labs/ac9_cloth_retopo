"""Guide Separate's share of the sidebar.

Drawn inside the 3D View panel (the Guide's state lives there), via
draw_guide_tools; its tuning values are the child panel below.
"""

import bpy

from .. import ui_common as uic
from . import core


def draw_guide_tools(layout, context, top):
    """Contact  [Check] [Separate] [x]   /   3D Source  [Original|Separated]"""
    guide = top.guide_obj
    has_sep = core.has_separation(guide)

    row = uic.labeled_row(layout, "Contact")
    uic.draw_check_apply(row, "ac9_cloth.separate_guide", "Separate",
                         apply_icon='MOD_SHRINKWRAP')
    row.operator("ac9_cloth.clear_guide_separation", text="", icon='X')

    row = uic.labeled_row(layout, "3D Source")
    row.enabled = has_sep
    row.prop(top, "guide_3d_source", expand=True)


class AC9_PT_GuideSeparateSettings(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Retopo"
    bl_label = "Separation Settings"
    bl_parent_id = "AC9_PT_MirrorView"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        # Its Contact / Separate controls are gated the same way (see
        # draw_guide_tools above) — no point showing the tuning knobs for a
        # feature that has no button to run right now.
        return uic.experimental_enabled(context)

    def draw(self, context):
        layout = self.layout
        p = context.scene.ac9_cloth_retopo.separate
        col = layout.column(align=True)
        col.prop(p, "gap_mm")
        col.prop(p, "smooth_radius_mm")
        col.prop(p, "max_iterations")
        col.prop(p, "include_solidify")
