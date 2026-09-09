"""Guide Separate — push self-touching drape layers of the Guide apart.

Why
---
Tightly draped garments (pleats, wrap skirts, gathered fabric) leave layers
of the high-poly Guide touching or a millimetre apart. Every ray-cast bake
(normal, AO, curvature; Blender, Substance, Marmoset alike) then hits the
neighbouring layer instead of the one the low-poly vertex belongs to, and
the map is peppered with wrong-layer artifacts. Weight transfer by nearest
surface suffers the same way. The manual fix is to nudge layers apart with
proportional editing — slow, irreversible, and easy to overdo.

What
----
A deterministic pass over the Guide that opens a minimum gap between
layers while leaving the drape shape visually unchanged, stored as the
ShapeKey ``AC9_Separated`` on the Guide (the Basis stays the original):

1. Detect self-contact: a vertex is in contact when another surface is
   within *gap* in 3D **and** far away in the 2D flat layout. The 2D test
   is what makes this reliable — an adjacent part of the same panel is
   close in both spaces, a different layer is close only in 3D — so no
   heuristic on normals or topology is needed.
2. Push: each contact vertex gets half the missing distance, directed away
   from the surface it touches. Both layers move, meeting in the middle.
3. Smooth the *displacement field* (not the positions) with a uniform
   Laplacian. The push is a low-frequency correction; the drape's folds
   are high-frequency detail; smoothing the displacement keeps the two
   apart, so the surface does not go lumpy. The smoothing radius sets how
   wide the opening is spread; iterations are derived from it and the
   Guide's edge length.
4. Repeat until every contact vertex has the gap or the count stops
   improving (a gap that cannot be reached, see below).

Solidify
--------
Bakes are usually done with a Solidify modifier on the Guide so the
low-poly's inner side has something to hit. The shell that Solidify adds
(vertex − thickness × normal, or + for a positive offset) is included in
the detection: each base vertex is pushed when either it or its shell
vertex touches another layer. The thickness is read from the Guide's own
Solidify modifier; nothing is added to the mesh.

A shell thicker than the spacing between layers penetrates the neighbour
inside every fold and no amount of pushing resolves it — the iteration
plateaus and the operator reports the unresolved count. In practice the
bake thickness has to stay below the drape spacing (a couple of
millimetres for garment-scale folds).

The gap check that runs before / after (``AC9_Gap`` colour attribute:
red = less than half the gap, yellow = between, green = reached, grey =
no other layer nearby) is the diagnostic for both cases.

Coordinates are handled in world space like the rest of the projector;
results are written back in the Guide's local space.
"""

import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from ..clo_projector.guide import (
    SEPARATED_SK_NAME,
    _extract_tri_verts_np,
    _extract_world_co_np,
    validate_guide,
)

GAP_ATTR = "AC9_Gap"

# A hit counts as another layer when its 2D distance exceeds BOTH of these:
# a multiple of the 3D distance (a fold's crease is the tightest legitimate
# 2D-near / 3D-near case) and an absolute floor (the 2D distance across a
# fold is at least the fold's circumference).
RATIO_2D = 4.0
ABS_2D_M = 0.015

SMOOTH_STEPS_MIN = 10
SMOOTH_STEPS_MAX = 600

# Plateau detection: stop when the unresolved count has not dropped by this
# fraction over PLATEAU_WINDOW consecutive iterations.
PLATEAU_WINDOW = 3
PLATEAU_MIN_DROP = 0.02


@dataclass
class SeparateStats:
    contact_before: int = 0          # base vertices within gap before
    unresolved: int = 0              # base vertices still within gap after
    min_gap_mm: float = 0.0
    max_disp_mm: float = 0.0
    p99_disp_mm: float = 0.0
    normal_p99_deg: float = 0.0
    iterations: int = 0
    smooth_steps: int = 0
    thickness_mm: float = 0.0
    converged: bool = False
    seconds: float = 0.0
    gap: Optional[np.ndarray] = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Guide data
# ---------------------------------------------------------------------------


def solidify_params(guide_obj, include: bool) -> Tuple[float, float]:
    """(thickness, sign) of the Guide's first Solidify modifier, or (0, 0).

    The sign is the direction of the shell along the vertex normal (Solidify
    offset −1 builds the shell inward, +1 outward). Disabled-in-viewport
    modifiers still count: the bake typically enables them for render only.
    """
    if not include:
        return 0.0, 0.0
    for m in guide_obj.modifiers:
        if m.type == 'SOLIDIFY' and (m.show_viewport or m.show_render):
            if m.thickness <= 0.0:
                return 0.0, 0.0
            sign = -1.0 if m.offset < 0.0 else 1.0
            return float(m.thickness), sign
    return 0.0, 0.0


