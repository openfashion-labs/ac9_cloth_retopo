"""Core algorithms for CLO Cleanup. bmesh / mathutils, no bpy.ops and no UI,
so every function can be tested headless on a bmesh.

The workflow is three steps, done on a bmesh holding a planar shape key (the
UV layout as geometry, see FlatSession below):

tag_creases_by_angle  tag edges by dihedral angle (fold lines)
inset_pieces          per pattern piece: a TRUE 2D offset of the outline
inset_line            inset a fold line (a set of edges) to both sides

Both insets work the same way, on the flat shape: shapely offsets the region
(`buffer(-W, mitre)` for a piece, the two offset curves of a line for a
band), the original faces that fall inside are deleted, and the ring between
the old boundary and the new row is re-triangulated with shapely's
constrained Delaunay. NOTHING is welded, so every outline vertex survives by
construction and the sewn 1:1 seam pairs cannot drift apart.

repair_fold_lines, tag_edges and the small tracking helpers below them are
kept for the panel's Selected -> Crease / Untag buttons and for callers that
want to rebuild a broken fold line by hand; the insets no longer call them.
"""

import heapq
import math
from collections import Counter

import bmesh
import numpy as np
from mathutils import Vector, kdtree
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform

KIND_LAYER = "ac9_crease_kind"   # edge INT attribute
KIND_NONE = 0
KIND_CREASE = 2    # rim edge or fold: gets rounded

# Edge INT attribute: 1 on the rows an inset built (the inner edges of the
# Inset Pieces strip, the outer rows of an Inset Line band). A later inset
# must not absorb those rows, and the sharp flag cannot be used to recognise
# them: a CLO export already carries sharp edges of its own (2925 on a jacket,
# including a line 0.35 mm beside the lapel crease).
ROW_LAYER = "ac9_inset_row"
PIECE_LAYER = "ac9_flat_piece"   # vertex INT, temporary during a FlatSession
# Face INT attribute: 1 on the faces an Inset Line band is made of. A later
# Inset Pieces keeps those faces (they are the fold's own geometry) and
# builds its strip around them, so the two steps commute.
BAND_LAYER = "ac9_inset_band"

# Inset deletes every original face closer than this multiple of the width to
# the outline, not just closer than the width itself. A garment drawn with the
# parallel-line trick already carries an internal line at exactly the width the
# user then insets by, and that line's vertices scatter a little to both sides
# of it: with an exact cut half of them are inside the new row and half are
# left standing right where it lands, which tears the strip. The margin puts
# the whole line on one side of the cut. Measured on a jacket export whose line
# sits at 1.00 mm: the distance histogram has 2202 vertices in 0.95-1.05 and a
# clear valley at 1.25-1.35, so a quarter of the width is both enough and not
# greedy (the next internal line was at 2.00 mm).
ABSORB_MARGIN = 1.25

# A convex corner sharper than this is bevelled by the offset instead of being
# mitred out to infinity (the even offset shoots out 49 mm for a 1 mm inset at
# an acute tip).
MITRE_LIMIT = 2.5

# Coordinate key rounding, in metres: 1 nm. Keys are only ever built from the
# DOUBLE coordinate a vertex was created at, never from v.co, so they stay
# exact -- see F32_TOL.
KEY_DIGITS = 9

# BMVert.co is float32: a test of a mesh vertex position against a shapely
# geometry must allow for that. Measured error 1.2e-8 .. 2.3e-8 m at metre
# magnitudes, so 1e-9 comparisons fail on every single vertex.
F32_TOL = 2e-7


class ShapelyMissing(RuntimeError):
    """Raised when shapely can't be imported - surfaced to the user in the UI."""


def require_shapely():
    """The shapely module, or ShapelyMissing. Same shape as
    uv_seam_guide.preview_fill._require_shapely: the wheel ships with the
    add-on, so this only fires on a hand-assembled install."""
    try:
        import shapely
        from shapely.geometry import Polygon, LineString, Point  # noqa: F401
        from shapely.prepared import prep  # noqa: F401
        return shapely
    except Exception as exc:  # pragma: no cover - environment dependent
        raise ShapelyMissing(
            "shapely is required for Inset Pieces / Inset Line but could not "
            "be imported. Reinstall the add-on from its release ZIP (the "
            "wheel ships with it), or install it into Blender's Python:\n"
            "  <blender>/python/bin/python -m pip install shapely"
        ) from exc


# ---------------------------------------------------------------------------
# helpers

def co_finite(co):
    """True when every component of `co` is a real number.

    A NaN coordinate is not a rounding error, it is a hole in the data, and it
    spreads: every comparison against NaN is False, so a guard written as
    `if length > limit` passes it through and a `dict` keyed on the position
    cannot even find its own key (NaN != NaN). Both bit the add-on
    (2026-09-09: barycentric_transform over a zero-area CLO triangle wrote
    NaN into a Guide's Basis, and the next Inset Line died in resync_seams
    with `KeyError: (nan, nan, nan)`), so every reader that keys or measures a
    position filters through this.
    """
    return (math.isfinite(co[0]) and math.isfinite(co[1])
            and math.isfinite(co[2]))


def sanitize_nonfinite(bm, layers=(), max_passes=6):
    """Put every vertex whose position is not finite back at the centroid of
    its finite neighbours, in `v.co` and in each shape layer of `layers`
    (pass None for "every shape layer"). Returns the number of vertices that
    were not finite.

    A NaN vertex carries no information about where it belonged, but its
    neighbours do: on the measured case (a jacket's trouser Guide, 5 NaN
    vertices) every one of them had 3-4 finite neighbours within 1 mm, so the
    centroid is within a fabric millimetre of the truth — while leaving the
    NaN in place crashes the next operator and deleting the vertex tears a
    hole in the Guide. Repaired outward over `max_passes`, so a small cluster
    of NaN vertices is repaired from its rim inward; a vertex with no finite
    neighbour at all after that is deleted (it has no recoverable position and
    nothing can be measured against it).
    """
    if layers is None:
        layers = [bm.verts.layers.shape[n]
                  for n in bm.verts.layers.shape.keys()]
    layers = list(layers)
    bad = [v for v in bm.verts
           if not co_finite(v.co) or any(not co_finite(v[l]) for l in layers)]
    if not bad:
        return 0
    n_bad = len(bad)
    for _ in range(max_passes):
        if not bad:
            break
        moved = []
        for v in bad:
            nbs = [e.other_vert(v) for e in v.link_edges]
            ok = False
            for lay in (None,) + tuple(layers):
                cur = v.co if lay is None else v[lay]
                if co_finite(cur):
                    ok = True
                    continue
                good = [(w.co if lay is None else w[lay]) for w in nbs]
                good = [c for c in good if co_finite(c)]
                if not good:
                    continue
                c = Vector()
                for g in good:
                    c += g
                c /= len(good)
                if lay is None:
                    v.co = c
                else:
                    v[lay] = c
                ok = True
            if ok and co_finite(v.co) and all(co_finite(v[l]) for l in layers):
                moved.append(v)
        if not moved:
            break
        done = set(moved)
        bad = [v for v in bad if v not in done]
    if bad:
        bmesh.ops.delete(bm, geom=bad, context='VERTS')
    return n_bad


def _dihedral(e):
    """Unsigned angle (radians) between the two faces of `e`; 0 if not manifold."""
    if len(e.link_faces) != 2:
        return 0.0
    try:
        return abs(e.calc_face_angle_signed())
    except ValueError:
        return 0.0


def kind_layer(bm, create=True):
    lay = bm.edges.layers.int.get(KIND_LAYER)
    if lay is None and create:
        lay = bm.edges.layers.int.new(KIND_LAYER)
    return lay


def row_layer(bm, create=True):
    lay = bm.edges.layers.int.get(ROW_LAYER)
    if lay is None and create:
        lay = bm.edges.layers.int.new(ROW_LAYER)
    return lay


def band_layer(bm, create=True):
    lay = bm.faces.layers.int.get(BAND_LAYER)
    if lay is None and create:
        lay = bm.faces.layers.int.new(BAND_LAYER)
    return lay


def prepare_layers(bm):
    """Create every custom layer the insets use (ROW_LAYER, PIECE_LAYER).
    Adding a layer reallocates the custom data and invalidates the element
    references taken before it, so an operator calls this BEFORE it collects
    the selection it hands to inset_line (ReferenceError otherwise)."""
    row_layer(bm)
    if bm.verts.layers.int.get(PIECE_LAYER) is None:
        bm.verts.layers.int.new(PIECE_LAYER)


def tagged_edges(bm):
    lay = bm.edges.layers.int.get(KIND_LAYER)
    if lay is None:
        return {}
    return {e: e[lay] for e in bm.edges if e[lay] != KIND_NONE}


def _surface_band(bm, sources, radius):
    """Dijkstra over edges from `sources`; {vert: surface distance <= radius}.
    Surface distance (not 3D) so a neighbouring cloth layer is not reached."""
    INF = 1e18
    dist = {}
    heap = []
    for v in sources:
        dist[v] = 0.0
        heap.append((0.0, v.index))
    heapq.heapify(heap)
    while heap:
        d0, vi = heapq.heappop(heap)
        v = bm.verts[vi]
        if d0 > dist.get(v, INF):
            continue
        for e in v.link_edges:
            o = e.other_vert(v)
            nd = d0 + e.calc_length()
            if nd <= radius and nd < dist.get(o, INF):
                dist[o] = nd
                heapq.heappush(heap, (nd, o.index))
    return dist


class _SegmentIndex:
    """Perpendicular distance from a point to the nearest of a set of edge
    segments (KD-tree over midpoints, candidates filtered by facing so a
    segment on another layer is not taken)."""

    def __init__(self, edges):
        self.seg = []
        for e in edges:
            a, b = e.verts[0].co.copy(), e.verts[1].co.copy()
            t = b - a
            if t.length < 1e-12:
                continue
            n = Vector()
            for f in e.link_faces:
                n += f.normal
            if n.length < 1e-12:
                n = e.verts[0].normal + e.verts[1].normal
            self.seg.append((a, t.normalized(), t.length, n.normalized()))
        self.kd = kdtree.KDTree(len(self.seg))
        for i, (a, t, ln, n) in enumerate(self.seg):
            self.kd.insert(a + t * (ln * 0.5), i)
        self.kd.balance()

    def distance(self, p, nref=None, k=8):
        best = None
        for (_co, i, _d) in self.kd.find_n(p, k):
            a, t, ln, n = self.seg[i]
            if nref is not None and n.dot(nref) < 0.0:
                continue
            u = max(0.0, min(1.0, (p - a).dot(t) / ln))
            dd = (p - (a + t * (u * ln))).length
            if best is None or dd < best:
                best = dd
        return best


# ---------------------------------------------------------------------------
# flat session: edit in 2D, return to the untouched 3D surface
#
# The export carries a planar shape key (its UV layout laid out as geometry,
# the retopo Guide's flat state). Every inset is done on that flat shape:
# the offsets are exact parallels, a vertex has one UV (= its flat position),
# and nothing depends on 3D face normals. At the end every vertex, old or new,
# is put back onto the ORIGINAL 3D surface by locating its flat position in
# the original flat triangles and taking the same barycentric point of the
# original 3D triangle. Old vertices land on themselves; new rows land on the
# surface exactly. Welding in 3D (absorb) had carried the absorbed corner's
# UV onto the outline vertex, which made the UV outline zigzag (863 outline
# vertices with split UVs on a jacket, 19 before); here the UV is rewritten
# from the flat position, so it cannot.

def find_flat_layer(bm, exclude=()):
    """Name of a shape layer that is planar (one coordinate constant), or
    None. `exclude`: the reference key's name."""
    for name in bm.verts.layers.shape.keys():
        if name in exclude:
            continue
        lay = bm.verts.layers.shape[name]
        for axis in range(3):
            lo = hi = None
            planar = True
            for v in bm.verts:
                c = v[lay][axis]
                # A NaN would make every `hi - lo > tol` test False and report
                # ANY layer as planar (see co_finite): skip it, and a layer
                # that is nothing but NaN stays unplanar (lo is None below).
                if not math.isfinite(c):
                    continue
                if lo is None:
                    lo = hi = c
                else:
                    lo = min(lo, c)
                    hi = max(hi, c)
                if hi - lo > 1e-7:
                    planar = False
                    break
            if planar and lo is not None:
                return name
    return None


