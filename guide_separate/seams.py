"""Sewn seams: find them, exempt them from detection, hold them shut.

A CLO Guide needs no guessing here. Panels are exported unwelded, so the two
sides of a stitch sit on the same coordinates, and every panel outline is a
boundary edge. "Sewn vertex" is therefore exactly "boundary vertex that
coincides with another one".

Two separate jobs come out of that, and both are needed -- the session that
built this shipped the first one alone and the seams still opened by 0.821 mm
at the median.

*Exemption.* Two sewn panels are near in 3D and in different UV islands, so the
detector calls every stitch an overlap and pushes the groove between panels
open. `make_keep_fn` drops those pairs. This stops the seam from CAUSING a
push; it does nothing when a panel moves for some other reason.

*The twin constraint.* `co0[A] == co0[B]` for a sewn pair, so giving both the
same displacement keeps the distance between them identically zero -- the seam
cannot come apart, as a property of the field rather than of the topology, so
the Guide's own Flat SK is never touched. (Welding the fine Guide instead was
tried: it damages 978 vertices by up to 901 mm, because a welded seam vertex
can only carry one panel's flat coordinate.)

Distance, not ring count
------------------------
The exemption's first form dilated k rings on a graph with the sewn pairs
welded. It works, but `k = ceil(gap/median_edge)+1` is 4 on a 1.62 mm Guide and
26 on a 0.162 mm one, so the rule silently changes meaning with CLO's particle
distance -- and the ring sets stop being affordable (8.6M pairs at k=4, 37 s to
build). The distance form says the same thing in a unit that does not move, and
builds in 0.15 s.
"""

import numpy as np

from .detect import ragged_range

WELD_TOL_M = 1e-5      # CLO's coincident stitch vertices measure well under 1 um


def cell_pairs(co_hash, co_query, cell):
    """(query index, hashed index) for every pair within one cell of each other."""
    key = np.floor(co_hash / cell).astype(np.int64)
    lo = key.min(0)
    key -= lo
    qkey = np.floor(co_query / cell).astype(np.int64) - lo
    dims = np.maximum(key.max(0), qkey.max(0)) + 3
    code = (key[:, 0] * dims[1] + key[:, 1]) * dims[2] + key[:, 2]
    qcode = (qkey[:, 0] * dims[1] + qkey[:, 1]) * dims[2] + qkey[:, 2]
    order = np.argsort(code, kind="stable")
    uniq, start, cnt = np.unique(code[order], return_index=True, return_counts=True)
    nu = len(uniq)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                tgt = qcode + (dx * dims[1] + dy) * dims[2] + dz
                pos = np.clip(np.searchsorted(uniq, tgt), 0, nu - 1)
                ok = uniq[pos] == tgt
                vi = np.flatnonzero(ok)
                if not len(vi):
                    continue
                c, s = cnt[pos[ok]], start[pos[ok]]
                yield np.repeat(vi, c), order[np.repeat(s, c) + ragged_range(c)]


def boundary_mask(tris, edges, n):
    """(per-vertex boundary flag, per-edge face count)."""
    e0 = edges[:, 0].astype(np.int64)
    e1 = edges[:, 1].astype(np.int64)
    ekey = np.minimum(e0, e1) * n + np.maximum(e0, e1)
    te = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]).astype(np.int64)
    tkey = np.minimum(te[:, 0], te[:, 1]) * n + np.maximum(te[:, 0], te[:, 1])
    order = np.argsort(ekey)
    pos = np.searchsorted(ekey[order], tkey)
    cnt = np.zeros(len(edges), dtype=np.int32)
    ok = (pos < len(ekey)) & (ekey[order][np.clip(pos, 0, len(ekey) - 1)] == tkey)
    np.add.at(cnt, order[pos[ok]], 1)
    bnd = np.zeros(n, dtype=bool)
    bnd[e0[cnt == 1]] = True
    bnd[e1[cnt == 1]] = True
    return bnd, cnt


def sewn_vertices(co, tris, edges, tol=WELD_TOL_M):
    """(indices of boundary vertices that coincide with another, boundary mask)."""
    n = len(co)
    bnd, _cnt = boundary_mask(tris, edges, n)
    bi = np.flatnonzero(bnd)
    if len(bi) == 0:
        return np.empty(0, np.int64), bnd
    sub = co[bi]
    hit = np.zeros(len(bi), dtype=bool)
    for qi, hj in cell_pairs(sub, sub, max(tol, 1e-9)):
        m = qi != hj
        if not m.any():
            continue
        qi, hj = qi[m], hj[m]
        m = np.linalg.norm(sub[qi] - sub[hj], axis=1) <= tol
        if m.any():
            hit[qi[m]] = True
    return bi[hit], bnd


