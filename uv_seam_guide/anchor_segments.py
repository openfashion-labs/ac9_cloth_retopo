"""Anchor-segment seam matching — read-only analysis.

Problem: the Guide mesh (CLO/MD source data) can have a sewn seam whose two
sides carry different vertex counts (particle distance is set per panel in
the CLO pattern editor, so a ribbed cuff and the sleeve it is sewn to can
disagree). Downstream retopo tooling treats "coincident in 3D" as "sewn",
so a mismatch there turns into manual count-matching and snapping by hand.

This module finds, for every sewn seam, the matching stretch of boundary on
each side and how many vertices each side carries. It is READ-ONLY — no mesh
is modified. Actually inserting the vertices is a separate, more carefully
gated pass; `resample_span` here computes the positions without writing them.

Design: anchors first, then whole spans
---------------------------------------
Anchors are the boundary points that are pinned in 3D by the sewing itself:
where three or more panels meet, and where a seam ends and a free edge (hem,
neckline) begins. These coincide exactly in 3D even when the two sides'
particle distance differs, which makes them reliable division points.

Each island's boundary loop is then cut at its anchors into spans, and two
spans are paired when BOTH of their end anchors are mutual partners. Crucially
the interior of a span is never required to pair up vertex-by-vertex — that is
what lets a density-mismatched seam (one side dense, the other sparse, no
interior coincidence at all) still be recognised as one seam with a count
mismatch, rather than being mistaken for an unsewn free edge.

An earlier version of this module walked both sides in lockstep, requiring
every interior vertex to have exactly one partner. It worked on uniform-density
data (measured: 48 runs covering 1992/2000 seam verts on a production mesh) but
by construction could not see a density mismatch at all, and broke into
"isolated" single points wherever a pinch cluster made the next step ambiguous.
"""

import math
from collections import defaultdict

import bmesh
from mathutils import kdtree


def _collect_boundary(guide_obj):
    """Return (boundary_verts, vert_neighbors, basis_co, flat_co).

    boundary_verts — vertex indices touching an open-boundary edge.
    vert_neighbors — dict[v] -> the (exactly 2) boundary-loop neighbours of v.
                     Vertices with a different count are irregular (chain end
                     or self-touching boundary) and are left out, so a walk
                     simply stops there.
    basis_co       — world-space 3D (Basis) position per vertex index.
    flat_co        — world-space flat-layout position per vertex index, or
                     None when the caller passed no flat ShapeKey name.
    """
    mesh = guide_obj.data
    matrix = guide_obj.matrix_world
    kb = mesh.shape_keys.key_blocks if mesh.shape_keys else None
    basis = kb["Basis"] if (kb is not None and "Basis" in kb) else None
    n = len(mesh.vertices)
    if basis is not None:
        basis_co = [matrix @ basis.data[i].co for i in range(n)]
    else:
        basis_co = [matrix @ mesh.vertices[i].co for i in range(n)]

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    neighbor_pairs = defaultdict(list)
    boundary_verts = set()
    for e in bm.edges:
        if not e.is_boundary:
            continue
        i1, i2 = e.verts[0].index, e.verts[1].index
        boundary_verts.add(i1)
        boundary_verts.add(i2)
        neighbor_pairs[i1].append(i2)
        neighbor_pairs[i2].append(i1)
    bm.free()

    vert_neighbors = {v: nb for v, nb in neighbor_pairs.items() if len(nb) == 2}
    return boundary_verts, vert_neighbors, basis_co


def get_flat_co(guide_obj, flat_sk_name):
    """World-space flat-layout position per vertex index."""
    mesh = guide_obj.data
    matrix = guide_obj.matrix_world
    if mesh.shape_keys is None or flat_sk_name not in mesh.shape_keys.key_blocks:
        raise RuntimeError(
            f"Flat ShapeKey '{flat_sk_name}' not found on guide '{guide_obj.name}'."
        )
    flat = mesh.shape_keys.key_blocks[flat_sk_name]
    return [matrix @ flat.data[i].co for i in range(len(mesh.vertices))]


def _vertex_islands(guide_obj):
    """Union-find over ALL mesh edges -> per-vertex UV island id (the root
    vertex index of its connected component). Same pattern as
    analysis.detect_island_symmetry.
    """
    mesh = guide_obj.data
    n = len(mesh.vertices)
    parent = list(range(n))

    def _find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for e in mesh.edges:
        ra, rb = _find(int(e.vertices[0])), _find(int(e.vertices[1]))
        if ra != rb:
            parent[ra] = rb
    return [_find(i) for i in range(n)]


