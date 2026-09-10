"""Structured grid inside a four-sided patch, with the boundary left alone.

This is the first half of the final topology generator. The user marks the
corners the edge flow turns at (`topology_corner`), those cut a panel outline
into sides, and a four-sided patch gets a real grid: rows and columns that
follow the panel rather than the lozenge soup a triangulation produces.

WHY IT IS NOT A PLAIN GRID
--------------------------
A plain n x m grid needs opposite sides to carry the same number of vertices.
On the production file they almost never do, and the counts cannot be changed:
a boundary vertex is one half of a sewn pair, so adding or dropping one breaks
the seam. Measured on a production Guide, cutting each panel outline at its
corners:

    4-corner rings                     6 of 21
    ... with opposite sides equal      3   (and those are the smallest panels)
    biggest panel, cut at its 4 macro corners:
        left 13 = right 13, but bottom 34 vs top 14

The bottom is a zigzag hem — it needs those vertices to keep its shape. So the
count difference is a property of the pattern, not an error to fix.

WHAT IT DOES INSTEAD
--------------------
    * a Coons (transfinite) map of the four sides gives a structured core
      lattice strictly INSIDE the outline. Every cell there is a quad and the
      flow is clean
    * the core's perimeter is then stitched to the real outline side by side,
      matched on normalised arc length. A quad wherever both sides can advance
      together, a triangle where only one can. The number of triangles is
      exactly the count difference between the two, and they all sit in one
      band next to the boundary
    * the side curves are Laplacian-smoothed BEFORE they go into the map, so a
      zigzag hem does not print itself onto every interior row. The boundary
      vertices themselves are never moved — only the map's idea of the side is
      smoothed

Corners are taken as the user marked them. Exactly four means a patch; more
means the panel needs dividing first (that is the next stage, not this one) and
is reported rather than guessed at. Everything created is flagged in an int
layer so Clear removes exactly this and nothing the user made.
"""

import math
from collections import Counter, defaultdict

import bmesh
from mathutils import Vector

# A band fans when many boundary vertices on a long side stitch onto few
# core vertices on the short side it faces (stitch_band's "outer"-kind
# triangles all sharing one inner vertex). Confirmed on a production file:
# a lopsided region (short curved side, e.g. an armhole, next to a long
# straight one) collapsed the core to nu=2 and fanned onto a single vertex
# — visually obvious, but neither mesh.validate() nor a self-intersection
# test catches it, since every individual triangle stays valid. A normal
# core/corner vertex sees at most 6 faces (4 quads, or 2 quads + 2 tris at
# a corner); 7+ is what a human calls a fan.
MAX_BAND_VALENCE = 6


def _band_max_valence(faces):
    """Highest face-count at any single vertex across `faces` (vertex-tuple
    list). 0 for an empty list."""
    valence = Counter(v for f in faces for v in f)
    return max(valence.values()) if valence else 0


# --------------------------------------------------------- region containment
def _point_in_ring(x, y, ring_xy):
    """Even-odd point-in-polygon test against a plain (x, y) ring. No
    self-intersection repair — shapely's buffer(0) is still used when the
    (bundled as a wheel, see blender_manifest.toml) dependency is installed, see
    build_region_polygon; this is only the always-available fallback.
    """
    inside = False
    x1, y1 = ring_xy[-1]
    for x2, y2 in ring_xy:
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        x1, y1 = x2, y2
    return inside


class _RegionPolygon:
    """poly.contains_xy(x, y), shapely-backed when available (its buffer(0)
    repairs a self-intersecting outline) and a plain even-odd test
    otherwise — so the core-node containment check in grid_one_patch /
    grid_matched_patch runs unconditionally instead of silently skipping
    the region on a machine without shapely installed.
    """
    __slots__ = ("_shapely_poly", "_ring")

    def __init__(self, ring_xy):
        self._ring = ring_xy
        self._shapely_poly = None
        try:
            from shapely.geometry import Polygon
            p = Polygon(ring_xy)
            self._shapely_poly = p if p.is_valid else p.buffer(0)
        except Exception:
            self._shapely_poly = None

    def contains_xy(self, x, y):
        if self._shapely_poly is not None:
            try:
                from shapely.geometry import Point
                return self._shapely_poly.contains(Point(x, y))
            except Exception:
                pass
        return _point_in_ring(x, y, self._ring)


def build_region_polygon(ring_xy):
    """ring_xy: a sequence of (x, y) pairs. Always returns a usable
    poly.contains_xy(x, y) wrapper — never None — see _RegionPolygon."""
    return _RegionPolygon(ring_xy)

from . import face_orient as fo
from . import preview_fill as pf
from . import topology_corner as tc

# One name per DOMAIN. Blender requires attribute names to be unique across
# domains, and a bmesh that uses the same name for its vertex, edge and face
# layers loses all but one of them the moment the mesh is saved — measured on
# the user's file, which came back with ac9_patch_grid on POINT only and no way
# for Clear to find the faces and edges it had made.
GRID_VERT_LAYER = "ac9_grid_vert"
GRID_EDGE_LAYER = "ac9_grid_edge"
GRID_FACE_LAYER = "ac9_grid_face"


# ---------------------------------------------------------------- layers
def _vert_layer(bm, create=True):
    lay = bm.verts.layers.int.get(GRID_VERT_LAYER)
    if lay is None and create:
        lay = bm.verts.layers.int.new(GRID_VERT_LAYER)
    return lay


def _edge_layer(bm, create=True):
    lay = bm.edges.layers.int.get(GRID_EDGE_LAYER)
    if lay is None and create:
        lay = bm.edges.layers.int.new(GRID_EDGE_LAYER)
    return lay


def _face_layer(bm, create=True):
    lay = bm.faces.layers.int.get(GRID_FACE_LAYER)
    if lay is None and create:
        lay = bm.faces.layers.int.new(GRID_FACE_LAYER)
    return lay