def twin_pairs(co, tris, edges, tol=WELD_TOL_M):
    """(A, B) index arrays of coincident sewn vertices, each pair once."""
    idx, _bnd = sewn_vertices(co, tris, edges, tol)
    if len(idx) == 0:
        return np.empty(0, np.int64), np.empty(0, np.int64)
    sub = co[idx]
    A, B = [], []
    for qi, hj in cell_pairs(sub, sub, max(tol, 1e-9)):
        m = qi < hj
        if not m.any():
            continue
        qi, hj = qi[m], hj[m]
        m = np.linalg.norm(sub[qi] - sub[hj], axis=1) <= tol
        if m.any():
            A.append(idx[qi[m]])
            B.append(idx[hj[m]])
    if not A:
        return np.empty(0, np.int64), np.empty(0, np.int64)
    return np.concatenate(A), np.concatenate(B)


class TwinGroups:
    """Sewn vertices grouped by connectivity, so a whole group agrees at once.

    Grouping and not a walk over the pairs: where three or more panels meet at a
    corner a vertex belongs to several pairs, and writing `D[A] = D[B] = mean`
    pair by pair lets the last write win, so the group never converges --
    measured 0.121 mm of residual at exactly those corners. Averaging over the
    whole connected group closes it.
    """

    def __init__(self, n, twin_a, twin_b):
        self.a, self.b = twin_a, twin_b
        self.count = len(twin_a)
        self.groups = 0
        if self.count == 0:
            return
        par = np.arange(n)

        def find(x):
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x

        for ia, ib in zip(twin_a, twin_b):
            ra, rb = find(int(ia)), find(int(ib))
            if ra != rb:
                par[ra] = rb
        self.verts = np.unique(np.concatenate([twin_a, twin_b]))
        roots = np.array([find(int(i)) for i in self.verts])
        uniq, self.gid = np.unique(roots, return_inverse=True)
        self.groups = len(uniq)
        self.gcnt = np.bincount(self.gid, minlength=self.groups).astype(np.float64)

    def project(self, D):
        """In place: every vertex of a group receives the group's mean."""
        if self.groups == 0:
            return D
        sub = D[self.verts]
        mean = np.empty((self.groups, 3))
        for c in range(3):
            mean[:, c] = np.bincount(self.gid, weights=sub[:, c],
                                     minlength=self.groups) / self.gcnt
        D[self.verts] = mean[self.gid]
        return D


def seam_distance(co, seam_pos, reach):
    """Per-point (distance to the nearest seam position, its index in seam_pos).

    Positions rather than indices, so the field can be evaluated on a mesh that
    does not contain the seam vertices: the proxy is decimated and its own
    boundary has moved, so its seams have to come from the fine Guide.
    Only computed out to `reach` -- beyond it the distance is inf and the index
    -1, which is all the exemption needs and keeps the pair count small.
    """
    n = len(co)
    d = np.full(n, np.inf)
    near = np.full(n, -1, dtype=np.int64)
    if len(seam_pos) == 0 or reach <= 0.0:
        return d, near
    for qi, hj in cell_pairs(seam_pos, co, reach):
        dd = np.linalg.norm(co[qi] - seam_pos[hj], axis=1)
        m = dd <= reach
        if not m.any():
            continue
        qi, hj, dd = qi[m], hj[m], dd[m]
        # np.minimum.at cannot carry the argmin, so let the last write win:
        # sort descending and the closest is written last.
        o = np.argsort(-dd)
        qb, hb, db = qi[o], hj[o], dd[o]
        keep = db < d[qb]
        d[qb[keep]] = db[keep]
        near[qb[keep]] = hb[keep]
    return d, near


def make_keep_fn(co, tris, seam_pos, reach):
    """(keep_pairs for Detector, per-vertex seam distance).

    A contact is dropped when both ends sit within `reach` of the SAME seam.
    Both ends, so a different panel draped across the seam is still detected --
    it is not the sewn continuation. The same seam, because two unrelated seams
    can pass near each other; sewn partners coincide, so a seam is one curve in
    3D and "the same seam" is just "the two nearest seam points are within
    `reach` of each other".
    """
    dseam, nseam = seam_distance(co, seam_pos, reach)
    tri_d = dseam[tris]
    arg = np.argmin(tri_d, axis=1)
    rows = np.arange(len(tris))
    tri_best_d = tri_d[rows, arg]
    tri_best_seam = nseam[tris[rows, arg]]

    def keep(qi, qt):
        cand = (dseam[qi] <= reach) & (tri_best_d[qt] <= reach)
        if not cand.any():
            return np.ones(len(qi), dtype=bool)
        si, st = nseam[qi], tri_best_seam[qt]
        same = np.zeros(len(qi), dtype=bool)
        c = np.flatnonzero(cand & (si >= 0) & (st >= 0))
        if len(c):
            same[c] = np.linalg.norm(seam_pos[si[c]] - seam_pos[st[c]], axis=1) <= reach
        return ~(cand & same)

    return keep, dseam
