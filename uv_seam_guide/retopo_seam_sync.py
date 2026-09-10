"""Give the retopo's two sides of a sewn seam matching vertices.

The Guide says where the seams are and where they divide (anchor spans, see
anchor_segments). The retopo is authored in the Guide's FLAT space, so each
Guide span has a stretch of retopo boundary running along its flat outline.
This module compares the two sides of every seam pair and creates whatever
one side has and the other lacks.

Why mirroring rather than redistributing
-----------------------------------------
Measured on a production retopo: the one seam with both sides authored had
14 vertices against 13, and thirteen of them already sat at IDENTICAL
parameters along the span, 0.00mm off the outline. The whole difference was
one extra vertex at the far end. Redistributing both sides to a common count
would have moved thirteen perfectly placed vertices to fix one missing one.

So the default operation inserts only what is missing and never moves an
existing vertex. Deliberately changing a span's density is a separate,
explicit action — that one does redistribute, because that is the point.

Placement of a missing vertex, in order of preference:
  * past either end of the target chain -> a new vertex joined to the end
    vertex (extends the boundary; creates no n-gon)
  * target side empty -> a whole new chain of vertices and edges
  * between two existing chain vertices -> splits that retopo boundary edge,
    which turns the adjacent quad into an n-gon. Those are counted and
    reported rather than silently left behind, since a retopo wants quads.
"""

import math

import bmesh
from mathutils import Vector

from . import analysis as an
from . import anchor_segments as anch


def span_point_at_u(span, u, basis_co, flat_co):
    """Position at 3D-arc-length parameter u (0..1) along a Guide span.

    Returns (flat_pos, basis_pos), both world space. Parameterising by 3D
    arc length is what makes the two sides of a seam line up: they are the
    same curve in 3D once sewn, however differently their flat layouts run.
    """
    cum = anch._arc_lengths(span, basis_co)
    total = cum[-1]
    if total <= 1e-12:
        return flat_co[span[0]].copy(), basis_co[span[0]].copy()
    target = min(max(u, 0.0), 1.0) * total
    seg = len(span) - 2
    for i in range(1, len(cum)):
        if cum[i] >= target:
            seg = i - 1
            break
    span_len = cum[seg + 1] - cum[seg]
    t = 0.0 if span_len <= 1e-12 else (target - cum[seg]) / span_len
    v0, v1 = span[seg], span[seg + 1]
    return flat_co[v0].lerp(flat_co[v1], t), basis_co[v0].lerp(basis_co[v1], t)


def _project_to_span(p_flat, span, basis_co, flat_co):
    """(distance, u) of a flat-space point onto a span's flat polyline, with
    u read off the corresponding 3D segment.
    """
    cum = anch._arc_lengths(span, basis_co)
    total = cum[-1]
    best = None
    for i in range(len(span) - 1):
        a, b = flat_co[span[i]], flat_co[span[i + 1]]
        ab = b - a
        d2 = ab.dot(ab)
        if d2 <= 1e-18:
            t, q = 0.0, a
        else:
            t = max(0.0, min(1.0, (p_flat - a).dot(ab) / d2))
            q = a + ab * t
        d = (p_flat - q).length
        if best is None or d < best[0]:
            s = cum[i] + (cum[i + 1] - cum[i]) * t
            best = (d, (s / total) if total > 0 else 0.0)
    return best


def _reparam_existing(entries, run, spans, basis_co, flat_co, max_dist=None):
    """Re-read parameters measured on ORIGINAL spans as parameters of `run`.

    map_retopo_to_spans files every retopo vertex under the span it was
    measured against, so its u runs 0..1 along THAT span. A run assembled by
    chain_free_spans (or merge_seam_rows) covers several of them end to end,
    and — where a span is stored the other way round — can run against their
    direction. Sorting those raw parameters therefore does NOT order the
    vertices along the run: apply_seam_sync builds its chain from that order,
    so it joins the new vertices to the wrong neighbours and draws lines
    straight across the middle of the panel. Measured on the synthetic
    fixture (test_generate_partial_rerun): re-running Generate over a run
    that was half built left a 154.7mm chord where nothing on a clean pass
    exceeds 20.2mm.

    Each parameter is turned back into a flat position on its own span and
    projected onto the run. That is exact, not an approximation:
    span_point_at_u and _project_to_span are inverses on the same polyline —
    both read the flat segment fraction against the same 3D arc length — so a
    span that is part of the run round-trips to the matching point on it, and
    a span stored reversed comes back as 1-u with no special case. Where the
    span is NOT part of the run (its two anchors sit on the run but its body
    does not), the projection still lands the vertex where the run comes
    closest to it, which is the only ordering that means anything there.

    A vertex sitting on an anchor is filed under every span meeting there and
    so arrives more than once. The nearest reading wins and the rest are
    dropped: a chain holding the same vertex twice has no edge between those
    two entries, and apply_seam_sync's mid-insert then finds no edge to split
    and skips the vertex entirely.

    max_dist drops a vertex whose nearest point on the run is further than
    that. The callers gather from every span that TOUCHES the run, which is
    what finds the vertices on a span the run only partly covers (a closed
    loop opened at a corner cuts one span in two, and its half outside the
    cut used to be invisible here). The price is that a span touching the run
    at one end and running away from it brings its whole length along, and
    those vertices are not on this run at all; the distance is what tells the
    two apart.

    `entries` are (span index, u, retopo vertex index) triples. Returns
    [(u along run, retopo vertex index)], sorted.
    """
    best = {}
    for si, u, i in entries:
        p_flat, _b = span_point_at_u(spans[si], u, basis_co, flat_co)
        d, ru = _project_to_span(p_flat, run, basis_co, flat_co)
        if max_dist is not None and d > max_dist:
            continue
        prev = best.get(i)
        if prev is None or d < prev[1]:
            best[i] = (ru, d)
    return sorted((ru, i) for i, (ru, _d) in best.items())


def build_orphan_index(bverts, rpos, assign):
    """KD index of the retopo boundary vertices NO Guide span could claim.

    map_retopo_to_spans files a vertex under the span it is nearest to, so a
    panel that HAS no span — one sewn to nothing, whose boundary loop carries
    no anchor and which build_spans therefore cannot cut (see
    anchor_segments.anchorless_loop_runs) — has none of its vertices filed
    anywhere. The generation planners read that as "nothing here yet" and lay
    the whole chain down again, every single time, however often it has been
    generated before.

    Returns {"kd", "index", "pos"}: the tree, kd key -> retopo vertex index,
    and the world positions the tree was built from.
    """
    from mathutils import kdtree

    claimed = set()
    for entries in assign.values():
        for _u, i, _d in entries:
            claimed.add(i)
    index = [vi for vi in bverts if vi not in claimed]
    kd = kdtree.KDTree(len(index))
    for k, vi in enumerate(index):
        kd.insert(rpos[vi], k)
    kd.balance()
    return {"kd": kd, "index": index, "pos": rpos}


def augment_existing_by_position(existing, run, basis_co, flat_co, orphans,
                                 tol=0.005):
    """Add the run's own orphan vertices (build_orphan_index) to `existing`.

    Only vertices no span claimed are considered, deliberately. A vertex some
    span DID claim has already been arbitrated by nearest-span, and on a
    narrow panel — a placket, a binding, a strap — the outline across the
    panel sits well within `tol` of this run without having anything to do
    with it. Measuring by raw position everywhere would hand those to the
    wrong run.
    """
    if not orphans or not orphans["index"]:
        return existing
    seg = [(flat_co[run[i + 1]] - flat_co[run[i]]).length
           for i in range(len(run) - 1)]
    radius = tol + (max(seg) * 0.5 if seg else 0.0)
    kd, index, pos = orphans["kd"], orphans["index"], orphans["pos"]
    have = {i for _u, i in existing}
    cand = set()
    for v in run:
        for (_co, k, _d) in kd.find_range(flat_co[v], radius):
            cand.add(index[k])
    extra = []
    for i in cand:
        if i in have:
            continue
        d, u = _project_to_span(pos[i], run, basis_co, flat_co)
        if d <= tol:
            extra.append((u, i))
    if not extra:
        return existing
    return sorted(list(existing) + extra)


def covered_intervals(existing, bedge_set):
    """The u stretches of a run that an existing retopo boundary EDGE spans.

    This is the "already built" test, and it is deliberately about edges, not
    vertices. A vertex is a point: a division parameter chosen at a different
    density never lands on one, so a proximity test against vertices lets the
    whole second pass through and the new vertices come to rest ON the old
    edges without joining them. An edge covers its whole stretch, so anything
    inside it is rejected however the density changed.

    `existing` must be sorted by u, as _reparam_existing returns it. Returns
    (u_lo, u_hi, vertex at u_lo, vertex at u_hi) so a chain stopping at the
    interval knows which vertex to join onto.
    """
    if not bedge_set:
        return []
    iv = []
    for (ua, ia), (ub, ib) in zip(existing, existing[1:]):
        if (min(ia, ib), max(ia, ib)) in bedge_set:
            iv.append((ua, ub, ia, ib))
    return iv


def _blocker(u, existing, u_tol, covered, occupied, span, basis_co, flat_co):
    """(vertex on the low side, vertex on the high side), or None to build.

    Says whether this wanted parameter is already taken, and by WHICH retopo
    vertex on each side — the chain that stops here has to join onto it, or
    it ends in mid-air 20mm short of the corner. Measured on a production
    file: 3 such loose ends on one re-generated panel, every one of them at a
    span end whose anchor vertex a neighbouring chain had already placed.
    """
    for ue, ie in existing:
        if abs(u - ue) <= u_tol:
            return ie, ie
    for lo, hi, i_lo, i_hi in covered:
        if lo < u < hi:
            return i_lo, i_hi
    if occupied is not None:
        kd, index = occupied
        flat_pos, _b = span_point_at_u(span, u, basis_co, flat_co)
        hits = kd.find_range(flat_pos, 0.0005)
        if hits:
            best = min(hits, key=lambda h: h[2])
            v = index[best[1]]
            return v, v
    return None


def _divide_missing(us, existing, u_tol, covered, occupied, span,
                    basis_co, flat_co):
    """(flat list of wanted parameters, the same list cut into GROUPS).

    A parameter is dropped when an existing vertex already sits at it
    (u_tol), when an existing boundary edge already covers it
    (covered_intervals), or when some retopo vertex is within 0.5mm of the
    position whatever span it is filed under — generation places vertices
    exactly on the outline, so a position test is exact here.

    The groups carry as much as the list does. Each is

        {"us": [...], "before": vertex index or None, "after": ...}

    where `before`/`after` name the retopo vertex that blocked the parameter
    on either side of the group. apply_seam_sync must not run an edge ACROSS
    a break — that draws the new chain over the blocking vertex and leaves it
    on an edge it is not joined to — but it must join the group's two ends TO
    those vertices, or the outline never closes.
    """
    missing, groups, cur, pending_before = [], [], None, None
    for u in us:
        block = _blocker(u, existing, u_tol, covered, occupied, span,
                         basis_co, flat_co)
        if block is None:
            if cur is None:
                cur = {"us": [], "before": pending_before, "after": None}
                groups.append(cur)
            cur["us"].append(u)
            missing.append(u)
            continue
        if cur is not None:
            cur["after"] = block[0]
            cur = None
        pending_before = block[1]
    return missing, groups


