"""Core algorithms for CLO Cleanup. bmesh / mathutils, no bpy.ops and no UI,
so every function can be tested headless on a bmesh.

The workflow is three steps, done on a bmesh holding a planar shape key (the
UV layout as geometry, see FlatSession below):

inset_pieces          per pattern piece: absorb + inset_region a parallel row
tag_creases_by_angle  tag edges by dihedral angle (fold lines)
inset_line            inset a tagged internal fold line to both sides

repair_fold_lines, tag_edges and the small tracking helpers below them are
kept because inset_line calls them internally (and tag_edges lets the panel's
Selected -> Crease / Untag buttons mark or clear a line by hand).
"""

import heapq
import math

import bmesh
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

# Inset absorbs everything closer than this multiple of the width, not just
# closer than the width itself. A garment drawn with the parallel-line trick
# already carries an internal line at exactly the width the user then insets
# by, and that line's vertices scatter a little to both sides of it: with an
# exact cut half of them are collapsed and half are left standing right where
# the new row lands, which tears the strip. The margin puts the whole line on
# one side of the cut. Measured on a jacket export whose line sits at 1.00 mm:
# the distance histogram has 2202 vertices in 0.95-1.05 and a clear valley at
# 1.25-1.35, so a quarter of the width is both enough and not greedy (the next
# internal line was at 2.00 mm).
ABSORB_MARGIN = 1.25

# A triangle whose height over its longest edge is below this fraction of the
# width is a needle left over from the absorb, not cloth. Measured: the needles
# are 0.009 mm high for a 1 mm width, the thinnest real triangle in the same
# neighbourhood 0.77 mm, so anything in between separates them.
SLIVER_ALTITUDE = 0.05


# ---------------------------------------------------------------------------
# helpers

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
                if lo is None:
                    lo = hi = c
                else:
                    lo = min(lo, c)
                    hi = max(hi, c)
                if hi - lo > 1e-7:
                    planar = False
                    break
            if planar:
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
        for comp in _pieces(bm):
            verts = list({v for f in comp for v in f.verts})
            for v in verts:
                p2 = v.co.copy()
                k = v[self.player]
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
        bm.verts.layers.int.remove(self.player)
        bm.normal_update()
        return unmapped


# ---------------------------------------------------------------------------
# 1. inset pieces (per pattern piece: the pieces are still loose parts, so
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


def _keeps_orientation(v, target):
    """True when moving `v` onto `target` flips none of v's faces (the faces
    that contain `target` collapse and are fine). In 2D this is the test that
    a triangle is not turned inside out; welding onto the nearest outline
    vertex without it inverted 64 strip triangles on a jacket (measured), the
    overlaps that then read as shading defects along a straight outline."""
    for f in v.link_faces:
        if target in f.verts:
            continue
        cos = [target.co if x is v else x.co for x in f.verts]
        n = Vector()
        for i in range(len(cos)):
            n += cos[i].cross(cos[(i + 1) % len(cos)])
        if n.dot(f.normal) <= 0.0:
            return False
    return True


def _collapse_target(v, candidates):
    """The nearest of `candidates` that `v` can be welded onto without
    flipping a face, or None (dissolve it instead)."""
    for c in sorted(candidates, key=lambda c: (c.co - v.co).length):
        if _keeps_orientation(v, c):
            return c
    return None


