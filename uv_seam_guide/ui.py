"""UV Seam Guide's share of the AC9 Cloth Retopo sidebar tab.

Owns the Boundary panel (+ Boundary Settings) and the Analysis Settings child
of the root Setup panel, and exposes draw functions the root panels compose:

  draw_analysis_state                        -> Setup panel (Analyze Guide
                                                button is the root's; the
                                                one-at-a-time rows,
                                                draw_analysis_rows, sit in
                                                Analysis Settings)
  draw_advanced                              -> Advanced panel

Boundary is split by what the operators' poll() already enforces: whole-mesh
operations run in Object Mode, selection-driven ones in Edit Mode. The panel
shows the half that matches the Retopo's mode and folds the other half into
one line.

Everything is drawn with the shared helpers in ../ui_common.py: one action per
labelled row, dry-run beside apply, make beside clear. Tuning values live in
the "... Settings" child panels (set once, rarely touched); explanations live
in the operators' bl_description; the only text drawn here is an actionable
warning or the panel's single result line.

Display toggles and cosmetic colours are NOT here — they live in the Overlays
panel (see ../overlays.py).
"""

import bpy

from .. import ui_common as uic
from .gpu_overlay import _cache


# ── Setup panel contributions ────────────────────────────────────────────────

def draw_analysis_rows(layout, context):
    """The three Guide analyses. Read-only on the Guide, nothing touches the retopo.

    Folds and twins are detected by one button (Analyze Symmetry) but keep
    separate tolerances in Analysis Settings: normalised the same way, yet
    merging them without re-verifying on darted panels risks the twin test
    over- or under-firing.
    """
    col = layout.column(align=True)
    row = uic.labeled_row(col, "Seams")
    uic.draw_make_clear(row, "ac9_cloth.analyze_uv_seam_pairs",
                        "ac9_cloth.clear_uv_seam_guides",
                        "Analyze", icon='UV_DATA')
    row = uic.labeled_row(col, "Symmetry")
    uic.draw_make_clear(row, "ac9_cloth.analyze_guide",
                        "ac9_cloth.clear_guide_analysis",
                        "Analyze", icon='MOD_MIRROR')
    row = uic.labeled_row(col, "Anchors")
    row.scale_y = 1.2
    row.operator("ac9_cloth.analyze_anchors", text="Analyze",
                 icon='DECORATE_KEYFRAME')


def draw_analysis_state(layout, context):
    """What is analyzed right now — or the one warning when nothing is.

    The seam analysis is the foundation every overlay, ghost and snap reads,
    and it is emptied on file open and on Reload Scripts. The fix button sits
    two rows up, so the warning carries none of its own.
    """
    if not _cache["pairs"]:
        uic.draw_blocker(layout, "Seams not analyzed")
        return
    draw_analysis_readout(layout, context)


def draw_analysis_readout(layout, context):
    """One dimmed line: what is currently analyzed and how much of it."""
    scene = context.scene
    parts = []
    pair_count = scene.get("ac9_cloth_retopo_seam_pair_count")
    if pair_count is not None:
        parts.append(f"Seams {pair_count}")
    if _cache["anchors"]:
        parts.append(f"Anchors {len(_cache['anchors'])}")
    ghost_count = scene.get("ac9_cloth_retopo_ghost_count")
    if ghost_count is not None:
        parts.append(f"Ghosts {ghost_count}")
    sym_count = scene.get("ac9_cloth_retopo_symmetry_count")
    if sym_count is not None:
        parts.append(f"Folds {sym_count}")
    mirror_count = scene.get("ac9_cloth_retopo_mirror_pair_count")
    mirror_tested = scene.get("ac9_cloth_retopo_mirror_pair_tested")
    if mirror_count is not None:
        parts.append(f"Twins {mirror_count} / {mirror_tested}")
    uic.draw_status(layout, "  ·  ".join(parts))


