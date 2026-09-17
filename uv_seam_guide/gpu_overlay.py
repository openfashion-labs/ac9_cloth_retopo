"""GPU overlay: cache state, batch builders, draw callback, and handlers.

Module-level _cache / _batches hold the parsed seam data and GPU batches
across frame redraws. All property update callbacks that touch the overlay
live here so properties.py can import them without circular imports.
"""

import math

import bpy
import gpu
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from .. import ui_common as uic


# ─────────────────────────────────────────────────────────────
# Color presets (used by properties.py EnumProperty items)
# ─────────────────────────────────────────────────────────────

COLOR_PRESETS = {
    "WHITE":   (1.0, 1.0, 1.0, 1.0),
    "YELLOW":  (1.0, 0.85, 0.0, 1.0),
    "CYAN":    (0.0, 0.85, 1.0, 1.0),
    "MAGENTA": (1.0, 0.0, 0.85, 1.0),
    "GREEN":   (0.1, 1.0, 0.25, 1.0),
    "ORANGE":  (1.0, 0.45, 0.0, 1.0),
    "RED":     (1.0, 0.1, 0.1, 1.0),
    "BLUE":    (0.35, 0.6, 1.0, 1.0),
}

COLOR_ITEMS = [
    ("WHITE",   "White",   ""),
    ("YELLOW",  "Yellow",  ""),
    ("CYAN",    "Cyan",    ""),
    ("MAGENTA", "Magenta", ""),
    ("GREEN",   "Green",   ""),
    ("ORANGE",  "Orange",  ""),
    ("RED",     "Red",     ""),
    ("BLUE",    "Blue",    ""),
]


# ─────────────────────────────────────────────────────────────
# Runtime cache and GPU batch state
# ─────────────────────────────────────────────────────────────

_draw_handle = None
_draw_handle_px = None
_depsgraph_handler_registered = False
_in_depsgraph_rebuild = False
_addon_keymaps = []

_cache = {
    "pairs":           [],
    "pair_kd":         None,   # mathutils KDTree over seam-segment midpoints
    "pair_kd_entries": None,   # [(pair_index, 'A'|'B'), ...] aligned to the KDTree
    "boundary_segments": [],   # ALL guide boundary edges (sewn + free), flat world
    "boundary_kd":     None,   # KDTree over boundary-segment midpoints (UV Seam Snap)
    "free_segments":   [],     # boundary edges that are NOT a side of any sewn
                               # pair — hems, necklines, openings — flat world.
                               # Drawn by the Free Edges overlay and used as the
                               # foot-point target for free-edge ghosts.
    "free_kd":         None,   # KDTree over free-segment midpoints
    "symmetry_segments": [],   # fold (centre) lines of self-symmetric islands, flat world
    "symmetry_folds":  [],     # detection RESULT: [{"root","a","b"}] per fold axis —
                               # what Symmetrize Island consumes (root -> which island,
                               # a/b -> the axis, through the island's OWN centroid)
    "symmetry_kd":     None,   # KDTree over fold-segment midpoints (fold snap)
    "crease_segments": [],     # Guide edges tagged by Find Folds (ac9_crease_kind),
                               # flat world — the Creases overlay + crease snap
    "crease_kd":       None,   # KDTree over crease-segment midpoints
    "mirror_pair_segments": [],  # (Vector, Vector) centroid-to-centroid connectors
                                 # per detected mirror pair, flat world — overlay only
    "mirror_pairs":    [],     # detection RESULT: dicts from analysis.detect_mirror_pairs
                               # (root_a/root_b/verts_a/verts_b/rms/topo_match) —
                               # what Phase 2 (island replacement) will consume
    "mirror_pair_rejections": [],  # diagnostics: why an island didn't pair (see
                                    # analysis.detect_mirror_pairs docstring)
    "mirror_pair_count": 0,
    "ghost_results":   [],
    # Seam status overlay: state name -> [(p1, p2), ...] in flat world space.
    # Filled by the Seam Status report; empty until that has been run.
    "seam_status_segments": {},
    # Anchor overlay: flat-world positions of the Guide's anchor vertices —
    # the points where spans start and end. Filled by Analyze Anchors.
    "anchors":         [],
    # Guide vertex index per "anchors" point (same order/dedup), so the 3D
    # variant below can look up each anchor's Basis (3D) position directly.
    "anchor_indices":  [],
    "anchor_spans":    0,     # how many spans those anchors cut the boundary into
    "anchor_dirty":    False,
    # Tracks the Guide's Flat SK value so a manual slider crossing 2D<->3D
    # also flips which anchor-batch variant (flat vs 3D) is drawn — same
    # polling pattern as clo_projector/gpu_overlay.py's last_flat_sk_val.
    "anchor_last_flat_sk_val": -1.0,
    # Topology corners: world positions of the RETOPO vertices the user pinned
    # the edge flow to. Refreshed whenever one of the corner operators runs.
    "topo_corners":       [],
    "topo_corner_dirty":  False,
    # Density pins: world positions of the RETOPO vertices the user pinned
    # against Adjust Density (a knife-cut vertex etc). Refreshed whenever
    # one of the density-pin operators runs.
    "density_pins":       [],
    "density_pin_dirty":  False,
    "seam_dirty":      False,
    "ghost_dirty":     False,
    "snap_active":     False,
    "snap_target_3d":  None,
}

_batches = {
    "seam":           None,
    "free":           None,   # free (unsewn) boundary edges of the pattern
    "pair":           None,
    "cross_unplaced": None,   # ghost whose partner vert is NOT yet placed
    "cross_placed":   None,   # ghost whose partner vert already exists nearby
    "orphan_rings":   None,   # rings on the SOURCE vert of every unplaced sewn ghost
    "conn":           None,
    "conn_unplaced":  None,   # connectors for unplaced ghosts only
    "snap_ring":  None,   # active-snap feedback (modal only)
    "snap_rings": None,   # permanent snap-radius circles around all ghosts
    "symmetry":   None,   # fold (centre) lines of self-symmetric islands
    "crease":     None,   # Find Folds creases tagged on the Guide
    "mirror_pair": None,  # centroid connectors for detected cross-island mirror pairs
    "status_matched":   None,  # seam status overlay, one batch per state
    "status_mismatch":  None,
    "status_misaligned": None,
    "status_one_sided": None,
    "status_free_ok":   None,  # free-edge status: on / off the outline
    "status_free_off":  None,
    "anchors":    None,   # crosses at the Guide's span-dividing anchor points
    "topo_corners": None, # diamonds at the user's topology corners
    "density_pins": None, # squares at the user's density-pinned vertices
}

#: Seam status colours. Fixed rather than user-configurable — the whole point
#: is that the same colour always means the same verdict.
# Red and purple answer different questions, and that is the point of having
# both: red is a COUNT problem, which Match Seams or Generate fills in, while
# purple is a POSITION problem, which nothing here fixes — those vertices have
# to be moved by hand. One colour for both left the two jobs indistinguishable
# in the viewport (measured on the production jacket: 24 red seams were in
# fact 17 count problems and 7 position ones).
STATUS_COLORS = {
    "matched":    (0.15, 0.85, 0.35, 0.95),  # green  — paired and aligned
    "mismatch":   (1.00, 0.20, 0.20, 0.95),  # red    — counts differ
    "misaligned": (0.85, 0.30, 0.95, 0.95),  # purple — same count, out of line
    "one_sided":  (1.00, 0.65, 0.10, 0.95),  # orange — only one side authored
    # A free edge has no partner side, so "matched" would be a lie there. Its
    # own question is whether the retopo sits ON the outline. Off the outline
    # takes misaligned's purple: a free edge has no count to get wrong, so
    # drifting off it is a position problem, and the same colour therefore
    # still means the same job.
    #
    # On-outline is TEAL, not the blue it was until 2026-09-07: that blue sat
    # next to this purple in hue, and those two are exactly the pair that
    # appears side by side along a single hem — i.e. the one confusion that
    # costs something, "fine" misread as "fix me". Teal puts the two OK
    # verdicts (green, teal) in one family and the three problem verdicts
    # (red, purple, orange) in another, so a mix-up can now only happen
    # between colours that call for the same action.
    "free_ok":    (0.10, 0.78, 0.70, 0.90),  # teal   — free edge, on outline
    "free_off":   (0.85, 0.30, 0.95, 0.95),  # purple — free edge, off outline
}


# ─────────────────────────────────────────────────────────────
# Redraw / dirty helpers
# ─────────────────────────────────────────────────────────────

def _tag_redraw_3d(context=None):
    ctx = context or bpy.context
    if ctx is None:
        return
    try:
        for area in ctx.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    except Exception:
        pass


# ── Property update callbacks ─────────────────────────────────

def prop_redraw(self, context):
    _tag_redraw_3d(context)


def prop_dirty_seam(self, context):
    _cache["seam_dirty"] = True
    _tag_redraw_3d(context)


