"""Guide Maps panel — drawn inside the AC9 Cloth Retopo sidebar tab.

Bakes retopo-guidance maps from the shared Guide (and Retopo Mesh):
  Residual Map — how far the current retopo still is from the Guide
  Sag Map      — low-frequency per-panel bowing of the Guide itself
  Drape Map    — AO x Curvature shaded reference for 2D knife-cutting
Results land in fixed-name images (AC9_ResidualMap / AC9_SagMap /
AC9_DrapeMap); keep one open in an Image Editor and it refreshes on every
re-bake.
"""

import bpy

from .. import ui_common as uic


class AC9_PT_BakeMaps(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Retopo"
    bl_label = "Guide Maps"
    bl_order = 5
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        top = context.scene.ac9_cloth_retopo
        p = top.maps

        if not uic.guide_ready(layout, top):
            return

        col = layout.column(align=True)
        row = uic.labeled_row(col, "Resolution")
        row.prop(p, "resolution", text="")
        row = uic.labeled_row(col, "Residual")
        row.scale_y = 1.2
        row.operator("ac9_cloth.bake_residual_map", text="Bake",
                     icon='RENDER_STILL')
        row = uic.labeled_row(col, "Sag")
        row.scale_y = 1.2
        row.operator("ac9_cloth.bake_sag_map", text="Bake",
                     icon='RENDER_STILL')
        row = uic.labeled_row(col, "Drape")
        row.scale_y = 1.2
        row.operator("ac9_cloth.bake_drape_map", text="Bake",
                     icon='RENDER_STILL')

        row = uic.labeled_row(col, "Preview")
        row.prop(p, "preview_map", text="")
        row.operator("ac9_cloth.bake_preview_plane", text="Plane",
                     icon='MESH_PLANE')
        # The viewport's own Solid colour source, surfaced here because the
        # map is invisible until it says Texture — and few users know the
        # setting exists. Same property as Viewport Shading > Color.
        space = context.space_data
        if space is not None and getattr(space, "type", None) == 'VIEW_3D':
            row = uic.labeled_row(col, "Solid")
            row.prop(space.shading, "color_type", text="")

        uic.draw_status(layout, top.status_maps)


class AC9_PT_BakeMapsSettings(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Retopo"
    bl_label = "Map Settings"
    bl_parent_id = "AC9_PT_BakeMaps"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        p = context.scene.ac9_cloth_retopo.maps

        col = layout.column(align=True)
        col.label(text="Residual Map", icon='MOD_DATA_TRANSFER')
        col.prop(p, "residual_scale_mm")
        col.prop(p, "cover_eps_mm")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Sag Map", icon='MOD_SMOOTH')
        col.prop(p, "sag_scale_mm")