def draw_symmetry_details(layout):
    """Per-pair twin listing and the reasons islands did not pair.

    Diagnostic detail, not workflow: shown where the user goes looking for it
    rather than on the analysis panel itself.
    """
    mirror_pairs = _cache.get("mirror_pairs") or []
    rejections = _cache.get("mirror_pair_rejections") or []
    if not mirror_pairs and not rejections:
        uic.draw_status(layout, "No twin analysis loaded")
        return
    if mirror_pairs:
        col = layout.column(align=True)
        col.label(text=f"{len(mirror_pairs)} twin pair(s) on the Guide:",
                  icon='INFO')
        for p in mirror_pairs:
            # verts_a/verts_b are GUIDE vertex counts, not the retopo's —
            # whether THAT matches is what Diagnose Twin Mapping reports.
            col.label(
                text=f"  guide {len(p['verts_a'])}v ↔ {len(p['verts_b'])}v  "
                     f"rms {p['rms']:.4f}",
                icon='DOT')
            if p["same_handed"]:
                col.label(text="    same-handed: a duplicate, not a reflection?",
                          icon='QUESTION')
    if rejections:
        reason_counts = {}
        for r in rejections:
            reason_counts[r["reason"]] = reason_counts.get(r["reason"], 0) + 1
        col = layout.column(align=True)
        col.label(text="Unpaired islands:", icon='INFO')
        labels = {
            "too_few_boundary_verts": "too small to test",
            "no_mutual_match": "no matching partner found",
        }
        for reason, count in reason_counts.items():
            col.label(text=f"  {count} × {labels.get(reason, reason)}")


class AC9_PT_AnalysisSettings(bpy.types.Panel):
    """Tuning values for the analyses in Setup — collapsed by default since
    these are set once per Guide and rarely touched again.
    """
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "AC9 Cloth Retopo"
    bl_label       = "Analysis Settings"
    bl_parent_id   = "AC9_PT_Setup"
    bl_options     = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        top = context.scene.ac9_cloth_retopo
        props = top.seam

        if not uic.guide_ready(layout, top):
            return

        # The three analyses one at a time (Setup's Analyze Guide runs all).
        draw_analysis_rows(layout, context)
        layout.separator()

        col = layout.column(align=True)
        col.label(text="Analyze Seams", icon='UV_DATA')
        col.prop(props, "precision")
        col.prop(props, "match_distance_3d")
        row = col.row(align=True)
        row.prop(props, "use_marked_seams", toggle=True, icon='MOD_EDGESPLIT')
        sub = col.row()
        sub.enabled = props.use_marked_seams
        sub.prop(props, "marked_project_distance")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Ghosts", icon='GHOST_ENABLED')
        col.prop(props, "max_distance_uv")
        col.prop(props, "bond_distance")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Analyze Symmetry", icon='MOD_MIRROR')
        col.prop(props, "symmetry_tolerance")
        col.prop(props, "mirror_pair_tolerance")


# ── Boundary panel: the two halves ───────────────────────────────────────────
# Split by what the operators' poll() already enforces: whole-mesh operations
# run in Object Mode, selection-driven ones in Edit Mode. Kept as two draw
# functions so the panel can show the relevant half first.

def draw_boundary_object_tools(layout, context, props):
    """Whole-mesh operations (Object Mode): generate, verify, preview.

    No hidden prerequisite here: Generate and Seam Status recompute the
    Guide's spans (and Generate its folds) on every run, so they do not
    depend on the cached analyses.

    Match is NOT here any more: it needs the side you trust to be selected,
    which is an Edit Mode idea (see AC9_OT_MatchSeams.poll).
    """
    col = layout.column(align=True)
    row = uic.labeled_row(col, "Boundary")
    row.scale_y = 1.2
    row.operator("ac9_cloth.generate_seam_chain", text="Generate", icon='ADD')
    row = uic.labeled_row(col, "Verify")
    row.scale_y = 1.2
    op = row.operator("ac9_cloth.seam_status_report", text="Status",
                      icon='CHECKMARK')
    op.select_problems = True



def draw_symmetry_rebuild_tools(layout, props):
    """Rebuild one side of a symmetric panel from the other: Twin = from the
    partner island (Replace Twin Island), Self = from the island's own fold
    axis (Symmetrize Island). Both destructive; both re-detect the Guide's
    symmetry on execute, so no prerequisite. The F3 names stay long.

    Lives in Faces (composed from __init__.py), not Boundary — like Fill/
    Grid Regions, this rebuilds FACES, not just the outline.

    `props` is the seam sub-props: Self's fold axis is a setting that has to
    be visible BEFORE clicking (a waistband is symmetric both ways, and the
    wrong axis is a destructive no-op), so it is a scene property drawn on
    its own row and copied onto the operator here.
    """
    col = layout.column(align=True)
    row = uic.labeled_row(col, "Symmetry")
    row.scale_y = 1.2
    row.operator("ac9_cloth.replace_mirror_island", text="Twin",
                 icon='MOD_MIRROR')
    op = row.operator("ac9_cloth.symmetrize_island", text="Self",
                      icon='MOD_MIRROR')
    op.axis = props.self_axis
    row = uic.labeled_row(col, "Self axis")
    row.prop(props, "self_axis", text="")


