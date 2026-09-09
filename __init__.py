"""AC9 Cloth Retopo — unified garment retopology toolkit.

Sub-packages
------------
uv_seam_guide   UV seam analysis + opposite-side ghost overlay
clo_projector   2D↔3D barycentric projection via CLO/MD guide pair
flat_merge      Drape Merge — carve flat drape strips into a flat grid
bake_maps       Guide Maps — residual (vs retopo) + sag (plane fit) bakes
guide_separate  Guide Separate — push self-touching drape layers apart for
                baking (ShapeKey AC9_Separated; '3D Source' selects it)
quad_fix        Quad Fix — re-split non-planar quads along the right diagonal
mesh_edit       Mesh Edit — UV-preserving Collapse (not Cloth-specific; will
                likely move to a shared mesh-tools addon later)
clo_cleanup     CLO Cleanup — inset the pattern outlines and fold lines on the
                flat (UV) shape so the CLO export bakes cleanly, UVs intact
flatten_uv      Create Flat SK — native bmesh replacement for the third-party
                "UV Flatten Tool" add-on: splits UV islands (every selected
                mesh) and writes the flat layout as a ShapeKey (module, not a
                sub-package)
finalize        Finalize — bakes the Retopo + Guide projection into a plain
                <Retopo>_Final mesh object: 3D shape, UV from the 2D layout,
                no ShapeKeys, no AC9 data (module, not a sub-package)
overlays        Overlays — every viewport-overlay toggle in one panel, plus
                work-mode presets (module, not a sub-package: no props of its
                own, it just drives the other tools' show_* properties)
ui_common       Shared drawing helpers — the panel grammar every ui.py follows
clear_all       Clear All — one operator that removes every trace the add-on
                wrote into the file (module, not a sub-package)
translations    Japanese (ja_JP) UI catalogue for bpy.app.translations. Keyed by
                the exact English literals used everywhere else, so English
                stays the source of truth and no label needed wrapping

Sidebar tab (organised by work phase, not by sub-package)
----------------------------------------------------------
  Prepare     the CLO export before retopo: Flat SK, then    (clo_cleanup,
              Cleanup insets                                  composes flatten_uv)
  Setup       pickers + the analyses everything depends on   (root, here)
  Boundary    the retopo's 2D boundary; Object/Edit Mode halves (uv_seam_guide)
  Faces       filling and refining the 2D interior            (root, composes
              uv_seam_guide / clo_projector / mesh_edit / quad_fix; Drape
              Merge is a child panel)
              Connect Rows — rungs between two selected lines, one per
              vertex pair, dividing the faces between them into a row
  3D View     the Mirror, the Guide's 2D/3D state, Finalize   (clo_projector,
              composes guide_separate's Contact / 3D Source rows, finalize)
  Guide Maps  diagnostic bakes                                (bake_maps)
  Overlays    everything that is drawn in the viewport        (overlays)
  Advanced    legacy, maintenance, diagnostics, abandoned     (root, composes)

Experimental (off by default; Edit > Preferences > Add-ons > AC9 Cloth Retopo):
  Preview Fill, Grid Regions, Drape Merge, Guide Separate, Mesh Edit, Quad Fix,
  Legacy Flip

Shared state
------------
bpy.types.Scene.ac9_cloth_retopo  (AC9ClothRetopoProps)
  .retopo_obj / .guide_obj / .guide_flat_shapekey — the shared inputs
  .guide_3d_source — which ShapeKey is the Guide's 3D shape (Basis / Separated)
  .status_boundary / .status_faces / .status_guide / .status_maps — one
                  result line per panel
  .seam         — AC9SeamGuideProps   (UV Seam Guide settings)
  .proj         — AC9CloProjectorProps (CLO Projector settings)
  .flat_merge   — AC9FlatMergeProps   (Drape Merge settings; self-contained,
                  ignores the shared Retopo / Guide / Flat SK pickers)
  .maps         — AC9BakeMapsProps    (Guide Maps bake settings)
  .separate     — AC9GuideSeparateProps (Guide Separate settings)
  .clo          — AC9CloCleanupProps   (CLO Cleanup settings)
"""