def _flip_outline_slivers(bm, faces=None, max_passes=3):
    """A triangle whose three vertices are consecutive outline vertices (two
    of its edges are boundary) is what the absorb leaves where two vertices
    were welded onto two neighbouring outline vertices. It is a thin sliver
    between the outline arc and its chord, on either side of the chord — too
    tall for _collapse_slivers when the outline curves (sagitta 0.05-0.5 mm
    measured), and inset_region then offsets that sliver's inner copy a full
    Width inward, past its own chord: the strip folds over and its row runs
    outside the piece (64 inverted strip triangles on a jacket). The chord
    edge is dissolved, which merges the sliver into the face beyond: the
    outline is untouched and the merged face is wide enough to inset.
    (Flipping the chord instead was tried: with the sliver on the wrong side
    of the chord the flipped triangles fold, and the winding cases were
    error-prone. Merging never inverts anything.) Returns the merge count."""
    total = 0
    for _ in range(max_passes):
        merge = []
        pool = bm.faces if faces is None else [f for f in faces if f.is_valid]
        for f in pool:
            if len(f.verts) != 3:
                continue
            bnd = [e for e in f.edges if e.is_boundary]
            if len(bnd) != 2:
                continue
            chord = next(e for e in f.edges if not e.is_boundary)
            if len(chord.link_faces) == 2:
                merge.append(chord)
        if not merge:
            break
        seen = set()
        edges = []
        for e in merge:
            fs = tuple(e.link_faces)
            if any(x in seen for x in fs):
                continue
            seen.update(fs)
            edges.append(e)
        bmesh.ops.dissolve_edges(bm, edges=edges, use_verts=False)
        total += len(edges)
        faces = None
    return total


def _collapse_slivers(bm, min_altitude, max_passes=3, faces=None):
    """Collapse the needle triangles the absorb leaves behind.

    `faces`: restrict the search to these faces (default: the whole mesh).

    Collapsing an interior vertex onto its nearest outline vertex turns a
    triangle that spanned two outline vertices into a triangle of three
    consecutive outline vertices: nearly collinear, area next to zero (0.018
    against a 1.0 median measured on a jacket export, altitude 0.009 mm). Its
    normal is arbitrary, and inset_region averages the face normals of a
    vertex's fan into the direction it offsets that vertex, so the new row
    dips off the surface right there (0.68 mm measured) — the dents along an
    outline. The middle vertex of such a needle is welded onto the nearer end
    of the long edge, which removes the face and closes the outline over it
    (the long edge is already there).

    Returns the number of vertices welded away."""
    total = 0
    for _ in range(max_passes):
        targetmap = {}
        pool = bm.faces if faces is None else [f for f in faces if f.is_valid]
        for f in pool:
            if len(f.verts) != 3:
                continue
            e_long = max(f.edges, key=lambda e: e.calc_length())
            ln = e_long.calc_length()
            if ln < 1e-12:
                continue
            if 2.0 * f.calc_area() / ln >= min_altitude:
                continue
            apex = next((v for v in f.verts if v not in e_long.verts), None)
            if apex is None:
                continue
            # an outline vertex may only merge into the outline: welding it
            # onto an interior vertex bends the outline inward, and the inset
            # row then runs OUTSIDE the piece there (64 inverted strip faces
            # on a jacket, all along straight outline; measured)
            cands = [x for x in e_long.verts if x.is_boundary or not apex.is_boundary]
            tgt = _collapse_target(apex, cands) if cands else None
            if tgt is not None and tgt is not apex:
                targetmap.setdefault(apex, tgt)
        # a vertex that is itself being welded away cannot be a target
        targetmap = {v: t for v, t in targetmap.items() if t not in targetmap}
        if not targetmap:
            break
        bmesh.ops.weld_verts(bm, targetmap=targetmap)
        total += len(targetmap)
    return total


# The corner row vertex is pushed at most this many widths in, and rows are
# merged into it over at most this many widths along the edge. 4 W made a fan
# of 13 long thin triangles at the shoulder point of a back piece (dense
# outline, very acute); 2.5 W is the compromise between that and the fold-over.
CORNER_MAX = 2.5
SLIT_FIX = False   # see _fix_slits; off until it is verified on real data
CORNER_ANGLE = math.radians(100.0)   # convex corners sharper than this are treated


