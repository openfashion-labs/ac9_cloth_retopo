"""Coarser copies of the Guide, and the transfers between them.

The displacement field this feature computes is low-frequency by construction:
it is smoothed over a 35 mm radius before it is applied. Solving it on the
Guide's own 1.6 mm grid computes a low-frequency quantity on a high-frequency
grid, and pays for the difference twice -- the detector sees every vertex, and
the diffusion's step count goes as (radius/edge)^2. So the work happens on two
coarser grids and is sampled back:

    proxy       ~2.9 mm   detection and the final field
    diffusion   radius/4  the Laplacian steps only
    Guide       as built  receives the field, barycentrically

Both coarse grids are derived from the Guide, not authored, and neither is a
knob: the proxy's edge length is the one setting, and the diffusion grid is
`radius / 4` because four samples per smoothing radius is everything a field
smoothed over that radius can carry. Six was tried and moved the dihedral
metric not at all (0.0046 either way).

Welding comes first
-------------------
CLO exports panels unwelded. A grid rebuilt from that geometry has a seam that
is two separate chains of vertices, so a field diffused on it stops dead at
every seam and each panel ends up on its own step -- the failure this session
walked into twice. Every level is welded before it is decimated. The fine Guide
is never welded (that would destroy its flat layout); its seams are held by the
twin constraint in `seams.py` instead.

Normals decide the correspondence
---------------------------------
Every sample here picks the nearest triangle whose normal AGREES with the
target's. At a true contact -- two layers coincident, gap 0.000 mm -- the plain
closest point lands on the neighbouring layer and hands a vertex the opposite
displacement while its neighbour gets the right one, so the edge between them
tears: measured edge length max +2510% before this rule, +93% after. Two
touching layers face opposite ways, which is the whole discriminator. (The
addon's own projector uses the same normal-first rule.)
"""

import numpy as np
import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.interpolate import poly_3d_calc

from ..clo_projector.guide import _extract_tri_verts_np

# The proxy weld only has to close CLO's coincident stitches, which measure
# well under a micrometre. It is deliberately tighter than the seam tolerance
# in seams.py: welding two layers that merely pinch would fuse them, and no
# amount of pushing separates a fused layer afterwards.
PROXY_WELD_TOL_M = 1e-6

MIN_DECIMATE_RATIO = 0.002
TRANSFER_RADIUS_M = 0.002    # fine->proxy surface distance measured max 0.08 mm


class Level:
    """One coarse grid: positions, triangles, and its smoothing graph."""

    def __init__(self, co, tris, edges, median_edge):
        self.co = co
        self.tris = tris
        self.edges = edges
        self.median_edge = median_edge
        self.n = len(co)
        self.e0 = edges[:, 0]
        self.e1 = edges[:, 1]
        self.deg = (np.bincount(self.e0, minlength=self.n)
                    + np.bincount(self.e1, minlength=self.n))


def build_level(co0, tris, median_edge_m, target_edge_m, name="AC9_TempLevel"):
    """A welded, decimated copy of `co0`/`tris` at roughly `target_edge_m`.

    Decimate takes a ratio, but a ratio means a different edge length on every
    Guide -- CLO's particle distance moves it case by case -- so it is derived
    here: Collapse roughly halves the edge count per halving of vertex count,
    so edge length scales as 1/sqrt(ratio).

    The modifier is evaluated through the depsgraph rather than applied with
    `bpy.ops.object.modifier_apply`, so this never touches the user's mode,
    selection or active object.
    """
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(c) for c in co0], [], [tuple(int(x) for x in t) for t in tris])
    me.update()

    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=PROXY_WELD_TOL_M)
    bm.to_mesh(me)
    bm.free()

    ob = bpy.data.objects.new(name, me)
    mod = ob.modifiers.new("AC9_Decimate", 'DECIMATE')
    mod.decimate_type = 'COLLAPSE'
    ratio = (median_edge_m / target_edge_m) ** 2 if target_edge_m > 0.0 else 1.0
    mod.ratio = float(np.clip(ratio, MIN_DECIMATE_RATIO, 1.0))

    coll = bpy.context.scene.collection
    coll.objects.link(ob)
    try:
        dg = bpy.context.evaluated_depsgraph_get()
        ev = ob.evaluated_get(dg)
        out = bpy.data.meshes.new_from_object(ev, depsgraph=dg)
        nv = len(out.vertices)
        co = np.empty(nv * 3)
        out.vertices.foreach_get("co", co)
        co = co.reshape(nv, 3)
        lvl_tris = np.asarray(_extract_tri_verts_np(out), dtype=np.int64)
        e = np.empty(len(out.edges) * 2, dtype=np.int32)
        out.edges.foreach_get("vertices", e)
        e = e.reshape(-1, 2).astype(np.int64)
        bpy.data.meshes.remove(out)
    finally:
        coll.objects.unlink(ob)
        bpy.data.objects.remove(ob)
        bpy.data.meshes.remove(me)

    length = np.linalg.norm(co[e[:, 0]] - co[e[:, 1]], axis=1)
    length = length[length > 1e-9]
    med = float(np.median(length)) if len(length) else 0.0
    return Level(co, lvl_tris, e, med)