def find_vertex_partners(guide_obj, match_distance=0.0, precision_3d=4):
    """Cluster boundary vertices by (near-)coincident Basis position.

    match_distance == 0 uses exact rounded-position grouping (the common
    split-mesh case, where sewn sides are bit-identical in 3D — this also
    covers a self-seam dart, whose two edges are duplicated from one
    simulated mesh and so are bit-identical too).

    match_distance > 0 uses KDTree proximity for layered seams whose 3D gap
    is small but nonzero (a pocket sewn onto a body panel), restricted to
    DIFFERENT-island candidates. Without that restriction, on a mesh whose
    own boundary edges are shorter than match_distance, a vertex's immediate
    same-side neighbours fall inside the radius and register as false
    partners — measured on a 78,986-vert production mesh at
    match_distance=0.005: every one of 3614 boundary verts read as a junction
    and zero runs were found.

    Returns (boundary_verts, vert_neighbors, partners) where partners maps
    v -> set of other boundary vertices at the same position (0 = free edge,
    1 = clean seam vertex, 2+ = a junction where 3+ panels meet).
    """
    boundary_verts, vert_neighbors, basis_co = _collect_boundary(guide_obj)
    partners = defaultdict(set)

    if match_distance and match_distance > 0.0:
        islands = _vertex_islands(guide_obj)
        verts = list(boundary_verts)
        kd = kdtree.KDTree(len(verts))
        for v in verts:
            kd.insert(basis_co[v], v)
        kd.balance()
        for v in verts:
            for _co, idx, _d in kd.find_range(basis_co[v], match_distance):
                if idx != v and islands[idx] != islands[v]:
                    partners[v].add(idx)
    else:
        def _key(co):
            return (round(co.x, precision_3d),
                    round(co.y, precision_3d),
                    round(co.z, precision_3d))

        groups = defaultdict(list)
        for v in boundary_verts:
            groups[_key(basis_co[v])].append(v)
        for group in groups.values():
            if len(group) < 2:
                continue
            for v in group:
                for w in group:
                    if w != v:
                        partners[v].add(w)

    return boundary_verts, vert_neighbors, dict(partners)


def find_anchors(boundary_verts, vert_neighbors, partners):
    """Boundary vertices that divide the loops into spans.

    An anchor marks a point where the CORRESPONDENCE CHANGES — where what
    this piece of boundary is sewn to stops being what the next piece is sewn
    to. Everything between two anchors can then be taken wholesale, without
    asking the interior to pair up vertex by vertex.

    A matched vertex is an anchor when either:
      (b) a boundary neighbour is unmatched — the seam ends here and a free
          edge (hem, neckline, opening) begins; or
      (c) some partner of this vertex does not carry over to the neighbour:
          it is neither a partner of the neighbour nor adjacent to one. A
          panel that starts, stops or swaps here fails that test.
    An irregular vertex (not exactly two boundary neighbours) is an anchor
    too, since a walk cannot pass through it.

    Both are pinned in 3D by the sewing itself, so they coincide exactly even
    when the two sides were built at different particle distance — which is
    what makes them safe division points.

    Why there is no "2+ partners means a junction" clause
    -----------------------------------------------------
    There used to be one, first and short-circuiting:

        if len(p) >= 2: anchors.add(v); continue

    It reads a vertex with several partners as a place where three or more
    panels pinch together. On real garment data that is wrong far more often
    than it is right, because sheets do not merely touch at a point — they
    run TOGETHER for the whole length of a seam. Measured on a production
    Guide (jacket, 3776 boundary vertices): at one seam FOUR boundary strands
    coincide, three of them from the same island, so every vertex along it
    reported three partners, every vertex became an anchor, and every single
    Guide edge became its own 3.3mm span. The requested 20mm spacing never
    got a say and the retopo simply inherited the Guide's own sampling.

    Dropping the clause and letting (c) decide, measured on that file:

        anchors 457 -> 197      spans under 5mm 331 -> 25
        median span 3.32mm -> 26.86mm      planned vertices 1239 -> 737
        length inside paired spans 3459mm -> 3424mm   (-1%, coverage kept)
        spans the flat outline folds back on: 0 both ways
        anchors whose partners are all non-anchors: 8 both ways

    A genuine junction is still caught, and by the rule that actually means
    it: where a third panel begins, its vertex is a partner here and has
    nothing to carry over to on the neighbour, so (c) fires.

    Two rejected variants, for the record — both measured worse. Also
    requiring len(partners(nb)) == len(partners(v)) leaves 381 anchors: the
    count flickers with sampling wherever the sheets differ in density, so it
    invents anchors at the beat frequency. Also requiring the partners' island
    set to match leaves 315 and costs paired coverage (3459 -> 3270mm).

    Caveat on (b): an unmatched neighbour is read as a free edge. That is
    correct only while a genuine seam always produces vertex coincidence. It
    is the one place a density mismatch could still be misread — deciding it
    properly needs vertex-to-EDGE proximity rather than vertex-to-vertex.
    """
    anchors = set()
    for v in boundary_verts:
        p = partners.get(v, ())
        if not p:
            continue
        nbs = vert_neighbors.get(v, ())
        if len(nbs) != 2:
            anchors.add(v)  # irregular boundary: a walk can't pass through
            continue
        for nb in nbs:
            pnb = partners.get(nb, ())
            if not pnb:
                anchors.add(v)          # (b)
                break
            # (c) every partner must continue into the neighbour's partners:
            # either it is one of them, or it is next to one. Testing each
            # partner separately is the point — a vertex sewn to three sheets
            # stays interior only while ALL three carry on.
            carried = set(pnb)
            if not all((({w} | set(vert_neighbors.get(w, ()))) & carried)
                       for w in p):
                anchors.add(v)          # (c)
                break
    return anchors


