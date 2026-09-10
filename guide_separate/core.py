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
A deterministic pass that opens a minimum gap between layers while leaving the
drape recognisable, stored as the ShapeKey ``AC9_Separated`` on the Guide (the
Basis stays the original). One round is:

1. Detect self-contact — a vertex is in contact when another surface is within
   *gap* in 3D **and** far away in the flat layout. See ``detect.py``; that
   module also owns the two constraints the bare test needs, the sewn-seam
   exemption and the flat layout's scale.
2. Weight-paint the result: the contact set is 1, diffused outward into a
   falloff, and the movement each contact needs is carried along that falloff
   so the fabric around it comes too instead of tearing away from it. The push
   is a low-frequency correction and the drape's folds are high-frequency
   detail, which is why the displacement is smoothed and the positions are not.
3. Apply it, once.
4. Then round after round over ONLY the vertices still in contact, until the
   set stops shrinking. It collapses quickly — 8,228 to 936 over five rounds on
   a skirt — so each round costs less than the one before.

Steps 1–3 run on coarse grids derived from the Guide and the result is sampled
back onto it (``levels.py``): the field is smoothed over 35 mm by construction,
so computing it on a 1.6 mm grid computes a low-frequency quantity on a
high-frequency one. Measured: over an hour on the Guide itself against 80–110 s
this way, with the quality metrics equal or better.

Then the field is held to the seams. Sewn twins are coincident in the Basis, so
giving both the same displacement keeps the distance between them identically
zero and the groove between panels — the detail Solidify exists to bake —
cannot be pulled open. Measured 0.000000 mm on all three test garments; the
seam exemption alone left them 0.821 mm apart at the median.

Everything the user sets is a length in millimetres. CLO's particle distance
changes the Guide's edge length case by case, so a ratio or a step count
silently changes meaning with it: the ring form of the seam exemption needed
k=4 on a 1.62 mm Guide and k=26 on a 0.162 mm one. The grids' own resolutions
are derived from the gap and the smoothing radius, not exposed.

Solidify
--------
Bakes are usually done with a Solidify modifier on the Guide so the low-poly's
inner side has something to hit. The shell it adds (vertex ± thickness ×
normal) is folded into the detection as a scalar reduction of the measured
distance — no shell geometry is built. A shell thicker than the spacing between
layers penetrates the neighbour inside every fold and no amount of pushing
resolves it; the iteration plateaus and the operator reports the unresolved
count.

The gap check that runs before / after (``AC9_Gap`` colour attribute:
red = less than half the gap, yellow = between, green = reached, grey =
no other layer nearby) is the diagnostic for both cases, and is always measured
on the Guide itself, never on a proxy — it has to describe what will be baked.

