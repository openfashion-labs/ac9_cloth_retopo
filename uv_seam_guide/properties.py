"""Scene sub-property group for the UV Seam Guide tool.

Registered as a nested PointerProperty on AC9ClothRetopoProps.seam.
Does NOT contain retopo_obj — that is shared at the top level.

The Guide object and its Flat ShapeKey are NOT stored here: they live at the
top level (top.guide_obj / top.guide_flat_shapekey), shared with the CLO
Projector.  Seam pairs and retopo verts therefore live in one common
coordinate space (FlattenUV flat layout, in world space) — there is no scalar
mapping (no guide_scale, no raw-UV conversion).
"""

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import PropertyGroup

from .gpu_overlay import (
    COLOR_ITEMS,
    prop_bond_distance_changed,
    prop_dirty_anchors,
    prop_dirty_both,
    prop_dirty_density_pins,
    prop_dirty_topo_corners,
    prop_dirty_ghost,
    prop_live_update_changed,
    prop_redraw,
    prop_snap_distance_changed,
)


def _ghost_snap_changed(self, context):
    # Only one snap mode active at a time (both bind G).
    if self.ghost_snap_mode and self.uv_snap_mode:
        self.uv_snap_mode = False


def _uv_snap_changed(self, context):
    if self.uv_snap_mode and self.ghost_snap_mode:
        self.ghost_snap_mode = False