def _world_matrix_parts(guide_obj):
    m = np.array(guide_obj.matrix_world, dtype=np.float64)
    return m[:3, :3], m[:3, 3]


def _edges_np(mesh) -> np.ndarray:
    e = np.empty(len(mesh.edges) * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", e)
    return e.reshape(-1, 2)


def _vertex_normals(co: np.ndarray, tris: np.ndarray) -> np.ndarray:
    a, b, c = co[tris[:, 0]], co[tris[:, 1]], co[tris[:, 2]]
    fn = np.cross(b - a, c - a)          # area-weighted
    n = np.zeros_like(co)
    for j in range(3):
        np.add.at(n, tris[:, j], fn)
    return n / np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]


def _median_edge_length(co: np.ndarray, edges: np.ndarray) -> float:
    l = np.linalg.norm(co[edges[:, 0]] - co[edges[:, 1]], axis=1)
    l = l[l > 1e-9]
    return float(np.median(l)) if len(l) else 0.0


def smooth_steps_for(radius_m: float, edge_len_m: float) -> int:
    """Uniform-Laplacian steps (λ=0.5) whose diffusion radius ≈ radius_m.

    Each step spreads a value over roughly one ring; after S steps the
    reach is about edge_len·sqrt(S/2). Clamped so a very coarse or very
    fine Guide neither under-smooths nor stalls.
    """
    if edge_len_m <= 0.0:
        return SMOOTH_STEPS_MIN
    s = int(round(2.0 * (radius_m / edge_len_m) ** 2))
    return max(SMOOTH_STEPS_MIN, min(SMOOTH_STEPS_MAX, s))


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


class _Detector:
    """Self-contact search over the base surface plus its Solidify shell."""

    def __init__(self, co2: np.ndarray, tris: np.ndarray, thickness: float, sign: float):
        self.n = len(co2)
        self.tris = tris
        self.thickness = thickness
        self.sign = sign
        self.with_shell = thickness > 0.0
        tri_c2 = co2[tris].mean(axis=1)
        if self.with_shell:
            self.polys = np.concatenate([tris, tris[:, ::-1] + self.n])
            self.face_c2 = np.concatenate([tri_c2, tri_c2])
            self.vert_c2 = np.concatenate([co2, co2])
        else:
            self.polys = tris
            self.face_c2 = tri_c2
            self.vert_c2 = co2
        self._poly_list = [tuple(int(x) for x in p) for p in self.polys]

    def surface(self, co: np.ndarray) -> np.ndarray:
        if not self.with_shell:
            return co
        shell = co + self.sign * self.thickness * _vertex_normals(co, self.tris)
        return np.concatenate([co, shell])

    def run(self, co: np.ndarray, radius: float, candidates=None):
        """Return (gap per base vertex, push per base vertex).

        gap[i] is the smallest distance from vertex i or its shell vertex to
        another layer (inf when none within radius). push[i] accumulates,
        over all such hits, half the missing distance away from the hit.
        candidates restricts the query to those base vertices (and their
        shell vertices); None queries every vertex.
        """
        allco = self.surface(co)
        bvh = BVHTree.FromPolygons([Vector(c) for c in allco], self._poly_list)
        n = self.n
        gap = np.full(n, np.inf)
        push = np.zeros((n, 3))
        if candidates is None:
            base_idx = range(n)
        else:
            base_idx = candidates
        face_c2 = self.face_c2
        vert_c2 = self.vert_c2
        abs2d = ABS_2D_M
        for i in base_idx:
            queries = (i, i + n) if self.with_shell else (i,)
            for q in queries:
                p = allco[q]
                hits = bvh.find_nearest_range(Vector(p), radius)
                if not hits:
                    continue
                c2 = vert_c2[q]
                for loc, _nor, fi, d in hits:
                    if d < 1e-9:
                        continue
                    d2 = face_c2[fi] - c2
                    if (d2[0] * d2[0] + d2[1] * d2[1] + d2[2] * d2[2]) ** 0.5 <= max(RATIO_2D * d, abs2d):
                        continue
                    if d < gap[i]:
                        gap[i] = d
                    push[i] += (p - np.asarray(loc)) / d * ((radius - d) * 0.5)
        return gap, push


