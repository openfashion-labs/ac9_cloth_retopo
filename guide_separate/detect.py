"""Self-contact detection: which Guide vertices have another layer against them.

The test
--------
A vertex is in contact when another surface is within *gap* in 3D **and** far
away in the flat layout. The 2D half is what separates a layer that fell onto
this one from the fabric immediately around it: on the flat pattern your own
neighbourhood is close and a fallen layer is not. It needs no heuristic on
normals or topology.

Two things the test cannot see on its own, both supplied by the caller:

*Sewing.* Two panels stitched along their outlines are near in 3D and in
different UV islands, so every seam reads as an overlap and the groove between
panels -- the detail Solidify exists to bake -- gets pushed open. `keep_pairs`
drops those pairs; `seams.py` builds the predicate.

*The flat layout's scale.* `Create Flat SK` writes raw UV coordinates as
geometry, so the layout's scale is whatever the UV packing happened to be:
measured 1.0023 for a jacket whose UV fills the square against 0.3318 for the
same jacket packed with a whole outfit. `ABS_2D_M` would silently mean 15 mm in
one file and 45 mm in another. The flat coordinates are divided by that scale
once here, so both constants keep their physical meaning and nothing downstream
has to remember which space it is in.

How it runs
-----------
Stage 1  A spatial hash over the vertices (cell = search radius, 27 neighbours)
         yields candidate pairs; the 2D test throws away almost all of them.
         Vertex distance overestimates the surface distance, so the 2D
         threshold uses the strict lower bound `d_vert - rho_j - 2t`, and the
         2D test itself is relaxed per partner because Stage 2 measures from
         the face centroid where Stage 1 only has the vertex.
Stage 2  Exact point-to-triangle distance, against the triangles incident to
         each surviving partner -- deduplicated, because a triangle is reached
         from up to three of its vertices and `push` accumulates per face.
Stage 2b Triangles whose covering radius exceeds the Stage 1 pad cannot be
         reached from a partner vertex at all; a KDTree picks those up.

Everything is vectorised. The version this replaces looped in Python over every
vertex times every BVH hit -- 290 faces per vertex at a 4 mm radius on a 1.6 mm
Guide, so 38 million iterations for one pass, 56 seconds with no progress bar.
"""

import numpy as np
from mathutils import Vector
from mathutils.kdtree import KDTree

# A hit counts as another layer when its flat distance exceeds BOTH of these:
# a multiple of the 3D distance (a fold's crease is the tightest legitimate
# 2D-near / 3D-near case) and an absolute floor (the flat distance across a
# fold is at least the fold's circumference). Both are real-space, because the
# flat coordinates are rescaled on the way in.
#
# What the floor costs, so nobody has to rediscover it from a grey Check: at
# the usual 4 mm gap the ratio is dead (it only bites past d = 3.75 mm), so
# ABS_2D_M decides alone, and going across a fold covers about twice its depth
# in the flat layout. **A fold shallower than ~7.5 mm is therefore not
# separated, by design** -- the valley of a fine pleat reads as grey in Check,
# meaning "nothing near", rather than red. The error is one-sided: this misses
# contacts, it never invents them. Deeper folds are caught normally.
RATIO_2D = 4.0
ABS_2D_M = 0.015

EPS_D = 1e-9
EPS_AREA = 1e-14
CHUNK = 4_000_000          # vertex-triangle pairs per Stage 2 batch
RHO_PCT = 99.0             # above this covering radius a triangle goes to Stage 2b


# ---------------------------------------------------------------------------
# small vectorised helpers
# ---------------------------------------------------------------------------


def neighbour_mean(field, e0, e1, deg):
    """Mean of each vertex's neighbours, for a uniform-Laplacian smoothing step.

    `np.bincount` rather than `np.add.at`: measured 3.0x faster over 390k edges
    with the results identical to 1.9e-19 m.
    """
    n = len(field)
    out = np.empty_like(field)
    for k in range(field.shape[1]):
        col = field[:, k]
        out[:, k] = (np.bincount(e0, weights=col[e1], minlength=n)
                     + np.bincount(e1, weights=col[e0], minlength=n))
    return out / np.maximum(deg, 1)[:, None]