def find_axis_breaks(span, flat_co, basis_co, fold_a, fold_b, eps=0.0005):
    """Where `span` (a Guide boundary run) crosses a self-symmetry fold axis
    (`fold_a`, `fold_b` — Flat-space points from analysis.detect_island_
    symmetry's `folds`), as 3D-arc-length parameters (0..1) for division_
    parameters' `hard_us`.

    Classifies every span vertex by signed distance to the fold line in FLAT
    space — the same test symmetrize_retopo_island uses. A vertex within
    `eps` (metres) of the line already IS the axis point: a cut-on-fold
    panel normally has a real vertex sitting there, since that is exactly
    what rewards a low reflection RMS in detect_island_symmetry. Its u is
    reported directly, no interpolation, and the segment(s) touching it are
    skipped in the sign-flip search below so the same crossing is not
    counted twice.

    CROSSING, NOT TOUCHING. Consecutive on-axis vertices are one run and get
    at most ONE parameter, and a run only counts when the span actually
    changes side across it: the nearest vertex outside `eps` before the run
    and after it must have OPPOSITE signs. This is the exact definition of a
    transversal crossing, so no tolerance is involved beyond `eps` itself.
    Three cases fall out of it:

      * the axis runs ALONG the span (measured: 125 of 229 span x fold
        combinations on the raw-FBX ladder, every vertex within 1.2 um of the
        line) -> there is no vertex outside eps at all, so nothing is
        reported. Before this rule every vertex of such a span became a
        forced division point, which made every stretch shorter than
        min_seam_mm and collapsed Generate to the Guide's own vertex spacing
        (measured: 20 mm asked, 2.960 mm delivered, 3611 of 3611 retopo
        vertices sitting exactly on a Guide vertex).
      * the span TOUCHES the axis and returns the same way (a dart tip on the
        fold, an outline tangent to it) -> same sign either side, not a
        crossing, nothing reported. A vertex there is not wanted: the two
        sides of the seam do not swap.
      * a run reaching either END of the span -> no outside vertex on that
        side, so nothing reported. The endpoints 0.0 and 1.0 are division
        points already (see division_parameters).

    Only where two consecutive vertices sit on STRICTLY opposite sides, both
    outside `eps`, is the crossing interpolated: the zero of the signed
    distance (linear in FLAT space) gives a fraction t, which is then read
    against the SAME segment's 3D arc length — the same domain-mixing
    span_point_at_u / _project_to_span already rely on (t comes from flat
    space, the position and arc length it is applied to are 3D).

    Returns a list of u values — normally one, for an open run threading
    past the axis once; more for a run that grazes it repeatedly (or an
    island with more than one fold axis, called once per fold and
    concatenated by the caller).
    """
    d = (fold_b - fold_a)
    if d.length < 1e-9 or len(span) < 2:
        return []
    d = d.normalized()
    normal = Vector((-d.y, d.x, 0.0))
    signed = [(flat_co[v] - fold_a).dot(normal) for v in span]
    on_axis = [abs(s) <= eps for s in signed]

    cum = anch._arc_lengths(span, basis_co)
    total = cum[-1]
    if total <= 1e-12:
        return []

    us = []
    n = len(span)
    i = 0
    while i < n:
        if not on_axis[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and on_axis[j + 1]:
            j += 1
        # the run is span[i..j]; does the span change side across it?
        before = signed[i - 1] if i > 0 else None
        after = signed[j + 1] if j + 1 < n else None
        if (before is not None and after is not None
                and before * after < 0.0):
            # one parameter for the whole run, at its arc-length midpoint
            us.append(0.5 * (cum[i] + cum[j]) / total)
        i = j + 1
    for i in range(n - 1):
        if on_axis[i] or on_axis[i + 1]:
            continue  # a neighbouring vertex already claimed this crossing
        if signed[i] * signed[i + 1] < 0.0:
            t = signed[i] / (signed[i] - signed[i + 1])
            s = cum[i] + (cum[i + 1] - cum[i]) * t
            us.append(s / total)
    us.sort()
    return us


def collect_retopo_boundary(retopo):
    """(vert indices on the boundary, set of boundary edges as sorted pairs).

    Counts an edge with at most one face: BMEdge.is_boundary is False for a
    wire edge, and the chains this module lays down on an empty side start
    life as wire. Skipping those made the pass non-idempotent — a second run
    could not see what the first had created and planned it all over again.
    """
    bm = bmesh.new()
    bm.from_mesh(retopo.data)
    bm.edges.ensure_lookup_table()
    verts, edges = set(), set()
    for e in bm.edges:
        if len(e.link_faces) <= 1:
            a, b = e.verts[0].index, e.verts[1].index
            verts.update((a, b))
            edges.add((min(a, b), max(a, b)))
    bm.free()
    return verts, edges


# ---------------------------------------------------------------------------
# Guide island <-> Retopo island correspondence
# ---------------------------------------------------------------------------
#
# Everything above matches individual SEAMS (spans). Mirror Pair detection
# (analysis.detect_mirror_pairs) instead works at the ISLAND level and runs
# on the Guide, because the pattern's correct shape only exists there — the
# Retopo may be mid-work, with some panels not yet built at all. Applying a
# detected pair to the Retopo therefore needs one more step first: which
# Retopo island IS which Guide island.


def find_t_junctions(retopo, tol=0.0005):
    """Boundary vertices lying ON a boundary edge they are not an end of.

    Returns [(vertex index, (edge end a, edge end b)), ...], measured in
    world space. This is the shape every "vertex on an edge but not joined
    to it" defect takes, whichever pass made it, so Seam Status counts and
    selects them instead of each writer being taken on trust. 0.5mm is
    generous — the real ones measure 0.00mm — but a knife cut that just
    missed an edge is worth seeing too.
    """
    from mathutils import kdtree
    bverts, bedges = collect_retopo_boundary(retopo)
    if not bverts or not bedges:
        return []
    mat = retopo.matrix_world
    verts = sorted(bverts)
    co = {i: mat @ retopo.data.vertices[i].co for i in verts}
    kd = kdtree.KDTree(len(verts))
    for k, i in enumerate(verts):
        kd.insert(co[i], k)
    kd.balance()
    hits = []
    for ia, ib in bedges:
        a, b = co[ia], co[ib]
        ab = b - a
        d2 = ab.dot(ab)
        if d2 <= 1e-18:
            continue
        for (_c, k, _d) in kd.find_range((a + b) * 0.5, ab.length * 0.5 + tol):
            i = verts[k]
            if i == ia or i == ib:
                continue
            t = (co[i] - a).dot(ab) / d2
            if not (0.0 < t < 1.0):
                continue
            if (co[i] - (a + ab * t)).length <= tol:
                hits.append((i, (ia, ib)))
    return hits


def _island_centroids(mesh, world_co, islands=None):
    """{island root: mean world-space position}, one entry per connected
    component (analysis._compute_islands' grouping). `world_co` is a list of
    already-world-space Vectors, indexed by vertex — computed once by the
    caller rather than per-vertex here, since an island can hold thousands of
    vertices and a matrix multiply per vertex was measured as the dominant
    cost in a similar hot loop elsewhere in this module (_build_span_kd).

    A plain vertex mean, not the length-weighted boundary centroid detect_
    mirror_pairs uses: that precision matters for scoring outline SHAPE, but
    here the only question is "which blob is this", and two different
    islands' centroids are measured tens of millimetres apart at minimum.

    `islands`, when given, is used as-is instead of recomputing union-find —
    pass analysis.compute_islands_cached(mesh) for a Guide mesh, where that
    cache cut a measured 217ms of this function's 263ms. Do NOT pass a
    cached result for a RETOPO mesh: Phase 2's island replacement deletes N
    vertices/edges/faces and creates N back, so vertex/edge/polygon counts
    — the cache's entire invalidation key — come out UNCHANGED even though
    the actual topology did (measured: caused a second consecutive Replace
    to look up a vertex island that no longer existed). Leave `islands` as
    None for a Retopo mesh; the recompute costs under 1ms there regardless
    (measured: 0.5ms on a 604-vertex production Retopo), so there is no
    speed reason to cache it, only a correctness reason not to.
    """
    if islands is None:
        islands = an._compute_islands(mesh)
    out = {}
    for root, idxs in islands.items():
        s = Vector((0.0, 0.0, 0.0))
        for i in idxs:
            s += world_co[i]
        out[root] = s / len(idxs)
    return out


def _nearest(query_c, items):
    """(root, distance, second-nearest distance) of the item in `items`
    closest to `query_c`. The second distance is what tells a caller whether
    a match is actually unambiguous, as opposed to merely closer than some
    fixed tolerance — see map_retopo_islands_to_guide's margin_ratio.
    """
    best_root, best_d, second_d = None, None, None
    for root, c in items:
        d = (query_c - c).length
        if best_d is None or d < best_d:
            best_root, best_d, second_d = root, d, best_d
        elif second_d is None or d < second_d:
            second_d = d
    return best_root, best_d, second_d


def map_retopo_islands_to_guide(guide, retopo, flat_sk_name,
                                centroid_tol=0.05, margin_ratio=None):
    """Match each Retopo island to the Guide island it was retopologised from.

    Both meshes live in the same flat-pattern space — the Retopo is authored
    directly in the Guide's Flat SK layout (see module docstring) — so a
    plain island centroid already separates them cleanly. Measured on a
    production jacket guide (18 Guide islands large enough to test by
    detect_mirror_pairs, 2 panels not yet retopologised): all 18 Retopo
    islands found their Guide counterpart at 0.0001-0.0275m, inside the
    0.05m default.

    Matching is MUTUAL nearest-neighbour (each other's nearest, in BOTH
    directions), not a one-directional lookup — but mutuality alone does
    NOT protect against every wrong match. It only stops one Retopo island
    from stealing another Retopo island's rightful Guide partner (they would
    be competing for the same nearest neighbour, and only one can win it
    mutually). A Guide island with NO Retopo counterpart at all — the common
    case, an unretopologised panel — competes with nobody, so if it happens
    to sit closer to some Retopo island than that island's true partner
    does, mutuality alone will not catch it. `centroid_tol` (and, if given,
    `margin_ratio`) are the only defences against THAT failure mode, so they
    carry real weight — this is not a "loose safety net on top of a solid
    primary check", both checks matter.

    `margin_ratio`, when given, additionally requires the best match to beat
    the second-best by that factor (best_dist <= margin_ratio * second_dist,
    so e.g. 0.5 means "at least twice as close as anything else"). Off by
    default (None) until real margins have been measured on production data
    — see _dev_tests/test_mirror_pair_retopo_map.py, which reports the
    worst-case margin ratio for exactly this purpose.

    Returns (mapping, unmatched_retopo, unmatched_guide):
      mapping          — dict {retopo_root: (guide_root, dist)}
      unmatched_retopo — [{"root", "reason", "best_guide_root", "best_dist"}]
                          reason is "no_guide_islands", "no_mutual_match",
                          "beyond_tolerance" or "ambiguous_margin"
      unmatched_guide  — [{"root", "best_retopo_root", "best_dist"}] Guide
                          roots with no matched Retopo island (e.g. not yet
                          retopologised) — best_retopo_root/best_dist are
                          None when there is no Retopo island at all
    """
    flat_co = anch.get_flat_co(guide, flat_sk_name)
    retopo_co = [retopo.matrix_world @ v.co for v in retopo.data.vertices]

    g_centroids = _island_centroids(guide.data, flat_co,
                                    islands=an.compute_islands_cached(guide.data))
    r_centroids = _island_centroids(retopo.data, retopo_co)  # never cached — see docstring

    g_items = list(g_centroids.items())
    r_items = list(r_centroids.items())

    r_to_g = {r: _nearest(c, g_items) for r, c in r_items}
    g_to_r = {g: _nearest(c, r_items) for g, c in g_items}

    def _ambiguous(d, second_d):
        return (margin_ratio is not None and second_d is not None
                and d > margin_ratio * second_d)

    mapping = {}
    unmatched_retopo = []
    for r, (g, d, second_d) in r_to_g.items():
        if g is None:
            unmatched_retopo.append({"root": r, "reason": "no_guide_islands"})
            continue
        back_r, _back_d, _back_second = g_to_r[g]
        if back_r != r:
            unmatched_retopo.append({
                "root": r, "reason": "no_mutual_match",
                "best_guide_root": g, "best_dist": d,
            })
            continue
        if d > centroid_tol:
            unmatched_retopo.append({
                "root": r, "reason": "beyond_tolerance",
                "best_guide_root": g, "best_dist": d,
            })
            continue
        if _ambiguous(d, second_d):
            unmatched_retopo.append({
                "root": r, "reason": "ambiguous_margin",
                "best_guide_root": g, "best_dist": d,
            })
            continue
        mapping[r] = (g, d)

    matched_guide_roots = {g for g, _d in mapping.values()}
    unmatched_guide = []
    for g, _c in g_items:
        if g in matched_guide_roots:
            continue
        best_r, best_d, _second = g_to_r[g]
        unmatched_guide.append({
            "root": g, "best_retopo_root": best_r, "best_dist": best_d,
        })

    return mapping, unmatched_retopo, unmatched_guide


def translate_mirror_pairs_to_retopo(pairs, retopo_to_guide):
    """Guide-root mirror pairs (analysis.detect_mirror_pairs) -> Retopo-root
    pairs, via the {retopo_root: (guide_root, dist)} mapping
    map_retopo_islands_to_guide produced.

    A pair is only translated when BOTH sides have a matched Retopo island —
    Phase 2 (island replacement) acts on the Retopo mesh, so a Guide pair
    whose partner panel is not retopologised yet has nothing to receive it.
    Those go to `skipped` with which side(s) are missing, rather than being
    dropped silently.

    `translated["retopo_root_a"]` is guaranteed to be the ONLY Retopo island
    mapped to that Guide root: `retopo_to_guide` only ever holds mutual
    matches, so at most one Retopo root can map to any given Guide root —
    the reverse lookup below is 1:1 by construction, not a pick-the-first-
    of-several.

    Pairs with `same_handed=True` are passed through UNFILTERED, tagged
    `"same_handed"` for the caller to see — detect_mirror_pairs' docstring
    is explicit that these look like a duplicated copy of one panel rather
    than a left/right mirror, so mirroring one onto the other would be
    WRONG. A Phase 2 caller must branch on this flag; it is not safe to
    apply every entry of `translated` uniformly.

    Each translated pair is a SHALLOW copy of the input pair plus two new
    keys — `verts_a`/`verts_b` (lists) and `centroid_a`/`centroid_b`
    (mathutils.Vector) are shared with the original `pairs` entries, not
    copied. Fine for read-only use; a Phase 2 that mutates them in place
    would also be mutating the original detection result.

    Returns (translated, skipped):
      translated — pairs (each the original dict plus "retopo_root_a" /
                   "retopo_root_b") ready for Phase 2, same_handed included
      skipped    — [{"pair": <original pair dict>, "missing": [...]}] where
                   missing is "a", "b", or both — whichever side(s) have no
                   matched Retopo island
    """
    # 1:1, not 1:N — see docstring. A dict comprehension makes that the only
    # possible shape rather than something the reader has to verify.
    guide_to_retopo = {g: r for r, (g, _d) in retopo_to_guide.items()}

    translated, skipped = [], []
    for p in pairs:
        ra = guide_to_retopo.get(p["root_a"])
        rb = guide_to_retopo.get(p["root_b"])
        if ra is None or rb is None:
            missing = [side for side, v in (("a", ra), ("b", rb)) if v is None]
            skipped.append({"pair": p, "missing": missing})
            continue
        translated.append({**p, "retopo_root_a": ra, "retopo_root_b": rb})
    return translated, skipped


# ---------------------------------------------------------------------------
# Phase 2: island replacement
# ---------------------------------------------------------------------------
#
# Everything above only DETECTS and MAPS. This is the one function that
# actually rewrites the Retopo mesh: it replaces one mismatched island's
# topology with a transformed copy of its mirror partner's, so the two
# sides finally have identical vertex counts and can be kept in sync
# (Phase 3+) instead of just flagged as mismatched.
#
# Design settled by measurement, not by guessing (see
# ミラーペア機能設計.md and the Opus design review it records):
#   - the transform is applied in WORLD space (retopo.matrix_world), the
#     same convention detect_mirror_pairs' centroid_a/centroid_b already
#     use — applying it to LOCAL co directly would be silently wrong on
#     any retopo object whose matrix_world isn't identity.
#   - a brand-new vertex's 2D position has to be written to BOTH vert.co
#     AND the "Basis" shape-key layer. When a Basis ShapeKey block exists,
#     everything downstream (clo_projector's extract_points_world, the
#     viewport) reads THAT, not vert.co — apply_seam_sync already does
#     both for exactly this reason.
#   - face vertex-loop order IS REVERSED (corrected 2026-09-01 — the
#     original version of this note argued the opposite and was wrong).
#     A reflection's linear part has determinant -1, so a transformed
#     face's signed 2D area = det(linear part) * original signed area =
#     -1 * original: the copy comes out with the OPPOSITE winding, i.e.
#     its face normal points the opposite way from the source face's.
#     Reversing the loop flips the sign back, so the copy's normal
#     matches the source's (both "up"). The panel that first exposed this
#     (retopo island pair 285/311, the 872-Guide-vertex pair, hand-
#     mirrored by the user in production) is not counter-evidence: its two
#     sides' signed 2D areas came out equal in magnitude and OPPOSITE in
#     sign precisely because that hand-mirror had NOT reversed the loop —
#     i.e. it reproduces the same flipped-normal bug this fix removes, not
#     a correct reference.
#   - the 3D position of every new vertex is deliberately left for the
#     caller to fill in via clo_projector.core.run_forward_projection(...,
#     incremental=True) rather than computed here. A brand-new bmesh
#     vertex's "ac9_status" attachment attribute defaults to STATUS_NONE
#     (0), which is exactly what incremental projection treats as
#     "needs_compute" — so it picks up precisely the vertices this
#     function created, and nothing else. That is the ONLY way to get a
#     correct 3D position here: the two sides of a mirror pair drape
#     differently in 3D even from an identical 2D pattern (the whole
#     reason Phase 1 matches in 2D and not 3D — see analysis.
#     detect_mirror_pairs), so mirroring the SOURCE island's 3D positions
#     would silently assume a symmetric drape that generally isn't true.


def _bisect_island_on_fold_plane(mesh, idx, fold_a, normal, mat, inv, eps):
    """Bisect the given island's geometry on the fold plane (world-space
    plane through fold_a with normal `normal`) BEFORE symmetrize_retopo_
    island classifies its vertices into keep/other/axis.

    This is what closes the KNOWN UNFIXED GAP documented on symmetrize_
    retopo_island: without it, a face straddling the axis with no vertex
    actually ON the axis gets deleted along with the `other` side and
    never gets a replacement (measured, test_symmetrize_straddling_face.py
    -- a single quad crossing the axis with no axis vertex went from 1
    face to 0 after symmetrize). Bisecting first guarantees every face
    that crosses the plane has a vertex splitting it there, so the
    existing keep/other/axis classification (which already handles faces
    that DO have an axis vertex correctly) never sees a straddler.

    `dist=eps` means an existing vertex already within eps of the plane
    (e.g. one find_axis_breaks' hard_us already placed there) is reused
    as-is -- bmesh.ops.bisect_plane does not create a duplicate ON TOP of
    it. But its `geom_cut` return value is NOT "the vertices this call
    created": it also lists every EXISTING vertex it found already within
    `dist` of the plane, unchanged (measured, probe_bisect_existing_axis.py
    -- a mesh with two verts already exactly on the axis and nothing
    crossing it came back with total vert count unchanged, yet geom_cut
    contained those same two pre-existing verts by index and coordinate).
    So "vertex is in geom_cut" does NOT mean "vertex is new" -- telling
    them apart requires comparing against the vertex set from BEFORE this
    call, which is what `existing_verts` below is for. Only a face with NO
    vertex already on the axis produces a genuinely new one at the
    crossing, so on an island Generate already aligned to the fold (the
    common case once P0's hard_us are in place) the true new-vertex count
    is 0 even though geom_cut is non-empty.

    A vertex bisect_plane creates on a crossing edge has every shape-key
    layer correctly interpolated (measured) -- EXCEPT a domain-INT layer
    like "ac9_status", which is also interpolated from a neighbour
    (measured: inherits e.g. status 2 = "attached" instead of defaulting
    to 0 the way a plain bm.verts.new() vertex does). Left alone, that
    would make run_forward_projection(..., incremental=True) believe this
    brand-new vertex already has a valid 3D position and skip it -- so
    every genuinely new vertex from this bisect gets "ac9_status" forced
    back to STATUS_NONE here, matching what the rest of this module
    already relies on for freshly created vertices. An existing vertex
    geom_cut also lists must NOT have its status touched -- it already has
    whatever attachment state it had before this call.

    Returns {"new_verts": int} -- 0 means no face crossed the axis without
    an existing vertex already there (a no-op).
    """
    from ..clo_projector.attachment import STATUS_NONE, ATTR_STATUS

    local_plane_co = inv @ fold_a
    local_plane_no = (inv.to_3x3() @ normal).normalized()

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()

    island_verts = [bm.verts[i] for i in idx]
    island_vert_set = set(island_verts)
    island_edges = {e for v in island_verts for e in v.link_edges
                    if all(ev in island_vert_set for ev in e.verts)}
    island_faces = {f for v in island_verts for f in v.link_faces
                    if all(fv in island_vert_set for fv in f.verts)}
    geom = island_verts + list(island_edges) + list(island_faces)
    existing_verts = set(bm.verts)  # BEFORE bisect -- see docstring above

    res = bmesh.ops.bisect_plane(bm, geom=geom, dist=eps,
                                 plane_co=local_plane_co,
                                 plane_no=local_plane_no,
                                 use_snap_center=False,
                                 clear_outer=False, clear_inner=False)
    cut_verts = [g for g in res["geom_cut"] if isinstance(g, bmesh.types.BMVert)]
    new_verts = [v for v in cut_verts if v not in existing_verts]

    if new_verts:
        status_layer = bm.verts.layers.int.get(ATTR_STATUS)
        if status_layer is not None:
            for v in new_verts:
                v[status_layer] = STATUS_NONE

    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return {"new_verts": len(new_verts)}


def _collect_island_faces_and_wire_edges(mesh, vert_set):
    """Every face fully inside `vert_set`, plus every edge inside it that no
    such face already covers (a boundary/interior chain with no faces yet —
    apply_seam_sync's wire chains are exactly this, and a mid-work panel can
    easily have some).

    Returns (faces, wire_edges):
      faces      — [(vertex_index_tuple, material_index, smooth)], loop
                   order preserved exactly as authored
      wire_edges — [(v0, v1)] not implied by any returned face
    """
    vs = set(vert_set)
    faces = []
    face_edges = set()
    for poly in mesh.polygons:
        loop = tuple(poly.vertices)
        if not all(v in vs for v in loop):
            continue
        faces.append((loop, poly.material_index, poly.use_smooth))
        for i in range(len(loop)):
            a, b = loop[i], loop[(i + 1) % len(loop)]
            face_edges.add((min(a, b), max(a, b)))

    wire_edges = []
    for e in mesh.edges:
        a, b = e.vertices[0], e.vertices[1]
        if a not in vs or b not in vs:
            continue
        key = (min(a, b), max(a, b))
        if key not in face_edges:
            wire_edges.append((a, b))
    return faces, wire_edges


def replace_retopo_island(retopo, retopo_root_src, retopo_root_dst,
                          theta_deg, centroid_src, centroid_dst,
                          size_ratio_gate=(0.4, 2.5)):
    """Replace Retopo island `retopo_root_dst` with a transformed copy of
    island `retopo_root_src`'s topology (vertices, faces, wire edges).

    `theta_deg`, `centroid_src`, `centroid_dst` come straight from a
    translate_mirror_pairs_to_retopo entry — pass centroid_a/centroid_b (and
    plain theta_deg) when src/dst matches a/b, or swap centroid_a and
    centroid_b when copying b onto a. detect_mirror_pairs' reverse direction
    reuses the SAME theta as forward (see analysis.py's comment there); do
    NOT negate or complement it here.

    p_dst = R(theta_deg) @ diag(-1, 1) @ (p_src - centroid_src) + centroid_dst
    applied per vertex in WORLD space.

    `size_ratio_gate` is a cheap sanity check, not a shape comparison: the
    transformed source island's max centroid-radius must land within this
    factor of the EXISTING (about to be deleted) target island's own
    max-radius. Both sides of a real mirror pair retopologise the same
    Guide panel, so wildly different sizes point at a wrong pairing (wrong
    theta/centroids, or roots that no longer mean what the caller thinks —
    see map_retopo_islands_to_guide's docstring on root lifetime) rather
    than at a real difference worth replacing. Raises RuntimeError if the
    gate fails; nothing is modified when it does.

    Returns a stats dict: deleted_verts, deleted_faces, deleted_edges,
    created_verts, created_faces, created_wire_edges, size_ratio.

    BOTH `retopo_root_src` and `retopo_root_dst` are USELESS as island
    identifiers the moment this function returns — do not look either of
    them up again afterward. bmesh vertex deletion compacts every surviving
    vertex's index (new index = old index minus how many deleted vertices
    had a smaller old index), and a union-find root is itself just some
    vertex index inside its own component — so if the SOURCE island's root
    happened to be a vertex whose original index came after any deleted
    target vertex, that root now belongs to a different vertex, or none.
    Measured: root 460 (a real production source island) was ITSELF one of
    the 43 vertices in its own island's single 43-vertex face loop, and
    stopped being a valid analysis._compute_islands key at all once a
    lower-or-interleaved-index target island was deleted — even though the
    source island's actual DATA was verified bit-for-bit unchanged (an
    order-independent position-set comparison matched exactly; only the
    identifier went stale, not the geometry). If a caller needs to find
    either island again afterward, do it by something stable — vertex
    position, or a fresh analysis._compute_islands call matched by size and
    location — never by re-using retopo_root_src/retopo_root_dst.
    """
    from mathutils import Matrix

    mesh = retopo.data
    mat = retopo.matrix_world
    inv = mat.inverted()

    islands = an._compute_islands(mesh)
    src_idx = islands.get(retopo_root_src)
    dst_idx = islands.get(retopo_root_dst)
    if not src_idx:
        raise RuntimeError(f"Retopo island root {retopo_root_src} (source) not found.")
    if not dst_idx:
        raise RuntimeError(f"Retopo island root {retopo_root_dst} (target) not found.")

    src_world = [mat @ mesh.vertices[i].co for i in src_idx]
    dst_world = [mat @ mesh.vertices[i].co for i in dst_idx]

    def _max_radius(pts, centroid):
        return max((p - centroid).length for p in pts) if pts else 0.0

    src_radius_at_dst = 0.0  # filled in after transform, for the gate
    dst_radius = _max_radius(dst_world, centroid_dst)

    rot = Matrix.Rotation(math.radians(theta_deg), 3, 'Z')
    flip = Matrix.Diagonal((-1.0, 1.0, 1.0))
    xform = rot @ flip

    new_world = [xform @ (p - centroid_src) + centroid_dst for p in src_world]
    src_radius_at_dst = _max_radius(new_world, centroid_dst)

    lo, hi = size_ratio_gate
    ratio = (src_radius_at_dst / dst_radius) if dst_radius > 1e-9 else float('inf')
    if dst_radius > 1e-9 and not (lo <= ratio <= hi):
        raise RuntimeError(
            f"Transformed source island's size looks wrong for this target "
            f"(radius ratio {ratio:.3f}, expected within [{lo}, {hi}]). "
            f"Refusing to replace — likely a stale root or wrong pair."
        )

    local_index = {v: k for k, v in enumerate(src_idx)}
    faces, wire_edges = _collect_island_faces_and_wire_edges(mesh, src_idx)

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()

    deleted_verts = [bm.verts[i] for i in dst_idx]
    deleted_faces = len({f for v in deleted_verts for f in v.link_faces})
    deleted_edges = len({e for v in deleted_verts for e in v.link_edges})
    bmesh.ops.delete(bm, geom=deleted_verts, context='VERTS')

    new_local = [inv @ w for w in new_world]
    basis_layer = bm.verts.layers.shape.get("Basis")
    new_verts = []
    for co in new_local:
        v = bm.verts.new(co)
        if basis_layer is not None:
            v[basis_layer] = co
        new_verts.append(v)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()

    created_faces = 0
    for loop, mat_idx, smooth in faces:
        # Reversed — see the "Phase 2: island replacement" module comment
        # above (det(-1) of the reflection flips the copy's winding).
        loop_verts = [new_verts[local_index[v]] for v in reversed(loop)]
        try:
            f = bm.faces.new(loop_verts)
        except ValueError:
            continue  # face already exists (shared edge quirk) — skip
        f.material_index = mat_idx
        f.smooth = smooth
        created_faces += 1

    created_wire = 0
    for a, b in wire_edges:
        va, vb = new_verts[local_index[a]], new_verts[local_index[b]]
        if bm.edges.get((va, vb)) is not None:
            continue
        bm.edges.new((va, vb))
        created_wire += 1

    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    return {
        "deleted_verts": len(dst_idx), "deleted_faces": deleted_faces,
        "deleted_edges": deleted_edges,
        "created_verts": len(new_verts), "created_faces": created_faces,
        "created_wire_edges": created_wire,
        "size_ratio": ratio,
    }


def symmetrize_retopo_island(retopo, retopo_root, fold_a, fold_b, keep_sign,
                             eps=0.0005):
    """Make a single self-symmetric Retopo island (a panel with BOTH halves
    in one connected component, joined along a shared centre seam — a back
    yoke, a waistband) match itself: keep whichever half `keep_sign` picks,
    delete the other half, and rebuild it as a reflection of the kept half.

    Unlike replace_retopo_island (two SEPARATE islands, delete one whole
    island and rebuild it from the other), the axis vertices here are
    SHARED by both halves and must survive untouched — deleting them would
    tear the centre seam apart. So this classifies the island's vertices
    into three groups by signed distance to the fold line (through
    `fold_a`/`fold_b`, NOT world x=0 or y=0 — see detect_island_symmetry's
    docstring: the axis passes through the island's OWN centroid, wherever
    CLO happened to pack that panel):
      kept side   (sign matches `keep_sign`, |signed dist| > eps) — untouched
      other side  (sign is -keep_sign, |signed dist| > eps)       — deleted,
                   rebuilt as a reflection of the kept side
      axis        (|signed dist| <= eps)                          — kept,
                   SNAPPED exactly onto the line first (see below), reused
                   as-is for both the kept side's faces and the rebuilt
                   side's faces — never duplicated

    Axis snapping: a vertex within `eps` of the line is moved to sit
    EXACTLY on it (signed distance forced to 0) before anything else
    happens. Without this, the kept side and the rebuilt side would each
    want a slightly different position for "the same" seam vertex — the
    kept side's real (slightly off-axis) position vs. the reflected side's
    mirror of the OTHER kept vertex — and the seam would split by twice
    that vertex's off-axis distance. Reported in the returned stats
    (`axis_verts`, `max_axis_snap`) since a startlingly large snap distance
    means the panel wasn't as symmetric as detect_island_symmetry's
    tolerance thought.

    Face vertex-loop order IS REVERSED, exactly as replace_retopo_island
    (corrected 2026-09-01 — see that function's module-comment for the
    algebra: a reflection's determinant-(-1) linear part flips a
    transformed face's signed 2D area, i.e. its normal points the wrong
    way, on its own; reversing the loop flips it back so the rebuilt
    other-side face's normal matches the kept side's).

    This function does NOT itself touch 3D projection or the "ac9_status"
    attachment attribute — that's the caller's job. The axis vertices were
    just SNAPPED (moved), which stales their existing attachment without
    flipping their status to "none" on its own, so a caller using
    run_forward_projection(..., incremental=True) MUST first write
    STATUS_NONE (0) into "ac9_status" at the returned axis_vert_indices, or
    incremental projection will silently leave those vertices at their
    pre-snap 3D position. (See AC9_OT_symmetrize_island.execute for the
    reference caller.) A caller that skips that write must instead pass
    incremental=False for a full recompute (clo_projector.mirror.py notes
    the Guide-side triangle/BVH cache makes a full pass cheap once warm,
    which is what makes that fallback acceptable, if slower).

    FIXED (2026-09-02, was a KNOWN UNFIXED GAP as of 2026-09-01): a face
    straddling the axis with no vertex actually ON it used to be deleted
    along with `other_idx` and never get a replacement (measured, see
    test_symmetrize_straddling_face.py — a single quad crossing the axis
    with no axis vertex went from 1 face to 0). This is now closed by
    _bisect_island_on_fold_plane, called below BEFORE classification: it
    bisects the island's geometry on the fold plane first, so any face
    that crosses it gets a real vertex splitting it there (reusing an
    existing hard_u vertex already on the axis instead of duplicating it —
    see that function's docstring). Every face this classification sees
    is therefore already on one side, the other, or has a vertex exactly
    on the axis — never straddling.

    Returns a stats dict: kept_verts, deleted_verts, deleted_faces,
    deleted_edges, created_verts, created_faces, created_wire_edges,
    axis_verts, max_axis_snap, axis_vert_indices, new_vert_indices,
    bisected_verts. The last two vertex-index lists are POST-delete
    indices (valid immediately, before anything else touches the mesh)
    for a caller that wants to flag exactly the vertices this call
    moved/created as "needs projection" and run run_forward_projection(
    ..., incremental=True) instead of a full pass — new_vert_indices
    don't strictly need it (a fresh bmesh vertex's ac9_status attribute
    already defaults to STATUS_NONE) but axis_vert_indices do, since those
    vertices keep their pre-snap attachment status. `bisected_verts` (an
    int, not an index list) reports how many NEW vertices the fold-plane
    bisect above created — 0 is the common case once Generate's hard_us
    already cover the axis, but it is worth surfacing when it isn't.

    Like replace_retopo_island, `retopo_root` is USELESS the moment this
    returns — bmesh deletion recompacts indices, and the root may not even
    refer to a surviving vertex. Don't look it up again.
    """
    mesh = retopo.data
    mat = retopo.matrix_world
    inv = mat.inverted()

    islands = an._compute_islands(mesh)
    idx = islands.get(retopo_root)
    if not idx:
        raise RuntimeError(f"Retopo island root {retopo_root} not found.")

    d = (fold_b - fold_a)
    if d.length < 1e-9:
        raise RuntimeError("Fold axis has zero length (fold_a == fold_b).")
    d = d.normalized()
    normal = Vector((-d.y, d.x, 0.0))

    bisect_stats = _bisect_island_on_fold_plane(mesh, idx, fold_a, normal,
                                                mat, inv, eps)
    if bisect_stats["new_verts"]:
        # The island's vertex set grew (a face crossed the axis with no
        # vertex already there) -- re-resolve it. retopo_root itself is
        # still a valid, unmoved vertex index (bisect_plane never deletes
        # an existing vertex, only appends new ones), so this must find it.
        islands = an._compute_islands(mesh)
        idx = islands.get(retopo_root)
        if not idx:
            raise RuntimeError(
                f"Retopo island root {retopo_root} vanished after bisecting "
                f"the fold plane — this should not happen (bisect_plane "
                f"never deletes an existing vertex).")

    def _signed(p):
        return (p - fold_a).dot(normal)

    world = {i: mat @ mesh.vertices[i].co for i in idx}
    signed = {i: _signed(p) for i, p in world.items()}

    keep_idx = [i for i in idx if signed[i] * keep_sign > eps]
    other_idx = [i for i in idx if signed[i] * keep_sign < -eps]
    axis_idx = [i for i in idx if abs(signed[i]) <= eps]
    if not keep_idx:
        raise RuntimeError("Nothing on the kept side of the fold axis — "
                          "wrong keep_sign, or the axis misses this island.")
    if not other_idx:
        raise RuntimeError("Nothing on the other side of the fold axis — "
                          "island already looks one-sided, nothing to rebuild.")

    # Snap axis vertices exactly onto the line before anything else reads
    # their position (faces below are collected from `mesh`, still holding
    # the snapped value once written).
    # Retopo's 2D position is read via the "Basis" ShapeKey when one exists
    # (clo_projector.guide.extract_points_world prefers it over vert.co) —
    # write both, same lesson replace_retopo_island's review caught: a
    # vert.co-only write is invisible to anything downstream that evaluates
    # through the shape key.
    basis_kb = mesh.shape_keys.key_blocks.get("Basis") if mesh.shape_keys else None

    max_snap = 0.0
    for i in axis_idx:
        p = world[i]
        s = signed[i]
        snapped = p - normal * s
        max_snap = max(max_snap, abs(s))
        local = inv @ snapped
        mesh.vertices[i].co = local
        if basis_kb is not None:
            basis_kb.data[i].co = local
        world[i] = snapped
    mesh.update()

    # The kept side's faces/wire edges span keep_idx AND axis_idx (a face
    # straddling the seam has some loop vertices on each).
    kept_set = set(keep_idx) | set(axis_idx)
    faces, wire_edges = _collect_island_faces_and_wire_edges(mesh, kept_set)

    new_world = {i: world[i] - normal * (2.0 * signed[i]) for i in keep_idx}

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()

    deleted_verts = [bm.verts[i] for i in other_idx]
    deleted_faces = len({f for v in deleted_verts for f in v.link_faces})
    deleted_edges = len({e for v in deleted_verts for e in v.link_edges})
    axis_bmverts = {i: bm.verts[i] for i in axis_idx}  # captured BEFORE delete
    bmesh.ops.delete(bm, geom=deleted_verts, context='VERTS')
    if any(not v.is_valid for v in axis_bmverts.values()):
        raise RuntimeError("An axis vertex was invalidated by deleting the "
                          "other side — this should not happen (axis "
                          "vertices are excluded from the delete list); "
                          "aborting before writing anything.")

    basis_layer = bm.verts.layers.shape.get("Basis")
    new_bmverts = {}  # original index -> new BMVert, for keep_idx only
    for i in keep_idx:
        co = inv @ new_world[i]
        v = bm.verts.new(co)
        if basis_layer is not None:
            v[basis_layer] = co
        new_bmverts[i] = v
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()

    def _rebuilt_vert(orig_i):
        return new_bmverts[orig_i] if orig_i in new_bmverts else axis_bmverts[orig_i]

    created_faces = 0
    for loop, mat_idx, smooth in faces:
        # Reversed — see the docstring above (det(-1) of the reflection
        # flips the rebuilt face's winding relative to the kept side's).
        loop_verts = [_rebuilt_vert(v) for v in reversed(loop)]
        try:
            f = bm.faces.new(loop_verts)
        except ValueError:
            continue  # face already exists (shared edge quirk) — skip
        f.material_index = mat_idx
        f.smooth = smooth
        created_faces += 1

    created_wire = 0
    for a, b in wire_edges:
        va, vb = _rebuilt_vert(a), _rebuilt_vert(b)
        if bm.edges.get((va, vb)) is not None:
            continue
        bm.edges.new((va, vb))
        created_wire += 1

    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    # Post-delete indices, for the caller to flag as "needs projection" —
    # see symmetrize_retopo_island's docstring on why axis vertices (moved
    # by the snap above) need this even though they weren't deleted.
    axis_vert_indices = [v.index for v in axis_bmverts.values()]
    new_vert_indices = [v.index for v in new_bmverts.values()]
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    return {
        "kept_verts": len(keep_idx), "deleted_verts": len(other_idx),
        "deleted_faces": deleted_faces, "deleted_edges": deleted_edges,
        "created_verts": len(new_bmverts), "created_faces": created_faces,
        "created_wire_edges": created_wire,
        "axis_verts": len(axis_idx), "max_axis_snap": max_snap,
        "axis_vert_indices": axis_vert_indices,
        "new_vert_indices": new_vert_indices,
        "bisected_verts": bisect_stats["new_verts"],
    }


def _build_span_kd(spans, flat_co, tol):
    """KDTree over every span vertex in flat space, plus the radius to query.

    Returns (kd, kd_span, search_r) where kd_span[i] is the span index of the
    i-th inserted point.

    The radius is what makes this an exact accelerator rather than a heuristic.
    If a point lies within `tol` of some segment AB, its distance to the nearer
    of A and B is at most sqrt(tol^2 + (|AB|/2)^2) <= tol + |AB|/2. Querying
    with tol + (longest segment)/2 therefore cannot miss a span that the brute
    force pass would have accepted, so both produce the same assignment — and
    the caller's "nearest span, then reject beyond tol" logic is unaffected: a
    span outside the candidate set is farther than tol by construction, so it
    could never have won and survived the threshold.
    """
    from mathutils import kdtree

    size = sum(len(s) for s in spans)
    kd = kdtree.KDTree(size)
    kd_span = []
    max_seg = 0.0
    n = 0
    for si, s in enumerate(spans):
        prev = None
        for v in s:
            co = flat_co[v]
            kd.insert(co, n)
            kd_span.append(si)
            n += 1
            if prev is not None:
                seg = (co - prev).length
                if seg > max_seg:
                    max_seg = seg
            prev = co
    kd.balance()
    return kd, kd_span, tol + max_seg * 0.5 + 1e-9


def map_retopo_to_spans_bruteforce(guide, retopo, spans, basis_co, flat_co,
                                   tol=0.005):
    """The pre-KDTree assignment, kept as the reference the fast path is
    checked against. Not used at runtime — see test_span_kd_equivalence."""
    bverts, _bedges = collect_retopo_boundary(retopo)
    mat = retopo.matrix_world
    mesh = retopo.data

    spans_at_anchor = {}
    for si, s in enumerate(spans):
        spans_at_anchor.setdefault(s[0], []).append(si)
        spans_at_anchor.setdefault(s[-1], []).append(si)

    assign = {}
    for i in bverts:
        p = mat @ mesh.vertices[i].co
        best = None
        for si, s in enumerate(spans):
            d, u = _project_to_span(p, s, basis_co, flat_co)
            if best is None or d < best[0]:
                best = (d, u, si)
        if best is None or best[0] > tol:
            continue
        d, u, si = best
        targets = {si}
        for anchor in (spans[si][0], spans[si][-1]):
            if (p - flat_co[anchor]).length <= tol:
                targets.update(spans_at_anchor.get(anchor, ()))
        for tsi in targets:
            td, tu = _project_to_span(p, spans[tsi], basis_co, flat_co)
            if td <= tol:
                assign.setdefault(tsi, []).append((tu, i, td))
    for v in assign.values():
        v.sort()
    return assign


def map_retopo_to_spans(guide, retopo, spans, basis_co, flat_co, tol=0.005):
    """Assign each retopo boundary vertex to the Guide spans it lies along.

    A vertex goes to its nearest span, and — when it sits on one of that
    span's anchors — to every other span meeting at that anchor as well. A
    vertex on an anchor genuinely belongs to all of them, and filing it under
    one made the two sides of a seam look unequal: measured on a production
    retopo as "14 vs 13" where both sides in fact held 15 vertices at
    identical parameters.

    The extra spans are found through the anchor they SHARE, not by distance.
    A distance rule cannot separate the two cases it has to: an anchor vertex
    the user snapped loosely (measured up to 4.4mm off the outline) sits at
    noticeably different distances from the two spans it belongs to, while on
    a narrow panel (placket, binding, strap) the edge across the panel can be
    nearer than that yet have nothing to do with this vertex.

    Returns dict[span_index] -> list of (u, vert_index, distance), sorted by
    u. Vertices further than `tol` from every span are left out — they are
    cuts through the middle of a panel, not seam boundary.
    """
    bverts, _bedges = collect_retopo_boundary(retopo)
    mat = retopo.matrix_world
    mesh = retopo.data

    # Guide anchor vertex -> the spans that end there.
    spans_at_anchor = {}
    for si, s in enumerate(spans):
        spans_at_anchor.setdefault(s[0], []).append(si)
        spans_at_anchor.setdefault(s[-1], []).append(si)

    kd, kd_span, search_r = _build_span_kd(spans, flat_co, tol)

    assign = {}
    for i in bverts:
        p = mat @ mesh.vertices[i].co
        # Only spans with a vertex near p can possibly be within tol of p —
        # see _build_span_kd for why search_r makes that exact rather than
        # approximate. Projecting against every span instead was the single
        # slowest thing in this module (measured: 1.67s of a 1.96s pass).
        cand = {kd_span[idx] for (_co, idx, _d) in kd.find_range(p, search_r)}
        # Walk the candidates in span-index order. Ties are not rare here — a
        # vertex sitting exactly on a shared anchor is at distance 0 from every
        # span meeting there — and `<` keeps the FIRST one seen, so iterating a
        # set (hash order) would pick a different winner than the index-ordered
        # brute-force pass and quietly change the assignment on 146 of 457
        # spans (measured).
        best = None
        for si in sorted(cand):
            d, u = _project_to_span(p, spans[si], basis_co, flat_co)
            if best is None or d < best[0]:
                best = (d, u, si)
        if best is None or best[0] > tol:
            continue
        d, u, si = best
        targets = {si}
        for anchor in (spans[si][0], spans[si][-1]):
            if (p - flat_co[anchor]).length <= tol:
                targets.update(spans_at_anchor.get(anchor, ()))
        for tsi in targets:
            td, tu = _project_to_span(p, spans[tsi], basis_co, flat_co)
            if td <= tol:
                assign.setdefault(tsi, []).append((tu, i, td))
    for v in assign.values():
        v.sort()
    return assign


def build_span_index(spans):
    """Look up a span's index from its vertex tuple, either orientation.

    pair_spans hands side "b" back reversed when that is the matching
    direction, so identity lookup does not work.
    """
    idx = {}
    for si, s in enumerate(spans):
        idx[tuple(s)] = si
        idx[tuple(reversed(s))] = si
    return idx


def plan_seam_sync(pairs, spans, assign, span_index, basis_co, flat_co,
                   occupied=None, match_mm=1.0, min_chain=2,
                   source_spans=None, bedge_set=None):
    """Work out which vertices each side is missing. Reads only.

    `bedge_set` (the retopo's boundary edges, from collect_retopo_boundary)
    turns on the coverage test: a parameter inside a stretch that an existing
    edge between two of the target side's vertices already spans is not
    missing — somebody decided that stretch is done. Generate passes it
    (Generate never writes onto an edge, on any path); Match leaves it None,
    because splitting such an edge to bring the partner's vertices across is
    exactly Match's job.

    ADD-ONLY, and strictly so: a side is filled from its partner only when
    every interior vertex it already holds matches one of the partner's
    (within match_mm along the seam). Anything else — the two sides
    interleaved, one vertex slid along the outline, the "source" side the
    sparser one — is left alone and reported with a reason, because adding
    there makes it worse: measured on a production file, a 15-against-15
    seam with one vertex 21mm off would have become 16 against 15.

    `source_spans` (a set of span tuples) names the side to copy FROM. A
    pair with no span in it is not this run's business; a pair with exactly
    one is copied from that side whatever it holds; a pair with both falls
    back to the automatic rule (copy from the authored / fuller side).

    Presence is decided by PARAMETER along the seam, comparing the source
    side's parameters against the target side's. Both spans of a pair are
    within length_tol of each other in 3D, so the parameters are directly
    comparable.

    Comparing outline POSITIONS instead does not work: a retopo vertex the
    user has not snapped tightly still has a well-defined parameter, but sits
    off the outline — measured on a production retopo, up to 4.4mm. Mirroring
    places its counterpart exactly on the outline, so a position test then
    fails to recognise the original and re-plans it forever (measured: 9
    vertices came back on a second run).

    The anchor ambiguity that motivated a position test is instead handled by
    map_retopo_to_spans listing a vertex under every span it touches.

    A side counts as authored along this span only if it holds at least
    `min_chain` vertices AND at least one of them lies strictly between the
    anchors. Anchor vertices belong to every span meeting at that point, so a
    side whose only vertices are the two anchors says nothing about how THIS
    span is divided — mirroring it would draw a bare chord across the span.

    Returns (jobs, skipped). Each job is one side that needs work:
      {"span": [...], "target_side": "a"|"b", "existing": [(u, vidx)],
       "missing": [u, ...], "source_count": int}
    `skipped` lists seams left alone because their two sides are interleaved
    rather than one containing the other — see below.
    """
    jobs = []
    skipped = []
    for pr in pairs:
        ia = span_index[tuple(pr["a"])]
        ib = span_index[tuple(pr["b"])]
        edge_eps = 1e-3

        def _authored(entries):
            if len(entries) < min_chain:
                return False
            return any(edge_eps < u < 1.0 - edge_eps for u, _i, _d in entries)

        # Two different questions, and conflating them was a bug: whether a
        # side is authored enough to MIRROR FROM, and what vertices it
        # already HAS. A side holding one stray vertex is not a chain worth
        # copying, but that vertex is still there — discarding it made the
        # pass lay a fresh chain straight over the top of it, so every run
        # added the same vertices again (measured: 348 -> 366 -> 384).
        ea_all = list(assign.get(ia, []))
        eb_all = list(assign.get(ib, []))
        ea = ea_all if _authored(ea_all) else []
        eb = eb_all if _authored(eb_all) else []

        def _interior(entries):
            return sum(1 for u, _i, _d in entries
                       if edge_eps < u < 1.0 - edge_eps)

        def _skip(reason, stray=0):
            return {"count_a": len(ea_all), "count_b": len(eb_all),
                    "reason": reason, "stray": stray}

        # Which side is the SOURCE. Normally whichever is authored (both
        # authored: whichever holds what the other lacks). A caller naming
        # source spans — Match Seams run from a selection — overrides that
        # for the pairs it names.
        forced = None
        if source_spans is not None:
            sa = tuple(pr["a"]) in source_spans
            sb = tuple(pr["b"]) in source_spans
            if not sa and not sb:
                continue
            if sa != sb:
                forced = "a" if sa else "b"

        if not ea and not eb:
            if forced is not None:
                skipped.append(_skip("source_not_authored"))
            continue

        # Work out both directions before committing to either. When each
        # side has vertices the other lacks, their samplings are INTERLEAVED
        # rather than one being a superset of the other, and mirroring would
        # give both sides the union — roughly doubling the count. Measured on
        # a production file: a seam with 16 vertices per side drifting up to
        # 19.9mm along the seam would have become 32 against 32. Those seams
        # need their vertices moved, not more of them, so they are left alone
        # and reported.
        directions = []
        for src_side, src, dst, dst_span, dst_side in (
            # Mirror FROM an authored side, but compare against everything the
            # target actually holds.
            ("a", ea, eb_all, pr["b"], "b"),
            ("b", eb, ea_all, pr["a"], "a"),
        ):
            if forced is not None and src_side != forced:
                continue
            if not src:
                if forced is not None:
                    # The user named the sparse side as the truth: the
                    # partner would have to LOSE vertices, which this pass
                    # does not do.
                    skipped.append(_skip("needs_removal" if _interior(dst)
                                         else "source_not_authored"))
                continue
            total = anch._arc_lengths(dst_span, basis_co)[-1]
            if total <= 1e-12:
                continue
            u_tol = (match_mm / 1000.0) / total
            dst_us = [u for u, _i, _d in dst]
            # The position test is not optional. Where two spans overlap in
            # the flat layout — a lapel notch, where the outline doubles back
            # within a few vertices — a vertex this pass created gets filed
            # under the neighbouring span, so its parameter never shows up
            # here and every run re-created it (measured: 522 -> 537 -> 552,
            # the same 15 vertices each time). Checking the position too
            # catches it whatever span it was filed under, while the
            # parameter test still catches a user-placed vertex sitting off
            # the outline.
            #
            # _divide_missing also reports WHICH vertex blocked each
            # parameter, so the chain this becomes joins onto the vertex at
            # the span's end instead of stopping one spacing short of it.
            covered = ()
            if bedge_set:
                covered = covered_intervals(
                    sorted((u, i) for u, i, _d in dst), bedge_set)
            missing, groups = _divide_missing(
                [u for u, _i, _d in src],
                [(u, i) for u, i, _d in dst], u_tol, covered, occupied,
                dst_span, basis_co, flat_co)
            # Strict containment: every interior vertex the target already
            # holds must sit on one of the source's parameters. One that does
            # not is a vertex the source lacks, and adding the source's on top
            # of it leaves the seam mismatched by one more. Only measured when
            # the target is authored — a lone stray vertex on an unauthored
            # side is folded in by _divide_missing's blocker logic as before.
            stray = 0
            if (eb if dst_side == "b" else ea):
                src_us = [u for u, _i, _d in src]
                for u in dst_us:
                    if not (edge_eps < u < 1.0 - edge_eps):
                        continue
                    if min(abs(u - s) for s in src_us) > u_tol:
                        stray += 1
            directions.append((src, dst, dst_span, dst_side, missing, groups,
                               stray))

        acting = [d for d in directions if d[4]]
        if forced is None and ea and eb and len(acting) > 1:
            skipped.append(_skip("misaligned"))
            continue
        bad = [d for d in acting if d[6]]
        if bad:
            src, dst, _sp, _side, _missing, _g, stray = bad[0]
            skipped.append(_skip(
                "needs_removal" if _interior(dst) > _interior(src)
                else "misaligned", stray))
            continue
        if (forced is not None and not acting and directions
                and directions[0][6]):
            # Nothing to add, yet the target holds vertices the source does
            # not: matching it means removing them.
            skipped.append(_skip("needs_removal", directions[0][6]))
            continue

        for src, dst, dst_span, dst_side, missing, groups, _stray in directions:
            if not missing:
                continue
            jobs.append({
                "span": dst_span,
                "target_side": dst_side,
                "existing": [(u, i) for u, i, _d in dst],
                "missing": sorted(missing),
                "groups": groups,
                "source_count": len(src),
                # How well the side being copied FROM is snapped to the guide
                # outline. A loosely placed source produces a loosely placed
                # mirror, so it is worth surfacing rather than hiding.
                "source_max_offset": max(d for _u, _i, d in src),
            })
    return jobs, skipped


def _segment_blocked(mat, a_local, b_local, occupied, tol=0.0002):
    """Does another retopo boundary vertex sit ON the segment a..b?

    The bridge below joins two chain vertices that no edge connects. They are
    consecutive in the JOB's list, which is not the same as consecutive on the
    outline: a vertex some other span was credited with can sit between them,
    and the bridge would then run straight over it — the very thing the whole
    pass is trying not to do. Measured on a production file: one bridge, two
    new T-junctions.
    """
    if occupied is None:
        return False
    kd, _index = occupied
    aw, bw = mat @ a_local, mat @ b_local
    ab = bw - aw
    d2 = ab.dot(ab)
    if d2 <= 1e-18:
        return False
    for (co, _k, _d) in kd.find_range((aw + bw) * 0.5, ab.length * 0.5 + tol):
        if (co - aw).length <= tol or (co - bw).length <= tol:
            continue
        t = (co - aw).dot(ab) / d2
        if 0.0 < t < 1.0 and (co - (aw + ab * t)).length <= tol:
            return True
    return False


def apply_seam_sync(guide, retopo, jobs, basis_co, flat_co,
                    occupied=None):
    """Create the missing vertices. One bmesh, committed once at the end."""
    mesh = retopo.data
    mat = retopo.matrix_world
    inv = mat.inverted()

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    # The projector's 3D key; looked up by the name core.py owns so a rename
    # there cannot silently strand this.
    from ..clo_projector.core import SHAPEKEY_NAME
    shape_layers = bm.verts.layers.shape
    basis_layer = shape_layers.get("Basis")
    proj_layer = shape_layers.get(SHAPEKEY_NAME)

    stats = {"created": 0, "extended": 0, "inserted_mid": 0, "bridged": 0,
             "bridge_blocked": 0, "new_chains": 0, "ngons_created": 0,
             "welded": 0, "split_onto": 0, "closed_to_anchor": 0}
    created_verts = []
    # (chain-end vertex, span, 0.0 or 1.0): chains that stopped short of
    # their span's end with nothing to join onto — looked at again once every
    # job has run, see the end of this function.
    loose = []

    def _place(vert, u, span):
        flat_w, basis_w = span_point_at_u(span, u, basis_co, flat_co)
        local_flat = inv @ flat_w
        vert.co = local_flat
        if basis_layer is not None:
            vert[basis_layer] = local_flat
        if proj_layer is not None:
            vert[proj_layer] = inv @ basis_w

    def _boundary_edge_under(flat_w, tol=0.0005):
        """The existing boundary edge this outline position lies ON, strictly
        between its ends: (edge, fraction from edge.verts[0]) or None.

        Every vertex this pass creates goes through here first. A position an
        edge already covers is not a place for a free vertex — that is the
        T-junction: a vertex 0.00mm on an edge it is not joined to. Measured
        on a production file, this pass made 3 to 6 of them per run, every one
        at u=1.000 where the target side's own outline crossed the anchor with
        a single 12mm edge and the "extend the chain" branch hung the new
        vertex off the chain end instead of splitting that edge. Splitting
        keeps the outline one path whatever branch asked for the vertex.

        A linear scan: ~1500 edges times ~50 placements per run is nothing,
        and unlike a tree built up front it sees the edges that earlier
        splits in this same pass just made.
        """
        best = None
        for e in bm.edges:
            if len(e.link_faces) > 1:
                continue
            a = mat @ e.verts[0].co
            b = mat @ e.verts[1].co
            ab = b - a
            d2 = ab.dot(ab)
            if d2 <= 1e-18:
                continue
            t = (flat_w - a).dot(ab) / d2
            if not (0.0 < t < 1.0):
                continue
            if (flat_w - a).length <= tol or (flat_w - b).length <= tol:
                continue
            d = (flat_w - (a + ab * t)).length
            if d <= tol and (best is None or d < best[0]):
                best = (d, e, t)
        return None if best is None else (best[1], best[2])

    def _new_vert(u, span):
        """The one way this pass makes a boundary vertex: split the edge
        already under the position if there is one, else a fresh vertex.
        Either way the vertex ends up ON the outline at parameter u."""
        flat_w, _basis_w = span_point_at_u(span, u, basis_co, flat_co)
        hit = _boundary_edge_under(flat_w)
        if hit is not None:
            edge, t = hit
            _new_edge, v = bmesh.utils.edge_split(edge, edge.verts[0], t)
            # A split interpolates INT layers from the edge's ends, so a pin
            # or corner flag next door would come back set on the new vertex.
            from . import density_pin
            density_pin.clear_new_vertex(bm, v)
            stats["split_onto"] += 1
        else:
            v = bm.verts.new((0.0, 0.0, 0.0))
        _place(v, u, span)
        return v

    for job in jobs:
        span = job["span"]
        existing = sorted(job["existing"])
        chain = [bm.verts[i] for _u, i in existing]
        us = [u for u, _i in existing]

        # A GROUP is a stretch of consecutive wanted parameters with nothing
        # dropped between them, plus the retopo vertex that blocked the
        # parameter on either side of it (see _divide_missing). Edges run
        # WITHIN a group and never across the break between two — a break is
        # a parameter dropped for something already sitting there, and
        # bridging it draws the new chain over that vertex and leaves it on
        # an edge it is not joined to — but the group's two ends DO join onto
        # the blocking vertices, which is what closes the outline at a span's
        # anchors. A caller that sends no groups has no drops in its missing
        # list by construction, so it is one group joined to nothing.
        groups = job.get("groups")
        if groups is None:
            groups = ([{"us": job["missing"], "before": None, "after": None}]
                      if job["missing"] else [])

        def _join(a, b):
            if a is None or b is None or a is b:
                return
            try:
                bm.edges.new((a, b))
            except ValueError:
                pass

        def _blocking_vert(idx):
            if idx is None:
                return None
            bm.verts.ensure_lookup_table()
            v = bm.verts[idx]
            return v if v.is_valid else None

        if not chain:
            # Nothing on this side yet: lay down the whole chain.
            for group in groups:
                prev = None
                first = None
                for u in group["us"]:
                    v = _new_vert(u, span)
                    created_verts.append(v)
                    stats["created"] += 1
                    if prev is not None:
                        _join(prev, v)
                    else:
                        first = v
                    prev = v
                bm.verts.ensure_lookup_table()
                _join(_blocking_vert(group.get("before")), first)
                _join(prev, _blocking_vert(group.get("after")))
                if group["us"]:
                    if group.get("before") is None and group["us"][0] > 1e-3:
                        loose.append((first, span, 0.0))
                    if group.get("after") is None and group["us"][-1] < 1.0 - 1e-3:
                        loose.append((prev, span, 1.0))
            stats["new_chains"] += 1
            bm.verts.ensure_lookup_table()
            continue

        for group in groups:
            # The far side of a gap this group is filling; joined up once the
            # group is finished — see the `edge is None` branch below.
            pending_close = None
            first_new = last_new = None
            for u in group["us"]:
                if u < us[0]:
                    v = _new_vert(u, span)
                    created_verts.append(v)
                    if first_new is None:
                        first_new = v
                    last_new = v
                    try:
                        bm.edges.new((v, chain[0]))
                    except ValueError:
                        pass
                    chain.insert(0, v)
                    us.insert(0, u)
                    stats["created"] += 1
                    stats["extended"] += 1
                elif u > us[-1]:
                    v = _new_vert(u, span)
                    created_verts.append(v)
                    if first_new is None:
                        first_new = v
                    last_new = v
                    try:
                        bm.edges.new((chain[-1], v))
                    except ValueError:
                        pass
                    chain.append(v)
                    us.append(u)
                    stats["created"] += 1
                    stats["extended"] += 1
                elif len(chain) < 2:
                    # A lone existing vertex cannot bracket anything; attach
                    # to it rather than indexing past the end of the chain.
                    v = _new_vert(u, span)
                    created_verts.append(v)
                    if first_new is None:
                        first_new = v
                    last_new = v
                    try:
                        bm.edges.new((chain[0], v))
                    except ValueError:
                        pass
                    chain.append(v)
                    us.append(u)
                    stats["created"] += 1
                    stats["extended"] += 1
                else:
                    k = 0
                    for j in range(len(us) - 1):
                        if us[j] <= u <= us[j + 1]:
                            k = j
                            break
                    va, vb = chain[k], chain[k + 1]
                    edge = None
                    for e in va.link_edges:
                        if e.other_vert(va) is vb:
                            edge = e
                            break
                    if edge is None:
                        # The two bracketing vertices are not joined: this
                        # stretch of the outline was deleted (or never built).
                        # Rebuild it rather than skipping — "delete the part
                        # that came out wrong and press Generate again" is the
                        # only way to redo a stretch, and skipping here left
                        # the hole exactly as it was. The new vertex joins the
                        # near side now and the far side when the group ends,
                        # so the run comes back as one path — unless something
                        # else is already sitting between the two, in which
                        # case bridging would run over it.
                        if _segment_blocked(mat, va.co, vb.co, occupied):
                            stats["bridge_blocked"] += 1
                            continue
                        v = _new_vert(u, span)
                        created_verts.append(v)
                        if first_new is None:
                            first_new = v
                        last_new = v
                        _join(va, v)
                        chain.insert(k + 1, v)
                        us.insert(k + 1, u)
                        stats["created"] += 1
                        stats["bridged"] += 1
                        pending_close = (v, vb)
                        bm.verts.ensure_lookup_table()
                        continue
                    faces_before = {f: len(f.verts) for f in edge.link_faces}
                    _new_edge, v = bmesh.utils.edge_split(edge, va, 0.5)
                    _place(v, u, span)
                    if first_new is None:
                        first_new = v
                    last_new = v
                    for f, n in faces_before.items():
                        if f.is_valid and len(f.verts) > n and len(f.verts) > 4:
                            stats["ngons_created"] += 1
                    chain.insert(k + 1, v)
                    us.insert(k + 1, u)
                    stats["created"] += 1
                    stats["inserted_mid"] += 1
                bm.verts.ensure_lookup_table()
            if pending_close is not None:
                _join(*pending_close)
            # Join the group onto whatever blocked the parameter either side
            # of it. Where the group was spliced into an existing chain the
            # edge is already there and this is a no-op; where the chain
            # stopped one spacing short of a span's anchor, this is what
            # reaches it.
            _join(_blocking_vert(group.get("before")), first_new)
            _join(last_new, _blocking_vert(group.get("after")))
            # Only a group that became the chain's new END can be short of
            # the anchor; one spliced into the middle has chain vertices
            # either side of it already.
            if (first_new is not None and chain and chain[0] is first_new
                    and group.get("before") is None and us[0] > 1e-3):
                loose.append((first_new, span, 0.0))
            if (last_new is not None and chain and chain[-1] is last_new
                    and group.get("after") is None and us[-1] < 1.0 - 1e-3):
                loose.append((last_new, span, 1.0))

    # A chain that stops short of its span's end with no blocker was planned
    # against the mesh as it stood BEFORE this run. The anchor vertex the
    # NEIGHBOURING span's job creates in the same run was not there to be
    # found, so the group carries before/after None and the outline is left
    # open by exactly one edge. Measured on a production file: a dart whose
    # partner side had its tip dissolved into a chord — the inherited chain
    # starts at u=0.38, the anchor vertex at u=0 comes from the next span's
    # job, and nothing joined the two (2 open ends on a 33-vertex island).
    # Every job has run now, so look again for a vertex at the span's end.
    stats["loose_ends"] = len(loose)
    stats["loose_no_vertex"] = 0
    stats["loose_blocked"] = 0
    if loose:
        from mathutils import kdtree
        border_now = [v for v in bm.verts if v.is_valid
                      and any(len(e.link_faces) <= 1 for e in v.link_edges)]
        if border_now:
            kd = kdtree.KDTree(len(border_now))
            for k, v in enumerate(border_now):
                kd.insert(mat @ v.co, k)
            kd.balance()
            occ_now = (kd, border_now)
            for v, span, u_end in loose:
                if not v.is_valid:
                    continue
                flat_w, _basis_w = span_point_at_u(span, u_end, basis_co, flat_co)
                hits = [h for h in kd.find_range(flat_w, 0.0002)
                        if border_now[h[1]] is not v and border_now[h[1]].is_valid]
                if not hits:
                    stats["loose_no_vertex"] += 1
                    continue
                anchor = border_now[min(hits, key=lambda h: h[2])[1]]
                if any(e.other_vert(v) is anchor for e in v.link_edges):
                    continue
                if _segment_blocked(mat, v.co, anchor.co, occ_now):
                    stats["loose_blocked"] += 1
                    continue
                try:
                    bm.edges.new((v, anchor))
                    stats["closed_to_anchor"] += 1
                except ValueError:
                    pass

    # Weld the duplicates this pass just made. Two chains that meet at a
    # shared anchor each create their own vertex there — the occupancy check
    # is built once, before any of them exist, so it cannot see them. Left
    # unwelded, every chain stays its own island: measured on a production
    # retopo, 120 separate open chains with 240 loose ends and not one closed
    # loop, which means no panel can be filled with faces.
    #
    # Welding by FLAT position is safe precisely because the retopo lives in
    # the flat layout: the two sides of a seam are 3D-coincident but sit on
    # different panels far apart in 2D, so they can never be merged by
    # accident.
    if created_verts:
        targets = {v for v in created_verts if v.is_valid}
        for e in bm.edges:
            if len(e.link_faces) <= 1:
                targets.update(e.verts)
        before = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=list(targets), dist=0.0002)
        stats["welded"] = before - len(bm.verts)
        bm.verts.ensure_lookup_table()

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return stats