def _sharp_corners(bm, bverts, width):
    """({outline vertex: (reach, along)}, {outline vertex: axis}) for the
    sharp corners of a piece.

    The first dict is the convex corners: `reach` = where the two offset
    lines meet, width / sin(alpha/2), capped at CORNER_MAX widths; `along` =
    how far along each edge from the corner an outline vertex's own row
    point would fall beyond the other edge's offset line, width /
    tan(alpha/2).

    The second dict is the concave corners (a dart / slit tip): `axis` is
    the unit bisector of the two boundary edges, pointing toward the
    pattern's outside (the slit's open side) — the tip's own row vertex
    lies roughly `width` the other way, into the fabric."""
    out = {}
    slits = {}
    for b in bverts:
        nb = [e.other_vert(b) for e in b.link_edges if e.is_boundary]
        if len(nb) != 2 or not b.link_faces:
            continue
        e1 = nb[0].co - b.co
        e2 = nb[1].co - b.co
        if e1.length < 1e-12 or e2.length < 1e-12:
            continue
        e1.normalize()
        e2.normalize()
        ang = e1.angle(e2)
        if ang >= CORNER_ANGLE:
            continue
        inward = Vector()
        for f in b.link_faces:
            inward += f.calc_center_median() - b.co
        bis = e1 + e2
        if bis.length < 1e-9:
            continue
        if bis.dot(inward) < 0.0:
            slits[b] = bis.normalized()
            continue
        if bis.dot(inward) <= 0.0:
            continue
        half = ang * 0.5
        reach = min(width / math.sin(half), width * CORNER_MAX)
        along = min(width / math.tan(half), width * CORNER_MAX)
        out[b] = (reach, along)
    return out, slits


def _fix_corners(bm, strip_faces, corners):
    """Uneven offset puts every row vertex `width` from its outline vertex
    along the bisector. At a sharp convex corner that is too close: the rows
    coming along the two edges meet beyond it and the strip folds over
    (inverted faces at a 37 degree tip, measured). The corner's row vertex is
    moved to where the two offset lines meet (`reach`), and the row vertices
    of the outline vertices within `along` of the corner - whose own offset
    points lie beyond the other edge's offset line - are welded onto it, so
    the strip ends in a clean point. The interior vertices that the moved
    corner would run into were absorbed beforehand (see _inset_pieces).
    Returns the number of corners treated."""
    rails = {}
    for f in strip_faces:
        for e in f.edges:
            x, y = e.verts
            if x.is_boundary and not y.is_boundary:
                rails.setdefault(x, y)
            elif y.is_boundary and not x.is_boundary:
                rails.setdefault(y, x)
    targetmap = {}
    moved = 0
    for c, (reach, along) in corners.items():
        if not c.is_valid or c not in rails:
            continue
        r = rails[c]
        d = r.co - c.co
        if d.length < 1e-12:
            continue
        r.co = c.co + d.normalized() * reach
        moved += 1
        # walk the outline both ways and merge the near rows into r
        for e0 in [e for e in c.link_edges if e.is_boundary]:
            prev, cur, dist = c, e0.other_vert(c), e0.calc_length()
            while dist < along and cur.is_valid:
                rc = rails.get(cur)
                if rc is not None and rc is not r and rc.is_valid and rc not in targetmap:
                    targetmap[rc] = r
                nxt = [e for e in cur.link_edges if e.is_boundary and e.other_vert(cur) is not prev]
                if len(nxt) != 1:
                    break
                prev, cur = cur, nxt[0].other_vert(cur)
                dist += nxt[0].calc_length()
    targetmap = {k: v for k, v in targetmap.items() if v not in targetmap}
    if targetmap:
        bmesh.ops.weld_verts(bm, targetmap=targetmap)
    return moved