class FlatSession:
    """Swap the bmesh into its flat shape, remember the original flat and 3D
    triangles per piece, and on `end()` map every vertex back to 3D, write
    the flat shape and the UVs."""

    def __init__(self, bm, flat_name, basis_name, active_name=None):
        """`active_name`: the shape key whose coordinates `v.co` holds right
        now (the active key in Edit Mode; the basis in Object Mode)."""
        self.bm = bm
        self.flat = bm.verts.layers.shape[flat_name]
        self.basis = bm.verts.layers.shape[basis_name]
        self.active_is_basis = active_name in (None, basis_name)
        self.active = (bm.verts.layers.shape[active_name]
                       if active_name is not None else self.basis)
        # layer first: adding one reallocates and invalidates element refs
        self.player = bm.verts.layers.int.get(PIECE_LAYER) or bm.verts.layers.int.new(PIECE_LAYER)
        if self.active_is_basis:
            # v.co is the live basis; the layer may lag behind it
            for v in bm.verts:
                v[self.basis] = v.co
        self.pieces = None

    def enter_3d(self):
        """v.co = the 3D shape (for anything that reads the fold angles)."""
        for v in self.bm.verts:
            v.co = v[self.basis]
        self.bm.normal_update()

    def restore(self):
        """v.co = the shape key that was active when the session started
        (self.active). Used after a read-only pass in 3D (enter_3d) that must
        not leave the displayed shape swapped to the basis."""
        for v in self.bm.verts:
            v.co = v[self.active]

    def enter_2d(self):
        """v.co = the flat shape; remembers the original flat / 3D triangles
        per piece (once)."""
        bm = self.bm
        for v in bm.verts:
            v.co = v[self.flat]
        bm.normal_update()
        if self.pieces is not None:
            return
        uv = bm.loops.layers.uv.active
        # flat = s * uv + t (least squares on a sample; the user's layout has
        # s = 1, t = 0 but nothing here relies on that)
        su = sf = suu = suf = Vector((0.0, 0.0))
        n = 0
        step = max(1, len(bm.faces) // 3000)
        faces = list(bm.faces)
        for f in faces[::step]:
            for l in f.loops:
                u = l[uv].uv
                p = l.vert[self.flat].xy
                su = su + u
                sf = sf + p
                suu = suu + Vector((u.x * u.x, u.y * u.y))
                suf = suf + Vector((u.x * p.x, u.y * p.y))
                n += 1
        s = Vector((1.0, 1.0))
        for i in range(2):
            var = suu[i] - su[i] * su[i] / n
            if var > 1e-18:
                s[i] = (suf[i] - su[i] * sf[i] / n) / var
        self.scale = (s.x + s.y) * 0.5
        self.offset = (sf - su * self.scale) / n
        # per-piece BVH of the flat triangles, with their 3D triangles. Each
        # vertex is stamped with its piece index: mirrored pieces may share
        # the same flat coordinates (packed on top of each other), so a
        # position lookup cannot tell them apart, and new vertices inherit
        # the stamp from the vertices they interpolate.
        self.pieces = []
        for comp in _pieces(bm):
            verts = list({v for f in comp for v in f.verts})
            for v in verts:
                v[self.player] = len(self.pieces)
            vi = {v: i for i, v in enumerate(verts)}
            flat = [v.co.copy() for v in verts]
            tris = []
            for f in comp:
                fv = list(f.verts)
                for k in range(1, len(fv) - 1):
                    tris.append((vi[fv[0]], vi[fv[k]], vi[fv[k + 1]]))
            tree = BVHTree.FromPolygons(flat, tris, all_triangles=True)
            basis = [v[self.basis].copy() for v in verts]
            kd = kdtree.KDTree(len(flat))
            for i, co in enumerate(flat):
                kd.insert(co, i)
            kd.balance()
            self.pieces.append((tree, flat, basis, tris, kd))

    def _piece_of(self, comp_verts):
        """The original piece a (post-edit) component belongs to: the one
        whose original vertices coincide with most of the component's."""
        best = None
        sample = comp_verts[:: max(1, len(comp_verts) // 200)]
        for k, (tree, flat, basis, tris, kd) in enumerate(self.pieces):
            hits = 0
            for v in sample:
                co, i, d = kd.find(v.co)
                if d < 1e-9:
                    hits += 1
            if best is None or hits > best[0]:
                best = (hits, k)
        return best[1] if best and best[0] > 0 else None

    def end(self):
        bm = self.bm
        uv = bm.loops.layers.uv.active
        inv = 1.0 / self.scale if abs(self.scale) > 1e-12 else 1.0
        unmapped = 0
        clamped = 0
        nonfinite = 0
        for comp in _pieces(bm):
            verts = list({v for f in comp for v in f.verts})
            # Which original piece this component is: the MAJORITY stamp of
            # its original vertices (flat position coinciding with a vertex
            # of the stamped piece), applied to every vertex of the
            # component. The per-vertex stamp alone is not safe: a vertex
            # that maps nowhere keeps whatever its own stamp says, and a
            # wrong stamp then sends it off the garment (measured
            # 2026-09-05 on a 4-piece skirt, with the old absorb-and-weld
            # inset: stamp 4, basis layer (-459, 535, -325); mapped
            # nowhere, it kept that basis and flew 5 km). A component
            # never spans two original pieces — the insets split pieces,
            # they never join them — so the vote is exact.
            votes = Counter()
            for v in verts:
                k = v[self.player]
                if 0 <= k < len(self.pieces):
                    co, oi, od = self.pieces[k][4].find(v.co)
                    if od is not None and od < 1e-9:
                        votes[k] += 1
            k_comp = votes.most_common(1)[0][0] if votes else None
            for v in verts:
                p2 = v.co.copy()
                k = k_comp if k_comp is not None else v[self.player]
                if not (0 <= k < len(self.pieces)):
                    k = None
                if k is not None:
                    tree, flat, basis, tris, kd = self.pieces[k]
                    co, oi, od = kd.find(p2)
                    if od is not None and od < 1e-9:
                        # an original vertex: its own 3D position, exactly (the
                        # BVH may hand back a neighbouring triangle where the
                        # flat fan overlaps itself; 0.19 mm error measured)
                        p3 = basis[oi]
                        idx = None
                        loc = None
                    else:
                        loc, nrm, idx, dist = tree.find_nearest(p2)
                    if loc is None and od is not None and od < 1e-9:
                        pass
                    elif idx is not None:
                        a, b, c = tris[idx]
                        p3 = barycentric_transform(loc, flat[a], flat[b], flat[c],
                                                   basis[a], basis[b], basis[c])
                        # A degenerate original triangle (CLO exports carry
                        # slivers) makes the transform extrapolate wildly:
                        # vertices 2 m off the garment measured on a jacket.
                        # The pattern is drawn 1:1, so a new vertex cannot be
                        # much farther from its nearest original in 3D than
                        # it is in the flat layout. Beyond 3x that (plus 2 mm
                        # of slack) fall back to that original's 3D position:
                        # at most a row-width off, never off the garment.
                        if od is not None and (p3 - basis[oi]).length > 3.0 * od + 0.002:
                            p3 = basis[oi].copy()
                            clamped += 1
                        # ... and a triangle that is EXACTLY degenerate makes
                        # barycentric_transform return NaN rather than a wild
                        # number (measured 2026-09-09: a zero-area flat
                        # triangle on a trouser Guide). The clamp above cannot
                        # catch that — `nan > limit` is False — and the NaN
                        # then travels into the Basis key, where the next
                        # Inset Line dies on it (see co_finite). Fall back to
                        # the nearest original's 3D position, the same answer
                        # the clamp gives.
                        elif not co_finite(p3):
                            p3 = (basis[oi].copy() if od is not None
                                  else v[self.basis].copy())
                            nonfinite += 1
                    else:
                        p3 = v[self.basis].copy()
                        unmapped += 1
                else:
                    p3 = v[self.basis].copy()
                    unmapped += 1
                v[self.flat] = p2
                v[self.basis] = p3
                v.co = p3 if self.active_is_basis else v[self.active]
                for l in v.link_loops:
                    l[uv].uv = (p2.xy - self.offset) * inv
        # Vertices with no face are never reached above (the walk is over
        # face components), so they would keep their FLAT coordinates in 3D
        # — an edge from the garment to the flat layout, the "flown polygons"
        # measured on a jacket (2 per run, degree 1, a wire edge left by a
        # weld or dissolve). A faceless vertex is of no use to a Guide: drop
        # it, wire edges included.
        loose = [v for v in bm.verts if not v.link_faces]
        self.loose_removed = len(loose)
        if loose:
            bmesh.ops.delete(bm, geom=loose, context='VERTS')
        # Safety net: the fallbacks above are only as good as the original they
        # fall back to, and a Guide that ALREADY holds a NaN (written by a run
        # from before this guard existed) hands one over. Repair from the
        # neighbours, and rewrite the UVs that inherited the NaN.
        sane = [self.basis, self.flat]
        if not self.active_is_basis:
            sane.append(self.active)
        repaired = sanitize_nonfinite(bm, layers=sane)
        if repaired:
            for v in bm.verts:
                for l in v.link_loops:
                    uvv = l[uv].uv
                    if not (math.isfinite(uvv.x) and math.isfinite(uvv.y)):
                        l[uv].uv = (v[self.flat].xy - self.offset) * inv
        bm.verts.layers.int.remove(self.player)
        bm.normal_update()
        self.clamped = clamped
        self.nonfinite = nonfinite + repaired
        return unmapped


# ---------------------------------------------------------------------------
# 2. inset pieces (per pattern piece: the pieces are still loose parts, so
#    every outline edge, seam or free edge alike, is a boundary edge)

def _pieces(bm):
    """Connected components of faces."""
    seen = set()
    parts = []
    for f in bm.faces:
        if f in seen:
            continue
        stack = [f]
        seen.add(f)
        comp = []
        while stack:
            x = stack.pop()
            comp.append(x)
            for e in x.edges:
                for g in e.link_faces:
                    if g not in seen:
                        seen.add(g)
                        stack.append(g)
        parts.append(comp)
    return parts


def _edge_components(edges):
    """Connected components of a set of edges (a fold line each)."""
    adj = {}
    for e in edges:
        for v in e.verts:
            adj.setdefault(v, []).append(e)
    seen = set()
    comps = []
    for e0 in edges:
        if e0 in seen:
            continue
        stack = [e0]
        seen.add(e0)
        comp = []
        while stack:
            e = stack.pop()
            comp.append(e)
            for v in e.verts:
                for x in adj[v]:
                    if x not in seen:
                        seen.add(x)
                        stack.append(x)
        comps.append(comp)
    return comps


def _key(x, y):
    """Coordinate key for a flat position. Only ever built from the DOUBLE
    coordinate a vertex was created at, never from v.co (float32)."""
    return (round(x, KEY_DIGITS), round(y, KEY_DIGITS))


def _signed_area(coords):
    a = 0.0
    n = len(coords)
    for i in range(n):
        x1, y1 = coords[i]
        x2, y2 = coords[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return 0.5 * a


def _loops(edges):
    """Closed vertex loops of a set of edges that form simple cycles.
    Returns [(verts, closed_ok)]."""
    adj = {}
    for e in edges:
        for v in e.verts:
            adj.setdefault(v, []).append(e)
    seen = set()
    loops = []
    for e0 in edges:
        if e0 in seen:
            continue
        loop = []
        e = e0
        v = e.verts[0]
        ok = True
        for _ in range(len(edges) + 1):
            seen.add(e)
            loop.append(v)
            v = e.other_vert(v)
            nxt = [x for x in adj[v] if x is not e]
            if len(nxt) != 1:
                ok = False
                break
            e = nxt[0]
            if e is e0:
                break
            if e in seen:
                ok = False
                break
        loops.append((loop, ok))
    return loops


def _polygon_from_loops(loops):
    """Polygon with the largest loop as shell and the others as holes, from
    the output of _loops (v.co must hold the flat shape)."""
    from shapely.geometry import Polygon

    rings = []
    for verts, ok in loops:
        if not ok or len(verts) < 3:
            continue
        cs = [(v.co.x, v.co.y) for v in verts]
        rings.append((abs(_signed_area(cs)), cs))
    if not rings:
        return None
    rings.sort(key=lambda r: r[0], reverse=True)
    return Polygon(rings[0][1], [r[1] for r in rings[1:]])


def _polys(geom):
    """Every Polygon inside a shapely geometry, flattened."""
    from shapely.geometry import Polygon, MultiPolygon, GeometryCollection

    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, (MultiPolygon, GeometryCollection)):
        out = []
        for g in geom.geoms:
            out.extend(_polys(g))
        return out
    return []


def _ring_coords(poly):
    """All rings (exterior + interiors) of a polygon as coordinate lists,
    without the closing duplicate."""
    rings = [list(poly.exterior.coords)[:-1]]
    for r in poly.interiors:
        rings.append(list(r.coords)[:-1])
    return rings


def _seam_key(v, basis_layer):
    """4-decimal rounded OBJECT-space position of `v`, from `basis_layer` if given (a
    FlatSession leaves v.co holding the flat 2D layout, so the seam-twin
    lookup must read the untouched Basis/3D shape instead), else v.co.
    Same rounding as uv_seam_guide.analysis.find_seam_pairs_flat.

    None when the position is not finite: NaN is not equal to itself, so a
    dict keyed on such a tuple cannot find its own key back — the
    `KeyError: (nan, nan, nan)` of 2026-09-09, raised one line after the dict
    was built. Callers drop what has no key (see _finite_boundary_edges);
    sanitize_nonfinite is what actually repairs those vertices."""
    c = v[basis_layer] if basis_layer is not None else v.co
    if not co_finite(c):
        return None
    return (round(c.x, 4), round(c.y, 4), round(c.z, 4))


def _finite_boundary_edges(bm, basis_layer):
    """(boundary edges whose BOTH endpoints have a finite position, count of
    the ones dropped). A vertex with no position takes part in no seam pair,
    so every seam census skips it rather than keying on NaN."""
    out = []
    dropped = 0
    for e in bm.edges:
        if not e.is_boundary:
            continue
        if (_seam_key(e.verts[0], basis_layer) is None
                or _seam_key(e.verts[1], basis_layer) is None):
            dropped += 1
            continue
        out.append(e)
    return out, dropped


def count_seam_desync(bm, basis_layer=None):
    """Census of boundary edges whose seam pairing looks broken: both
    endpoints have a same-position twin (another boundary vertex with the
    same _seam_key) yet no other boundary edge shares their sorted endpoint-
    key pair — i.e. the 1:1 seam match that the rest of the addon assumes is
    broken there. Same test as _dev_tests/diag_false_free_census_any.py.

    `basis_layer`: see _seam_key.

    Returns {'desync': N, 'zero_len': M, 'nonfinite': K} — `zero_len` is a
    boundary edge whose two endpoints sit under 1e-6 apart in that same
    position (a degenerate edge, seam or not); `nonfinite` counts the
    boundary edges skipped because an endpoint has no finite position."""
    bnd, nonfinite = _finite_boundary_edges(bm, basis_layer)
    bverts = {v for e in bnd for v in e.verts}
    vg = {}
    for v in bverts:
        vg.setdefault(_seam_key(v, basis_layer), []).append(v)
    has_twin = {v: len(vg[_seam_key(v, basis_layer)]) > 1 for v in bverts}
    eg = {}
    edge_pair_key = {}
    for e in bnd:
        k1, k2 = _seam_key(e.verts[0], basis_layer), _seam_key(e.verts[1], basis_layer)
        pk = (k1, k2) if k1 <= k2 else (k2, k1)
        edge_pair_key[e] = pk
        eg.setdefault(pk, []).append(e)
    desync = 0
    zero_len = 0
    for e in bnd:
        a, b = e.verts
        if len(eg[edge_pair_key[e]]) <= 1 and has_twin[a] and has_twin[b]:
            desync += 1
        pa = a[basis_layer] if basis_layer is not None else a.co
        pb = b[basis_layer] if basis_layer is not None else b.co
        if (pa - pb).length < 1e-6:
            zero_len += 1
    return {'desync': desync, 'zero_len': zero_len, 'nonfinite': nonfinite}


# How far off the chord a partner-side vertex may sit and still count as ON it,
# as a multiple of the median BOUNDARY edge length. Derived rather than fixed
# because both bounds of the usable band scale with the edge length:
#
#   lower bound  the deviation we MUST cover. The welded-away apex sat within
#               5 % of the width (0.05 mm) of its chord in the FLAT
#               layout, but in 3D the surface curves: 0.25-0.62 mm measured on
#               the 4 edges a 0.25 mm band still missed (jacket, 2026-09-07)
#               = 0.13-0.32 x the boundary edge there (1.96 mm).
#   upper bound  what must stay OUT. The twin's neighbour running the other way
#               projects at t < 0, but a corner neighbour inside 0 < t < 1 sat
#               1.8 mm off = 0.92 x that boundary edge.
#
# Measured on the density ladder (jacket/pants/skirt at CLO particle distance
# 2/3/4/5, AUDIT_density_independence_2026-09-08.md): the chord deviation is
# LINEAR in edge length (p99 / median edge = 0.816 / 0.839 / 0.846 / 0.836,
# constant to +-2%), so a fixed millimetre value drifts out of the band as the
# mesh coarsens: the old 1 mm was 0.51 x the boundary edge at particle distance
# 2 but only 0.26 / 0.21 x at 4 / 5, under the lower bound, and Inset left
# 7 / 8 desynced seam edges where particle distance 2 left none.
#
# The value is swept, not a point estimate. Desync edges left after Inset
# Pieces (width 1 mm) over the whole ladder, particle distance 2/3/4/5:
#
#   k         jacket     pants      skirt      total
#   1 mm fix  0/7/8/2    0/0/0/1    6/0/3/1     28     <- what this replaces
#   0.55      0/2/4/0    0/0/0/0    6/0/0/0     12
#   0.60      0/0/2/0    0/0/0/0    6/0/0/0      8     <- minimum
#   0.65      0/0/2/0    0/2/0/0    6/0/0/0     10
#   0.70      0/0/2/0    0/2/2/0    6/0/0/0     12
#   0.75      0/0/2/0    0/2/2/0    6/0/0/0     12
#
# Every k in 0.55-0.75 beats the fixed millimetre; 0.60 is the minimum. Pushing
# it higher costs jacket nothing but starts breaking pants, and on jacket alone
# k >= 0.80 makes desync RISE while the insert count jumps (289 -> 300 points
# at particle distance 2): that is the walk starting to accept the corner
# neighbour and inserting a vertex that is not on the seam. The turn happens at
# 0.7-0.8 x the boundary edge = 0.85-0.97 x the all-edge median, which is where
# the 1.8 mm corner neighbour measured at particle distance 2 sits (1.11 x the
# all-edge median) -- the sweep and the geometry agree on the upper bound.
#
# The 6 left on skirt at particle distance 2 are there with the old fixed 1 mm
# too, at every k: a pre-existing failure at the density this addon was
# developed at, NOT a density effect. See AUDIT §8-C (sharp outline corners /
# Y-junctions, the known dart-and-Y-merge limitation).
ON_SEGMENT_FACTOR = 0.60

# Boundary edges run 1.18-1.21x the all-edge median on this data (measured on
# the same ladder), so rescale the factor if you ever switch the population.
def flat_scale_bm(bm, flat_layer, basis_layer):
    """median |flat edge| / |3D edge| for `bm`, or 1.0 when unmeasurable.

    The same quantity clo_projector.guide.flat_per_real reports, measured from a
    bmesh instead of an object -- which is what this package needs, because the
    insets run on `context.active_object` and never resolve the shared Guide
    pointer, so there is no Guide object to ask. Both layers live on the bm, so
    the ratio is right there.

    Multiply a real fabric length by this to get the flat layout's own units.
    Needed because Create Flat SK stores the raw UV as geometry with no
    normalisation: the flat layout's scale is whatever the UV packing happens to
    be, measured 1.0023 for a garment whose UV fills the square against 0.3318
    for a whole outfit packed into one square (AUDIT §8-B). Without it the same
    Width means 3.02x the fabric distance on the second layout.

    Degenerate 3D edges are skipped -- a CLO export carries a handful of
    sub-micron ones (21 in a production jacket, shortest 60 nm) and a ratio
    against those is noise.
    """
    if flat_layer is None or basis_layer is None:
        return 1.0
    ratios = []
    for e in bm.edges:
        a, b = e.verts
        d3 = (a[basis_layer] - b[basis_layer]).length
        if d3 <= 1e-9:
            continue
        ratios.append((a[flat_layer] - b[flat_layer]).length / d3)
    if not ratios:
        return 1.0
    ratios.sort()
    r = ratios[len(ratios) // 2]
    return r if r > 0.0 else 1.0


def flat_boundary_edge_median(bm, flat_layer=None):
    """Median boundary edge length in the space the insets actually run in.

    The insets offset the outline by `width` on the FLAT shape, so `width` is
    only meaningful relative to the flat spacing of the outline vertices. On a
    coarse CLO export the outline can be wider-spaced than the width itself
    (measured on the density ladder: flat boundary edge 1.6 mm at CLO particle
    distance 5 against the default 1 mm width), which means the offset row is
    being asked for below the mesh's own resolution. Absorb and needle removal
    still run, but there is no vertex spacing left to express the band with, so
    the operators say so rather than producing a quietly ragged strip.

    Returns 0.0 when there is no boundary.
    """
    def P(v):
        return v[flat_layer] if flat_layer is not None else v.co

    lens = sorted((P(e.verts[0]) - P(e.verts[1])).length
                  for e in bm.edges if e.is_boundary)
    return lens[len(lens) // 2] if lens else 0.0


def on_segment_tol(bm, basis_layer=None):
    """ON_SEGMENT_FACTOR x the median boundary edge length of `bm`.

    Measured through `basis_layer` — the SAME accessor _on_segment_walk tests
    with — not through `v.co`: FlatSession.end() only writes the 3D shape into
    `v.co` when the Basis is the active shape key, so on a Guide whose Flat SK
    is active (Create Flat SK leaves it at value 1.0) `v.co` holds the flat
    layout and would give a tolerance in the wrong space.

    Falls back to the all-edge median when nothing is on the boundary, and to 0
    on an empty mesh (which leaves _on_segment_walk finding nothing, so
    resync_seams reports every desync edge as unresolved rather than guessing).
    """
    def P(v):
        return v[basis_layer] if basis_layer is not None else v.co

    # A NaN length would land anywhere in sorted() and poison the median, so
    # only finite edges are measured (see co_finite).
    lens = sorted(L for L in ((P(e.verts[0]) - P(e.verts[1])).length
                              for e in bm.edges if e.is_boundary)
                  if math.isfinite(L))
    if not lens:
        lens = sorted(L for L in ((P(e.verts[0]) - P(e.verts[1])).length
                                  for e in bm.edges) if math.isfinite(L))
    if not lens:
        return 0.0
    return ON_SEGMENT_FACTOR * lens[len(lens) // 2]


def _on_segment_walk(starts, Pa, Pb, adj, coincident, exclude, P, key, max_steps,
                     tol):
    """Positions of the partner-side outline vertices that lie ON the segment
    Pa-Pb, found by walking the boundary outward from every twin of either
    end and keeping going only while the vertices stay on the segment.

    A plain shortest path from a's twins to b's twins is not enough: the
    partner side is not always one walk. At a seam junction two pieces meet
    at the welded-away position (joined only through a coincident vertex
    pair), and at an anchor — a seam ending where a free edge begins — the
    twin of `a` runs off in another direction altogether and only `b`'s side
    passes through the missing position (2 edges at a jacket shoulder,
    2026-09-07). Walking from both ends and testing "on the segment" instead
    of "reaches the other end" covers both; the perpendicular tolerance is
    what keeps the walk from wandering onto an unrelated outline.

    `coincident[v]`: other boundary vertices at v's position (free hops).
    `exclude`: never entered (the desync edge's own ends). Returns
    {position key: Vector} for strictly interior points, ordered later by
    the caller."""
    d = Pb - Pa
    L2 = d.length_squared
    if L2 <= 1e-18:
        return {}
    ka, kb = key(Pa), key(Pb)

    def on_segment(p):
        t = (p - Pa).dot(d) / L2
        if t <= 0.0 or t >= 1.0:
            return False
        return (p - (Pa + d * t)).length <= tol

    points = {}
    # depth at which a vertex was first entered; a vertex reached again by a
    # SHORTER route is expanded again, or a far start could pre-empt a near
    # one and cut the walk short of an on-segment vertex behind it
    seen = {v: -1 for v in exclude}
    for s0 in starts:
        seen[s0] = 0
    for s0 in starts:
        stack = [(s0, 0)]
        while stack:
            v, depth = stack.pop()
            if depth >= max_steps:
                continue
            nbs = list(adj.get(v, ()))
            nbs.extend(coincident.get(v, ()))
            for nb in nbs:
                prev = seen.get(nb)
                if prev is not None and prev <= depth + 1:
                    continue
                seen[nb] = depth + 1
                pv = P(nb)
                kv = key(pv)
                if kv == ka or kv == kb:
                    # the other end (or a coincident copy of an end): the
                    # walk may continue through it but it is not a split
                    stack.append((nb, depth + 1))
                    continue
                if not on_segment(pv):
                    continue
                points.setdefault(kv, pv.copy())
                stack.append((nb, depth + 1))
    return points


def resync_seams(bm, basis_layer=None, flat_layer=None, max_steps=8, max_passes=3,
                 active_layer=None, tol=None):
    """Repair seam pairs left desynced by an inset (see
    DESIGN_seam_twin_sync_2026-09-07.md).

    SAFETY NET since 2026-09-12: the 2D-offset insets weld nothing, so no
    outline vertex is lost and this should find nothing to do (measured: 0
    splits on a jacket and on a coarse export). It stays because it is the
    census that PROVES that, and because a Guide can arrive already
    desynced from an older run.

    The failure it repairs: an inset welds an outline apex away, and its 3D
    twin on the seam's other side is left with one vertex too many for the
    1:1 match the rest of the addon assumes — a boundary edge whose sorted
    endpoint-key pair no other boundary edge shares, exactly what
    count_seam_desync flags. This splits the coarser
    side's edge to match, using the finer side's OWN vertex positions as the
    new points; it never deletes or moves a face (the adjacent strip face
    just gains a vertex and becomes an n-gon -- which is why the faces the
    splits touched are triangulated again before returning: the Guide is
    read as triangles by the projector, and a quad left here reached the
    user as "Guide polygon N has 4 vertices", 96 of them on a shirt).

    Call this AFTER the bmesh is back in the Basis / 3D frame — that is,
    after the FlatSession's `end()`. Mid-edit, on the flat shape, a new
    vertex's 3D position has not been barycentric-mapped back yet, so its
    twin key would not match the one the seam census uses — see the
    module's design note.

    `basis_layer`, `flat_layer`: see _seam_key / FlatSession. `flat_layer` is
    optional: pass it whenever the caller also tracks a flat/UV shape key, so
    a new vertex gets a reasonable flat position (linear 3D-distance
    interpolation between the split edge's original endpoints); with None
    only `co` / `basis_layer` and the UV loop data (edge_split's own
    interpolation) are set.

    Each of up to `max_passes` passes:
      0. weld away zero-length boundary edges (b onto a; a vertex already
         welded away this pass cannot be a target, a chain is picked up next
         pass).
      1. build the boundary twin dictionary and boundary adjacency.
      2. enumerate desync edges (the count_seam_desync test).
      3. for each, walk the boundary outward from every twin of either end
         (up to `max_steps` edges, coincident vertices are free hops) and
         collect the partner-side vertices that lie ON the segment a-b
         (_on_segment_walk: 0 < t < 1, within `tol` of the chord — see
         on_segment_tol: derived from the median boundary edge length, because
         the deviation to cover is linear in it);
         split the desync edge at those positions in order from `a`.
         Several twins (a seam junction) contribute into one union, keyed
         by position; an edge with no on-segment vertex is left alone and
         counted in `unresolved`.
      4. re-count; stop when desync hits 0 or does not improve.

    Returns {'split': n, 'zero_welded': m, 'retriangulated': t,
    'unresolved': k, 'passes': p} —
    `unresolved` is the final count_seam_desync desync count: a stuck edge
    is retried every pass (topology upstream of it may still change), so a
    per-attempt tally would count the same stubborn edge more than once;
    what is left when the loop stops is the true unresolved set."""
    def P(v):
        return v[basis_layer] if basis_layer is not None else v.co

    if tol is None:
        tol = on_segment_tol(bm, basis_layer)

    split_total = 0
    zero_welded_total = 0
    passes_done = 0
    prev_desync = None
    # faces that gained a vertex from an edge_split below; triangulated once
    # at the end (a face can be split more than once, across passes, so
    # collecting and cleaning up per split would re-do the same face)
    touched_faces = set()

    for _ in range(max_passes):
        passes_done += 1

        # 0. zero-length boundary edge weld (chains are picked up next pass)
        # A vertex with no finite position is left out of every list here: it
        # keys nothing (see _seam_key) and measures nothing.
        bnd, nonfinite_edges = _finite_boundary_edges(bm, basis_layer)
        targetmap = {}
        for e in bnd:
            a, b = e.verts
            if (P(a) - P(b)).length < 1e-6:
                targetmap.setdefault(b, a)
        targetmap = {v: t for v, t in targetmap.items() if t not in targetmap}
        if targetmap:
            bmesh.ops.weld_verts(bm, targetmap=targetmap)
            zero_welded_total += len(targetmap)

        # 1. twins + boundary adjacency
        bnd, _ = _finite_boundary_edges(bm, basis_layer)
        bverts = {v for e in bnd for v in e.verts}
        twins = {}
        for v in bverts:
            twins.setdefault(_seam_key(v, basis_layer), []).append(v)
        adj = {}
        for e in bnd:
            x, y = e.verts
            adj.setdefault(x, set()).add(y)
            adj.setdefault(y, set()).add(x)
        # same-position boundary vertices are zero-length hops for the walk
        coincident = {v: [w for w in group if w is not v]
                      for group in twins.values() if len(group) > 1 for v in group}

        # 2. desync edges (same test as count_seam_desync)
        has_twin = {v: len(twins[_seam_key(v, basis_layer)]) > 1 for v in bverts}
        eg = {}
        edge_pair_key = {}
        for e in bnd:
            k1, k2 = _seam_key(e.verts[0], basis_layer), _seam_key(e.verts[1], basis_layer)
            pk = (k1, k2) if k1 <= k2 else (k2, k1)
            edge_pair_key[e] = pk
            eg.setdefault(pk, []).append(e)
        desync_edges = [e for e in bnd
                        if len(eg[edge_pair_key[e]]) <= 1
                        and has_twin[e.verts[0]] and has_twin[e.verts[1]]]

        if not desync_edges:
            break
        if prev_desync is not None and len(desync_edges) >= prev_desync:
            break
        prev_desync = len(desync_edges)

        # 3. split each desync edge at its twin side's intermediate positions
        for e in desync_edges:
            if not e.is_valid:
                continue
            a, b = e.verts
            key_a, key_b = _seam_key(a, basis_layer), _seam_key(b, basis_layer)
            a_twins = [v for v in twins.get(key_a, ()) if v is not a and v.is_valid]
            b_twins = {v for v in twins.get(key_b, ()) if v is not b and v.is_valid}
            if not a_twins or not b_twins:
                continue
            Pa, Pb = P(a).copy(), P(b).copy()
            direct = (Pa - Pb).length
            if direct <= 1e-9:
                continue
            points = _on_segment_walk(
                a_twins + list(b_twins), Pa, Pb, adj, coincident, (a, b),
                P, lambda p: (round(p.x, 4), round(p.y, 4), round(p.z, 4)),
                max_steps, tol)
            if not points:
                continue
            flat_a = a[flat_layer].copy() if flat_layer is not None else None
            flat_b = b[flat_layer].copy() if flat_layer is not None else None
            # order and parametrise by the PROJECTION onto the chord (the same
            # quantity _on_segment_walk tested), not by the distance from a: a
            # point `tol` off the chord near b would otherwise read
            # t > 1 on a short edge, clamp to fac 1.0 and take b's UVs.
            dvec = Pb - Pa
            L2 = dvec.length_squared
            ordered = sorted(points.values(), key=lambda p: (p - Pa).dot(dvec) / L2)
            e_cur = e
            cur_a = a
            t_prev = 0.0
            for pt in ordered:
                t = (pt - Pa).dot(dvec) / L2
                if t <= 0.0 or t >= 1.0 or t - t_prev < 1e-9:
                    continue    # would make a zero-length edge
                remaining = 1.0 - t_prev
                fac = (t - t_prev) / remaining
                _new_edge, new_vert = bmesh.utils.edge_split(e_cur, cur_a, fac)
                # bit-exact: no rounding, the whole point is to hit the twin's
                # own key. edge_split's own interpolation (proportional to
                # fac, in the current basis-frame coordinates) is left to set
                # everything else, including the UV loop data.
                if basis_layer is not None:
                    new_vert[basis_layer] = pt
                if flat_layer is not None:
                    new_vert[flat_layer] = flat_a + (flat_b - flat_a) * t
                # v.co is whatever shape key is ACTIVE (Edit Mode with the flat
                # key shown: the flat layout, not the Basis). Writing the 3D
                # position there would send the new vertex to the garment
                # while the mesh shows the layout — FlatSession.end() makes
                # the same distinction (v.co = p3 if active_is_basis else
                # v[active]). edge_split already interpolated the active
                # layer, so it is simply copied out.
                if active_layer is not None and active_layer != basis_layer:
                    new_vert.co = new_vert[active_layer]
                else:
                    new_vert.co = pt
                split_total += 1
                touched_faces.update(new_vert.link_faces)
                # e_cur (the same BMEdge, mutated by edge_split) is now the
                # side that still contains b — reuse it as-is for the next
                # split; edge_split's OWN return value is the piece already
                # finished (cur_a .. new_vert), of no further use here.
                cur_a = new_vert
                t_prev = t

    # The splits are on BOUNDARY edges, so each one turns exactly one face
    # into an n-gon. The new diagonal is interior: it changes no boundary
    # edge, so the desync count below is the same either way -- and it does
    # not inherit ROW_LAYER / KIND_LAYER (verified headless: a new edge from
    # bmesh.ops.triangulate carries 0, not the neighbours' value).
    ngons = [f for f in touched_faces if f.is_valid and len(f.verts) > 3]
    if ngons:
        bmesh.ops.triangulate(bm, faces=ngons, quad_method='BEAUTY',
                              ngon_method='BEAUTY')
        bm.normal_update()

    final = count_seam_desync(bm, basis_layer=basis_layer)
    return {'split': split_total, 'zero_welded': zero_welded_total,
            'retriangulated': len(ngons), 'unresolved': final['desync'],
            'passes': passes_done,
            'nonfinite': final.get('nonfinite', 0)}


def _sanitize_before_inset(bm, flat):
    """Repair any non-finite vertex the mesh arrives with, before an inset
    reads a single position from it. Returns the number repaired.

    A Guide can arrive holding NaN — written by a run from before the guard in
    FlatSession.end() existed, or by anything else that divides by a
    degenerate triangle — and from there the inset does not fail cleanly: it
    dies deep inside (`KeyError: (nan, nan, nan)` in resync_seams, or
    "zero length vectors have no valid angle" in _grow), after the mesh has
    already been edited. Repairing up front costs one pass over the vertices.
    """
    layers = ()
    if flat:
        layers = tuple(lay for lay in
                       (bm.verts.layers.shape.get(n) for n in flat)
                       if lay is not None)
    return sanitize_nonfinite(bm, layers=layers or None)


def inset_pieces(bm, width, sharp_inner=True, progress=None, flat=None):
    """Inset every pattern piece's outline by `width` on the flat shape.

    `flat`: (flat_key_name, basis_key_name) — required. The whole method is a
    2D one (a true offset of the outline polygon), so there is no 3D path;
    the operators refuse a Guide without a planar shape key before they get
    here.

    Per piece, with P the outline polygon (outer loop plus holes):

        Q  = P.buffer(-width, mitre)   the inner row, a TRUE offset
        S  = P - Q                     the strip: outline <-> row
        G  = Q - core                  the gap between the row and the core

    `core` is the original faces that lie entirely inside Q (plus any Inset
    Line band, which is kept whole); every other face is deleted and S and G
    are re-triangulated by shapely's constrained Delaunay, whose only allowed
    vertices are the outline's, the row's and the core's. Nothing is welded,
    so every outline vertex survives and the sewn pairs stay 1:1 by
    construction — the seam desync the old absorb-and-weld method had to
    repair afterwards cannot arise. Concave tips (slits, darts) close by
    themselves: the offset polygon simply has no crossing rows there.

    Returns a stats dict."""
    if not flat:
        raise ValueError("inset_pieces needs a flat shape key: "
                         "flat=(flat_key_name, basis_key_name)")
    require_shapely()
    n_nonfinite = _sanitize_before_inset(bm, flat)
    session = FlatSession(bm, *flat)
    session.enter_2d()
    stats = _inset_pieces_2d(bm, width, sharp_inner=sharp_inner,
                             progress=progress)
    stats['unmapped'] = session.end()
    stats['clamped'] = getattr(session, 'clamped', 0)
    stats['loose_removed'] = getattr(session, 'loose_removed', 0)
    # session.basis / session.flat stay valid past end() (it removes its own
    # int PIECE layer, a different layer domain). resync_seams needs the mesh
    # in the Basis / 3D frame, which is what end() leaves it in. The 2D method
    # is not supposed to desync anything, so this is a safety net that should
    # report zeros — and the stats say so.
    active = (session.active if not session.active_is_basis else None)
    resync = resync_seams(bm, session.basis, session.flat, active_layer=active)
    stats['resync_split'] = resync['split']
    stats['resync_zero'] = resync['zero_welded']
    stats['resync_retri'] = resync['retriangulated']
    stats['resync_unresolved'] = resync['unresolved']
    d = count_seam_desync(bm, basis_layer=session.basis)
    stats['desync'] = d['desync']
    stats['zero_len'] = d['zero_len']
    stats['nonfinite'] = n_nonfinite + getattr(session, 'nonfinite', 0)
    return stats


def _inset_pieces_2d(bm, width, margin=ABSORB_MARGIN, sharp_inner=True,
                     progress=None):
    """Run on a bmesh whose v.co already holds the flat shape (see
    inset_pieces). Returns a Counter-backed stats dict."""
    import shapely
    from shapely.geometry import Polygon, LineString, Point
    from shapely.prepared import prep

    tick = progress if callable(progress) else (lambda f: None)
    rlay = row_layer(bm)
    blay = bm.faces.layers.int.get(BAND_LAYER)
    klay = bm.edges.layers.int.get(KIND_LAYER)
    bm.verts.ensure_lookup_table()
    parts = _pieces(bm)
    stats = Counter()
    stats['pieces'] = len(parts)
    row_edges_all = []
    for k, comp in enumerate(parts):
        bedges = list({e for f in comp for e in f.edges if e.is_boundary})
        if not bedges:
            stats['pieces_no_outline'] += 1
            continue
        bverts = {v for e in bedges for v in e.verts}
        P = _polygon_from_loops(_loops(bedges))
        if P is None or not P.is_valid:
            stats['pieces_invalid_outline'] += 1
            continue
        # face orientation sign of the piece in 2D
        signs = Counter()
        for f in comp[:: max(1, len(comp) // 500)]:
            signs[_signed_area([(v.co.x, v.co.y) for v in f.verts]) > 0] += 1
        ccw = signs[True] >= signs[False]

        Q = P.buffer(-width, join_style='mitre', mitre_limit=MITRE_LIMIT)
        if Q.is_empty:
            stats['pieces_too_thin'] += 1
            continue
        stats['outline_verts'] += len(bverts)
        Qp = P.buffer(-width * margin, join_style='mitre', mitre_limit=MITRE_LIMIT)
        Q_prep = prep(Q)

        # --- core faces: entirely inside Q' (all vertices), and inside Q as a
        #     polygon where a vertex is close enough to the row to matter
        verts = list({v for f in comp for v in f.verts})
        xs = np.fromiter((v.co.x for v in verts), dtype=float, count=len(verts))
        ys = np.fromiter((v.co.y for v in verts), dtype=float, count=len(verts))
        inside_p = (shapely.contains_xy(Qp, xs, ys) if not Qp.is_empty
                    else np.zeros(len(verts), bool))
        vin = {v: bool(b) for v, b in zip(verts, inside_p)}
        # a face far from the row needs no polygon test: anything whose
        # vertices are all deeper than Q' by more than the face can reach.
        deep_lim = width * margin + 3.0 * width
        Qd = P.buffer(-deep_lim, join_style='mitre', mitre_limit=MITRE_LIMIT)
        inside_d = (shapely.contains_xy(Qd, xs, ys) if not Qd.is_empty
                    else np.zeros(len(verts), bool))
        vdeep = {v: bool(b) for v, b in zip(verts, inside_d)}
        core = []
        n_band = 0
        for f in comp:
            fv = f.verts
            if blay is not None and f[blay]:
                # an Inset Line band: the fold's own geometry, kept whole;
                # the strip is built around it (S and G subtract it below)
                core.append(f)
                n_band += 1
                continue
            if not all(vin[v] for v in fv):
                continue
            if all(vdeep[v] for v in fv):
                core.append(f)
                continue
            stats['core_tested'] += 1
            if Q_prep.contains(Polygon([(v.co.x, v.co.y) for v in fv])):
                core.append(f)
        stats['band_faces_kept'] += n_band
        core_set = set(core)
        removed = [f for f in comp if f not in core_set]
        stats['faces_removed'] += len(removed)

        # --- one row vertex per outline vertex (GEOS simplifies the ring
        #     before buffering, see _densify_rows)
        Q = _densify_rows(Q, bverts, width, stats)

        # --- crease edges that lose every face become CONSTRAINTS: the fold's
        #     own edges survive the inset, so a later Inset Line only has to
        #     reach the strip's inner row, never the outline.
        crease_lines = []
        if klay is not None:
            rv = {v for f in removed for v in f.verts}
            seen_e = set()
            for v in rv:
                if not v.is_valid:
                    continue
                for e in v.link_edges:
                    if e in seen_e or e[klay] != KIND_CREASE:
                        continue
                    seen_e.add(e)
                    a, b = e.verts
                    if a in rv and b in rv and not any(f in core_set for f in e.link_faces):
                        pa = (a.co.x, a.co.y)
                        pb = (b.co.x, b.co.y)
                        if pa != pb:
                            crease_lines.append(LineString([pa, pb]))

        # --- the points where a crease crosses Q. The crossing must be a
        #     vertex of Q ITSELF before S and G are cut, so both regions share
        #     the identical coordinate: a fold usually starts at an outline
        #     vertex, whose foot lands a few um from the crossing, and the two
        #     vertices 0.0003 mm apart left a zero-length edge and a crack (62
        #     hole edges measured).
        crossings = []
        if crease_lines:
            xg = Q.boundary.intersection(shapely.union_all(crease_lines))
            for p in shapely.get_parts(xg):
                if p.geom_type == 'Point':
                    crossings.append((p.x, p.y))
        stats['crease_crossings'] += len(crossings)
        if crossings:
            Q = _densify_rows(Q, (), width, stats, priority=crossings)
        stats['q_verts'] += sum(len(r) for qp in _polys(Q) for r in _ring_coords(qp))

        # --- core region from its boundary linework. build_area handles what
        #     a loop walk cannot: a core that touches itself at one vertex
        #     (figure-8, the loop is not simple), nested islands, and several
        #     components sharing a vertex. Walking loops and dropping the
        #     non-simple ones left core faces OUTSIDE core_geom, so G was cut
        #     too large and its triangles overlapped the kept faces (58 hole
        #     edges on a 3.3 mm strap, measured).
        cedges = set()
        for f in core:
            for e in f.edges:
                if sum(1 for x in e.link_faces if x in core_set) == 1:
                    cedges.add(e)
        core_geom = None
        if cedges:
            lines = [LineString([(e.verts[0].co.x, e.verts[0].co.y),
                                 (e.verts[1].co.x, e.verts[1].co.y)]) for e in cedges]
            core_geom = shapely.build_area(shapely.node(shapely.union_all(lines)))
            if core_geom.is_empty:
                core_geom = None
                stats['core_geom_empty'] += 1
            else:
                a_faces = sum(abs(_signed_area([(v.co.x, v.co.y) for v in f.verts]))
                              for f in core)
                if abs(core_geom.area - a_faces) > 1e-9 + 1e-4 * a_faces:
                    stats['core_geom_area_mismatch'] += 1

        S = P.difference(Q)
        G = Q if core_geom is None else Q.difference(core_geom)
        if core_geom is not None and n_band:
            # a band that reaches the outline protrudes into the strip zone
            S = S.difference(core_geom)
        if not S.is_valid:
            S = S.buffer(0)
        if not G.is_valid:
            G = G.buffer(0)

        # --- delete the non-core faces (faces only: outline and core vertices
        #     stay, the loose rest is swept at the end)
        if removed:
            bmesh.ops.delete(bm, geom=removed, context='FACES_ONLY')

        vmap = {}
        for v in verts:
            if v.is_valid:
                vmap[_key(v.co.x, v.co.y)] = v

        stats['crease_constraints'] += len(crease_lines)
        crease_geom = shapely.union_all(crease_lines) if crease_lines else None

        def _regions(region, tag):
            """Sub-regions of `region` split by the crease lines (polygonize
            of the noded boundary + creases), or [region] itself."""
            if crease_geom is None or region.is_empty or not crease_geom.intersects(region):
                return _polys(region)
            rings = []
            for pg in _polys(region):
                rings.append(LineString(list(pg.exterior.coords)))
                for r in pg.interiors:
                    rings.append(LineString(list(r.coords)))
            inside = crease_geom.intersection(region)
            noded = shapely.node(shapely.union_all(rings + list(shapely.get_parts(inside))))
            faces_geom = shapely.polygonize(list(shapely.get_parts(noded)))
            rp = prep(region)
            out = []
            for pg in _polys(faces_geom):
                if pg.area <= 1e-18:
                    stats[tag + '_regions_tiny'] += 1
                    continue
                # polygonize also returns the INTERIOR of every hole ring as a
                # face: those are far from `region` and dropped. A sliver face
                # of the region itself can have its representative point ON
                # the boundary, where contains() is False — keep anything
                # within float32 resolution of the region (11 such slivers per
                # garment measured, each one a hole otherwise).
                rpt = pg.representative_point()
                if rp.contains(rpt) or region.distance(rpt) < F32_TOL:
                    out.append(pg)
                else:
                    stats[tag + '_regions_outside'] += 1
            stats[tag + '_regions'] += len(out)
            cover = sum(pg.area for pg in out)
            if abs(cover - region.area) > 1e-12 + 1e-6 * region.area:
                stats[tag + '_regions_area_gap'] += 1
            return out or _polys(region)

        S_regions = _regions(S, 'S')
        G_regions = _regions(G, 'G')
        # Every boundary coordinate of the regions is a vertex: rows, and the
        # crease / row crossings the noding made. A coordinate the noding
        # moved by a few ulps must NOT become a second vertex next to the
        # first (that is a crack along the row): reuse anything within float32
        # resolution.
        near_list = [v for v in vmap.values() if v.is_valid]
        near_kd = kdtree.KDTree(len(near_list))
        for i, v in enumerate(near_list):
            near_kd.insert(v.co, i)
        near_kd.balance()
        pending = []
        for pg in S_regions + G_regions:
            for ring in _ring_coords(pg):
                for (x, y) in ring:
                    kk = _key(x, y)
                    if kk in vmap:
                        continue
                    _co, i, d = near_kd.find(Vector((x, y, 0.0)))
                    if d is not None and d < F32_TOL:
                        vmap[kk] = near_list[i]
                        stats['ring_vert_reused'] += 1
                        continue
                    hit = None
                    for (px, py, pv) in pending:
                        if abs(px - x) < F32_TOL and abs(py - y) < F32_TOL:
                            hit = pv
                            break
                    if hit is not None:
                        vmap[kk] = hit
                        stats['ring_vert_reused'] += 1
                        continue
                    nv = bm.verts.new((x, y, 0.0))
                    vmap[kk] = nv
                    pending.append((x, y, nv))
                    stats['row_verts'] += 1

        # --- triangulate S and G. Where an earlier band protrudes into the
        #     strip, the row crosses the band's side: those crossing points
        #     are resolved by splitting the band's side edge (EdgeSplitter).
        splitter = None
        if n_band:
            band_edges = {e for f in core if f.is_valid and f[blay] for e in f.edges}
            # ... plus every edge on the core boundary: a crossing point can
            # also sit on the edge of a kept gap face next to the band
            band_edges.update(e for e in cedges if e.is_valid)
            if band_edges:
                # the crossing points GEOS computes sit up to ~30 nm off the
                # band edge (measured); 1 um catches them and is still far
                # below anything the mesh can express
                splitter = EdgeSplitter(band_edges, tol=max(1e-6, width * 1e-3))
        new_faces_S = []
        for pg in S_regions:
            new_faces_S.extend(_cdt_faces(bm, pg, vmap, ccw, stats, 'S', splitter))
        new_faces_G = []
        for pg in G_regions:
            new_faces_G.extend(_cdt_faces(bm, pg, vmap, ccw, stats, 'G', splitter))
        if splitter is not None:
            stats['band_edges_split'] += splitter.splits
            stats['band_faces_retri'] += splitter.finish(bm)
        stats['strip_faces'] += len(new_faces_S)
        stats['gap_faces'] += len(new_faces_G)

        # --- row edges: new edges lying ON the row Q (both ends and the
        #     middle within float32 resolution of Q's boundary); crease edges
        #     re-tagged the same way against the constraint lines.
        #     NOTE: BMVert.co is float32, so a 1 nm test against v.co never
        #     passes — see F32_TOL.
        qb = Q.boundary
        new_edges = {e for f in new_faces_S + new_faces_G for e in f.edges}
        for e in new_edges:
            a, b = e.verts
            pa, pb = (a.co.x, a.co.y), (b.co.x, b.co.y)
            pm = ((pa[0] + pb[0]) * 0.5, (pa[1] + pb[1]) * 0.5)
            if (qb.distance(Point(pa)) < F32_TOL and qb.distance(Point(pb)) < F32_TOL
                    and qb.distance(Point(pm)) < F32_TOL):
                e[rlay] = 1
                if sharp_inner:
                    e.smooth = False
                row_edges_all.append(e)
            elif (crease_geom is not None and klay is not None
                  and crease_geom.distance(Point(pm)) < F32_TOL
                  and crease_geom.distance(Point(pa)) < F32_TOL
                  and crease_geom.distance(Point(pb)) < F32_TOL):
                e[klay] = KIND_CREASE
                stats['crease_edges_kept'] += 1

        # Cracks between S and G (or S and a kept band): a vertex of one side
        # lying on an edge of the other. Only the seams between regions can
        # crack — an edge with one face, or whose two faces come from
        # different regions — and only a vertex that still has faces can be
        # the odd one out: an original vertex of the removed zone that happens
        # to lie on the row (a CLO export with an internal line at exactly
        # Width puts 164 of them there) has no faces, is swept as loose below,
        # and must not split the row.
        set_S = set(new_faces_S)
        set_G = set(new_faces_G)

        def _region_of(f):
            return 0 if f in set_S else (1 if f in set_G else 2)

        cand_edges = []
        for e in new_edges:
            if not e.is_valid or (e.verts[0] in bverts and e.verts[1] in bverts):
                continue
            lf = e.link_faces
            if len(lf) < 2 or _region_of(lf[0]) != _region_of(lf[1]):
                cand_edges.append(e)
        cand_verts = list({v for e in cand_edges for v in e.verts if v.link_faces})
        repair_t_junctions(bm, cand_edges, cand_verts, 1e-6, stats, 'P')
        tick(0.1 + 0.8 * (k + 1) / len(parts))

    # --- sweep: vertices that lost every face, wire edges
    loose_v = [v for v in bm.verts if not v.link_faces]
    stats['loose_verts_removed'] = len(loose_v)
    if loose_v:
        bmesh.ops.delete(bm, geom=loose_v, context='VERTS')
    wire = [e for e in bm.edges if not e.link_faces]
    stats['wire_edges_removed'] = len(wire)
    if wire:
        bmesh.ops.delete(bm, geom=wire, context='EDGES')
    for f in bm.faces:
        f.smooth = True
    bm.normal_update()
    stats['row_edges'] = len(row_edges_all)
    tick(0.95)
    return dict(stats)


def _densify_rows(Q, bverts, width, stats, priority=()):
    """Give the row one vertex per outline vertex again.

    GEOS simplifies the input ring before buffering (vertices that deviate
    from a straight line by less than ~1 % of the distance are dropped), so
    the offset ring comes back with fewer vertices than the outline: 3457 for
    5373 on a jacket. The row's edges would then be long chords across the
    curved 3D surface. Every outline vertex is projected onto the nearest
    ring of Q and the foot inserted as a ring vertex — it lies ON the true
    offset, so it is still exactly `width` from the outline (a mitre corner's
    own vertices stay farther, as intended). Feet closer than 5 % of the
    width to an existing ring vertex are not inserted.

    `priority`: (x, y) points ON the ring that must become ring vertices with
    their exact coordinates (crease crossings). Feet within 25 % of the width
    of a priority point are not inserted."""
    import shapely
    from shapely.geometry import Polygon, Point

    rings = []   # (poly index, ring index, LinearRing)
    polys = _polys(Q)
    for pi, qp in enumerate(polys):
        rings.append((pi, -1, qp.exterior))
        for ri, r in enumerate(qp.interiors):
            rings.append((pi, ri, r))
    if not rings:
        return Q
    inserts = {i: [] for i in range(len(rings))}        # arc positions of feet
    prio = {i: [] for i in range(len(rings))}           # (arc, rank, coord)
    lim = width * 1.5
    for (x, y) in priority:
        p = Point(x, y)
        best = None
        for i, (_pi, _ri, ring) in enumerate(rings):
            d = ring.distance(p)
            if best is None or d < best[0]:
                best = (d, i)
        if best is None or best[0] > 1e-6:
            stats['row_priority_off_ring'] += 1
            continue
        prio[best[1]].append((rings[best[1]][2].project(p), 0, (x, y)))
    for v in bverts:
        p = Point(v.co.x, v.co.y)
        best = None
        for i, (_pi, _ri, ring) in enumerate(rings):
            d = ring.distance(p)
            if d > lim:
                continue
            if best is None or d < best[0]:
                best = (d, i)
        if best is None:
            stats['row_foot_none'] += 1
            continue
        ring = rings[best[1]][2]
        inserts[best[1]].append(ring.project(p))
    tol = width * 0.05
    tol_p = width * 0.25
    new_rings = {}
    for i, (_pi, _ri, ring) in enumerate(rings):
        coords = list(ring.coords)[:-1]
        L = ring.length
        pr = prio[i]
        pr_s = [s for s, _r, _c in pr]

        def near_prio(s, _pr_s=pr_s, _L=L):
            return any(abs(s - q) < tol_p or _L - abs(s - q) < tol_p for q in _pr_s)

        # ranks: 0 a crease crossing, 2 the ring's own vertex, 3 a foot. Feet
        # near a fixed point are not inserted; within `tol` the better rank
        # wins.
        pts = [(ring.project(Point(c)), 2, c) for c in coords]
        pts.extend(pr)
        for s in inserts[i]:
            if near_prio(s):
                stats['row_foot_near_crossing'] += 1
                continue
            pt = ring.interpolate(s)
            pts.append((s, 3, (pt.x, pt.y)))
        pts.sort(key=lambda t: (t[0], t[1]))
        out = []       # (s, rank, c)
        for s, rank, c in pts:
            if out and s - out[-1][0] < tol:
                if rank < out[-1][1]:
                    out[-1] = (s, rank, c)
                stats['row_foot_merged'] += 1
                continue
            out.append((s, rank, c))
        # the ring is closed: the first and the last may be within tol too
        if len(out) > 3 and (L - out[-1][0]) + out[0][0] < tol:
            if out[-1][1] < out[0][1]:
                out[0] = out[-1]
            out.pop()
        new_rings[i] = [c for _s, _r, c in out]
    rebuilt = []
    for pi, qp in enumerate(polys):
        shell = None
        holes = []
        for i, (ppi, ri, _ring) in enumerate(rings):
            if ppi != pi:
                continue
            if ri == -1:
                shell = new_rings[i]
            else:
                holes.append(new_rings[i])
        if shell is None or len(shell) < 3:
            continue
        pg = Polygon(shell, [h for h in holes if len(h) >= 3])
        if not pg.is_valid:
            stats['q_densify_invalid'] += 1
            pg = pg.buffer(0)
        rebuilt.append(pg)
    if not rebuilt:
        return Q
    return shapely.union_all(rebuilt) if len(rebuilt) > 1 else rebuilt[0]


class EdgeSplitter:
    """Resolves a region vertex that is not a mesh vertex yet but lies ON an
    existing edge (a T-junction: the row of Inset Pieces crossing the side of
    an earlier Inset Line band). The edge is split there; the face(s) on it
    become quads and are re-triangulated at the end (`finish`)."""

    def __init__(self, edges, tol=1e-9):
        self.edges = [e for e in edges if e.is_valid]
        self.tol = tol
        self.kd = kdtree.KDTree(len(self.edges))
        maxlen = 0.0
        for i, e in enumerate(self.edges):
            self.kd.insert((e.verts[0].co + e.verts[1].co) * 0.5, i)
            maxlen = max(maxlen, (e.verts[0].co - e.verts[1].co).length)
        self.kd.balance()
        # a point on an edge is at most half its length from the midpoint
        self.radius = 0.5 * maxlen + 1e-6
        self.touched = set()
        self.splits = 0
        self.misses = 0

    def resolve(self, x, y):
        p = Vector((x, y, 0.0))
        best = None
        for (_co, i, _d) in self.kd.find_range(p, self.radius):
            e = self.edges[i]
            if not e.is_valid:
                continue
            a, b = e.verts[0].co, e.verts[1].co
            t = b - a
            L = t.length
            if L < 1e-12:
                continue
            u = (p - a).dot(t) / (L * L)
            if u <= 1e-9 or u >= 1.0 - 1e-9:
                continue
            dd = (p - (a + t * u)).length
            if best is None or dd < best[0]:
                best = (dd, e, u)
        if best is None or best[0] > self.tol:
            self.misses += 1
            return None
        _dd, e, u = best
        self.touched.update(f for f in e.link_faces)
        ne, nv = bmesh.utils.edge_split(e, e.verts[0], u)
        nv.co = p
        self.edges.append(ne)
        self.splits += 1
        return nv

    def finish(self, bm):
        polys = [f for f in self.touched if f.is_valid and len(f.verts) > 3]
        if polys:
            bmesh.ops.triangulate(bm, faces=polys, quad_method='BEAUTY',
                                  ngon_method='BEAUTY')
        return len(polys)


def repair_t_junctions(bm, edges, verts, tol, stats, tag):
    """Split every edge in `edges` at each vertex of `verts` that lies on its
    interior (within `tol`), then re-triangulate the faces the splits turned
    into quads. Returns the number of splits.

    Safety net for the region method: two regions that share a boundary curve
    must agree on its vertices. They do by construction (the row is densified
    once, crease crossings are ring vertices before S and G are cut), but GEOS
    overlay can still hand one region a vertex the other does not have —
    measured 3 edges per garment after the fixes above: an A-B edge on one
    side against A-X-B on the other, X on AB within 1 um. Both sides then have
    a boundary edge there (a crack)."""
    pts = [v for v in verts if v.is_valid]
    if not pts:
        return 0
    kd = kdtree.KDTree(len(pts))
    for i, v in enumerate(pts):
        kd.insert(v.co, i)
    kd.balance()
    touched = set()
    splits = 0
    stack = [e for e in edges if e.is_valid]
    guard = 0
    while stack and guard < 10 * len(edges) + 100:
        guard += 1
        e = stack.pop()
        if not e.is_valid:
            continue
        a, b = e.verts[0], e.verts[1]
        t = b.co - a.co
        L = t.length
        if L < 1e-12:
            continue
        mid = (a.co + b.co) * 0.5
        hit = None
        for (_co, i, _d) in kd.find_range(mid, 0.5 * L + tol):
            v = pts[i]
            if v is a or v is b or not v.is_valid:
                continue
            u = (v.co - a.co).dot(t) / (L * L)
            if u <= 1e-6 or u >= 1.0 - 1e-6:
                continue
            if (v.co - (a.co + t * u)).length <= tol:
                hit = (u, v)
                break
        if hit is None:
            continue
        u, v = hit
        kind = 'crack' if len(e.link_faces) < 2 else '2faced'
        stats[tag + '_tjunction_' + kind] += 1
        touched.update(e.link_faces)
        ne, nv = bmesh.utils.edge_split(e, a, u)
        # the split made a new vertex ON the crack; weld it onto the existing
        # one so both sides share a single vertex
        bmesh.ops.weld_verts(bm, targetmap={nv: v})
        splits += 1
        stats[tag + '_tjunction_split'] += 1
        for x in v.link_edges:
            if x.is_valid and (x.other_vert(v) is a or x.other_vert(v) is b):
                stack.append(x)
    polys = [f for f in touched if f.is_valid and len(f.verts) > 3]
    if polys:
        bmesh.ops.triangulate(bm, faces=polys, quad_method='BEAUTY',
                              ngon_method='BEAUTY')
    return splits


def _cdt_faces(bm, region, vmap, ccw, stats, tag, splitter=None):
    """Constrained Delaunay of `region`; every triangle vertex must already be
    in `vmap` (no Steiner points), or lie on an edge `splitter` may split.
    Returns the new faces."""
    import shapely

    faces = []
    if region is None or region.is_empty:
        return faces
    tris = shapely.constrained_delaunay_triangles(region)
    # Fallback for a coordinate whose 1 nm key does not match although the
    # vertex is there (a GEOS output coordinate can differ from the input in
    # the last bits, and rounding then flips a digit — measured: a miss with
    # the nearest vertex at 0.00 um).
    near_kd = None
    near_list = None

    def _nearest(x, y):
        nonlocal near_kd, near_list
        if near_kd is None:
            near_list = [v for v in vmap.values() if v.is_valid]
            near_kd = kdtree.KDTree(len(near_list))
            for i, v in enumerate(near_list):
                near_kd.insert(v.co, i)
            near_kd.balance()
        _co, i, d = near_kd.find(Vector((x, y, 0.0)))
        if d is not None and d < 1e-7:
            stats[tag + '_key_fallback'] += 1
            return near_list[i]
        return None

    for t in _polys(tris):
        cs = list(t.exterior.coords)[:-1]
        if len(cs) != 3:
            stats[tag + '_non_tri'] += 1
            continue
        vs = []
        ok = True
        for (x, y) in cs:
            v = vmap.get(_key(x, y))
            if v is None:
                v = _nearest(x, y)
                if v is not None:
                    vmap[_key(x, y)] = v
            if v is None and splitter is not None:
                v = splitter.resolve(x, y)
                if v is not None:
                    vmap[_key(x, y)] = v
                    stats[tag + '_split'] += 1
            if v is None:
                ok = False
                break
            vs.append(v)
        if not ok:
            stats[tag + '_steiner'] += 1
            continue
        # new vertices all carry index -1 until index_update: compare identity
        if len(set(map(id, vs))) < 3:
            stats[tag + '_degenerate'] += 1
            continue
        if (_signed_area(cs) > 0) != ccw:
            vs.reverse()
        try:
            f = bm.faces.new(vs)
        except ValueError:
            stats[tag + '_dup_face'] += 1
            continue
        faces.append(f)
    return faces


# ---------------------------------------------------------------------------
# fold-line repair. No longer part of either inset (the 2D method keeps the
# fold's own edges instead of rebuilding them); kept for callers that want to
# close the gaps in a hand-selected line.

def _diagonal_between(a, b, exclude):
    """The edge c1-c2 whose two triangles are exactly {a,c1,c2} and {b,c1,c2},
    or None. Flipping that edge yields the edge a-b.

    `exclude`: vertices that may not be a diagonal endpoint — the line's own
    vertices. Without this, a-m-b (m the line vertex between a and b, c a
    side vertex) would match through the diagonal m-c and the flip would
    bypass m with a degenerate sliver."""
    na = {e.other_vert(a) for e in a.link_edges}
    nb = {e.other_vert(b) for e in b.link_edges}
    common = [c for c in (na & nb) if c not in exclude]
    for i in range(len(common)):
        for j in range(i + 1, len(common)):
            e = None
            for le in common[i].link_edges:
                if le.other_vert(common[i]) is common[j]:
                    e = le
                    break
            if e is None or len(e.link_faces) != 2:
                continue
            fv = {v for f in e.link_faces for v in f.verts}
            if a in fv and b in fv and len(fv) == 4:
                return e
    return None


def _on_crease(v, min_dihedral, exclude=None):
    """True when `v` has a creased edge other than `exclude` — i.e. the fold
    line continues through it. Side vertices on flat cloth have none."""
    for e in v.link_edges:
        if e is exclude:
            continue
        if _dihedral(e) >= min_dihedral:
            return True
    return False


def _usable_dir(vec):
    """True when `vec` has a real, non-zero length — i.e. .normalized() and
    .angle() will answer. Guards both the degenerate (zero) and the
    non-finite (NaN) case; see co_finite."""
    L = vec.length
    return math.isfinite(L) and L >= 1e-12


def _grow(chain, chainset, min_dihedral, max_steps=2000):
    """Extend an ordered chain [.., prev, cur] along the fold line.

    Step rule: the link edge that best continues the direction (within 35°)
    and has a crease of at least `min_dihedral`; failing that, a 2-hop jump
    (within 15°, not longer than 2.6x the last step) over a missing edge.
    Either way the new vertex must itself carry a creased edge onward, so the
    walk cannot slip onto a side vertex (e.g. a parallel internal line)."""
    for _ in range(max_steps):
        cur, prev = chain[-1], chain[-2]
        step = cur.co - prev.co
        # `nan < 1e-12` is False, so a non-finite position used to slip past
        # this and raise "zero length vectors have no valid angle" two lines
        # down (2026-09-09). See co_finite / sanitize_nonfinite.
        if not _usable_dir(step):
            break
        dirn = step.normalized()
        best = None
        for e in cur.link_edges:
            o = e.other_vert(cur)
            if o in chainset:
                continue
            d = o.co - cur.co
            if not _usable_dir(d):
                continue
            ang = dirn.angle(d.normalized())
            if (ang < math.radians(35) and _dihedral(e) >= min_dihedral
                    and _on_crease(o, min_dihedral, exclude=e)):
                if best is None or ang < best[0]:
                    best = (ang, o)
        if best is None:
            for e in cur.link_edges:
                o = e.other_vert(cur)
                for e2 in o.link_edges:
                    o2 = e2.other_vert(o)
                    if o2 in chainset or o2 is cur:
                        continue
                    d = o2.co - cur.co
                    if not _usable_dir(d) or d.length > 2.6 * step.length:
                        continue
                    ang = dirn.angle(d.normalized())
                    if ang < math.radians(15) and _on_crease(o2, min_dihedral):
                        if best is None or ang < best[0]:
                            best = (ang, o2)
        if best is None:
            break
        chain.append(best[1])
        chainset.add(best[1])
    return chain


def _extend_selection(bm, verts, min_dihedral):
    """Grow outward from the two ends of the selected run. An end is a
    selected vertex with exactly one other selected vertex within jumping
    distance (proximity, not edges — the run may have missing edges); it
    grows away from that neighbour. Returns the enlarged set."""
    sel = [v for v in verts if co_finite(v.co)]
    grown = set(verts)
    lens = sorted(L for L in (e.calc_length() for v in sel for e in v.link_edges)
                  if math.isfinite(L))
    if not lens:
        return grown
    radius = 2.6 * lens[len(lens) // 2]
    kd = kdtree.KDTree(len(sel))
    for i, v in enumerate(sel):
        kd.insert(v.co, i)
    kd.balance()
    for i, v in enumerate(sel):
        nbrs = [sel[j] for (_co, j, _d) in kd.find_range(v.co, radius) if j != i]
        if len(nbrs) != 1:
            continue
        chain = [nbrs[0], v]
        _grow(chain, grown, min_dihedral)
    return grown


def repair_fold_lines(bm, verts, extend=False, min_dihedral_deg=6.0):
    """Restore missing edges between fold-line vertices by flipping the
    diagonal that crosses the line.

    `verts`: the fold-line vertices (selected by the user). With `extend` the
    line is first walked outward from the selection.
    Returns (gaps_found, gaps_fixed, line_vertex_count)."""
    verts = [v for v in verts if v.is_valid]
    if len(verts) < 2:
        return 0, 0, len(verts)
    chain = set(verts)
    if extend:
        chain = _extend_selection(bm, verts, math.radians(min_dihedral_deg))
    cl = list(chain)
    lens = sorted(e.calc_length() for v in cl for e in v.link_edges)
    if not lens:
        return 0, 0, len(cl)
    radius = 2.6 * lens[len(lens) // 2]
    kd = kdtree.KDTree(len(cl))
    for i, v in enumerate(cl):
        kd.insert(v.co, i)
    kd.balance()
    gaps = fixed = 0
    for i, a in enumerate(cl):
        for (_co, j, _d) in kd.find_range(a.co, radius):
            if j <= i:
                continue
            b = cl[j]
            if not a.is_valid or not b.is_valid:
                continue
            if bm.edges.get([a, b]) is not None:
                continue
            e = _diagonal_between(a, b, chain)
            if e is None:
                continue
            gaps += 1
            bmesh.ops.rotate_edges(bm, edges=[e], use_ccw=False)
            if bm.edges.get([a, b]) is not None:
                fixed += 1
    return gaps, fixed, len(cl)


# ---------------------------------------------------------------------------
# 3. inset line (an internal fold line, e.g. a lapel roll line)

def inset_line(bm, edges, width, progress=None, flat=None, sharp_outer=True):
    """Inset a fold line to both sides: the same region method as
    inset_pieces, applied to a line inside a pattern piece.

    `edges`: the fold line as a set of EDGES; each connected component is one
    line. The caller decides what the line is (the selected edges, or the
    crease edges Find Folds tagged) — nothing here reads 3D angles, walks
    outward or repairs missing edges.
    `flat`: (flat_key_name, basis_key_name) — required, as for inset_pieces.

    The band is the true two-sided offset of the line at `width` (flat caps,
    mitre joins), clipped by the piece; the line's own vertices and edges stay
    exactly where they are (they ARE the fold). Run AFTER Inset Pieces: the
    piece then carries a row (ROW_LAYER) one width in from its outline, and
    the band stops at that row instead of at the outline, so the strip is
    never touched. Without a row the band runs to the outline and splits the
    outline edge it lands on together with its sewn twin, at the same
    parameter, so the 1:1 pairing survives.

    Returns a stats dict."""
    if not flat:
        raise ValueError("inset_line needs a flat shape key: "
                         "flat=(flat_key_name, basis_key_name)")
    require_shapely()
    n_nonfinite = _sanitize_before_inset(bm, flat)
    # Element references die when a layer is added: take the edge INDICES
    # first, make sure every layer exists, then resolve them again.
    bm.edges.index_update()
    bm.edges.ensure_lookup_table()
    idx = [e.index for e in edges if e.is_valid]
    row_layer(bm)
    kind_layer(bm)
    band_layer(bm)
    session = FlatSession(bm, *flat)
    session.enter_2d()
    bm.edges.ensure_lookup_table()
    n_edges = len(bm.edges)
    chain = [bm.edges[i] for i in idx if 0 <= i < n_edges]
    stats = _inset_line_2d(bm, chain, width, session.basis,
                           sharp_outer=sharp_outer, progress=progress)
    stats['unmapped'] = session.end()
    stats['clamped'] = getattr(session, 'clamped', 0)
    stats['loose_removed'] = getattr(session, 'loose_removed', 0)
    active = (session.active if not session.active_is_basis else None)
    resync = resync_seams(bm, session.basis, session.flat, active_layer=active)
    stats['resync_split'] = resync['split']
    stats['resync_zero'] = resync['zero_welded']
    stats['resync_retri'] = resync['retriangulated']
    stats['resync_unresolved'] = resync['unresolved']
    d = count_seam_desync(bm, basis_layer=session.basis)
    stats['desync'] = d['desync']
    stats['zero_len'] = d['zero_len']
    stats['nonfinite'] = n_nonfinite + getattr(session, 'nonfinite', 0)
    return stats


def _inset_line_2d(bm, chain_edges, width, basis_lay, margin=ABSORB_MARGIN,
                   sharp_outer=True, progress=None):
    """Run on a bmesh whose v.co already holds the flat shape (see
    inset_line). `basis_lay`: the 3D shape layer, used to find sewn twins."""
    import shapely
    from shapely.geometry import Polygon, LineString, MultiLineString, Point
    from shapely.prepared import prep

    tick = progress if callable(progress) else (lambda f: None)
    rlay = row_layer(bm)
    klay = kind_layer(bm)
    blay = band_layer(bm)
    bm.verts.ensure_lookup_table()
    stats = Counter()
    # sewn twins: boundary vertices grouped by 3D position
    twin_index = {}
    for v in bm.verts:
        if v.is_boundary:
            twin_index.setdefault(tuple(round(c, 7) for c in v[basis_lay]), []).append(v)
    placers = []
    chain_edges = [e for e in chain_edges if e.is_valid and len(e.link_faces) == 2]
    comps = _edge_components(chain_edges)
    stats['lines'] = len(comps)
    parts = _pieces(bm)
    piece_of = {}
    for k, comp in enumerate(parts):
        for f in comp:
            piece_of[f] = k
    piece_cache = {}

    def _orientation(comp):
        signs = Counter()
        for f in comp[:: max(1, len(comp) // 500)]:
            signs[_signed_area([(v.co.x, v.co.y) for v in f.verts]) > 0] += 1
        return signs[True] >= signs[False]

    def piece_data(k):
        """(clip polygon, placer, its vertices, ccw, clipped-to-row)."""
        if k in piece_cache:
            return piece_cache[k]
        comp = parts[k]
        bedges = list({e for f in comp for e in f.edges if e.is_boundary})
        bverts = list({v for e in bedges for v in e.verts})
        # After Inset Pieces the piece has a strip along its outline whose
        # inner row is tagged ROW: the band then stops at that row (the strip
        # is left alone; the fold's edges inside it were kept by Inset Pieces
        # as constraints). Corners on the row snap to a row vertex or split
        # the row edge — an unsewn edge, so no twin has to follow.
        redges = list({e for f in comp for e in f.edges if e[rlay] == 1})
        if redges:
            rverts = list({v for e in redges for v in e.verts})
            Pq = _polygon_from_loops(_loops(redges))
            if Pq is not None and Pq.is_valid:
                stats['pieces_clipped_to_row'] += 1
                placer = OutlinePlacer(bm, redges, rverts, basis_lay, width, {}, stats)
                placers.append(placer)
                d = (Pq, placer, rverts, _orientation(comp), True)
                piece_cache[k] = d
                return d
            stats['pieces_row_loops_bad'] += 1
        P = _polygon_from_loops(_loops(bedges)) if bedges else None
        placer = OutlinePlacer(bm, bedges, bverts, basis_lay, width, twin_index, stats)
        placers.append(placer)
        d = (P, placer, bverts, _orientation(comp), False)
        piece_cache[k] = d
        return d

    # --- per piece: chain edges, band region, absorb zone
    by_piece = {}
    for comp in comps:
        if len(comp) < 2:
            stats['lines_too_short'] += 1
            continue
        k = piece_of.get(comp[0].link_faces[0])
        if k is None:
            stats['lines_no_piece'] += 1
            continue
        segs = [LineString([(e.verts[0].co.x, e.verts[0].co.y),
                            (e.verts[1].co.x, e.verts[1].co.y)]) for e in comp]
        merged = shapely.line_merge(MultiLineString(segs))
        lines = [merged] if merged.geom_type == 'LineString' else list(merged.geoms)
        if sum(l.length for l in lines) < width * 2.0:
            stats['lines_too_short'] += 1
            continue
        if len(lines) > 1:
            stats['lines_branching'] += 1
        bands = [_band_polygon(l, width, stats) for l in lines]
        zones = [l.buffer(width * margin, cap_style='flat', join_style='mitre',
                          mitre_limit=MITRE_LIMIT) for l in lines]
        d = by_piece.setdefault(k, {'edges': [], 'bands': [], 'zones': [], 'chain': set()})
        d['edges'].extend(comp)
        d['bands'].extend(bands)
        d['zones'].extend(zones)
        d['chain'].update(v for e in comp for v in e.verts)
        stats['lines_used'] += 1
    if not by_piece:
        return dict(stats)
    tick(0.2)

    row_edges_all = []
    for n_done, (k, d) in enumerate(by_piece.items()):
        P, placer, bverts, ccw, clipped = piece_data(k)
        if P is None:
            stats['lines_no_piece'] += 1
            continue
        chain_set = d['chain']
        chain_pts = [(v.co.x, v.co.y) for v in chain_set]
        vmap_piece = {_key(v.co.x, v.co.y): v for v in bverts}
        R = shapely.union_all(d['bands']).intersection(P)
        if not R.is_valid:
            R = R.buffer(0)
        # rings of R, noded with the chain ends and placed on the outline
        ring_lines = []
        ring_keysets = []
        for pg in _polys(R):
            for ring in _ring_coords(pg):
                if len(ring) < 3:
                    continue
                ring = _densify_ring(ring, chain_pts, width, stats)
                ring = _snap_to_outline(ring, P.boundary, placer, vmap_piece, stats)
                if len(ring) < 3:
                    continue
                ring_lines.append(LineString(ring + [ring[0]]))
                ring_keysets.append([_key(*c) for c in ring])
        if not ring_lines:
            stats['band_empty'] += 1
            continue
        R2 = shapely.polygonize(ring_lines)
        R2 = shapely.union_all(list(R2.geoms)) if hasattr(R2, 'geoms') else R2
        chain_lines = [LineString([(e.verts[0].co.x, e.verts[0].co.y),
                                   (e.verts[1].co.x, e.verts[1].co.y)])
                       for e in d['edges']]
        # polygonize needs the linework split at every node: a ring that
        # merely PASSES THROUGH a chain end as one of its vertices is still
        # one edge to it and does not get split (measured: 1 face instead of
        # 2). shapely.node cuts the rings at the chain ends.
        noded = shapely.node(shapely.union_all(ring_lines + chain_lines))
        faces_geom = shapely.polygonize(list(shapely.get_parts(noded)))
        R_prep = prep(R2)
        regions = []
        for pg in _polys(faces_geom):
            if pg.area < 1e-18:
                continue
            if R_prep.contains(pg.representative_point()):
                regions.append(pg)
        stats['band_regions'] += len(regions)
        if not regions:
            stats['band_empty'] += 1
            continue
        band_geom = shapely.union_all(regions)
        zone_geom = shapely.union_all(d['zones'])
        band_prep = prep(band_geom)
        zone_prep = prep(zone_geom)
        # --- faces to delete
        reach = width * margin + 3.0 * width
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        near = _surface_band(bm, list(chain_set), reach)
        cand = {f for v in near for f in v.link_faces if piece_of.get(f) == k}
        removed = []
        for f in cand:
            if f[blay]:
                continue
            if clipped and any(v.is_boundary for v in f.verts):
                continue    # the strip of Inset Pieces is left alone
            hit = False
            for v in f.verts:
                if v in chain_set or v.is_boundary:
                    continue
                if zone_prep.contains(Point(v.co.x, v.co.y)):
                    hit = True
                    break
            if not hit:
                tri = Polygon([(v.co.x, v.co.y) for v in f.verts])
                if band_prep.intersects(tri) and band_geom.intersection(tri).area > 1e-16:
                    hit = True
            if hit:
                removed.append(f)
        stats['faces_removed'] += len(removed)
        if not removed:
            stats['band_no_faces'] += 1
            continue
        hole = shapely.union_all([Polygon([(v.co.x, v.co.y) for v in f.verts])
                                  for f in removed])
        verts = list({v for f in removed for v in f.verts})
        bmesh.ops.delete(bm, geom=removed, context='FACES_ONLY')
        vmap = {}
        for v in verts:
            if v.is_valid:
                vmap[_key(v.co.x, v.co.y)] = v
        for v in bverts:
            if v.is_valid:
                vmap[_key(v.co.x, v.co.y)] = v
        vmap.update({kk: v for kk, v in vmap_piece.items() if v.is_valid})
        for keys, line in zip(ring_keysets, ring_lines):
            for kk, (x, y) in zip(keys, list(line.coords)[:-1]):
                if kk not in vmap:
                    vmap[kk] = bm.verts.new((x, y, 0.0))
                    stats['row_verts'] += 1
        gap = hole.difference(band_geom)
        if not gap.is_valid:
            gap = gap.buffer(0)
        band_faces = []
        for pg in regions:
            band_faces.extend(_cdt_faces(bm, pg, vmap, ccw, stats, 'B'))
        gap_faces = _cdt_faces(bm, gap, vmap, ccw, stats, 'R')
        for f in band_faces:
            f[blay] = 1
        stats['band_faces'] += len(band_faces)
        stats['gap_faces'] += len(gap_faces)
        # rows = ring edges that are neither chain edges nor outline edges
        for keys in ring_keysets:
            n = len(keys)
            for i in range(n):
                a = vmap.get(keys[i])
                b = vmap.get(keys[(i + 1) % n])
                if a is None or b is None or a is b:
                    continue
                if a in chain_set and b in chain_set:
                    continue
                if a.is_boundary and b.is_boundary:
                    continue
                e = bm.edges.get([a, b])
                if e is None:
                    stats['row_edges_missing'] += 1
                    continue
                e[rlay] = 1
                if sharp_outer:
                    e.smooth = False
                row_edges_all.append(e)
        for f in band_faces:
            for e in f.edges:
                if not (e.verts[0] in chain_set and e.verts[1] in chain_set):
                    e[klay] = KIND_NONE
        # cracks between the band, the gap ring and the untouched faces
        # around (see repair_t_junctions)
        new_edges = {e for f in band_faces + gap_faces if f.is_valid for e in f.edges}
        # ... and the edges of the surviving faces around the hole (the
        # strip's row edges included): a band corner placed on the row can
        # leave the row edge on the strip side unsplit
        around = {e for f in cand if f.is_valid for e in f.edges}
        set_B = set(band_faces)
        set_R = set(gap_faces)

        def _region_of(f):
            return 0 if f in set_B else (1 if f in set_R else 2)

        outline = {v for e in new_edges | around if e.is_valid for v in e.verts
                   if v.is_boundary and not any(x[rlay] for x in v.link_edges)}
        edges_all = []
        for e in new_edges | around:
            if not e.is_valid or (e.verts[0] in outline and e.verts[1] in outline):
                continue
            lf = e.link_faces
            if len(lf) < 2 or _region_of(lf[0]) != _region_of(lf[1]):
                edges_all.append(e)
        near_verts = list({v for e in edges_all for v in e.verts if v.link_faces})
        repair_t_junctions(bm, edges_all, near_verts, 1e-6, stats, 'L')
        tick(0.2 + 0.7 * (n_done + 1) / len(by_piece))
    stats['row_edges'] = len(row_edges_all)
    for pl in placers:
        stats['outline_split_retri'] += pl.finish()
    loose_v = [v for v in bm.verts if not v.link_faces]
    stats['loose_verts_removed'] = len(loose_v)
    if loose_v:
        bmesh.ops.delete(bm, geom=loose_v, context='VERTS')
    wire = [e for e in bm.edges if not e.link_faces]
    stats['wire_edges_removed'] = len(wire)
    if wire:
        bmesh.ops.delete(bm, geom=wire, context='EDGES')
    for f in bm.faces:
        f.smooth = True
    bm.normal_update()
    tick(0.95)
    return dict(stats)


def _band_polygon(line, width, stats):
    """The band of `line`: its two offset curves at +-W, each densified with a
    foot per chain vertex, closed with flat caps.

    buffer() gives the same outline, but the feet were then added by
    projecting each chain vertex onto the WHOLE ring, and a vertex exactly
    midway between the two sides projects onto one side only: the other side
    kept its 2-3 simplified vertices and the CDT fanned the entire band from
    them (78 mm edges, a degree-79 vertex, measured)."""
    from shapely.geometry import Polygon

    chain_pts = list(line.coords)
    sides = []
    for sign in (+1.0, -1.0):
        oc = line.offset_curve(sign * width, join_style='mitre', mitre_limit=MITRE_LIMIT)
        if oc.is_empty or oc.geom_type != 'LineString' or len(oc.coords) < 2:
            sides = None
            break
        sides.append(_densify_side(oc, chain_pts, width, stats))
    fallback = line.buffer(width, cap_style='flat', join_style='mitre',
                           mitre_limit=MITRE_LIMIT)
    if sides is None:
        stats['band_buffer_fallback'] += 1
        return fallback
    left, right = sides
    # the two curves may run in the same or in opposite directions depending
    # on the GEOS version: try both closures, keep the valid one whose area
    # matches the buffer
    best = None
    for r in (list(reversed(right)), list(right)):
        ring = left + r
        if len(ring) < 3:
            continue      # a side collapsed to one point (a very short chain)
        pg = Polygon(ring)
        if pg.is_valid:
            err = abs(pg.area - fallback.area)
            if best is None or err < best[0]:
                best = (err, pg)
    if best is None or best[0] > 0.05 * fallback.area:
        stats['band_buffer_fallback'] += 1
        return fallback
    return best[1]


def _densify_side(side, chain_pts, width, stats):
    """One offset curve of a chain, with the foot of EVERY chain vertex
    inserted (merged within 5 % of W). Returns the coordinate list."""
    from shapely.geometry import Point

    pts = [(side.project(Point(c)), c) for c in side.coords]
    for c in chain_pts:
        p = Point(c)
        if side.distance(p) > width * 1.5:
            continue
        s = side.project(p)
        q = side.interpolate(s)
        pts.append((s, (q.x, q.y)))
    pts.sort(key=lambda t: t[0])
    tol = width * 0.05
    out = []
    last = None
    for s, c in pts:
        if last is not None and s - last < tol:
            stats['side_foot_merged'] += 1
            continue
        out.append(c)
        last = s
    return out


def _densify_ring(ring_coords, chain_pts, width, stats):
    """Insert the foot of every nearby chain vertex onto the ring (GEOS
    simplifies before buffering, so the offset comes back with fewer vertices
    than the chain). A foot within 1 nm of a chain vertex — a chain END, which
    lies on the flat cap — is replaced by the chain vertex's exact coordinate
    so the polygonize is properly noded there."""
    from shapely.geometry import LineString, Point

    ring = LineString(list(ring_coords) + [ring_coords[0]])
    L = ring.length
    chain_set = set(chain_pts)
    pts = [(ring.project(Point(c)), c) for c in ring_coords]
    for c in chain_pts:
        p = Point(c)
        d = ring.distance(p)
        if d > width * 1.5:
            continue
        s = ring.project(p)
        if d < 1e-9:
            pts.append((s, c))          # exact: the chain end on the cap
            stats['line_end_noded'] += 1
        else:
            q = ring.interpolate(s)
            pts.append((s, (q.x, q.y)))
    pts.sort(key=lambda t: t[0])
    tol = width * 0.05
    out = []
    last = None
    for s, c in pts:
        if last is not None and s - last[0] < tol:
            # keep an exact chain coordinate over an interpolated foot
            if c in chain_set and last[1] not in chain_set:
                out[-1] = c
                last = (s, c)
            else:
                stats['line_foot_merged'] += 1
            continue
        out.append(c)
        last = (s, c)
    if len(out) > 3 and (L - pts[-1][0]) + pts[0][0] < tol and out[0] not in chain_set:
        out.pop(0)
    return out


def _snap_to_outline(coords, P_boundary, placer, vmap, stats):
    """Ring coordinates that lie on the clip boundary but are not vertices of
    it (the band was clipped by an outline / row edge) are placed on it by
    `placer` (snap to a near vertex, else a paired split); the coordinate is
    replaced by the vertex's exact position and the vertex is registered in
    `vmap`."""
    from shapely.geometry import Point

    out = []
    for (x, y) in coords:
        if _key(x, y) in vmap:
            out.append((x, y))
            continue
        if P_boundary.distance(Point(x, y)) < 1e-9:
            v = placer.place(x, y)
            if v is not None:
                vmap[_key(v.co.x, v.co.y)] = v
                out.append((v.co.x, v.co.y))
                continue
        out.append((x, y))
    dedup = []
    for c in out:
        if not dedup or _key(*c) != _key(*dedup[-1]):
            dedup.append(c)
    if len(dedup) > 1 and _key(*dedup[0]) == _key(*dedup[-1]):
        dedup.pop()
    return dedup


class OutlinePlacer:
    """Puts a band corner ON the piece's clip boundary without breaking the
    sewn 1:1 pairs.

    A band clipped by the boundary ends with two corners on boundary edges.
    If a boundary vertex is within `snap` of the corner, the corner is moved
    onto it (a T-junction is never left). Otherwise the edge is split there —
    and so is its sewn TWIN on the neighbouring piece (the boundary edge
    whose two ends coincide with this one's in 3D), at the same parameter, so
    both sides gain one vertex at the same 3D point and the pairing stays
    exact. A free edge (no twin), and every row edge (the clip boundary after
    Inset Pieces, which is sewn to nothing), is simply split. Faces turned
    into quads by a split are re-triangulated by `finish`."""

    def __init__(self, bm, bedges, bverts, basis_lay, width, twin_index, stats):
        self.bm = bm
        self.stats = stats
        self.basis = basis_lay
        self.twins = twin_index
        self.snap = width * 0.5
        self.bverts = list(bverts)
        self.vkd = kdtree.KDTree(len(self.bverts))
        for i, v in enumerate(self.bverts):
            self.vkd.insert(v.co, i)
        self.vkd.balance()
        self.edges = [e for e in bedges if e.is_valid]
        self.ekd = kdtree.KDTree(len(self.edges))
        maxlen = 0.0
        for i, e in enumerate(self.edges):
            self.ekd.insert((e.verts[0].co + e.verts[1].co) * 0.5, i)
            maxlen = max(maxlen, e.calc_length())
        self.ekd.balance()
        self.radius = 0.5 * maxlen + 1e-6
        self.touched = set()

    def _bkey(self, v):
        return tuple(round(c, 7) for c in v[self.basis])

    def place(self, x, y):
        """Vertex for an on-boundary ring coordinate, or None."""
        p = Vector((x, y, 0.0))
        _co, i, d = self.vkd.find(p)
        if d is not None and d <= self.snap:
            self.stats['line_end_snapped'] += 1
            return self.bverts[i]
        best = None
        for (_c, i, _d) in self.ekd.find_range(p, self.radius):
            e = self.edges[i]
            if not e.is_valid:
                continue
            a, b = e.verts[0].co, e.verts[1].co
            t = b - a
            L = t.length
            if L < 1e-12:
                continue
            u = (p - a).dot(t) / (L * L)
            if u <= 1e-6 or u >= 1.0 - 1e-6:
                continue
            dd = (p - (a + t * u)).length
            if best is None or dd < best[0]:
                best = (dd, e, u)
        if best is None or best[0] > 1e-6:
            self.stats['line_end_unplaced'] += 1
            return None
        _dd, e, u = best
        va, vb = e.verts[0], e.verts[1]
        # the twin: a boundary edge whose ends coincide with va / vb in 3D
        twin = None
        for ta in self.twins.get(self._bkey(va), ()):
            if ta is va or not ta.is_valid:
                continue
            for te in ta.link_edges:
                if not te.is_boundary:
                    continue
                tb = te.other_vert(ta)
                if tb is not vb and self._bkey(tb) == self._bkey(vb):
                    twin = (te, ta)
                    break
            if twin:
                break
        self.touched.update(e.link_faces)
        ne, nv = bmesh.utils.edge_split(e, va, u)
        nv.co = p
        nv[self.basis] = va[self.basis].lerp(vb[self.basis], u)
        self.edges.append(ne)
        if twin is not None:
            te, ta = twin
            tb = te.other_vert(ta)
            self.touched.update(te.link_faces)
            tne, tnv = bmesh.utils.edge_split(te, ta, u)
            tnv.co = ta.co.lerp(tb.co, u)
            tnv[self.basis] = ta[self.basis].lerp(tb[self.basis], u)
            self.twins.setdefault(self._bkey(tnv), []).append(tnv)
            self.stats['outline_split_pairs'] += 1
        else:
            self.stats['outline_split_free'] += 1
        self.twins.setdefault(self._bkey(nv), []).append(nv)
        return nv

    def finish(self):
        polys = [f for f in self.touched if f.is_valid and len(f.verts) > 3]
        if polys:
            bmesh.ops.triangulate(self.bm, faces=polys, quad_method='BEAUTY',
                                  ngon_method='BEAUTY')
        return len(polys)


# ---------------------------------------------------------------------------
# 1. tagging (Find Folds)

def tag_creases_by_angle(bm, min_angle_deg):
    """Tag 2-face edges with dihedral >= min angle as creases. Returns
    tagged count."""
    lay = kind_layer(bm)
    thr = math.radians(min_angle_deg)
    n = 0
    for e in bm.edges:
        if _dihedral(e) >= thr:
            e[lay] = KIND_CREASE
            n += 1
    return n


def tag_edges(bm, edges, kind):
    lay = kind_layer(bm)
    for e in edges:
        e[lay] = kind
    return len(edges)


def clear_tags(bm):
    lay = bm.edges.layers.int.get(KIND_LAYER)
    if lay is not None:
        bm.edges.layers.int.remove(lay)
        return True
    return False