#: Status of one sewn seam, as far as the retopo is concerned.
#:
#: MISMATCH and MISALIGNED are kept apart because they are fixed in different
#: ways, and one colour for both hid that: a seam whose two sides hold a
#: different NUMBER of vertices is filled in by Match Seams or Generate, while
#: a seam holding the same number in the wrong PLACES needs those vertices
#: moved — something no pass here does, on purpose (see plan_seam_sync, which
#: skips it as "misaligned" rather than adding more).
STATE_MATCHED = "matched"        # both sides authored, one-to-one, aligned
STATE_MISMATCH = "mismatch"      # both sides authored, different counts
STATE_MISALIGNED = "misaligned"  # same count, but they do not line up
STATE_ONE_SIDED = "one_sided"    # authored on one side only
STATE_UNTOUCHED = "untouched"    # no retopo on either side yet

STATE_ORDER = (STATE_MISMATCH, STATE_MISALIGNED, STATE_ONE_SIDED,
               STATE_MATCHED, STATE_UNTOUCHED)

#: Both verdicts that mean "this seam is wrong", for callers that do not care
#: which of the two it is (selecting the problem vertices, say).
STATE_BAD = (STATE_MISMATCH, STATE_MISALIGNED)

#: Status of one FREE-edge run. A free edge has no partner side, so neither
#: "matched" nor "one_sided" means anything on it — the only question the
#: retopo can get wrong is whether it sits ON the pattern outline.
STATE_FREE_OK = "free_ok"      # every vertex within offset_mm of the outline
STATE_FREE_OFF = "free_off"    # at least one has drifted off it

