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
}

COLOR_ITEMS = [
    ("WHITE",   "White",   ""),
    ("YELLOW",  "Yellow",  ""),
    ("CYAN",    "Cyan",    ""),
    ("MAGENTA", "Magenta", ""),
    ("GREEN",   "Green",   ""),
    ("ORANGE",  "Orange",  ""),
    ("RED",     "Red",     ""),
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
    "symmetry_segments": [],   # fold (centre) lines of self-symmetric islands, flat world
    "symmetry_folds":  [],     # detection RESULT: [{"root","a","b"}] per fold axis —
                               # what Symmetrize Island consumes (root -> which island,
                               # a/b -> the axis, through the island's OWN centroid)
    "symmetry_kd":     None,   # KDTree over fold-segment midpoints (fold snap)
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
    "anchor_spans":    0,     # how many spans those anchors cut the boundary into
    "anchor_dirty":    False,
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
    "pair":           None,
    "cross_unplaced": None,   # ghost whose partner vert is NOT yet placed
    "cross_placed":   None,   # ghost whose partner vert already exists nearby
    "conn":           None,
    "snap_ring":  None,   # active-snap feedback (modal only)
    "snap_rings": None,   # permanent snap-radius circles around all ghosts
    "symmetry":   None,   # fold (centre) lines of self-symmetric islands
    "mirror_pair": None,  # centroid connectors for detected cross-island mirror pairs
    "status_matched":   None,  # seam status overlay, one batch per state
    "status_mismatch":  None,
    "status_one_sided": None,
    "anchors":    None,   # crosses at the Guide's span-dividing anchor points
    "topo_corners": None, # diamonds at the user's topology corners
    "density_pins": None, # squares at the user's density-pinned vertices
}