def _neighbour_mean(D: np.ndarray, e0, e1, deg) -> np.ndarray:
    s = np.zeros_like(D)
    np.add.at(s, e0, D[e1])
    np.add.at(s, e1, D[e0])
    return s / np.maximum(deg, 1)[:, None]


# ---------------------------------------------------------------------------
# Colour attribute
# ---------------------------------------------------------------------------


def write_gap_attr(mesh, gap: np.ndarray, target: float) -> None:
    """AC9_Gap: red < ½ gap, yellow → green at the gap, grey = nothing near."""
    n = len(gap)
    col = np.empty((n, 4), dtype=np.float64)
    col[:, 3] = 1.0
    near = np.isfinite(gap)
    col[~near, :3] = 0.55
    r = np.clip(gap[near] / max(target, 1e-9), 0.0, 1.0)
    # 0..0.5 → red, 0.5..1 → yellow→green
    red = r < 0.5
    c = np.empty((near.sum(), 3))
    c[red] = (1.0, 0.0, 0.0)
    t = (r[~red] - 0.5) * 2.0
    c[~red, 0] = 1.0 - t
    c[~red, 1] = 1.0
    c[~red, 2] = 0.0
    col[near, :3] = c
    ca = mesh.color_attributes.get(GAP_ATTR)
    if ca is not None and (ca.domain != 'POINT' or ca.data_type != 'FLOAT_COLOR'):
        mesh.color_attributes.remove(ca)
        ca = None
    if ca is None:
        ca = mesh.color_attributes.new(GAP_ATTR, 'FLOAT_COLOR', 'POINT')
    ca.data.foreach_set("color", col.astype(np.float32).ravel())


def remove_gap_attr(mesh) -> bool:
    ca = mesh.color_attributes.get(GAP_ATTR)
    if ca is None:
        return False
    mesh.color_attributes.remove(ca)
    return True


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _prepare(guide_obj, flat_sk: str, source_sk: str):
    mesh = guide_obj.data
    R, t = _world_matrix_parts(guide_obj)
    co3 = _extract_world_co_np(mesh, source_sk, guide_obj.matrix_world)
    co2 = _extract_world_co_np(mesh, flat_sk, guide_obj.matrix_world)
    tris = _extract_tri_verts_np(mesh)
    edges = _edges_np(mesh)
    return mesh, R, t, co3, co2, tris, edges


def check_gap(guide_obj, flat_sk: str, gap_m: float, include_solidify: bool,
              source_sk: str = "Basis") -> Tuple[Optional[str], SeparateStats]:
    """Measure self-contact on *source_sk* and write the AC9_Gap colours."""
    err = validate_guide(guide_obj, flat_sk)
    if err is not None:
        return err, SeparateStats()
    t0 = time.time()
    mesh, _R, _t, co3, co2, tris, _edges = _prepare(guide_obj, flat_sk, source_sk)
    thickness, sign = solidify_params(guide_obj, include_solidify)
    det = _Detector(co2, tris, thickness, sign)
    gap, _push = det.run(co3, gap_m)
    write_gap_attr(mesh, gap, gap_m)
    near = np.isfinite(gap)
    st = SeparateStats(
        contact_before=int(near.sum()),
        unresolved=int(near.sum()),
        min_gap_mm=float(gap[near].min() * 1000.0) if near.any() else gap_m * 1000.0,
        thickness_mm=thickness * 1000.0,
        seconds=time.time() - t0,
        gap=gap,
    )
    return None, st