CORNER_ANGLE_DEFAULT = 45.0


def find_corner_anchors(boundary_verts, vert_neighbors, partners, flat_co,
                        angle_deg=CORNER_ANGLE_DEFAULT):
    """Boundary vertices where the outline turns a corner.

    `find_anchors` only knows about points the SEWING pins down. A pattern
    corner — the right angle at the bottom of a hem, the point of a collar —
    is pinned by nothing, so it lands in the middle of a span and the
    generator, which spreads its vertices evenly by arc length, walks
    straight past it. The corner is then cut off: measured on a production
    Guide, 20 free-edge vertices turn 30 degrees or more and not one of them
    carried an anchor.

    The angle is read in the FLAT layout, not in 3D. The pattern is drawn
    flat and a corner there stays a corner once sewn, whereas 3D adds the
    drape's own curvature and would report corners all along a fold.

    Two cases are admitted, and only two:

      free edge (no partner) — safe outright. A free span has no partner to
        stay in step with, so cutting it anywhere costs nothing.

      seam with exactly one partner, where that partner turns sharply too —
        both are added together. Span pairing needs BOTH end anchors to be
        mutual partners, so adding a corner to one side alone would leave
        the two sides with different division points and break the pair.
        A corner that only one side reports is left alone.

    A vertex with 2+ partners is already an anchor by the junction rule, so
    it is skipped here rather than counted twice.

    Returns a set of vertex indices. `angle_deg` is the turn away from
    straight: 0 is a straight line, 90 a right angle.
    """
    if flat_co is None or angle_deg <= 0.0:
        return set()

    def _turn(v):
        nbs = vert_neighbors.get(v)
        if not nbs or len(nbs) != 2:
            return None
        a = flat_co[nbs[0]] - flat_co[v]
        b = flat_co[nbs[1]] - flat_co[v]
        if a.length < 1e-9 or b.length < 1e-9:
            return None
        c = max(-1.0, min(1.0, a.normalized().dot(b.normalized())))
        return 180.0 - math.degrees(math.acos(c))

    sharp = {}
    for v in boundary_verts:
        t = _turn(v)
        if t is not None and t >= angle_deg:
            sharp[v] = t

    corners = set()
    for v in sharp:
        p = partners.get(v, ())
        if not p:
            corners.add(v)                      # free edge
        elif len(p) == 1:
            w = next(iter(p))
            if w in sharp:
                corners.add(v)                  # seam, both sides agree
                corners.add(w)
    return corners