def prop_dirty_ghost(self, context):
    _cache["ghost_dirty"] = True
    _tag_redraw_3d(context)


def prop_dirty_anchors(self, context):
    """Anchor marker size changed — the batch bakes the size in, so rebuild."""
    _cache["anchor_dirty"] = True
    _tag_redraw_3d(context)


def prop_dirty_topo_corners(self, context):
    """Corner marker size changed — the batch bakes the size in, so rebuild."""
    _cache["topo_corner_dirty"] = True
    _tag_redraw_3d(context)


def prop_dirty_density_pins(self, context):
    """Pin marker size changed — the batch bakes the size in, so rebuild."""
    _cache["density_pin_dirty"] = True
    _tag_redraw_3d(context)


def prop_dirty_both(self, context):
    _cache["seam_dirty"] = True
    _cache["ghost_dirty"] = True
    _tag_redraw_3d(context)


def _flat_scale():
    """r for the current Guide: multiply a real length by it to get flat units.

    Max Seam Distance / Bond Distance / Snap Distance are written in real
    fabric millimetres like every other length in the add-on, while every
    consumer in this module measures in the flat layout. Measured factor
    between two packings of the same garment: 3.02 (AUDIT 8-B), so without
    this a 10 mm Snap Distance pulled vertices from 30 mm of fabric away.

    Imported lazily -- clo_projector imports this module, so a module-level
    import would close a cycle. Memoised, so per-redraw calls are free (see
    guide.scene_flat_scale).
    """
    from ..clo_projector.guide import scene_flat_scale
    return scene_flat_scale()


def prop_snap_distance_changed(self, context):
    """Snap distance changed — rebuild the snap-radius ring overlay."""
    _cache["ghost_dirty"] = True
    _tag_redraw_3d(context)


def prop_bond_distance_changed(self, context):
    """Bond Distance changed — re-classify existing ghosts (placed vs unplaced)
    and rebuild the cross batches so colours update live, WITHOUT recomputing
    the ghost field (cheap: no source/KDTree work, just a distance test)."""
    results = _cache["ghost_results"]
    if results:
        top = getattr(getattr(context, "scene", None), "ac9_cloth_retopo", None)
        retopo_obj = top.retopo_obj if top is not None else None
        if retopo_obj is not None and retopo_obj.type == 'MESH':
            from .ghost import get_retopo_vertex_positions
            positions = get_retopo_vertex_positions(retopo_obj)
            _classify_ghosts_placed(results, positions,
                                    self.bond_distance * _flat_scale())
            build_ghost_batches(
                results, self.z_offset, max(self.ghost_cross_size, 0.001)
            )
    _tag_redraw_3d(context)


def prop_live_update_changed(self, context):
    """Called when live_ghost_update toggles — (de)register the depsgraph handler."""
    if self.live_ghost_update:
        _register_depsgraph_handler()
        if _cache["pairs"]:
            top = getattr(getattr(context, "scene", None), "ac9_cloth_retopo", None)
            retopo_obj = top.retopo_obj if top is not None else None
            rebuild_ghosts(self, retopo_obj)
    else:
        _unregister_depsgraph_handler()
    _tag_redraw_3d(context)


# ─────────────────────────────────────────────────────────────
# GPU batch builders
# ─────────────────────────────────────────────────────────────

def _flat_to_3d(p, z):
    """Flat-layout world point → draw position (XY kept, Z forced to overlay height)."""
    return Vector((p.x, p.y, z))


def build_seam_batches(pairs, z):
    seam_coords = []
    pair_coords = []
    for p in pairs:
        a1 = _flat_to_3d(p["a_p1"], z)
        a2 = _flat_to_3d(p["a_p2"], z)
        b1 = _flat_to_3d(p["b_p1"], z)
        b2 = _flat_to_3d(p["b_p2"], z)
        seam_coords += [a1, a2, b1, b2]
        pair_coords += [a1, b1, a2, b2]

    try:
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    except SystemError:
        # Background mode has no drawing GPU (same guard as build_seam_status_
        # batches). Analyze Seams is worth running headlessly — that is how it
        # gets tested — and nothing will draw there anyway.
        _batches["seam"] = _batches["pair"] = None
        return
    _batches["seam"] = (
        batch_for_shader(shader, 'LINES', {"pos": seam_coords})
        if seam_coords else None
    )
    _batches["pair"] = (
        batch_for_shader(shader, 'LINES', {"pos": pair_coords})
        if pair_coords else None
    )


def build_free_edge_batch(segments, z):
    """Build the free-edge overlay batch — the pattern's UNSEWN boundary.

    segments = list of (Vector, Vector) in flat-layout world space, same
    convention (and the same Z as the seam lines) as build_seam_batches: the
    two sets together make up the whole pattern outline, and drawing the free
    edges UNDER the cyan seams keeps a shared edge reading as a seam.

    Without this the free edges are invisible in the flat layout whenever the
    Guide carries no use_seam flags, which is the normal case for a CLO export
    (measured on a production Guide: 5,133 boundary edges, 0 use_seam).
    """
    coords = []
    for p1, p2 in segments:
        coords += [_flat_to_3d(p1, z), _flat_to_3d(p2, z)]
    try:
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    except SystemError:
        _batches["free"] = None      # background mode has no drawing GPU
        return
    _batches["free"] = (
        batch_for_shader(shader, 'LINES', {"pos": coords}) if coords else None
    )


def build_crease_batch(segments, z):
    """Build the Creases overlay batch: the Guide's Find Folds lines (see
    analysis.find_crease_segments_flat), in flat-layout world space. Drawn a
    touch above the seam lines, like the fold (symmetry) lines."""
    coords = []
    for p1, p2 in segments:
        coords += [_flat_to_3d(p1, z + 0.017), _flat_to_3d(p2, z + 0.017)]
    try:
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    except SystemError:
        _batches["crease"] = None
        return
    _batches["crease"] = (
        batch_for_shader(shader, 'LINES', {"pos": coords}) if coords else None
    )


def build_symmetry_batches(segments, z_offset):
    """Build the fold-line overlay batch (one segment per symmetric island).

    segments = list of (Vector, Vector) in flat-layout world space. Drawn a
    touch above the seam lines so it reads clearly over them.
    """
    z = z_offset + 0.019
    coords = []
    for p1, p2 in segments:
        coords += [Vector((p1.x, p1.y, z)), Vector((p2.x, p2.y, z))]
    try:
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    except SystemError:
        # Background mode has no drawing GPU (same guard as build_seam_batches).
        # Without it Analyze Guide cannot run headlessly at all: it calls
        # Analyze Symmetry, whose operator builds this batch, so the SystemError
        # propagated up and took the whole analysis chain down.
        _batches["symmetry"] = None
        return
    _batches["symmetry"] = (
        batch_for_shader(shader, 'LINES', {"pos": coords}) if coords else None
    )


def build_mirror_pair_batches(segments, z_offset):
    """Build the cross-island mirror-pair overlay: one connector line per
    detected pair, drawn between the two islands' Flat-SK centroids.

    segments = list of (Vector, Vector) in flat-layout world space (same
    convention as build_symmetry_batches). Detection itself runs in 3D Basis
    space (see analysis.detect_mirror_pairs) — this is purely the 2D-layout
    visualisation of that result, drawn a touch above the fold-line overlay.
    """
    z = z_offset + 0.021
    coords = []
    for p1, p2 in segments:
        coords += [Vector((p1.x, p1.y, z)), Vector((p2.x, p2.y, z))]
    try:
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    except SystemError:
        _batches["mirror_pair"] = None   # background mode has no drawing GPU
        return
    _batches["mirror_pair"] = (
        batch_for_shader(shader, 'LINES', {"pos": coords}) if coords else None
    )


def build_seam_status_batches(segments_by_state, z_offset):
    """Build the seam-status overlay: one batch per verdict.

    segments_by_state maps a state name to a list of (Vector, Vector) in
    flat-layout world space — both sides of each seam, so a one-sided seam
    shows orange on the authored side AND on the empty partner, which is
    where the work still has to happen.

    Untouched seams are deliberately not drawn: on a real garment they are
    the overwhelming majority (192 of 195 measured), and colouring them
    would bury the handful that need attention.
    """
    z = z_offset + 0.02
    try:
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    except SystemError:
        # Background mode has no drawing GPU. The status report itself is
        # useful headlessly (that is how it gets tested), and nothing will
        # draw there anyway, so skip the batches rather than fail the caller.
        for state in STATUS_COLORS:
            _batches[f"status_{state}"] = None
        return
    # Driven off STATUS_COLORS rather than a hard-coded tuple so adding a
    # verdict (free_ok / free_off did exactly this) needs one entry, not
    # three lists that can drift apart.
    for state in STATUS_COLORS:
        coords = []
        for p1, p2 in segments_by_state.get(state, ()):
            coords += [Vector((p1.x, p1.y, z)), Vector((p2.x, p2.y, z))]
        _batches[f"status_{state}"] = (
            batch_for_shader(shader, 'LINES', {"pos": coords}) if coords else None
        )


