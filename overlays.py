"""Overlays — one place to switch every AC9 Cloth Retopo viewport overlay.

Before this module the 13 overlay toggles were scattered across the workflow
panels (10 in UV Seam Guide, 3 in CLO Projector), each next to the operator
that produced the data it draws. That reads well the first time and badly
every time after: switching from "boundary work" to "3D check" meant hunting
toggles in four different boxes.

So the toggles live here, grouped by what they draw, behind work-mode presets:

  Boundary      — building / checking the 2D seam boundary (the current job)
  Seams         — reading the guide's seam structure
  Mirror Check  — looking at the Mirror in 3D
  All Off       — kill every individual overlay (master switch untouched)

The master switch (top.show_overlays) still short-circuits every draw handler.
It is drawn here and in the viewport header — nowhere else.

Selection Link is deliberately NOT a preset-managed toggle: it is wanted in
every work mode (it is how you find a 2D selection on the Mirror and back),
so presets leave it alone.
"""

import bpy
from bpy.props import EnumProperty

from .uv_seam_guide.gpu_overlay import _cache as _seam_cache
from .clo_projector import ui as _proj_ui


# Per preset: which toggles go ON. Anything omitted is set to False, so a
# preset always lands on a fully-known state instead of inheriting leftovers
# from whatever was on before.
_PRESETS = {
    'BOUNDARY': {
        "seam": {
            "show_anchors":        True,
            "show_seam_guides":    True,
            "show_selected_ghost": True,
            "show_seam_parity":    True,
            "show_seam_status":    True,
            "show_only_unplaced":  True,
            "show_density_pins":   True,
        },
        "proj": {},
    },
    'SEAMS': {
        "seam": {
            "show_anchors":       True,
            "show_topo_corners":  True,
            "show_seam_guides":   True,
            "show_pair_lines":    True,
            "show_symmetry_axis": True,
            "show_mirror_pairs":  True,
            "show_ghost_points":  True,
            "show_only_unplaced": True,
        },
        "proj": {
            "show_seam_lines": True,
        },
    },
    'VIEW3D': {
        "seam": {},
        "proj": {
            "show_seam_lines":     True,
            "show_boundary_verts": True,
        },
    },
    'NONE': {
        "seam": {},
        "proj": {},
    },
}

# Every toggle this panel owns, in draw order. Kept explicit (rather than
# scraped from the RNA) so that an unrelated "show_*" property — e.g. the
# CLO Projector's show_experimental disclosure triangle — can't silently
# become an overlay that the presets stomp on.
_SEAM_TOGGLES = (
    "show_seam_guides",
    "show_pair_lines",
    "show_symmetry_axis",
    "show_mirror_pairs",
    "show_ghost_points",
    "show_ghost_lines",
    "show_only_unplaced",
    "show_selected_ghost",
    "show_seam_parity",
    "show_seam_status",
    "show_snap_radius",
    "show_anchors",
    "show_topo_corners",
    "show_density_pins",
)
_PROJ_TOGGLES = (
    "show_seam_lines",
    "show_boundary_verts",
)


class AC9_OT_OverlayPreset(bpy.types.Operator):
    """Switch every overlay toggle to a work-mode preset"""
    bl_idname = "ac9_cloth.overlay_preset"
    bl_label = "Overlay Preset"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(
        name="Preset",
        items=[
            ('BOUNDARY', "Boundary", "2D seam boundary work: seam guides, "
                                     "selected-pair ghost, parity counts, status"),
            ('SEAMS',    "Seams",    "Read the guide's seam structure: seams, "
                                     "pair lines, folds, twins, white outline, ghosts"),
            ('VIEW3D',   "Mirror",   "Look at the Mirror in 3D: white outline "
                                     "and boundary flags"),
            ('NONE',     "All Off",  "Turn every individual overlay off "
                                     "(the master switch is left alone)"),
        ],
        default='BOUNDARY',
    )

    def execute(self, context):
        top = context.scene.ac9_cloth_retopo
        spec = _PRESETS[self.preset]
        for group_attr, toggles in (("seam", _SEAM_TOGGLES), ("proj", _PROJ_TOGGLES)):
            group = getattr(top, group_attr)
            wanted = spec[group_attr]
            for name in toggles:
                setattr(group, name, wanted.get(name, False))
        # A preset is a request to SEE something; leaving the master off would
        # make it look like the button did nothing. "All Off" is the exception.
        if self.preset != 'NONE':
            top.show_overlays = True
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return {'FINISHED'}