if "bpy" in locals():
    import importlib
    # Import first: a sub-package added since the previous load (e.g. via the
    # junction dev workflow) won't exist in this namespace yet, and reloading
    # an unbound name raises NameError during Reload Scripts.
    from . import (ui_common, uv_seam_guide, clo_projector, flat_merge, bake_maps,
                   guide_separate, quad_fix, mesh_edit, clo_cleanup, flatten_uv,
                   finalize, overlays, reset_settings, clear_all, translations)
    # ui_common first: every ui.py imports from it.
    importlib.reload(ui_common)
    importlib.reload(translations)
    importlib.reload(uv_seam_guide)
    importlib.reload(clo_projector)
    importlib.reload(flat_merge)
    importlib.reload(bake_maps)
    importlib.reload(guide_separate)
    importlib.reload(quad_fix)
    importlib.reload(mesh_edit)
    importlib.reload(clo_cleanup)
    importlib.reload(flatten_uv)
    importlib.reload(finalize)
    importlib.reload(overlays)
    importlib.reload(reset_settings)
    # clear_all last: it imports names from the sub-packages above.
    importlib.reload(clear_all)
else:
    from . import (ui_common, uv_seam_guide, clo_projector, flat_merge, bake_maps,
                   guide_separate, quad_fix, mesh_edit, clo_cleanup, flatten_uv,
                   finalize, overlays, reset_settings, clear_all, translations)

import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty
from bpy.types import AddonPreferences, Object, PropertyGroup


class AC9_Prefs(AddonPreferences):
    """Add-on Preferences (Edit > Preferences > Add-ons > AC9 Cloth Retopo).

    Just the one switch: whether the unfinished tools show up in the sidebar
    at all. Off by default — this add-on is public, and half its
    sub-packages are still being built out (see the root CLAUDE.md)."""

    bl_idname = __package__

    experimental: BoolProperty(
        name="Experimental tools",
        default=False,
        description=(
            "Show unfinished tools: Preview Fill, Grid Regions, Drape Merge, "
            "Guide Separate, Mesh Edit, Quad Fix, Legacy Flip"
        ),
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "experimental")
        layout.label(
            text="Preview Fill, Grid Regions, Drape Merge, Guide Separate, "
                 "Mesh Edit, Quad Fix, Legacy Flip",
            icon='INFO',
        )


def _poll_mesh(self, obj) -> bool:
    return obj.type == "MESH"


def _tag_redraw_3d(self, context):
    """Redraw all 3D viewports — used by the global overlay master switch."""
    try:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    except Exception:
        pass


def _update_guide_3d_source(self, context):
    """'3D Source' changed: drop the cached Guide triangles and show the
    matching ShapeKey when the Guide is in 3D. The Mirror / projections are
    refreshed by the user's next Refresh (they read through the cache)."""
    guide = self.guide_obj
    if guide is None or guide.type != 'MESH':
        return
    clo_projector.core.invalidate_guide_cache(guide)
    guide_separate.core.sync_separated_value(
        guide, self.guide_flat_shapekey, self.guide_3d_source == 'SEPARATED')
    clo_projector.gpu_overlay.invalidate()
    _tag_redraw_3d(self, context)