def clear_grid(bm, within=None):
    """Remove exactly what a previous grid added. Returns (faces, verts).

    Same order as Preview Fill's Clear, and for the same reason: deleting a
    created vertex while its faces are still there takes the neighbouring
    boundary faces with it.

    `within` is an optional vertex predicate; when given, only geometry whose
    vertices satisfy it is touched (an island is one connected component, so
    testing one vertex of a face or edge is testing the island).
    """
    flay = _face_layer(bm, create=False)
    elay = _edge_layer(bm, create=False)
    vlay = _vert_layer(bm, create=False)
    ok = (lambda v: True) if within is None else within
    n_f = n_v = 0
    if flay is not None:
        dead = [f for f in bm.faces if f[flay] and ok(f.verts[0])]
        n_f = len(dead)
        if dead:
            bmesh.ops.delete(bm, geom=dead, context='FACES_ONLY')
    if vlay is not None:
        bm.verts.ensure_lookup_table()
        dead = [v for v in bm.verts if v[vlay] and ok(v)]
        n_v = len(dead)
        if dead:
            bmesh.ops.delete(bm, geom=dead, context='VERTS')
    if elay is not None:
        dead = [e for e in bm.edges if e[elay] and not e.link_faces
                and ok(e.verts[0])]
        if dead:
            bmesh.ops.delete(bm, geom=dead, context='EDGES')
    return n_f, n_v


# ------------------------------------------------------------- ring walking
def rings_from_edges(edges, pos):
    """Order a bag of (a, b) index pairs into rings/runs.

    Loose ends are walked first so an open run comes out as one run rather
    than as a ring with a tail. At a branch the walk keeps going as straight
    as it can, which is what a panel outline crossing another one wants.
    Returns [(vert index list, closed)].
    """
    adj = defaultdict(list)
    seen = set()
    for a, b in edges:
        k = (min(a, b), max(a, b))
        if k in seen:
            continue
        seen.add(k)
        adj[a].append(b)
        adj[b].append(a)

    used = set()

    def walk(start):
        seq = [start]
        prev, cur = None, start
        while True:
            nxts = [n for n in adj[cur]
                    if (min(cur, n), max(cur, n)) not in used]
            if prev is not None and len(nxts) > 1:
                d0 = pos[cur] - pos[prev]
                if d0.length > 1e-12:
                    d0 = d0.normalized()
                    nxts.sort(key=lambda n: -_safe_dir(pos[n] - pos[cur]).dot(d0))
            if not nxts:
                return seq, False
            n = nxts[0]
            used.add((min(cur, n), max(cur, n)))
            if n == start:
                return seq, True
            seq.append(n)
            prev, cur = cur, n

    out = []
    for v in sorted(adj, key=lambda v: (len(adj[v]) != 1, v)):
        if any((min(v, n), max(v, n)) not in used for n in adj[v]):
            out.append(walk(v))
    return out


def _safe_dir(v):
    return v.normalized() if v.length > 1e-12 else Vector((0.0, 0.0, 0.0))


def panel_edges(bm, island_of):
    """Boundary edges grouped by the Guide island (= panel) they belong to.

    An edge shared by two panels laid out touching is given to both, the same
    way Preview Fill does it: dropping it broke the ring on both.
    """
    by_island = defaultdict(list)
    for e in bm.edges:
        if len(e.link_faces) > 1:
            continue
        a, b = e.verts[0].index, e.verts[1].index
        ia, ib = island_of.get(a), island_of.get(b)
        if ia is None or ib is None:
            continue
        by_island[ia].append((a, b))
        if ib != ia:
            by_island[ib].append((a, b))
    return by_island


def sides_from_corners(ring, corner_set):
    """Cut a closed ring at the marked corners. Returns [side vertex lists].

    Each side includes both its end corners, so consecutive sides share a
    vertex and the four sides of a patch close up.
    """
    idx = [i for i, v in enumerate(ring) if v in corner_set]
    if len(idx) < 2:
        return []
    sides = []
    for k in range(len(idx)):
        a, b = idx[k], idx[(k + 1) % len(idx)]
        sides.append(ring[a:b + 1] if b > a else ring[a:] + ring[:b + 1])
    return sides


# ----------------------------------------------------------- the Coons core
def _arc_params(pts):
    """Normalised cumulative arc length along a polyline."""
    total = 0.0
    acc = [0.0]
    for i in range(len(pts) - 1):
        total += (pts[i + 1] - pts[i]).length
        acc.append(total)
    if total <= 1e-12:
        return [0.0] * len(pts), 0.0
    return [a / total for a in acc], total


def _sample(pts, params, t):
    """Point at normalised arc length t along a polyline."""
    if t <= 0.0:
        return pts[0].copy()
    if t >= 1.0:
        return pts[-1].copy()
    lo = 0
    hi = len(params) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if params[mid] <= t:
            lo = mid
        else:
            hi = mid
    span = params[lo + 1] - params[lo]
    f = 0.0 if span <= 1e-15 else (t - params[lo]) / span
    return pts[lo].lerp(pts[lo + 1], f)


def _smooth_curve(pts, passes=6, factor=0.5):
    """Laplacian smoothing with the ends pinned.

    Only the curve the MAP is built from is smoothed. The mesh's boundary
    vertices are untouched: a zigzag hem keeps every tooth, but the interior
    rows stop echoing it.
    """
    out = [p.copy() for p in pts]
    for _ in range(passes):
        nxt = [out[0]]
        for i in range(1, len(out) - 1):
            mid = (out[i - 1] + out[i + 1]) * 0.5
            nxt.append(out[i].lerp(mid, factor))
        nxt.append(out[-1])
        out = nxt
    return out


