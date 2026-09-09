"""Reusable projection pipeline shared between the explicit operator and the
depsgraph live-update handler.

The operator wraps this with REGISTER/UNDO + error reporting; the handler
wraps it with a re-entry guard + a deferred timer. Keeping the pipeline here
means both paths produce identical results.

Guide unification
-----------------
A single guide_obj carries both data states as ShapeKeys:
  Basis ShapeKey (or mesh.vertices when no ShapeKeys)  →  Guide 3D
  guide_flat_sk  (a named ShapeKey at value 1.0)       →  Guide 2D

No depsgraph evaluation is needed — ShapeKey data is read directly.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

from .attachment import (
    ATTR_STATUS,
    Attachment,
    apply_attachments_to_2d,
    apply_attachments_to_3d,
    bind_new_verts_3d,
    find_unbound_or_stale,
    build_bvh,
    build_bvh_2d,
    build_edge_neighbors,
    build_per_island_bvhs,
    classify_boundary_verts,
    compute_attachments,
    compute_attachments_for_indices,
    load_attachments_from_mesh,
    load_boundary_from_mesh,
    rebind_attachments_island_aware,
    save_attachments_to_mesh,
    save_boundary_to_mesh,
    snap_vertices_to_islands_bvh,
)
from .guide import (
    build_seam_edge_mask,
    extract_points_world,
    extract_triangles_basis,
    extract_triangles_flat,
    guide_3d_shapekey,
    set_basis_local,
    validate_guide,
)

# ---------------------------------------------------------------------------
# Guide data cache
# ---------------------------------------------------------------------------
# Keyed by (session_uid, flat_sk_name, 3d_sk_name, n_verts, n_polys).  The 3D
# source name (Basis / AC9_Separated) is in the key so flipping '3D Source'
# re-extracts; rebuilding AC9_Separated itself must call
# invalidate_guide_cache() (guide_separate does).  session_uid is
# Blender's session-unique ID for the data-block — unlike id() / a memory
# address it is NEVER reused, so an undo/redo that reallocates data-blocks
# can't make one mesh inherit another's cached triangles (which projected the
# retopo against garbage and shredded the layout).  Vertex/polygon counts are
# part of the key so topology edits on the guide auto-invalidate.  Use
# invalidate_guide_cache() or the "Clear Guide Cache" operator after pure
# vertex-position edits (counts unchanged).

_guide_cache: dict = {}


def _guide_cache_key(guide_obj, flat_sk: str):
    mesh = guide_obj.data
    return (mesh.session_uid, flat_sk, guide_3d_shapekey(mesh),
            len(mesh.vertices), len(mesh.polygons))


def invalidate_guide_cache(guide_obj=None) -> None:
    """Clear cached triangle lists and BVH for *guide_obj*, or everything."""
    if guide_obj is None:
        _guide_cache.clear()
        return
    uid = guide_obj.data.session_uid
    stale = [k for k in _guide_cache if k[0] == uid]
    for k in stale:
        del _guide_cache[k]


def _get_guide_cache(guide_obj, flat_sk: str) -> dict:
    """Return cached {tris_2d, tris_3d, bvh_2d} for *guide_obj* + *flat_sk*.

    On first call for this (mesh, sk) pair the triangle lists are extracted
    (numpy-accelerated) and a BVHTree is built once in C.  Subsequent calls
    return the cached objects immediately.
    """
    key = _guide_cache_key(guide_obj, flat_sk)
    if key not in _guide_cache:
        # Drop superseded entries for the same mesh (old topology / old SK).
        uid = key[0]
        for k in [k for k in _guide_cache if k[0] == uid]:
            del _guide_cache[k]
        tris_2d = extract_triangles_flat(guide_obj, flat_sk)
        tris_3d = extract_triangles_basis(guide_obj)
        bvh_2d  = build_bvh_2d(tris_2d)
        seam_mask = build_seam_edge_mask(guide_obj)
        _guide_cache[key] = {
            "tris_2d": tris_2d,
            "tris_3d": tris_3d,
            "bvh_2d": bvh_2d,
            "seam_mask": seam_mask,
        }
    return _guide_cache[key]


SHAPEKEY_NAME = "AC9_3D_Project"
FAILED_GROUP_NAME = "AC9_Project_Failed"


@dataclass
class ProjectionResult:
    success: bool
    error: Optional[str] = None
    total: int = 0
    projected: int = 0
    failed: int = 0
    new_verts_computed: int = 0       # only meaningful when incremental=True
    island_jumped: int = 0            # vertices that changed UV island (3D→2D)
    island_jumped_indices: Tuple = () # their vertex indices


# ---------------------------------------------------------------------------
# Forward: 2D layout → 3D shape (writes AC9_3D_Project ShapeKey)
# ---------------------------------------------------------------------------


def compute_forward_world(retopo_points_world, guide_obj, flat_sk: str, progress=None):
    """Pure forward projection: 2D world points → 3D world points on the Guide.

    Returns (new_world_positions, attachments, failed_count).  Writes nothing —
    used by the mirror's Edit-Mode refresh, which must not mutate the retopo
    mesh while it is in Edit Mode.  Caller must have validated the guide.

    `progress`, if given, is called with a 0..1 fraction across the
    per-vertex attachment lookup. Defaults to None (no-op).
    """
    cached = _get_guide_cache(guide_obj, flat_sk)
    attachments = compute_attachments(
        retopo_points_world, cached["tris_2d"], bvh_2d=cached["bvh_2d"],
        progress=progress,
    )
    new_world = apply_attachments_to_3d(attachments, retopo_points_world, cached["tris_3d"])
    failed = sum(1 for a in attachments if not a.is_ok)
    return new_world, attachments, failed


def run_mirror_move_to_2d(context, retopo, guide_obj, flat_sk, points_3d_world):
    """Lean, symmetric inverse for the experimental "3D move → 2D" flow.

    Each vertex's given 3D position (from the user-edited mirror) is snapped to
    the nearest point on the Guide 3D surface WITHIN its current UV island, and
    that barycentric position is written back to BOTH the 2D Basis (new layout)
    and the AC9_3D_Project ShapeKey (clean on-surface 3D).

    This is the exact mirror of the forward path (which finds the containing
    Guide 2D triangle and maps to Guide 3D).  Unlike run_reverse_projection it
    honours the move directly — NO sticky bias, NO boundary pin, NO neighbour
    voting — so a deliberate tangential nudge is not reverted.  Staying within
    the existing island makes seam crossings impossible by construction, so
    there are no island-jump warnings.

    Verts whose stored attachment isn't usable keep their current 2D position.
    """
    err = validate_guide(guide_obj, flat_sk)
    if err is not None:
        return ProjectionResult(success=False, error=err)

    retopo_mesh = retopo.data
    n = len(retopo_mesh.vertices)
    if n == 0:
        return ProjectionResult(success=False, error="2D Retopo Object has no vertices.")
    if len(points_3d_world) != n:
        return ProjectionResult(
            success=False, error="3D position count does not match retopo vertices."
        )

    cached = _get_guide_cache(guide_obj, flat_sk)
    tris_2d = cached["tris_2d"]
    tris_3d = cached["tris_3d"]
    _bvh_3d, tri_islands, per_island_bvh, per_island_remap = _ensure_reverse_caches(
        cached, guide_obj
    )

    existing = load_attachments_from_mesh(retopo_mesh)
    if len(existing) != n or not any(a.is_ok for a in existing):
        return ProjectionResult(
            success=False,
            error="No stored attachments — press 'Refresh Mirror' first.",
        )

    # Snap each vert to the nearest Guide-3D point within its own UV island.
    new_atts = snap_vertices_to_islands_bvh(
        points_3d_world, existing, tri_islands, tris_3d,
        per_island_bvh, per_island_remap,
    )

    retopo_inv = retopo.matrix_world.inverted()
    cur_2d_world = extract_points_world(retopo)  # fallback for unbound verts
    new_2d_world = apply_attachments_to_2d(new_atts, cur_2d_world, tris_2d)
    new_3d_world = apply_attachments_to_3d(new_atts, points_3d_world, tris_3d)

    set_basis_local(retopo, [retopo_inv @ p for p in new_2d_world])

    key = _ensure_shapekey(retopo, retopo_mesh, overwrite_shapekey=True)
    if key is not None:
        for i in range(n):
            key.data[i].co = retopo_inv @ new_3d_world[i]

    save_attachments_to_mesh(retopo_mesh, new_atts)
    is_boundary = classify_boundary_verts(new_atts, cached["seam_mask"])
    save_boundary_to_mesh(retopo_mesh, is_boundary)
    from . import gpu_overlay
    gpu_overlay.invalidate_boundary()

    failed = sum(1 for a in new_atts if not a.is_ok)
    return ProjectionResult(success=True, total=n, projected=n - failed, failed=failed)


def run_forward_projection(
    context,
    retopo,
    guide_obj,
    flat_sk: str,
    *,
    overwrite_shapekey: bool = True,
    clear_failed_group: bool = True,
    select_failed: bool = False,
    incremental: bool = False,
    progress=None,
) -> ProjectionResult:
    """Compute attachments for every retopo vertex against Guide 2D (flat SK),
    then write Guide-3D-projected positions into the AC9_3D_Project ShapeKey.

    incremental=True reuses attachments persisted on the retopo mesh; only
    vertices whose stored status is "none" get their attachment computed now.
    incremental=False recomputes every vertex from scratch.

    `progress`, if given, is called with a 0..1 fraction across the whole
    call (attachment lookup, then the shapekey write). Defaults to None.
    """
    tick = progress if callable(progress) else None
    if retopo is None or retopo.type != "MESH":
        return ProjectionResult(success=False, error="2D Retopo Object must be a mesh.")

    err = validate_guide(guide_obj, flat_sk)
    if err is not None:
        return ProjectionResult(success=False, error=err)

    retopo_mesh = retopo.data
    n = len(retopo_mesh.vertices)
    if n == 0:
        return ProjectionResult(success=False, error="2D Retopo Object has no vertices.")

    retopo_inv = retopo.matrix_world.inverted()
    retopo_points_world = extract_points_world(retopo)
    cached = _get_guide_cache(guide_obj, flat_sk)
    tris_2d = cached["tris_2d"]
    tris_3d = cached["tris_3d"]
    bvh_2d  = cached["bvh_2d"]

    new_verts_computed = 0

    # Attachment lookup is the slow part on a real mesh (per-vertex BVH
    # query) — give it the first 80% of the range. The shapekey write loop
    # below gets the rest.
    attach_tick = (lambda f: tick(0.8 * f)) if tick is not None else None

    if incremental:
        attachments = load_attachments_from_mesh(retopo_mesh)
        if len(attachments) != n:
            attachments = compute_attachments(retopo_points_world, tris_2d,
                                              bvh_2d=bvh_2d, progress=attach_tick)
        else:
            to_compute = [i for i, a in enumerate(attachments) if a.needs_compute]
            new_verts_computed = len(to_compute)
            if to_compute:
                results = compute_attachments_for_indices(
                    retopo_points_world, tris_2d, to_compute, bvh_2d=bvh_2d
                )
                for idx, att in results:
                    attachments[idx] = att
            if tick is not None:
                tick(0.8)
    else:
        attachments = compute_attachments(retopo_points_world, tris_2d,
                                          bvh_2d=bvh_2d, progress=attach_tick)

    new_world = apply_attachments_to_3d(attachments, retopo_points_world, tris_3d)
    new_local = [retopo_inv @ p for p in new_world]

    key = _ensure_shapekey(retopo, retopo_mesh, overwrite_shapekey)
    if key is None:
        return ProjectionResult(
            success=False,
            error=(
                f"ShapeKey '{SHAPEKEY_NAME}' already exists. Enable "
                f"'Overwrite Existing ShapeKey' to replace it."
            ),
        )

    for i, co in enumerate(new_local):
        key.data[i].co = co
        if tick is not None and n:
            tick(0.8 + 0.2 * i / n)

    if tick is not None:
        tick(1.0)

    save_attachments_to_mesh(retopo_mesh, attachments)
    # Classify each vertex by whether its 2D bary lands on a Guide seam edge.
    # Boundary verts will be pinned in subsequent Sync 3D > 2D operations so
    # the CLO pattern outline is never moved by 3D edits.
    is_boundary = classify_boundary_verts(attachments, cached["seam_mask"])
    save_boundary_to_mesh(retopo_mesh, is_boundary)
    from . import gpu_overlay
    gpu_overlay.invalidate_boundary()
    _update_failed_group(retopo, retopo_mesh, attachments, clear_failed_group, select_failed)

    failed_count = sum(1 for a in attachments if not a.is_ok)
    return ProjectionResult(
        success=True,
        total=n,
        projected=n - failed_count,
        failed=failed_count,
        new_verts_computed=new_verts_computed,
    )


# ---------------------------------------------------------------------------
# Live update: like forward, but preserves vertices the user just added in 3D
# ---------------------------------------------------------------------------


def run_live_update(
    context,
    retopo,
    guide_obj,
    flat_sk: str,
) -> ProjectionResult:
    """Handler-facing variant of forward projection.

    * Existing vertices (status==ok or ==failed): re-bind from current Basis.
    * Newly-added vertices (status==none): skip — leave user's 3D placement.

    First-time path (no existing attachments) falls back to full forward.
    """
    if retopo is None or retopo.type != "MESH":
        return ProjectionResult(success=False, error="2D Retopo Object must be a mesh.")

    err = validate_guide(guide_obj, flat_sk)
    if err is not None:
        return ProjectionResult(success=False, error=err)

    retopo_mesh = retopo.data
    n = len(retopo_mesh.vertices)
    if n == 0:
        return ProjectionResult(success=False, error="2D Retopo Object has no vertices.")

    retopo_inv = retopo.matrix_world.inverted()
    retopo_points_world = extract_points_world(retopo)
    cached = _get_guide_cache(guide_obj, flat_sk)
    tris_2d = cached["tris_2d"]
    tris_3d = cached["tris_3d"]
    bvh_2d  = cached["bvh_2d"]

    existing = load_attachments_from_mesh(retopo_mesh)
    has_prior_bind = (
        len(existing) == n and any(not a.needs_compute for a in existing)
    )

    if not has_prior_bind:
        attachments = compute_attachments(retopo_points_world, tris_2d, bvh_2d=bvh_2d)
        skip_indices = set()
    else:
        attachments = list(existing)
        rebind = [i for i, a in enumerate(existing) if not a.needs_compute]
        for idx, att in compute_attachments_for_indices(
            retopo_points_world, tris_2d, rebind, bvh_2d=bvh_2d
        ):
            attachments[idx] = att
        skip_indices = {i for i, a in enumerate(existing) if a.needs_compute}

    new_world = apply_attachments_to_3d(attachments, retopo_points_world, tris_3d)
    new_local = [retopo_inv @ p for p in new_world]

    key = _ensure_shapekey(retopo, retopo_mesh, overwrite_shapekey=True)
    for i, co in enumerate(new_local):
        if i in skip_indices:
            continue
        key.data[i].co = co

    save_attachments_to_mesh(retopo_mesh, attachments)
    _update_failed_group(
        retopo, retopo_mesh, attachments,
        clear_failed_group=True, select_failed=False,
    )

    failed_count = sum(1 for a in attachments if a.status == "failed")
    return ProjectionResult(
        success=True,
        total=n,
        projected=n - failed_count - len(skip_indices),
        failed=failed_count,
        new_verts_computed=0,
    )


# ---------------------------------------------------------------------------
# Reverse: 3D shape → 2D layout (updates Basis + ShapeKey)
# ---------------------------------------------------------------------------


def run_reverse_projection(
    context,
    retopo,
    guide_obj,
    flat_sk: str,
) -> ProjectionResult:
    """Read the AC9_3D_Project ShapeKey directly, snap each vertex's stored 3D
    position to the nearest point on Guide 3D (Basis), and rewrite both Basis
    (2D layout) and ShapeKey (snapped 3D) so they stay consistent.
    """
    if retopo is None or retopo.type != "MESH":
        return ProjectionResult(success=False, error="2D Retopo Object must be a mesh.")

    err = validate_guide(guide_obj, flat_sk)
    if err is not None:
        return ProjectionResult(success=False, error=err)

    retopo_mesh = retopo.data
    n = len(retopo_mesh.vertices)
    if n == 0:
        return ProjectionResult(success=False, error="2D Retopo Object has no vertices.")

    if retopo_mesh.shape_keys is None or SHAPEKEY_NAME not in retopo_mesh.shape_keys.key_blocks:
        return ProjectionResult(
            success=False,
            error=(
                f"ShapeKey '{SHAPEKEY_NAME}' does not exist. Run "
                f"'Sync 2D > 3D' first to establish the 3D state."
            ),
        )

    key = retopo_mesh.shape_keys.key_blocks[SHAPEKEY_NAME]
    matrix = retopo.matrix_world
    retopo_3d_world = [matrix @ key.data[i].co for i in range(n)]

    cached = _get_guide_cache(guide_obj, flat_sk)
    tris_2d = cached["tris_2d"]
    tris_3d = cached["tris_3d"]

    bvh_3d, tri_islands, per_island_bvh, per_island_remap = (
        _ensure_reverse_caches(cached, guide_obj)
    )

    old_attachments = load_attachments_from_mesh(retopo_mesh)
    edge_neighbors = build_edge_neighbors(retopo_mesh)
    # UV-boundary pin: verts identified as sitting on a Guide seam edge at
    # bind time are NEVER re-bound — their 2D position is the pattern truth.
    boundary_mask = load_boundary_from_mesh(retopo_mesh)

    # ── New-vert pre-bind ────────────────────────────────────────────────────
    # Verts created in 3D Edit Mode arrive with status==none and (thanks to
    # Blender's shape-key custom-data interpolation) possibly garbage Basis /
    # ShapeKey coords.  Bind them first via the neighbour-hinted, 2D-coherent
    # path so the island-aware rebind below starts from sane attachments — a
    # plain nearest-in-island snap can land them on the wrong fold side of the
    # fabric ("back-face fusion").  Also corrects retopo_3d_world entries to
    # the trustworthy source position.
    new_idx = _bind_new_verts_inplace(
        retopo, guide_obj, cached, old_attachments, edge_neighbors, retopo_3d_world
    )
    # Freshly-bound verts must not act as boundary-pinned: their flag may be
    # interpolation-inherited (Blender copies our attributes onto new verts)
    # and pinning would both freeze a possibly wrong seam-side choice and hide
    # them from the 2D-jump repair pass.
    for i in new_idx:
        if i < len(boundary_mask):
            boundary_mask[i] = False

    # ── Diagnostics ──────────────────────────────────────────────────────────
    _dbg_n_boundary = sum(1 for b in boundary_mask if b)
    _dbg_n_ok       = sum(1 for a in old_attachments if a.is_ok)
    print(
        f"[AC9 CLO Projector] Sync 3D>2D — "
        f"boundary_flag: {_dbg_n_boundary}/{n}  "
        f"ok_attachments: {_dbg_n_ok}/{n}"
    )

    # Topology-first reverse mapping: each vertex's UV island is decided by
    # existing attachment + retopo neighbours, then snapped within that island
    # only.  Sticky bias preserves stable verts; boundary mask pins seam verts.
    new_attachments, jumped_list = rebind_attachments_island_aware(
        retopo_3d_world,
        old_attachments,
        edge_neighbors,
        tri_islands,
        tris_3d,
        bvh_3d,
        per_island_bvh,
        per_island_remap,
        boundary_mask=boundary_mask,
        triangles_2d_world=tris_2d,
    )

    # ── Pin diagnostic ───────────────────────────────────────────────────────
    _dbg_pin = sum(
        1 for i in range(n)
        if i < len(boundary_mask) and boundary_mask[i]
        and i < len(old_attachments) and old_attachments[i].is_ok
        and i < len(new_attachments)
        and new_attachments[i].triangle_index == old_attachments[i].triangle_index
    )
    print(
        f"[AC9 CLO Projector] Sync 3D>2D — "
        f"pinned (boundary kept): {_dbg_pin}/{_dbg_n_boundary}"
    )

    # ── Early exit: no changes detected ──────────────────────────────────────
    # (Suppressed when new verts were just bound — old_attachments was mutated
    # by the pre-bind, so a match only means the rebind agreed with it.)
    if not new_idx and _attachments_match(old_attachments, new_attachments):
        return ProjectionResult(
            success=False,
            error=(
                "No 3D edits detected on the AC9_3D_Project ShapeKey. "
                "To edit in 3D: select AC9_3D_Project as the active ShapeKey "
                "in the Shape Keys panel, enable 'Edit Mode Visible Shape' "
                "(the pin icon), then move vertices in Edit Mode."
            ),
        )

    # Vertices where spatial best-fit disagreed with topological assignment —
    # they were snapped to the island boundary instead of crossing the seam.
    island_jumped_indices: tuple = tuple(jumped_list)

    # ── Compute final positions from attachments ─────────────────────────────
    retopo_inv = matrix.inverted()
    # Failed/none attachments must NOT fall back to the 3D position for the 2D
    # layout — that dumps them at their 3D height inside the flat layout (the
    # long diagonal streaks the user saw).  Use the vertex's current Basis (2D)
    # position so a vert that failed to re-bind simply stays put in 2D.
    retopo_2d_world = extract_points_world(retopo)
    new_2d_world = apply_attachments_to_2d(new_attachments, retopo_2d_world, tris_2d)
    new_2d_local = [retopo_inv @ p for p in new_2d_world]
    new_3d_world = apply_attachments_to_3d(new_attachments, retopo_3d_world, tris_3d)
    new_3d_local = [retopo_inv @ p for p in new_3d_world]

    set_basis_local(retopo, new_2d_local)

    for i, co in enumerate(new_3d_local):
        key.data[i].co = co

    save_attachments_to_mesh(retopo_mesh, new_attachments)
    # Re-classify boundary: interior verts may have snapped onto a seam edge,
    # or boundary verts may have shifted (they shouldn't, but be safe).
    new_boundary = classify_boundary_verts(new_attachments, cached["seam_mask"])
    save_boundary_to_mesh(retopo_mesh, new_boundary)
    from . import gpu_overlay
    gpu_overlay.invalidate_boundary()

    failed_count = sum(1 for a in new_attachments if not a.is_ok)
    return ProjectionResult(
        success=True,
        total=n,
        projected=n - failed_count,
        failed=failed_count,
        island_jumped=len(island_jumped_indices),
        island_jumped_indices=island_jumped_indices,
    )


# ---------------------------------------------------------------------------
# New-vert binding (verts created in 3D Edit Mode)
# ---------------------------------------------------------------------------


def _ensure_reverse_caches(cached, guide_obj):
    """Lazily build + memoise the 3D BVH, per-tri island map, and per-island
    BVHs on the guide cache dict.  Shared by reverse projection and new-vert
    binding so neither pays the build cost twice.
    """
    tris_3d = cached["tris_3d"]
    bvh_3d = cached.get("bvh_3d")
    if bvh_3d is None:
        bvh_3d = build_bvh(tris_3d)
        cached["bvh_3d"] = bvh_3d
    tri_islands = cached.get("tri_islands")
    if tri_islands is None:
        from .islands import detect_triangle_islands
        tri_islands = detect_triangle_islands(guide_obj)
        cached["tri_islands"] = tri_islands
    per_island_bvh = cached.get("per_island_bvh")
    per_island_remap = cached.get("per_island_remap")
    if per_island_bvh is None or per_island_remap is None:
        per_island_bvh, per_island_remap = build_per_island_bvhs(tri_islands, tris_3d)
        cached["per_island_bvh"] = per_island_bvh
        cached["per_island_remap"] = per_island_remap
    return bvh_3d, tri_islands, per_island_bvh, per_island_remap


def _bbox_diagonal(triangles) -> float:
    """World-space bounding-box diagonal of a triangle list."""
    if not triangles:
        return 0.0
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for tri in triangles:
        for p in tri:
            for k in range(3):
                if p[k] < lo[k]:
                    lo[k] = p[k]
                if p[k] > hi[k]:
                    hi[k] = p[k]
    return sum((hi[k] - lo[k]) ** 2 for k in range(3)) ** 0.5


def _choose_new_vert_world(i, key, mesh, matrix, edge_neighbors, attachments):
    """The trustworthy world-space 3D position of a vert created in Edit Mode.

    On Edit-mode exit the edit-cage coords land in the active ShapeKey, while
    mesh.vertices / Basis hold whatever Blender's custom-data interpolation
    produced — and for some tools the ShapeKey layer itself ends up garbage
    (the "vertex flies away on Tab" spike).  Pick whichever of the two stores
    lies closer to the mean ShapeKey position of the vert's already-bound
    neighbours; if even the winner is wildly outside the neighbours' spread
    (a true garbage spike), fall back to the neighbour mean itself so the
    repair lands near the new vert's topological home.
    """
    pk = matrix @ key.data[i].co
    pv = matrix @ mesh.vertices[i].co
    ref = [
        matrix @ key.data[j].co
        for j in (edge_neighbors[i] if i < len(edge_neighbors) else ())
        if 0 <= j < len(attachments) and attachments[j].is_ok
    ]
    if not ref:
        return pk
    mean = ref[0].copy()
    for q in ref[1:]:
        mean += q
    mean /= len(ref)
    if (pk - pv).length_squared < 1e-12:
        p = pk
    else:
        p = pk if (pk - mean).length_squared <= (pv - mean).length_squared else pv
    # Spike guard: neighbours' spread approximates the local edge scale.  A
    # genuine new vert sits within a few edge lengths of its neighbours; a
    # corrupted one can be metres away — repair from the neighbour mean then.
    spread = max((q - mean).length for q in ref)
    if spread > 1e-9 and (p - mean).length > 10.0 * spread:
        return mean
    return p


def _bind_new_verts_inplace(
    retopo, guide_obj, cached, attachments, edge_neighbors, points_3d_world
):
    """Bind every new/stale vert in *attachments* (mutated in place) from its
    3D position, and overwrite the matching *points_3d_world* entries with the
    chosen source position.  Returns the list of bound vertex indices.

    Detection is NOT based on status==none: Blender interpolates our custom
    attributes onto verts created in Edit Mode, so new verts arrive carrying a
    copied attachment with status==ok.  Instead a vert is flagged when its
    Basis (2D) position disagrees with its attachment evaluated on Guide 2D —
    an invariant every legitimate write path maintains (see
    find_unbound_or_stale).

    No-ops (returns []) when nothing is flagged, or when EVERY vert is
    flagged — the latter means the mesh has never been bound at all and needs
    a full 'Sync 2D > 3D' instead.
    """
    basis_world = extract_points_world(retopo)

    # Scale-aware tolerance.  Attachments drift by millimetres through normal
    # editing (RetopoFlow-style tools recreate verts and blend our custom
    # attributes), and that drift is harmless — every Sync recomputes or
    # tolerates it.  Only GROSS mismatches (wrong fold side, interpolated
    # garbage: centimetres to half a metre in measured data) may trigger a
    # repair; 3 × the median retopo edge length separates the two populations
    # by an order of magnitude.  A fixed tiny eps here once flagged 295/708
    # verts on a healthy working file and shredded its layout.
    mesh = retopo.data
    med = 0.0
    if len(mesh.edges):
        lens = sorted(
            (basis_world[e.vertices[0]] - basis_world[e.vertices[1]]).length
            for e in mesh.edges
        )
        med = lens[len(lens) // 2]
    eps = max(3.0 * med, 2e-3)

    stale = set(find_unbound_or_stale(
        attachments, basis_world, points_3d_world,
        cached["tris_2d"], cached["tris_3d"], cached["bvh_2d"],
        eps=eps,
    ))

    bvh_3d, tri_islands, per_island_bvh, per_island_remap = (
        _ensure_reverse_caches(cached, guide_obj)
    )

    # ── Spike detection ──────────────────────────────────────────────────────
    # Some Edit-Mode ops leave a new vert's ShapeKey coordinate pointing far
    # off into space (typically toward the 2D layout area) while its Basis /
    # attachment pair stays self-consistent — invisible to the 2D check above.
    # No legitimate retopo edit places a vert a quarter of the whole garment's
    # bounding diagonal away from the Guide surface, so flag those too.
    diag = cached.get("diag_3d")
    if diag is None:
        diag = _bbox_diagonal(cached["tris_3d"])
        cached["diag_3d"] = diag
    if diag > 0.0:
        max_dist = diag * 0.25
        for i, att in enumerate(attachments):
            if i in stale or not att.is_ok:
                continue
            loc, _n, _ti, dist = bvh_3d.find_nearest(points_3d_world[i])
            if loc is not None and dist > max_dist:
                stale.add(i)

    stale = sorted(stale)
    if not stale or len(stale) == len(attachments):
        return []

    # Demote flagged attachments to none BEFORE binding so they are never used
    # as anchors (neither by bind_new_verts_3d's neighbour search nor by
    # _choose_new_vert_world's reference mean).
    for i in stale:
        attachments[i] = Attachment.none()

    mesh = retopo.data
    key = mesh.shape_keys.key_blocks[SHAPEKEY_NAME]
    matrix = retopo.matrix_world

    for i in stale:
        points_3d_world[i] = _choose_new_vert_world(
            i, key, mesh, matrix, edge_neighbors, attachments
        )

    bound = bind_new_verts_3d(
        stale,
        points_3d_world,
        attachments,
        edge_neighbors,
        tri_islands,
        cached["tris_3d"],
        cached["tris_2d"],
        bvh_3d,
        per_island_bvh,
        per_island_remap,
    )
    for i, att in bound.items():
        attachments[i] = att
    return stale


def run_bind_new_verts(
    context,
    retopo,
    guide_obj,
    flat_sk: str,
) -> ProjectionResult:
    """Bind vertices freshly created in 3D Edit Mode and repair their data.

    Flags every vert whose stored attachment disagrees with its Basis (2D)
    position (the tell that Blender's attribute interpolation manufactured it
    — see find_unbound_or_stale), computes a neighbour-hinted, 2D-coherent
    attachment for each, then rewrites BOTH its Basis (2D layout) and
    AC9_3D_Project (snapped 3D) coordinates.  This fixes the two artefacts new
    verts show right after leaving Edit Mode:
      * the spike — Blender leaves garbage in the non-active shape-key layer
        of a new vert, so the Basis(2D)/ShapeKey mix flings it far away;
      * back-face fusion — a later nearest-surface snap can land the vert on
        the wrong fold side; the 2D-coherent bind prevents that here.

    Cheap when there is nothing to do.  Called by the manual operator and by
    the Edit-mode-exit auto-bind handler.
    """
    if retopo is None or retopo.type != "MESH":
        return ProjectionResult(success=False, error="2D Retopo Object must be a mesh.")

    err = validate_guide(guide_obj, flat_sk)
    if err is not None:
        return ProjectionResult(success=False, error=err)

    retopo_mesh = retopo.data
    n = len(retopo_mesh.vertices)
    if n == 0:
        return ProjectionResult(success=False, error="2D Retopo Object has no vertices.")

    # Never been bound (no status attribute) → nothing to repair; the user
    # must run a full Sync 2D > 3D first.
    if retopo_mesh.attributes.get(ATTR_STATUS) is None:
        return ProjectionResult(success=True, total=n)

    if (
        retopo_mesh.shape_keys is None
        or SHAPEKEY_NAME not in retopo_mesh.shape_keys.key_blocks
    ):
        return ProjectionResult(success=True, total=n)

    attachments = load_attachments_from_mesh(retopo_mesh)
    key = retopo_mesh.shape_keys.key_blocks[SHAPEKEY_NAME]
    matrix = retopo.matrix_world
    edge_neighbors = build_edge_neighbors(retopo_mesh)
    points_3d_world = [matrix @ key.data[i].co for i in range(n)]

    cached = _get_guide_cache(guide_obj, flat_sk)
    tris_2d = cached["tris_2d"]
    tris_3d = cached["tris_3d"]

    bound_idx = _bind_new_verts_inplace(
        retopo, guide_obj, cached, attachments, edge_neighbors, points_3d_world
    )
    if not bound_idx:
        return ProjectionResult(success=True, total=n)

    # ── Rewrite Basis (2D) + ShapeKey (3D) for the bound verts only ─────────
    retopo_inv = matrix.inverted()
    basis = retopo_mesh.shape_keys.reference_key
    repaired = 0
    for i in bound_idx:
        att = attachments[i]
        if not att.is_ok:
            continue
        a2, b2, c2 = tris_2d[att.triangle_index]
        a3, b3, c3 = tris_3d[att.triangle_index]
        u, v, w = att.bary
        local_2d = retopo_inv @ (a2 * u + b2 * v + c2 * w)
        local_3d = retopo_inv @ (a3 * u + b3 * v + c3 * w)
        basis.data[i].co = local_2d
        retopo_mesh.vertices[i].co = local_2d
        key.data[i].co = local_3d
        repaired += 1

    save_attachments_to_mesh(retopo_mesh, attachments)
    # Boundary flag: keep existing flags (written by forward sync / align) but
    # force the freshly-bound verts to False — even when their new attachment
    # happens to land on a seam edge.  Flagging them here would pin a possibly
    # wrong seam-side choice forever: Sync 3D > 2D keeps pinned verts verbatim
    # AND its 2D-jump repair pass skips them.  The flag returns legitimately
    # via the next Sync's re-classification or 'Align Boundary to UV Seams'.
    boundary = load_boundary_from_mesh(retopo_mesh)
    for i in bound_idx:
        if i < len(boundary):
            boundary[i] = False
    save_boundary_to_mesh(retopo_mesh, boundary)
    retopo_mesh.update()
    from . import gpu_overlay
    gpu_overlay.invalidate_boundary()

    failed_count = sum(1 for a in attachments if not a.is_ok)
    return ProjectionResult(
        success=True,
        total=n,
        projected=repaired,
        failed=failed_count,
        new_verts_computed=len(bound_idx),
    )


def _attachments_match(old, new, eps: float = 1e-6) -> bool:
    if len(old) != len(new):
        return False
    for a, b in zip(old, new):
        if a.status != b.status:
            return False
        if not a.is_ok:
            continue
        if a.triangle_index != b.triangle_index:
            return False
        if (
            abs(a.bary[0] - b.bary[0]) > eps
            or abs(a.bary[1] - b.bary[1]) > eps
            or abs(a.bary[2] - b.bary[2]) > eps
        ):
            return False
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_shapekey(retopo, retopo_mesh, overwrite_shapekey: bool):
    if retopo_mesh.shape_keys is None:
        retopo.shape_key_add(name="Basis", from_mix=False)
    key_blocks = retopo_mesh.shape_keys.key_blocks
    if SHAPEKEY_NAME in key_blocks:
        if not overwrite_shapekey:
            return None
        return key_blocks[SHAPEKEY_NAME]
    return retopo.shape_key_add(name=SHAPEKEY_NAME, from_mix=False)


def _update_failed_group(retopo, retopo_mesh, attachments, clear_failed_group, select_failed):
    if FAILED_GROUP_NAME in retopo.vertex_groups:
        if clear_failed_group:
            retopo.vertex_groups.remove(retopo.vertex_groups[FAILED_GROUP_NAME])
            vg = retopo.vertex_groups.new(name=FAILED_GROUP_NAME)
        else:
            vg = retopo.vertex_groups[FAILED_GROUP_NAME]
    else:
        vg = retopo.vertex_groups.new(name=FAILED_GROUP_NAME)

    failed_indices = [i for i, a in enumerate(attachments) if not a.is_ok]
    if failed_indices:
        vg.add(failed_indices, 1.0, "REPLACE")

    if select_failed:
        for v in retopo_mesh.vertices:
            v.select = False
        for idx in failed_indices:
            retopo_mesh.vertices[idx].select = True
