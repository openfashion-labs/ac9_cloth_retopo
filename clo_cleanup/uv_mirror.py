"""UV Mirror — copy one side's edited UV layout onto its mirror partner,
matched through the untouched pattern layout rather than through topology
or 3D position.

Where this sits in Prepare
--------------------------
After Inset Line / Inset Pieces (so the dart's fold band exists as
geometry), the user welds the dart edges (Merge by Distance), stitches the
dart closed in the UV editor on ONE side, relaxes it (Unwrap / Minimize
Stretch), and then runs this to give the other side the identical, mirrored
result. Flat SK is re-created from the finished UV afterwards.

Why match through a reference UV
--------------------------------
Left and right pattern pieces are mirror images as 2D patterns but NOT in 3D
(cloth simulation drapes each side differently — see
analysis.detect_mirror_pairs) and NOT in topology (CLO meshes each piece on
its own, so vertex counts differ — measured 3 of 7 pairs on a production
jacket). Blender's own tools fail on exactly these two points:
`mesh.faces_mirror_uv` looks for 3D-mirrored vertices, `uv.paste` needs
identical island topology. The outlines DO match (2D reflection RMS
0.000000 on every true pair), so the transfer here is geometric: a copy of
the original CLO UV is kept as the *reference* layer, a target vertex's
reference position is reflected into the source island's reference space,
the source triangle containing it is found, and the source's *edited* UV is
interpolated there with barycentric weights. Topology never has to agree.

Reference layer
---------------
`make_reference_uv` duplicates the active UV layer into REF_UV_NAME once,
before any editing. Everything below reads island structure and mirror
transforms from that layer only; the working layer is what gets written.

Temporary split mesh
--------------------
The pair / fold detectors in uv_seam_guide.analysis take an object whose
mesh is already physically split per UV island (a Flat-SK Guide). Before
Flatten, or after Merge by Distance, the CLO mesh is not — so a throwaway
mesh is built whose vertices are the distinct (vertex, reference-UV)
corners, laid out at their reference UV. That IS a per-island split mesh,
so both detectors run on it unchanged. It is unlinked and deleted before
returning.

Placement
---------
Pair mode: the source island's relaxed layout is reflected (same reflection
the detector found) and centred on the target island's original centroid —
"the partner stays where it was, as a mirror image".
Self mode (one island, cut-on-fold): the fold line's two endpoints are
mapped through the same lookup, giving the fold's position in the edited
layout; the kept half is reflected across that.
"""

import math

import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform, intersect_point_tri_2d

from ..uv_seam_guide import analysis

REF_UV_NAME = "AC9_UV_Reference"
_TMP_NAME = "AC9_tmp_uv_mirror"
_QUANT = 1e-6          # reference-UV quantisation when merging loop corners
_CHANGED_EPS = 1e-6    # working != reference beyond this -> island was edited


def make_reference_uv(bm, src_name):
    """Copy UV layer `src_name` into REF_UV_NAME (created or overwritten).
    Returns the number of loops copied."""
    src = bm.loops.layers.uv.get(src_name)
    if src is None:
        raise RuntimeError(f"UV layer '{src_name}' not found.")
    ref = bm.loops.layers.uv.get(REF_UV_NAME)
    if ref is None:
        ref = bm.loops.layers.uv.new(REF_UV_NAME)
        # .new() may invalidate the src handle: fetch it again.
        src = bm.loops.layers.uv.get(src_name)
    n = 0
    for f in bm.faces:
        for l in f.loops:
            l[ref].uv = l[src].uv
            n += 1
    return n


def _reflection(theta_deg):
    """2x2 matrix R(theta) @ diag(-1, 1) — detect_mirror_pairs' pair transform.
    It is an involution (a plane reflection), so the same matrix maps a -> b
    and b -> a; only the centroids swap."""
    return Matrix.Rotation(math.radians(theta_deg), 2) @ Matrix(((-1.0, 0.0), (0.0, 1.0)))


def _reflect_across(p, a, n):
    """Reflect 2D `p` across the line through `a` with unit normal `n`."""
    return p - 2.0 * (p - a).dot(n) * n


def _corner_key(loop, ref_layer):
    uv = loop[ref_layer].uv
    return (loop.vert.index, round(uv.x / _QUANT), round(uv.y / _QUANT))