Coordinates are handled in world space like the rest of the projector;
results are written back in the Guide's local space.
"""

import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

import numpy as np

from ..clo_projector.guide import (
    SEPARATED_SK_NAME,
    _extract_tri_verts_np,
    _extract_world_co_np,
    flat_scale,
    validate_guide,
)
from . import levels, seams
from .detect import Detector, neighbour_mean, vertex_normals

GAP_ATTR = "AC9_Gap"

SMOOTH_STEPS_MIN = 10
SMOOTH_STEPS_MAX = 600

# Plateau detection: stop when the unresolved count has not dropped by this
# fraction over PLATEAU_WINDOW consecutive iterations.
PLATEAU_WINDOW = 3
PLATEAU_MIN_DROP = 0.02

# --- resolutions, all derived so nothing new is exposed as a knob ---------
#
# The proxy has to resolve the layer spacing it is asked to open, so its edge
# is a fraction of the gap (0.7 × 4 mm = 2.9 mm, the value the rework was
# validated at). It is never finer than the Guide itself.
PROXY_EDGE_PER_GAP = 0.7
# The diffusion grid: four samples per smoothing radius is everything a field
# smoothed over that radius can carry. Six was tried and moved the dihedral
# metric not at all (0.0046 either way), so four is simply cheaper. Skipped
# when it would not actually be coarser than the proxy.
DIFFUSION_SAMPLES_PER_RADIUS = 4.0
DIFFUSION_MIN_COARSER = 1.2
# After the field lands on the Guide it still carries steps at the proxy's own
# edge length, so it is diffused over a small multiple of that.
POST_SMOOTH_PER_PROXY_EDGE = 2.0
# The seam exemption reaches past the gap by half again, so it still covers a
# stitch the proxy resolves only coarsely.
SEAM_REACH_PER_GAP = 1.5
# Cleanup rounds after the first push. Measured on the three test garments;
# beyond this the rounds are grinding vertices from 3.5 mm to 4.0 mm, which
# costs shape and buys nothing a bake can see (29 rounds against 5 took
# displacement on shirts from 7.2 mm to 15.4 mm and edges moved over 0.5 mm
# from 1 to 288). The user's Max Iterations caps this, it does not set it.
#
# Restricting the rounds to only the vertices inside half the gap was tried and
# is much worse, not better: the amplitude normalisation divides by the
# diffused contact indicator, and a sparse active set makes that indicator
# tiny, so the division explodes -- 45.9 mm of displacement and 16,743 stretched
# edges on shirts. The rounds have to see the whole contact set to stay scaled.
CLEANUP_ROUNDS = 5
# Gain on the weighted displacement field.
#
# Measured directly: one weighted push delivers 0.68 of the clearance the
# contacts need, because diffusing the push averages it against the zeros
# around it. A gap of 1.58 mm then opens to 3.23 mm, stays under a 4 mm target,
# and counts as unresolved -- the pass looks like it did nothing. A uniform
# scale corrects the amplitude and cannot affect smoothness, since scaling a
# smooth field leaves it exactly as smooth.
#
# 2.0 rather than the 1/0.68 = 1.47 that would land exactly on target: the
# cleanup rounds converge faster with some margin, and no single value is
# right everywhere (3.0 resolved 84.8% of the skirt but only 45.5% of the
# shirts in one shot). Getting it exactly right is not this constant's job --
# the rounds that follow are self-correcting, which is why they exist.
PUSH_GAIN = 2.0


@dataclass
class SeparateStats:
    # "unresolved" counts every vertex with anything at all inside the gap,
    # which reads as "still interfering" and is not: measured on a finished
    # skirt, 96.4% of those 14,071 vertices were between 3.0 and 4.0 mm of a
    # 4 mm target, none were touching, and only 38 were within 2 mm. The
    # colours said so all along; the number did not. `close` is the one worth
    # reading, and `deficit_mm` has no threshold to hide behind at all.
    unresolved: int = 0              # anything within the gap
    close: int = 0                   # within HALF the gap -- the real problem
    deficit_mm: float = 0.0          # total missing clearance, summed
    contact_before: int = 0          # measured only by Check, not by Separate
    min_gap_mm: float = 0.0
    max_disp_mm: float = 0.0
    p99_disp_mm: float = 0.0
    normal_p99_deg: float = 0.0
    iterations: int = 0
    smooth_steps: int = 0
    thickness_mm: float = 0.0
    converged: bool = False
    seconds: float = 0.0
    # diagnostics for the rework: what the coarse machinery actually did
    proxy_verts: int = 0
    proxy_edge_mm: float = 0.0
    diffusion_verts: int = 0
    twin_pairs: int = 0
    twin_gap_max_mm: float = 0.0
    flat_scale: float = 1.0
    # "" unless a derived smoothing radius was clamped and so is not what the
    # settings say. See smooth_reach_note.
    smooth_clamped: str = ""
    # Seconds per stage. Kept because the runtime is the open question on this
    # feature and "the smoothing dominates" was once extrapolated rather than
    # measured — it was the detection, at 84%.
    stage_seconds: dict = field(default_factory=dict, repr=False)
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
    return e.reshape(-1, 2).astype(np.int64)


def _median_edge_length(co: np.ndarray, edges: np.ndarray) -> float:
    length = np.linalg.norm(co[edges[:, 0]] - co[edges[:, 1]], axis=1)
    length = length[length > 1e-9]
    return float(np.median(length)) if len(length) else 0.0


def _steps_raw(radius_m: float, edge_len_m: float) -> int:
    return int(round(2.0 * (radius_m / edge_len_m) ** 2))


def smooth_steps_for(radius_m: float, edge_len_m: float) -> int:
    """Uniform-Laplacian steps (λ=0.5) whose diffusion radius ≈ radius_m.

    Each step spreads a value over roughly one ring; after S steps the
    reach is about edge_len·sqrt(S/2). Clamped so a very coarse or very
    fine Guide neither under-smooths nor stalls.

    Two of the four callers cannot reach the clamps by construction, which is
    the point: the diffusion grid's edge is defined as radius/4, so its step
    count is 2·4² = 32 whatever the user sets, and the proxy fallback only runs
    when radius ≤ 3.36·gap, which bounds it at 46. The two that can are the ones
    dividing a grid resolution by the *Guide's* edge length — `smooth_reach_note`
    exists for those.
    """
    if edge_len_m <= 0.0:
        return SMOOTH_STEPS_MIN
    return max(SMOOTH_STEPS_MIN, min(SMOOTH_STEPS_MAX, _steps_raw(radius_m, edge_len_m)))


def smooth_reach_note(label: str, radius_m: float, edge_len_m: float) -> str:
    """"" unless the clamp changed the reach, else what it actually became.

    A clamped step count makes a derived radius silently untrue — measured on a
    direct solve, 935 steps clamped to 600 turned a 35 mm smoothing radius into
    28.03 mm. Reporting it needs no threshold and no judgement about which
    settings are reasonable: either the ceiling bound or it did not.

    Only a shortfall is worth saying. SMOOTH_STEPS_MIN can only round the reach
    *up* (a Guide coarse enough to want 8 steps gets 10, so 3.2 mm becomes
    3.6 mm), which is the floor doing what it is for — stopping a very coarse or
    very fine Guide from under-smoothing — not a setting turning into a lie.
    """
    if edge_len_m <= 0.0:
        return ""
    raw = _steps_raw(radius_m, edge_len_m)
    steps = max(SMOOTH_STEPS_MIN, min(SMOOTH_STEPS_MAX, raw))
    if steps >= raw:
        return ""
    reach = edge_len_m * (steps / 2.0) ** 0.5
    return ("%s smoothing wanted %.1fmm (%d steps) but is clamped to %d, "
            "reaching %.1fmm" % (label, radius_m * 1000.0, raw, steps, reach * 1000.0))


def _diffuse(field, level, steps):
    for _ in range(steps):
        field = field + 0.5 * (
            neighbour_mean(field, level.e0, level.e1, level.deg) - field)
    return field


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
    tris = np.asarray(_extract_tri_verts_np(mesh), dtype=np.int64)
    edges = _edges_np(mesh)
    return mesh, R, t, co3, co2, tris, edges


def _fine_detector(co3, co2, tris, edges, thickness, sign, r, gap_m):
    """The detector at the Guide's own resolution — the diagnostic's measurer.

    No `tri_c2` override and no `m2d_cap`: the Guide is unwelded, so the mean of
    a triangle's three flat coordinates is the honest centroid and no triangle
    of it spans two panels. The seam exemption is the same one the solve uses,
    or Check would report contacts that Separate deliberately leaves alone.
    """
    seam_idx, _bnd = seams.sewn_vertices(co3, tris, edges)
    keep_fn, _dseam = seams.make_keep_fn(co3, tris, co3[seam_idx],
                                         SEAM_REACH_PER_GAP * gap_m)
    return Detector(co2, tris, thickness, sign, flat_scale=r, keep_pairs=keep_fn)


def check_gap(guide_obj, flat_sk: str, gap_m: float, include_solidify: bool,
              source_sk: str = "Basis",
              progress: Optional[Callable[[float], None]] = None
              ) -> Tuple[Optional[str], SeparateStats]:
    """Measure self-contact on *source_sk* and write the AC9_Gap colours.

    Measured 17-24 s on production Guides, essentially all of it the detection
    (reading the Guide is 0.1-0.2 s and the seam exemption 0.3-0.7 s), so
    `progress` matters: a bar that sits at zero for twenty seconds is the
    complaint this feature started from, only quieter.
    """
    err = validate_guide(guide_obj, flat_sk)
    if err is not None:
        return err, SeparateStats()
    t0 = time.time()
    mesh, _R, _t, co3, co2, tris, edges = _prepare(guide_obj, flat_sk, source_sk)
    if len(co3) == 0 or len(tris) == 0:
        return "Guide has no geometry.", SeparateStats()
    thickness, sign = solidify_params(guide_obj, include_solidify)
    r = flat_scale(guide_obj, flat_sk, None if source_sk == "Basis" else source_sk)
    det = _fine_detector(co3, co2, tris, edges, thickness, sign, r, gap_m)
    gap, _push = det.run(co3, gap_m, progress=progress)
    write_gap_attr(mesh, gap, gap_m)
    near = np.isfinite(gap)
    st = SeparateStats(
        contact_before=int(near.sum()),
        unresolved=int(near.sum()),
        close=int((gap < 0.5 * gap_m).sum()),
        deficit_mm=(float(np.maximum(gap_m - gap[near], 0.0).sum() * 1000.0)
                    if near.any() else 0.0),
        min_gap_mm=float(gap[near].min() * 1000.0) if near.any() else gap_m * 1000.0,
        thickness_mm=thickness * 1000.0,
        flat_scale=r,
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
    progress: Optional[Callable[[float], None]] = None,
) -> Tuple[Optional[str], SeparateStats]:
    """Compute the separated Guide from its Basis and store it as AC9_Separated.

    The ShapeKey is (re)created at value 0; the caller decides whether to
    show it. Also writes AC9_Gap for the result, measured on the Guide.

    `progress` is called with the overall fraction done. The stage weights are
    the measured split on a 200k-vertex Guide: building the grids and their
    transfers is a fifth of the run, the iteration a little over half, and
    landing the field back on the Guide the rest.
    """
    err = validate_guide(guide_obj, flat_sk)
    if err is not None:
        return err, SeparateStats()
    if gap_m <= 0.0:
        return "Gap must be positive.", SeparateStats()

    stage = {}
    marks = [time.time()]

    def tick(frac, label=None):
        if label is not None:
            now = time.time()
            stage[label] = round(now - marks[0], 2)
            marks[0] = now
        if progress is not None:
            progress(min(max(frac, 0.0), 1.0))

    t0 = time.time()
    mesh, R, t, co0, co2, tris, edges = _prepare(guide_obj, flat_sk, "Basis")
    n = len(co0)
    if n == 0 or len(tris) == 0:
        return "Guide has no geometry.", SeparateStats()

    thickness, sign = solidify_params(guide_obj, include_solidify)
    fine_edge = _median_edge_length(co0, edges)
    r = flat_scale(guide_obj, flat_sk, None)
    tick(0.02, "read")

    # --- 1. the grids ----------------------------------------------------
    proxy_edge = max(PROXY_EDGE_PER_GAP * gap_m, fine_edge)
    proxy = levels.build_level(co0, tris, fine_edge, proxy_edge, "AC9_SepProxy")
    if proxy.n == 0 or len(proxy.tris) == 0:
        return "Could not build the coarse proxy for this Guide.", SeparateStats()
    tick(0.06, "proxy")

    diffusion_edge = smooth_radius_m / DIFFUSION_SAMPLES_PER_RADIUS
    diffusion = None
    transfer = None
    if diffusion_edge > proxy.median_edge * DIFFUSION_MIN_COARSER:
        diffusion = levels.build_level(co0, tris, fine_edge, diffusion_edge,
                                       "AC9_SepDiffusion")
        if diffusion.n and len(diffusion.tris):
            transfer = levels.Barycentric(
                diffusion.co, diffusion.tris, proxy.co,
                vertex_normals(proxy.co, proxy.tris),
                max(3.0 * diffusion.median_edge, 0.01))
        else:
            diffusion = None
    tick(0.12, "diffusion+transfer")

    if diffusion is not None:
        diffusion_steps = smooth_steps_for(smooth_radius_m, diffusion.median_edge)
        # Enough diffusion on the proxy to erase features at the coarse grid's
        # OWN edge length; without it the grid's faceting is integrated into
        # the shape, measured as dihedral >5° going 0.33% -> 0.49%.
        polish_steps = smooth_steps_for(diffusion.median_edge, proxy.median_edge)
        proxy_steps = 0
        notes = [smooth_reach_note("Polish", diffusion.median_edge, proxy.median_edge)]
    else:
        diffusion_steps = polish_steps = 0
        proxy_steps = smooth_steps_for(smooth_radius_m, proxy.median_edge)
        notes = []

    # --- 2. what the proxy cannot carry itself ---------------------------
    # Flat coordinates: the proxy is welded, so a seam vertex holds only one
    # panel's flat coordinate and the mean of a triangle's three would jump the
    # UV distance between panels. Both are sampled off the fine Guide.
    proxy_co2 = levels.sample_flat(co0, tris, co2, proxy.co)
    proxy_tri_c2 = levels.sample_flat(co0, tris, co2, proxy.co[proxy.tris].mean(axis=1))
    tick(0.18, "flat sample")

    # Built once, on the undeformed proxy, and reused for every iteration: the
    # exemption band goes slightly stale as panels move (measured up to 7.6 mm
    # of displacement against a 6 mm band), but rebuilding it per iteration
    # would cost more than the seam integrity it buys — the twin constraint
    # below is what actually holds the seams shut, and it holds them exactly.
    seam_idx, _bnd = seams.sewn_vertices(co0, tris, edges)
    seam_pos = co0[seam_idx]
    keep_fn, _dseam = seams.make_keep_fn(proxy.co, proxy.tris, seam_pos,
                                         SEAM_REACH_PER_GAP * gap_m)
    tick(0.20, "seam exemption")

    det = Detector(proxy_co2, proxy.tris, thickness, sign, flat_scale=r,
                   tri_c2=proxy_tri_c2, m2d_cap=3.0 * proxy.median_edge,
                   keep_pairs=keep_fn)

    # --- 3. solve on the proxy -------------------------------------------
    # One weighted push, then rounds over only what is still in contact.
    #
    # The previous form pushed a little, diffused, re-detected, thirty times,
    # and re-tested every candidate every round. The rounds were not there for
    # a geometric reason: the diffusion spreads each push over the smoothing
    # radius, so a vertex keeps a fraction of what it was given and the loop
    # made up the difference -- at the price of running detection, 63-70% of
    # the runtime, thirty times.
    #
    # Measured over the three test garments, this form resolves as much or more
    # (the skirt 92.4% against 83.1%), distorts less (edges moved over 0.5 mm on
    # the jacket: 2 against 26), and runs about three times faster.
    def _smooth(field):
        if transfer is not None:
            out = transfer.prolong(_diffuse(transfer.restrict(field),
                                            diffusion, diffusion_steps))
            return _diffuse(out, proxy, polish_steps)
        return _diffuse(field, proxy, proxy_steps)

    def build_field(co_p, active=None):
        """(displacement, contacts) -- detect once, weight-paint, move.

        The weight map is literally that: the contact set is 1, diffused
        outward into a falloff, and the movement each contact needs is carried
        along that falloff so the fabric around it comes too instead of tearing
        away from it. Dividing by the diffused indicator is what keeps the
        amplitude at the core -- without it the push is averaged against the
        zeros around it and only 68% of the needed clearance is delivered,
        which leaves a 1.6 mm gap at 3.2 mm and resolves almost nothing.
        """
        gap_p, push = det.run(co_p, gap_m, active)
        need = np.linalg.norm(push, axis=1)
        live = np.isfinite(gap_p) & (need > 1e-12)
        n_live = int(live.sum())
        if n_live == 0:
            return None, 0
        V = np.zeros_like(co_p)
        V[live] = push[live]
        ind = np.zeros(proxy.n)
        ind[live] = 1.0
        w = _smooth(np.repeat(ind[:, None], 3, axis=1))[:, 0]
        A = _smooth(V) / np.maximum(w, 1e-9)[:, None]
        core_w = float(np.median(w[live]))
        W = np.clip(w / max(core_w, 1e-9), 0.0, 1.0)
        return PUSH_GAIN * W[:, None] * A, n_live

    co_p = proxy.co.copy()
    D, n_first = build_field(co_p)
    iterations = 1
    converged = n_first == 0
    if D is not None:
        co_p = co_p + D
    tick(0.40, "first push")

    # Then only what is still flagged. The active set collapses as contacts
    # clear -- 8,228 -> 936 over five rounds on the skirt -- so each round
    # costs less than the one before, which is the whole point of carrying a
    # per-vertex status instead of re-testing every candidate to the end.
    history = []
    if not converged:
        gap_p, _ = det.run(co_p, gap_m)
        active = np.flatnonzero(np.isfinite(gap_p))
        for it in range(min(CLEANUP_ROUNDS, max(max_iterations - 1, 0))):
            if len(active) == 0:
                converged = True
                break
            # not `n` -- that is the Guide's vertex count, and shadowing it
            # here silently fed the wrong size to the twin grouping below
            D, n_live = build_field(co_p, active)
            if n_live == 0:
                converged = True
                break
            iterations += 1
            co_p = co_p + D
            history.append(len(active))
            if len(history) > PLATEAU_WINDOW:
                ref = history[-1 - PLATEAU_WINDOW]
                if ref > 0 and (ref - n_live) / ref < PLATEAU_MIN_DROP:
                    break
            gap_p, _ = det.run(co_p, gap_m, active)
            active = active[np.isfinite(gap_p[active])]
            tick(0.40 + 0.35 * (it + 1) / max(max_iterations, 1))
    tick(0.75, "iterate")

    # --- 4. land the field on the Guide ----------------------------------
    field = levels.Barycentric(proxy.co, proxy.tris, co0,
                               vertex_normals(co0, tris),
                               levels.TRANSFER_RADIUS_M).prolong(co_p - proxy.co)
    tick(0.86, "transfer to guide")

    twin_a, twin_b = seams.twin_pairs(co0, tris, edges)
    twins = seams.TwinGroups(n, twin_a, twin_b)
    field = twins.project(field)

    post_want = POST_SMOOTH_PER_PROXY_EDGE * proxy.median_edge
    post_steps = smooth_steps_for(post_want, fine_edge)
    notes.append(smooth_reach_note("Post", post_want, fine_edge))
    if post_steps > 0:
        # The sewing pairs join the smoothing graph as zero-length edges, so the
        # field diffuses across a seam instead of stopping at it, and the twins
        # are re-projected every step so smoothing cannot pull them apart again.
        if twins.count:
            se0 = np.concatenate([edges[:, 0], twin_a])
            se1 = np.concatenate([edges[:, 1], twin_b])
        else:
            se0, se1 = edges[:, 0], edges[:, 1]
        deg = np.bincount(se0, minlength=n) + np.bincount(se1, minlength=n)
        for _ in range(post_steps):
            field = field + 0.5 * (neighbour_mean(field, se0, se1, deg) - field)
            field = twins.project(field)
    co1 = co0 + field
    tick(0.92, "twins+post smooth")

    # --- 5. result, and the diagnostic measured on the Guide itself ------
    # One pass, on the result. The "before" number used to be measured here too
    # and cost as much again; Check already reports it on demand, and the
    # breakdown below says more about the result than a difference of two
    # conflated counts ever did.
    fine_det = _fine_detector(co0, co2, tris, edges, thickness, sign, r, gap_m)
    gap = fine_det.run(co1, gap_m, progress=lambda f: tick(0.92 + 0.06 * f))[0]
    tick(0.98, "fine gap check")

    disp = np.linalg.norm(field, axis=1)
    Rinv = np.linalg.inv(R)
    local = (co1 - t) @ Rinv.T

    kb = mesh.shape_keys.key_blocks if mesh.shape_keys is not None else None
    if kb is not None and SEPARATED_SK_NAME in kb:
        guide_obj.shape_key_remove(kb[SEPARATED_SK_NAME])
    key = guide_obj.shape_key_add(name=SEPARATED_SK_NAME, from_mix=False)
    key.data.foreach_set("co", local.astype(np.float32).ravel())
    key.value = 0.0

    write_gap_attr(mesh, gap, gap_m)

    n0 = vertex_normals(co0, tris)
    n1 = vertex_normals(co1, tris)
    ang = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", n0, n1), -1.0, 1.0)))
    near = np.isfinite(gap)
    close = int((gap < 0.5 * gap_m).sum())
    deficit_mm = float(np.maximum(gap_m - gap[near], 0.0).sum() * 1000.0) if near.any() else 0.0
    twin_gap = (float(np.linalg.norm(co1[twin_a] - co1[twin_b], axis=1).max() * 1000.0)
                if twins.count else 0.0)
    st = SeparateStats(
        unresolved=int(near.sum()),
        close=close,
        deficit_mm=deficit_mm,
        min_gap_mm=float(gap[near].min() * 1000.0) if near.any() else gap_m * 1000.0,
        max_disp_mm=float(disp.max() * 1000.0),
        p99_disp_mm=float(np.percentile(disp, 99) * 1000.0),
        normal_p99_deg=float(np.percentile(ang, 99)),
        iterations=iterations,
        smooth_steps=diffusion_steps or proxy_steps,
        thickness_mm=thickness * 1000.0,
        converged=converged,
        seconds=time.time() - t0,
        proxy_verts=proxy.n,
        proxy_edge_mm=proxy.median_edge * 1000.0,
        diffusion_verts=diffusion.n if diffusion is not None else 0,
        twin_pairs=twins.count,
        twin_gap_max_mm=twin_gap,
        flat_scale=r,
        smooth_clamped="; ".join(n for n in notes if n),
        stage_seconds=stage,
        gap=gap,
    )
    tick(1.0, "write")
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

    Writes only when the value actually changes — the Guide is a large mesh
    and any write to a ShapeKey value costs it a depsgraph re-evaluation and
    a viewport re-upload, whether or not the number moved.

    A muted Flat SK contributes nothing whatever its value says, so it must be
    read as "off" — found on a production Guide sitting at value 1.0 and muted,
    where reading the value alone would have switched the separation off and
    shown the user the original drape with no hint why.
    """
    if not has_separation(guide_obj):
        return
    kb = guide_obj.data.shape_keys.key_blocks
    flat = kb.get(flat_sk)
    in_3d = flat is None or flat.mute or flat.value < 0.5
    want = 1.0 if (use_separated and in_3d) else 0.0
    sep = kb[SEPARATED_SK_NAME]
    if sep.value != want:
        sep.value = want