def draw_overlay_body(layout, context):
    """Every overlay switch. Shared by the sidebar panel and the viewport-
    header popover, so the two can never drift apart."""
    top = context.scene.ac9_cloth_retopo
    seam = top.seam
    proj = top.proj

    row = layout.row(align=True)
    row.scale_y = 1.3
    row.prop(
        top, "show_overlays",
        text="Overlays: ON" if top.show_overlays else "Overlays: OFF",
        toggle=True,
        icon='HIDE_OFF' if top.show_overlays else 'HIDE_ON',
    )

    # Almost everything below draws from _seam_cache["pairs"], which only
    # Analyze Seams fills — and which is emptied on file load and on every
    # Reload Scripts. Without this the toggles read as ON while nothing is
    # drawn, with nothing on screen to say why. The fix sits on the same
    # row as the warning; this is the only Analyze button in this panel.
    if not _seam_cache["pairs"]:
        warn = layout.box()
        warn.alert = True
        row = warn.row(align=True)
        row.label(text="Seams not analyzed", icon='ERROR')
        row.operator("ac9_cloth.analyze_uv_seam_pairs", text="Analyze",
                     icon='UV_DATA')

    col = layout.column(align=True)
    col.enabled = top.show_overlays
    col.label(text="Presets")
    row = col.row(align=True)
    row.operator("ac9_cloth.overlay_preset", text="Boundary").preset = 'BOUNDARY'
    row.operator("ac9_cloth.overlay_preset", text="Seams").preset = 'SEAMS'
    row = col.row(align=True)
    row.operator("ac9_cloth.overlay_preset", text="Mirror").preset = 'VIEW3D'
    row.operator("ac9_cloth.overlay_preset", text="All Off").preset = 'NONE'

    body = layout.column()
    body.enabled = top.show_overlays

    box = body.box()
    box.label(text="Seam Lines", icon='UV_DATA')
    col = box.column(align=True)
    col.prop(seam, "show_seam_guides", text="Seams (cyan)")
    col.prop(seam, "show_pair_lines", text="Pair Lines")
    col.prop(seam, "show_symmetry_axis", text="Fold Lines")
    col.prop(seam, "show_mirror_pairs", text="Twins (magenta)")
    col.prop(proj, "show_seam_lines", text="Outline (white)")

    box = body.box()
    # Anchors come from their own analysis pass (a manual cache, not
    # refreshed by Generate), so the refresh button sits on the heading.
    head = box.row(align=True)
    head.label(text="Marks", icon='DECORATE_KEYFRAME')
    head.operator("ac9_cloth.analyze_anchors", text="", icon='FILE_REFRESH')
    box.prop(seam, "show_anchors", text="Anchors")
    box.prop(seam, "show_topo_corners", text="Corners")
    box.prop(seam, "show_density_pins", text="Pins")
    if seam.show_anchors and not _seam_cache["anchors"]:
        sub = box.column()
        sub.alert = True
        sub.label(text="Anchors not analyzed yet", icon='ERROR')
    elif _seam_cache["anchors"]:
        box.label(text=f"{len(_seam_cache['anchors'])} anchors, "
                       f"{_seam_cache['anchor_spans']} spans", icon='INFO')

    box = body.box()
    # Ghosts are recomputed by Refresh Mirror and (optionally) on every
    # confirmed edit; the button on the heading is the manual refresh.
    head = box.row(align=True)
    head.label(text="Ghosts", icon='GHOST_ENABLED')
    head.operator("ac9_cloth.refresh_ghosts", text="", icon='FILE_REFRESH')
    head.operator("ac9_cloth.clear_ghost_points", text="", icon='X')
    col = box.column(align=True)
    col.prop(seam, "show_selected_ghost", text="Selected Only")
    col.prop(seam, "show_ghost_points", text="Points")
    sub = col.row()
    sub.enabled = seam.show_ghost_points
    sub.prop(seam, "show_only_unplaced", text="Only Unplaced")
    col.prop(seam, "show_ghost_lines", text="Lines")
    col.prop(seam, "show_snap_radius", text="Snap Radius")

    box = body.box()
    box.label(text="Status", icon='CHECKMARK')
    col = box.column(align=True)
    col.prop(seam, "show_seam_parity", text="Vertex Counts")
    col.prop(seam, "show_seam_status", text="Seam Status")
    col.prop(proj, "show_boundary_verts", text="Boundary Flags")

    box = body.box()
    box.label(text="Mirror", icon='MOD_MIRROR')
    box.prop(proj, "show_selection_link", text="Selection Link")

    # Not a GPU overlay but the same question — "what do I see on the
    # Guide" — so it lives with the other display switches.
    box = body.box()
    box.label(text="Guide", icon='OBJECT_DATA')
    _proj_ui.draw_guide_islands(box, context, top)