def build_anchor_batch(points, z_offset, cross_size):
    """Crosses at the Guide's anchor points, in flat-layout world space.

    Anchors are where spans begin and end — the divisions every density edit
    works in. Without them on screen you only learn where a span ended after
    running something, which is how this overlay came to be asked for.
    """
    z = z_offset + 0.023   # above the status lines (z+0.02) so it stays legible
    s = max(cross_size, 0.0005)
    try:
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    except SystemError:
        _batches["anchors"] = None      # background mode has no drawing GPU
        return
    coords = []
    for p in points:
        c = Vector((p.x, p.y, z))
        # A diagonal cross, so it reads differently from the ghost '+' markers.
        coords += [c + Vector((-s, -s, 0)), c + Vector((s, s, 0)),
                   c + Vector((-s, s, 0)), c + Vector((s, -s, 0))]
    _batches["anchors"] = (
        batch_for_shader(shader, 'LINES', {"pos": coords}) if coords else None
    )


def build_anchor_batch_3d(guide_obj, indices, cross_size):
    """Crosses at the Guide's anchor points, in real 3D world space (Basis).

    Used while the Guide is shown in 3D (Flat SK < 0.5): the flat-layout
    z-offset stacking trick above does not apply to a Guide displaying its
    draped garment shape, so this draws directly at each anchor vertex's
    Basis world position instead — no z_offset added.
    """
    s = max(cross_size, 0.0005)
    try:
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    except SystemError:
        _batches["anchors"] = None      # background mode has no drawing GPU
        return
    mesh = guide_obj.data
    matrix = guide_obj.matrix_world
    n = len(mesh.vertices)
    coords = []
    for i in indices:
        if i < 0 or i >= n:
            continue
        c = matrix @ mesh.vertices[i].co
        coords += [c + Vector((-s, -s, 0)), c + Vector((s, s, 0)),
                   c + Vector((-s, s, 0)), c + Vector((s, -s, 0))]
    _batches["anchors"] = (
        batch_for_shader(shader, 'LINES', {"pos": coords}) if coords else None
    )


def build_topo_corner_batch(points, z_offset, size):
    """Diamonds at the user's topology corners, in flat-layout world space.

    A diamond, not the anchor's diagonal cross: the two sets overlap heavily
    (a pattern corner is often an anchor too) and the whole point of marking
    corners is to see which ones YOU chose, not which ones the sewing implies.
    """
    z = z_offset + 0.024   # just above the anchors, so an overlap still reads
    s = max(size, 0.0005)
    try:
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    except SystemError:
        _batches["topo_corners"] = None   # background mode has no drawing GPU
        return
    coords = []
    for p in points:
        c = Vector((p.x, p.y, z))
        up = Vector((0.0, s, 0.0))
        rt = Vector((s, 0.0, 0.0))
        ring = [c + up, c + rt, c - up, c - rt]
        for k in range(4):
            coords += [ring[k], ring[(k + 1) % 4]]
    _batches["topo_corners"] = (
        batch_for_shader(shader, 'LINES', {"pos": coords}) if coords else None
    )


def clear_topo_corner_batch():
    _batches["topo_corners"] = None
    _cache["topo_corners"] = []
    _cache["topo_corner_dirty"] = False


def build_density_pin_batch(points, z_offset, size):
    """Squares at the user's density-pinned vertices, in flat-layout world
    space.

    A square, not the corner's diamond or the anchor's cross: a pin can
    coincide with either (a knife-cut vertex can also be a topology corner,
    or sit near an anchor), and the whole point is to see which ones are
    protected from Adjust Density specifically.
    """
    z = z_offset + 0.028   # just above topo corners, so an overlap still reads
    s = max(size, 0.0005)
    try:
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    except SystemError:
        _batches["density_pins"] = None   # background mode has no drawing GPU
        return
    coords = []
    for p in points:
        c = Vector((p.x, p.y, z))
        up = Vector((0.0, s, 0.0))
        rt = Vector((s, 0.0, 0.0))
        ring = [c + up + rt, c + up - rt, c - up - rt, c - up + rt]
        for k in range(4):
            coords += [ring[k], ring[(k + 1) % 4]]
    _batches["density_pins"] = (
        batch_for_shader(shader, 'LINES', {"pos": coords}) if coords else None
    )


def clear_density_pin_batch():
    _batches["density_pins"] = None
    _cache["density_pins"] = []
    _cache["density_pin_dirty"] = False


def clear_anchor_batch():
    _batches["anchors"] = None
    _cache["anchors"] = []
    _cache["anchor_indices"] = []
    _cache["anchor_spans"] = 0


def clear_seam_status_batches():
    for state in STATUS_COLORS:
        _batches[f"status_{state}"] = None
    _cache["seam_status_segments"] = {}


# ── Wholesale resets: one of the shared inputs was swapped ───

# Every cache above is derived from ONE of the two shared inputs (the Guide or
# the Retopo), and nothing used to notice when the user picked a DIFFERENT
# object. Swapping the Guide (jacket -> shirt) left the jacket's seam lines,
# ghosts and status colours on screen, and Analyze Guide did not fix it:
# that button only refreshes the caches its three sub-passes own (pairs /
# free edges / creases, folds, twins, anchors), so the Seam Status snapshot
# and the ghosts (whenever Live Ghost Update is off) kept drawing the old
# garment. Toggling the overlay off and on could not fix those either --
# the seam toggles only tag a redraw, they never rebuild a batch -- which is
# why some overlays appeared to recover and others never did.
#
# The two reset_* functions below are the single authoritative answer to "the
# input changed, forget what you knew". __init__.py calls them from the
# pointer properties' update callbacks, so a swap is enough; no Analyze and
# no toggling required.


def clear_seam_batches():
    """The seam analysis and everything indexed off it (outline, free edges,
    creases). What Analyze Seams fills in."""
    _cache["pairs"] = []
    _cache["pair_kd"] = None
    _cache["pair_kd_entries"] = None
    _cache["boundary_segments"] = []
    _cache["boundary_kd"] = None
    _cache["free_segments"] = []
    _cache["free_kd"] = None
    _cache["crease_segments"] = []
    _cache["crease_kd"] = None
    _cache["seam_dirty"] = False
    _batches["seam"] = None
    _batches["pair"] = None
    _batches["free"] = None
    _batches["crease"] = None


def clear_symmetry_batches():
    """Fold lines (Detect Folds)."""
    _cache["symmetry_segments"] = []
    _cache["symmetry_folds"] = []
    _cache["symmetry_kd"] = None
    _batches["symmetry"] = None


def clear_mirror_pair_batches():
    """Twin connectors (Detect Twins)."""
    _cache["mirror_pair_segments"] = []
    _cache["mirror_pairs"] = []
    _cache["mirror_pair_rejections"] = []
    _cache["mirror_pair_count"] = 0
    _batches["mirror_pair"] = None


def clear_ghost_batches():
    """Ghost crosses, their connector lines and the snap-radius rings.

    Leaves the modal snap ring (_batches["snap_ring"] / "snap_active") alone:
    rebuild_ghosts() calls this and can fire from the depsgraph handler in the
    middle of a Ghost Snap Move, where that ring is live state, not a cache.
    """
    _cache["ghost_results"] = []
    _cache["ghost_dirty"] = False
    _batches["cross_unplaced"] = None
    _batches["cross_placed"] = None
    _batches["orphan_rings"] = None
    _batches["conn"] = None
    _batches["conn_unplaced"] = None
    _batches["snap_rings"] = None


def clear_scene_counters(scene=None):
    """Drop the scene counters the Setup readout prints (Seams 412 / Folds 6 /
    ...). They are results of the analyses being cleared, so leaving them
    behind would state a count for a garment that is no longer loaded.

    NOT called on file load or on unregister: both write to the scene, which
    would mark a freshly opened file dirty (and ID writes are not always
    allowed at unregister time). Both paths pass counters=False.
    """
    if scene is None:
        scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return
    for key in ("ac9_cloth_retopo_seam_pair_count",
                "ac9_cloth_retopo_symmetry_count",
                "ac9_cloth_retopo_mirror_pair_count",
                "ac9_cloth_retopo_mirror_pair_tested",
                "ac9_cloth_retopo_ghost_count",
                "ac9_cloth_retopo_island_jumped"):
        try:
            if key in scene:
                del scene[key]
        except Exception:
            pass