def _fix_slits(bm, strip_faces, slits, width):
    """At a concave sharp corner (a dart / slit tip), the offset rows coming
    from the two sides cross each other before they reach `width` of the
    tip: a 2D-inverted strip face on the near side, a fan of long edges at
    the tip on the far side (the row vertices from both sides land almost on
    top of each other instead of meeting). Every row vertex within `width`
    of the tip — measured as the perpendicular distance from the slit's
    axis, not the distance along it — is moved onto the axis line instead of
    its own offset direction, so both sides run parallel to the slit and
    close cleanly at the tip; the tip's own row vertex is left where it is.
    Row vertices from the two sides that end up within `width * 0.05` of
    each other are then welded into one. Returns the number of tips fixed."""
    rails = {}
    for f in strip_faces:
        for e in f.edges:
            x, y = e.verts
            if x.is_boundary and not y.is_boundary:
                rails.setdefault(x, y)
            elif y.is_boundary and not x.is_boundary:
                rails.setdefault(y, x)
    moved_verts = []
    fixed = 0
    for t, axis in slits.items():
        if not t.is_valid or t not in rails:
            continue
        sides = [e for e in t.link_edges if e.is_boundary]
        if len(sides) != 2:
            continue
        any_side = False
        for e0 in sides:
            prev, cur = t, e0.other_vert(t)
            while cur.is_valid:
                d = ((cur.co - t.co) - axis * (cur.co - t.co).dot(axis)).length
                if d >= width:
                    break
                r = rails.get(cur)
                if r is not None and r.is_valid:
                    r.co = t.co + axis * (cur.co - t.co).dot(axis)
                    moved_verts.append(r)
                    any_side = True
                nxt = [e for e in cur.link_edges if e.is_boundary and e.other_vert(cur) is not prev]
                if len(nxt) != 1:
                    break
                prev, cur = cur, nxt[0].other_vert(cur)
        if any_side:
            fixed += 1
    if moved_verts:
        pts = [v for v in moved_verts if v.is_valid]
        kd = kdtree.KDTree(len(pts))
        for i, v in enumerate(pts):
            kd.insert(v.co, i)
        kd.balance()
        thresh = width * 0.05
        targetmap = {}
        for i, v in enumerate(pts):
            if v in targetmap:
                continue
            for (_co, j, _d) in kd.find_range(v.co, thresh):
                if j <= i:
                    continue
                o = pts[j]
                if o is v or o in targetmap:
                    continue
                targetmap[o] = v
        targetmap = {k: v for k, v in targetmap.items() if v not in targetmap}
        if targetmap:
            bmesh.ops.weld_verts(bm, targetmap=targetmap)
    return fixed


def inset_pieces(bm, width, sharp_inner=True, progress=None, flat=None):
    """`flat`: (flat_key_name, basis_key_name) to do the work on the flat
    shape (see FlatSession); None works in 3D as before."""
    session = FlatSession(bm, *flat) if flat else None
    if session is not None:
        session.enter_2d()
    stats = _inset_pieces(bm, width, sharp_inner, progress)
    if session is not None:
        stats['unmapped'] = session.end()
    return stats