class CoonsPatch:
    """Transfinite map of four side polylines onto the unit square.

    Sides are given in ring order and re-read as
        C0(u): P00 -> P10     D1(v): P10 -> P11
        C1(u): P01 -> P11     D0(v): P00 -> P01
    """

    def __init__(self, sides_xy, smooth_passes=6):
        s0, s1, s2, s3 = sides_xy
        self.c0 = _smooth_curve(s0, passes=smooth_passes)
        self.d1 = _smooth_curve(s1, passes=smooth_passes)
        self.c1 = _smooth_curve(list(reversed(s2)), passes=smooth_passes)
        self.d0 = _smooth_curve(list(reversed(s3)), passes=smooth_passes)
        self.p_c0, self.l_c0 = _arc_params(self.c0)
        self.p_c1, self.l_c1 = _arc_params(self.c1)
        self.p_d0, self.l_d0 = _arc_params(self.d0)
        self.p_d1, self.l_d1 = _arc_params(self.d1)
        self.P00 = self.c0[0]
        self.P10 = self.c0[-1]
        self.P01 = self.c1[0]
        self.P11 = self.c1[-1]

    def at(self, u, v):
        a = _sample(self.c0, self.p_c0, u) * (1.0 - v)
        b = _sample(self.c1, self.p_c1, u) * v
        c = _sample(self.d0, self.p_d0, v) * (1.0 - u)
        d = _sample(self.d1, self.p_d1, v) * u
        corr = (self.P00 * (1.0 - u) * (1.0 - v) + self.P10 * u * (1.0 - v)
                + self.P01 * (1.0 - u) * v + self.P11 * u * v)
        return a + b + c + d - corr


def discrete_coons(c0, c1, d0, d1):
    """Bilinearly blended Coons patch over the four boundary vertex lists.

    c0[i], c1[i] for i in 0..n are the two opposite sides (bottom, top) and
    d0[j], d1[j] for j in 0..m the other two (left, right). Corners must agree:
    c0[0] == d0[0], c0[n] == d1[0], c1[0] == d0[m], c1[n] == d1[m].

    Indexed, NOT sampled by arc length: node (i, j) belongs to the row that
    starts at boundary vertex c0[i], so the row arrives exactly there. Sampling
    the side at i/n instead is what made every row skew by a different amount.
    """
    n = len(c0) - 1
    m = len(d0) - 1
    p00, p10, p01, p11 = c0[0], c0[n], c1[0], c1[n]
    out = {}
    for i in range(n + 1):
        u = i / n if n else 0.0
        for j in range(m + 1):
            v = j / m if m else 0.0
            out[(i, j)] = (c0[i] * (1.0 - v) + c1[i] * v
                           + d0[j] * (1.0 - u) + d1[j] * u
                           - (p00 * (1.0 - u) * (1.0 - v) + p10 * u * (1.0 - v)
                              + p01 * (1.0 - u) * v + p11 * u * v))
    return out, n, m


def relax_interior(node, n, m, passes=40):
    """Laplacian relaxation with the boundary pinned.

    A transfinite blend carries the boundary's own unevenness inwards; relaxing
    the interior against its four neighbours spreads it out and is what makes a
    hand-built grid look regular. In place, Gauss-Seidel, so it converges in a
    few dozen passes on the sizes involved here.
    """
    if n < 2 or m < 2:
        return node
    for _ in range(passes):
        for i in range(1, n):
            for j in range(1, m):
                node[(i, j)] = (node[(i - 1, j)] + node[(i + 1, j)]
                                + node[(i, j - 1)] + node[(i, j + 1)]) * 0.25
    return node


def resample_side(pts, count):
    """`count` + 1 points spread evenly along a polyline by arc length."""
    params, _total = _arc_params(pts)
    return [_sample(pts, params, k / count if count else 0.0)
            for k in range(count + 1)]


# ---------------------------------------------------------- band stitching
def match_runs(po, pi, tri_penalty=10.0, end_bias=4.0):
    """Rungs joining two polylines given as normalised arc-length lists.

    Returns the list of steps ("both" / "outer" / "inner") from one end to the
    other. "both" makes a quad, the others a triangle.

    A greedy walk was tried first and measured worse than it needs to be: on
    the biggest panel it produced 48 triangles where the count difference only
    forces 16, because once the two parameter sequences drift out of step the
    walk keeps advancing one side at a time. This is a small dynamic program
    instead — the triangle penalty dominates the alignment term, so it takes
    the fewest triangles possible and only then lines the rungs up.
    """
    m, q = len(po) - 1, len(pi) - 1
    INF = float("inf")

    def rung(i, j):
        return abs(po[min(i, m)] - pi[min(j, q)])

    def tri_cost(t):
        """A triangle costs more the closer it is to an end of the side.

        The ends of a side ARE the corners of the patch, and a triangle there
        is what the user pointed at: a right angle should be turned by a quad.
        Parameter drift is worst at the ends, so without this the matcher put
        them there by default. The weight tapers to 1 at mid-side.
        """
        d = min(t, 1.0 - t)
        return tri_penalty * (1.0 + end_bias * max(0.0, 1.0 - d / 0.25))

    dp = [[INF] * (q + 1) for _ in range(m + 1)]
    back = [[None] * (q + 1) for _ in range(m + 1)]
    dp[0][0] = 0.0
    for i in range(m + 1):
        for j in range(q + 1):
            cur = dp[i][j]
            if cur == INF:
                continue
            if i < m and j < q:
                c = cur + rung(i + 1, j + 1)
                if c < dp[i + 1][j + 1]:
                    dp[i + 1][j + 1] = c
                    back[i + 1][j + 1] = (i, j, "both")
            if i < m:
                c = cur + tri_cost(po[min(i + 1, m)]) + rung(i + 1, j)
                if c < dp[i + 1][j]:
                    dp[i + 1][j] = c
                    back[i + 1][j] = (i, j, "outer")
            if j < q:
                c = cur + tri_cost(pi[min(j + 1, q)]) + rung(i, j + 1)
                if c < dp[i][j + 1]:
                    dp[i][j + 1] = c
                    back[i][j + 1] = (i, j, "inner")

    steps = []
    i, j = m, q
    while (i, j) != (0, 0):
        pi_, pj, kind = back[i][j]
        steps.append(kind)
        i, j = pi_, pj
    steps.reverse()
    return steps