class AC9SeamGuideProps(PropertyGroup):
    # ── Overlay placement ─────────────────────────────────────
    # NOTE: spatial props use unit='LENGTH' so Blender shows them as mm/cm and
    # gives sensible drag sensitivity in the FlattenUV 0–1 m flat space (raw
    # 0.01-style values were painfully fiddly to scrub).
    z_offset: FloatProperty(
        name="Z Offset",
        description="Z height of the seam/ghost overlay above the flat layout plane",
        default=0.0, min=-1000.0, max=1000.0,
        unit='LENGTH', precision=4,
        update=prop_dirty_both,
    )
    precision: FloatProperty(
        name="Merge Precision",
        description="Decimal places for flat-coordinate duplicate detection",
        default=6.0, min=1.0, max=10.0,
    )
    match_distance_3d: FloatProperty(
        name="3D Match Distance",
        description=(
            "Maximum 3D (Basis) distance for two boundary edges to count as a "
            "sewn pair. Keep this small (1-3 mm) — it is for near-coincident "
            "split seams only. For layered seams (a pocket sewn onto a body "
            "panel, gap varies with drape) use Mark Sharp on the guide instead. "
            "0 = exact position matching — the right setting when CLO exported "
            "split seams perfectly coincident (typical); raising it can pick up "
            "folded ribs/hems whose two layers nearly touch"
        ),
        default=0.0, min=0.0, max=0.1,
        unit='LENGTH', precision=4,
    )
    use_marked_seams: BoolProperty(
        name="Marked Seams (Sharp)",
        description=(
            "Project edges marked Sharp on the GUIDE mesh onto the nearest "
            "other panel and show them as seam pairs. Mark a pocket outline "
            "with Mark Sharp to register layered seams explicitly. Turn OFF if "
            "the guide came in with stray Sharp edges (some importers mark "
            "hard edges) and unexpected guides appear. The Sharp rows CLO "
            "Cleanup's insets build are ignored"
        ),
        default=True,
    )
    marked_project_distance: FloatProperty(
        name="Marked Seam Distance",
        description=(
            "Max projection distance for Mark Sharp seam edges. Edges marked "
            "Sharp on the GUIDE mesh (e.g. a pocket outline) are projected onto "
            "the nearest other panel within this distance and shown as seam "
            "pairs — the explicit way to register layered seams that 3D Match "
            "Distance cannot infer. Keep small (~1 cm): larger values project "
            "marked outlines onto unrelated panels that merely hang nearby in "
            "3D (a sleeve cuff next to a pocket)"
        ),
        default=0.01, min=0.0001, max=0.5,
        unit='LENGTH', precision=4,
    )

    # ── Seam guide display ────────────────────────────────────
    show_seam_guides: BoolProperty(
        name="Show Seams",
        description=(
            "Draw the sewn seam pairs found by Analyze Seams (cyan). The white "
            "Outline overlay shows the whole pattern outline including free edges"
        ),
        default=True,
        update=prop_redraw,
    )
    show_pair_lines: BoolProperty(
        name="Show Pair Lines",
        description="Draw connector lines between corresponding seam endpoints",
        default=True,
        update=prop_redraw,
    )
    show_free_edges: BoolProperty(
        name="Show Free Edges",
        description=(
            "Draw the pattern's FREE edges (yellow) — every boundary edge with "
            "no sewn partner: hems, necklines, openings, a collar's outer edge. "
            "Together with the cyan Seams these make up the whole pattern "
            "outline in the flat layout. Filled in by Analyze Seams"
        ),
        default=True,
        update=prop_redraw,
    )
    free_edge_color: EnumProperty(
        name="Free Edge Color",
        items=COLOR_ITEMS,
        default="YELLOW",
        update=prop_redraw,
    )
    guide_color: EnumProperty(
        name="Guide Color",
        items=COLOR_ITEMS,
        default="CYAN",
        update=prop_redraw,
    )
    guide_line_width: FloatProperty(
        name="Guide Line Width (px)",
        default=2.0, min=0.5, max=20.0,
        update=prop_redraw,
    )

    # ── Island symmetry (cut-on-fold fold lines) ─────────────
    symmetry_tolerance: FloatProperty(
        name="Symmetry Tolerance",
        description=(
            "How strict the bilateral-symmetry test is, as a normalised RMS "
            "(point-to-outline reflection error at the best-fit axis, ÷ island "
            "size — the actual pass/fail cutoff is 0.4x this value, tightened "
            "from the raw setting so the number keeps its old meaning for "
            "anyone with a value already dialled in). Lower = stricter. ~0.015 "
            "cleanly separates cut-on-fold panels (back body, waistband, "
            "plackets, cuffs) from genuinely asymmetric pieces (sleeves, "
            "front-opening panels), which are left/right TWINS rather "
            "than self-symmetric. Raise it to catch panels with darts; lower "
            "it to reject borderline shapes"
        ),
        default=0.015, min=0.0, max=0.2, precision=4,
    )
    show_symmetry_axis: BoolProperty(
        name="Show Fold Lines",
        description=(
            "Draw the detected fold (centre) line of each self-symmetric UV "
            "island. Run Detect Folds (or Analyze Symmetry) first"
        ),
        default=True,
        update=prop_redraw,
    )
    snap_to_fold: BoolProperty(
        name="Snap to Fold Lines",
        description=(
            "Include the detected fold (centre) lines as snap targets in Outline "
            "Snap, so the centre column of retopo verts can land cleanly on a "
            "cut-on-fold line. Fold lines sit at island centres, far from the "
            "outline, so this won't pull boundary verts off the pattern edge"
        ),
        default=True,
    )
    show_crease_lines: BoolProperty(
        name="Show Creases",
        description=(
            "Draw the fold lines tagged on the Guide by CLO Cleanup's Find "
            "Folds / Tag Selected Edges — the crease the Inset Line band was "
            "built on — in the flat layout, so a cut line can be laid on it. "
            "Different from Fold Lines, which are an island's symmetry axis. "
            "Filled in by Analyze Seams"
        ),
        default=True,
        update=prop_redraw,
    )
    snap_to_crease: BoolProperty(
        name="Snap to Creases",
        description=(
            "Include the Guide's tagged fold lines (Find Folds) as Outline "
            "Snap targets, so the retopo vertices of a cut land exactly on "
            "the crease"
        ),
        default=True,
    )
    crease_color: EnumProperty(
        name="Crease Color",
        items=COLOR_ITEMS,
        default="BLUE",
        update=prop_redraw,
    )
    symmetry_color: EnumProperty(
        name="Fold Line Color",
        items=COLOR_ITEMS,
        default="GREEN",
        update=prop_redraw,
    )
    symmetry_line_width: FloatProperty(
        name="Fold Line Width (px)",
        default=2.5, min=0.5, max=20.0,
        update=prop_redraw,
    )
    # The Self button's fold axis. Kept here rather than only on the operator
    # so the choice survives between clicks and shows next to the button; the
    # UI copies it onto the operator, whose own `axis` property is what the
    # F9 redo panel edits.
    self_axis: EnumProperty(
        name="Self Axis",
        description=(
            "Which fold axis Self mirrors across. A panel can be symmetric "
            "both ways (a waistband: left-right AND top-bottom), and only one "
            "of them is the copy you meant"
        ),
        items=[
            ('AUTO', "Auto",
             "Pick the fold that still has work: the side-to-side asymmetric "
             "one; refuses when both are equally asymmetric"),
            ('VERTICAL', "Vertical axis (mirror left-right)",
             "Fold along the panel's vertical centre line, so the left half "
             "is rebuilt from the right half (or the other way round)"),
            ('HORIZONTAL', "Horizontal axis (mirror top-bottom)",
             "Fold along the panel's horizontal centre line, so the bottom "
             "half is rebuilt from the top half (or the other way round)"),
        ],
        # Horizontal rather than Auto: most pattern pieces are near enough to
        # a rectangle that both folds look equally asymmetric, which is
        # exactly the case Auto refuses -- so Auto's default cost a refusal
        # and a second click on the common piece (2026-09-09, production).
        default='HORIZONTAL',
    )

    # ── Twins (cross-island left/right reflection detection) ──────────
    # Internal names keep the historical "mirror_pair" identifier so saved
    # .blend values survive; every user-facing string says "Twin".
    mirror_pair_tolerance: FloatProperty(
        name="Twin Tolerance",
        description=(
            "How strict the twin (cross-island reflection) test is, as a normalised RMS "
            "(outline mismatch ÷ larger island's bbox diagonal) in the flat "
            "pattern layout. Lower = stricter. Not a delicate setting: "
            "measured on a production jacket, true pairs came in at 0.000-"
            "0.007 and the closest false candidate at 0.047, so anything in "
            "between gives the same answer. Companion to Symmetry Tolerance "
            "above, which tests one island against ITSELF for a fold line; "
            "this tests two DIFFERENT islands against each other"
        ),
        default=0.02, min=0.0, max=0.2, precision=4,
    )
    show_mirror_pairs: BoolProperty(
        name="Show Twin Lines",
        description=(
            "Draw a connector between the Flat-SK centroids of each detected "
            "twin pair (e.g. left sleeve ↔ right sleeve). Run Detect Twins (or "
            "Analyze Symmetry) first"
        ),
        default=True,
        update=prop_redraw,
    )
    mirror_pair_color: EnumProperty(
        name="Twin Line Color",
        items=COLOR_ITEMS,
        default="MAGENTA",
        update=prop_redraw,
    )
    mirror_pair_line_width: FloatProperty(
        name="Twin Line Width (px)",
        default=2.0, min=0.5, max=20.0,
        update=prop_redraw,
    )

    # ── Ghost display ─────────────────────────────────────────
    max_distance_uv: FloatProperty(
        name="Max Seam Distance",
        description=(
            "Maximum distance, in the cloth's real dimensions, for a retopo "
            "vertex to be treated as sitting on a seam and given a ghost "
            "(converted into the flat layout the ghosts are drawn in)"
        ),
        default=0.02, min=0.000001, max=1.0,
        unit='LENGTH', precision=4,
    )
    show_ghost_points: BoolProperty(
        name="Show Ghost Points",
        description="Toggle ghost cross marker visibility",
        default=True,
        update=prop_redraw,
    )
    show_ghost_lines: BoolProperty(
        name="Show Ghost Lines",
        description=(
            "Draw lines from every retopo vertex to its opposite-side ghost. "
            "Off by default — the full set is cluttered; use 'Show Pair for "
            "Selected Only' to see just the connections you care about"
        ),
        default=False,
        update=prop_redraw,
    )
    ghost_cross_size: FloatProperty(
        name="Ghost Cross Size",
        description="Arm length of ghost cross markers (flat-layout world units)",
        default=0.01, min=0.0001, max=10.0,
        unit='LENGTH', precision=4,
        update=prop_dirty_ghost,
    )
    ghost_line_width: FloatProperty(
        name="Ghost Line Width (px)",
        default=2.0, min=0.5, max=20.0,
        update=prop_redraw,
    )
    bond_distance: FloatProperty(
        name="Bond Distance",
        description=(
            "How close a retopo vertex must be to a ghost to count as BONDED "
            "(placed → green, and hidden by Only Unplaced). In the cloth's "
            "real dimensions, converted into the flat layout. This is a "
            "CLASSIFICATION threshold only — keep it small (a few mm) so "
            "'placed' still means placed. The radius that actually pulls "
            "vertices, both for Ghost Snap on G and for Force Bond, is Snap "
            "Distance"
        ),
        default=0.001, min=0.0001, max=1.0,
        unit='LENGTH', precision=4,
        update=prop_bond_distance_changed,
    )
    show_only_unplaced: BoolProperty(
        name="Only Unplaced",
        description=(
            "Show only ghosts whose partner vertex isn't placed yet (the gaps "
            "you still need to fill). Hides 'placed' ghosts that already sit on "
            "an existing retopo vert. A vert counts as placed when it's within "
            "Bond Distance of the ghost. Turn OFF to also see placed ghosts "
            "(drawn in the Placed colour) for a full picture"
        ),
        default=False,
        update=prop_redraw,
    )
    show_orphan_rings: BoolProperty(
        name="Orphan Rings",
        description=(
            "Ring the retopo vertex that OWNS each unplaced ghost — the vertex "
            "whose counterpart is missing. The ghost cross itself marks the "
            "empty spot on the PARTNER panel, which in a split flat layout is "
            "a whole panel away and off screen at working zoom, so without "
            "this nothing warns you at the vertex you are actually looking at. "
            "Sewn seams only — a free edge's foot sits on the vertex's own "
            "outline, close enough that a ring would just blur into the cross. "
            "The ring says 'the slot opposite this vertex is empty' — it does "
            "not say this vertex is the wrong one; which side ends up unpaired "
            "is decided by nearest-neighbour matching within Bond Distance"
        ),
        default=True,
        update=prop_redraw,
    )
    ghost_lines_unplaced_only: BoolProperty(
        name="Lines: Unplaced Only",
        description=(
            "Draw the connector line only for unplaced ghosts — the ones that "
            "still need work. A placed ghost sits on a vertex that already "
            "exists, so its connector is pure confirmation, and on a finished "
            "panel those outnumber the unplaced ones by two orders of "
            "magnitude and bury them. Turn OFF to get a line for every ghost"
        ),
        default=True,
        update=prop_redraw,
    )
    ghost_color_unplaced: EnumProperty(
        name="Unplaced Ghost Color",
        items=COLOR_ITEMS,
        default="RED",
        update=prop_redraw,
    )
    ghost_color_placed: EnumProperty(
        name="Placed Ghost Color",
        items=COLOR_ITEMS,
        default="GREEN",
        update=prop_redraw,
    )
    ghost_line_color: EnumProperty(
        name="Ghost Line Color",
        items=COLOR_ITEMS,
        default="MAGENTA",
        update=prop_redraw,
    )

    # ── Selected-vertex pair preview ──────────────────────────
    show_selected_ghost: BoolProperty(
        name="Show Pair for Selected Only",
        description=(
            "Live preview: draw the opposite-side ghost + connector ONLY for "
            "SELECTED BOUNDARY verts in Edit Mode. Interior verts have no seam "
            "partner so they're ignored; boundary verts farther than Max Seam "
            "Distance from any seam (free edges / hems) are skipped too. Shows "
            "ALL partners, so an N-way junction (folded hem / pocket / 3+ panels "
            "meeting) draws every counterpart, not just the nearest. Updates as "
            "you select/move"
        ),
        default=False,
        update=prop_redraw,
    )
    show_seam_parity: BoolProperty(
        name="Show Seam Vertex Count",
        description=(
            "While SELECTED seam (boundary) verts are highlighted in Edit Mode, "
            "show the vertex count on this side (A) and on the partner side (B) "
            "as text at each seam location. Green when they match, orange + '≠' "
            "when they don't — so you instantly know how many verts to add or "
            "remove for a clean weld. B counts the retopo verts already placed "
            "near the partner seam (not the guide ghosts)"
        ),
        default=True,
        update=prop_redraw,
    )

    # ── Live update (confirm-time) ────────────────────────────
    live_ghost_update: BoolProperty(
        name="Live Ghost Update",
        description=(
            "Recompute the full ghost field automatically after a retopo edit "
            "is confirmed (e.g. you finish a move). Saves running Refresh Ghosts "
            "by hand. Updates on confirm, not continuously mid-drag"
        ),
        default=False,
        update=prop_live_update_changed,
    )

    # ── Ghost Snap Mode ───────────────────────────────────────
    ghost_snap_mode: BoolProperty(
        name="Ghost Snap Mode",
        description=(
            "When ON, pressing G in Edit Mode activates Ghost Snap Move — "
            "vertices snap only to ghost points"
        ),
        default=False,
        update=_ghost_snap_changed,
    )
    uv_snap_mode: BoolProperty(
        name="Outline Snap Mode",
        description=(
            "When ON, pressing G in Edit Mode activates Outline Snap Move — "
            "vertices snap to the nearest point on the FULL Guide pattern "
            "outline: sewn seams AND free edges (hems, necklines, openings), "
            "not just sewn seams. Use it to drop boundary verts cleanly onto "
            "the pattern outline during initial placement"
        ),
        default=False,
        update=_uv_snap_changed,
    )
    snap_distance: FloatProperty(
        name="Snap Distance",
        description=(
            "Maximum distance, in the cloth's real dimensions, for a moving "
            "vertex to snap to a ghost — this MOVES vertices, both for Ghost "
            "Snap on G and for Force Bond. Converted into the flat layout. "
            "Enable 'Show Snap Radius' to visualize this as circles around "
            "each ghost"
        ),
        default=0.01, min=0.0001, max=100.0,
        unit='LENGTH', precision=4,
        update=prop_snap_distance_changed,
    )
    show_snap_radius: BoolProperty(
        name="Show Snap Radius",
        description=(
            "Draw a circle of the Snap Distance radius around every ghost point. "
            "Helps judge whether a retopo vertex is within snapping range"
        ),
        default=False,
        update=prop_redraw,
    )

    # ── Seam vertex counts (anchor spans) ──────────────────────────────────
    show_seam_status: BoolProperty(
        name="Show Seam Status",
        description=(
            "Colour the Guide outline by the last Seam Status run.\n"
            "\n"
            "SEWN SEAMS\n"
            "• green = both sides authored and aligned\n"
            "• red = the two sides hold different numbers of vertices\n"
            "• purple = the same number, but out of line along the seam\n"
            "• orange = only one side done\n"
            "\n"
            "FREE EDGES — no partner side, so what is checked is whether\n"
            "the retopo sits on the outline\n"
            "• teal = on it\n"
            "• purple = more than 1 mm off\n"
            "\n"
            "Drawn OVER the Seams and Free Edges overlays, so those two go "
            "dim while this is on. Seams and runs with no retopo yet are "
            "left uncoloured. Run 'Seam Status' to fill it in"
        ),
        # OFF by default, and off in every preset but Status: this overlay
        # repaints the same edges the cyan/yellow seam overlays draw, so
        # having it on alongside them is the colour clash it was reported as.
        default=False,
        update=prop_redraw,
    )
    # Result strings of the boundary tools live on the top-level props
    # (status_boundary / status_faces): one line per panel, last writer wins.

    # ── Generating a seam from nothing ─────────────────────────────────────
    gen_scope: EnumProperty(
        name="Generate",
        description="Which seams without any retopo to lay down",
        items=[
            ('ALL', "All Empty Seams",
             "Every seam that has no retopo yet, in one go"),
            ('CURSOR', "Nearest to 3D Cursor",
             "Only the empty seam closest to the 3D cursor — for starting one "
             "seam at a time while working a panel"),
        ],
        default='ALL',
    )
    gen_targets: EnumProperty(
        name="What",
        description="Which parts of the pattern outline to lay down",
        items=[
            ('BOTH', "Seams + Free Edges",
             "Sewn seams and free edges alike — what it takes to close every "
             "panel boundary, which is the prerequisite for filling faces"),
            ('SEAMS', "Sewn Seams Only",
             "Only edges sewn to another panel"),
            ('FREE', "Free Edges Only",
             "Only hems, openings, necklines and the like — edges with no "
             "sewn partner"),
        ],
        default='BOTH',
    )
    gen_divide_by: EnumProperty(
        name="Divide By",
        items=[
            ('SPACING', "Spacing",
             "Pick the count per seam so vertices land roughly this far "
             "apart, keeping density even across seams of different lengths"),
            ('COUNT', "Count", "Give every seam the same number of vertices"),
        ],
        default='SPACING',
    )
    gen_spacing_mm: FloatProperty(
        name="Spacing (mm)",
        description=(
            "Roughly how far apart the generated vertices should sit, in mm of "
            "fabric measured along the garment's 3D surface. The interior "
            "fills convert it into the flat layout so both end up at the same "
            "fabric spacing whatever the UV packing"
        ),
        default=20.0, min=1.0, soft_max=100.0,
    )
    gen_straight_tol_mm: FloatProperty(
        name="Straight Tolerance (mm)",
        description=(
            "0 (the default) divides straight runs by Spacing like everything "
            "else. Raised, a stretch of the outline that stays within this "
            "distance of a straight line (both sides of a seam) is left "
            "undivided, so straight runs carry only their corners while curves "
            "still get the Spacing. In mm of fabric: the straightness test "
            "runs on the flat layout, whose scale depends on the UV packing, "
            "so the value is converted before use"
        ),
        default=0.0, min=0.0, soft_max=5.0, step=10, precision=1,
    )
    gen_count: IntProperty(
        name="Vertices",
        description="How many vertices per side, counting both anchors",
        default=8, min=2, soft_max=64,
    )
    gen_min_seam_mm: FloatProperty(
        name="Ignore Under (mm)",
        description=(
            "Skip seams shorter than this. Pattern corners produce a lot of "
            "millimetre-long seams that are not worth dividing"
        ),
        default=5.0, min=0.0, soft_max=50.0,
    )
    gen_corner_angle_deg: FloatProperty(
        name="Corner Angle",
        description=(
            "Pin a vertex wherever the pattern outline turns at least this "
            "much, so a right-angled hem stays square instead of being cut "
            "across. Measured in the flat layout: 0 is straight, 90 a right "
            "angle. Set to 0 to divide purely by spacing"
        ),
        default=45.0, min=0.0, max=179.0, soft_min=20.0, soft_max=90.0,
    )
    # ── Topology Corners ──────────────────────────────────────────────────
    show_topo_corners: BoolProperty(
        name="Topology Corners",
        description=(
            "Show the corners you pinned the edge flow to, as diamonds. These "
            "are yours — unlike anchors, nothing recomputes them"
        ),
        default=True,
        update=prop_redraw,
    )
    topo_corner_color: EnumProperty(
        name="Corner Color",
        items=COLOR_ITEMS,
        default="CYAN",
        update=prop_redraw,
    )
    topo_corner_size: FloatProperty(
        name="Corner Marker Size",
        description="Half-width of the topology-corner diamonds "
                    "(flat-layout world units)",
        default=0.007, min=0.0005, max=1.0,
        unit='LENGTH', precision=4,
        update=prop_dirty_topo_corners,
    )
    topo_corner_detect_deg: FloatProperty(
        name="Candidate Angle",
        description=(
            "Detect Candidates proposes a corner wherever the pattern outline "
            "turns at least this much. It only ever proposes — the set is "
            "yours to edit afterwards"
        ),
        default=45.0, min=1.0, max=179.0, soft_min=20.0, soft_max=90.0,
    )
    # ── Density Pins ──────────────────────────────────────────────────────
    show_density_pins: BoolProperty(
        name="Density Pins",
        description=(
            "Show the vertices pinned against Adjust Density, as squares. "
            "A pin (e.g. a knife-cut vertex added to follow a fabric "
            "wrinkle) survives a Count/Spacing change or a Step that would "
            "otherwise dissolve or slide it"
        ),
        default=True,
        update=prop_redraw,
    )
    density_pin_color: EnumProperty(
        name="Pin Color",
        items=COLOR_ITEMS,
        default="WHITE",
        update=prop_redraw,
    )
    density_pin_size: FloatProperty(
        name="Pin Marker Size",
        description="Half-width of the density-pin squares "
                    "(flat-layout world units)",
        default=0.007, min=0.0005, max=1.0,
        unit='LENGTH', precision=4,
        update=prop_dirty_density_pins,
    )
    # ── Preview Fill ──────────────────────────────────────────────────────
    fill_target_mm: FloatProperty(
        name="Fill Spacing (mm)",
        description=(
            "Interior quad size for the preview fill, in mm of fabric "
            "(converted into the flat layout the retopo lives in). 0 follows "
            "the boundary Spacing above, which is what makes the preview "
            "honest: the interior then shows the density the boundary is "
            "asking for"
        ),
        default=0.0, min=0.0, soft_max=100.0,
    )
    fill_margin_factor: FloatProperty(
        name="Edge Clearance",
        description=(
            "How far the interior lattice keeps away from the outline, as a "
            "fraction of the fill spacing. Too small and the border quads come "
            "out as slivers"
        ),
        default=0.6, min=0.1, max=1.5,
    )
    # ── Patch Grid ────────────────────────────────────────
    grid_target_mm: FloatProperty(
        name="Grid Spacing (mm)",
        description=(
            "Cell size for the structured grid, in mm of fabric (converted "
            "into the flat layout the retopo lives in). 0 follows the boundary "
            "Spacing above"
        ),
        default=0.0, min=0.0, soft_max=100.0,
    )
    grid_smooth_passes: IntProperty(
        name="Side Smoothing",
        description=(
            "How much the four side curves are smoothed before the grid is "
            "mapped onto them. The boundary vertices never move — this only "
            "stops a zigzag hem from printing itself onto every interior row. "
            "0 follows the outline exactly"
        ),
        default=6, min=0, max=40,
    )
    grid_band_fallback: BoolProperty(
        name="Fill Mismatched Regions",
        description=(
            "Also fill regions whose opposite sides carry different numbers of "
            "vertices. The core grid follows the boundary counts and the "
            "difference is absorbed by one row of triangles, which are left "
            "selected so they can be found. Regions where that row would fan "
            "many triangles onto one vertex (a short curved side against a "
            "long one, e.g. a sleeve cap) are refused and reported instead. "
            "Regions whose sides already match are unaffected — they come "
            "out as pure quads either way"
        ),
        default=True,
    )
    # ── Anchors (span divisions) ───────────────────────────────────────────
    show_anchors: BoolProperty(
        name="Show Anchors",
        description=(
            "Mark the Guide's anchor points — where three or more panels meet, "
            "and where a sewn seam turns into a free edge. Anchors are what cut "
            "the boundary into spans, so this shows where one span ends and the "
            "next begins, which is the unit every density edit works in. Run "
            "'Analyze Anchors' to fill it in"
        ),
        default=False,
        update=prop_redraw,
    )
    anchor_color: EnumProperty(
        name="Anchor Color",
        items=COLOR_ITEMS,
        default="ORANGE",
        update=prop_redraw,
    )
    anchor_cross_size: FloatProperty(
        name="Anchor Cross Size",
        description="Arm length of the anchor markers (flat-layout world units)",
        default=0.006, min=0.0005, max=1.0,
        unit='LENGTH', precision=4,
        update=prop_dirty_anchors,
    )

    # ── Span density (resample an existing span) ───────────────────────────
    dens_count: IntProperty(
        name="Vertices",
        description=(
            "Vertex count to give the selected span, both anchors included. "
            "Both sides of a sewn seam are set to the same number"
        ),
        default=8, min=2, soft_max=64,
    )
    dens_spacing_mm: FloatProperty(
        name="Spacing (mm)",
        description=(
            "Rebuild the selected span so its vertices land roughly this far "
            "apart. The count follows from the span's 3D length"
        ),
        default=20.0, min=1.0, soft_max=100.0,
    )

    retopo_sync_tol: FloatProperty(
        name="Seam Distance",
        description=(
            "How close a retopo boundary vertex must be to a Guide seam to "
            "count as sitting on it. Vertices further away are cuts through "
            "the middle of a panel and are left alone. In real fabric "
            "distance: the test runs in the flat layout, whose scale depends "
            "on the UV packing, so the value is converted before use"
        ),
        default=0.005, min=0.0001, soft_max=0.05,
        unit='LENGTH', precision=4,
    )