class UVSpace:
    """Reference-UV view of a bmesh: distinct corners ("tv"), their loops,
    per-face corner tuples, and the temporary split mesh the detectors need."""

    def __init__(self, bm, ref_name, work_name):
        self.bm = bm
        self.ref = bm.loops.layers.uv.get(ref_name)
        self.work = bm.loops.layers.uv.get(work_name)
        if self.ref is None:
            raise RuntimeError(f"Reference UV layer '{ref_name}' not found — run Make Reference first.")
        if self.work is None:
            raise RuntimeError(f"UV layer '{work_name}' not found.")
        if ref_name == work_name:
            raise RuntimeError("The active UV layer is the reference layer — make another layer active.")

        bm.faces.ensure_lookup_table()
        bm.faces.index_update()
        bm.verts.index_update()

        self.key_to_tv = {}
        self.tv_ref = []       # Vector2 per tv
        self.tv_loops = []     # [BMLoop, ...] per tv
        self.faces_tv = []     # tuple of tv per bm face (bm.faces order)
        for f in bm.faces:
            corners = []
            for l in f.loops:
                key = _corner_key(l, self.ref)
                tv = self.key_to_tv.get(key)
                if tv is None:
                    tv = len(self.tv_ref)
                    self.key_to_tv[key] = tv
                    uv = l[self.ref].uv
                    self.tv_ref.append(Vector((uv.x, uv.y)))
                    self.tv_loops.append([])
                self.tv_loops[tv].append(l)
                corners.append(tv)
            self.faces_tv.append(tuple(corners))

        self.tmp_ob = None
        self.tmp_me = None
        self.islands = None      # {root: [tv]}
        self.root_of_tv = None
        self.faces_by_root = None
        self.pairs = []

    # ---- temporary split mesh -------------------------------------------

    def build_temp(self, context):
        me = bpy.data.meshes.new(_TMP_NAME)
        me.from_pydata([(p.x, p.y, 0.0) for p in self.tv_ref], [], self.faces_tv)
        me.update()
        ob = bpy.data.objects.new(_TMP_NAME, me)
        context.scene.collection.objects.link(ob)
        ob.shape_key_add(name="Basis", from_mix=False)
        ob.shape_key_add(name="Flat", from_mix=False)
        self.tmp_ob, self.tmp_me = ob, me

        self.islands = analysis.compute_islands_cached(me)
        self.root_of_tv = {}
        for root, idxs in self.islands.items():
            for i in idxs:
                self.root_of_tv[i] = root
        self.faces_by_root = {}
        for fi, corners in enumerate(self.faces_tv):
            self.faces_by_root.setdefault(self.root_of_tv[corners[0]], []).append(fi)

    def free_temp(self):
        if self.tmp_ob is not None:
            bpy.data.objects.remove(self.tmp_ob)
            self.tmp_ob = None
        if self.tmp_me is not None:
            bpy.data.meshes.remove(self.tmp_me)
            self.tmp_me = None

    # ---- helpers ----------------------------------------------------------

    def tv_work(self, tv):
        uv = self.tv_loops[tv][0][self.work].uv
        return Vector((uv.x, uv.y))

    def island_changed(self, root):
        for tv in self.islands[root]:
            if (self.tv_work(tv) - self.tv_ref[tv]).length > _CHANGED_EPS:
                return True
        return False

    def tv_of_loop(self, loop):
        tv = self.key_to_tv.get(_corner_key(loop, self.ref))
        if tv is None:
            raise RuntimeError("Selected corner not found in reference layout.")
        return tv

    def _lookup_builder(self, face_ids):
        """BVH over `face_ids` in reference space + a function mapping a
        reference-space 2D point to the interpolated WORKING UV there."""
        verts3 = [(p.x, p.y, 0.0) for p in self.tv_ref]
        polys = [self.faces_tv[f] for f in face_ids]
        bvh = BVHTree.FromPolygons(verts3, polys, all_triangles=False)
        ref, work_of = self.tv_ref, self.tv_work

        def lookup(p):
            loc, _n, idx, _d = bvh.find_nearest(Vector((p.x, p.y, 0.0)))
            if idx is None:
                return None
            corners = self.faces_tv[face_ids[idx]]
            p2 = Vector((loc.x, loc.y))
            best = None
            for i in range(1, len(corners) - 1):
                a, b, c = corners[0], corners[i], corners[i + 1]
                ra, rb, rc = ref[a], ref[b], ref[c]
                area2 = abs((rb - ra).cross(rc - ra))
                if area2 < 1e-16:
                    continue
                inside = intersect_point_tri_2d(p2, ra, rb, rc)
                d = 0.0 if inside else (p2 - (ra + rb + rc) / 3.0).length
                if best is None or d < best[0]:
                    best = (d, a, b, c)
                if inside:
                    break
            if best is None:
                return None
            _, a, b, c = best
            q = barycentric_transform(
                Vector((p2.x, p2.y, 0.0)),
                ref[a].to_3d(), ref[b].to_3d(), ref[c].to_3d(),
                work_of(a).to_3d(), work_of(b).to_3d(), work_of(c).to_3d())
            return Vector((q.x, q.y))

        return lookup

    def _write(self, tv, uv):
        for l in self.tv_loops[tv]:
            l[self.work].uv = uv

    # ---- pair mode --------------------------------------------------------

    def mirror_pairs(self, tolerance=0.02, progress=None, selected_roots=None):
        """Write the source island's edit, mirrored and interpolated, onto its
        partner island for every detected mirror pair.

        Which side is the source ("correct") follows the same rule as Self and
        Faces > Symmetry: the island holding a SELECTED vertex. `selected_roots`
        (island roots of the selected vertices) restricts the run to those
        pairs; a pair with both islands selected is skipped. With nothing
        selected (None / empty) every pair runs and the side that differs from
        the reference UV is the source — the one-click first pass. Returns a
        stats dict."""
        pairs, n_tested, _rej = analysis.detect_mirror_pairs(
            self.tmp_ob, "Flat", tolerance=tolerance, progress=progress)
        stats = {"n_pairs": len(pairs), "n_tested": n_tested, "mirrored": 0,
                 "skipped_unchanged": 0, "skipped_both": 0, "skipped_same_handed": 0,
                 "skipped_unselected": 0, "verts_written": 0, "failed": 0,
                 "by_selection": bool(selected_roots)}
        self.pairs = pairs
        for pair in pairs:
            if pair["same_handed"]:
                stats["skipped_same_handed"] += 1
                continue
            ra, rb = pair["root_a"], pair["root_b"]
            if selected_roots:
                ca, cb = ra in selected_roots, rb in selected_roots
                if ca and cb:
                    stats["skipped_both"] += 1
                    continue
                if not (ca or cb):
                    stats["skipped_unselected"] += 1
                    continue
            else:
                ca, cb = self.island_changed(ra), self.island_changed(rb)
                if ca == cb:
                    stats["skipped_both" if ca else "skipped_unchanged"] += 1
                    continue
            src, dst = (ra, rb) if ca else (rb, ra)
            c_src = pair["centroid_a" if ca else "centroid_b"].xy
            c_dst = pair["centroid_b" if ca else "centroid_a"].xy
            M = _reflection(pair["theta_deg"])
            src_tvs = self.islands[src]
            c_src_new = sum((self.tv_work(t) for t in src_tvs), Vector((0.0, 0.0))) / len(src_tvs)
            lookup = self._lookup_builder(self.faces_by_root[src])
            for tv in self.islands[dst]:
                p = M @ (self.tv_ref[tv] - c_dst) + c_src
                q = lookup(p)
                if q is None:
                    stats["failed"] += 1
                    continue
                self._write(tv, M @ (q - c_src_new) + c_dst)
                stats["verts_written"] += 1
            stats["mirrored"] += 1
        return stats

    # ---- self mode --------------------------------------------------------

    def mirror_self(self, keep_loop, tolerance=0.015, progress=None):
        """One cut-on-fold island: the half containing `keep_loop`'s corner is
        the source; the other half receives its reflection across the fold.
        Returns a stats dict."""
        tv_keep = self.tv_of_loop(keep_loop)
        root = self.root_of_tv[tv_keep]
        _segs, _nt, _ns, folds = analysis.detect_island_symmetry(
            self.tmp_ob, "Flat", tolerance=tolerance, progress=progress)
        folds = [f for f in folds if f["root"] == root]
        if not folds:
            raise RuntimeError("The selected vertex's island has no detected fold line (not self-symmetric).")

        # Doubly-symmetric islands have two folds: take the one the selected
        # vertex is most clearly to one side of.
        p_keep = self.tv_ref[tv_keep]
        best = None
        for f in folds:
            a, b = f["a"].xy, f["b"].xy
            d = b - a
            if d.length < 1e-12:
                continue
            n = Vector((-d.y, d.x)).normalized()
            s = (p_keep - a).dot(n) / d.length
            if best is None or abs(s) > abs(best[0]):
                best = (s, a, b, n, d.length)
        if best is None:
            raise RuntimeError("Degenerate fold line.")
        s_keep, a, b, n, fold_len = best
        eps = 1e-5 * fold_len
        if abs(s_keep) * fold_len <= eps:
            raise RuntimeError("Select a vertex clearly on one side of the fold line, not on it.")
        keep_sign = 1.0 if s_keep > 0 else -1.0

        tvs = self.islands[root]
        side = {tv: (self.tv_ref[tv] - a).dot(n) * keep_sign for tv in tvs}
        # Every face touching the kept side, so faces straddling the fold
        # still cover reflected points that land right next to it.
        src_faces = [fi for fi in self.faces_by_root[root]
                     if any(side[t] > eps for t in self.faces_tv[fi])]
        lookup = self._lookup_builder(src_faces)

        # The fold line in the EDITED layout: map its two endpoints through
        # the same lookup, then reflect the kept half across that line.
        a_new, b_new = lookup(a), lookup(b)
        if a_new is None or b_new is None or (b_new - a_new).length < 1e-12:
            raise RuntimeError("Could not locate the fold line in the edited layout.")
        d_new = b_new - a_new
        n_new = Vector((-d_new.y, d_new.x)).normalized()

        stats = {"root": root, "n_folds": len(folds), "kept": 0, "axis": 0,
                 "verts_written": 0, "failed": 0}
        for tv in tvs:
            s = side[tv]
            if s > eps:
                stats["kept"] += 1
                continue
            if s >= -eps:
                stats["axis"] += 1
                continue
            q = lookup(_reflect_across(self.tv_ref[tv], a, n))
            if q is None:
                stats["failed"] += 1
                continue
            self._write(tv, _reflect_across(q, a_new, n_new))
            stats["verts_written"] += 1
        return stats