def stitch_band(outer, inner):
    """Faces joining two matched runs of (position, vertex) pairs.

    Both runs start and end at the same pair of corners. The count difference
    between them — 34 vertices along a zigzag hem against a 13-column core —
    turns into that many triangles, all of them in this band and none in the
    interior.
    """
    po, _lo = _arc_params([p for p, _ in outer])
    pi, _li = _arc_params([p for p, _ in inner])
    faces = []
    i = j = 0
    for kind in match_runs(po, pi):
        if kind == "both":
            faces.append((outer[i][1], outer[i + 1][1],
                          inner[j + 1][1], inner[j][1]))
            i += 1
            j += 1
        elif kind == "outer":
            faces.append((outer[i][1], outer[i + 1][1], inner[j][1]))
            i += 1
        else:
            faces.append((outer[i][1], inner[j + 1][1], inner[j][1]))
            j += 1
    return faces


# ------------------------------------------------------------------- filling
def grid_one_patch(bm, sides, W, vref, inv, target_mm, vlay, poly=None,
                   smooth_passes=6, cells_from='COUNTS', relax_passes=40):
    """Grid a single four-sided patch. Returns a per-patch report.

    `sides` are the four boundary vertex runs in ring order, `W` maps a vertex
    index to its world position, `inv` takes a world position back to local.
    `vlay` is the int layer new vertices are flagged in, and it is a PARAMETER
    on purpose: creating a custom-data layer invalidates every BMVert already
    held, so it cannot be created down here where `vref` and `sides` are
    already in hand. A synthetic test caught exactly that — the returned faces
    came back holding dead references.
    `poly` is a shapely polygon of the region when available — a core node
    outside it means the map has folded, which happens on a concave outline,
    and the patch is refused rather than filled with crossing faces.
    """
    rep = {"sides": [len(s) - 1 for s in sides], "quads": 0, "tris": 0,
           "verts": 0, "status": "ok", "kind": "core"}
    if len(sides) != 4:
        rep["status"] = "not four sides"
        return rep, []

    sides_xy = [[W[v] for v in s] for s in sides]
    lu = 0.5 * (_arc_params(sides_xy[0])[1] + _arc_params(sides_xy[2])[1])
    lv = 0.5 * (_arc_params(sides_xy[1])[1] + _arc_params(sides_xy[3])[1])
    target = target_mm / 1000.0
    # Two measurements disagree here, and the later one is the one that
    # counts. An early test (before the matched path and the valence gate
    # existed) had COUNTS at 61.2% quads against 75.8% for SPACING. Re-run on
    # a production file with the gate in place: SPACING sizes the core from
    # raw length, so a lopsided region gets a core far narrower than its
    # boundary count and the gate refuses every one of them (15 of 15
    # mismatched regions, 0 banded); COUNTS gridded 8 of those 15 at 97.3%
    # quads, 0 self-intersections, but with one 12-degree sliver corner.
    # Looked at on 2026-09-02 (production file, 38 regions): COUNTS filled 36
    # against 28 for SPACING, 97% quads, 0 self-intersections, min corner
    # 17 degrees; the mismatch shows as one sheared row plus a pair of
    # triangles per odd vertex, judged usable. COUNTS is the default since.
    # SPACING is kept for the record only.
    if cells_from == 'COUNTS':
        # Follow the boundary. Opposite sides of a patch rarely carry the same
        # number of vertices, but their average is the resolution that costs
        # the fewest triangles, and it makes the matching case exact. Sizing
        # the core from the spacing alone left the difference to the band,
        # which is what the long triangle fans in a dense region were.
        nu = max(1, int(round((len(sides[0]) - 1 + len(sides[2]) - 1) / 2.0)))
        nv = max(1, int(round((len(sides[1]) - 1 + len(sides[3]) - 1) / 2.0)))
    else:
        nu = max(1, int(round(lu / target)))
        nv = max(1, int(round(lv / target)))
    # Never finer than the boundary it has to be stitched to. A core of five
    # columns inside a region whose sides carry one edge cannot be joined
    # without a fan of triangles, and that fan is what showed up as a star at
    # the corners (measured: an 8-vertex region came out with 28 faces).
    nu = max(1, min(nu, max(len(sides[0]) - 1, len(sides[2]) - 1)))
    nv = max(1, min(nv, max(len(sides[1]) - 1, len(sides[3]) - 1)))
    rep["cells"] = (nu, nv)
    rep["cells_from"] = cells_from
    rep["side_mm"] = (round(lu * 1000, 1), round(lv * 1000, 1))

    # A cap side carrying more than one edge needs something to land on, so
    # give that direction a column even if the spacing says one cell.
    if nu <= 1 and (len(sides[0]) > 2 or len(sides[2]) > 2):
        nu = 2
    if nv <= 1 and (len(sides[1]) > 2 or len(sides[3]) > 2):
        nv = 2
    rep["cells"] = (nu, nv)

    if nu <= 1 or nv <= 1:
        # A placket, a waistband, a strip of piping: one cell across, so there
        # is no interior. Ladder the two long sides straight to each other with
        # the same matcher — no new vertices at all.
        if nu <= 1 and nv <= 1:
            rep["status"] = "one cell in both directions"
            return rep, []
        rep["kind"] = "ladder"
        if nv <= 1:
            outer = [(W[v], vref[v]) for v in sides[0]]
            inner = [(W[v], vref[v]) for v in reversed(sides[2])]
        else:
            outer = [(W[v], vref[v]) for v in reversed(sides[3])]
            inner = [(W[v], vref[v]) for v in sides[1]]
        band_faces = stitch_band(outer, inner)
        worst = _band_max_valence(band_faces)
        rep["max_valence"] = worst
        if worst > MAX_BAND_VALENCE:
            rep["status"] = ("the band would fan %d faces onto one vertex — "
                             "cut it again" % worst)
            return rep, []
        return rep, band_faces

    # Same construction as the matched case, over sides resampled to the core
    # resolution: blend by index, then relax. The core is inset by one cell, so
    # its own border comes from the (0/nu, j/nv) ring of this lattice.
    patch = CoonsPatch(sides_xy, smooth_passes=smooth_passes)
    c0 = resample_side(sides_xy[0], nu)
    d1 = resample_side(sides_xy[1], nv)
    c1 = resample_side(list(reversed(sides_xy[2])), nu)
    d0 = resample_side(list(reversed(sides_xy[3])), nv)
    pos, _n, _m = discrete_coons(c0, c1, d0, d1)
    relax_interior(pos, nu, nv, passes=relax_passes)

    node = {}
    outside = 0
    for i in range(1, nu):
        for j in range(1, nv):
            p = pos[(i, j)]
            if poly is not None and not poly.contains_xy(p.x, p.y):
                outside += 1
            nv_ = bm.verts.new(inv @ Vector((p.x, p.y, 0.0)))
            nv_[vlay] = 1
            node[(i, j)] = nv_
    rep["core_outside"] = outside
    if outside:
        # Undo: a folded map cannot be salvaged by nudging.
        bmesh.ops.delete(bm, geom=list(node.values()), context='VERTS')
        # The map folded: the region is too far from a quadrilateral for one
        # patch, whatever four corners are chosen. Another cut line through it
        # is the answer, so say that rather than reporting the symptom.
        rep["status"] = ("the grid folds out of this region (%d of %d interior "
                         "nodes land outside) — cut it again"
                         % (outside, (nu - 1) * (nv - 1)))
        return rep, []
    rep["verts"] = len(node)

    faces = []
    for i in range(1, nu - 1):
        for j in range(1, nv - 1):
            faces.append((node[(i, j)], node[(i + 1, j)],
                          node[(i + 1, j + 1)], node[(i, j + 1)]))

    # The core's perimeter, in the same rotation as the outline's sides.
    inner_runs = [
        [(pos[(i, 1)], node[(i, 1)]) for i in range(1, nu)],
        [(pos[(nu - 1, j)], node[(nu - 1, j)]) for j in range(1, nv)],
        [(pos[(i, nv - 1)], node[(i, nv - 1)]) for i in range(nu - 1, 0, -1)],
        [(pos[(1, j)], node[(1, j)]) for j in range(nv - 1, 0, -1)],
    ]
    # How the band meets a corner decides whether the ideal case comes out
    # clean. A true n x m grid has a QUAD at each corner — the outline corner,
    # one outline vertex along each of the two sides, and the core's corner
    # node — and no rung along the diagonal. Running the corner as part of the
    # side band instead puts that diagonal in and leaves each side one vertex
    # out of step, which showed up as four extra triangles on a patch whose
    # counts matched perfectly. So the corner cells are laid first and each
    # side band then joins the outline BETWEEN its corners, where the ideal
    # relation is exactly (side edges - 2) == (core run edges).
    corner_cells = all(len(s_) - 1 >= 2 for s_ in sides)
    rep["corner_cells"] = corner_cells
    if corner_cells:
        for k in range(4):
            faces.append((vref[sides[k][0]], vref[sides[k][1]],
                          inner_runs[k][0][1], vref[sides[k - 1][-2]]))
        for k in range(4):
            outer = [(W[v], vref[v]) for v in sides[k][1:-1]]
            faces += stitch_band(outer, inner_runs[k])
    else:
        # A side that is a single edge has no room for a corner cell: both of
        # its ends would claim that one edge and the boundary edge would end
        # up with two faces. Fall back to rungs from the corners themselves.
        for k in range(4):
            outer = [(W[v], vref[v]) for v in sides[k]]
            faces += stitch_band(outer, inner_runs[k])

    worst = _band_max_valence(faces)
    rep["max_valence"] = worst
    if worst > MAX_BAND_VALENCE:
        bmesh.ops.delete(bm, geom=list(node.values()), context='VERTS')
        rep["status"] = ("the band would fan %d faces onto one vertex — cut "
                         "it again" % worst)
        return rep, []

    return rep, faces