def vertex_normals(co, tris):
    """Area-weighted vertex normals."""
    a, b, c = co[tris[:, 0]], co[tris[:, 1]], co[tris[:, 2]]
    fn = np.cross(b - a, c - a)
    n = np.zeros_like(co)
    for k in range(3):
        np.add.at(n, tris[:, k], fn)
    return n / np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]


def ragged_range(counts):
    """[0..c0-1, 0..c1-1, ...] for counts [c0, c1, ...] -- expands a CSR."""
    total = int(counts.sum())
    return np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts)


def closest_on_tri(P, A, B, C):
    """Closest point on each triangle to each point (Ericson, vectorised).

    The regions are evaluated all at once and then overwritten in reverse
    priority, so the earlier test wins exactly as it would in the branching
    form.
    """
    ab, ac, ap = B - A, C - A, P - A
    d1 = np.einsum("ij,ij->i", ab, ap)
    d2 = np.einsum("ij,ij->i", ac, ap)
    bp = P - B
    d3 = np.einsum("ij,ij->i", ab, bp)
    d4 = np.einsum("ij,ij->i", ac, bp)
    cp = P - C
    d5 = np.einsum("ij,ij->i", ab, cp)
    d6 = np.einsum("ij,ij->i", ac, cp)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    den = va + vb + vc
    den = np.where(np.abs(den) < 1e-30, 1.0, den)
    out = A + ab * (vb / den)[:, None] + ac * (vc / den)[:, None]
    m = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)                # edge BC
    if m.any():
        f = ((d4 - d3) / np.maximum((d4 - d3) + (d5 - d6), 1e-30))[:, None]
        out = np.where(m[:, None], B + f * (C - B), out)
    m = (vb <= 0) & (d2 >= 0) & (d6 <= 0)                              # edge AC
    if m.any():
        out = np.where(m[:, None], A + (d2 / np.maximum(d2 - d6, 1e-30))[:, None] * ac, out)
    m = (d6 >= 0) & (d5 <= d6)                                         # vertex C
    out = np.where(m[:, None], C, out)
    m = (vc <= 0) & (d1 >= 0) & (d3 <= 0)                              # edge AB
    if m.any():
        out = np.where(m[:, None], A + (d1 / np.maximum(d1 - d3, 1e-30))[:, None] * ab, out)
    m = (d3 >= 0) & (d4 <= d3)                                         # vertex B
    out = np.where(m[:, None], B, out)
    m = (d1 <= 0) & (d2 <= 0)                                          # vertex A
    out = np.where(m[:, None], A, out)
    return out


# ---------------------------------------------------------------------------
# the detector
# ---------------------------------------------------------------------------


