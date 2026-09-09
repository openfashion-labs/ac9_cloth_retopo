"""Ghost point computation.

Given seam pairs (endpoints in Flat-SK world space) and the current retopo
vertex positions (also in world space), find for each retopo boundary vertex
the "opposite-side" seam position — the mirror point across the seam. These
become the ghost cross overlays in the viewport.

Both sides live in the SAME coordinate space (FlattenUV normalised 0–1 m,
transformed to world by each object's matrix_world), so distances are real
and `max_distance` is a meaningful threshold — no scale mapping anywhere.

A KDTree over seam-segment midpoints provides the fast path: each retopo
vertex queries only its nearest few segments instead of all N pairs.
"""

import bmesh
from mathutils import Vector


def _world_positions(obj, indices_iter):
    matrix = obj.matrix_world
    return [matrix @ obj.data.vertices[i].co for i in indices_iter]


def get_retopo_vertex_positions(obj, selected_only=False):
    """Return WORLD-space vertex positions from the retopo object.

    Reads from the live BMesh when the object is in Edit Mode, or from the
    stored mesh data otherwise.
    """
    matrix = obj.matrix_world
    positions = []
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        for v in bm.verts:
            if (not selected_only) or v.select:
                positions.append(matrix @ v.co)
    else:
        for v in obj.data.vertices:
            if (not selected_only) or v.select:
                positions.append(matrix @ v.co)
    return positions


def get_retopo_boundary_vertex_positions(obj):
    """Return WORLD-space positions of open-boundary vertices only.

    Uses bmesh edge.is_boundary (edges belonging to exactly one face) to
    identify the retopo silhouette — no selection required.  This reliably
    picks up the boundary loop that sits on CLO UV seam edges.

    Works in both Edit Mode (reads the live BMesh) and Object Mode.
    """
    matrix = obj.matrix_world
    positions = []

    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        boundary_verts = set()
        for e in bm.edges:
            if e.is_boundary:
                boundary_verts.add(e.verts[0].index)
                boundary_verts.add(e.verts[1].index)
        for v in bm.verts:
            if v.index in boundary_verts:
                positions.append(matrix @ v.co)
    else:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        boundary_verts = set()
        for e in bm.edges:
            if e.is_boundary:
                boundary_verts.add(e.verts[0].index)
                boundary_verts.add(e.verts[1].index)
        for v in bm.verts:
            if v.index in boundary_verts:
                positions.append(matrix @ v.co)
        bm.free()

    return positions


def build_pair_kd(seam_pairs):
    """Build a KDTree over both seam-segment midpoints (one entry per side).

    Returns (kdtree, entries) where entries[i] = (pair_index, side) and side
    is 'A' or 'B'.  Query with find_n((x, y, 0.0), k) to get candidate
    segments near a point; resolve back via entries[index].
    """
    from mathutils.kdtree import KDTree

    entries = []
    for pi in range(len(seam_pairs)):
        entries.append((pi, 'A'))
        entries.append((pi, 'B'))

    kd = KDTree(len(entries))
    for ei, (pi, side) in enumerate(entries):
        pair = seam_pairs[pi]
        if side == 'A':
            mid = (pair["a_p1"] + pair["a_p2"]) * 0.5
        else:
            mid = (pair["b_p1"] + pair["b_p2"]) * 0.5
        kd.insert((mid.x, mid.y, 0.0), ei)
    kd.balance()
    return kd, entries


def build_segment_kd(segments):
    """Build a KDTree over segment midpoints. segments = list of (Vector, Vector).

    Returns the KDTree; query find_n((x,y,0), k) gives indices straight into
    `segments` (no entries table needed — one entry per segment).
    """
    from mathutils.kdtree import KDTree

    kd = KDTree(len(segments))
    for i, (p1, p2) in enumerate(segments):
        mid = (p1 + p2) * 0.5
        kd.insert((mid.x, mid.y, 0.0), i)
    kd.balance()
    return kd