FREE_STATE_ORDER = (STATE_FREE_OFF, STATE_FREE_OK)


def _partner_us(entries, pair_span, stored_span):
    """Side b's parameters, read in the direction the PAIR runs.

    pair_spans orients side "b" to run with side "a", handing back the span
    reversed where that is the matching direction — but map_retopo_to_spans
    files parameters against the span as STORED, so on a reversed pair the
    two sides' parameters run against each other. Comparing them as they come
    then reports a perfectly paired seam as wildly out: measured on the
    production jacket (ML_Retopo_06), three seams read 20.3 / 21.1 / 13.2 mm
    apart where the true figures are 0.045 / 0.031 / 0.047 mm. Roughly half
    the pairs in that file are stored reversed (23 of 43), so this is the
    common case, not an edge one.

    plan_seam_generation solves the same problem with _reparam_existing,
    which re-projects; here the spans are the same polyline either way round,
    so 1-u is exact and no projection is needed.
    """
    us = [u for u, _i, _d in entries]
    if list(pair_span) == list(stored_span):
        return us
    return sorted(1.0 - u for u in us)


def classify_seam_pairs(guide, retopo, flat_sk, match_distance=0.0, tol=0.005,
                        match_mm=1.0, min_chain=2):
    """Report, per sewn seam, whether the retopo actually pairs up on it.

    Two numbers decide whether a seam is genuinely matched, and both are
    reported so a "matched" verdict can be checked rather than trusted:

      align_mm  — how far apart corresponding vertices sit ALONG the seam.
                  Two vertices meant to be sewn together have to land on the
                  same point of the seam; drift here is what breaks the
                  "coincident in 3D means sewn" assumption downstream.
      offset_mm — how far the retopo vertices stray from the Guide outline
                  they are supposed to lie on. A seam can be perfectly
                  matched side-to-side and still be sitting off the pattern.

    Returns (rows, summary). Each row:
      {"state", "count_a", "count_b", "align_mm", "offset_mm", "length_mm",
       "span_a", "span_b"}
    """
    bvs, vns, parts = anch.find_vertex_partners(guide, match_distance=match_distance)
    _a, _b, basis_co = anch._collect_boundary(guide)
    flat_co = anch.get_flat_co(guide, flat_sk)
    anchors = anch.find_anchors(bvs, vns, parts)
    spans = anch.build_spans(vns, anchors)
    pairs, _unpaired = anch.pair_spans(spans, parts, basis_co)

    assign = map_retopo_to_spans(guide, retopo, spans, basis_co, flat_co, tol=tol)
    span_index = build_span_index(spans)

    edge_eps = 1e-3
    rows = []
    for pr in pairs:
        ea = assign.get(span_index[tuple(pr["a"])], [])
        eb = assign.get(span_index[tuple(pr["b"])], [])

        def _authored(entries):
            return (len(entries) >= min_chain
                    and any(edge_eps < u < 1.0 - edge_eps for u, _i, _d in entries))

        a_ok, b_ok = _authored(ea), _authored(eb)
        length = anch._arc_lengths(pr["a"], basis_co)[-1]
        offset = max((d for _u, _i, d in list(ea) + list(eb)), default=0.0)

        if not a_ok and not b_ok:
            state, align = STATE_UNTOUCHED, 0.0
        elif a_ok != b_ok:
            state, align = STATE_ONE_SIDED, 0.0
        else:
            ua = [u for u, _i, _d in ea]
            ub = _partner_us(eb, pr["b"], spans[span_index[tuple(pr["b"])]])
            if len(ua) != len(ub):
                # Counts differ, so there is no correspondence to measure —
                # None rather than 0.0, which would read as "perfectly
                # aligned" for exactly the seams that are worst off.
                state, align = STATE_MISMATCH, None
            else:
                align = max(abs(x - y) for x, y in zip(ua, ub)) * length
                state = (STATE_MATCHED if align * 1000.0 <= match_mm
                         else STATE_MISALIGNED)
        rows.append({
            "state": state,
            "count_a": len(ea),
            "count_b": len(eb),
            "align_mm": None if align is None else align * 1000.0,
            "offset_mm": offset * 1000.0,
            "length_mm": length * 1000.0,
            "span_a": pr["a"],
            "span_b": pr["b"],
            "verts_a": [i for _u, i, _d in ea],
            "verts_b": [i for _u, i, _d in eb],
        })

    # Within a state, worst first; an unmeasurable alignment (differing
    # counts) sorts to the top, since that is the most broken case of all.
    rows.sort(key=lambda r: (
        STATE_ORDER.index(r["state"]),
        0 if r["align_mm"] is None else 1,
        -(r["align_mm"] or 0.0),
    ))
    summary = {s: sum(1 for r in rows if r["state"] == s) for s in STATE_ORDER}
    summary["total"] = len(rows)
    summary["worst_align_mm"] = max(
        (r["align_mm"] for r in rows if r["align_mm"] is not None), default=0.0
    )
    summary["unmeasurable"] = sum(1 for r in rows if r["align_mm"] is None)
    summary["worst_offset_mm"] = max(
        (r["offset_mm"] for r in rows
         if r["state"] in (STATE_MATCHED,) + STATE_BAD),
        default=0.0,
    )
    return rows, summary