class Detector:
    """Self-contact over a surface, with the Solidify shell folded in as a scalar.

    `flat_scale` converts the flat layout's own units into real ones (see the
    module docstring); pass `clo_projector.guide.flat_scale(...)`, which is 1.0
    when it cannot be measured, i.e. the behaviour from before the conversion.

    `tri_c2` overrides the per-triangle flat centroid. A proxy welded across its
    sewn seams can only give a seam vertex one panel's flat coordinate, so the
    mean of a triangle's three would jump the distance between panels in the UV
    layout; the caller samples the centroids from the fine Guide instead.

    `keep_pairs(qi, qt) -> bool mask` drops (vertex, triangle) pairs after the
    2D test -- used to exempt sewn seams.

    `m2d_cap` bounds the Stage 1 relaxation, as a real-space length. One needle
    triangle in the flat layout would otherwise set a slack big enough to gut
    the prefilter.
    """

    def __init__(self, co2, tris, thickness, sign, flat_scale=1.0,
                 tri_c2=None, m2d_cap=None, keep_pairs=None, push_mode="bounded"):
        self.n = len(co2)
        self.tris = np.asarray(tris, dtype=np.int64)
        # One division, here, and every flat-derived quantity below is real-space.
        s = float(flat_scale) if flat_scale else 1.0
        self.co2 = np.asarray(co2, dtype=np.float64) / s
        self.thickness = float(thickness)
        self.sign = float(sign)
        self.with_shell = thickness > 0.0
        self.keep_pairs = keep_pairs
        self.push_mode = push_mode
        T = len(self.tris)
        self.tri_c2 = (self.co2[self.tris].mean(axis=1) if tri_c2 is None
                       else np.asarray(tri_c2, dtype=np.float64) / s)

        # Stage 1 sees partner vertices; Stage 2 tests face centroids. Relax the
        # Stage 1 threshold by that difference or it silently drops contacts --
        # per vertex, never a global maximum: one needle triangle in the flat
        # layout makes a global slack big enough to gut the prefilter, measured
        # as 139 s against 67 s for the same pass.
        d2v = np.linalg.norm(self.co2[self.tris] - self.tri_c2[:, None, :], axis=2)
        if m2d_cap is not None:
            # Real-space, like everything else past the division above.
            d2v = np.minimum(d2v, m2d_cap)
        self.m2d_v = np.zeros(self.n)
        for k in range(3):
            np.maximum.at(self.m2d_v, self.tris[:, k], d2v[:, k])

        vt = self.tris.ravel()
        tid = np.repeat(np.arange(T, dtype=np.int64), 3)
        o = np.argsort(vt, kind="stable")
        self._tid_s = tid[o]
        vt_s = vt[o]
        self._inc_start = np.searchsorted(vt_s, np.arange(self.n))
        self._inc_cnt = (np.searchsorted(vt_s, np.arange(self.n), side="right")
                         - self._inc_start)
        self._geo = None

    # -- geometry that depends on where the vertices are right now -----------
    def _geometry(self, co):
        tris = self.tris
        A, B, C = co[tris[:, 0]], co[tris[:, 1]], co[tris[:, 2]]
        la = np.linalg.norm(C - B, axis=1)
        lb = np.linalg.norm(C - A, axis=1)
        lc = np.linalg.norm(B - A, axis=1)
        cross = np.cross(B - A, C - A)
        area = 0.5 * np.linalg.norm(cross, axis=1)
        good = area > EPS_AREA
        circum = (la * lb * lc) / np.maximum(4.0 * area, 1e-18)
        mx = np.maximum(np.maximum(la, lb), lc)
        # The point of a triangle farthest from all three vertices: the
        # circumcentre when acute, the long edge's midpoint when obtuse.
        rho = np.where(mx ** 2 > la ** 2 + lb ** 2 + lc ** 2 - mx ** 2, mx * 0.5, circum)
        rho = np.where(good, rho, 0.0)
        pad = float(np.percentile(rho[good], RHO_PCT)) if good.any() else 0.0
        rho_v = np.zeros(self.n)
        for k in range(3):
            np.maximum.at(rho_v, tris[:, k], rho)
        cent = (A + B + C) / 3.0
        rho_c = np.maximum(np.maximum(np.linalg.norm(A - cent, axis=1),
                                      np.linalg.norm(B - cent, axis=1)),
                           np.linalg.norm(C - cent, axis=1))
        fn = cross / np.maximum(np.linalg.norm(cross, axis=1), 1e-18)[:, None]
        self._geo = dict(rho_v=rho_v, pad=pad, good=good, cent=cent, rho_c=rho_c,
                         fn=fn, big=np.flatnonzero(good & (rho > pad)),
                         nrm=vertex_normals(co, tris) if self.with_shell else None)
        return self._geo

    # -- stage 1 -------------------------------------------------------------
    def _pairs(self, co, cell, query):
        """(step 0..26, query vertex, any vertex) for every pair within one cell.

        The step index is yielded so the caller can report progress: this loop is
        a third of the detection and there is nothing else to count inside it.
        """
        key = np.floor(co / cell).astype(np.int64)
        key -= key.min(0)
        dims = key.max(0) + 3
        code = (key[:, 0] * dims[1] + key[:, 1]) * dims[2] + key[:, 2]
        order = np.argsort(code, kind="stable")
        uniq, start, cnt = np.unique(code[order], return_index=True, return_counts=True)
        nu = len(uniq)
        qcode = code[query]
        step = 0
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    step += 1
                    tgt = qcode + (dx * dims[1] + dy) * dims[2] + dz
                    pos = np.clip(np.searchsorted(uniq, tgt), 0, nu - 1)
                    ok = uniq[pos] == tgt
                    if not ok.any():
                        yield step, None, None
                        continue
                    vi = query[ok]
                    c, s = cnt[pos[ok]], start[pos[ok]]
                    yield (step, np.repeat(vi, c),
                           order[np.repeat(s, c) + ragged_range(c)])

    # -- stage 2 -------------------------------------------------------------
    def _accumulate(self, gap, push, dirsum, qi, qt, radius, co):
        """push and dirsum may both be None when only the gap is wanted."""
        g = self._geo
        keep = g["good"][qt]
        if not keep.all():
            qi, qt = qi[keep], qt[keep]
            if len(qi) == 0:
                return
        tr = self.tris[qt]
        P = co[qi]
        v = P - closest_on_tri(P, co[tr[:, 0]], co[tr[:, 1]], co[tr[:, 2]])
        d = np.sqrt(np.einsum("ij,ij->i", v, v))
        t2 = 2.0 * self.thickness if self.with_shell else 0.0
        m = (d > EPS_D) & (d <= radius + t2)
        if not m.any():
            return
        qi, qt, d, v = qi[m], qt[m], d[m], v[m]
        w = self.tri_c2[qt] - self.co2[qi]
        d2 = np.sqrt(np.einsum("ij,ij->i", w, w))
        m = d2 > np.maximum(RATIO_2D * d, ABS_2D_M)
        if not m.any():
            return
        qi, qt, d, v = qi[m], qt[m], d[m], v[m]
        if self.keep_pairs is not None:
            m = self.keep_pairs(qi, qt)
            if not m.any():
                return
            qi, qt, d, v = qi[m], qt[m], d[m], v[m]
        if self.with_shell:
            # The shell only eats into the gap where it grows toward the
            # partner, and only by its projection onto that direction --
            # a yes/no test on the sign subtracts the full thickness sideways.
            t = self.thickness
            u = -v / d[:, None]                     # from P toward the other layer
            red_p = np.clip(self.sign * t * np.einsum("ij,ij->i", g["nrm"][qi], u), 0.0, t)
            red_t = np.clip(-self.sign * t * np.einsum("ij,ij->i", g["fn"][qt], u), 0.0, t)
            d = np.maximum(d - red_p - red_t, 0.0)
            m = (d > EPS_D) & (d <= radius)
            if not m.any():
                return
            qi, d, v = qi[m], d[m], v[m]
        np.fmin.at(gap, qi, d)
        if push is None and dirsum is None:
            return
        u_away = v / np.maximum(d, EPS_D)[:, None]
        if dirsum is None:
            np.add.at(push, qi, u_away * ((radius - d) * 0.5)[:, None])
        else:
            np.add.at(dirsum, qi, u_away * (radius - d)[:, None])

    # -- public --------------------------------------------------------------
    def run(self, co, radius, candidates=None, progress=None):
        """(gap, push) per vertex. gap is inf where nothing is within radius.

        `progress(fraction)` is called as the stages complete. The weights are
        the measured split of a solve's detection time -- Stage 1 a third,
        deduplication a seventh, Stage 2 a half -- so the bar moves at roughly
        the rate the work does rather than in equal jumps.

        `push` is half the missing distance, once, in the mean of the hit
        directions. Summing it over every hit face instead -- which is what the
        first version did -- overshoots by a median of 34x and a maximum of
        143x, and a garment inflates: measured 450 mm of displacement on a
        shirt whose whole bounding box is 373 mm.
        """
        co = np.ascontiguousarray(co, dtype=np.float64)
        n = self.n
        g = self._geometry(co)
        pad, rho_v = g["pad"], g["rho_v"]
        t2 = 2.0 * self.thickness if self.with_shell else 0.0
        gap = np.full(n, np.inf)
        push = np.zeros((n, 3))
        bounded = self.push_mode == "bounded"
        dirsum = np.zeros((n, 3)) if bounded else None
        acc_push = None if bounded else push
        query = (np.arange(n, dtype=np.int64) if candidates is None
                 else np.asarray(candidates, dtype=np.int64))
        if len(query) == 0:
            return gap, push

        def tick(frac):
            if progress is not None:
                progress(min(max(frac, 0.0), 1.0))

        tick(0.0)
        cell = radius + t2 + pad
        QI, QT = [], []
        for step, I, J in self._pairs(co, cell, query):
            tick(0.33 * step / 27.0)
            if I is None:
                continue
            m = I != J
            if not m.any():
                continue
            I, J = I[m], J[m]
            v = co[J] - co[I]
            d = np.sqrt(np.einsum("ij,ij->i", v, v))
            m = (d > EPS_D) & (d <= cell)
            if not m.any():
                continue
            I, J, d = I[m], J[m], d[m]
            d_lb = np.maximum(d - rho_v[J] - t2, 0.0)      # strict lower bound
            w = self.co2[J] - self.co2[I]
            d2 = np.sqrt(np.einsum("ij,ij->i", w, w))
            m = d2 > np.maximum(RATIO_2D * d_lb, ABS_2D_M) - self.m2d_v[J]
            if not m.any():
                continue
            I, J = I[m], J[m]
            c = self._inc_cnt[J]
            k = c > 0
            if k.any():
                Ik, Jk, ck = I[k], J[k], c[k]
                QI.append(np.repeat(Ik, ck))
                QT.append(self._tid_s[np.repeat(self._inc_start[Jk], ck)
                                      + ragged_range(ck)])
            # A partner with no triangle of its own -- an isolated vertex, or
            # one whose every triangle was dropped as degenerate -- has no
            # surface for Stage 2 to measure, so bound its gap by the vertex
            # distance. Only for those: Stage 1's 2D test is deliberately
            # relaxed, and feeding all of its pairs into `gap` would invent
            # contacts the strict centroid test rejects.
            lone = ~k
            if lone.any():
                dv = co[J[lone]] - co[I[lone]]
                np.fmin.at(gap, I[lone], np.sqrt(np.einsum("ij,ij->i", dv, dv)))

        if QI:
            QI = np.concatenate(QI)
            QT = np.concatenate(QT)
            # A triangle is incident to three vertices, so the expansion above
            # reaches the same (vertex, triangle) pair up to three times. The
            # duplicates inflate `push` by 2.2x and triple Stage 2's work.
            #
            # The key encodes the pair uniquely (QT < len(tris)), so it can be
            # unpacked again and no index is needed. Asking np.unique for
            # return_index costs a *stable* argsort plus the index array --
            # measured 1.037 s against 0.210 s for this form over 4M pairs.
            # Pairs come out in key order rather than discovery order, which
            # `_accumulate` does not care about: it only scatters through
            # np.fmin.at and np.add.at.
            T = len(self.tris)
            key = np.unique(QI * T + QT)
            QI, QT = np.divmod(key, T)
            tick(0.47)
            nchunk = max((len(QI) + CHUNK - 1) // CHUNK, 1)
            for c, s in enumerate(range(0, len(QI), CHUNK)):
                self._accumulate(gap, acc_push, dirsum, QI[s:s + CHUNK],
                                 QT[s:s + CHUNK], radius, co)
                tick(0.47 + 0.50 * (c + 1) / nchunk)

        big = g["big"]
        if len(big):
            kd = KDTree(n)
            for i in range(n):
                kd.insert(Vector(co[i]), i)
            kd.balance()
            in_q = np.zeros(n, dtype=bool)
            in_q[query] = True
            bi, bt = [], []
            lim = radius + t2
            for t in big:
                for _co, vi, _d in kd.find_range(Vector(g["cent"][t]),
                                                 float(g["rho_c"][t] + lim)):
                    if in_q[vi]:
                        bi.append(vi)
                        bt.append(t)
            if bi:
                self._accumulate(gap, acc_push, dirsum, np.asarray(bi, dtype=np.int64),
                                 np.asarray(bt, dtype=np.int64), radius, co)

        tick(1.0)
        gap[gap > radius] = np.inf
        if bounded:
            near = np.isfinite(gap)
            ln = np.linalg.norm(dirsum, axis=1)
            live = near & (ln > 1e-18)
            push[live] = (dirsum[live] / ln[live][:, None]
                          * ((radius - gap[live]) * 0.5)[:, None])
        return gap, push