class AC9ClothRetopoProps(PropertyGroup):
    """Top-level scene property group. Holds the shared Retopo Mesh + Guide
    pickers (used by BOTH tools) and nested sub-props for each tool.
    """

    # Global master switch for ALL overlay drawing (both tools). When OFF, every
    # per-frame draw callback short-circuits at its top — so this is both a
    # one-click "hide everything" and a performance kill-switch (the heavy
    # per-frame work in selected-ghost / seam-parity is skipped entirely).
    show_overlays: BoolProperty(
        name="Show Overlays",
        description=(
            "Master switch for ALL AC9 Cloth Retopo viewport overlays (seams, "
            "ghosts, parity text, boundary markers — both tools). Turn OFF to "
            "hide everything at once without touching the individual toggles. "
            "Also stops all per-frame overlay computation, so it lightens the "
            "viewport when things feel heavy"
        ),
        default=True,
        update=_tag_redraw_3d,
    )

    retopo_obj: PointerProperty(
        name="Retopo",
        description=(
            "The low-poly retopo mesh you are building. Every tool in this tab "
            "works on it"
        ),
        type=Object,
        poll=_poll_mesh,
    )
    # Guide is shared by both tools (UV Seam Guide reads its Flat SK for seam
    # pairs; CLO Projector projects against Basis↔Flat SK). Lives at the top
    # level alongside Retopo Mesh, not inside either tool's sub-props.
    guide_obj: PointerProperty(
        name="Guide",
        description=(
            "Triangulated garment mesh carrying both the 3D shape (Basis "
            "ShapeKey) and the UV-flat layout (the ShapeKey named in 'Flat SK'). "
            "Every analysis reads it; nothing writes to it"
        ),
        type=Object,
        poll=_poll_mesh,
    )
    guide_flat_shapekey: StringProperty(
        name="Flat SK",
        description=(
            "Name of the ShapeKey that represents the UV-flattened (2D) layout. "
            "Example: 'UV_Map_Flattened'"
        ),
        default="",
    )
    # Which ShapeKey is the Guide's 3D shape for every reader (projection,
    # Mirror, Guide Maps, overlays). 'Separated' exists only after Guide
    # Separate has run; guide.guide_3d_shapekey() falls back to Basis.
    guide_3d_source: EnumProperty(
        name="3D Source",
        description="Which shape the projector and the maps read as the Guide's 3D shape",
        items=(
            ('BASIS', "Original",
             "The Basis ShapeKey: the drape as exported. Use for the final retopo"),
            ('SEPARATED', "Separated",
             "The AC9_Separated ShapeKey: layers pushed apart by Separate "
             "Self-Contact. Use while baking"),
        ),
        default='BASIS',
        update=_update_guide_3d_source,
    )
    # One result line per panel (R3 of the redesign): the last operator run
    # from that panel overwrites it. Replaces the per-feature *_stats strings.
    status_boundary: StringProperty(name="Boundary Result", default="")
    status_faces: StringProperty(name="Faces Result", default="")
    status_guide: StringProperty(name="Guide Result", default="")
    status_maps: StringProperty(name="Guide Maps Result", default="")

    # Disclosure state of the folded "other mode" tools (UI state only).
    ui_boundary_show_other: BoolProperty(
        name="Show Other Mode Tools",
        description="Also show the Boundary tools of the other mode (they run only in their own mode)",
        default=False,
    )
    ui_faces_show_other: BoolProperty(
        name="Show Other Mode Tools",
        description="Also show the Faces tools of the other mode (they run only in their own mode)",
        default=False,
    )

    seam: PointerProperty(type=uv_seam_guide.properties.AC9SeamGuideProps)
    proj: PointerProperty(type=clo_projector.properties.AC9CloProjectorProps)
    flat_merge: PointerProperty(type=flat_merge.properties.AC9FlatMergeProps)
    maps: PointerProperty(type=bake_maps.properties.AC9BakeMapsProps)
    separate: PointerProperty(type=guide_separate.properties.AC9GuideSeparateProps)
    quad_fix: PointerProperty(type=quad_fix.properties.AC9QuadFixProps)
    mesh_edit: PointerProperty(type=mesh_edit.properties.AC9MeshEditProps)
    clo: PointerProperty(type=clo_cleanup.properties.AC9CloCleanupProps)


class AC9_PT_Setup(bpy.types.Panel):
    """The shared inputs and the analyses every other panel depends on."""
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "AC9 Cloth Retopo"
    bl_label       = "Setup"
    bl_order       = 1

    def draw(self, context):
        layout = self.layout
        top = context.scene.ac9_cloth_retopo

        col = layout.column(align=True)
        col.prop(top, "retopo_obj")
        col.prop(top, "guide_obj")
        # Flat SK: searchable dropdown of the Guide's shape keys (no typing).
        guide = top.guide_obj
        if guide is not None and guide.type == 'MESH' and guide.data.shape_keys is not None:
            col.prop_search(
                top, "guide_flat_shapekey",
                guide.data.shape_keys, "key_blocks",
                text="Flat SK",
            )
        else:
            col.prop(top, "guide_flat_shapekey")

        if not ui_common.guide_ready(layout, top):
            return

        # One button runs every Guide analysis (Seams, Symmetry, Anchors);
        # the individual ones live in Analysis Settings.
        layout.separator()
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("ac9_cloth.analyze_all", text="Analyze Guide",
                     icon='VIEWZOOM')
        row.operator("ac9_cloth.clear_analysis", text="", icon='X')
        uv_seam_guide.ui.draw_analysis_state(layout, context)


