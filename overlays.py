"""Overlays — one place to switch every AC9 Cloth Retopo viewport overlay.

Before this module the 13 overlay toggles were scattered across the workflow
panels (10 in UV Seam Guide, 3 in CLO Projector), each next to the operator
that produced the data it draws. That reads well the first time and badly
every time after: switching from "boundary work" to "3D check" meant hunting
toggles in four different boxes.

So the toggles live here, grouped by what they draw, behind work-mode presets:

  Boundary      — building / checking the 2D seam boundary (the current job)
  Seams         — reading the guide's seam structure
  Status        — reading the Seam Status verdict
  3D Check      — looking at the Mirror in 3D
  All Off       — kill every individual overlay (master switch untouched)

The preset called "3D Check" used to be called "Mirror", which collided with
the "Mirror" box at the bottom of the panel (Selection Link) — same word for
a work mode and for a group of toggles.

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
from . import ui_common as uic


# Per preset: which toggles go ON. Anything omitted is set to False, so a
# preset always lands on a fully-known state instead of inheriting leftovers
# from whatever was on before.
_PRESETS = {
    'BOUNDARY': {
        "seam": {
            "show_anchors":       True,
            "show_seam_guides":   True,
            "show_free_edges":    True,
            "show_crease_lines":  True,
            "show_seam_parity":   True,
            "show_ghost_points":  True,
            "show_only_unplaced": True,
            "show_orphan_rings":  True,
            # Generate places vertices ON the fold axis and rebuilds one twin
            # from the other, so both are wanted while the boundary is being
            # built, not only while reading the structure (2026-09-09).
            "show_symmetry_axis": True,
            "show_mirror_pairs":  True,
            # Pins are read by Adjust Density alone, and only right after it
            # runs; a permanent overlay for them in the main work preset was
            # noise (2026-09-09).
        },
        "proj": {},
    },
    'SEAMS': {
        "seam": {
            "show_anchors":       True,
            "show_seam_guides":   True,
            "show_free_edges":    True,
            # Pair Lines (one line per matched seam pair) is left OFF: with the
            # seams, free edges, folds and twins already drawn it reads as
            # noise over the same edges (2026-09-09). Still a toggle.
            "show_symmetry_axis": True,
            "show_crease_lines":  True,
            "show_mirror_pairs":  True,
            "show_ghost_points":  True,
            "show_only_unplaced": True,
            "show_orphan_rings":  True,
        },
        "proj": {
            "show_seam_lines": True,
        },
    },
    # Status is its own mode, not a layer on top of Boundary. The status
    # colours are drawn over the seam lines they describe and are thicker, so
    # with Seams (cyan) and Free Edges (yellow) also on, one edge is being
    # coloured by two systems at once and the status verdict always wins —
    # reported from production as "the two colour schemes fight". So this
    # preset drops the structural line colours and keeps only what does NOT
    # compete for the same pixels: the white outline (which still shows the
    # untouched spans status leaves uncoloured), the ghosts and anchors you
    # read while fixing a seam, and the parity counts, which are text.
    'STATUS': {
        "seam": {
            "show_seam_status":   True,
            "show_seam_parity":   True,
            "show_ghost_points":  True,
            "show_only_unplaced": True,
            "show_orphan_rings":  True,
            "show_anchors":       True,
        },
        "proj": {
            "show_seam_lines": True,
        },
    },
    'VIEW3D': {
        "seam": {},
        "proj": {
            "show_seam_lines":     True,
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
    "show_free_edges",
    "show_pair_lines",
    "show_symmetry_axis",
    "show_crease_lines",
    "show_mirror_pairs",
    "show_ghost_points",
    "show_ghost_lines",
    "show_only_unplaced",
    "show_orphan_rings",
    # ghost_lines_unplaced_only is deliberately NOT here. This list is what the
    # presets stomp, and everything in it draws something on its own. That flag
    # only picks WHICH batch the Ghost Lines layer draws, so stomping it to
    # False would mean "All Off" silently re-arms all 320 connectors the next
    # time Lines is switched back on.
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
            ('BOUNDARY', "Boundary", "2D seam boundary work: seam guides, free "
                                     "edges, creases, folds, twins, ghosts, "
                                     "anchors, parity counts"),
            ('SEAMS',    "Seams",    "Read the guide's seam structure: seams, "
                                     "free edges, folds, twins, white "
                                     "outline, ghosts"),
            ('STATUS',   "Status",   "Read the Seam Status verdict: status "
                                     "colours, white outline, ghosts, anchors, "
                                     "parity counts — the structural seam "
                                     "colours are turned off so nothing else "
                                     "is colouring the same edges"),
            ('VIEW3D',   "3D Check", "Look at the Mirror in 3D: white outline"),
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
    row.operator("ac9_cloth.overlay_preset", text="Status").preset = 'STATUS'
    row.operator("ac9_cloth.overlay_preset", text="3D Check").preset = 'VIEW3D'
    col.operator("ac9_cloth.overlay_preset", text="All Off").preset = 'NONE'

    body = layout.column()
    body.enabled = top.show_overlays

    box = body.box()
    box.label(text="Seam Lines", icon='UV_DATA')
    col = box.column(align=True)
    # Seams and Free Edges are the exact edges Seam Status repaints, wider and
    # on top, so while it is on these two toggles cannot change what you see.
    # Greyed rather than hidden or force-disabled: switching a toggle OFF
    # behind the user's back is the "it says ON and draws nothing" trap this
    # panel already has a warning box for.
    sub = col.column(align=True)
    sub.active = not seam.show_seam_status
    sub.prop(seam, "show_seam_guides", text="Seams (cyan)")
    sub.prop(seam, "show_free_edges", text="Free Edges (yellow)")
    if seam.show_seam_status:
        sub.label(text="Status is colouring these", icon='INFO')
    col.prop(seam, "show_pair_lines", text="Pair Lines")
    # Fold Lines and Twins are the two halves of one answer (a panel is either
    # symmetric within itself or has a left/right partner), so they are read
    # together and sit together.
    col.prop(seam, "show_symmetry_axis", text="Fold Lines")
    col.prop(seam, "show_mirror_pairs", text="Twins (magenta)")
    col.prop(seam, "show_crease_lines", text="Creases (Find Folds)")
    col.prop(proj, "show_seam_lines", text="Outline (white)")

    box = body.box()
    # Anchors come from their own analysis pass (a manual cache, not
    # refreshed by Generate), so the refresh button sits on the heading.
    head = box.row(align=True)
    head.label(text="Marks", icon='DECORATE_KEYFRAME')
    head.operator("ac9_cloth.analyze_anchors", text="", icon='FILE_REFRESH')
    box.prop(seam, "show_anchors", text="Anchors")
    # Corners and Pins are drawn from the Density family's attributes, which
    # is Experimental since 2026-09-09 — a toggle that can only ever draw
    # nothing is worse than no toggle.
    if uic.experimental_enabled(context):
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
    # The cross marks the partner side; the ring marks the vertex that owns it.
    # Both are the same warning seen from opposite ends, so the ring lives here
    # under Points rather than as a layer of its own.
    sub = col.row()
    sub.enabled = seam.show_ghost_points
    sub.prop(seam, "show_orphan_rings", text="Orphan Rings")
    col.prop(seam, "show_ghost_lines", text="Lines")
    sub = col.row()
    sub.enabled = seam.show_ghost_lines
    sub.prop(seam, "ghost_lines_unplaced_only", text="Unplaced Lines Only")
    col.prop(seam, "show_snap_radius", text="Snap Radius")

    box = body.box()
    # Seam Status is a snapshot: the colours are built when the operator runs
    # and never again, so an edit made afterwards leaves stale colours on
    # screen. Hence the same refresh-on-the-heading pattern as Anchors and
    # Ghosts above. select_problems is OFF here — from the overlay panel this
    # is a "redraw what I am looking at" button, and it should not reach over
    # and change the selection the way the Boundary panel's Verify does.
    head = box.row(align=True)
    head.label(text="Status", icon='CHECKMARK')
    head.operator("ac9_cloth.seam_status_report", text="",
                  icon='FILE_REFRESH').select_problems = False
    col = box.column(align=True)
    col.prop(seam, "show_seam_parity", text="Vertex Counts")
    col.prop(seam, "show_seam_status", text="Seam Status")
    # ac9_is_boundary is written only by the Experimental Sync 2D>3D /
    # Sync 3D>2D / Align Boundary ops (it is their seam-side pin); Refresh
    # Mirror never touches it, so outside Experimental it is stale or empty.
    if uic.experimental_enabled(context):
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
        col.prop(seam, "free_edge_color",  text="Free Edge")
        col.prop(seam, "guide_line_width", text="Seam Width")

        col = layout.column(align=True)
        col.prop(seam, "ghost_color_unplaced", text="Ghost (Unplaced)")
        col.prop(seam, "ghost_color_placed",   text="Ghost (Placed)")
        col.prop(seam, "ghost_line_color",     text="Ghost Line")
        col.prop(seam, "ghost_cross_size")
        col.prop(seam, "ghost_line_width")

        col = layout.column(align=True)
        col.prop(seam, "symmetry_color",      text="Fold")
        col.prop(seam, "crease_color",        text="Crease")
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