def _inset_pieces(bm, width, sharp_inner=True, progress=None):
    """For every pattern piece: absorb the vertices closer than
    ABSORB_MARGIN * `width` to the outline, then inset the outline by `width`
    so a vertex row runs parallel to it (the parallel internal line, made in
    Blender).

    The absorb radius is deliberately wider than the inset: an export made
    with the parallel-line trick already has an internal line at the width
    being inset by, and a vertex left standing between `width` and the absorb
    radius is pierced by the new row. See ABSORB_MARGIN.

    Absorb rule: a vertex inside the absorb radius is collapsed along an
    existing edge onto its nearest outline vertex; one with no outline
    neighbour is dissolved on its own. (Dissolving the whole ring at once
    leaves an annular face bmesh cannot represent; welding onto a
    non-adjacent vertex makes non-manifold edges. Both were tried.) The
    needle triangles that collapse leaves on the outline are then removed —
    see _collapse_slivers, they are what dented the row.

    The inset uses the uneven offset: the even (mitre) offset shoots out at
    acute corners (49 mm measured for a 1 mm inset). The inner edges of the
    strip are marked sharp so the strip's normals do not leak into the
    irregular triangles further in (the visible residue otherwise).

    Returns a stats dict."""
    tick = progress if callable(progress) else (lambda f: None)
    bm.normal_update()
    parts = _pieces(bm)
    stats = {'pieces': len(parts), 'absorbed': 0, 'dissolved': 0, 'slivers': 0,
             'strip_faces': 0}
    targetmap = {}
    dissolve = []
    absorb_r = width * ABSORB_MARGIN
    reach = absorb_r * 1.5
    corners_all = {}
    slits_all = {}
    for comp in parts:
        bedges = list({e for f in comp for e in f.edges if e.is_boundary})
        if not bedges:
            continue
        bset = {v for e in bedges for v in e.verts}
        index = _SegmentIndex(bedges)
        # sharp convex corners: the strip must reach further in there (see
        # _fix_corners), so everything within that reach of the corner is
        # absorbed too, or the widened corner runs into interior triangles
        # (16 inverted faces measured without this)
        corners, slits = _sharp_corners(bm, bset, width)
        corners_all.update(corners)
        slits_all.update(slits)
        c_reach = max([r for r, _a in corners.values()] + [0.0]) * ABSORB_MARGIN
        dist = _surface_band(bm, bset, max(reach, c_reach * 1.5))
        for v in dist:
            if v in bset:
                continue
            d = index.distance(v.co, None, k=8)
            if d is None:
                continue
            if d >= absorb_r and not any((v.co - c.co).length < r * ABSORB_MARGIN
                                         for c, (r, _a) in corners.items()):
                continue
            nb = [e.other_vert(v) for e in v.link_edges if e.other_vert(v) in bset]
            tgt = _collapse_target(v, nb) if nb else None
            if tgt is not None:
                targetmap[v] = tgt
            else:
                dissolve.append(v)
    tick(0.3)
    n0 = len(bm.verts)
    if targetmap:
        bmesh.ops.weld_verts(bm, targetmap=targetmap)
    dissolve = [v for v in dissolve if v.is_valid]
    if dissolve:
        bmesh.ops.dissolve_verts(bm, verts=dissolve, use_face_split=False,
                                 use_boundary_tear=False)
    stats['absorbed'] = n0 - len(bm.verts)
    stats['dissolved'] = len(dissolve)
    stats['slivers'] = _collapse_slivers(bm, width * SLIVER_ALTITUDE)
    bm.normal_update()
    stats['flipped'] = _flip_outline_slivers(bm)
    bm.normal_update()
    tick(0.5)
    # Adding a custom data layer reallocates: make it before taking any
    # element references.
    rlay = row_layer(bm)
    parts = _pieces(bm)
    for comp in parts:
        if not any(e.is_boundary for f in comp for e in f.edges):
            continue
        r = bmesh.ops.inset_region(bm, faces=comp, thickness=width, depth=0.0,
                                   use_boundary=True, use_even_offset=False,
                                   use_interpolate=True, use_relative_offset=False,
                                   use_edge_rail=False, use_outset=False)
        stats['strip_faces'] += len(r['faces'])
        inner = {v for f in r['faces'] for v in f.verts if not v.is_boundary}
        for f in r['faces']:
            for e in f.edges:
                if e.verts[0] in inner and e.verts[1] in inner:
                    e[rlay] = 1
                    if sharp_inner:
                        e.smooth = False
        # (the corners of the other pieces have no rail in this strip and
        # are skipped inside)
        stats['corners'] = stats.get('corners', 0) + _fix_corners(bm, r['faces'], corners_all)
        # Disabled 2026-09-05: on the jacket it moved rows at 1 of 9 tips and raised
        # the 2D-inverted count 17 -> 22 (measured). Kept for the next design pass.
        if SLIT_FIX:
            stats['slits'] = stats.get('slits', 0) + _fix_slits(bm, r['faces'], slits_all, width)
    tick(0.8)
    polys = [f for f in bm.faces if len(f.verts) > 3]
    if polys:
        bmesh.ops.triangulate(bm, faces=polys, quad_method='BEAUTY', ngon_method='BEAUTY')
    for f in bm.faces:
        f.smooth = True
    bm.normal_update()
    tick(0.95)
    return stats


# ---------------------------------------------------------------------------
# fold-line repair

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
        if step.length < 1e-12:
            break
        dirn = step.normalized()
        best = None
        for e in cur.link_edges:
            o = e.other_vert(cur)
            if o in chainset:
                continue
            d = o.co - cur.co
            if d.length < 1e-12:
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
                    if d.length < 1e-12 or d.length > 2.6 * step.length:
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
    sel = list(verts)
    grown = set(sel)
    lens = sorted(e.calc_length() for v in sel for e in v.link_edges)
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