def reset_guide_derived(scene=None, counters=True):
    """Blank every overlay and cache read off the Guide.

    Corners and Pins are deliberately NOT in here: they are attributes on the
    RETOPO mesh and keep their meaning whichever Guide is loaded.
    """
    clear_seam_batches()
    clear_symmetry_batches()
    clear_mirror_pair_batches()
    clear_anchor_batch()
    clear_seam_status_batches()
    clear_ghost_batches()
    _cache["anchor_dirty"] = False
    _cache["anchor_last_flat_sk_val"] = -1.0
    _cache["snap_active"] = False
    _cache["snap_target_3d"] = None
    _batches["snap_ring"] = None
    if counters:
        clear_scene_counters(scene)


def reset_retopo_derived(scene=None, counters=True):
    """Blank every overlay and cache read off the Retopo.

    The Guide analysis itself (pairs, folds, twins, anchors) is untouched: a
    new Retopo against the same Guide is a normal thing to do, and re-running
    the whole Guide analysis for it would be wasted work.
    """
    clear_ghost_batches()
    clear_seam_status_batches()
    clear_topo_corner_batch()
    clear_density_pin_batch()
    _cache["snap_active"] = False
    _cache["snap_target_3d"] = None
    _batches["snap_ring"] = None
    if counters:
        if scene is None:
            scene = getattr(bpy.context, "scene", None)
        try:
            if scene is not None and "ac9_cloth_retopo_ghost_count" in scene:
                del scene["ac9_cloth_retopo_ghost_count"]
        except Exception:
            pass


def build_ghost_batches(results, z_offset, cross_size, ring_segments=16):
    # Split crosses by whether the partner vert is already placed (r["matched"]).
    # Unplaced ghosts are the actionable ones — the spots you still need to fill;
    # placed ghosts sit on an existing vert and are mostly confirmation.
    #
    # A ghost cross marks the PARTNER side — where a vertex is missing — not the
    # vertex that owns the ghost. In the split flat layout those are a whole
    # panel apart: measured 0.802 flat units between an unpaired boundary vertex
    # and its own cross on a production trouser panel, against a 0.005 cross
    # size. At working zoom the warning is off screen and the orphan vertex on
    # screen carries no mark at all, which reads as "no warning at all" — that
    # is what prompted this. `orphan_coords` rings the orphan, so the vertex you
    # are looking at tells you its counterpart slot is empty. Sewn only: a free
    # edge's foot is on the vertex's own outline, close enough that a ring there
    # would just smear into the cross.
    #
    # Same reason the connectors are split: on that panel 320 ghosts produced 2
    # unplaced, so a connector per ghost buried the 2 that mattered under 318
    # that said nothing. Ghost Lines has been OFF by default ever since.
    unplaced_coords = []
    placed_coords = []
    orphan_coords = []
    conn_coords = []
    conn_unplaced_coords = []
    z_cross = z_offset + 0.02
    z_ring  = z_offset + 0.0205
    z_conn  = z_offset + 0.018
    ring_r = max(cross_size, 0.001) * 1.6

    for r in results:
        opp = _flat_to_3d(r["opposite_pt"], z_cross)
        s = cross_size
        arms = [
            opp + Vector((-s, 0, 0)), opp + Vector((s, 0, 0)),
            opp + Vector((0, -s, 0)), opp + Vector((0, s, 0)),
        ]
        matched = bool(r.get("matched"))
        (placed_coords if matched else unplaced_coords).extend(arms)
        src = Vector((r["source_pt"].x, r["source_pt"].y, z_conn))
        dst = Vector((r["opposite_pt"].x, r["opposite_pt"].y, z_conn))
        conn_coords += [src, dst]
        if not matched:
            conn_unplaced_coords += [src, dst]
            if r.get("side") != "free":
                cx, cy = r["source_pt"].x, r["source_pt"].y
                for i in range(ring_segments):
                    a1 = 2.0 * math.pi * i / ring_segments
                    a2 = 2.0 * math.pi * (i + 1) / ring_segments
                    orphan_coords += [
                        Vector((cx + math.cos(a1) * ring_r,
                                cy + math.sin(a1) * ring_r, z_ring)),
                        Vector((cx + math.cos(a2) * ring_r,
                                cy + math.sin(a2) * ring_r, z_ring)),
                    ]

    try:
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    except SystemError:
        # Background mode has no drawing GPU (same guard as build_seam_status_
        # batches). Refresh Ghosts is worth running headlessly — that is how the
        # free-edge foot classification gets tested — and nothing draws there.
        _batches["cross_unplaced"] = None
        _batches["cross_placed"] = None
        _batches["orphan_rings"] = None
        _batches["conn"] = None
        _batches["conn_unplaced"] = None
        return
    _batches["cross_unplaced"] = (
        batch_for_shader(shader, 'LINES', {"pos": unplaced_coords})
        if unplaced_coords else None
    )
    _batches["cross_placed"] = (
        batch_for_shader(shader, 'LINES', {"pos": placed_coords})
        if placed_coords else None
    )
    _batches["orphan_rings"] = (
        batch_for_shader(shader, 'LINES', {"pos": orphan_coords})
        if orphan_coords else None
    )
    _batches["conn"] = (
        batch_for_shader(shader, 'LINES', {"pos": conn_coords})
        if conn_coords else None
    )
    _batches["conn_unplaced"] = (
        batch_for_shader(shader, 'LINES', {"pos": conn_unplaced_coords})
        if conn_unplaced_coords else None
    )


def build_snap_rings_batch(ghost_results, z_offset, snap_distance, segments=32):
    """Draw snap-distance circles around every ghost point (permanent overlay).

    Helps the user judge which retopo vertices are within snapping range.
    The circle lies in the XY plane at flat-layout world coordinates.
    """
    coords = []
    z = z_offset + 0.021  # just above ghost cross (z_cross = z_offset + 0.02)
    r = snap_distance
    for ghost in ghost_results:
        cx = ghost["opposite_pt"].x
        cy = ghost["opposite_pt"].y
        for i in range(segments):
            a1 = 2.0 * math.pi * i / segments
            a2 = 2.0 * math.pi * (i + 1) / segments
            p1 = Vector((cx + math.cos(a1) * r, cy + math.sin(a1) * r, z))
            p2 = Vector((cx + math.cos(a2) * r, cy + math.sin(a2) * r, z))
            coords += [p1, p2]
    try:
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    except SystemError:
        _batches["snap_rings"] = None    # background mode has no drawing GPU
        return
    _batches["snap_rings"] = (
        batch_for_shader(shader, 'LINES', {"pos": coords}) if coords else None
    )


def build_snap_ring_batch(center, radius, segments=32):
    coords = []
    for i in range(segments):
        a1 = 2.0 * math.pi * i / segments
        a2 = 2.0 * math.pi * (i + 1) / segments
        p1 = center + Vector((math.cos(a1) * radius, math.sin(a1) * radius, 0.0))
        p2 = center + Vector((math.cos(a2) * radius, math.sin(a2) * radius, 0.0))
        coords += [p1, p2]
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    _batches["snap_ring"] = batch_for_shader(shader, 'LINES', {"pos": coords})


# ─────────────────────────────────────────────────────────────
# Shared rebuild helper
# ─────────────────────────────────────────────────────────────

def _dedup_ghosts_by_opposite(results, precision=4):
    """Drop ghosts whose opposite point coincides (rounded) with an earlier one.

    Returns (deduped_results, raw_count, max_pile) where max_pile is the largest
    number of source verts that collapsed onto a single opposite location —
    a quick health signal (1-2 = fine; large = doubled verts / density issue).
    """
    seen = {}
    out = []
    for r in results:
        k = (round(r["opposite_pt"].x, precision), round(r["opposite_pt"].y, precision))
        n = seen.get(k, 0)
        if n == 0:
            out.append(r)
        seen[k] = n + 1
    max_pile = max(seen.values()) if seen else 0
    return out, len(results), max_pile


def _classify_ghosts_placed(results, retopo_positions, snap_distance):
    """Tag each ghost dict with r["matched"] = True when an existing retopo vert
    sits within snap_distance of its opposite_pt (the partner is already placed).

    Mutates `results` in place. Uses a KDTree over the retopo boundary verts so
    this stays cheap even with thousands of ghosts on a 1M-poly guide.
    """
    if not results:
        return
    if not retopo_positions:
        for r in results:
            r["matched"] = False
        return

    from mathutils.kdtree import KDTree

    vk = KDTree(len(retopo_positions))
    for i, p in enumerate(retopo_positions):
        vk.insert((p.x, p.y, 0.0), i)
    vk.balance()

    tol = snap_distance
    for r in results:
        op = r["opposite_pt"]
        _loc, _idx, dist = vk.find((op.x, op.y, 0.0))
        r["matched"] = (dist is not None and dist <= tol)