def build_spans(vert_neighbors, anchors):
    """Cut every boundary loop at its anchors; return the resulting spans.

    A span is the ordered vertex list from one anchor to the next, inclusive
    of both. Each boundary edge is consumed once, so each span appears once.
    A loop carrying no anchor at all yields nothing (there is no division
    point on it, and nothing to pair it against).
    """
    spans = []
    used_edges = set()

    def _ekey(a, b):
        return (a, b) if a < b else (b, a)

    for a in sorted(anchors):
        for first in vert_neighbors.get(a, ()):
            if _ekey(a, first) in used_edges:
                continue
            span = [a]
            used_edges.add(_ekey(a, first))
            prev, cur = a, first
            while True:
                span.append(cur)
                if cur in anchors:
                    break
                nbs = vert_neighbors.get(cur)
                if nbs is None or len(nbs) != 2:
                    break
                nxt = nbs[0] if nbs[0] != prev else nbs[1]
                k = _ekey(cur, nxt)
                if k in used_edges:
                    break
                used_edges.add(k)
                prev, cur = cur, nxt
            if len(span) >= 2 and span[-1] in anchors:
                spans.append(span)
    return spans


def anchorless_loop_runs(boundary_verts, vert_neighbors, anchors,
                         corner_verts=(), basis_co=None):
    """Open runs covering the boundary loops that carry no anchor at all.

    build_spans cuts the boundary at its anchors, so a loop with none yields
    nothing, generation never sees it, and that panel ends up with no retopo
    boundary at all — it can never be filled. That is not a corner case: a
    panel sewn to nothing (a patch, an appliqué) has no coincident vertices
    anywhere, so it has no anchors by construction. Measured on a production
    Guide: 4 such loops, 909mm, needing about 46 vertices at 20mm spacing.

    The loop is handed back cut into OPEN runs, not as a closed one, so the
    rest of the pipeline works unchanged. A closed polyline breaks
    span_is_unambiguous: its round trip places a sample at u=1, reads the
    position back, and gets u=0 — the same point — so the whole loop would be
    rejected as folding back on itself.

    Cut at the pattern corners where there are any (they have to carry a
    vertex anyway, so cutting there costs nothing and puts the divisions where
    they belong); otherwise cut in half, by arc length when basis_co is given.
    """
    corners = set(corner_verts)
    runs = []
    seen = set()
    for start in sorted(boundary_verts):
        if start in seen:
            continue
        nbs = vert_neighbors.get(start)
        if not nbs or len(nbs) != 2:
            continue
        loop, prev, cur = [], None, start
        closed = False
        while True:
            loop.append(cur)
            seen.add(cur)
            nb = vert_neighbors.get(cur)
            if not nb or len(nb) != 2:
                break
            nxt = nb[0] if nb[0] != prev else nb[1]
            if nxt == start:
                closed = True
                break
            if nxt in seen:
                break
            prev, cur = cur, nxt
        if not closed or len(loop) < 4:
            continue
        if any(v in anchors for v in loop):
            continue

        cuts = [i for i, v in enumerate(loop) if v in corners]
        if len(cuts) < 2:
            if basis_co is not None:
                cum = _arc_lengths(loop + [loop[0]], basis_co)
                half = cum[-1] * 0.5
                mid = min(range(1, len(loop)), key=lambda i: abs(cum[i] - half))
            else:
                mid = len(loop) // 2
            cuts = [0, mid]
        for k in range(len(cuts)):
            a = cuts[k]
            b = cuts[(k + 1) % len(cuts)]
            run = (loop[a:b + 1] if a < b
                   else loop[a:] + loop[:b + 1])
            if len(run) >= 2:
                runs.append(run)
    return runs


def _arc_lengths(span, co):
    """Cumulative distance along the span's polyline, in `co` space."""
    acc = [0.0]
    for i in range(1, len(span)):
        acc.append(acc[-1] + (co[span[i]] - co[span[i - 1]]).length)
    return acc


def _point_at_arc(span, co, cumulative, target):
    """Position at arc length `target` along the span, plus which segment it
    fell on and how far into it. Returns (segment_index, t).
    """
    if cumulative[-1] <= 1e-12:
        return 0, 0.0
    target = min(max(target, 0.0), cumulative[-1])
    lo = 0
    for i in range(1, len(cumulative)):
        if cumulative[i] >= target:
            lo = i - 1
            break
    else:
        lo = len(cumulative) - 2
    seg = cumulative[lo + 1] - cumulative[lo]
    t = 0.0 if seg <= 1e-12 else (target - cumulative[lo]) / seg
    return lo, t