def _draw_faces_object_tools(layout, context, top):
    """Faces, Object Mode: fill regions for the Knife, grid what the cuts
    made, subdivide, preview.

    Grid Regions is Fill Regions' working successor (see panel_regions.py's
    module docstring) — a different, earlier feature (fully-automatic
    gridding with no user cuts at all) was the one abandoned in B-28. It had
    been miscategorized in Advanced alongside that dead experiment.
    """
    col = layout.column(align=True)
    if ui_common.experimental_enabled(context):
        row = ui_common.labeled_row(col, "Preview")
        ui_common.draw_make_clear(row, "ac9_cloth.preview_fill",
                                  "ac9_cloth.clear_preview_fill",
                                  "Auto Fill", icon='MESH_GRID')
    # Connect = fill every closed region AND join the open lines first; a
    # plain Fill (faces without joining the lines) had no use of its own.
    row = ui_common.labeled_row(col, "Regions")
    row.scale_y = 1.2
    row.operator("ac9_cloth.connect_loose_ends", text="Connect",
                 icon='SNAP_MIDPOINT').extend_selected = False
    if ui_common.experimental_enabled(context):
        row.operator("ac9_cloth.grid_regions", text="Grid", icon='MESH_GRID')
        row.operator("ac9_cloth.clear_patch_grid", text="", icon='X')
    uv_seam_guide.ui.draw_symmetry_rebuild_tools(col, top.seam)
    clo_projector.ui.draw_faces_object_tools(col, context, top)


def _draw_faces_edit_tools(layout, context, top):
    """Faces, Edit Mode: regions on the selected islands, rungs between two
    selected lines, UV-keeping collapse and non-planar quad repair."""
    col = layout.column(align=True)
    row = ui_common.labeled_row(col, "Regions")
    row.scale_y = 1.2
    row.operator("ac9_cloth.connect_loose_ends", text="Connect",
                 icon='SNAP_MIDPOINT').extend_selected = False
    rows = ui_common.labeled_row(col, "Rows")
    rows.scale_y = 1.2
    rows.operator("ac9_cloth.connect_rows", text="Connect Rows",
                  icon='MOD_LATTICE')
    ui_common.draw_hint(col, "Select two lines (edges); rungs cut through "
                             "the faces between them")
    if ui_common.experimental_enabled(context):
        row.operator("ac9_cloth.grid_regions", text="Grid", icon='MESH_GRID')
        row.operator("ac9_cloth.clear_patch_grid", text="", icon='X')
        ui_common.draw_hint(col, "Selected islands only")
        mesh_edit.ui.draw_mesh_edit_tools(layout, context, top.mesh_edit)
        quad_fix.ui.draw_quad_fix_tools(layout, context, top.quad_fix)
    # Twin / Self read the selected vertex in either mode (their invoke reads
    # the edit-mesh selection when the retopo is in Edit Mode), so they belong
    # in both halves — Edit Mode is where the source island gets picked.
    uv_seam_guide.ui.draw_symmetry_rebuild_tools(col, top.seam)


class AC9_PT_Faces(bpy.types.Panel):
    """Filling and refining the 2D interior once the boundary exists.

    Composes tools from four sub-packages (uv_seam_guide's Fill Regions,
    clo_projector's Subdivide, mesh_edit, quad_fix): one work
    phase, so one panel — the sub-package boundaries are not the user's.
    """
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "AC9 Cloth Retopo"
    bl_label       = "Faces"
    bl_order       = 3

    def draw(self, context):
        layout = self.layout
        top = context.scene.ac9_cloth_retopo

        if not ui_common.retopo_ready(layout, top):
            return

        if ui_common.retopo_mode(top) == 'EDIT':
            _draw_faces_edit_tools(layout, context, top)
            other = ui_common.draw_fold(layout, top, "ui_faces_show_other",
                                        "Object Mode tools")
            if other is not None:
                _draw_faces_object_tools(other, context, top)
        else:
            _draw_faces_object_tools(layout, context, top)
            other = ui_common.draw_fold(layout, top, "ui_faces_show_other",
                                        "Edit Mode tools")
            if other is not None:
                _draw_faces_edit_tools(other, context, top)

        ui_common.draw_status(layout, top.status_faces)


class AC9_PT_Advanced(bpy.types.Panel):
    """Legacy workflows, maintenance, diagnostics and abandoned experiments.

    Kept out of the work-phase panels so those show only what the current
    workflow uses. Whether something here is deleted or kept is a separate
    decision from where it is shown.
    """
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "AC9 Cloth Retopo"
    bl_label       = "Advanced"
    bl_order       = 9
    bl_options     = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        clo_projector.ui.draw_advanced(layout, context)
        layout.separator()
        uv_seam_guide.ui.draw_advanced(layout, context)

        layout.separator()
        col = layout.column(align=True)
        row = ui_common.labeled_row(col, "Settings")
        row.operator("ac9_cloth.reset_settings", text="Reset to Defaults",
                     icon='LOOP_BACK')

        # Leaving the add-on: one button that takes everything it wrote out of
        # the file (see clear_all.py). Last in the last panel, on purpose.
        layout.separator()
        col = layout.column(align=True)
        col.label(text="Done with this file", icon='TRASH')
        col.operator("ac9_cloth.clear_all", text="Clear All AC9 Data",
                     icon='TRASH')

        if not ui_common.experimental_enabled(context):
            layout.separator()
            ui_common.draw_hint(
                layout,
                "Experimental tools: Edit > Preferences > Add-ons > AC9 Cloth Retopo",
            )