STRAIGHT_SIDE_TOL = 0.02   # chord deviation / chord length
STRIP_ROWS = 3             # a region this thin across is a strip
HANG_FRAC = 0.3            # an unpaired column this close to a neighbour hangs on it
STRIP_TAPER = 1.5          # long sides differing in length by more than this: not a strip


def _side_straightness(pts):
    """Largest deviation of a polyline from its chord, over the chord length."""
    a, b = pts[0], pts[-1]
    d = b - a
    l2 = d.length_squared
    if l2 < 1e-18:
        return 0.0
    worst = 0.0
    for p in pts[1:-1]:
        t = (p - a).dot(d) / l2
        worst = max(worst, ((a + d * t) - p).length)
    return worst / math.sqrt(l2)


def relax_passes_for(sides_xy, n, m, default=40):
    """How many relaxation passes a matched patch should get.

    None on a strip or on straight sides. Measured on a production file
    (2026-09-02): the 35x6 waistband, four straight sides, came out with its
    interior lines 0.00 mm off the chord and 0.1 degrees off square when the
    boundary vertices were simply joined to their opposite partners, and
    2.04 mm / 7.4 degrees off after 40 Laplacian passes — the relaxation
    was printing the boundary's slightly uneven spacing as a wobble down
    every column. On the curved body regions of the same file it bought
    0.2-0.5 mm of chord deviation and cost 1-3 degrees of angle, so it is
    kept only where both n and m are large and a side actually curves.
    """
    if min(n, m) <= STRIP_ROWS:
        return 0
    if all(_side_straightness(s) < STRAIGHT_SIDE_TOL for s in sides_xy):
        return 0
    return default