class AC9_PT_Overlays(bpy.types.Panel):
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "AC9 Cloth Retopo"
    bl_label       = "Overlays"
    bl_order       = 8          # after every workflow panel, before Advanced
    bl_options     = {'DEFAULT_CLOSED'}

    def draw(self, context):
        draw_overlay_body(self.layout, context)


class AC9_PT_OverlaysPopover(bpy.types.Panel):
    """The same switches as a popover from the viewport header.

    A HEADER-region panel is never listed in the sidebar; it exists only to be
    opened with layout.popover() (see the root _draw_view3d_header_extras).
    The Appearance child stays sidebar-only — cosmetic, rarely touched.
    """
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label       = "Overlays"
    bl_ui_units_x  = 14

    def draw(self, context):
        draw_overlay_body(self.layout, context)


class AC9_PT_OverlayAppearance(bpy.types.Panel):
    """Colours, widths and marker sizes for the overlays above.

    Cosmetic only — nothing here re-runs an analysis, so it's a child panel
    that stays folded away from the toggles people actually flip.
    """
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "AC9 Cloth Retopo"
    bl_label       = "Appearance"
    bl_parent_id   = "AC9_PT_Overlays"
    bl_options     = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        top = context.scene.ac9_cloth_retopo
        seam = top.seam
        proj = top.proj
        layout.enabled = top.show_overlays

        col = layout.column(align=True)
        col.prop(seam, "guide_color",      text="Seam")
        col.prop(seam, "guide_line_width", text="Seam Width")

        col = layout.column(align=True)
        col.prop(seam, "ghost_color_unplaced", text="Ghost (Unplaced)")
        col.prop(seam, "ghost_color_placed",   text="Ghost (Placed)")
        col.prop(seam, "ghost_line_color",     text="Ghost Line")
        col.prop(seam, "ghost_cross_size")
        col.prop(seam, "ghost_line_width")

        col = layout.column(align=True)
        col.prop(seam, "symmetry_color",      text="Fold")
        col.prop(seam, "symmetry_line_width", text="Fold Width")

        col = layout.column(align=True)
        col.prop(seam, "anchor_color",      text="Anchor")
        col.prop(seam, "anchor_cross_size")

        col = layout.column(align=True)
        col.prop(proj, "selection_point_size", text="Link Point Size")
        col.prop(proj, "boundary_cross_size",  text="Boundary Cross Size")

        layout.prop(seam, "z_offset")


_CLASSES = (
    AC9_OT_OverlayPreset,
    AC9_PT_Overlays,
    AC9_PT_OverlayAppearance,
    AC9_PT_OverlaysPopover,
)


def get_classes():
    return _CLASSES
