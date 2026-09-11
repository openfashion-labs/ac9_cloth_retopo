"""AC9 Cloth Retopo — unified garment retopology toolkit.

Sub-packages
------------
uv_seam_guide   UV seam analysis + opposite-side ghost overlay
clo_projector   2D↔3D barycentric projection via CLO/MD guide pair
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
  .maps         — AC9BakeMapsProps    (Guide Maps bake settings)
  .separate     — AC9GuideSeparateProps (Guide Separate settings)
  .clo          — AC9CloCleanupProps   (CLO Cleanup settings)
"""

if "bpy" in locals():
    import importlib
    # Import first: a sub-package added since the previous load (e.g. via the
    # junction dev workflow) won't exist in this namespace yet, and reloading
    # an unbound name raises NameError during Reload Scripts.
    from . import (ui_common, uv_seam_guide, clo_projector, bake_maps,
                   guide_separate, quad_fix, mesh_edit, clo_cleanup, flatten_uv,
                   finalize, overlays, reset_settings, clear_all, translations)
    # ui_common first: every ui.py imports from it.
    importlib.reload(ui_common)
    importlib.reload(translations)
    importlib.reload(uv_seam_guide)
    importlib.reload(clo_projector)
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
    from . import (ui_common, uv_seam_guide, clo_projector, bake_maps,
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
            "Show unfinished tools: Grid Regions, Align to Outline, Mesh "
            "Edit, Quad Fix, Legacy Flip, and the Density family (Density, "
            "Even Out, Count, Spacing, Pins, Corners)"
        ),
    )

    progress_audit: BoolProperty(
        name="Report slow operators",
        default=False,
        description=(
            "Print a line to the console whenever a tool of this add-on "
            "takes a second or more without showing progress. For developing "
            "the add-on: it names the tools that need a progress bar"
        ),
    )

    bake_on_gpu: BoolProperty(
        name="Bake on GPU when available",
        default=True,
        description=(
            "Run the Guide Maps bakes on the GPU set up in Preferences > "
            "System, instead of whatever device the .blend happens to carry. "
            "A scene that has never rendered with Cycles carries CPU, and a "
            "2048 Drape bake then freezes the interface for a minute or more "
            "(measured: 67.6 s against 12.4 s on the GPU). Turn this off if "
            "a bake fails for lack of VRAM — it will then use the scene's own "
            "device, which is what earlier versions always did"
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
        layout.separator()
        layout.prop(self, "bake_on_gpu")
        layout.separator()
        layout.prop(self, "progress_audit")


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


# ── Input swap: reset everything derived from the old object ─────────────────
#
# Both pointers are read by every analysis, overlay and GPU batch in the
# add-on, and none of that used to notice a SWAP. Picking a different Guide
# (jacket -> shirt) left the jacket's seam lines, ghosts, status colours and
# outline on screen, and Analyze Guide did not clear them: it only refreshes
# the caches its own three passes fill (seams, folds/twins, anchors), so the
# Seam Status snapshot, the ghosts (when Live Ghost Update is off) and the
# projector's Outline batch went on drawing the old garment. Toggling the
# overlays off and on fixed exactly the ones whose toggle happens to call an
# invalidate() (Outline, Boundary Flags) and none of the others, which is why
# it looked half-broken rather than broken.
#
# The identity check below is what makes this safe to hang on `update`:
# Blender fires the callback on any assignment, and re-picking the SAME
# object must not throw away a valid multi-second analysis.

_last_inputs = {"guide": None, "retopo": None, "flat_sk": None}
_in_input_update = False

# On the Guide object: the Flat SK it was last used with. Swapping between
# five garments means re-picking the Guide five times a session, and the Flat
# SK name is the one input that cannot survive a swap (it names a key on the
# OTHER object), so it is remembered here instead of asked for again.
FLAT_SK_PROP = "ac9_flat_sk"


def _clear_status(top, guide_too: bool = True):
    """Drop the panels' result lines. They report a run against the inputs
    that were picked at the time, so after a swap they describe a different
    garment — a stale verdict on screen is worse than a blank one."""
    top.status_boundary = ""
    top.status_faces = ""
    top.status_maps = ""
    if guide_too:
        top.status_guide = ""


def _input_key(obj):
    """Identity of a picked object for change detection: None, or (name, mesh
    session_uid). The mesh uid alone is not enough — two objects can share one
    mesh and the overlays are drawn in world space, so the object matters —
    and the name alone is not enough either, since a mesh swap keeps it."""
    if obj is None or obj.type != 'MESH':
        return None
    return (obj.name, obj.data.session_uid)


def _seed_input_keys(scene=None):
    """Record what the Guide / Retopo pointers currently point at WITHOUT
    resetting anything.

    Needed because the change detection is session state, not file state: a
    file that opens with both pointers already set has no pending pick, and
    without this the user's first re-pick of the SAME Guide would read as a
    change and throw away a multi-second analysis they had just run.
    """
    if scene is None:
        scene = getattr(bpy.context, "scene", None)
    top = getattr(scene, "ac9_cloth_retopo", None) if scene is not None else None
    if top is None:
        _last_inputs["guide"] = None
        _last_inputs["retopo"] = None
        return
    _last_inputs["guide"] = _input_key(top.guide_obj)
    _last_inputs["retopo"] = _input_key(top.retopo_obj)
    _last_inputs["flat_sk"] = top.guide_flat_shapekey


@bpy.app.handlers.persistent
def _load_post_seed(_dummy):
    # Session uids are per-session, so the keys from the previous file are
    # meaningless now. The overlay caches are emptied by each tool's own
    # load_post handler, so there is nothing to reset here — only to re-seed.
    _seed_input_keys()


def _on_guide_changed(self, context):
    """A different Guide (or Flat SK) was picked — drop every cached result."""
    global _in_input_update
    if _in_input_update:
        return
    key = _input_key(self.guide_obj)
    if key == _last_inputs["guide"]:
        return
    _last_inputs["guide"] = key

    scene = getattr(context, "scene", None)
    uv_seam_guide.gpu_overlay.reset_guide_derived(scene)
    clo_projector.gpu_overlay.reset_guide_derived()
    clo_projector.selection_overlay.invalidate_selection()
    clo_projector.mirror.mark_preview_dirty(self.retopo_obj)
    # The projector's triangle/BVH cache and the symmetry/twin memo caches are
    # keyed on the mesh's session_uid, so the new Guide cannot read the old
    # one's entries and they are deliberately NOT flushed here: swapping back
    # then costs nothing instead of another multi-second re-detect.

    # A Flat SK name from the previous Guide almost never exists on the new
    # one, and every operator would fail with "Flat ShapeKey 'X' not found"
    # while the panel still looked ready. Restore the key THIS Guide was last
    # used with (remembered on the object), and fall back to blank so the
    # Setup panel asks for it again (the dropdown lists the new Guide's keys).
    guide = self.guide_obj
    name = self.guide_flat_shapekey
    if guide is not None:
        keys = getattr(guide.data, "shape_keys", None)
        have = keys.key_blocks if keys is not None else None
        if not name or have is None or name not in have:
            remembered = guide.get(FLAT_SK_PROP)
            wanted = remembered if (remembered and have is not None
                                    and remembered in have) else ""
            if wanted != name:
                _in_input_update = True
                try:
                    self.guide_flat_shapekey = wanted
                finally:
                    _in_input_update = False
    _last_inputs["flat_sk"] = self.guide_flat_shapekey

    # One Preview Plane is shared by every garment while the map images are
    # per Guide, so without this the plane keeps showing the PREVIOUS
    # garment's map — the one failure mode here that is silent.
    try:
        bake_maps.preview.sync_preview_image(self.maps.preview_map, guide)
    except Exception as exc:
        print(f"[AC9 Cloth Retopo] preview re-sync skipped: {exc}")
    _clear_status(self)
    guide_name = guide.name if guide is not None else "None"
    print(f"[AC9 Cloth Retopo] Guide -> '{guide_name}': overlays and Guide "
          f"analysis reset. Run Analyze Guide for the new one.")
    _tag_redraw_3d(self, context)


def _on_flat_sk_changed(self, context):
    """The Flat SK names the space every seam pair, ghost and anchor lives in,
    so pointing it at a different key invalidates the same set as a Guide
    swap."""
    if _in_input_update:
        return
    # Same reason as the Guide's identity check: picking the same key out of
    # the dropdown again must not cost the analysis.
    if self.guide_flat_shapekey == _last_inputs["flat_sk"]:
        return
    _last_inputs["flat_sk"] = self.guide_flat_shapekey
    scene = getattr(context, "scene", None)
    uv_seam_guide.gpu_overlay.reset_guide_derived(scene)
    clo_projector.gpu_overlay.reset_guide_derived()
    clo_projector.mirror.mark_preview_dirty(self.retopo_obj)
    # Remember it for this Guide, so coming back to this garment does not ask
    # for the key again (see FLAT_SK_PROP).
    guide = self.guide_obj
    if guide is not None and self.guide_flat_shapekey:
        guide[FLAT_SK_PROP] = self.guide_flat_shapekey
    _clear_status(self)
    print(f"[AC9 Cloth Retopo] Flat SK -> '{self.guide_flat_shapekey}': "
          f"overlays and Guide analysis reset.")
    _tag_redraw_3d(self, context)


def _on_retopo_changed(self, context):
    """A different Retopo was picked. The Guide analysis stands (a new Retopo
    against the same Guide is routine); everything read off the retopo mesh
    goes."""
    if _in_input_update:
        return
    key = _input_key(self.retopo_obj)
    if key == _last_inputs["retopo"]:
        return
    _last_inputs["retopo"] = key

    scene = getattr(context, "scene", None)
    uv_seam_guide.gpu_overlay.reset_retopo_derived(scene)
    clo_projector.gpu_overlay.reset_retopo_derived()
    clo_projector.selection_overlay.invalidate_selection()

    # Corners and Pins are attributes ON the retopo mesh, so the new object
    # can be read straight away rather than left blank until the user finds
    # the refresh button.
    retopo = self.retopo_obj
    if retopo is not None and retopo.type == 'MESH':
        try:
            from .uv_seam_guide import density_pin as _dp
            from .uv_seam_guide import topology_corner as _tc
            ov = uv_seam_guide.gpu_overlay
            ov._cache["topo_corners"] = _tc.positions(retopo)
            ov._cache["topo_corner_dirty"] = True
            ov._cache["density_pins"] = _dp.positions(retopo)
            ov._cache["density_pin_dirty"] = True
        except Exception as exc:
            print(f"[AC9 Cloth Retopo] Corner/Pin overlay refresh skipped: {exc}")
    # Same reasoning as the Guide swap, minus the Guide's own result line:
    # the analysis it reports still stands for the new retopo.
    _clear_status(self, guide_too=False)
    retopo_name = retopo.name if retopo is not None else "None"
    print(f"[AC9 Cloth Retopo] Retopo -> '{retopo_name}': ghost / status "
          f"overlays reset (the Guide analysis is kept).")
    _tag_redraw_3d(self, context)


def _update_guide_3d_source(self, context):
    """'3D Source' changed: drop the cached Guide triangles and show the
    matching ShapeKey when the Guide is in 3D. The Mirror / projections are
    refreshed by the user's next Refresh (they read through the cache)."""
    guide = self.guide_obj
    if guide is None or guide.type != 'MESH':
        return
    clo_projector.core.invalidate_guide_cache(guide)
    clo_projector.mirror.mark_preview_dirty(self.retopo_obj)
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
        update=_on_retopo_changed,
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
        update=_on_guide_changed,
    )
    guide_flat_shapekey: StringProperty(
        name="Flat SK",
        description=(
            "Name of the ShapeKey that represents the UV-flattened (2D) layout. "
            "Example: 'UV_Map_Flattened'"
        ),
        default="",
        update=_on_flat_sk_changed,
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

    # (The Object/Edit halves of Boundary and Faces used to swap places with
    # the Retopo's mode, the inactive half folded behind a disclosure
    # triangle. Both halves are now always drawn, in the same order, with the
    # inactive one greyed out — a button that moves cannot be learned by
    # position, which is what a tool panel is read by. The two disclosure
    # properties that state lived in are gone; an old .blend simply carries
    # two unread keys.)

    seam: PointerProperty(type=uv_seam_guide.properties.AC9SeamGuideProps)
    proj: PointerProperty(type=clo_projector.properties.AC9CloProjectorProps)
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
    bl_order       = 0          # first; Guide Prep (clo_cleanup) is 1

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

        # An all-triangle Guide is a hard requirement of the projector, and
        # nothing in 2D needs it — so a non-triangle Guide used to go
        # unnoticed through a whole retopo and only surface as an error on
        # the first 3D Mirror. Said here instead, where the Guide is picked.
        # guide_all_tris is O(1); a per-polygon scan in draw() would not be.
        if guide.type == 'MESH' and not clo_projector.guide.guide_all_tris(guide.data):
            ui_common.draw_blocker(
                layout, "Guide is not all triangles (3D Mirror will fail)",
                "ac9_cloth.triangulate_guide", "Fix", 'MOD_TRIANGULATE',
            )

        # A vertex with no position (NaN) makes every analysis fail somewhere
        # deep instead of here — see guide.nonfinite_vertex_count, whose
        # census is memoised precisely so this line can run in a draw().
        n_nan = clo_projector.guide.nonfinite_vertex_count(
            guide, top.guide_flat_shapekey)
        if n_nan:
            ui_common.draw_blocker(
                layout,
                f"Guide has {n_nan} vertex(es) with no valid position (NaN)",
                "ac9_cloth.repair_guide_nonfinite", "Repair", 'MODIFIER',
            )

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
    # Preview Fill came out of Experimental on 2026-09-09 (used in the rhythm
    # of the work). It needs shapely, which now ships as a wheel - say so
    # here rather than only on the press, for the platforms PyPI has no wheel
    # for (windows-arm64) and for a wheel that failed to install.
    if not ui_common.shapely_available():
        ui_common.draw_blocker(
            col, "Preview Fill needs shapely (ships with the add-on; not "
                 "available for this Blender)")
    else:
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
    # The × belongs to the ROW, not to Grid: Connect's own n-gons are
    # scaffold faces and Clear Regions is what takes them off. It used to sit
    # inside the Experimental branch above, so moving Grid behind that gate
    # (2026-09-09) hid the only way to undo a Connect short of Ctrl+Z.
    row.operator("ac9_cloth.clear_patch_grid", text="", icon='X')
    clo_projector.ui.draw_faces_object_tools(col, context, top)


def _draw_faces_edit_tools(layout, context, top):
    """Faces, Edit Mode: regions on the selected islands, rungs between two
    selected lines, UV-keeping collapse and non-planar quad repair."""
    col = layout.column(align=True)
    # Same row as the Object Mode half, but scoped: in Edit Mode Auto Fill
    # and × work on the islands holding a selection, in place.
    if not ui_common.shapely_available():
        ui_common.draw_blocker(
            col, "Preview Fill needs shapely (ships with the add-on; not "
                 "available for this Blender)")
    else:
        row = ui_common.labeled_row(col, "Preview")
        ui_common.draw_make_clear(row, "ac9_cloth.preview_fill",
                                  "ac9_cloth.clear_preview_fill",
                                  "Auto Fill", icon='MESH_GRID')
        ui_common.draw_hint(col, "Auto Fill: selected islands only")
    row = ui_common.labeled_row(col, "Regions")
    row.scale_y = 1.2
    row.operator("ac9_cloth.connect_loose_ends", text="Connect",
                 icon='SNAP_MIDPOINT').extend_selected = False
    if ui_common.experimental_enabled(context):
        row.operator("ac9_cloth.grid_regions", text="Grid", icon='MESH_GRID')
    # See the Object Mode half: the × takes Connect's scaffold n-gons off
    # too, so it is not Grid's button and must not be behind Grid's gate.
    row.operator("ac9_cloth.clear_patch_grid", text="", icon='X')
    ui_common.draw_hint(col, "Regions / × : selected islands only")
    rows = ui_common.labeled_row(col, "Rows")
    rows.scale_y = 1.2
    rows.operator("ac9_cloth.connect_rows", text="Connect Rows",
                  icon='MOD_LATTICE')
    ui_common.draw_hint(col, "Select two lines (edges); rungs cut through "
                             "the faces between them")
    if ui_common.experimental_enabled(context):
        mesh_edit.ui.draw_mesh_edit_tools(layout, context, top.mesh_edit)
        quad_fix.ui.draw_quad_fix_tools(layout, context, top.quad_fix)


class AC9_PT_Faces(bpy.types.Panel):
    """Filling and refining the 2D interior once the boundary exists.

    Composes tools from four sub-packages (uv_seam_guide's Fill Regions,
    clo_projector's Subdivide, mesh_edit, quad_fix): one work
    phase, so one panel — the sub-package boundaries are not the user's.

    Both mode halves are always drawn in the same order, the one that does
    not match the Retopo's mode greyed out (see AC9_PT_Boundary for why the
    swapping/folding version was dropped).
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

        mode = ui_common.retopo_mode(top)

        obj = layout.column()
        obj.enabled = mode == 'OBJECT'
        obj.label(text="Object Mode tools")
        _draw_faces_object_tools(obj, context, top)

        layout.separator()
        edit = layout.column()
        edit.enabled = mode == 'EDIT'
        edit.label(text="Edit Mode tools")
        _draw_faces_edit_tools(edit, context, top)

        # Twin / Self belong to NEITHER half: they read the selected vertex in
        # either mode (their invoke reads the edit-mesh selection when the
        # retopo is in Edit Mode). While the halves swapped and only one was
        # visible, being drawn in both was how they stayed reachable; with
        # both halves permanently on screen that put them on the panel twice
        # (reported from the 5.2 test round). One row, outside the halves, so
        # neither greys it out.
        layout.separator()
        uv_seam_guide.ui.draw_symmetry_rebuild_tools(layout.column(align=True),
                                                     top.seam)

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
    # Before register_class: every operator's execute is timed, so a tool that
    # makes the user wait without showing progress reports itself instead of
    # waiting to be noticed (ui_common, "Progress audit").
    ui_common.audit_wrap_operators(_classes)
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ac9_cloth_retopo = PointerProperty(type=AC9ClothRetopoProps)
    uv_seam_guide.register_extras()
    clo_projector.register_extras()
    bake_maps.register_extras()
    guide_separate.register_extras()
    quad_fix.register_extras()
    mesh_edit.register_extras()
    clo_cleanup.register_extras()
    bpy.types.VIEW3D_HT_header.append(_draw_view3d_header_extras)
    if _load_post_seed not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post_seed)
    _seed_input_keys()
    # After the classes: the catalogue keys off the labels they carry.
    translations.register()


def unregister():
    # First: stop translating before anything is torn down.
    translations.unregister()
    while _load_post_seed in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post_seed)
    try:
        bpy.types.VIEW3D_HT_header.remove(_draw_view3d_header_extras)
    except Exception:
        pass
    clo_cleanup.unregister_extras()
    mesh_edit.unregister_extras()
    quad_fix.unregister_extras()
    guide_separate.unregister_extras()
    bake_maps.unregister_extras()
    clo_projector.unregister_extras()
    uv_seam_guide.unregister_extras()
    if hasattr(bpy.types.Scene, "ac9_cloth_retopo"):
        del bpy.types.Scene.ac9_cloth_retopo
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    ui_common.audit_unwrap_operators()


if __name__ == "__main__":
    register()