def strip_orientation(counts):
    """Rotation that puts a strip's matching short sides at 1 and 3, or None.

    A strip is a region where ONE pair of opposite sides carries the same
    count (the short way across) and the other pair does not (along the
    strip). Both pairs equal is the matched patch; both unequal is the band.
    """
    if counts[1] == counts[3] and counts[0] != counts[2] and counts[1] >= 2:
        return 0
    if counts[0] == counts[2] and counts[1] != counts[3] and counts[0] >= 2:
        return 1
    return None


def _stitch_columns(cols, node, outer_row, inner_row, which):
    """Faces between a real boundary row and the first interior row.

    `cols[k][which]` is the boundary vertex index at column k, or None where
    the column comes from the other side only. Every column with a boundary
    vertex closes a quad; the columns between two such carry the extra
    interior nodes, and each one costs a triangle fanned from the last real
    vertex — the same shape stitch_band makes, but with the columns fixed
    beforehand so every rung is straight.
    """
    faces = []
    last = 0
    for k in range(1, len(cols)):
        if cols[k][which] is None:
            continue
        if k == last + 1:
            faces.append((node[(last, outer_row)], node[(k, outer_row)],
                          node[(k, inner_row)], node[(last, inner_row)]))
        else:
            # Several interior columns between two boundary vertices: the
            # quad goes on the segment nearest the middle and the rest fan
            # from whichever boundary vertex is nearer. Anchoring every
            # triangle on the left vertex instead made the result depend on
            # the ring's direction (measured: 1 or 3 triangles for the same
            # collar, run to run) and split a hanging column's quad in two.
            mid = 0.5 * (cols[last][0] + cols[k][0])
            j = min(range(last, k),
                    key=lambda c: abs(0.5 * (cols[c][0] + cols[c + 1][0]) - mid))
            for c in range(last, j):
                faces.append((node[(last, outer_row)], node[(c + 1, inner_row)],
                              node[(c, inner_row)]))
            faces.append((node[(last, outer_row)], node[(k, outer_row)],
                          node[(j + 1, inner_row)], node[(j, inner_row)]))
            for c in range(j + 1, k):
                faces.append((node[(k, outer_row)], node[(c + 1, inner_row)],
                              node[(c, inner_row)]))
        last = k
    return faces


def _dedupe_ring(vs):
    """Drop consecutive repeats (wrapping) from a vertex ring; None if fewer
    than three distinct vertices remain."""
    out = []
    for v in vs:
        if not out or out[-1] is not v:
            out.append(v)
    while len(out) > 1 and out[0] is out[-1]:
        out.pop()
    if len(out) < 3 or len(set(out)) != len(out):
        return None
    return tuple(out)