def division_parameters(span, basis_co, hard_verts=(), hard_us=(), count=8,
                        spacing_mm=0.0, min_seam_mm=5.0, merge_eps_mm=0.5):
    """Where to divide a span (or a merged run), as parameters 0..1 by 3D
    arc length.

    Vertices in `hard_verts` — pattern corners — always get a parameter of
    their own, and every stretch between two of them is then divided on its
    own account. Dividing the whole run in one go and merely adding the
    corners afterwards would leave a vertex sitting a fraction of a
    millimetre from a corner, which reads as a kink; splitting first keeps
    the spacing even inside each straight stretch instead.

    `hard_us` is the same idea for a break that has no existing vertex to
    anchor it to — a self-symmetry fold axis crossing the run at a point of
    its own (see retopo_seam_sync.find_axis_breaks). Given as raw 0..1
    parameters rather than vertex indices, merged into the same break list
    and sorted. A hard_u within `merge_eps_mm` of an existing break (an
    anchor, a corner, another hard_u) is dropped rather than added — that
    break's own vertex already sits close enough to serve as the fold-axis
    point, and forcing a second one that close on top of it would produce a
    near-zero-length stretch (the tiny "n=2" span min_seam_mm hands back).

    A stretch shorter than min_seam_mm gets its two ends and nothing more,
    the same rule the planners already apply to a whole short span.

    The endpoints 0.0 and 1.0 are always present.
    """
    cum = _arc_lengths(span, basis_co)
    total = cum[-1]
    if total <= 1e-12:
        return [0.0, 1.0]

    breaks = [0.0]
    if hard_verts:
        hard = set(hard_verts)
        for i in range(1, len(span) - 1):
            if span[i] in hard:
                u = cum[i] / total
                if u - breaks[-1] > 1e-6:
                    breaks.append(u)
    if hard_us:
        # Checked against the eventual endpoint 1.0 too, even though it is
        # not appended to `breaks` until below — a hard_u a fraction of a
        # millimetre short of the run's own end must be dropped the same
        # way one a fraction short of an interior break is.
        merge_eps_u = (merge_eps_mm / 1000.0) / total if total > 0 else 0.0
        for u in hard_us:
            u = min(max(float(u), 0.0), 1.0)
            if (1.0 - u) <= merge_eps_u or any(abs(u - b) <= merge_eps_u for b in breaks):
                continue
            breaks.append(u)
        breaks.sort()
    if 1.0 - breaks[-1] > 1e-6:
        breaks.append(1.0)
    else:
        breaks[-1] = 1.0

    us = []
    for k in range(len(breaks) - 1):
        u0, u1 = breaks[k], breaks[k + 1]
        seg_mm = (u1 - u0) * total * 1000.0
        if seg_mm < min_seam_mm:
            n = 2
        elif spacing_mm > 0.0:
            n = max(2, int(round(seg_mm / spacing_mm)) + 1)
        else:
            n = max(2, int(count))
        for i in range(n):
            us.append(u0 + (u1 - u0) * i / (n - 1))
    # the breaks are shared between neighbouring stretches
    out = []
    for u in us:
        if not out or u - out[-1] > 1e-9:
            out.append(u)
    return out


def span_midpoint(span, co):
    """The point halfway along the span by arc length (not the middle vertex —
    an unevenly sampled span's middle vertex can sit far from its centre).
    """
    cum = _arc_lengths(span, co)
    seg, t = _point_at_arc(span, co, cum, cum[-1] * 0.5)
    a, b = co[span[seg]], co[span[seg + 1]]
    return a.lerp(b, t)


def _point_to_polyline(p, span, co):
    """Shortest distance from p to the span's polyline."""
    best = None
    for i in range(len(span) - 1):
        a, b = co[span[i]], co[span[i + 1]]
        ab = b - a
        d2 = ab.dot(ab)
        if d2 <= 1e-18:
            q = a
        else:
            t = max(0.0, min(1.0, (p - a).dot(ab) / d2))
            q = a + ab * t
        d = (p - q).length
        if best is None or d < best:
            best = d
    return best if best is not None else float("inf")