def nearest_segment_point(co, segments, kd=None, n_candidates=16):
    """Nearest point on ANY segment to world point `co` (XY plane).

    Generalises nearest_seam_point to a plain segment list — used to snap onto
    the full guide pattern outline (every boundary edge, sewn seam or free
    edge), not just sewn seam pairs. Returns (Vector3, distance); Z interpolated
    along the segment. Returns (None, inf) when there are no segments.
    """
    from .analysis import _closest_point_on_segment_2d

    if not segments:
        return None, float('inf')
    if kd is None:
        kd = build_segment_kd(segments)

    p = Vector((co.x, co.y))
    best_pt = None
    best_d = float('inf')
    k = min(n_candidates, len(segments))
    for _loc, si, _dist in kd.find_n((co.x, co.y, 0.0), k):
        s1, s2 = segments[si]
        q, t, d = _closest_point_on_segment_2d(
            p, Vector((s1.x, s1.y)), Vector((s2.x, s2.y))
        )
        if d < best_d:
            best_d = d
            z = s1.z + (s2.z - s1.z) * t
            best_pt = Vector((q.x, q.y, z))
    return best_pt, best_d


def nearest_seam_point(co, seam_pairs, kd=None, entries=None, n_candidates=16):
    """Nearest point on ANY seam segment to world point `co` (XY plane).

    Returns (Vector3, distance) — the closest point lies on the nearest seam
    line (either side of any pair). Z is interpolated along the segment.
    Returns (None, inf) when there are no pairs.
    """
    from .analysis import _closest_point_on_segment_2d

    if not seam_pairs:
        return None, float('inf')
    if kd is None or entries is None:
        kd, entries = build_pair_kd(seam_pairs)

    p = Vector((co.x, co.y))
    best_pt = None
    best_d = float('inf')
    k = min(n_candidates, len(entries))
    for _loc, ei, _dist in kd.find_n((co.x, co.y, 0.0), k):
        pi, side = entries[ei]
        pair = seam_pairs[pi]
        if side == 'A':
            s1, s2 = pair["a_p1"], pair["a_p2"]
        else:
            s1, s2 = pair["b_p1"], pair["b_p2"]
        q, t, d = _closest_point_on_segment_2d(
            p, Vector((s1.x, s1.y)), Vector((s2.x, s2.y))
        )
        if d < best_d:
            best_d = d
            z = s1.z + (s2.z - s1.z) * t
            best_pt = Vector((q.x, q.y, z))
    return best_pt, best_d