def grid_strip_patch(bm, sides, W, vref, inv, vlay, poly=None):
    """A region whose short sides match but whose long sides do not.

    A waistband, a collar, a cuff: what a hand-built one looks like is a set
    of straight rungs, each running from a boundary vertex on one long side
    to the vertex opposite it, with a single triangle wherever one side has a
    vertex the other lacks. The band construction cannot give that: its core
    is resampled evenly along the sides, so no rung lands on a real boundary
    vertex and every rung leans (the collar in the user's screenshot).

    Here the columns ARE the boundary vertices. The two long sides are paired
    by normalised arc length with the same fewest-triangles matcher the band
    uses; a paired column has a vertex at both ends, an unpaired one has a
    vertex at one end and an interpolated point on the other side. Interior
    rows sit on each column at the fraction the two short sides' own vertices
    dictate (blended across), so every column is a straight segment and every
    row arrives exactly at a short-side vertex. Triangles appear only in the
    two boundary rows, one per unpaired vertex, and nowhere else.
    """
    rep = {"sides": [len(s) - 1 for s in sides], "quads": 0, "tris": 0,
           "verts": 0, "status": "ok", "kind": "strip"}
    k = strip_orientation(rep["sides"])
    if k is None:
        rep["status"] = "not a strip"
        return rep, []
    sides = sides[k:] + sides[:k]
    m = len(sides[1]) - 1
    bottom = sides[0]
    right = sides[1]
    top = list(reversed(sides[2]))
    left = list(reversed(sides[3]))
    bpts = [W[v] for v in bottom]
    tpts = [W[v] for v in top]
    pb, lb = _arc_params(bpts)
    pt, lt = _arc_params(tpts)
    pl, _ = _arc_params([W[v] for v in left])
    pr, _ = _arc_params([W[v] for v in right])
    # A wedge is not a strip: rungs from the long side converge on the short
    # one and the fan triangles there come out as slivers (measured: 8.9
    # degrees where the sides were 3 against 6 edges and the short side a
    # quarter the length). The band construction handles the taper.
    if lb <= 1e-12 or lt <= 1e-12 or max(lb, lt) / min(lb, lt) > STRIP_TAPER:
        rep["status"] = "not a strip"
        return rep, []

    cols = [(0.0, 0, 0)]              # (param, bottom index, top index)
    i = j = 0
    for kind in match_runs(pb, pt):
        if kind == "both":
            i += 1
            j += 1
            cols.append((0.5 * (pb[i] + pt[j]), i, j))
        elif kind == "outer":
            i += 1
            cols.append((pb[i], i, None))
        else:
            j += 1
            cols.append((pt[j], None, j))
    # The far corners are two real vertices at parameter 1 on both sides; if
    # the matcher reached them one side at a time, fold those columns into
    # the single corner column they describe.
    bi = tj = None
    while cols and cols[-1][0] >= 1.0 - 1e-9:
        _p, b_, t_ = cols.pop()
        bi = b_ if b_ is not None else bi
        tj = t_ if t_ is not None else tj
    cols.append((1.0, len(bottom) - 1 if bi is None else bi,
                 len(top) - 1 if tj is None else tj))
    K = len(cols)
    rep["cells"] = (K - 1, m)

    # An unpaired vertex that sits almost on top of a neighbouring column
    # would get a column of its own a hair's breadth away, and the fan
    # triangle between the two would be a sliver (measured: 8.7 degrees,
    # legs 11.7 mm on a 1.8 mm base). Such a vertex hangs on the neighbour
    # instead: its interior nodes are the neighbour's, so the thin quad and
    # the sliver collapse (deduplicated below) into one healthy triangle
    # whose base is the real boundary edge.
    # The test is made on the side the column LACKS a vertex on, against the
    # real vertex parameters there: a paired column's rung may lean (its two
    # ends sit at different parameters), so its bottom end can coincide with
    # an unpaired column's interpolated bottom point even when the columns'
    # mean parameters look well apart (measured: 0.251 against 0.333, and
    # the two nodes 1.2 mm apart).
    sb = [pb[b_] if b_ is not None else s_ for s_, b_, t_ in cols]
    st = [pt[t_] if t_ is not None else s_ for s_, b_, t_ in cols]
    alias = {}
    for kc in range(1, K - 1):
        _s, b_, t_ = cols[kc]
        if b_ is not None and t_ is not None:
            continue
        lacks_bottom = b_ is None
        par = sb if lacks_bottom else st
        has = (lambda c: cols[c][1] is not None) if lacks_bottom else               (lambda c: cols[c][2] is not None)
        p = kc - 1
        while p > 0 and not has(p):
            p -= 1
        q = kc + 1
        while q < K - 1 and not has(q):
            q += 1
        gp = par[kc] - par[p]
        gq = par[q] - par[kc]
        span = max(par[q] - par[p], 1e-12)
        if gp <= gq and p == kc - 1 and gp < HANG_FRAC * span:
            alias[kc] = p
        elif gq < gp and q == kc + 1 and gq < HANG_FRAC * span:
            alias[kc] = q
    # follow chains (a hanger next to a hanger) to a real column
    for kc in list(alias):
        seen = set()
        tgt = alias[kc]
        while tgt in alias and tgt not in seen:
            seen.add(tgt)
            tgt = alias[tgt]
        alias[kc] = tgt
    rep["hanging"] = len(alias)
    rep["cols"] = [(round(s_, 3), b_ is not None, t_ is not None, kc in alias)
                   for kc, (s_, b_, t_) in enumerate(cols)]

    node = {}
    for kc, (_s, b_, t_) in enumerate(cols):
        if b_ is not None:
            node[(kc, 0)] = vref[bottom[b_]]
        if t_ is not None:
            node[(kc, m)] = vref[top[t_]]
    for jr in range(m + 1):
        node[(0, jr)] = vref[left[jr]]
        node[(K - 1, jr)] = vref[right[jr]]

    made = []
    outside = 0
    for kc in range(1, K - 1):
        if kc in alias:
            continue
        s, b_, t_ = cols[kc]
        B = bpts[b_] if b_ is not None else _sample(bpts, pb, s)
        T = tpts[t_] if t_ is not None else _sample(tpts, pt, s)
        for jr in range(1, m):
            t = (1.0 - s) * pl[jr] + s * pr[jr]
            p = B + (T - B) * t
            if poly is not None and not poly.contains_xy(p.x, p.y):
                outside += 1
            nv = bm.verts.new(inv @ Vector((p.x, p.y, 0.0)))
            nv[vlay] = 1
            node[(kc, jr)] = nv
            made.append(nv)
    rep["core_outside"] = outside
    if outside:
        bmesh.ops.delete(bm, geom=made, context='VERTS')
        rep["status"] = ("the rungs fold out of this region (%d of %d interior "
                         "nodes land outside) — cut it again"
                         % (outside, max(1, (K - 2) * (m - 1))))
        return rep, []
    rep["verts"] = len(made)
    for kc, tgt in alias.items():
        for jr in range(1, m):
            node[(kc, jr)] = node[(tgt, jr)]

    faces = []
    for jr in range(1, m - 1):
        for kc in range(K - 1):
            faces.append((node[(kc, jr)], node[(kc + 1, jr)],
                          node[(kc + 1, jr + 1)], node[(kc, jr + 1)]))
    faces += _stitch_columns(cols, node, 0, 1, 1)
    faces += _stitch_columns(cols, node, m, m - 1, 2)
    faces = [f for f in (_dedupe_ring(f) for f in faces) if f is not None]

    worst = _band_max_valence(faces)
    rep["max_valence"] = worst
    if worst > MAX_BAND_VALENCE:
        bmesh.ops.delete(bm, geom=made, context='VERTS')
        rep["status"] = ("the rungs would fan %d faces onto one vertex — cut "
                         "it again" % worst)
        return rep, []
    return rep, faces