def classify_free_runs(guide, retopo, flat_sk, match_distance=0.0, tol=0.005,
                       offset_mm=1.0, min_chain=2, corner_angle_deg=45.0):
    """Report, per FREE-edge run, whether the retopo sits on the outline.

    classify_seam_pairs above only ever looks at SEWN seams, so on a
    production Guide it said nothing at all about 51 of 76 authored free
    spans (measured 2026-09-05) — half the panel boundary was simply not part
    of the report. This is the free-edge half, kept as a separate function
    because Generate reads classify_seam_pairs' rows and depends on their
    shape (span_a / span_b); a free run has no second side to put there.

    The unit is a RUN, not a span: the anchors that divide a hem come from
    the seam analysis and mean nothing on a free edge, so chain_free_spans
    dissolves them and one hem is one row — the same runs Generate divides.

    A free run cannot be "matched" or "one-sided", so the only verdict is
    whether its vertices lie on the Guide outline they are supposed to
    follow: STATE_FREE_OK within offset_mm, STATE_FREE_OFF beyond it.

    Runs carrying fewer than min_chain vertices are left out entirely — they
    are the free-edge equivalent of STATE_UNTOUCHED, which classify_seam_
    pairs also keeps off the overlay.

    Returns (rows, summary). Each row:
      {"state", "count", "offset_mm", "length_mm", "span", "verts"}
    """
    bvs, vns, parts = anch.find_vertex_partners(guide, match_distance=match_distance)
    _a, _b, basis_co = anch._collect_boundary(guide)
    flat_co = anch.get_flat_co(guide, flat_sk)
    corner_verts = anch.find_corner_anchors(
        bvs, vns, parts, flat_co, angle_deg=corner_angle_deg)
    anchors = anch.find_anchors(bvs, vns, parts)
    spans = anch.build_spans(vns, anchors)
    pairs, _unpaired = anch.pair_spans(spans, parts, basis_co)
    span_index = build_span_index(spans)

    paired_idx = set()
    for pr in pairs:
        paired_idx.add(span_index[tuple(pr["a"])])
        paired_idx.add(span_index[tuple(pr["b"])])
    free_idx = [si for si in range(len(spans)) if si not in paired_idx]

    assign = map_retopo_to_spans(guide, retopo, spans, basis_co, flat_co, tol=tol)

    # Exactly the runs Generate would divide, so a row and a Generate job
    # always talk about the same stretch of outline.
    runs = []
    for run in chain_free_spans(spans, free_idx, vns):
        if len(run) > 3 and run[0] == run[-1]:
            runs.extend(_open_closed_run(run[:-1], corner_verts, basis_co))
        else:
            runs.append(run)
    span_runs = len(runs)
    extra_runs = anch.anchorless_loop_runs(
        bvs, vns, anchors, corner_verts=corner_verts, basis_co=basis_co)
    runs.extend(extra_runs)

    # A panel sewn to nothing carries no anchor, so build_spans yields no span
    # for it and `assign` knows nothing about it. Its retopo vertices are
    # found by projecting them onto the run directly — affordable only
    # because those runs are a handful (17 of 53 on the Guide measured).
    bverts, _be = collect_retopo_boundary(retopo)
    rmat = retopo.matrix_world

    rows = []
    for ri, run in enumerate(runs):
        if len(run) < 2:
            continue
        run_set = set(run)
        entries = {}          # retopo vert index -> outline offset (m)
        if ri < span_runs:
            for si2, s2 in enumerate(spans):
                if si2 not in paired_idx and s2[0] in run_set and s2[-1] in run_set:
                    for u, vi, d in assign.get(si2, ()):
                        entries[vi] = max(entries.get(vi, 0.0), d)
        else:
            for vi in bverts:
                p = rmat @ retopo.data.vertices[vi].co
                d, _u = _project_to_span(p, run, basis_co, flat_co)
                if d <= tol:
                    entries[vi] = d
        if len(entries) < min_chain:
            continue
        worst = max(entries.values())
        rows.append({
            "state": (STATE_FREE_OFF if worst * 1000.0 > offset_mm
                      else STATE_FREE_OK),
            "count": len(entries),
            "offset_mm": worst * 1000.0,
            "length_mm": anch._arc_lengths(run, basis_co)[-1] * 1000.0,
            "span": run,
            "verts": sorted(entries),
        })

    rows.sort(key=lambda r: (FREE_STATE_ORDER.index(r["state"]),
                             -r["offset_mm"]))
    summary = {s: sum(1 for r in rows if r["state"] == s)
               for s in FREE_STATE_ORDER}
    summary["total"] = len(rows)
    summary["runs"] = len(runs)
    summary["worst_offset_mm"] = max((r["offset_mm"] for r in rows), default=0.0)
    return rows, summary