def run_separate(
    guide_obj,
    flat_sk: str,
    gap_m: float,
    smooth_radius_m: float,
    max_iterations: int,
    include_solidify: bool,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[Optional[str], SeparateStats]:
    """Compute the separated Guide from its Basis and store it as AC9_Separated.

    The ShapeKey is (re)created at value 0; the caller decides whether to
    show it. Also writes AC9_Gap for the result.
    """
    err = validate_guide(guide_obj, flat_sk)
    if err is not None:
        return err, SeparateStats()
    if gap_m <= 0.0:
        return "Gap must be positive.", SeparateStats()

    t0 = time.time()
    mesh, R, t, co0, co2, tris, edges = _prepare(guide_obj, flat_sk, "Basis")
    n = len(co0)
    if n == 0 or len(tris) == 0:
        return "Guide has no geometry.", SeparateStats()

    thickness, sign = solidify_params(guide_obj, include_solidify)
    det = _Detector(co2, tris, thickness, sign)

    e0, e1 = edges[:, 0], edges[:, 1]
    deg = np.bincount(e0, minlength=n) + np.bincount(e1, minlength=n)
    edge_len = _median_edge_length(co0, edges)
    steps = smooth_steps_for(smooth_radius_m, edge_len)

    # First pass: wide search to collect candidates. A vertex farther than
    # 3×gap from any other layer cannot be brought within the gap by the
    # displacements this pass produces (each side moves at most about gap).
    gap0, push = det.run(co0, 3.0 * gap_m)
    candidates = np.flatnonzero(np.isfinite(gap0))
    contact_before = int((gap0 < gap_m).sum())

    co = co0.copy()
    history = []
    iterations = 0
    converged = False
    gap = gap0
    if contact_before == 0:
        converged = True
    else:
        for it in range(max_iterations):
            gap, push = det.run(co, gap_m, candidates)
            bad = int(np.isfinite(gap).sum())
            iterations = it + 1
            if progress is not None:
                progress(it, max_iterations)
            if bad == 0:
                converged = True
                break
            history.append(bad)
            if len(history) > PLATEAU_WINDOW:
                ref = history[-1 - PLATEAU_WINDOW]
                if ref > 0 and (ref - bad) / ref < PLATEAU_MIN_DROP:
                    break
            D = push
            for _ in range(steps):
                D = D + 0.5 * (_neighbour_mean(D, e0, e1, deg) - D)
            co = co + D
        if not converged:
            gap, _push = det.run(co, gap_m, candidates)

    # Result → ShapeKey (local space), colours, stats.
    disp = co - co0
    mag = np.linalg.norm(disp, axis=1)
    Rinv = np.linalg.inv(R)
    local = (co - t) @ Rinv.T

    kb = mesh.shape_keys.key_blocks if mesh.shape_keys is not None else None
    if kb is not None and SEPARATED_SK_NAME in kb:
        guide_obj.shape_key_remove(kb[SEPARATED_SK_NAME])
    key = guide_obj.shape_key_add(name=SEPARATED_SK_NAME, from_mix=False)
    key.data.foreach_set("co", local.astype(np.float32).ravel())
    key.value = 0.0

    write_gap_attr(mesh, gap, gap_m)

    n0 = _vertex_normals(co0, tris)
    n1 = _vertex_normals(co, tris)
    ang = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", n0, n1), -1.0, 1.0)))
    near = np.isfinite(gap)
    st = SeparateStats(
        contact_before=contact_before,
        unresolved=int(near.sum()),
        min_gap_mm=float(gap[near].min() * 1000.0) if near.any() else gap_m * 1000.0,
        max_disp_mm=float(mag.max() * 1000.0),
        p99_disp_mm=float(np.percentile(mag, 99) * 1000.0),
        normal_p99_deg=float(np.percentile(ang, 99)),
        iterations=iterations,
        smooth_steps=steps,
        thickness_mm=thickness * 1000.0,
        converged=converged,
        seconds=time.time() - t0,
        gap=gap,
    )
    return None, st


def remove_separation(guide_obj) -> Tuple[bool, bool]:
    """Delete the AC9_Separated key and AC9_Gap colours. Returns (key, attr)."""
    mesh = guide_obj.data
    removed_key = False
    kb = mesh.shape_keys.key_blocks if mesh.shape_keys is not None else None
    if kb is not None and SEPARATED_SK_NAME in kb:
        guide_obj.shape_key_remove(kb[SEPARATED_SK_NAME])
        removed_key = True
    removed_attr = remove_gap_attr(mesh)
    return removed_key, removed_attr


def has_separation(guide_obj) -> bool:
    if guide_obj is None or guide_obj.type != 'MESH':
        return False
    sk = guide_obj.data.shape_keys
    return sk is not None and SEPARATED_SK_NAME in sk.key_blocks


def sync_separated_value(guide_obj, flat_sk: str, use_separated: bool) -> None:
    """Show AC9_Separated only while the Guide is in 3D and the source is it.

    ShapeKeys are additive, so the separation offsets must never be on top
    of the flat layout: value 1 only when Flat SK is off.
    """
    if not has_separation(guide_obj):
        return
    kb = guide_obj.data.shape_keys.key_blocks
    flat = kb.get(flat_sk)
    in_3d = flat is None or flat.value < 0.5
    kb[SEPARATED_SK_NAME].value = 1.0 if (use_separated and in_3d) else 0.0