def _face_normals(co, tris):
    a, b, c = co[tris[:, 0]], co[tris[:, 1]], co[tris[:, 2]]
    fn = np.cross(b - a, c - a)
    return fn / np.maximum(np.linalg.norm(fn, axis=1), 1e-18)[:, None]


def _nearest_agreeing(bvh, polys, co_src, fn_src, point, normal, search):
    """(location, triangle) of the nearest triangle facing the same way.

    Falls back to the plain nearest when nothing in range agrees -- a fold whose
    far side is the only thing nearby. Returns None only if the tree is empty.
    """
    fallback = None
    for loc, _nor, fi, _d in sorted(bvh.find_nearest_range(Vector(point), search),
                                    key=lambda h: h[3]):
        if fallback is None:
            fallback = (loc, fi)
        if float(np.dot(fn_src[fi], normal)) > 0.0:
            return (loc, fi), True
    if fallback is not None:
        return fallback, False
    loc, _nor, fi, _d = bvh.find_nearest(Vector(point))
    if fi is None:
        return None, False
    return (loc, fi), False


class Barycentric:
    """Each target point expressed as three weights on one source triangle.

    Built once. The grids never move -- only the field on them does -- so
    rebuilding the correspondence per iteration would cost far more than the
    diffusion it saves (the transfer to the fine Guide alone takes 1.7 s).
    """

    def __init__(self, src_co, src_tris, dst_co, dst_normals, search):
        polys = [tuple(int(x) for x in t) for t in src_tris]
        bvh = BVHTree.FromPolygons([Vector(c) for c in src_co], polys)
        fn = _face_normals(src_co, src_tris)
        m = len(dst_co)
        tri = np.zeros(m, dtype=np.int64)
        w = np.zeros((m, 3))
        self.disagreed = 0
        self.missed = 0
        for i in range(m):
            hit, agreed = _nearest_agreeing(bvh, polys, src_co, fn,
                                            dst_co[i], dst_normals[i], search)
            if hit is None:
                self.missed += 1
                w[i] = (1.0, 0.0, 0.0)
                continue
            if not agreed:
                self.disagreed += 1
            loc, fi = hit
            tri[i] = fi
            w[i] = poly_3d_calc([Vector(src_co[k]) for k in polys[fi]], loc)
        self.idx = src_tris[tri]
        self.w = w
        self.src_n = len(src_co)
        # The transpose, normalised: what each source vertex receives back.
        den = np.zeros(self.src_n)
        for k in range(3):
            den += np.bincount(self.idx[:, k], weights=w[:, k], minlength=self.src_n)
        self.den = np.maximum(den, 1e-12)
        self.orphans = int((den <= 1e-11).sum())

    def prolong(self, field):
        """source field -> target (barycentric interpolation)."""
        return (self.w[:, 0, None] * field[self.idx[:, 0]]
                + self.w[:, 1, None] * field[self.idx[:, 1]]
                + self.w[:, 2, None] * field[self.idx[:, 2]])

    def restrict(self, field):
        """target field -> source (the transpose of prolong, normalised)."""
        out = np.zeros((self.src_n, 3))
        for k in range(3):
            wk = self.w[:, k]
            for c in range(3):
                out[:, c] += np.bincount(self.idx[:, k], weights=wk * field[:, c],
                                         minlength=self.src_n)
        return out / self.den[:, None]


def sample_flat(co0, tris, co2, points):
    """Flat-layout coordinates for arbitrary points, sampled off the fine Guide.

    The proxy needs these or the 2D half of the detection test is meaningless,
    and it cannot carry its own: after the weld a seam vertex holds only one
    panel's flat coordinate. Plain nearest, no normal test -- a proxy vertex sits
    on the fine surface it came from, so the two layers of a contact are not
    competing here the way they are for a displacement.
    """
    polys = [tuple(int(x) for x in t) for t in tris]
    bvh = BVHTree.FromPolygons([Vector(c) for c in co0], polys)
    out = np.zeros((len(points), 3))
    for i, pt in enumerate(points):
        loc, _nor, fi, _d = bvh.find_nearest(Vector(pt))
        if fi is None:
            out[i] = co2[int(np.argmin(np.linalg.norm(co0 - pt, axis=1)))]
            continue
        vs = polys[fi]
        w = poly_3d_calc([Vector(co0[k]) for k in vs], loc)
        out[i] = sum(wk * co2[k] for wk, k in zip(w, vs))
    return out