def grid_matched_patch(bm, sides, W, vref, inv, vlay, poly=None,
                      smooth_passes=6, relax_passes=40):
    """A patch whose opposite sides carry the same count: a pure grid.

    No band and no triangles. Every row runs from a real boundary vertex on one
    side straight through to its partner on the opposite side, which is what
    the boundary band was only ever approximating: the triangles in the banded
    version are entirely the cost of the core being inset by one cell and
    stitched back on. Here the grid's own border IS the outline.

    Requires len(side0) == len(side2) and len(side1) == len(side3), which is
    what the interval assignment in `panel_regions` arranges by choosing how
    finely each cut line is divided — a cut is not a seam, so its count is
    free. Returns (report, faces) with faces as vertex tuples.
    """
    rep = {"sides": [len(s_) - 1 for s_ in sides], "quads": 0, "tris": 0,
           "verts": 0, "status": "ok", "kind": "matched"}
    n = len(sides[0]) - 1
    m = len(sides[1]) - 1
    if n != len(sides[2]) - 1 or m != len(sides[3]) - 1:
        rep["status"] = "opposite sides differ"
        return rep, []
    if n < 1 or m < 1:
        rep["status"] = "a side has no edge"
        return rep, []
    rep["cells"] = (n, m)

    top = list(reversed(sides[2]))     # (0,m) -> (n,m)
    left = list(reversed(sides[3]))    # (0,0) -> (0,m)
    # Blend the real boundary positions by index, then relax the interior —
    # but only where relaxing helps; see relax_passes_for.
    relax_passes = relax_passes_for([[W[v] for v in s_] for s_ in sides],
                                    n, m, relax_passes)
    rep["relax"] = relax_passes
    pos, _n, _m = discrete_coons([W[v] for v in sides[0]],
                                 [W[v] for v in top],
                                 [W[v] for v in left],
                                 [W[v] for v in sides[1]])
    relax_interior(pos, n, m, passes=relax_passes)

    node = {}
    for i, v in enumerate(sides[0]):
        node[(i, 0)] = vref[v]
    for j, v in enumerate(sides[1]):
        node[(n, j)] = vref[v]
    for i, v in enumerate(top):
        node[(i, m)] = vref[v]
    for j, v in enumerate(left):
        node[(0, j)] = vref[v]

    made = []
    outside = 0
    for i in range(1, n):
        for j in range(1, m):
            p = pos[(i, j)]
            if poly is not None and not poly.contains_xy(p.x, p.y):
                outside += 1
            nv = bm.verts.new(inv @ Vector((p.x, p.y, 0.0)))
            nv[vlay] = 1
            node[(i, j)] = nv
            made.append(nv)
    rep["core_outside"] = outside
    if outside:
        bmesh.ops.delete(bm, geom=made, context='VERTS')
        rep["status"] = ("the grid folds out of this region (%d of %d interior "
                         "nodes land outside) — cut it again"
                         % (outside, max(1, (n - 1) * (m - 1))))
        return rep, []
    rep["verts"] = len(made)

    faces = []
    for i in range(n):
        for j in range(m):
            faces.append((node[(i, j)], node[(i + 1, j)],
                          node[(i + 1, j + 1)], node[(i, j + 1)]))
    return rep, faces


def run_patch_grid(guide, retopo, flat_sk, target_mm, only_island=None,
                   smooth_passes=6, match_distance=0.0, clear_preview=True,
                   progress=None):
    """Grid every panel whose outline carries exactly four marked corners.

    Returns a report. Panels with a different number of corners are listed,
    not guessed at: dividing an outline with twelve corners into patches is
    the next stage.

    `progress`, if given, is called with a 0..1 fraction once per panel — pass
    a ui_common.ProgressThrottle, never a raw progress_update.
    """
    me = retopo.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()

    cleared = clear_grid(bm)
    if clear_preview:
        pf.clear_fill(bm)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.ensure_lookup_table()

    # Create every layer up front. Adding one reallocates custom data and
    # invalidates the BMVert references held below (the same trap Preview Fill
    # documents), so nothing may be created after `vref` is built.
    vlay = _vert_layer(bm)
    flay = _face_layer(bm)
    elay = _edge_layer(bm)

    island_of = pf.island_of_boundary(guide, retopo, flat_sk, bm,
                                      match_distance=match_distance)
    clay = tc.layer(bm, create=False)
    corner_set = set() if clay is None else set(
        v.index for v in bm.verts if v[clay])

    mat = retopo.matrix_world
    inv = mat.inverted()
    W = {v.index: mat @ v.co for v in bm.verts}
    # Hold BMVert references, not indices: creating a core vertex invalidates
    # the index table (measured in Preview Fill as an IndexError on the second
    # panel), while the references stay good.
    vref = {v.index: v for v in bm.verts}

    by_island = panel_edges(bm, island_of)
    report = {"panels": 0, "gridded": 0, "quads": 0, "tris": 0, "verts": 0,
              "cleared_faces": cleared[0], "cleared_verts": cleared[1],
              "skipped": [], "per_panel": []}

    pre_edges = set(frozenset((e.verts[0], e.verts[1])) for e in bm.edges)
    new_faces = []

    n_islands = len(by_island)
    for i_isl, (isl, edges) in enumerate(sorted(by_island.items())):
        if progress is not None and n_islands:
            progress((i_isl + 1) / n_islands)
        if only_island is not None and isl != only_island:
            continue
        for ring, closed in rings_from_edges(edges, W):
            report["panels"] += 1
            tag = "isl%s(%dv)" % (isl, len(ring))
            if not closed:
                report["skipped"].append("%s: outline not closed" % tag)
                continue
            n_corner = sum(1 for v in ring if v in corner_set)
            if n_corner != 4:
                report["skipped"].append(
                    "%s: %d corners marked (need 4)" % (tag, n_corner))
                continue
            sides = sides_from_corners(ring, corner_set)
            poly = build_region_polygon([(W[v].x, W[v].y) for v in ring])
            rep, faces = grid_one_patch(bm, sides, W, vref, inv, target_mm,
                                        vlay, poly=poly,
                                        smooth_passes=smooth_passes)
            rep["island"] = isl
            report["per_panel"].append(rep)
            if rep["status"] != "ok":
                report["skipped"].append("%s: %s" % (tag, rep["status"]))
                continue
            made_q = made_t = 0
            for vs in faces:
                if len(set(vs)) != len(vs):
                    continue
                try:
                    f = bm.faces.new(vs)
                except ValueError:
                    continue
                f[flay] = 1
                new_faces.append(f)
                if len(vs) == 4:
                    made_q += 1
                else:
                    made_t += 1
            rep["quads"], rep["tris"] = made_q, made_t
            report["quads"] += made_q
            report["tris"] += made_t
            report["verts"] += rep["verts"]
            report["gridded"] += 1

    for e in bm.edges:
        if frozenset((e.verts[0], e.verts[1])) not in pre_edges:
            e[elay] = 1
    if new_faces:
        fo.orient_faces_up(new_faces, mat)

    bm.to_mesh(me)
    bm.free()
    me.update()
    return report


def run_clear_grid(retopo):
    me = retopo.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()
    n_f, n_v = clear_grid(bm)
    bm.to_mesh(me)
    bm.free()
    me.update()
    return {"faces": n_f, "verts": n_v}