def _draw_view3d_header_extras(self, context):
    """Overlays and Refresh Mirror in the viewport header, always reachable.

    These are the two controls pressed most often: the overlays get toggled
    constantly while reading a boundary, and Refresh follows every edit. The
    viewport header does not scroll, so they stay put. The only deliberate
    duplicates of sidebar controls in the add-on.

    Drawn only when a Retopo is set, so a file that does not use this add-on
    sees nothing added to its header.
    """
    top = getattr(context.scene, "ac9_cloth_retopo", None)
    if top is None or top.retopo_obj is None:
        return
    layout = self.layout
    layout.separator()
    row = layout.row(align=True)
    row.prop(top, "show_overlays", text="", toggle=True,
             icon='HIDE_OFF' if top.show_overlays else 'HIDE_ON')
    # The full Overlays panel as a popover, so the toggles are reachable
    # without scrolling the sidebar (see overlays.AC9_PT_OverlaysPopover).
    row.popover(panel="AC9_PT_OverlaysPopover", text="Overlays")
    row.operator("ac9_cloth.refresh_mirror", text="Refresh Mirror",
                 icon="FILE_REFRESH")


# Registration order matters:
#   1. Sub-PropertyGroups first (AC9SeamGuideProps, AC9CloProjectorProps, ...)
#   2. AC9ClothRetopoProps (references the above as PointerProperty types)
#   3. Root panels that are PARENTS of sub-package child panels (Setup holds
#      Analysis Settings; Faces holds Face Settings and Drape Merge) — a parent
#      must be registered before its children
#   4. Operator + panel classes from each sub-package
_classes = (
    AC9_Prefs,
    uv_seam_guide.get_prop_class(),
    clo_projector.get_prop_class(),
    flat_merge.get_prop_class(),
    bake_maps.get_prop_class(),
    guide_separate.get_prop_class(),
    quad_fix.get_prop_class(),
    mesh_edit.get_prop_class(),
    clo_cleanup.get_prop_class(),
    AC9ClothRetopoProps,
    AC9_PT_Setup,
    AC9_PT_Faces,
    AC9_PT_Advanced,
    *uv_seam_guide.get_classes(),
    *clo_projector.get_classes(),
    *flat_merge.get_classes(),
    *bake_maps.get_classes(),
    *guide_separate.get_classes(),   # after clo_projector: child of AC9_PT_MirrorView
    *quad_fix.get_classes(),
    *mesh_edit.get_classes(),
    *clo_cleanup.get_classes(),
    *flatten_uv.get_classes(),
    *finalize.get_classes(),
    *overlays.get_classes(),
    *reset_settings.get_classes(),
    *clear_all.get_classes(),
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ac9_cloth_retopo = PointerProperty(type=AC9ClothRetopoProps)
    uv_seam_guide.register_extras()
    clo_projector.register_extras()
    flat_merge.register_extras()
    bake_maps.register_extras()
    guide_separate.register_extras()
    quad_fix.register_extras()
    mesh_edit.register_extras()
    clo_cleanup.register_extras()
    bpy.types.VIEW3D_HT_header.append(_draw_view3d_header_extras)
    # After the classes: the catalogue keys off the labels they carry.
    translations.register()


def unregister():
    # First: stop translating before anything is torn down.
    translations.unregister()
    try:
        bpy.types.VIEW3D_HT_header.remove(_draw_view3d_header_extras)
    except Exception:
        pass
    clo_cleanup.unregister_extras()
    mesh_edit.unregister_extras()
    quad_fix.unregister_extras()
    guide_separate.unregister_extras()
    bake_maps.unregister_extras()
    flat_merge.unregister_extras()
    clo_projector.unregister_extras()
    uv_seam_guide.unregister_extras()
    if hasattr(bpy.types.Scene, "ac9_cloth_retopo"):
        del bpy.types.Scene.ac9_cloth_retopo
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