def run_retopo_seam_sync(guide, retopo, flat_sk, match_distance=0.0,
                         tol=0.005, match_mm=1.0, min_chain=2, dry_run=True,
                         source_spans=None):
    """Full pass. dry_run reports without touching the retopo.

    `source_spans`: span tuples (as in `pairs[i]["a"]`) the user selected as
    the side to copy FROM — see plan_seam_sync. None means every sewn seam,
    copied from whichever side is the fuller one.
    """
    bvs, vns, parts = anch.find_vertex_partners(guide, match_distance=match_distance)
    _a, _b, basis_co = anch._collect_boundary(guide)
    flat_co = anch.get_flat_co(guide, flat_sk)
    anchors = anch.find_anchors(bvs, vns, parts)
    spans = anch.build_spans(vns, anchors)
    pairs, _unpaired = anch.pair_spans(spans, parts, basis_co)

    span_index = build_span_index(spans)
    from mathutils import kdtree

    def _plan():
        """Map the retopo AS IT STANDS NOW onto the spans and plan."""
        assign = map_retopo_to_spans(guide, retopo, spans, basis_co, flat_co,
                                     tol=tol)
        bverts, _be = collect_retopo_boundary(retopo)
        border = sorted(bverts)
        rmat = retopo.matrix_world
        occupied_kd = kdtree.KDTree(len(border))
        for k, vi in enumerate(border):
            occupied_kd.insert(rmat @ retopo.data.vertices[vi].co, k)
        occupied_kd.balance()
        occupied = (occupied_kd, border)
        jobs, skipped = plan_seam_sync(
            pairs, spans, assign, span_index, basis_co, flat_co,
            occupied=occupied, match_mm=match_mm, min_chain=min_chain,
            source_spans=source_spans)
        return jobs, skipped, occupied

    jobs, skipped, occupied = _plan()

    reasons = {}
    for s in skipped:
        reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
    result = {
        "pairs": len(pairs),
        "jobs": len(jobs),
        "missing_total": sum(len(j["missing"]) for j in jobs),
        "new_chains": sum(1 for j in jobs if not j["existing"]),
        # Every seam left alone, whatever the reason; the breakdown is in
        # skipped_reasons ("misaligned" / "needs_removal" /
        # "source_not_authored").
        "skipped_interleaved": len(skipped),
        "skipped_reasons": reasons,
        "skipped_detail": skipped,
        "worst_source_offset": max(
            (j["source_max_offset"] for j in jobs), default=0.0
        ),
        "applied": None,
        "jobs_detail": jobs,
        "passes": 0,
    }
    if not dry_run and jobs:
        # A pass plans against the mesh as it stood BEFORE any vertex was
        # made, so the anchor vertex one job creates is invisible to the job
        # on the neighbouring seam, which then finds that anchor "missing" on
        # the next press (measured on a production file: 40 added, then 4
        # more, then 0). Re-plan on the updated mesh and go again until
        # nothing is left, so one press converges. The cap is a guard, not a
        # budget: the third pass was already empty.
        totals = {}
        while jobs and result["passes"] < 4:
            ap = apply_seam_sync(guide, retopo, jobs, basis_co, flat_co,
                                 occupied=occupied)
            for k, v in ap.items():
                totals[k] = totals.get(k, 0) + v
            result["passes"] += 1
            jobs, _skipped_now, occupied = _plan()
        result["applied"] = totals
    return result


# ---------------------------------------------------------------------------
# Sync, scoped to specific vertices (the knife-cut case)
# ---------------------------------------------------------------------------
#
# run_retopo_seam_sync above mirrors an entire authored side onto its
# partner. That is right for a seam nobody has touched carefully yet, but
# wrong for the common mid-work case: the user knife-cuts ONE vertex into an
# otherwise-already-synced seam to follow a fabric wrinkle. Running the bulk
# Sync then is not dangerous by itself (plan_seam_sync's "authored" gate and
# per-vertex missing-diff would in fact only add that one vertex's
# counterpart) but it re-plans and re-applies EVERY sewn seam on the mesh to
# do it, which is more than the user asked for and, per the 2026-09-02 design
# discussion (see the project handoff notes), does not know to protect the
# result with a Density Pin. This section is the targeted alternative: act
# on exactly the vertices the caller names, and pin both sides of every
# correspondence it touches — created here, or already there — since an
# unpaired pin is unsafe (density_pin.py's module docstring). That danger is
# specific to a SEWN seam, where pinning one half lets the other half be slid
# away from it; a vertex on a FREE edge has no other half, so it is pinned on
# its own (see `free_only` below).


def plan_selected_vertex_sync(spans, pairs, span_index, assign, basis_co, flat_co,
                              selected_indices, match_mm=1.0):
    """Plan a Sync restricted to `selected_indices` (Retopo vertex indices).

    For each selected vertex, finds the Guide span(s) it sits on (via
    `assign`, so an anchor vertex belonging to two spans is handled
    correctly) and, for each, the seam partner span. When the partner side
    already has a vertex within `match_mm` of the same 3D-arc-length
    parameter, nothing needs creating — the pair goes to `already_synced`
    so the caller can still pin it. Otherwise the missing parameter is
    queued.

    Missing parameters that land on the SAME partner span are grouped into
    one job (matching plan_seam_sync's job shape), because
    apply_selected_vertex_sync applies a job's insertions against a chain
    that updates as it goes — splitting them into separate jobs would let a
    second insertion look for an edge (A, B) that the first insertion just
    replaced with (A, new) + (new, B), and silently find nothing.

    Returns (jobs, already_synced, unmatched, free_only):
      jobs           — like plan_seam_sync's, plus a parallel "missing_src"
                       list (same length as "missing") naming which
                       selected vertex each missing parameter came from
      already_synced — [(selected_vert, partner_vert)] pairs needing no new
                       vertex — both still want a Density Pin
      unmatched      — selected vertices that lie on no Guide span at all
                       (an interior cut), left untouched
      free_only      — selected vertices every one of whose spans is a FREE
                       edge. There is no partner to create, but the vertex is
                       still a deliberate boundary point, so it wants a pin
                       just as much as a seam correspondence does. Before
                       this these were dropped in silence: measured on a
                       production file, 60 free-span vertices produced zero
                       jobs, zero already_synced and zero unmatched, so the
                       operator reported nothing at all had happened.
    """
    vert_spans = {}
    for si, entries in assign.items():
        for u, vi, d in entries:
            vert_spans.setdefault(vi, []).append((si, u, d))

    span_partner = {}
    for pr in pairs:
        ia = span_index[tuple(pr["a"])]
        ib = span_index[tuple(pr["b"])]
        span_partner[ia] = (ib, "b")
        span_partner[ib] = (ia, "a")

    by_span = {}      # dst span index -> job-in-progress dict
    seen_u = {}        # dst span index -> set of already-queued rounded u's
    already_synced = []
    unmatched = []
    free_only = []

    for vi in selected_indices:
        entries = vert_spans.get(vi)
        if not entries:
            unmatched.append(vi)
            continue
        if all(si not in span_partner for si, _u, _d in entries):
            # Every span this vertex sits on is a free edge — nothing to
            # mirror, but it is still on the boundary and worth pinning.
            free_only.append(vi)
            continue
        for si, u, d in entries:
            partner = span_partner.get(si)
            if partner is None:
                continue  # this span has no sewn partner (free edge)
            dsi, dst_side = partner
            dst_span = spans[dsi]
            dst_entries = assign.get(dsi, [])
            total = anch._arc_lengths(dst_span, basis_co)[-1]
            if total <= 1e-12:
                continue
            u_tol = (match_mm / 1000.0) / total
            match = next((e for e in dst_entries if abs(e[0] - u) <= u_tol), None)
            if match is not None:
                already_synced.append((vi, match[1]))
                continue
            key = round(u, 6)
            bucket = seen_u.setdefault(dsi, set())
            if key in bucket:
                continue
            bucket.add(key)
            entry = by_span.setdefault(dsi, {
                "span": dst_span, "target_side": dst_side,
                "existing": [(eu, ei) for eu, ei, _ed in dst_entries],
                "missing": [],
            })
            entry["missing"].append((u, vi, d))

    jobs = []
    for entry in by_span.values():
        ordered = sorted(entry["missing"], key=lambda m: m[0])
        jobs.append({
            "span": entry["span"],
            "target_side": entry["target_side"],
            "existing": entry["existing"],
            "missing": [u for u, _vi, _d in ordered],
            "missing_src": [vi for _u, vi, _d in ordered],
            "source_max_offset": max(d for _u, _vi, d in ordered),
        })
    return jobs, already_synced, unmatched, free_only


def apply_selected_vertex_sync(retopo, jobs, basis_co, flat_co, already_synced,
                               pin=True, free_only=()):
    """Create the counterpart vertex for each plan_selected_vertex_sync job,
    then (when `pin` is set) Density-Pin every vertex this call knows to be
    an intentional seam correspondence point on BOTH sides: a job's source
    vertex and its newly created counterpart, plus both sides of every
    already_synced pair that needed no new vertex at all.

    `free_only` vertices are pinned too, with nothing created for them. They
    sit on a FREE edge, which has no other side — so density_pin's warning
    that a one-sided pin is unsafe (see its module docstring) does not apply:
    that warning is about pinning one half of a SEWN correspondence, where
    the unpinned half can then be slid away from its partner. A free edge has
    no partner to drift from.

    Marking happens in the SAME bmesh session as the vertex creation and
    welding, not a second bmesh reopened afterward to re-find the new
    vertices by position — the source and already-synced vertices are
    captured as BMVert references up front (before any topology change),
    and the newly created ones are already BMVert objects in hand, so
    nothing here needs to survive a round trip through an index that
    bmesh deletion (remove_doubles, in the weld pass below) could have
    invalidated. See density_pin.mark's own docstring for why creating its
    attribute layer for the first time is itself safe to do on these same
    references: it captures each vertex's `.index` before touching the
    layer and re-fetches by index afterward, which is exactly the case
    this function's own welding pass would otherwise have to worry about.

    Job placement logic mirrors apply_seam_sync's (extend past an end /
    attach to a lone vertex / split mid-chain / lay a fresh chain when the
    target side is empty), duplicated rather than shared because this
    version also has to track which selected vertex each insertion belongs
    to, for the pinning pass below.

    Returns a stats dict: created, extended, inserted_mid, new_chains,
    ngons_created, welded, pinned.
    """
    from . import density_pin as dp
    from ..clo_projector.core import SHAPEKEY_NAME

    mesh = retopo.data
    mat = retopo.matrix_world
    inv = mat.inverted()

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()

    shape_layers = bm.verts.layers.shape
    basis_layer = shape_layers.get("Basis")
    proj_layer = shape_layers.get(SHAPEKEY_NAME)

    # Every vertex this call might need to pin that already existed BEFORE
    # this session touches the mesh -- captured as BMVert refs now, while
    # their original indices are still guaranteed valid.
    pin_seed_idx = {i for a, b in already_synced for i in (a, b)}
    src_indices = {vi for job in jobs for vi in job["missing_src"]}
    pin_seed_idx |= src_indices
    pin_seed_idx |= set(free_only)
    seed_refs = {i: bm.verts[i] for i in pin_seed_idx if i < len(bm.verts)}

    stats = {"created": 0, "extended": 0, "inserted_mid": 0,
             "new_chains": 0, "ngons_created": 0, "welded": 0, "pinned": 0}

    def _place(vert, u, span):
        flat_w, basis_w = span_point_at_u(span, u, basis_co, flat_co)
        local_flat = inv @ flat_w
        vert.co = local_flat
        if basis_layer is not None:
            vert[basis_layer] = local_flat
        if proj_layer is not None:
            vert[proj_layer] = inv @ basis_w

    created_this_call = []   # every new BMVert, for the weld pass
    paired_refs = []         # (source BMVert or None, new BMVert)

    for job in jobs:
        span = job["span"]
        existing = sorted(job["existing"])
        chain = [bm.verts[i] for _u, i in existing]
        us = [eu for eu, _i in existing]

        if not chain:
            prev = None
            for u, src_vi in zip(job["missing"], job["missing_src"]):
                v = bm.verts.new((0.0, 0.0, 0.0))
                _place(v, u, span)
                created_this_call.append(v)
                paired_refs.append((seed_refs.get(src_vi), v))
                stats["created"] += 1
                if prev is not None:
                    try:
                        bm.edges.new((prev, v))
                    except ValueError:
                        pass
                prev = v
            stats["new_chains"] += 1
            bm.verts.ensure_lookup_table()
            continue

        for u, src_vi in zip(job["missing"], job["missing_src"]):
            if u < us[0]:
                v = bm.verts.new((0.0, 0.0, 0.0))
                _place(v, u, span)
                try:
                    bm.edges.new((v, chain[0]))
                except ValueError:
                    pass
                chain.insert(0, v)
                us.insert(0, u)
                stats["created"] += 1
                stats["extended"] += 1
            elif u > us[-1]:
                v = bm.verts.new((0.0, 0.0, 0.0))
                _place(v, u, span)
                try:
                    bm.edges.new((chain[-1], v))
                except ValueError:
                    pass
                chain.append(v)
                us.append(u)
                stats["created"] += 1
                stats["extended"] += 1
            elif len(chain) < 2:
                v = bm.verts.new((0.0, 0.0, 0.0))
                _place(v, u, span)
                try:
                    bm.edges.new((chain[0], v))
                except ValueError:
                    pass
                chain.append(v)
                us.append(u)
                stats["created"] += 1
                stats["extended"] += 1
            else:
                k = 0
                for j in range(len(us) - 1):
                    if us[j] <= u <= us[j + 1]:
                        k = j
                        break
                va, vb = chain[k], chain[k + 1]
                edge = None
                for e in va.link_edges:
                    if e.other_vert(va) is vb:
                        edge = e
                        break
                if edge is None:
                    continue  # not directly connected; source stays unpaired
                faces_before = {f: len(f.verts) for f in edge.link_faces}
                _new_edge, v = bmesh.utils.edge_split(edge, va, 0.5)
                _place(v, u, span)
                for f, n in faces_before.items():
                    if f.is_valid and len(f.verts) > n and len(f.verts) > 4:
                        stats["ngons_created"] += 1
                chain.insert(k + 1, v)
                us.insert(k + 1, u)
                stats["created"] += 1
                stats["inserted_mid"] += 1
            created_this_call.append(v)
            paired_refs.append((seed_refs.get(src_vi), v))
            bm.verts.ensure_lookup_table()

    # Weld exactly like apply_seam_sync: a vertex created here for one job
    # can coincide with one created for another (a shared anchor) or with
    # something already on the boundary.
    if created_this_call:
        targets = {v for v in created_this_call if v.is_valid}
        for e in bm.edges:
            if len(e.link_faces) <= 1:
                targets.update(e.verts)
        before = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=list(targets), dist=0.0002)
        stats["welded"] = before - len(bm.verts)
        bm.verts.ensure_lookup_table()

    if pin:
        pin_verts = [v for v in seed_refs.values() if v.is_valid]
        for src_ref, v in paired_refs:
            if v.is_valid:
                pin_verts.append(v)
            if src_ref is not None and src_ref.is_valid:
                pin_verts.append(src_ref)
        stats["pinned"] = dp.mark(bm, pin_verts, 1)

    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return stats