def _protected(v, rlay):
    """A vertex Inset Line may not absorb: on the pattern outline, or on a
    row an earlier inset built (ROW_LAYER set on its edges)."""
    if v.is_boundary:
        return True
    if rlay is not None:
        for e in v.link_edges:
            if e[rlay]:
                return True
    return False


def inset_line(bm, verts, width, extend=True, min_dihedral_deg=6.0, profile=1.0,
               progress=None, flat=None):
    """Inset an internal fold line to both sides: the same idea as
    inset_pieces, applied to a line inside a pattern piece instead of to its
    outline. In 2D terms: two lines parallel to the crease at +-`width`, and
    everything that fell between them absorbed.

    `verts`: the fold-line vertices (selected by the user; with `extend` the
    line is first walked outward from them, as in repair_fold_lines).

    1. Missing edges along the line are restored (repair_fold_lines), so the
       line is a chain of edges.
    2. Every vertex within ABSORB_MARGIN * `width` of the chain (over the
       surface, reachable) is collapsed along an existing edge onto its
       nearest chain vertex, or dissolved if it has none — the same rule as
       inset_pieces. Vertices on the pattern outline and on the row of an
       earlier Inset Pieces are left alone (see _protected): the band must
       not tear the outline strip where the line runs into it. Chain
       vertices that are protected, or whose protected neighbour is closer
       than `width`, are trimmed off the chain instead, so no bevel offset
       has to slide along an edge shorter than itself (the bevel's overlap
       clamp is global: one short edge would shrink the whole band).
    3. Needle triangles left on the chain are welded away (_collapse_slivers).
    4. The chain edges are bevelled by `width` with 2 segments: the middle
       row stays on the crease, the two outer rows are the parallel lines.
       `profile` 1.0 keeps the fold's shape (the middle row exactly on the
       old edge); 0.5 rounds it.
    5. The outer rows' edges are marked sharp (the band's normals must not
       leak into the irregular triangles beside it), ngons re-triangulated,
       all faces smooth.

    Returns a stats dict; 'width_achieved' is the median distance from the
    middle row to the outer rows, which should equal `width` — smaller means
    the overlap clamp fired.

    `flat`: (flat_key_name, basis_key_name) — the chain is found in 3D (the
    fold's dihedral is what identifies it), everything after that is done on
    the flat shape and mapped back (see FlatSession)."""
    tick = progress if callable(progress) else (lambda f: None)
    stats = {'line_verts': 0, 'trimmed': 0, 'repaired': 0, 'absorbed': 0,
             'dissolved': 0, 'slivers': 0, 'needles_merged': 0, 'bevel_faces': 0,
             'sharp_edges': 0, 'width_achieved': 0.0}
    verts = [v for v in verts if v.is_valid]
    if len(verts) < 2:
        return stats
    # the layer first: adding one reallocates and invalidates element refs
    rlay = row_layer(bm)
    session = FlatSession(bm, *flat) if flat else None
    if session is not None:
        session.enter_3d()
    bm.normal_update()
    bm.verts.index_update()
    bm.verts.ensure_lookup_table()
    verts = [v for v in verts if v.is_valid]

    # 1. the chain, with its missing edges restored
    gaps, fixed, _n = repair_fold_lines(bm, verts, extend=extend,
                                        min_dihedral_deg=min_dihedral_deg)
    stats['repaired'] = fixed
    chain = set(v for v in verts if v.is_valid)
    if extend:
        chain = _extend_selection(bm, list(chain), math.radians(min_dihedral_deg))
    if session is not None:
        session.enter_2d()
    try:
        stats = _inset_line_body(bm, chain, width, profile, rlay, stats, tick)
    finally:
        if session is not None:
            stats['unmapped'] = session.end()
    return stats