#: Seam status colours. Fixed rather than user-configurable — the whole point
#: is that the same colour always means the same verdict.
STATUS_COLORS = {
    "matched":   (0.15, 0.85, 0.35, 0.95),   # green  — paired and aligned
    "mismatch":  (1.00, 0.20, 0.20, 0.95),   # red    — both sides, disagreeing
    "one_sided": (1.00, 0.65, 0.10, 0.95),   # orange — only one side authored
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
            _classify_ghosts_placed(results, positions, self.bond_distance)
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

    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    _batches["seam"] = (
        batch_for_shader(shader, 'LINES', {"pos": seam_coords})
        if seam_coords else None
    )
    _batches["pair"] = (
        batch_for_shader(shader, 'LINES', {"pos": pair_coords})
        if pair_coords else None
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
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
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
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
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
        for state in ("matched", "mismatch", "one_sided"):
            _batches[f"status_{state}"] = None
        return
    for state in ("matched", "mismatch", "one_sided"):
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
    _cache["anchor_spans"] = 0


def clear_seam_status_batches():
    for state in ("matched", "mismatch", "one_sided"):
        _batches[f"status_{state}"] = None
    _cache["seam_status_segments"] = {}


def build_ghost_batches(results, z_offset, cross_size):
    # Split crosses by whether the partner vert is already placed (r["matched"]).
    # Unplaced ghosts are the actionable ones — the spots you still need to fill;
    # placed ghosts sit on an existing vert and are mostly confirmation.
    unplaced_coords = []
    placed_coords = []
    conn_coords = []
    z_cross = z_offset + 0.02
    z_conn  = z_offset + 0.018

    for r in results:
        opp = _flat_to_3d(r["opposite_pt"], z_cross)
        s = cross_size
        arms = [
            opp + Vector((-s, 0, 0)), opp + Vector((s, 0, 0)),
            opp + Vector((0, -s, 0)), opp + Vector((0, s, 0)),
        ]
        (placed_coords if r.get("matched") else unplaced_coords).extend(arms)
        src = Vector((r["source_pt"].x, r["source_pt"].y, z_conn))
        dst = Vector((r["opposite_pt"].x, r["opposite_pt"].y, z_conn))
        conn_coords += [src, dst]

    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    _batches["cross_unplaced"] = (
        batch_for_shader(shader, 'LINES', {"pos": unplaced_coords})
        if unplaced_coords else None
    )
    _batches["cross_placed"] = (
        batch_for_shader(shader, 'LINES', {"pos": placed_coords})
        if placed_coords else None
    )
    _batches["conn"] = (
        batch_for_shader(shader, 'LINES', {"pos": conn_coords})
        if conn_coords else None
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
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
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


def rebuild_ghosts(seam_props, retopo_obj, write_stats=True):
    """Recompute ghost results from cached seam pairs + current retopo state.

    seam_props  — the AC9SeamGuideProps sub-property group
    retopo_obj  — the shared retopo Object (from ac9_cloth_retopo.retopo_obj)
    write_stats — write scene["ac9_cloth_retopo_ghost_count"] (skip when called
                  from contexts where writing to ID data is disallowed).
    Returns (ghost_count, retopo_vertex_count).

    Ghost source is always the retopo's open-boundary loop (bmesh
    edge.is_boundary) — that's where CLO UV seams sit.
    """
    from .ghost import (
        get_retopo_boundary_vertex_positions,
        get_retopo_vertex_positions,
        find_all_opposite_points,
        build_pair_kd,
    )

    if retopo_obj is None or retopo_obj.type != 'MESH' or not _cache["pairs"]:
        _cache["ghost_results"] = []
        _batches["cross_unplaced"] = None
        _batches["cross_placed"]   = None
        _batches["conn"]  = None
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
    md = seam_props.max_distance_uv
    kd = _cache["pair_kd"]
    entries = _cache["pair_kd_entries"]
    results = []
    for co in retopo_positions:
        results.extend(find_all_opposite_points(
            co, _cache["pairs"], max_distance=md, kd=kd, entries=entries,
        ))

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
    classify_positions = get_retopo_vertex_positions(retopo_obj)
    _classify_ghosts_placed(results, classify_positions, seam_props.bond_distance)
    n_unplaced = sum(1 for r in results if not r.get("matched"))
    if write_stats:
        print(f"[AC9 Seam] ghosts: {n_raw} raw → {len(results)} unique "
              f"opposite locations ({n_unplaced} unplaced / "
              f"{len(results) - n_unplaced} placed; max pile: {max_pile})")

    _cache["ghost_results"] = results
    _cache["ghost_dirty"] = False
    build_ghost_batches(
        results, seam_props.z_offset,
        max(seam_props.ghost_cross_size, 0.001),
    )
    build_snap_rings_batch(results, seam_props.z_offset, seam_props.snap_distance)

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
    _cache["pairs"] = []
    _cache["pair_kd"] = None
    _cache["pair_kd_entries"] = None
    _cache["boundary_segments"] = []
    _cache["boundary_kd"] = None
    _cache["symmetry_segments"] = []
    _cache["symmetry_folds"] = []
    _cache["symmetry_kd"] = None
    _cache["mirror_pair_segments"] = []
    _cache["mirror_pairs"] = []
    _cache["mirror_pair_rejections"] = []
    _cache["mirror_pair_count"] = 0
    _cache["ghost_results"] = []
    _cache["anchors"] = []
    _cache["anchor_spans"] = 0
    _cache["topo_corners"] = []
    _cache["topo_corner_dirty"] = False
    _cache["density_pins"] = []
    _cache["density_pin_dirty"] = False
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
    from .ghost import find_all_opposite_points, build_pair_kd

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
    # counterpart. Max Seam Distance still suppresses boundary verts (free
    # edges / hems) that aren't near any sewn seam.
    results = []
    for co in sel:
        results.extend(find_all_opposite_points(
            co, _cache["pairs"], max_distance=props.max_distance_uv,
            kd=_cache["pair_kd"], entries=_cache["pair_kd_entries"],
        ))
    if not results:
        return

    z = props.z_offset
    z_cross = z + 0.022   # above the regular ghost crosses (z+0.02)
    z_conn  = z + 0.0195
    s = max(props.ghost_cross_size, 0.001) * 1.5

    cross_coords = []
    conn_coords = []
    for r in results:
        opp = Vector((r["opposite_pt"].x, r["opposite_pt"].y, z_cross))
        cross_coords += [
            opp + Vector((-s, 0, 0)), opp + Vector((s, 0, 0)),
            opp + Vector((0, -s, 0)), opp + Vector((0, s, 0)),
        ]
        src = Vector((r["source_pt"].x, r["source_pt"].y, z_conn))
        dst = Vector((r["opposite_pt"].x, r["opposite_pt"].y, z_conn))
        conn_coords += [src, dst]

    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    cross_batch = batch_for_shader(shader, 'LINES', {"pos": cross_coords})
    conn_batch  = batch_for_shader(shader, 'LINES', {"pos": conn_coords})
    _draw_single(conn_batch, COLOR_PRESETS["WHITE"], 2.0, vp)
    _draw_single(cross_batch, COLOR_PRESETS["GREEN"], 3.0, vp)


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

    max_d = props.max_distance_uv
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
        isl = next(iter(b_by_island))
        in_range = [(bi, bw) for bi, bw in other_boundary
                    if find_island(bi) == isl
                    and any((bw.x - op.x) ** 2 + (bw.y - op.y) ** 2 <= md2
                            for op in opposites)]
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
        if _cache["symmetry_segments"]:
            build_symmetry_batches(_cache["symmetry_segments"], props.z_offset)
        if _cache["mirror_pair_segments"]:
            build_mirror_pair_batches(_cache["mirror_pair_segments"], props.z_offset)
        _cache["seam_dirty"] = False

    # Clamp cross size — a stored value of 0.0 (bad scene state) would make
    # all crosses invisible; ensure it's never below the property minimum.
    cross_size = max(props.ghost_cross_size, 0.001)

    if _cache["ghost_dirty"] and _cache["ghost_results"]:
        build_ghost_batches(
            _cache["ghost_results"], props.z_offset, cross_size
        )
        build_snap_rings_batch(
            _cache["ghost_results"], props.z_offset, props.snap_distance
        )
        _cache["ghost_dirty"] = False

    gpu.state.depth_test_set('ALWAYS')
    gpu.state.blend_set('ALPHA')

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
            _draw_single(_batches[f"status_{state}"], color, width, vp)

    # Anchors last of the line overlays: they are reference points you read
    # while looking at everything else, so they sit on top.
    if getattr(props, "show_anchors", False):
        if _cache["anchor_dirty"] and _cache["anchors"]:
            build_anchor_batch(_cache["anchors"], props.z_offset,
                               props.anchor_cross_size)
            _cache["anchor_dirty"] = False
        if _batches["anchors"] is not None:
            c = COLOR_PRESETS.get(props.anchor_color, COLOR_PRESETS["ORANGE"])
            _draw_single(_batches["anchors"], c,
                         max(2.0, props.guide_line_width * 1.4), vp)

    if getattr(props, "show_topo_corners", False):
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

    if getattr(props, "show_density_pins", False):
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

    if props.show_ghost_lines:
        c = COLOR_PRESETS.get(props.ghost_line_color, COLOR_PRESETS["MAGENTA"])
        _draw_single(_batches["conn"], c, max(1.0, props.ghost_line_width * 0.7), vp)

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
    _cache["pairs"] = []
    _cache["pair_kd"] = None
    _cache["pair_kd_entries"] = None
    _cache["boundary_segments"] = []
    _cache["boundary_kd"] = None
    _cache["symmetry_segments"] = []
    _cache["symmetry_folds"] = []
    _cache["symmetry_kd"] = None
    _cache["mirror_pair_segments"] = []
    _cache["mirror_pairs"] = []
    _cache["mirror_pair_rejections"] = []
    _cache["mirror_pair_count"] = 0
    _cache["ghost_results"] = []
    _cache["anchors"] = []
    _cache["anchor_spans"] = 0


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