def draw_boundary_edit_tools(layout, context, props):
    """Selection-driven operations (Edit Mode): match, snapping — and, behind
    Experimental, the Density family.

    Density (+-1 / Count / Spacing), Even Out, Pins and Corners are
    Experimental since 2026-09-09: unused through the production run, and
    closed (their two attributes are read by span_density alone), so they are
    shown only when Experimental is on rather than deleted.
    """
    if uic.experimental_enabled(context):
        resample = "ac9_cloth.resample_span_density"
        col = layout.column(align=True)
        row = uic.labeled_row(col, "Density")
        row.scale_y = 1.2
        op = row.operator(resample, text="− 1", icon='REMOVE')
        op.mode = 'DELTA'
        op.delta = -1
        op = row.operator(resample, text="+ 1", icon='ADD')
        op.mode = 'DELTA'
        op.delta = 1
        row.operator("ac9_cloth.even_out_span_density", text="Even Out",
                     icon='MOD_LENGTH')
        row = uic.labeled_row(col, "Count")
        row.prop(props, "dens_count", text="")
        op = row.operator(resample, text="Set")
        op.mode = 'COUNT'
        row = uic.labeled_row(col, "Spacing")
        row.prop(props, "dens_spacing_mm", text="")
        op = row.operator(resample, text="Apply")
        op.mode = 'SPACING'

    col = layout.column(align=True)
    row = uic.labeled_row(col, "Match")
    row.scale_y = 1.2
    # One button, and it does the thing. The dry run used to sit beside it as
    # "Check", which read as a second checker next to Verify -> Status while
    # the only thing it added was the count before committing — of an
    # add-only operation that Ctrl+Z undoes, and whose skipped seams are
    # reported by the real run too. `apply` is still on the operator, so F3
    # and scripts can dry-run it.
    row.operator("ac9_cloth.match_seams", text="Match",
                 icon='ADD').apply = True
    if uic.experimental_enabled(context):
        uic.draw_mark(col, "Pin", "ac9_cloth.mark_density_pin",
                      "ac9_cloth.clear_density_pin", "all_pins")
        uic.draw_mark(col, "Corner", "ac9_cloth.mark_topology_corner",
                      "ac9_cloth.clear_topology_corner", "all_corners",
                      detect_id="ac9_cloth.detect_corner_candidates")

    # Ghosts are what Snap and Force Bond below actually read, so the refresh
    # sits with them rather than in Advanced. Refresh was reachable only from
    # the Overlays panel's heading icon and from the "No ghosts" blocker below
    # — and that blocker hides itself as soon as any ghosts exist, which is
    # exactly when they have gone stale and need this button.
    col = layout.column(align=True)
    row = uic.labeled_row(col, "Ghosts")
    row.operator("ac9_cloth.refresh_ghosts", text="Refresh",
                 icon='FILE_REFRESH')
    row.prop(props, "live_ghost_update", text="Live", toggle=True, icon='TEMP')

    # Two mutually-exclusive snap modes; both fire on G in Edit Mode.
    row = uic.labeled_row(col, "Snap (G)")
    row.prop(props, "ghost_snap_mode", text="Ghost", toggle=True,
             icon='GHOST_ENABLED')
    row.prop(props, "uv_snap_mode", text="Outline", toggle=True,
             icon='SNAP_EDGE')
    if props.ghost_snap_mode or props.uv_snap_mode:
        row = uic.labeled_row(col, "")
        row.prop(props, "snap_distance", text="Distance")
        if props.uv_snap_mode and _cache["symmetry_segments"]:
            row.prop(props, "snap_to_fold", text="Fold", toggle=True,
                     icon='MOD_MIRROR')
        if props.uv_snap_mode and _cache["crease_segments"]:
            row.prop(props, "snap_to_crease", text="Crease", toggle=True,
                     icon='MOD_SIMPLEDEFORM')
    row = uic.labeled_row(col, "Bond")
    row.enabled = bool(_cache["ghost_results"])
    row.operator("ac9_cloth.ghost_force_bond", text="Force Bond",
                 icon='SNAP_ON')
    # Force Bond pulls within Snap Distance. When neither snap mode is on the
    # Snap row above hides that value, so show it here — otherwise a vertex a
    # few mm off its ghost is skipped with no visible reason.
    if not (props.ghost_snap_mode or props.uv_snap_mode):
        row.prop(props, "snap_distance", text="")

    # Hard prerequisites of the snap modes, with the fix on the same row.
    if props.ghost_snap_mode and not _cache["ghost_results"]:
        uic.draw_blocker(layout, "No ghosts",
                         "ac9_cloth.refresh_ghosts", "Refresh",
                         'FILE_REFRESH')
    if props.uv_snap_mode and not (_cache["boundary_segments"]
                                   or _cache["symmetry_segments"]):
        uic.draw_blocker(layout, "Seams not analyzed",
                         "ac9_cloth.analyze_uv_seam_pairs", "Analyze",
                         'UV_DATA')