def _inset_line_body(bm, chain, width, profile, rlay, stats, tick):
    # trim: protected chain vertices, then ends whose protected neighbour is
    # closer than the offset the bevel will slide along that edge
    n_chain0 = len(chain)
    # Protected chain vertices (on the outline, or on the Inset Pieces row)
    # are trimmed, so the band stops one row short of the strip. Letting the
    # band run into the strip was tried (2026-09-05): the bevel at a strip or
    # outline vertex clamps that line to 0% and leaves needles (42 measured),
    # so the small notch at the line's end is the lesser evil for now.
    # Outline vertices stay as the band's terminals, so the band runs up to
    # the outline (Inset Line runs BEFORE Inset Pieces; the strip is built
    # afterwards around the band, whose rows it must not absorb). Vertices on
    # an earlier inset's row are trimmed: a bevel ending on such a row clamps
    # the whole line to 0% and leaves needles (measured).
    chain = {v for v in chain if not (_protected(v, rlay) and not v.is_boundary)}
    while True:
        drop = [v for v in chain
                if any(_protected(e.other_vert(v), rlay) and not e.other_vert(v).is_boundary
                       and e.calc_length() < width * 0.9 for e in v.link_edges)]
        if not drop:
            break
        chain.difference_update(drop)
    stats['trimmed'] = n_chain0 - len(chain)
    stats['line_verts'] = len(chain)
    cedges = [e for e in bm.edges
              if e.verts[0] in chain and e.verts[1] in chain and len(e.link_faces) == 2]
    if not cedges:
        return stats
    tick(0.2)

    # 2. absorb
    absorb_r = width * ABSORB_MARGIN
    index = _SegmentIndex(cedges)
    dist = _surface_band(bm, chain, absorb_r * 1.5)
    targetmap = {}
    dissolve = []
    for v in dist:
        if v in chain or _protected(v, rlay):
            continue
        d = index.distance(v.co, v.normal, k=8)
        if d is None or d >= absorb_r:
            continue
        nb = [e.other_vert(v) for e in v.link_edges if e.other_vert(v) in chain]
        tgt = _collapse_target(v, nb) if nb else None
        if tgt is not None:
            targetmap[v] = tgt
        else:
            dissolve.append(v)
    n0 = len(bm.verts)
    if targetmap:
        bmesh.ops.weld_verts(bm, targetmap=targetmap)
    dissolve = [v for v in dissolve if v.is_valid]
    if dissolve:
        bmesh.ops.dissolve_verts(bm, verts=dissolve, use_face_split=False,
                                 use_boundary_tear=False)
    stats['absorbed'] = n0 - len(bm.verts)
    stats['dissolved'] = len(dissolve)
    tick(0.4)

    # 3. needles on the chain
    chain = {v for v in chain if v.is_valid}
    near_faces = list({f for v in chain for f in v.link_faces})
    stats['slivers'] = _collapse_slivers(bm, width * SLIVER_ALTITUDE, faces=near_faces)
    chain = {v for v in chain if v.is_valid}

    # 3b. The bevel's overlap clamp is global: one place where the offset
    #     cannot reach `width` shrinks the whole band to that place. Two
    #     such places are left by the absorb and are removed here.
    #     (a) An edge from a chain vertex to a protected vertex that the weld
    #         made shorter than `width` (an absorbed vertex carried that edge
    #         over): the chain vertex is trimmed; an unprotected vertex that
    #         close is welded onto the chain.
    #     (b) A face with two chain edges: three consecutive line vertices
    #         (the middle one gained the face when a vertex was welded onto
    #         it). The line bends a few degrees, so the needle is taller
    #         than _collapse_slivers removes (measured 0.003-0.05 mm, the
    #         clamp then made the band exactly that wide). Its third edge is
    #         dissolved, which merges it into the face beyond.
    # One pass only: repeating it let a chain vertex swallow ring after ring
    # (13 long edges radiating from one vertex, measured). What it leaves is
    # handled per chain by the bevel's own clamp and shows in the report.
    cset = {e for e in bm.edges if e.verts[0] in chain and e.verts[1] in chain}
    targetmap = {}
    drop = set()
    for v in chain:
        for e in v.link_edges:
            if e in cset or e.calc_length() >= width * 0.9:
                continue
            o = e.other_vert(v)
            if _protected(o, rlay):
                drop.add(v)
            elif o not in targetmap and _keeps_orientation(o, v):
                targetmap[o] = v
    chain.difference_update(drop)
    stats['trimmed'] += len(drop)
    targetmap = {o: v for o, v in targetmap.items() if v in chain}
    if targetmap:
        bmesh.ops.weld_verts(bm, targetmap=targetmap)
        stats['absorbed'] += len(targetmap)
    chain = {v for v in chain if v.is_valid}
    cset = {e for e in bm.edges if e.verts[0] in chain and e.verts[1] in chain}
    merge = set()
    for v in chain:
        for f in v.link_faces:
            if sum(1 for e in f.edges if e in cset) >= 2:
                merge.update(e for e in f.edges
                             if e not in cset and len(e.link_faces) == 2)
    if merge:
        bmesh.ops.dissolve_edges(bm, edges=list(merge), use_verts=False)
    stats['needles_merged'] = len(merge)
    bm.normal_update()
    chain = {v for v in chain if v.is_valid}
    stats['line_verts'] = len(chain)
    cedges = [e for e in bm.edges
              if e.verts[0] in chain and e.verts[1] in chain and len(e.link_faces) == 2]
    if not cedges:
        return stats
    tick(0.5)

    # 4. bevel: 2 segments -> middle row on the crease, outer rows at +-width.
    #    One call per connected line: the overlap clamp is global within a
    #    call, so a single tight spot would otherwise shrink every band
    #    (19% of Width measured with 69 lines in one call).
    adj = {}
    for e in cedges:
        for v in e.verts:
            adj.setdefault(v, []).append(e)
    seen = set()
    groups = []
    for e in cedges:
        if e in seen:
            continue
        stack = [e]
        seen.add(e)
        comp = []
        while stack:
            x = stack.pop()
            comp.append(x)
            for v in x.verts:
                for y in adj[v]:
                    if y not in seen:
                        seen.add(y)
                        stack.append(y)
        groups.append(comp)
    r = {'faces': [], 'verts': [], 'edges': []}
    widths = []
    for comp in groups:
        comp = [e for e in comp if e.is_valid]
        if not comp:
            continue
        rg = bmesh.ops.bevel(bm, geom=comp, offset=width, offset_type='OFFSET',
                             segments=2, profile=profile, affect='EDGES',
                             clamp_overlap=True, loop_slide=True,
                             mark_seam=False, mark_sharp=False)
        for k in r:
            r[k].extend(rg[k])
        gf = set(rg['faces'])
        mid = [v for v in rg['verts']
               if v.is_valid and v.link_faces and all(f in gf for f in v.link_faces)]
        ds = sorted((e.other_vert(v).co - v.co).length for v in mid for e in v.link_edges
                    if any(f not in gf for f in e.other_vert(v).link_faces))
        if ds:
            widths.append(ds[len(ds) // 2])
    stats['lines'] = len(groups)
    if widths:
        widths.sort()
        stats['width_min'] = widths[0]
        stats['width_achieved'] = widths[len(widths) // 2]
    bevel_faces = set(r['faces'])
    stats['bevel_faces'] = len(bevel_faces)
    bm.normal_update()
    tick(0.7)

    # 5. outer rows sharp, re-triangulate, smooth
    n_sharp = 0
    for e in r['edges']:
        if not e.is_valid or len(e.link_faces) != 2:
            continue
        if (e.link_faces[0] in bevel_faces) != (e.link_faces[1] in bevel_faces):
            e.smooth = False
            e[rlay] = 1
            n_sharp += 1
    stats['sharp_edges'] = n_sharp
    # the dissolve's ngons all touch the band, so this is every new polygon
    polys = list({f for v in r['verts'] if v.is_valid for f in v.link_faces
                  if len(f.verts) > 3})
    if polys:
        bmesh.ops.triangulate(bm, faces=polys, quad_method='BEAUTY', ngon_method='BEAUTY')
        for f in bm.faces:
            f.smooth = True
    bm.normal_update()
    tick(0.95)
    return stats


# ---------------------------------------------------------------------------
# 2. tagging (Find Folds)

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