def run_selected_vertex_sync(guide, retopo, flat_sk, selected_indices,
                             tol=0.005, match_mm=1.0, pin=True, dry_run=True):
    """Full pass, scoped to `selected_indices` — see plan_selected_vertex_
    sync and apply_selected_vertex_sync above. dry_run reports without
    touching the retopo (and without marking any pin).
    """
    bvs, vns, parts = anch.find_vertex_partners(guide)
    _a, _b, basis_co = anch._collect_boundary(guide)
    flat_co = anch.get_flat_co(guide, flat_sk)
    anchors = anch.find_anchors(bvs, vns, parts)
    spans = anch.build_spans(vns, anchors)
    pairs, _unpaired = anch.pair_spans(spans, parts, basis_co)

    assign = map_retopo_to_spans(guide, retopo, spans, basis_co, flat_co, tol=tol)
    span_index = build_span_index(spans)

    jobs, already_synced, unmatched, free_only = plan_selected_vertex_sync(
        spans, pairs, span_index, assign, basis_co, flat_co,
        selected_indices, match_mm=match_mm,
    )

    result = {
        "selected": len(selected_indices),
        "unmatched": len(unmatched),
        "unmatched_verts": unmatched,
        "jobs": len(jobs),
        "missing_total": sum(len(j["missing"]) for j in jobs),
        "already_synced": len(already_synced),
        "free_only": len(free_only),
        "free_only_verts": free_only,
        "applied": None,
    }
    if not dry_run and (jobs or (pin and (already_synced or free_only))):
        result["applied"] = apply_selected_vertex_sync(
            retopo, jobs, basis_co, flat_co, already_synced, pin=pin,
            free_only=free_only,
        )
    return result


# ---------------------------------------------------------------------------
# Generating a seam from nothing
# ---------------------------------------------------------------------------
#
# The sync above mirrors what one side already has, so a seam nobody has
# touched yet stays empty — there is nothing to copy. This lays down BOTH
# sides at once from the Guide alone: pick a division count, and each side
# gets its vertices at the same parameters along the seam, which puts them on
# the same points in 3D by construction.


def span_is_unambiguous(span, basis_co, flat_co, samples=9, u_tol=0.02):
    """Can a position on this span be identified from its flat coordinates?

    Some Guide spans fold back onto themselves in the FLAT layout — a
    zero-width slit, where the boundary walks up one side of the cut and back
    down the other, leaves both halves sitting on top of each other. A point
    placed at u=0.8 on such a span then occupies the same flat position as
    u=0.2, so reading a position back gives the wrong parameter and the two
    sides of the seam cannot be made to correspond.

    Measured on a production Guide: a 110mm span whose parameters folded at
    the midpoint (0.6 read back as 0.4, 0.8 as 0.2, 1.0 as 0.0), which made a
    freshly generated seam report 66mm out of alignment.

    The test is a round trip: place a sample, look it up again, and require
    the parameter to come back.
    """
    for k in range(samples):
        u = k / (samples - 1)
        flat_pos, _b = span_point_at_u(span, u, basis_co, flat_co)
        _d, u_back = _project_to_span(flat_pos, span, basis_co, flat_co)
        if abs(u_back - u) > u_tol:
            return False
    return True


def merge_seam_rows(rows, states=(STATE_UNTOUCHED,)):
    """Join seams that continue into each other into one longer seam.

    The anchor analysis cuts a seam wherever three or more boundary vertices
    coincide. Around a lapel notch that happens at nearly every vertex, so a
    continuous seam arrives here as dozens of ~3mm pieces, each too short to
    divide — measured on a production Guide, 866mm of genuine seam across 271
    pieces, forming 26 stretches of which the longest ran 108mm. None of it
    got a single vertex, and the panel outlines never closed.

    Two seams may be joined only when BOTH sides continue together: side a's
    end anchor is side a's start anchor of the next seam, AND the same holds
    for side b. That keeps the two sides corresponding, which is the whole
    point of the pairing.

    Returns new row dicts with concatenated spans, carrying the summed
    length. Rows outside `states`, and any that cannot be joined, come back
    unchanged.
    """
    idx = [i for i, r in enumerate(rows) if r["state"] in states]
    if not idx:
        return list(rows)

    def joints(r):
        return ((r["span_a"][0], r["span_b"][0]),
                (r["span_a"][-1], r["span_b"][-1]))

    at_joint = {}
    for i in idx:
        for j in joints(rows[i]):
            at_joint.setdefault(j, []).append(i)

    seen, out = set(), []
    for i in idx:
        if i in seen:
            continue
        chain = [i]
        seen.add(i)
        # Extend from both ends while exactly two seams meet at the joint.
        for which in (0, 1):
            joint = joints(rows[i])[which]
            while True:
                here = at_joint.get(joint, [])
                nxt = [k for k in here if k not in seen]
                if len(here) != 2 or len(nxt) != 1:
                    break
                k = nxt[0]
                seen.add(k)
                if which == 0:
                    chain.insert(0, k)
                else:
                    chain.append(k)
                j0, j1 = joints(rows[k])
                joint = j1 if j0 == joint else j0

        if len(chain) == 1:
            out.append(rows[i])
            continue

        # Stitch the spans head-to-tail, both sides in step.
        sa = list(rows[chain[0]]["span_a"])
        sb = list(rows[chain[0]]["span_b"])
        ok = True
        for k in chain[1:]:
            na, nb = list(rows[k]["span_a"]), list(rows[k]["span_b"])
            if sa[-1] == na[0]:
                pass
            elif sa[-1] == na[-1]:
                na, nb = list(reversed(na)), list(reversed(nb))
            elif sa[0] == na[-1]:
                sa, sb, na, nb = na, nb, sa, sb
            elif sa[0] == na[0]:
                sa, sb = list(reversed(sa)), list(reversed(sb))
            else:
                ok = False
                break
            if sa[-1] != na[0] or sb[-1] != nb[0]:
                ok = False
                break
            sa.extend(na[1:])
            sb.extend(nb[1:])
        if not ok:
            out.extend(rows[k] for k in chain)
            continue

        merged = dict(rows[chain[0]])
        merged["span_a"] = sa
        merged["span_b"] = sb
        merged["length_mm"] = sum(rows[k]["length_mm"] for k in chain)
        merged["merged_from"] = len(chain)
        out.append(merged)

    out.extend(rows[i] for i in range(len(rows)) if rows[i]["state"] not in states)
    return out


def resolve_fold_axis_hard_us(span_a, span_b, root_a, root_b, flat_co,
                              basis_co, folds_by_root, length_m,
                              match_mm=1.0, spacing_mm=0.0, count=8):
    """Both-sides-independent fold-axis crossing resolution, shared by
    plan_seam_generation (Generate Seam Chain) and Adjust Density's hard_us
    pinning (span_density.py) — extracted 2026-09-02 so both callers use the
    exact same axis_tol derivation and midpoint-merge rule instead of two
    copies drifting apart. See plan_seam_generation's docstring for the full
    reasoning behind "why midpoint" and "why axis_tol, not match_mm alone"
    (that reasoning is unchanged by this extraction, just no longer
    duplicated at the call site).

    `length_m` is the seam's own 3D length in metres (used only to convert
    axis_tol from millimetres into the same 0..1 parameter space find_axis_
    breaks and division_parameters work in) — pass 0 to force an empty
    result (e.g. a degenerate span) rather than dividing by it.

    Returns (hard_us, mismatched): `hard_us` is a list of 0..1 parameters
    against span_a's OWN arc length, meant to be used for BOTH sides (the
    same reason plan_seam_generation reads corner_verts off side a only —
    the two sides share the same 3D curve). `mismatched` is True when some
    crossing on side a had a same-crossing candidate on side b that
    disagreed by more than axis_tol — the caller decides what to do with
    that (plan_seam_generation reports it in `axis_mismatches` and still
    generates the seam without a fold pin there; a caller with an existing
    seam might prefer to skip the group instead of guessing).
    """
    if not folds_by_root or root_a is None or length_m <= 0.0:
        return [], False

    spacing = (spacing_mm if spacing_mm > 0.0
              else (length_m * 1000.0) / max(1, int(count) - 1))
    axis_tol = (max(match_mm, 0.1 * spacing) / 1000.0) / length_m

    us_a = []
    for f in folds_by_root.get(root_a, ()):
        us_a.extend(find_axis_breaks(span_a, flat_co, basis_co, f["a"], f["b"]))
    us_b = []
    for f in folds_by_root.get(root_b, ()):
        us_b.extend(find_axis_breaks(span_b, flat_co, basis_co, f["a"], f["b"]))

    hard_us = []
    mismatched = False
    for ua in us_a:
        if not us_b:
            # Nothing on side b to contradict it -- see plan_seam_
            # generation's docstring on why this is not gated on whether
            # side b's ISLAND is self-symmetric.
            hard_us.append(ua)
            continue
        ub = min(us_b, key=lambda u: abs(u - ua))
        if abs(ua - ub) <= axis_tol:
            hard_us.append(0.5 * (ua + ub))
        else:
            mismatched = True
    return hard_us, mismatched


def plan_seam_generation(rows, spans, assign, span_index, basis_co, flat_co,
                         occupied=None, bedge_set=None, orphans=None,
                         tol=0.005, count=8, spacing_mm=0.0,
                         min_seam_mm=5.0, match_mm=1.0, corner_verts=(),
                         states=(STATE_UNTOUCHED,),
                         folds_by_root=None, island_of=None,
                         straight_tol_mm=0.0, stats=None):
    """Turn empty seams into chain-creation jobs for BOTH of their sides.

    spacing_mm, when positive, wins over `count`: divisions are chosen per
    seam so the vertices land roughly that far apart, which keeps density
    even across seams of very different lengths. `count` gives every seam the
    same number regardless of how long it is.

    min_seam_mm drops degenerate spans. A real garment carries a few
    millimetre-long seams at pattern corners (measured: two 1mm seams on a
    production jacket); dividing those adds vertices nobody wants.

    corner_verts pins pattern corners. The parameters are taken from side a
    and used for BOTH sides, because the two sides have to be divided
    identically for the seam to come out matched — side b's own corner sits
    at the same place on the shared 3D curve, so reading it off side a costs
    nothing and keeps the two lists identical by construction.

    folds_by_root / island_of pin a self-symmetric island's fold axis the
    same way — a collar sewn to a bodice can EACH be cut on the fold, their
    shared neckline seam passing through both islands' own centre point at
    once (see find_axis_breaks). Both sides' crossings are found
    independently and, where they describe the same crossing, the break goes
    at their MIDPOINT: one parameter has to serve both sides, neither fitted
    axis is ground truth, and the midpoint is what halves the worst error.

    "The same crossing" is judged by the spacing, not by match_mm — see the
    axis_tol comment below for why, and for the measurement behind it. A
    crossing side b disagrees with beyond that is not forced through: the
    seam is left undivided there and reported (the returned `axis_
    mismatches`), because a seam assembled off-centre — the collar not quite
    centred on the bodice — would otherwise get a vertex that is on side a's
    axis and nowhere near side b's, silently "fixing" one side while leaving
    the other wrong. Where side b produces no crossing at all there is
    nothing to contradict side a and its crossing is used as-is.

    Returns (jobs, ambiguous, axis_mismatches).

    A seam counts as empty when neither side is AUTHORED, which still allows
    a stray vertex sitting on one of its anchors — anchors are shared with
    the neighbouring seams, so the chain next door leaves one behind. Those
    are matched against the wanted parameters and skipped, otherwise that
    side ends up one vertex ahead of its partner and the seam comes out
    mismatched (measured: 7 against 6 on the first attempt).
    """
    jobs = []
    ambiguous = []
    axis_mismatches = []
    for r in rows:
        if r["state"] not in states:
            continue
        if not (span_is_unambiguous(r["span_a"], basis_co, flat_co)
                and span_is_unambiguous(r["span_b"], basis_co, flat_co)):
            ambiguous.append(r)
            continue

        length = r["length_mm"] / 1000.0
        u_tol = (match_mm / 1000.0) / length if length > 0 else 0.0

        # Fold-axis pinning: both sides' crossings are found independently
        # and merged at their midpoint when they agree within axis_tol (NOT
        # match_mm alone — that measures sewn-vertex agreement, this is two
        # axes FITTED independently on two different islands; see
        # resolve_fold_axis_hard_us's docstring for the full history behind
        # this tolerance, extracted 2026-09-02 so Adjust Density's hard_us
        # pinning shares the exact same derivation instead of drifting from
        # a second copy).
        root_a = island_of.get(r["span_a"][0]) if island_of is not None else None
        root_b = island_of.get(r["span_b"][0]) if island_of is not None else None
        hard_us, mismatched = resolve_fold_axis_hard_us(
            r["span_a"], r["span_b"], root_a, root_b, flat_co, basis_co,
            folds_by_root, length, match_mm=match_mm,
            spacing_mm=spacing_mm, count=count)
        if mismatched:
            axis_mismatches.append(r)

        # A span below the minimum is not worth dividing, but its two anchors
        # still have to exist: they are shared with the neighbouring spans,
        # and without them the chains either side have nothing to weld to and
        # the panel outline stays broken. Give it its endpoints and no more.
        # straight_tol_mm: a straight stretch gets no interior vertex — but
        # only where BOTH sides are straight (span_b is oriented like span_a
        # by pair_spans, so the same u names the same sewn point on each).
        us = anch.division_parameters(
            r["span_a"], basis_co, hard_verts=corner_verts, hard_us=hard_us,
            count=count, spacing_mm=spacing_mm, min_seam_mm=min_seam_mm,
            flat_co=flat_co, straight_tol_mm=straight_tol_mm,
            partner=r["span_b"], stats=stats)
        n = len(us)

        for span, side in ((r["span_a"], "a"), (r["span_b"], "b")):
            # A merged run is not itself in the index, so gather what sits on
            # any of the original spans it was built from.
            si = span_index.get(tuple(span))
            if si is not None:
                raw = [(si, u, i) for u, i, _d in assign.get(si, [])]
            else:
                span_set = set(span)
                raw = []
                for si2, s2 in enumerate(spans):
                    if s2[0] in span_set or s2[-1] in span_set:
                        raw.extend((si2, u, i) for u, i, _d
                                   in assign.get(si2, []))
            # Re-read against THIS span before ordering or comparing. A merged
            # row is not one of the original spans, so its parts each carry
            # their own 0..1; and even a single span reached through
            # span_index can be the reversed orientation, whose parameters
            # then run 1..0 against the ones in `us` (see _reparam_existing).
            existing = _reparam_existing(raw, span, spans, basis_co, flat_co,
                                         max_dist=tol)
            existing = augment_existing_by_position(
                existing, span, basis_co, flat_co, orphans, tol=tol)
            covered = covered_intervals(existing, bedge_set)
            missing, groups = _divide_missing(
                us, existing, u_tol, covered, occupied, span,
                basis_co, flat_co)
            if not missing:
                continue
            jobs.append({
                "span": span,
                "target_side": side,
                "existing": existing,
                "missing": missing,
                "groups": groups,
                "source_count": n,
                "source_max_offset": 0.0,
            })
    return jobs, ambiguous, axis_mismatches


def _seam_distance_to_point(row, point, flat_co):
    """Closest approach from `point` to either side of a seam, in flat space."""
    best = None
    for span in (row["span_a"], row["span_b"]):
        for i in range(len(span) - 1):
            a, b = flat_co[span[i]], flat_co[span[i + 1]]
            ab = b - a
            d2 = ab.dot(ab)
            if d2 <= 1e-18:
                q = a
            else:
                t = max(0.0, min(1.0, (point - a).dot(ab) / d2))
                q = a + ab * t
            d = (point - q).length
            if best is None or d < best:
                best = d
    return best if best is not None else float("inf")


def chain_free_spans(spans, free_idx, vert_neighbors):
    """Join adjacent free-edge spans into continuous runs.

    Free edges have no sewn partner, so there is nothing to align against and
    no reason to respect the anchors that divide them. Those anchors come
    from the seam analysis and can chop a hem into millimetre pieces — on a
    production Guide, 62 free spans totalling 2779mm, most of them under the
    5mm minimum and therefore skipped, which left the panel boundary open.

    Merging first means a hem is divided once, evenly, along its whole
    length.

    Returns a list of vertex-index polylines.
    """
    free = set(free_idx)
    ends = {}
    for si in free:
        for v in (spans[si][0], spans[si][-1]):
            ends.setdefault(v, []).append(si)

    runs, seen = [], set()
    for si in free_idx:
        if si in seen:
            continue
        # Walk out from this span in both directions along shared endpoints.
        chain = [si]
        seen.add(si)
        for direction in (0, -1):
            end_v = spans[si][direction]
            while True:
                nxt = [j for j in ends.get(end_v, ()) if j not in seen]
                # Only extend through a point where exactly two free spans
                # meet; anywhere else the outline genuinely branches.
                if len(nxt) != 1 or len(ends.get(end_v, ())) != 2:
                    break
                j = nxt[0]
                seen.add(j)
                if direction == 0:
                    chain.insert(0, j)
                else:
                    chain.append(j)
                end_v = spans[j][-1] if spans[j][0] == end_v else spans[j][0]

        # Stitch the span vertex lists head-to-tail into one polyline.
        poly = list(spans[chain[0]])
        for j in chain[1:]:
            nxt = list(spans[j])
            if poly[-1] == nxt[0]:
                poly.extend(nxt[1:])
            elif poly[-1] == nxt[-1]:
                poly.extend(reversed(nxt[:-1]))
            elif poly[0] == nxt[-1]:
                poly = nxt[:-1] + poly
            elif poly[0] == nxt[0]:
                poly = list(reversed(nxt[1:])) + poly
            else:
                continue    # not actually adjacent; leave it out
        runs.append(poly)
    return runs


def _open_closed_run(loop, corner_verts, basis_co):
    """Cut a closed vertex loop into open runs at its corners (else in half)."""
    corners = set(corner_verts or ())
    cuts = [i for i, v in enumerate(loop) if v in corners]
    if len(cuts) < 2:
        cum = anch._arc_lengths(loop + [loop[0]], basis_co)
        half = cum[-1] * 0.5
        mid = min(range(1, len(loop)), key=lambda i: abs(cum[i] - half))
        cuts = [0, mid]
    runs = []
    for k in range(len(cuts)):
        a = cuts[k]
        b = cuts[(k + 1) % len(cuts)]
        run = loop[a:b + 1] if a < b else loop[a:] + loop[:b + 1]
        if len(run) >= 2:
            runs.append(run)
    return runs