def rebuild_ghosts(seam_props, retopo_obj, write_stats=True, progress=None):
    """Recompute ghost results from cached seam pairs + current retopo state.

    seam_props  — the AC9SeamGuideProps sub-property group
    retopo_obj  — the shared retopo Object (from ac9_cloth_retopo.retopo_obj)
    write_stats — write scene["ac9_cloth_retopo_ghost_count"] (skip when called
                  from contexts where writing to ID data is disallowed).
    progress    — optional callable taking a 0..1 fraction; ticked across the
                  per-boundary-vertex classification, which is the slow part
                  (3.8 s on a 172 k-triangle Guide). Pass a
                  ui_common.ProgressThrottle, never a raw progress_update.
    Returns (ghost_count, retopo_vertex_count).

    Ghost source is always the retopo's open-boundary loop (bmesh
    edge.is_boundary) — that's where CLO UV seams sit.
    """
    from .ghost import (
        get_retopo_boundary_vertex_positions,
        get_retopo_vertex_positions,
        build_pair_kd,
        build_segment_kd,
        classify_boundary_vertex,
    )

    if retopo_obj is None or retopo_obj.type != 'MESH' or not _cache["pairs"]:
        # Snap rings go too: they are built from the same ghost list, and
        # leaving them behind drew a field of circles around ghosts that no
        # longer existed.
        clear_ghost_batches()
        return 0, 0

    # Always the open-boundary loop — that's where seams live. (The old
    # "use all vertices" mode just flooded interiors; Max Seam Distance already
    # gates proximity, so it added nothing.)
    retopo_positions = get_retopo_boundary_vertex_positions(retopo_obj)

    if _cache["pair_kd"] is None:
        _cache["pair_kd"], _cache["pair_kd_entries"] = build_pair_kd(_cache["pairs"])

    # Use ALL partners per boundary vert (same as the Show-Pair preview), not
    # just the single nearest. At a 3-way junction (rib fold / pocket / 3 panels
    # meeting) the single-nearest assignment is arbitrary between equidistant
    # partners and silently drops the others — so verts sitting exactly on a
    # seam (dist 0) could end up with no cross. find_all clusters to one point
    # per partner panel, so this doesn't spray; the dedup below still collapses
    # genuine doubled verts.
    #
    # Free edges are handled in the same pass: a boundary vert sitting on a
    # hem has no opposite side, so its counterpart is the foot of the
    # perpendicular onto the free edge. classify_boundary_vertex decides which
    # kind of ghost a vert gets so the two never both fire on the same vert
    # (except at an anchor, where they genuinely both apply).
    fs = _flat_scale()
    md = seam_props.max_distance_uv * fs
    kd = _cache["pair_kd"]
    entries = _cache["pair_kd_entries"]
    free_segments = _cache["free_segments"]
    if free_segments and _cache["free_kd"] is None:
        _cache["free_kd"] = build_segment_kd(free_segments)
    free_kd = _cache["free_kd"]
    results = []
    n_positions = len(retopo_positions)
    for i, co in enumerate(retopo_positions):
        results.extend(classify_boundary_vertex(
            co, _cache["pairs"], kd, entries, free_segments, free_kd,
            max_distance=md, bond_distance=seam_props.bond_distance * fs,
        ))
        if progress is not None and n_positions:
            progress((i + 1) / n_positions)

    # Collapse piles: many retopo verts (doubled verts, density mismatch, or a
    # whole partner panel) can land on the SAME opposite point. Keep one cross
    # per opposite location so the overlay reads as one marker per seam spot.
    results, n_raw, max_pile = _dedup_ghosts_by_opposite(results)

    # Classify each ghost: is there already a retopo vert sitting on its
    # opposite_pt (within Snap Distance)? If so the partner is "placed" — the
    # ghost is just confirmation. Otherwise it's "unplaced" — a gap to fill.
    #
    # Classify against ALL retopo verts, not just the open boundary: a pocket
    # (layered / appliqué seam) is sewn into the body as an INTERIOR loop, so
    # the verts that "place" it are never open-boundary and a boundary-only
    # check would leave them red forever. This adds NO new ghosts (the source
    # set above is unchanged) — it only flips existing reds to green when a vert
    # actually sits on them, so it can't add overlay noise. The only edge case
    # is a stray interior vert landing within Snap Distance of a ghost reading
    # as placed; lower Snap Distance if that bites. In the split flat layout the
    # islands are far apart, so a ghost's own source vert never matches itself.
    #
    # Only the SEWN ghosts go through that test. A free foot's "partner" is
    # its own source vertex projected onto the outline, so the nearest-vert
    # lookup would always find the source itself and report every free foot
    # as placed — exactly hiding the ones worth seeing. A free foot is placed
    # when the vertex is already ON the outline, which is its own distance.
    sewn_results = [r for r in results if r.get("side") != "free"]
    free_results = [r for r in results if r.get("side") == "free"]
    classify_positions = get_retopo_vertex_positions(retopo_obj)
    _classify_ghosts_placed(sewn_results, classify_positions,
                            seam_props.bond_distance * fs)
    for r in free_results:
        r["matched"] = (r["distance"] <= seam_props.bond_distance * fs)
    n_unplaced = sum(1 for r in results if not r.get("matched"))
    if write_stats:
        n_free_unplaced = sum(1 for r in free_results if not r.get("matched"))
        print(f"[AC9 Seam] ghosts: {n_raw} raw → {len(results)} unique "
              f"opposite locations ({n_unplaced} unplaced / "
              f"{len(results) - n_unplaced} placed; max pile: {max_pile}); "
              f"sewn {len(sewn_results)} / free {len(free_results)} "
              f"({n_free_unplaced} off the outline)")

    _cache["ghost_results"] = results
    _cache["ghost_dirty"] = False
    build_ghost_batches(
        results, seam_props.z_offset,
        max(seam_props.ghost_cross_size, 0.001),
    )
    build_snap_rings_batch(results, seam_props.z_offset,
                           seam_props.snap_distance * fs)

    if write_stats:
        scene = bpy.context.scene
        if scene is not None:
            scene["ac9_cloth_retopo_ghost_count"] = len(results)
    return len(results), len(retopo_positions)


# ─────────────────────────────────────────────────────────────
# Live Ghost Update — depsgraph handler (confirm-time) + load handler
# ─────────────────────────────────────────────────────────────


@persistent
def _depsgraph_post_handler(scene, depsgraph):
    global _in_depsgraph_rebuild
    if _in_depsgraph_rebuild:
        return
    # Whole body guarded: this fires during New Scene / file teardown too, when
    # mesh/edit-mesh state can be transient. Never let it raise (or crash).
    try:
        top = getattr(scene, "ac9_cloth_retopo", None)
        if top is None:
            return
        seam_props = top.seam
        retopo_obj = top.retopo_obj
        if not seam_props.live_ghost_update or not _cache["pairs"] or retopo_obj is None:
            return

        affected = False
        for upd in depsgraph.updates:
            oid = getattr(upd.id, "original", None)
            if oid is None:
                continue
            if oid == retopo_obj or oid == retopo_obj.data:
                affected = True
                break
            try:  # evaluated copies don't always compare equal — fall back to name
                if oid.name == retopo_obj.name or oid.name == retopo_obj.data.name:
                    affected = True
                    break
            except Exception:
                pass
        if not affected:
            return

        _in_depsgraph_rebuild = True
        try:
            rebuild_ghosts(seam_props, retopo_obj, write_stats=False)
        finally:
            _in_depsgraph_rebuild = False
        _tag_redraw_3d()
    except Exception as exc:  # noqa: BLE001 — never crash Blender from a handler
        print(f"[AC9 Seam] live-update handler skipped: {exc}")


def _register_depsgraph_handler():
    global _depsgraph_handler_registered
    if _depsgraph_post_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_depsgraph_post_handler)
    _depsgraph_handler_registered = True


def _unregister_depsgraph_handler():
    global _depsgraph_handler_registered
    while _depsgraph_post_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_depsgraph_post_handler)
    _depsgraph_handler_registered = False


# ─────────────────────────────────────────────────────────────
# Guide-edit cache invalidation — always on, independent of Live Ghost Update
# ─────────────────────────────────────────────────────────────
#
# detect_island_symmetry_cached / detect_mirror_pairs_cached key on the
# Guide's V/E/F counts + matrix_world (see their docstrings in analysis.py),
# which is deliberate for a manual "Analyze"/"Detect" button (force=True,
# always recomputes) but leaves a silent gap for an AUTOMATED caller that
# passes force=False to reuse the memo (Symmetrize Island, Replace Mirror
# Island, Adjust Density's symmetry_cache_has check): dragging Guide verts
# in Edit Mode WITHOUT changing those counts does not change the cache key,
# so a stale fold axis / mirror pairing keeps being served after the user
# hand-edits the Guide's flat layout. Same EDIT -> OBJECT transition trick
# as clo_projector.handlers' auto-bind/auto-refresh: cheap per-tick (one
# attribute read, no timer) and only clears the two Guide-position-sensitive
# memo caches, which is a correctness reset, not a rebuild.
_guide_last_mode: dict = {}