def span_deviation(span_a, span_b, co):
    """How far apart two spans run — the larger of the two one-sided maximum
    vertex-to-polyline distances (a discrete Hausdorff distance).

    Deliberately NOT midpoint-to-midpoint: on a curved seam whose two sides
    are sampled at different densities, the coarse side's chords cut the
    corner, so the midpoints legitimately differ by the sagitta even though
    the spans are the same seam. Measuring every vertex against the other
    side's polyline stays small in exactly that case, while a free edge
    running off around the panel still measures large.
    """
    worst = 0.0
    for v in span_a:
        worst = max(worst, _point_to_polyline(co[v], span_b, co))
    for v in span_b:
        worst = max(worst, _point_to_polyline(co[v], span_a, co))
    return worst


def _max_segment(span, co):
    """Longest single edge in the span — the span's discretisation scale."""
    return max(
        (co[span[i + 1]] - co[span[i]]).length for i in range(len(span) - 1)
    ) if len(span) > 1 else 0.0


def _param_gap(span_a, span_b, co, samples=21):
    """Worst distance between the two spans' points at the SAME arc-length
    parameter — i.e. how badly "read the parameter off side a and use it on
    side b" would misplace a vertex.

    span_deviation asks whether the two spans trace the same CURVE, which is
    orientation-blind by construction (it is a Hausdorff distance). This asks
    the sharper question everything downstream actually depends on: does u
    mean the same thing on both sides. Two spans can trace the same curve
    perfectly and still have this come out at half the loop's diameter, if
    one of them is wound the other way round.
    """
    ca, cb = _arc_lengths(span_a, co), _arc_lengths(span_b, co)
    ta, tb = ca[-1], cb[-1]
    if ta <= 1e-12 or tb <= 1e-12:
        return 0.0
    worst = 0.0
    for k in range(samples):
        u = k / (samples - 1)
        sa, t = _point_at_arc(span_a, co, ca, u * ta)
        pa = co[span_a[sa]].lerp(co[span_a[sa + 1]], t)
        sb, t = _point_at_arc(span_b, co, cb, u * tb)
        pb = co[span_b[sb]].lerp(co[span_b[sb + 1]], t)
        worst = max(worst, (pa - pb).length)
    return worst


def pair_spans(spans, partners, basis_co, abs_tol=1e-4, rel_tol=0.5,
               length_tol=0.1):
    """Pair spans whose BOTH end anchors are mutual partners AND which run
    together in 3D.

    Sharing end anchors is not sufficient on its own: a panel's own outline
    is also divided at those same anchors, so the seam span and the free-edge
    span going the other way round the panel share both endpoints. What
    separates them is that the two sides of a real seam stay close along
    their whole length.

    A candidate has to clear two independent gates:

      1. span_deviation within max(abs_tol, rel_tol * longest segment of
         either side) — the two spans trace the same curve locally.
         Scaling by SEGMENT length rather than span length is the point:
         when one side of a curved seam is sampled coarsely its chords cut
         the corner, and that error is set by how long each chord is, not by
         how long the whole seam is. A circular arc's sagitta can never
         exceed a quarter of its chord, so rel_tol=0.5 admits any genuine
         discretisation error by construction. An earlier version scaled by
         span length and rejected correct pairs on tight curves (measured:
         deviation 1.14mm against a 0.79mm allowance at 150mm radius).

      2. 3D arc lengths agreeing within length_tol — the two spans are the
         same length. Gate 1 alone is not enough: loosening it far enough to
         admit coarse chords also lets a panel's own free edge through when
         that edge happens to run within a segment length of the seam
         (measured on the branch fixture: a 10-unit outline paired with an
         8-unit seam). Measuring in 3D keeps gathers safe — a gathered seam's
         two sides differ in the FLAT layout but occupy the same curve in 3D
         once sewn, so their 3D lengths still match.

    Raise abs_tol in step with `match_distance` when pairing layered seams
    that are close but not coincident.

    Where a junction offers more than one surviving candidate (three panels
    meeting share their anchors), the closest wins.

    Returns (pairs, unpaired) where each pair is
    {"a": span, "b": span, "deviation": float}, with "b" oriented to run in
    the same direction as "a" — by the end anchors where they settle it, and
    by _param_gap on a CLOSED seam, where they cannot (see below).
    """
    pairs = []
    used = set()
    for i, sa in enumerate(spans):
        if i in used:
            continue
        pa, qa = sa[0], sa[-1]
        seg_a = _max_segment(sa, basis_co)
        len_a = _arc_lengths(sa, basis_co)[-1]
        cands = []
        for j, sb in enumerate(spans):
            if j == i or j in used:
                continue
            pb, qb = sb[0], sb[-1]
            fwd = pb in partners.get(pa, ()) and qb in partners.get(qa, ())
            rev = qb in partners.get(pa, ()) and pb in partners.get(qa, ())
            if not (fwd or rev):
                continue
            len_b = _arc_lengths(sb, basis_co)[-1]
            longest = max(len_a, len_b)
            if longest > 0.0 and abs(len_a - len_b) / longest > length_tol:
                continue
            dev = span_deviation(sa, sb, basis_co)
            limit = max(abs_tol, rel_tol * max(seg_a, _max_segment(sb, basis_co)))
            if dev > limit:
                continue
            cands.append((dev, j, fwd, rev))
        if not cands:
            continue
        cands.sort(key=lambda c: c[0])
        dev, j, fwd, rev = cands[0]
        used.add(i)
        used.add(j)
        sb = list(reversed(spans[j])) if rev else spans[j]
        if fwd and rev:
            # A CLOSED seam — side a's two end anchors are the SAME point, so
            # both windings satisfy the end-anchor test and it cannot say
            # which way round side b runs. Picking `rev` there was a coin
            # flip, and getting it wrong is invisible under an even division
            # (the set {i/n} is its own mirror) but misplaces every forced
            # break — a pattern corner, a fold-axis crossing — by however far
            # apart the mirrored parameters sit. Measured on a production
            # Guide: 3 of 36 pairs were closed, and the two the coin flip got
            # wrong ran a parameter gap of 60.2mm and 60.0mm (half a 186mm
            # armhole loop's diameter) against 0.000mm the other way round.
            # Decide it by which winding actually corresponds, then.
            alt = list(reversed(sb))
            if _param_gap(sa, alt, basis_co) < _param_gap(sa, sb, basis_co):
                sb = alt
        pairs.append({"a": sa, "b": sb, "deviation": dev})
    unpaired = [s for k, s in enumerate(spans) if k not in used]
    return pairs, unpaired