def find_all_opposite_points(co, seam_pairs, kd=None, entries=None,
                             max_distance=0.02, n_candidates=64):
    """ALL opposite-side ghost points for a single retopo vertex.

    Unlike find_opposite_points_for_retopo_vertices (one nearest partner per
    vertex), this returns EVERY pair whose NEAR side is within max_distance of
    `co`. At an N-way junction (folded hem, pocket attachment, 3+ panels meeting
    at one 3D location) the vertex genuinely has 2+ counterparts — split into
    separate flat positions — and this surfaces all of them, not just one.

    A high-poly guide samples each seam into MANY tiny edges, so one vertex sits
    within max_distance of dozens of segments that all belong to the SAME
    partner. Returning them all sprays the opposite seam with crosses. We instead
    cluster candidate opposites by proximity and keep ONE representative (the
    nearest near-side) per cluster. Distinct partners at an N-way junction live
    on different panels — far apart in the flat layout — so they survive as
    separate clusters, while the single-partner spray collapses to one cross.

    Returns a list of dicts: {source_pt, opposite_pt, distance, side}.
    """
    from .analysis import _closest_point_on_segment_2d

    if not seam_pairs:
        return []
    if kd is None or entries is None:
        kd, entries = build_pair_kd(seam_pairs)

    p = Vector((co.x, co.y))
    k = min(n_candidates, len(entries))

    # 1. Collect every candidate whose NEAR side is within max_distance.
    cands = []
    for _loc, ei, _dist in kd.find_n((co.x, co.y, 0.0), k):
        pi, side = entries[ei]
        pair = seam_pairs[pi]
        if side == 'A':
            s1, s2 = pair["a_p1"], pair["a_p2"]
            o1, o2 = pair["b_p1"], pair["b_p2"]
            tag = "A_to_B"
        else:
            s1, s2 = pair["b_p1"], pair["b_p2"]
            o1, o2 = pair["a_p1"], pair["a_p2"]
            tag = "B_to_A"
        _q, t, d = _closest_point_on_segment_2d(
            p, Vector((s1.x, s1.y)), Vector((s2.x, s2.y))
        )
        if d > max_distance:
            continue
        opposite = o1 + (o2 - o1) * t
        cands.append((d, opposite, tag))

    if not cands:
        return []

    cands.sort(key=lambda c: c[0])

    # 1b. ON-SEAM gate: keep only pairs whose NEAR side is the one this vertex
    #     actually sits on. At a 3-way junction (edges A,B,C coincident → pairs
    #     A-B, A-C, B-C) a vert on edge A is at distance ~0 from A-B and A-C
    #     (their near side IS edge A) but only matches B-C because edge B/C is
    #     coincidentally within max_distance in the packed flat layout. That
    #     B-C cross is "indirect" — the vert isn't on that seam. Both genuine
    #     partners (A-B, A-C) share edge A, so they sit at the same minimum
    #     near-distance; the indirect B-C is farther. Keep d ≈ d_min only.
    #
    # Use 0.30 (not 0.15) of max_distance as the tolerance band. When the guide
    # has many Mark-Sharp interior edges (pocket projection or importer artefacts),
    # spurious pairs scatter B-side midpoints around the flat space. One of those
    # B-sides may accidentally lie closer to the vertex than the genuine seam pair
    # (d_min_spurious < d_min_genuine), which with a tight 0.15 tolerance cuts the
    # genuine seam out of the candidate list entirely.  Doubling to 0.30 gives a
    # wide enough band to keep the genuine pair while still rejecting the truly
    # indirect B-C cross at a 3-way junction (that sits on a different island,
    # many cm away in the flat layout).
    d_min = cands[0][0]
    on_seam_tol = max(0.30 * max_distance, 0.001)
    cands = [c for c in cands if c[0] <= d_min + on_seam_tol]

    # 2. Cluster by opposite proximity, nearest-first; one representative each.
    #    The single-partner spread is roughly the matched near-segment extent
    #    (≤ ~2·max_distance), while separate panels are much farther apart.
    cluster_r = 2.5 * max_distance
    out = []
    for d, opposite, tag in cands:
        if any((opposite - r["opposite_pt"]).length <= cluster_r for r in out):
            continue
        out.append({
            "source_pt": Vector((co.x, co.y, co.z)),
            "opposite_pt": opposite.copy(),
            "distance": d,
            "side": tag,
        })
    return out


def find_opposite_points_for_retopo_vertices(
    retopo_positions, seam_pairs, max_distance=0.01,
    kd=None, entries=None, n_candidates=12,
):
    """For each retopo vertex near a seam, find its opposite-side ghost position.

    retopo_positions and seam-pair endpoints are both in world space (FlattenUV
    flat layout).  For each vertex we query the KDTree for the nearest segment
    midpoints, measure the precise distance to those segments, and map the
    closest segment's t-parameter onto the opposite side.

    Returns a list of dicts with keys: source_pt, opposite_pt, distance, side.
    Only vertices within max_distance of a seam edge are included.
    """
    from .analysis import _closest_point_on_segment_2d

    if not seam_pairs:
        return []
    if kd is None or entries is None:
        kd, entries = build_pair_kd(seam_pairs)

    k = min(n_candidates, len(entries))
    results = []
    for co in retopo_positions:
        p = Vector((co.x, co.y))
        best = None
        for _loc, ei, _dist in kd.find_n((co.x, co.y, 0.0), k):
            pi, side = entries[ei]
            pair = seam_pairs[pi]
            if side == 'A':
                s1, s2 = pair["a_p1"], pair["a_p2"]
                o1, o2 = pair["b_p1"], pair["b_p2"]
                tag = "A_to_B"
            else:
                s1, s2 = pair["b_p1"], pair["b_p2"]
                o1, o2 = pair["a_p1"], pair["a_p2"]
                tag = "B_to_A"
            _q, t, d = _closest_point_on_segment_2d(
                p, Vector((s1.x, s1.y)), Vector((s2.x, s2.y))
            )
            if best is None or d < best["distance"]:
                opposite = o1 + (o2 - o1) * t
                best = {
                    "source_pt": Vector((co.x, co.y, co.z)),
                    "opposite_pt": opposite.copy(),
                    "distance": d,
                    "side": tag,
                }
        if best and best["distance"] <= max_distance:
            results.append(best)
    return results