@persistent
def _guide_edit_invalidation_handler(scene, depsgraph):
    try:
        top = getattr(scene, "ac9_cloth_retopo", None)
        if top is None:
            return
        guide_obj = top.guide_obj
        if guide_obj is None or guide_obj.type != "MESH":
            return

        mode = guide_obj.mode
        prev = _guide_last_mode.get(guide_obj.name)
        _guide_last_mode[guide_obj.name] = mode
        if prev != "EDIT" or mode != "OBJECT":
            return

        from . import analysis as _an
        _an.invalidate_symmetry_cache(guide_obj)
        _an.invalidate_mirror_pair_cache(guide_obj)
    except Exception as exc:  # noqa: BLE001 — never crash Blender from a handler
        print(f"[AC9 Seam] guide cache invalidation skipped: {exc}")


def _register_guide_invalidation_handler():
    if _guide_edit_invalidation_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_guide_edit_invalidation_handler)


def _unregister_guide_invalidation_handler():
    while _guide_edit_invalidation_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_guide_edit_invalidation_handler)
    _guide_last_mode.clear()


@persistent
def _load_post_handler(_dummy):
    scene = bpy.context.scene
    top = getattr(scene, "ac9_cloth_retopo", None) if scene is not None else None
    if top is not None:
        try:
            if top.seam.live_ghost_update:
                _register_depsgraph_handler()
            else:
                _unregister_depsgraph_handler()
        except Exception:
            pass
    # Session_uids from the previous file mean nothing after a load — drop
    # the detect_mirror_pairs/island computation caches too, not just this
    # module's display cache.
    from . import analysis as _an
    _an.invalidate_mirror_pair_cache()
    _an._island_cache.clear()

    # Cache doesn't survive file reload — clear batches to avoid stale display.
    # counters=False: the counters live in the file that was just opened, and
    # deleting them here would mark it dirty on open. The Setup readout is
    # gated on _cache["pairs"], which this empties, so they are not shown.
    reset_guide_derived(counters=False)
    reset_retopo_derived(counters=False)
    for k in _batches:
        _batches[k] = None


# ─────────────────────────────────────────────────────────────
# Draw callback
# ─────────────────────────────────────────────────────────────

def _draw_single(batch, color, line_px, vp):
    if batch is None:
        return
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    shader.bind()
    shader.uniform_float("color", color)
    shader.uniform_float("lineWidth", line_px)
    shader.uniform_float("viewportSize", vp)
    batch.draw(shader)


def draw_selected_pair(props, top, vp):
    """Per-frame: draw the opposite ghost + connector for SELECTED retopo verts.

    Computed live each frame (cheap — only a handful of selected verts query the
    KDTree), so it tracks selection and movement instantly without depending on
    depsgraph. Drawn in a distinct colour (green cross / white line, thicker) so
    it stands out from the full magenta ghost field.
    """
    retopo = top.retopo_obj
    if retopo is None or retopo.type != 'MESH' or retopo.mode != 'EDIT':
        return
    if not _cache["pairs"]:
        return

    import bmesh as _bmesh
    from .ghost import build_pair_kd, build_segment_kd, classify_boundary_vertex

    bm = _bmesh.from_edit_mesh(retopo.data)
    mw = retopo.matrix_world

    # A pair only makes sense for BOUNDARY verts: a seam vertex has a counterpart
    # on the opposite side of the seam. Interior verts have no partner, so drawing
    # their "nearest seam" is pure noise (and points at unrelated seams). Gate on
    # boundary first; Max Seam Distance (below) then handles boundary edges that
    # aren't actually sewn seams (hems / free openings have no partner nearby).
    #
    # Also include vertices with NO linked faces (no_face_verts): a freshly added
    # vertex that hasn't been incorporated into any face yet has zero is_boundary
    # edges (is_boundary requires exactly 1 linked face), so the strict boundary
    # check silently excludes it even when it sits exactly on a seam. Since this
    # is a per-selection diagnostic, being permissive here is correct — Max Seam
    # Distance still suppresses truly unrelated positions.
    bm.edges.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    boundary_idx = set()
    for e in bm.edges:
        if e.is_boundary:
            boundary_idx.add(e.verts[0].index)
            boundary_idx.add(e.verts[1].index)
    sel = [mw @ v.co for v in bm.verts
           if v.select and (v.index in boundary_idx or not v.link_faces)]
    if not sel:
        return

    if _cache["pair_kd"] is None:
        _cache["pair_kd"], _cache["pair_kd_entries"] = build_pair_kd(_cache["pairs"])

    # Gather ALL partners per selected vert (not just the nearest one), so an
    # N-way junction — common here, lots of non-manifold seams — shows every
    # counterpart. Max Seam Distance still suppresses boundary verts that
    # aren't near any outline edge at all.
    #
    # Free edges come through the same classifier as the full ghost field, so
    # a selected vert on a hem shows the point on the outline it belongs on
    # instead of nothing at all — which is what it used to show.
    free_segments = _cache["free_segments"]
    if free_segments and _cache["free_kd"] is None:
        _cache["free_kd"] = build_segment_kd(free_segments)
    results = []
    for co in sel:
        results.extend(classify_boundary_vertex(
            co, _cache["pairs"], _cache["pair_kd"], _cache["pair_kd_entries"],
            free_segments, _cache["free_kd"],
            max_distance=props.max_distance_uv * _flat_scale(),
            bond_distance=props.bond_distance * _flat_scale(),
        ))
    if not results:
        return

    z = props.z_offset
    z_cross = z + 0.022   # above the regular ghost crosses (z+0.02)
    z_conn  = z + 0.0195
    s = max(props.ghost_cross_size, 0.001) * 1.5

    cross_coords = []
    free_coords = []
    conn_coords = []
    for r in results:
        opp = Vector((r["opposite_pt"].x, r["opposite_pt"].y, z_cross))
        arms = [
            opp + Vector((-s, 0, 0)), opp + Vector((s, 0, 0)),
            opp + Vector((0, -s, 0)), opp + Vector((0, s, 0)),
        ]
        (free_coords if r.get("side") == "free" else cross_coords).extend(arms)
        src = Vector((r["source_pt"].x, r["source_pt"].y, z_conn))
        dst = Vector((r["opposite_pt"].x, r["opposite_pt"].y, z_conn))
        conn_coords += [src, dst]

    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    conn_batch  = batch_for_shader(shader, 'LINES', {"pos": conn_coords})
    _draw_single(conn_batch, COLOR_PRESETS["WHITE"], 2.0, vp)
    if cross_coords:
        cross_batch = batch_for_shader(shader, 'LINES', {"pos": cross_coords})
        _draw_single(cross_batch, COLOR_PRESETS["GREEN"], 3.0, vp)
    if free_coords:
        # Yellow, matching the Free Edges line overlay, so it is obvious the
        # marker is a point on the outline and not a sewn counterpart.
        free_batch = batch_for_shader(shader, 'LINES', {"pos": free_coords})
        _draw_single(free_batch, COLOR_PRESETS["YELLOW"], 3.0, vp)


def _blf_label(font_id, px, text, col):
    import blf
    blf.position(font_id, px.x + 8.0, px.y + 8.0, 0.0)
    blf.color(font_id, *col)
    blf.draw(font_id, text)


def _build_island_finder(bm):
    """Union-find over a bmesh's edges → callable find(index) returning the
    connected-component (island) root. Retopo panels are separate components,
    so this lets us group partner verts by which panel they belong to."""
    n = len(bm.verts)
    parent = list(range(n))

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for e in bm.edges:
        ra = find(e.verts[0].index)
        rb = find(e.verts[1].index)
        if ra != rb:
            parent[ra] = rb
    return find