def resample_span(span, basis_co, flat_co, target_count):
    """Positions for `target_count` points spread evenly along the span by
    3D (Basis) arc length. Computes only — nothing is written.

    Basis arc length, not flat: downstream retopo reads "coincident in 3D" as
    "sewn", so the inserted vertices have to line up in 3D. The two sides of a
    seam occupy the same curve in 3D once sewn even when their flat lengths
    differ (that difference IS the gathering/ease), so spacing by 3D arc
    length makes both sides land on the same points with no special case for
    gathers — whereas spacing by flat length would pull them apart exactly
    where a garment is gathered.

    Each result carries the segment and parameter it came from, so the flat
    position is read off the SAME segment at the SAME fraction and the new
    point stays exactly on the existing flat outline.

    Returns a list of dicts: {basis, flat, segment, t}. The first and last
    reproduce the span's anchors exactly.
    """
    if target_count < 2:
        raise ValueError("target_count must be at least 2")
    cum = _arc_lengths(span, basis_co)
    total = cum[-1]
    out = []
    for i in range(target_count):
        s = total * i / (target_count - 1)
        seg, t = _point_at_arc(span, basis_co, cum, s)
        v0, v1 = span[seg], span[seg + 1]
        entry = {
            "basis": basis_co[v0].lerp(basis_co[v1], t),
            "flat": flat_co[v0].lerp(flat_co[v1], t) if flat_co is not None else None,
            "segment": seg,
            "t": t,
        }
        out.append(entry)
    return out


def analyze_span_pairs(pairs, basis_co):
    """Per-pair counts, lengths and mismatch, worst mismatch first."""
    out = []
    for pr in pairs:
        sa, sb = pr["a"], pr["b"]
        la = _arc_lengths(sa, basis_co)[-1]
        lb = _arc_lengths(sb, basis_co)[-1]
        out.append({
            "side_a": sa,
            "side_b": sb,
            "count_a": len(sa),
            "count_b": len(sb),
            "mismatch": len(sa) != len(sb),
            "target_count": max(len(sa), len(sb)),
            "len_a": la,
            "len_b": lb,
            "deviation": pr["deviation"],
        })
    out.sort(key=lambda r: abs(r["count_a"] - r["count_b"]), reverse=True)
    return out