def plan_free_edge_generation(spans, free_idx, assign, basis_co, flat_co,
                              vert_neighbors, occupied=None,
                              bedge_set=None, orphans=None, tol=0.005,
                              count=8,
                              spacing_mm=0.0, min_seam_mm=5.0,
                              corner_verts=(), extra_runs=(),
                              folds_by_root=None, island_of=None,
                              straight_tol_mm=0.0, stats=None):
    """Divide the pattern's FREE edges — hems, openings, necklines, a collar's
    outer edge — the ones with no sewn partner at all.

    Nothing to mirror here, so the only question is how finely to divide, and
    the answer is the same spacing used for seams. Without these, a panel's
    boundary never closes, and an open boundary cannot be filled with faces.

    extra_runs carries the loops that have no anchor at all, which build_spans
    cannot represent (see anchor_segments.anchorless_loop_runs). They are
    already cut into open runs, so they divide exactly like any other free
    edge from here on.

    folds_by_root / island_of (both optional — pass both or neither) pin a
    self-symmetric island's fold axis the same way a pattern corner is
    pinned: `island_of[run[0]]` looks up which Guide island the run belongs
    to, `folds_by_root` supplies that island's fold(s) (there can be more
    than one — a doubly-symmetric panel like a waistband), and
    find_axis_breaks turns each into a forced division point. Without this a
    run that happens to thread past the island's own mirror axis is divided
    by plain arc length and generally does NOT land a vertex on the axis —
    see the fold-line UI overlay for what that looks like.
    """
    jobs, ambiguous = [], []
    runs = []
    for run in chain_free_spans(spans, free_idx, vert_neighbors):
        # An island none of whose spans found a partner (a patch pocket: its
        # seam side sits on a slit of the body panel, and the slit's two sides
        # pair with each other) chains into one CLOSED run. A closed polyline
        # never passes span_is_unambiguous — u=1 reads back as u=0 — so the
        # whole island was dropped as "folding back on itself", silently.
        # Measured: 4 pocket islands in the 13 ambiguous runs of a production
        # Guide, and not one vertex generated on them. Open the loop the way
        # anchorless_loop_runs does: at the pattern corners, else in half.
        if len(run) > 3 and run[0] == run[-1]:
            runs.extend(_open_closed_run(run[:-1], corner_verts, basis_co))
        else:
            runs.append(run)
    runs.extend(extra_runs)
    for span in runs:
        L = anch._arc_lengths(span, basis_co)[-1] * 1000.0
        if not span_is_unambiguous(span, basis_co, flat_co):
            ambiguous.append(span)
            continue
        hard_us = []
        if folds_by_root and island_of is not None:
            root = island_of.get(span[0])
            for f in folds_by_root.get(root, ()):
                hard_us.extend(find_axis_breaks(span, flat_co, basis_co,
                                                f["a"], f["b"]))
        # Under the minimum: anchors only, so the outline still joins up.
        # Corners survive the merge here — chain_free_spans deliberately
        # dissolves the anchors that divided the hem, and a pattern corner is
        # exactly the one it must not lose.
        us = anch.division_parameters(
            span, basis_co, hard_verts=corner_verts, hard_us=hard_us,
            count=count, spacing_mm=spacing_mm, min_seam_mm=min_seam_mm,
            flat_co=flat_co, straight_tol_mm=straight_tol_mm, stats=stats)
        n = len(us)
        # A merged run spreads across several original spans, so gather what
        # is already on any of them.
        span_set = set(span)
        raw = []
        for si2, s2 in enumerate(spans):
            if s2[0] in span_set or s2[-1] in span_set:
                raw.extend((si2, u, i) for u, i, _d in assign.get(si2, []))
        # Those parameters were measured against the ORIGINAL spans, not this
        # merged run, so they have to be re-read along the run before anything
        # can be ordered by them or compared against `us` — see
        # _reparam_existing for what sorting the raw ones does to the chain.
        existing = _reparam_existing(raw, span, spans, basis_co, flat_co,
                                     max_dist=tol)
        existing = augment_existing_by_position(
            existing, span, basis_co, flat_co, orphans, tol=tol)
        covered = covered_intervals(existing, bedge_set)
        u_tol = (1.0 / L) if L > 0 else 0.0   # 1mm expressed as a parameter
        missing, groups = _divide_missing(
            us, existing, u_tol, covered, occupied, span, basis_co, flat_co)
        if not missing:
            continue
        jobs.append({
            "span": span,
            "target_side": "free",
            "existing": existing,
            "missing": missing,
            "groups": groups,
            "source_count": n,
            "source_max_offset": 0.0,
        })
    return jobs, ambiguous


def _drop_positions_on_boundary_edges(jobs, retopo, basis_co, flat_co,
                                      tol=0.0005):
    """Strip from every job the wanted parameters whose outline position lies
    ON an existing retopo boundary edge, strictly between its ends.

    A group is cut where a parameter is dropped, exactly as _divide_missing
    cuts at a blocker: the chain must not run an edge across the stretch the
    existing edge already covers. Jobs left with nothing are removed from the
    list in place. Returns the number of parameters dropped.
    """
    _bverts, bedges = collect_retopo_boundary(retopo)
    if not bedges or not jobs:
        return 0
    from mathutils import kdtree
    mat = retopo.matrix_world
    verts = retopo.data.vertices
    segs = [(mat @ verts[a].co, mat @ verts[b].co) for a, b in bedges]
    kd = kdtree.KDTree(len(segs))
    reach = tol
    for k, (a, b) in enumerate(segs):
        kd.insert((a + b) * 0.5, k)
        reach = max(reach, (b - a).length * 0.5 + tol)
    kd.balance()

    def _on_edge(p):
        for (_c, k, _d) in kd.find_range(p, reach):
            a, b = segs[k]
            ab = b - a
            d2 = ab.dot(ab)
            if d2 <= 1e-18:
                continue
            t = (p - a).dot(ab) / d2
            if not (0.0 < t < 1.0):
                continue
            if (p - a).length <= tol or (p - b).length <= tol:
                continue
            if (p - (a + ab * t)).length <= tol:
                return True
        return False

    dropped = 0
    kept_jobs = []
    for j in jobs:
        span = j["span"]
        groups = j.get("groups")
        if groups is None:
            groups = ([{"us": list(j["missing"]), "before": None, "after": None}]
                      if j["missing"] else [])
        new_groups, kept_us = [], []
        for g in groups:
            cur = None
            first_piece = True
            for u in g["us"]:
                p, _basis = span_point_at_u(span, u, basis_co, flat_co)
                if _on_edge(p):
                    dropped += 1
                    cur = None          # a drop ends the piece; no "after"
                    continue
                if cur is None:
                    # Only the piece that starts where the group started keeps
                    # the group's "before" blocker; a piece opened after a
                    # drop has nothing known on its low side.
                    cur = {"us": [], "after": None,
                           "before": g.get("before") if first_piece else None}
                    first_piece = False
                    new_groups.append(cur)
                cur["us"].append(u)
                kept_us.append(u)
            if cur is not None:
                # The group's last parameter survived, so the last piece still
                # ends where the group ended and may join its "after".
                cur["after"] = g.get("after")
        if kept_us:
            j["missing"] = sorted(kept_us)
            j["groups"] = new_groups
            kept_jobs.append(j)
    jobs[:] = kept_jobs
    return dropped


def run_generate_seam_chains(guide, retopo, flat_sk, count=8, spacing_mm=0.0,
                             nearest_to=None, min_seam_mm=5.0, tol=0.005,
                             match_distance=0.0, match_mm=1.0,
                             corner_angle_deg=0.0, symmetry_tolerance=0.015,
                             targets='BOTH', dry_run=True, progress=None,
                             straight_tol_mm=0.0):
    """Create retopo boundary on seams that have none yet.

    straight_tol_mm > 0 leaves straight stretches of the outline undivided
    (Spacing mode only; see anchor_segments.division_parameters). The number
    of division points that removed is returned as "straight_dropped".

    progress — optional callable taking a fraction 0..1, called at the phase
    boundaries (spans / symmetry / pairing / planning / applied) so the
    operator can drive Blender's progress cursor. Nothing else about the run
    depends on it.

    nearest_to — a flat-space point (typically the 3D cursor). When given,
    only the single nearest empty seam is generated, which is how this gets
    used in practice: one seam at a time while working a panel. Leave it None
    to do every empty seam at once.

    corner_angle_deg > 0 also pins the pattern's corners. They are NOT added
    to the anchor set — splitting a span changes the pairing, and a corner
    the two sides disagree about would break it — they are handed to the
    planners as forced division points instead. The span structure, and so
    everything the status report and the density tools read, is unchanged.

    symmetry_tolerance runs analysis.detect_island_symmetry to find any
    self-symmetric island's own fold axis, so a FREE run threading past it
    gets a forced division point there too (see find_axis_breaks /
    plan_free_edge_generation's folds_by_root). Pass the SAME value as the
    "Analyze Guide" panel's Symmetry Tolerance — a different default here
    would let Generate treat a panel as symmetric that Symmetrize (or the
    reverse) does not. Detection failing outright (no Flat SK, etc.) is not
    fatal to boundary generation, so it is swallowed rather than raised.
    """
    tick = progress if callable(progress) else (lambda f: None)
    tick(0.02)
    bvs, vns, parts = anch.find_vertex_partners(guide, match_distance=match_distance)
    _a, _b, basis_co = anch._collect_boundary(guide)
    flat_co = anch.get_flat_co(guide, flat_sk)
    corner_verts = anch.find_corner_anchors(
        bvs, vns, parts, flat_co, angle_deg=corner_angle_deg)
    anchors = anch.find_anchors(bvs, vns, parts)
    spans = anch.build_spans(vns, anchors)
    tick(0.15)

    try:
        _segs, _n_tested, _n_sym, folds = an.detect_island_symmetry(
            guide, flat_sk, tolerance=symmetry_tolerance)
    except Exception:
        folds = []
    tick(0.55)
    folds_by_root = {}
    for f in folds:
        folds_by_root.setdefault(f["root"], []).append(f)
    island_of = {}
    if folds_by_root:
        for root, idxs in an.compute_islands_cached(guide.data).items():
            for v in idxs:
                island_of[v] = root
    pairs, _unpaired = anch.pair_spans(spans, parts, basis_co)
    assign = map_retopo_to_spans(guide, retopo, spans, basis_co, flat_co, tol=tol)
    span_index = build_span_index(spans)

    rows, _summary = classify_seam_pairs(
        guide, retopo, flat_sk, match_distance=match_distance, tol=tol,
        match_mm=match_mm,
    )
    tick(0.75)

    # Join seams that continue into each other first, so a run shredded into
    # sub-millimetre pieces is divided once along its real length.
    rows = merge_seam_rows(rows)
    # Short seams are NOT dropped here: the planner gives them their two
    # anchors and nothing more. Dropping them left a hole at every one, and
    # the chains either side had nothing to weld to.
    empty = [r for r in rows if r["state"] == STATE_UNTOUCHED]
    too_short = sum(1 for r in rows
                    if r["state"] == STATE_UNTOUCHED and r["length_mm"] < min_seam_mm)

    # A seam with retopo on ONE side is not this planner's to divide: the
    # spacing does not get a say there, the partner does — the two sides have
    # to carry the same vertices or the seam cannot be sewn. It is still a
    # seam with a bare side, though, and leaving it out is what made "delete
    # the island that came out wrong and press Generate again" come back with
    # holes: deleting one island turns every seam it shares with a surviving
    # neighbour one-sided (measured on a production panel: 4 seams, 296mm,
    # none of it rebuilt). So they are planned here too, by mirroring the
    # authored side — the same plan Sync Retopo Seams makes, made in the same
    # pass so the user does not have to know which of the two buttons the
    # stretch in front of them needs.
    one_sided = [r for r in rows if r["state"] == STATE_ONE_SIDED]

    # Drop the un-dividable spans BEFORE choosing the nearest one, or aiming
    # the cursor at a zero-width slit would pick it and then generate nothing.
    usable, ambiguous = [], []
    for r in empty:
        ok = (span_is_unambiguous(r["span_a"], basis_co, flat_co)
              and span_is_unambiguous(r["span_b"], basis_co, flat_co))
        (usable if ok else ambiguous).append(r)
    empty = usable

    picked_distance = None
    if nearest_to is not None:
        # One seam at a time, and the nearest one may well be a one-sided
        # one — picking only among the empty ones would silently walk past it.
        cand = empty + one_sided
        if cand:
            cand.sort(key=lambda r: _seam_distance_to_point(r, nearest_to,
                                                            flat_co))
            picked = cand[0]
            picked_distance = _seam_distance_to_point(picked, nearest_to,
                                                      flat_co)
            empty = [r for r in empty if r is picked]
            one_sided = [r for r in one_sided if r is picked]

    from mathutils import kdtree
    bverts, bedge_set = collect_retopo_boundary(retopo)
    border = sorted(bverts)
    rmat = retopo.matrix_world
    rpos = {vi: rmat @ retopo.data.vertices[vi].co for vi in border}
    occupied_kd = kdtree.KDTree(len(border))
    for k, vi in enumerate(border):
        occupied_kd.insert(rpos[vi], k)
    occupied_kd.balance()
    # The boundary EDGES are what says "this stretch is already built" (see
    # covered_intervals) and the orphans are the vertices no span can speak
    # for (build_orphan_index). Both used to be thrown away here, which is
    # why a second pass at another density could build straight over the
    # first one.
    orphans = build_orphan_index(border, rpos, assign)
    occupied = (occupied_kd, border)

    jobs = []
    axis_mismatches = []
    gen_stats = {"straight_dropped": 0}
    if targets in ('BOTH', 'SEAMS'):
        seam_jobs, _skipped, axis_mismatches = plan_seam_generation(
            empty, spans, assign, span_index, basis_co, flat_co,
            occupied=occupied, bedge_set=bedge_set, orphans=orphans,
            tol=tol, count=count, spacing_mm=spacing_mm,
            min_seam_mm=min_seam_mm, match_mm=match_mm,
            corner_verts=corner_verts,
            folds_by_root=folds_by_root, island_of=island_of,
            straight_tol_mm=straight_tol_mm, stats=gen_stats,
        )
        jobs.extend(seam_jobs)

        # The one-sided ones, mirrored from whichever side is authored.
        inherit_pairs = [{"a": r["span_a"], "b": r["span_b"]}
                         for r in one_sided
                         if tuple(r["span_a"]) in span_index
                         and tuple(r["span_b"]) in span_index]
        if inherit_pairs:
            inherit_jobs, _sk = plan_seam_sync(
                inherit_pairs, spans, assign, span_index, basis_co, flat_co,
                occupied=occupied, match_mm=match_mm, bedge_set=bedge_set)
            jobs.extend(inherit_jobs)
        else:
            inherit_jobs = []
    else:
        empty = []
        one_sided = []
        inherit_jobs = []

    # Panels sewn to nothing carry no anchor, so build_spans yields nothing for
    # them and they would silently get no boundary at all.
    extra_runs = anch.anchorless_loop_runs(
        bvs, vns, anchors, corner_verts=corner_verts, basis_co=basis_co)

    free_jobs = []
    if targets in ('BOTH', 'FREE'):
        paired_idx = set()
        for pr in pairs:
            paired_idx.add(span_index[tuple(pr["a"])])
            paired_idx.add(span_index[tuple(pr["b"])])
        free_idx = [si for si in range(len(spans)) if si not in paired_idx]
        free_jobs, free_ambiguous = plan_free_edge_generation(
            spans, free_idx, assign, basis_co, flat_co, vns,
            occupied=occupied, bedge_set=bedge_set, orphans=orphans,
            tol=tol, count=count, spacing_mm=spacing_mm,
            min_seam_mm=min_seam_mm, corner_verts=corner_verts,
            extra_runs=extra_runs,
            folds_by_root=folds_by_root, island_of=island_of,
            straight_tol_mm=straight_tol_mm, stats=gen_stats,
        )
        jobs.extend(free_jobs)
        ambiguous = list(ambiguous) + list(free_ambiguous)

    # Generate never puts a vertex where a boundary edge already runs — on
    # any path, for any reason. The span-level test (covered_intervals) sees
    # only edges whose BOTH ends are filed under the same span; an edge that
    # crosses an anchor with no vertex there — a dart tip the user dissolved
    # into a chord, an outline simplified on purpose — is filed under no span
    # and sailed through, and the inherit path had no coverage test at all,
    # so a vertex the user had deliberately removed came back on the next
    # press. An edge there means somebody decided that stretch is done;
    # Generate is not the tool that overrules it (Match is, when pressed).
    dropped_on_edge = _drop_positions_on_boundary_edges(
        jobs, retopo, basis_co, flat_co)

    result = {
        "dropped_on_edge": dropped_on_edge,
        "empty_seams": len(empty),
        "inherited_seams": len(inherit_jobs),
        "free_edges": len(free_jobs),
        "too_short": too_short,
        "ambiguous": len(ambiguous),
        "jobs": len(jobs),
        "verts_total": sum(len(j["missing"]) for j in jobs),
        "picked_distance": picked_distance,
        "seam_lengths_mm": [r["length_mm"] for r in empty],
        "corners": len(corner_verts),
        "anchorless_runs": len(extra_runs),
        "fold_axes": len(folds),
        "axis_mismatches": len(axis_mismatches),
        "straight_dropped": gen_stats["straight_dropped"],
        "applied": None,
    }
    tick(0.9)
    if not dry_run and jobs:
        result["applied"] = apply_seam_sync(
            guide, retopo, jobs, basis_co, flat_co, occupied=occupied)
    tick(1.0)
    return result