def draw_seam_parity_text(props, top, region, rv3d):
    """Draw the selected seam's vertex count (A) and the partner side's, broken
    down per partner panel (B), at each seam location.

    A (headline, RELIABLE): number of selected boundary verts that sit on a seam
    — just count what's selected, so select either side to read its exact count.

    B (per-panel breakdown): follow the same all-partners connectors that Show
    Pair draws to the nearest retopo vert on each partner island (panel), and
    tally them per panel. A 3-way garment junction (sleeve ↔ front ↔ back) thus
    shows e.g. "front B:8 / back B:1" instead of one misleading lumped number.
    When there's a single partner panel we count every partner vert in range
    (catches a denser partner); at junctions we count what the connectors hit
    (avoids bleeding the count across panels). Green when a panel matches A,
    orange when not.
    """
    import bmesh as _bmesh
    import blf
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from .ghost import find_all_opposite_points, build_pair_kd

    retopo = top.retopo_obj
    if retopo is None or retopo.type != 'MESH' or retopo.mode != 'EDIT':
        return
    if not _cache["pairs"]:
        return

    bm = _bmesh.from_edit_mesh(retopo.data)
    bm.edges.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    boundary_idx = set()
    for e in bm.edges:
        if e.is_boundary:
            boundary_idx.add(e.verts[0].index)
            boundary_idx.add(e.verts[1].index)
    # A generated boundary starts life as WIRE — its edges carry no face, and
    # BMEdge.is_boundary is False for those, so the set above comes back empty
    # and this overlay used to bail out during exactly the work it exists for
    # (counting vertices while building the 2D boundary). Show Pair already
    # allows face-less verts; match it, here and for the partner candidates
    # below, which are drawn from the same set.
    for v in bm.verts:
        if not v.link_faces:
            boundary_idx.add(v.index)
    if not boundary_idx:
        return

    mw = retopo.matrix_world
    sel = [(v.index, mw @ v.co) for v in bm.verts
           if v.select and v.index in boundary_idx]
    if not sel:
        return

    if _cache["pair_kd"] is None:
        _cache["pair_kd"], _cache["pair_kd_entries"] = build_pair_kd(_cache["pairs"])

    max_d = props.max_distance_uv * _flat_scale()
    md2 = max_d * max_d
    kd = _cache["pair_kd"]
    entries = _cache["pair_kd_entries"]

    sel_indices = {idx for idx, _ in sel}
    # Candidate partner verts (everything on a boundary except the selection).
    other_boundary = [(bi, mw @ bm.verts[bi].co)
                      for bi in boundary_idx if bi not in sel_indices]
    find_island = _build_island_finder(bm)

    # A side (reliable): selected verts that sit on a seam.
    # Partner side: follow each selected vert's partner connector(s) — the same
    # all-partners data Show Pair draws — to the nearest retopo vert on each
    # partner island, and tally distinct verts per island. Also keep the partner
    # positions so a clean single-panel seam can be re-counted by range.
    a_world = []
    opposites = []
    b_by_island = {}     # island root -> set of partner vert indices (connector-hit)
    for _idx, co in sel:
        res = find_all_opposite_points(
            co, _cache["pairs"], max_distance=max_d, kd=kd, entries=entries,
        )
        if not res:
            continue
        a_world.append(co)
        for r in res:
            op = r["opposite_pt"]
            opposites.append(op)
            best_bi = None
            best_d2 = md2
            for bi, bw in other_boundary:
                d2 = (bw.x - op.x) ** 2 + (bw.y - op.y) ** 2
                if d2 < best_d2:
                    best_d2 = d2
                    best_bi = bi
            if best_bi is not None:
                b_by_island.setdefault(find_island(best_bi), set()).add(best_bi)
    if not a_world:
        return
    a_count = len(a_world)

    font_id = 0
    blf.size(font_id, 18)

    # A headline at the selection centroid (neutral colour — it's just the count).
    a_cent = sum(a_world, Vector()) / len(a_world)
    a_px = location_3d_to_region_2d(region, rv3d, a_cent)
    if a_px is not None:
        _blf_label(font_id, a_px, f"A:{a_count}", COLOR_PRESETS["YELLOW"])

    if not b_by_island:
        return

    # Build per-island B counts. Single partner panel → count every partner vert
    # within range of the connectors (catches a denser partner). Junction (≥2
    # panels) → count only the connector-hit verts per panel (no cross-panel
    # bleed), and skip the OK/≠ verdict since "match" is ambiguous there.
    single = (len(b_by_island) == 1)
    if single:
        # Count the partner verts ON the selected stretch: within a couple of
        # millimetres of a connector's opposite point, or of the seam between
        # two neighbouring opposite points (that is where a denser partner's
        # extra verts sit). NOT within Max Seam Distance of a connector — at
        # the default 20 mm that radius is the generated spacing itself, so a
        # single selected vertex read B:2 or B:3 from its partner's neighbours
        # 18.8-19.4 mm away (measured on a jacket, 2026-09-05) while the seam
        # was perfectly matched.
        isl = next(iter(b_by_island))
        tol2 = (max(props.bond_distance, 0.002) * _flat_scale()) ** 2
        segs = []
        for i, o1 in enumerate(opposites):
            for o2 in opposites[i + 1:]:
                if (o1 - o2).length_squared <= md2:
                    segs.append((o1, o2))

        def _on_stretch(bw):
            for op in opposites:
                if (bw.x - op.x) ** 2 + (bw.y - op.y) ** 2 <= tol2:
                    return True
            for o1, o2 in segs:
                d = o2 - o1
                L2 = d.length_squared
                if L2 <= 1e-18:
                    continue
                u = max(0.0, min(1.0, (bw - o1).dot(d) / L2))
                if (bw - (o1 + d * u)).length_squared <= tol2:
                    return True
            return False

        in_range = [(bi, bw) for bi, bw in other_boundary
                    if find_island(bi) == isl and _on_stretch(bw)]
        panels = [(isl, set(bi for bi, _ in in_range))] if in_range else []
    else:
        panels = sorted(b_by_island.items(), key=lambda kv: -len(kv[1]))

    for _isl, bset in panels:
        if not bset:
            continue
        count = len(bset)
        cent = sum((mw @ bm.verts[bi].co for bi in bset), Vector()) / len(bset)
        px = location_3d_to_region_2d(region, rv3d, cent)
        if px is None:
            continue
        match = (count == a_count)
        col = COLOR_PRESETS["GREEN"] if match else COLOR_PRESETS["ORANGE"]
        txt = f"B:{count}"
        if single:
            txt += "  " + ("OK" if match else "≠")
        _blf_label(font_id, px, txt, col)


def _draw_callback_px():
    """POST_PIXEL pass — screen-space text (blf can't draw in POST_VIEW)."""
    context = bpy.context
    if not hasattr(context, 'scene') or context.scene is None:
        return
    top = getattr(context.scene, "ac9_cloth_retopo", None)
    if top is None:
        return
    if not getattr(top, "show_overlays", True):
        return
    props = top.seam
    if not getattr(props, "show_seam_parity", False):
        return
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None:
        return
    try:
        draw_seam_parity_text(props, top, region, rv3d)
    except Exception:
        pass


