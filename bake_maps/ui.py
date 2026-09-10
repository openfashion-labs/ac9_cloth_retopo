"""Guide Maps panel — drawn inside the AC9 Cloth Retopo sidebar tab.

Bakes retopo-guidance maps from the shared Guide (and Retopo Mesh):
  Residual Map — how far the current retopo still is from the Guide
  Sag Map      — low-frequency per-panel bowing of the Guide itself
  Drape Maps   — AO, Curvature and their product: the shaded reference for
                 2D knife-cutting
Results land in images named after the Guide they came from
(AC9_ResidualMap_<Guide> / AC9_SagMap_<Guide> / AC9_DrapeMap_<Guide>), so
garments retopologised side by side keep their own sets; keep one open in an
Image Editor and it refreshes on every re-bake. The Baked Maps sub-panel
lists what a file is carrying and what it costs.
"""

import bpy

from .. import ui_common as uic
from . import core


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

        # The one thing worth saying before the button is pressed: on the
        # CPU a 2048 bake freezes Blender for a minute or more (measured
        # 67.6 s on a 202k-vert Guide, against 12.4 s on the GPU), and
        # nothing can be drawn while it runs.
        note = core.slow_bake_warning(int(p.resolution))
        if note:
            box = layout.box()
            box.label(text="CPU bake: Blender stays frozen until it finishes",
                      icon='ERROR')
            box.label(text="Enable a GPU in Preferences > System, or use 1024.")

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


class AC9_PT_BakedMapsList(bpy.types.Panel):
    """What this file is carrying: every baked map, grouped by the Guide it
    belongs to, with what it costs in the .blend and a way to throw it away.

    Grouped rather than flat because the interesting question with several
    garments open is "whose maps are these" — and the current Guide's group
    is drawn first for the same reason."""

    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Retopo"
    bl_label = "Baked Maps"
    bl_parent_id = "AC9_PT_BakeMaps"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        top = context.scene.ac9_cloth_retopo
        entries = core.map_images()
        if not entries:
            layout.label(text="No maps baked yet.", icon='INFO')
            return

        total_mb = sum(core.packed_megabytes(img) for img, _k in entries)
        row = layout.row(align=True)
        row.label(text=f"{len(entries)} maps · {total_mb:.0f} MB in file")
        row.operator("ac9_cloth.delete_all_maps", text="", icon='TRASH')

        groups = {}
        for img, key in entries:
            groups.setdefault(core.map_guide_label(img.name), []).append((img, key))
        current = top.guide_obj.name if top.guide_obj is not None else None

        for label in sorted(groups, key=lambda n: (n != current, n.lower())):
            box = layout.box()
            head = box.row(align=True)
            head.label(text=label or "(no Guide in the name)",
                       icon='OUTLINER_OB_MESH' if label == current else 'MESH_DATA')
            op = head.operator("ac9_cloth.delete_guide_maps", text="", icon='X')
            op.guide_name = label

            col = box.column(align=True)
            for img, key in sorted(groups[label], key=lambda e: e[1]):
                mb = core.packed_megabytes(img)
                cost = f"{mb:.0f} MB" if mb else "session"
                text = f"{core.MAP_LABELS.get(key, key)} · {img.size[0]}px · {cost}"
                retopo = img.get(core.RETOPO_PROP)
                if key == 'RESIDUAL' and retopo:
                    text += f" · vs {retopo}"
                r = col.row(align=True)
                r.label(text=text, icon='IMAGE_DATA')
                op = r.operator("ac9_cloth.delete_map_image", text="", icon='X')
                op.image_name = img.name


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
        col.prop(p, "keep_residual_in_file")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Sag Map", icon='MOD_SMOOTH')
        col.prop(p, "sag_scale_mm")
        col.prop(p, "keep_sag_in_file")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Drape Maps", icon='SHADING_RENDERED')
        col.prop(p, "ao_distance_mm")
        col.prop(p, "drape_ao_mix")
        col.prop(p, "keep_drape_in_file")
        col.prop(p, "keep_drape_passes")