class AC9_PT_Boundary(bpy.types.Panel):
    """The retopo's 2D boundary. Both halves are always drawn in the same
    order — Object Mode tools, then Edit Mode tools — and the half that does
    not match the Retopo's current mode is greyed out.

    The halves used to swap places instead, the inactive one folded behind a
    disclosure triangle. It saved a few lines of height and cost the thing a
    tool panel is actually read by: a button always being in the same place.
    """
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "AC9 Cloth Retopo"
    bl_label       = "Boundary"
    bl_order       = 2

    def draw(self, context):
        layout = self.layout
        top = context.scene.ac9_cloth_retopo
        props = top.seam

        if not uic.guide_ready(layout, top):
            return
        if not uic.retopo_ready(layout, top):
            return

        mode = uic.retopo_mode(top)

        obj = layout.column()
        obj.enabled = mode == 'OBJECT'
        obj.label(text="Object Mode tools")
        draw_boundary_object_tools(obj, context, props)

        layout.separator()
        edit = layout.column()
        edit.enabled = mode == 'EDIT'
        edit.label(text="Edit Mode tools")
        draw_boundary_edit_tools(edit, context, props)

        uic.draw_status(layout, top.status_boundary)


class AC9_PT_BoundarySettings(bpy.types.Panel):
    """Tuning values for the Boundary panel — set once, rarely touched."""
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "AC9 Cloth Retopo"
    bl_label       = "Boundary Settings"
    bl_parent_id   = "AC9_PT_Boundary"
    bl_options     = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        top = context.scene.ac9_cloth_retopo
        props = top.seam

        col = layout.column(align=True)
        col.label(text="Generate", icon='ADD')
        col.prop(props, "gen_targets", text="")
        col.prop(props, "gen_scope", text="")
        col.prop(props, "gen_divide_by", text="")
        if props.gen_divide_by == 'SPACING':
            col.prop(props, "gen_spacing_mm")
            col.prop(props, "gen_straight_tol_mm")
        else:
            col.prop(props, "gen_count")
        col.prop(props, "gen_min_seam_mm")
        col.prop(props, "gen_corner_angle_deg")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Match", icon='MOD_LENGTH')
        col.prop(props, "retopo_sync_tol")

        # Corners belongs to the Density family (Experimental since
        # 2026-09-09): no knob for a feature with no button.
        if uic.experimental_enabled(context):
            layout.separator()
            col = layout.column(align=True)
            col.label(text="Corners", icon='PIVOT_CURSOR')
            col.prop(props, "topo_corner_detect_deg")



# ── Advanced panel contribution ──────────────────────────────────────────────

def draw_advanced(layout, context):
    """Diagnostics and rarely-used switches. Composed into the root Advanced
    panel. (Grid Regions moved to the Faces panel — it is Fill Regions'
    working successor, not the abandoned automatic interior grid B-28
    dropped; it had been miscategorized here alongside genuinely dead
    experiments.)
    """
    top = context.scene.ac9_cloth_retopo
    props = top.seam

    col = layout.column(align=True)
    col.label(text="Diagnostics", icon='VIEWZOOM')
    row = uic.labeled_row(col, "Seam")
    row.operator("ac9_cloth.inspect_seam_vertex", text="Vertex")
    row.operator("ac9_cloth.diagnose_seam_spaces", text="Spaces")
    row = uic.labeled_row(col, "Twins")
    row.operator("ac9_cloth.mirror_pair_retopo_map", text="Mapping")
    draw_symmetry_details(col)