def _draw_callback_3d():
    context = bpy.context
    if not hasattr(context, 'scene') or context.scene is None:
        return
    top = getattr(context.scene, "ac9_cloth_retopo", None)
    if top is None:
        return
    if not getattr(top, "show_overlays", True):
        return

    region = context.region
    if region is None:
        return
    vp = (float(region.width), float(region.height))

    props = top.seam

    # Lazy rebuild when z_offset / cross_size changed via property callbacks
    if _cache["seam_dirty"]:
        if _cache["pairs"]:
            build_seam_batches(_cache["pairs"], props.z_offset)
        if _cache["free_segments"]:
            build_free_edge_batch(_cache["free_segments"], props.z_offset)
        if _cache["symmetry_segments"]:
            build_symmetry_batches(_cache["symmetry_segments"], props.z_offset)
        if _cache["crease_segments"]:
            build_crease_batch(_cache["crease_segments"], props.z_offset)
        if _cache["mirror_pair_segments"]:
            build_mirror_pair_batches(_cache["mirror_pair_segments"], props.z_offset)
        _cache["seam_dirty"] = False

    # Clamp cross size — a stored value of 0.0 (bad scene state) would make
    # all crosses invisible; ensure it's never below the property minimum.
    cross_size = max(props.ghost_cross_size, 0.001)

    if _cache["ghost_dirty"]:
        # Rebuild even when the cache just emptied out -- gating this on
        # `and _cache["ghost_results"]` skipped the rebuild in exactly that
        # case, so the OLD crosses stayed on screen after the ghosts were
        # cleared (same bug the topo_corners / density_pins branches below
        # already carry a note about). build_ghost_batches / _snap_rings
        # both blank their batch on an empty input, so this is safe.
        build_ghost_batches(
            _cache["ghost_results"], props.z_offset, cross_size
        )
        build_snap_rings_batch(
            _cache["ghost_results"], props.z_offset,
            props.snap_distance * _flat_scale()
        )
        _cache["ghost_dirty"] = False

    gpu.state.depth_test_set('ALWAYS')
    gpu.state.blend_set('ALPHA')

    # Free edges first, so where a free edge and a sewn seam share a screen
    # position the cyan seam is the one you see.
    if getattr(props, "show_free_edges", False):
        c = COLOR_PRESETS.get(props.free_edge_color, COLOR_PRESETS["YELLOW"])
        _draw_single(_batches["free"], c, props.guide_line_width, vp)

    if props.show_seam_guides:
        c = COLOR_PRESETS.get(props.guide_color, COLOR_PRESETS["CYAN"])
        _draw_single(_batches["seam"], c, props.guide_line_width, vp)

    if props.show_pair_lines:
        c = COLOR_PRESETS.get(props.guide_color, COLOR_PRESETS["CYAN"])
        dim = (c[0] * 0.6, c[1] * 0.6, c[2] * 0.6, 0.65)
        _draw_single(_batches["pair"], dim, max(1.0, props.guide_line_width * 0.7), vp)

    # Seam status verdicts, drawn over the seam lines they describe.
    if getattr(props, "show_seam_status", False):
        width = max(2.0, props.guide_line_width * 1.6)
        for state, color in STATUS_COLORS.items():
            _draw_single(_batches.get(f"status_{state}"), color, width, vp)

    # Anchors last of the line overlays: they are reference points you read
    # while looking at everything else, so they sit on top.
    if getattr(props, "show_anchors", False):
        # Track the Guide's Flat SK value so a manual slider crossing the
        # 2D<->3D boundary also flips which anchor-batch variant is drawn —
        # same polling pattern clo_projector/gpu_overlay.py uses for its own
        # last_flat_sk_val.
        guide_obj = top.guide_obj
        flat_sk_name = top.guide_flat_shapekey
        flat_val = 1.0
        if guide_obj is not None and flat_sk_name:
            sk_data = getattr(guide_obj.data, "shape_keys", None)
            if sk_data and flat_sk_name in sk_data.key_blocks:
                flat_val = sk_data.key_blocks[flat_sk_name].value
        if abs(flat_val - _cache["anchor_last_flat_sk_val"]) > 0.001:
            _cache["anchor_dirty"] = True
            _cache["anchor_last_flat_sk_val"] = flat_val

        if _cache["anchor_dirty"] and _cache["anchors"]:
            if flat_val < 0.5 and guide_obj is not None and _cache["anchor_indices"]:
                # Guide in 3D: draw crosses at the anchors' real 3D (Basis)
                # position instead of the flat-layout stacked overlay plane.
                build_anchor_batch_3d(guide_obj, _cache["anchor_indices"],
                                      props.anchor_cross_size)
            else:
                build_anchor_batch(_cache["anchors"], props.z_offset,
                                   props.anchor_cross_size)
            _cache["anchor_dirty"] = False
        if _batches["anchors"] is not None:
            c = COLOR_PRESETS.get(props.anchor_color, COLOR_PRESETS["ORANGE"])
            _draw_single(_batches["anchors"], c,
                         max(2.0, props.guide_line_width * 1.4), vp)

    # Corners / Pins belong to the Density family (Experimental): a .blend
    # saved while they were on would otherwise keep drawing them with no
    # toggle left to switch off (review finding, 2026-09-09).
    if (getattr(props, "show_topo_corners", False)
            and uic.experimental_enabled(bpy.context)):
        if _cache["topo_corner_dirty"]:
            # Rebuild even when the cache just emptied out (the last corner
            # was cleared) -- gating this on `and _cache["topo_corners"]`
            # skipped the rebuild in exactly that case, leaving the OLD
            # (non-empty) batch on screen forever after a Clear (measured
            # 2026-09-02 on density_pins, the sibling overlay copied from
            # this one: "Cleared 1 pin(s); 0 left" reported correctly but
            # the marker stayed drawn).
            if _cache["topo_corners"]:
                build_topo_corner_batch(_cache["topo_corners"], props.z_offset,
                                        props.topo_corner_size)
            else:
                _batches["topo_corners"] = None
            _cache["topo_corner_dirty"] = False
        if _batches["topo_corners"] is not None:
            c = COLOR_PRESETS.get(props.topo_corner_color,
                                  COLOR_PRESETS["CYAN"])
            _draw_single(_batches["topo_corners"], c,
                         max(2.0, props.guide_line_width * 1.4), vp)

    if (getattr(props, "show_density_pins", False)
            and uic.experimental_enabled(bpy.context)):
        if _cache["density_pin_dirty"]:
            # See the topo_corners branch just above for why this rebuilds
            # even on an empty cache (a Clear must actually blank the
            # batch, not just skip touching it).
            if _cache["density_pins"]:
                build_density_pin_batch(_cache["density_pins"], props.z_offset,
                                        props.density_pin_size)
            else:
                _batches["density_pins"] = None
            _cache["density_pin_dirty"] = False
        if _batches["density_pins"] is not None:
            c = COLOR_PRESETS.get(props.density_pin_color,
                                  COLOR_PRESETS["WHITE"])
            _draw_single(_batches["density_pins"], c,
                         max(2.0, props.guide_line_width * 1.4), vp)

    if props.show_symmetry_axis and _batches["symmetry"] is not None:
        c = COLOR_PRESETS.get(props.symmetry_color, COLOR_PRESETS["GREEN"])
        _draw_single(_batches["symmetry"], c, props.symmetry_line_width, vp)

    if getattr(props, "show_crease_lines", False) and _batches["crease"] is not None:
        c = COLOR_PRESETS.get(props.crease_color, COLOR_PRESETS["BLUE"])
        _draw_single(_batches["crease"], c, props.guide_line_width, vp)

    if getattr(props, "show_mirror_pairs", False) and _batches["mirror_pair"] is not None:
        c = COLOR_PRESETS.get(props.mirror_pair_color, COLOR_PRESETS["MAGENTA"])
        _draw_single(_batches["mirror_pair"], c, props.mirror_pair_line_width, vp)

    if props.show_ghost_points:
        # Placed ghosts (partner already exists) are confirmation only — hide
        # them when "Only Unplaced" is on so the actionable gaps stand out.
        if not props.show_only_unplaced:
            cp = COLOR_PRESETS.get(props.ghost_color_placed, COLOR_PRESETS["GREEN"])
            _draw_single(_batches["cross_placed"], cp, props.ghost_line_width, vp)
        cu = COLOR_PRESETS.get(props.ghost_color_unplaced, COLOR_PRESETS["RED"])
        _draw_single(_batches["cross_unplaced"], cu, props.ghost_line_width, vp)
        # The orphan end of the same warning. Same colour as the cross so the
        # two read as one pair; different shape (ring vs cross) so it is still
        # obvious which end is the vertex and which is the empty slot.
        if getattr(props, "show_orphan_rings", False):
            _draw_single(_batches["orphan_rings"], cu,
                         max(1.0, props.ghost_line_width), vp)

    if props.show_ghost_lines:
        c = COLOR_PRESETS.get(props.ghost_line_color, COLOR_PRESETS["MAGENTA"])
        key = ("conn_unplaced"
               if getattr(props, "ghost_lines_unplaced_only", False) else "conn")
        _draw_single(_batches[key], c, max(1.0, props.ghost_line_width * 0.7), vp)

    # Permanent snap-radius circles (one per ghost, always visible when toggled)
    if props.show_snap_radius and _batches["snap_rings"] is not None:
        c = COLOR_PRESETS.get(props.ghost_color_unplaced, COLOR_PRESETS["RED"])
        dim = (c[0] * 0.5, c[1] * 0.5, c[2] * 0.5, 0.5)
        _draw_single(_batches["snap_rings"], dim, 1.0, vp)

    # Active-snap feedback ring (drawn only during Ghost Snap Move modal)
    if _cache["snap_active"] and _batches["snap_ring"] is not None:
        _draw_single(_batches["snap_ring"], COLOR_PRESETS["WHITE"], 2.0, vp)

    # Selected-vertex pair preview (live, per-frame, separate feature).
    if props.show_selected_ghost:
        try:
            draw_selected_pair(props, top, vp)
        except Exception:
            pass

    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.blend_set('NONE')


# ─────────────────────────────────────────────────────────────
# Register / unregister
# ─────────────────────────────────────────────────────────────

def register_draw_handler():
    global _draw_handle, _draw_handle_px
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback_3d, (), 'WINDOW', 'POST_VIEW'
        )
    if _draw_handle_px is None:
        _draw_handle_px = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback_px, (), 'WINDOW', 'POST_PIXEL'
        )
    if _load_post_handler not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post_handler)
    _register_guide_invalidation_handler()


def unregister_draw_handler():
    global _draw_handle, _draw_handle_px
    while _load_post_handler in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post_handler)
    _unregister_depsgraph_handler()
    _unregister_guide_invalidation_handler()
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    if _draw_handle_px is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle_px, 'WINDOW')
        _draw_handle_px = None
    for key in list(_batches.keys()):
        _batches[key] = None
    # One list, in reset_guide_derived / reset_retopo_derived — this used to
    # keep its own copy and drifted (it never cleared the Seam Status or the
    # Corner / Pin caches).
    reset_guide_derived(counters=False)
    reset_retopo_derived(counters=False)


def register_keymap():
    from . import operators
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc is None:
        return
    km  = kc.keymaps.new(name='Mesh', space_type='EMPTY')
    # Both bind G; each operator's poll() gates on its own mode toggle, so only
    # the active one runs. Ghost Snap and UV Seam Snap are mutually exclusive.
    kmi = km.keymap_items.new(operators.AC9_OT_ghost_snap_move.bl_idname, 'G', 'PRESS')
    _addon_keymaps.append((km, kmi))
    kmi2 = km.keymap_items.new(operators.AC9_OT_uv_seam_snap_move.bl_idname, 'G', 'PRESS')
    _addon_keymaps.append((km, kmi2))


def unregister_keymap():
    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _addon_keymaps.clear()
