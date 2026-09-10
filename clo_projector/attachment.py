"""Attachment: the per-vertex link between a retopo vertex and a guide triangle.

Holds the triangle index plus barycentric weights so the same retopo vertex
can be re-evaluated on Guide 2D (current MVP path) or Guide 3D (Phase 3
reverse mapping), and so depsgraph updates (Phase 2) can re-project only the
vertices that actually moved.

Persisted on the retopo mesh as per-vertex custom attributes
(`ac9_tri_idx`, `ac9_bary_u`, `ac9_bary_v`, `ac9_status`). New vertices Blender
adds via subdivide / loop-cut inherit default values (status==0), which the
live-update handler detects and computes attachments for on the fly.

Pure module: depends only on mathutils (no bpy import at module level — the
persistence helpers take `mesh` as a duck-typed Blender Mesh).
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import closest_point_on_tri

from .geometry import DEGENERATE_XY_EPS, barycentric_xy, cross_xy


Triangle = Tuple[Vector, Vector, Vector]


# Status codes used both in-memory (Attachment.status) and on disk
# (mesh attribute "ac9_status"). The numeric form is what gets stored — the
# string form is just a convenience for readability inside Python code.
STATUS_NONE = 0  # default value for new vertices: no attachment computed yet
STATUS_OK = 1
STATUS_FAILED = 2

_STATUS_TO_STR = {STATUS_NONE: "none", STATUS_OK: "ok", STATUS_FAILED: "failed"}
_STR_TO_STATUS = {v: k for k, v in _STATUS_TO_STR.items()}

ATTR_TRI_IDX = "ac9_tri_idx"
ATTR_BARY_U = "ac9_bary_u"
ATTR_BARY_V = "ac9_bary_v"
ATTR_STATUS = "ac9_status"
ATTR_IS_BOUNDARY = "ac9_is_boundary"


@dataclass
class Attachment:
    """Link from one retopo vertex to a guide triangle."""

    triangle_index: int = -1
    bary: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    status: str = "none"  # "none" | "ok" | "failed"

    @classmethod
    def none(cls) -> "Attachment":
        return cls()

    @classmethod
    def failed(cls) -> "Attachment":
        return cls(status="failed")

    @classmethod
    def ok(cls, triangle_index: int, bary: Tuple[float, float, float]) -> "Attachment":
        return cls(triangle_index=triangle_index, bary=bary, status="ok")

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"

    @property
    def needs_compute(self) -> bool:
        """True if this vertex has no attachment yet (e.g. freshly added via
        subdivide / loop-cut) and should be computed now."""
        return self.status == "none"


# ---------------------------------------------------------------------------
# Forward projection (2D layout → 3D shape)
# ---------------------------------------------------------------------------


class Bvh2D:
    """Guide 2D BVH holding only triangles barycentric_xy can actually invert.

    Composition rather than subclassing: mathutils.BVHTree is not subclassable
    and every caller only ever needs find_nearest.  Degenerate (zero-XY-area)
    triangles are kept OUT of the tree at build time, and `_remap` maps the
    tree's own face index back to the caller's triangle index — so
    `Attachment.triangle_index` keeps meaning "index into tris_2d / tris_3d"
    and the values already persisted on retopo meshes stay valid.

    The invariant this buys: every triangle find_nearest can return has a
    usable barycentric frame.  Previously the tree also held the slivers CLO
    exports always carry (see clo_cleanup.core: "CLO exports carry slivers"),
    and a retopo vertex that landed on one — which Outline Snap makes likely,
    since it snaps retopo verts exactly onto Guide verts — failed to project
    and was left at its 2D layout position, i.e. metres off the garment.
    Measured on a jacket Guide: 20 of 339545 triangles degenerate, 2 of 1153
    retopo verts flung off the garment by them.
    """

    __slots__ = ("_tree", "_remap", "n_excluded")

    def __init__(self, tree, remap, n_excluded: int = 0):
        self._tree = tree
        # None means "identity" — nothing was excluded, so no indirection.
        self._remap = remap
        self.n_excluded = n_excluded

    def find_nearest(self, co):
        """(location, normal, triangle_index, distance), as BVHTree does.

        triangle_index is already mapped back to the caller's numbering.
        Returns four Nones when the tree is empty (every triangle degenerate),
        which callers already handle as a failed attachment.
        """
        if self._tree is None:
            return (None, None, None, None)
        loc, normal, index, dist = self._tree.find_nearest(co)
        if index is None or self._remap is None:
            return (loc, normal, index, dist)
        return (loc, normal, self._remap[index], dist)


def build_bvh_2d(triangles_2d_world: Sequence[Triangle]) -> Bvh2D:
    """Build a Z=0-flattened BVH over the invertible Guide 2D triangles.

    All vertices are projected to z=0 so that find_nearest queries from any
    retopo 2D vertex (also lying at z≈0) return the containing or nearest
    triangle without requiring a brute-force scan.  Building is O(M log M) in
    C; each subsequent find_nearest is O(log M) — versus the former O(N*M)
    Python loop over every triangle per retopo vertex.

    Triangles whose |cross_xy| is below DEGENERATE_XY_EPS are skipped, which is
    what makes the returned Bvh2D's invariant hold; the returned object carries
    how many were skipped so the caller can report it.

    Vertices are stored as plain tuples (cheaper than Vector) since BVHTree
    accepts any 3-element sequence.
    """
    verts: List = []
    faces: List = []
    remap: List[int] = []
    n_excluded = 0
    for i, (a, b, c) in enumerate(triangles_2d_world):
        if abs(cross_xy(a, b, c)) < DEGENERATE_XY_EPS:
            n_excluded += 1
            continue
        base = len(verts)
        verts.append((a.x, a.y, 0.0))
        verts.append((b.x, b.y, 0.0))
        verts.append((c.x, c.y, 0.0))
        faces.append((base, base + 1, base + 2))
        remap.append(i)
    if not faces:
        return Bvh2D(None, None, n_excluded)
    tree = BVHTree.FromPolygons(verts, faces, all_triangles=True)
    return Bvh2D(tree, remap if n_excluded else None, n_excluded)


def _attach_point_bvh(
    p: Vector,
    bvh_2d: Bvh2D,
    triangles_2d_world: Sequence[Triangle],
) -> "Attachment":
    """Attach one retopo vertex via a single BVH nearest query.

    find_nearest naturally handles both inside-triangle and outside-triangle
    cases: when p is inside a triangle the snapped point equals p; when p is
    outside every triangle the snapped point lies on the nearest boundary.
    Either way bary is computed at the snapped location, which is always
    within the returned triangle.

    Bvh2D excludes degenerate triangles, so the `bary is None` branch below is
    unreachable for a tree built by build_bvh_2d — it stays as the guard that
    keeps this function correct for any tree, and as the last line of defence
    if that invariant is ever broken.
    """
    loc, _, tri_idx, _ = bvh_2d.find_nearest(Vector((p.x, p.y, 0.0)))
    if loc is None or tri_idx is None:
        return Attachment.failed()
    a, b, c = triangles_2d_world[tri_idx]
    bary = barycentric_xy(loc, a, b, c)
    if bary is None:
        return Attachment.failed()
    return Attachment.ok(tri_idx, bary)


def compute_attachments(
    retopo_points_world: Sequence[Vector],
    triangles_2d_world: Sequence[Triangle],
    *,
    bvh_2d: Optional[Bvh2D] = None,
    progress=None,
) -> List[Attachment]:
    """For each retopo vertex find the Guide 2D triangle that contains it on
    the XY plane and record (triangle_index, barycentric).  Vertices outside
    every triangle are snapped to the nearest boundary.

    Uses a BVHTree for O(N log M) performance.  If the caller already built a
    BVH (e.g. from a cached guide), pass it via *bvh_2d* to skip rebuilding.

    `progress`, if given, is called with a 0..1 fraction as this walks the
    vertex list — the natural per-vertex loop callers with a slow-on-real-
    data mesh hook a wm.progress_update into. Defaults to None (no-op) so
    every existing caller is unaffected.
    """
    if bvh_2d is None:
        bvh_2d = build_bvh_2d(triangles_2d_world)
    tick = progress if callable(progress) else None
    n = len(retopo_points_world)
    out = []
    # Report progress at most once per percent, not once per vertex. The
    # callback ends in wm.progress_update, which is free headless but redraws
    # the cursor in the GUI -- measured at ~1.4 ms per call, so calling it per
    # vertex made a 4153-vertex refresh take 6 s of pure cursor updates.
    last_pct = -1
    for i, p in enumerate(retopo_points_world):
        out.append(_attach_point_bvh(p, bvh_2d, triangles_2d_world))
        if tick is not None and n:
            pct = (i * 100) // n
            if pct != last_pct:
                last_pct = pct
                tick(i / n)
    return out


def compute_attachments_for_indices(
    retopo_points_world: Sequence[Vector],
    triangles_2d_world: Sequence[Triangle],
    indices: Sequence[int],
    *,
    bvh_2d: Optional[Bvh2D] = None,
) -> List[Tuple[int, Attachment]]:
    """Same as compute_attachments but only for the specified vertex indices.
    Used by the live-update handler to fill in attachments for new vertices
    without re-scanning every existing vertex.
    """
    if bvh_2d is None:
        bvh_2d = build_bvh_2d(triangles_2d_world)
    return [
        (i, _attach_point_bvh(retopo_points_world[i], bvh_2d, triangles_2d_world))
        for i in indices
    ]


def apply_attachments_to_3d(
    attachments: Sequence[Attachment],
    fallback_positions: Sequence[Vector],
    triangles_3d_world: Sequence[Triangle],
) -> List[Vector]:
    """For each Attachment, reconstruct a world-space position on the Guide 3D
    triangle of the same index using its barycentric coords. Failed/none
    attachments fall back to the matching position in `fallback_positions`
    (typically the retopo's current 2D position).
    """
    return _apply_to_triangles(attachments, fallback_positions, triangles_3d_world)


def apply_attachments_to_2d(
    attachments: Sequence[Attachment],
    fallback_positions: Sequence[Vector],
    triangles_2d_world: Sequence[Triangle],
) -> List[Vector]:
    """Mirror of apply_attachments_to_3d but onto Guide 2D triangles. Used by
    the reverse (3D→2D) operator after rebinding attachments via BVH.
    """
    return _apply_to_triangles(attachments, fallback_positions, triangles_2d_world)


def _apply_to_triangles(attachments, fallback_positions, triangles):
    out: List[Vector] = []
    for att, fallback in zip(attachments, fallback_positions):
        if not att.is_ok:
            out.append(fallback.copy())
            continue
        a, b, c = triangles[att.triangle_index]
        u, v, w = att.bary
        out.append(a * u + b * v + c * w)
    return out


# ---------------------------------------------------------------------------
# Reverse projection (3D shape → 2D layout)
# ---------------------------------------------------------------------------


def build_bvh(triangles_world: Sequence[Triangle]) -> BVHTree:
    """Build a BVH over a triangle list for nearest-surface queries.

    `triangles_world` is the same format used elsewhere — a list of 3-tuples of
    world-space Vectors. We unpack them into a flat verts+faces representation
    because BVHTree.FromPolygons wants that shape.
    """
    verts: List[Vector] = []
    faces: List[Tuple[int, int, int]] = []
    for tri in triangles_world:
        base = len(verts)
        verts.append(tri[0])
        verts.append(tri[1])
        verts.append(tri[2])
        faces.append((base, base + 1, base + 2))
    return BVHTree.FromPolygons(verts, faces, all_triangles=True)


def rebind_attachments_from_3d(
    points_world: Sequence[Vector],
    bvh_3d: BVHTree,
    triangles_3d_world: Sequence[Triangle],
    existing_attachments: Optional[Sequence["Attachment"]] = None,
    island_bias: float = 1.1,
) -> List[Attachment]:
    """For each 3D point, snap to the nearest point on Guide 3D and compute a
    new Attachment for that snapped position.

    existing_attachments (optional)
        When provided, a "sticky island" bias is applied: if the vertex's
        previously bound Guide 3D triangle is within island_bias × the BVH
        best-distance, that triangle is kept rather than switching to the BVH
        result.  This prevents UV-island jumps when the user makes minor 3D
        edits near seam boundaries — at a seam, both sides of the fabric are
        spatially coincident in 3D, so the BVH can non-deterministically pick
        either side.  Keeping the existing triangle means the vertex stays in
        its original UV island for small edits; large deliberate moves (where
        the BVH finds something clearly closer) still switch naturally.

    island_bias
        Tolerance factor. 1.1 = prefer existing triangle if its projection
        distance is within 10 % of the BVH's best distance.  At seam edges
        both distances are ≈ 0, so even a tiny bias keeps the existing island.
    """
    n = len(existing_attachments) if existing_attachments else 0
    out: List[Attachment] = []
    for i, p in enumerate(points_world):
        existing = existing_attachments[i] if i < n else None
        if existing is not None and existing.is_ok:
            out.append(
                _attach_via_bvh_sticky(p, existing, bvh_3d, triangles_3d_world, island_bias)
            )
        else:
            out.append(_attach_via_bvh(p, bvh_3d, triangles_3d_world))
    return out


def _attach_via_bvh_sticky(
    point_world,
    existing: "Attachment",
    bvh_3d: BVHTree,
    triangles_3d_world: Sequence[Triangle],
    island_bias: float,
) -> "Attachment":
    """BVH lookup with existing-attachment bias to preserve UV island.

    Logic:
    1. Ask BVH for the globally nearest point (and its triangle).
    2. Project point_world onto the *existing* triangle to measure old_dist.
    3. If old_dist <= island_bias * bvh_dist → keep existing triangle
       (same UV island, just recompute barycentric at the projected point).
    4. Otherwise the vertex has genuinely moved far → use BVH result.
    """
    # ── BVH result ────────────────────────────────────────────
    bvh_loc, _normal, bvh_idx, _d = bvh_3d.find_nearest(point_world)

    # BVH failed entirely — fall back to existing attachment as-is.
    if bvh_loc is None or bvh_idx is None:
        return existing

    # Same triangle already → just accept BVH (barycentric update only).
    if bvh_idx == existing.triangle_index:
        a, b, c = triangles_3d_world[bvh_idx]
        bary = _barycentric_3d(bvh_loc, a, b, c)
        return Attachment.ok(bvh_idx, bary) if bary else Attachment.failed()

    # ── Compare old vs BVH distances ──────────────────────────
    old_idx = existing.triangle_index
    if old_idx < 0 or old_idx >= len(triangles_3d_world):
        # Stale index — just take BVH.
        a, b, c = triangles_3d_world[bvh_idx]
        bary = _barycentric_3d(bvh_loc, a, b, c)
        return Attachment.ok(bvh_idx, bary) if bary else Attachment.failed()

    a_old, b_old, c_old = triangles_3d_world[old_idx]
    loc_old = closest_point_on_tri(point_world, a_old, b_old, c_old)
    dist_old = (loc_old - point_world).length
    dist_bvh = (bvh_loc - point_world).length

    # Prefer existing triangle if it's close enough (same UV island).
    if dist_old <= dist_bvh * island_bias:
        bary = _barycentric_3d(loc_old, a_old, b_old, c_old)
        if bary is not None:
            return Attachment.ok(old_idx, bary)

    # BVH wins — vertex moved to a genuinely different surface area.
    a, b, c = triangles_3d_world[bvh_idx]
    bary = _barycentric_3d(bvh_loc, a, b, c)
    return Attachment.ok(bvh_idx, bary) if bary else Attachment.failed()


def _attach_via_bvh(point_world, bvh_3d, triangles_3d_world) -> "Attachment":
    location, _normal, index, _distance = bvh_3d.find_nearest(point_world)
    if location is None or index is None:
        return Attachment.failed()
    a, b, c = triangles_3d_world[index]
    bary = _barycentric_3d(location, a, b, c)
    if bary is None:
        return Attachment.failed()
    return Attachment.ok(index, bary)


def build_per_island_bvhs(
    tri_islands: Sequence[int],
    triangles_3d_world: Sequence[Triangle],
) -> Tuple[dict, dict]:
    """Build one BVHTree per UV island, plus a local→global triangle-index map.

    Why per-island: the topology-first reverse mapping snaps each retopo vertex
    to the closest 3D point WITHIN its assigned island.  A single global BVH
    would return triangles from any island, defeating the constraint.  Building
    ~N_islands smaller BVHs makes each within-island query O(log M_island)
    instead of O(M_island) Python brute force — for 200k guides this is the
    difference between ~60 s and ~50 ms.

    Returns
    -------
    bvhs : dict[island_id, BVHTree]
    remaps : dict[island_id, list[int]]
        Maps the BVH's local face index back to the global triangle index used
        by attachment.triangle_index / triangles_3d_world.
    """
    by_island: dict = {}
    n_tris = min(len(tri_islands), len(triangles_3d_world))
    for global_idx in range(n_tris):
        by_island.setdefault(tri_islands[global_idx], []).append(global_idx)

    bvhs: dict = {}
    remaps: dict = {}
    for isl, global_tris in by_island.items():
        verts: List = []
        faces: List = []
        for local_idx, global_idx in enumerate(global_tris):
            a, b, c = triangles_3d_world[global_idx]
            base = local_idx * 3
            verts.append((a.x, a.y, a.z))
            verts.append((b.x, b.y, b.z))
            verts.append((c.x, c.y, c.z))
            faces.append((base, base + 1, base + 2))
        bvhs[isl] = BVHTree.FromPolygons(verts, faces, all_triangles=True)
        remaps[isl] = global_tris
    return bvhs, remaps


def build_edge_neighbors(mesh) -> List[List[int]]:
    """Per-vertex list of edge-connected neighbour indices on *mesh*.

    Used by the island-aware reverse mapping to propagate UV-island membership
    along the retopo topology — independent of the spatial 3D positions.
    """
    n = len(mesh.vertices)
    nbrs: List[List[int]] = [[] for _ in range(n)]
    for e in mesh.edges:
        a, b = int(e.vertices[0]), int(e.vertices[1])
        nbrs[a].append(b)
        nbrs[b].append(a)
    return nbrs


def rebind_attachments_island_aware(
    points_world: Sequence[Vector],
    existing_attachments: Sequence["Attachment"],
    edge_neighbors: Sequence[Sequence[int]],
    tri_islands: Sequence[int],
    triangles_3d_world: Sequence[Triangle],
    bvh_3d: BVHTree,
    per_island_bvh: Optional[dict] = None,
    per_island_remap: Optional[dict] = None,
    boundary_mask: Optional[Sequence[bool]] = None,
    *,
    triangles_2d_world: Optional[Sequence[Triangle]] = None,
    consensus_passes: int = 8,
    sticky_bias: float = 1.5,
    progress=None,
) -> Tuple[List["Attachment"], List[int]]:
    """Topology-first reverse binding.

    For each retopo vertex we first decide which UV island it belongs to using
    only the retopo topology — never the 3D position — then snap to the closest
    point WITHIN that island.  This makes UV-seam crossings structurally
    impossible: a vertex that drifted past its island's boundary in 3D lands on
    the boundary edge, which is exactly the seam.

    Island assignment rules
    -----------------------
    1. If the vertex has a usable existing attachment (status == ok), keep
       its previous island.  This preserves the user's prior binding and is
       the load-bearing rule that prevents stochastic seam jumps.
    2. Otherwise (newly-added vertex, or status != ok), pull the island from
       edge-connected neighbours via iterative majority vote until convergence
       (or *consensus_passes* iterations).
    3. If still unassigned (truly isolated vertex with no bound neighbour),
       fall back to global BVH — this only fires on the very first bind.

    Returns
    -------
    new_attachments : list[Attachment]
    jumped_indices  : list[int]
        Vertex indices where the global BVH nearest triangle belongs to a
        DIFFERENT island than the topological assignment.  These vertices were
        snapped to the island boundary instead of crossing the seam — useful
        telemetry, not an error.  In a healthy edit the list stays short;
        long lists mean the user's 3D edits pushed verts well across seams.
    """
    n = len(points_world)
    n_tri_isl = len(tri_islands)

    # ── 1. Seed assignment from existing attachments ────────────────────────
    assigned: List[Optional[int]] = [None] * n
    for i in range(n):
        if i >= len(existing_attachments):
            break
        att = existing_attachments[i]
        if att.is_ok and 0 <= att.triangle_index < n_tri_isl:
            assigned[i] = tri_islands[att.triangle_index]

    # ── 2. Iterative neighbour propagation for unassigned vertices ──────────
    for _ in range(consensus_passes):
        changed = False
        for i in range(n):
            if assigned[i] is not None:
                continue
            votes: dict = {}
            for nb in edge_neighbors[i]:
                isl = assigned[nb] if 0 <= nb < n else None
                if isl is not None:
                    votes[isl] = votes.get(isl, 0) + 1
            if votes:
                assigned[i] = max(votes, key=votes.get)
                changed = True
        if not changed:
            break

    # ── 3. Snap each vertex to closest point within its assigned island ─────
    # Per-island BVH path is fast (C, O(log M_island)); the brute-force
    # _attach_within_island fallback is kept for callers that don't pass BVHs.
    sticky_sq = sticky_bias * sticky_bias
    n_tris_total = len(triangles_3d_world)
    new_attachments: List[Attachment] = []
    jumped_indices: List[int] = []

    for i in range(n):
        if progress is not None and n:
            progress((i + 1) / n)
        p = points_world[i]
        island = assigned[i]

        # ── Boundary pin: UV-seam vertices are sacred (CLO pattern outline) ─
        # If the vertex was previously identified as sitting on a Guide seam
        # edge, its 2D position is the pattern's truth — we don't re-snap on
        # 3D edits.  Keep the existing attachment verbatim, which gives an
        # identical 2D position and snaps the 3D back onto the seam edge.
        if (
            boundary_mask is not None
            and i < len(boundary_mask)
            and boundary_mask[i]
            and i < len(existing_attachments)
            and existing_attachments[i].is_ok
        ):
            new_attachments.append(existing_attachments[i])
            continue

        if island is None:
            new_attachments.append(_attach_via_bvh(p, bvh_3d, triangles_3d_world))
            continue

        # ── Best in-island candidate via per-island BVH (or brute force) ───
        best_idx, best_loc, best_dist_sq = -1, None, float("inf")
        if per_island_bvh is not None and island in per_island_bvh:
            loc, _, local_idx, _ = per_island_bvh[island].find_nearest(p)
            if loc is not None and local_idx is not None:
                best_idx = per_island_remap[island][local_idx]
                best_loc = loc
                best_dist_sq = (loc - p).length_squared
        else:
            for ti, (a, b, c) in enumerate(triangles_3d_world):
                if ti >= n_tri_isl or tri_islands[ti] != island:
                    continue
                loc = closest_point_on_tri(p, a, b, c)
                d = (loc - p).length_squared
                if d < best_dist_sq:
                    best_dist_sq = d
                    best_idx = ti
                    best_loc = loc

        # ── Sticky bias: keep existing triangle if it's still close enough ─
        # Stable verts (the user didn't move) get pinned to their previous
        # attachment, producing identical 2D output.  Without this the closest
        # in-island triangle can flip non-deterministically near boundary
        # edges, causing the long "within-island jump" artefacts we saw in
        # the sleeve cuffs.
        existing = existing_attachments[i] if i < len(existing_attachments) else None
        if (
            existing is not None
            and existing.is_ok
            and 0 <= existing.triangle_index < n_tris_total
            and 0 <= existing.triangle_index < n_tri_isl
            and tri_islands[existing.triangle_index] == island
        ):
            a, b, c = triangles_3d_world[existing.triangle_index]
            loc_old = closest_point_on_tri(p, a, b, c)
            dist_old_sq = (loc_old - p).length_squared
            if dist_old_sq <= best_dist_sq * sticky_sq:
                best_idx = existing.triangle_index
                best_loc = loc_old
                best_dist_sq = dist_old_sq

        # ── Build the attachment ───────────────────────────────────────────
        if best_idx < 0 or best_loc is None:
            att = _attach_via_bvh(p, bvh_3d, triangles_3d_world)
        else:
            a, b, c = triangles_3d_world[best_idx]
            bary = _barycentric_3d(best_loc, a, b, c)
            att = (
                Attachment.ok(best_idx, bary) if bary is not None
                else _attach_via_bvh(p, bvh_3d, triangles_3d_world)
            )
        # Never regress a previously-bound vert to "failed": a failed 2D apply
        # falls back to the raw position, which is what produced the stray
        # diagonal streaks.  Keep the old binding so the vert stays mapped.
        if not att.is_ok and existing is not None and existing.is_ok:
            att = existing
        new_attachments.append(att)

        # Telemetry: did the global BVH disagree with the topological island?
        _, _, bvh_idx, _ = bvh_3d.find_nearest(p)
        if (
            bvh_idx is not None
            and 0 <= bvh_idx < n_tri_isl
            and tri_islands[bvh_idx] != island
        ):
            jumped_indices.append(i)

    # ── 4. 2D-coherence repair pass ─────────────────────────────────────────
    # The per-island snap above keeps every vertex on the right island, but
    # within an island whose 3D surface folds back on itself (sleeve tube,
    # collar, hem) the nearest-in-3D triangle can sit on the wrong fold side —
    # and its 2D unwrap position lands on the far edge of the island, i.e. the
    # "diagonal jump" artefact.  The 3D sticky-bias can't catch this once the
    # user has genuinely moved the vertex.  This pass anchors each vertex to
    # its same-island neighbours' 2D centroid and re-picks the in-island
    # triangle whose 2D position matches, so 2D topology — not 3D proximity —
    # has the final say for outliers.
    if (
        triangles_2d_world is not None
        and per_island_bvh is not None
        and per_island_remap is not None
    ):
        _repair_2d_jumps(
            new_attachments,
            points_world,
            edge_neighbors,
            tri_islands,
            triangles_3d_world,
            triangles_2d_world,
            per_island_bvh,
            per_island_remap,
            boundary_mask,
        )

    return new_attachments, jumped_indices


def _repair_2d_jumps(
    new_attachments: List["Attachment"],
    points_world: Sequence[Vector],
    edge_neighbors: Sequence[Sequence[int]],
    tri_islands: Sequence[int],
    triangles_3d_world: Sequence[Triangle],
    triangles_2d_world: Sequence[Triangle],
    per_island_bvh: dict,
    per_island_remap: dict,
    boundary_mask: Optional[Sequence[bool]],
    *,
    jump_factor: float = 4.0,
    max_iters: int = 3,
    search_radii: Tuple[float, ...] = (0.01, 0.03, 0.08),
) -> List[int]:
    """Pull back vertices whose 2D position is a wild outlier vs their
    same-island neighbours (the diagonal-jump fix).

    For each non-pinned vertex we form the centroid of its already-resolved
    same-island neighbours in 2D.  If the vertex's own 2D position deviates by
    more than ``jump_factor`` × the local neighbour spacing it's flagged as a
    jump.  We then look at the in-island guide triangles near the same 3D point
    (via the per-island BVH find_nearest_range) and re-pick the one whose 2D
    position is closest to that centroid — anchoring to 2D topology rather than
    ambiguous 3D proximity.  Iterated a few times so corrections propagate
    inward from the stable (un-jumped) majority.

    Mutates ``new_attachments`` in place.  Returns the list of repaired indices.
    """
    n = len(new_attachments)
    n_tri_isl = len(tri_islands)
    n_tri_2d = len(triangles_2d_world)

    def _pos2d(att: "Attachment") -> Vector:
        a, b, c = triangles_2d_world[att.triangle_index]
        u, v, w = att.bary
        return Vector((a.x * u + b.x * v + c.x * w,
                       a.y * u + b.y * v + c.y * w, 0.0))

    cur2d: List[Optional[Vector]] = [None] * n
    island_of: List[Optional[int]] = [None] * n
    for i, att in enumerate(new_attachments):
        if att.is_ok and 0 <= att.triangle_index < n_tri_2d and att.triangle_index < n_tri_isl:
            cur2d[i] = _pos2d(att)
            island_of[i] = tri_islands[att.triangle_index]

    repaired: List[int] = []
    for _ in range(max_iters):
        changed = False
        for i in range(n):
            if cur2d[i] is None:
                continue
            # Pinned boundary verts are the pattern's truth — never move them.
            if boundary_mask is not None and i < len(boundary_mask) and boundary_mask[i]:
                continue

            isl = island_of[i]
            nb = [
                cur2d[j]
                for j in edge_neighbors[i]
                if 0 <= j < n and cur2d[j] is not None and island_of[j] == isl
            ]
            if len(nb) < 2:
                continue

            cx = sum(p.x for p in nb) / len(nb)
            cy = sum(p.y for p in nb) / len(nb)
            ds = sorted(((p.x - cx) ** 2 + (p.y - cy) ** 2) ** 0.5 for p in nb)
            scale = ds[len(ds) // 2] or 1e-6
            dev = ((cur2d[i].x - cx) ** 2 + (cur2d[i].y - cy) ** 2) ** 0.5
            if dev <= jump_factor * scale:
                continue

            # ── Outlier → re-pick within island, minimising 2D dist to centroid
            bvh = per_island_bvh.get(isl)
            remap = per_island_remap.get(isl)
            if bvh is None or remap is None:
                continue
            p3 = points_world[i]
            cands = []
            for r in search_radii:
                cands = bvh.find_nearest_range(p3, r)
                if len(cands) >= 2:
                    break
            if not cands:
                continue

            best = None
            best_d2 = float("inf")
            for loc, _nrm, local_idx, _d in cands:
                if local_idx is None or local_idx >= len(remap):
                    continue
                gti = remap[local_idx]
                a, b, c = triangles_3d_world[gti]
                bary = _barycentric_3d(loc, a, b, c)
                if bary is None:
                    continue
                a2, b2, c2 = triangles_2d_world[gti]
                u, v, w = bary
                px = a2.x * u + b2.x * v + c2.x * w
                py = a2.y * u + b2.y * v + c2.y * w
                d2 = (px - cx) ** 2 + (py - cy) ** 2
                if d2 < best_d2:
                    best_d2 = d2
                    best = (gti, bary, Vector((px, py, 0.0)))

            if best is None:
                continue
            gti, bary, newp = best
            if gti != new_attachments[i].triangle_index:
                new_attachments[i] = Attachment.ok(gti, bary)
                cur2d[i] = newp
                island_of[i] = tri_islands[gti] if gti < n_tri_isl else isl
                changed = True
                repaired.append(i)

        if not changed:
            break

    return repaired


def attach_with_neighbor_hint(
    point_world: Vector,
    neighbor_triangle_indices: Sequence[int],
    triangles_3d_world: Sequence[Triangle],
    bvh_3d_fallback: BVHTree,
) -> "Attachment":
    """Snap a 3D point to the closest Guide 3D triangle, but bias the search
    toward triangles that the point's mesh neighbours are already bound to.

    Motivating case: a knife-cut on the retopo creates a new vertex sitting
    "inside" the curved Guide 3D surface (the cut runs across the chord of a
    retopo face). A naive BVH find_nearest can hop to a totally different
    triangle on the other side of a concavity. But the cut point is by
    construction in the neighbourhood of the face's corner verts, which are
    bound to specific Guide 3D triangles — so we test those first via
    closest_point_on_tri and only fall back to BVH if no candidate is in
    range.

    `neighbor_triangle_indices` is the set of Guide 3D triangle indices that
    the new vert's mesh-connected neighbours are currently attached to.
    Empty / None → goes straight to BVH (true Phase-3.5 "island" case).
    """
    if not neighbor_triangle_indices:
        return _attach_via_bvh(point_world, bvh_3d_fallback, triangles_3d_world)

    best_dist_sq = float("inf")
    best_idx = -1
    best_loc = None
    for tri_idx in neighbor_triangle_indices:
        if tri_idx < 0 or tri_idx >= len(triangles_3d_world):
            continue
        a, b, c = triangles_3d_world[tri_idx]
        loc = closest_point_on_tri(point_world, a, b, c)
        d = (loc - point_world).length_squared
        if d < best_dist_sq:
            best_dist_sq = d
            best_idx = tri_idx
            best_loc = loc

    if best_idx < 0 or best_loc is None:
        return _attach_via_bvh(point_world, bvh_3d_fallback, triangles_3d_world)

    a, b, c = triangles_3d_world[best_idx]
    bary = _barycentric_3d(best_loc, a, b, c)
    if bary is None:
        return _attach_via_bvh(point_world, bvh_3d_fallback, triangles_3d_world)
    return Attachment.ok(best_idx, bary)


def find_unbound_or_stale(
    attachments: Sequence["Attachment"],
    basis_points_world: Sequence[Vector],
    key_points_world: Sequence[Vector],
    triangles_2d_world: Sequence[Triangle],
    triangles_3d_world: Sequence[Triangle],
    bvh_2d: Optional[Bvh2D] = None,
    eps: float = 1e-4,
) -> List[int]:
    """Indices of verts whose stored attachment cannot be trusted.

    Blender INTERPOLATES custom attributes onto vertices created in Edit Mode
    (extrude copies the source vert's values, knife/subdivide blend them), so
    a brand-new vert arrives with a plausible-looking attachment and
    status==ok — ``status == none`` alone can NOT identify new verts.

    A vert is flagged only when BOTH stores disagree with its attachment:

      d2: Basis (2D) vs attachment on Guide 2D
      d3: ShapeKey (3D) vs attachment on Guide 3D

    Why both: a user who edited the 2D layout and hasn't pressed Sync 2D>3D
    yet legitimately has d2 > 0 — but their ShapeKey still matches (d3 == 0),
    since only Sync writes it.  Conversely a user's 3D edit gives d3 > 0 with
    d2 == 0.  Only Blender's attribute interpolation produces verts where
    BOTH disagree.  (Flagging on d2 alone shredded a real working file: 295
    of 708 verts were mid-2D-edit and got their Basis rewritten from 3D.)

    Overhang exception: verts outside the pattern outline legitimately carry
    a nearest-boundary attachment (att2d == snap(basis) ≠ basis) — compared
    via *bvh_2d* and not flagged.

    status==failed verts are NOT flagged: their Basis is the user's authored
    2D position and re-binding them is the explicit job of the Sync operators.
    """
    eps_sq = eps * eps
    n_tri = min(len(triangles_2d_world), len(triangles_3d_world))
    out: List[int] = []
    for i, att in enumerate(attachments):
        if i >= len(basis_points_world) or i >= len(key_points_world):
            break
        if att.needs_compute:
            out.append(i)
            continue
        if not att.is_ok:
            continue
        ti = att.triangle_index
        if not (0 <= ti < n_tri):
            out.append(i)
            continue
        u, v, w = att.bary

        # ── 2D check ─────────────────────────────────────────────────────
        a, b, c = triangles_2d_world[ti]
        px = a.x * u + b.x * v + c.x * w
        py = a.y * u + b.y * v + c.y * w
        p = basis_points_world[i]
        if (px - p.x) ** 2 + (py - p.y) ** 2 <= eps_sq:
            continue  # basis agrees (vert inside the layout)
        if bvh_2d is not None:
            # Overhang: compare against what binding basis would give.
            loc, _n, _ti2, _d = bvh_2d.find_nearest(Vector((p.x, p.y, 0.0)))
            if (
                loc is not None
                and (px - loc.x) ** 2 + (py - loc.y) ** 2 <= eps_sq
            ):
                continue

        # ── 3D check (only reached when 2D disagrees) ────────────────────
        a3, b3, c3 = triangles_3d_world[ti]
        q = key_points_world[i]
        qx = a3.x * u + b3.x * v + c3.x * w
        qy = a3.y * u + b3.y * v + c3.y * w
        qz = a3.z * u + b3.z * v + c3.z * w
        if (qx - q.x) ** 2 + (qy - q.y) ** 2 + (qz - q.z) ** 2 <= eps_sq:
            continue  # ShapeKey agrees → this is a pending user 2D edit

        out.append(i)
    return out


def bind_new_verts_3d(
    indices: Sequence[int],
    points_world: Sequence[Vector],
    attachments: Sequence["Attachment"],
    edge_neighbors: Sequence[Sequence[int]],
    tri_islands: Sequence[int],
    triangles_3d_world: Sequence[Triangle],
    triangles_2d_world: Sequence[Triangle],
    bvh_3d: BVHTree,
    per_island_bvh: Optional[dict] = None,
    per_island_remap: Optional[dict] = None,
    *,
    search_radii: Tuple[float, ...] = (0.005, 0.015, 0.04, 0.1),
    dist3d_slack: float = 2.5,
) -> dict:
    """Bind freshly-created vertices (status==none) using neighbour context.

    Why not plain BVH nearest: a new vertex created in 3D Edit Mode typically
    sits slightly OFF the Guide surface (on the chord of a curved span, or with
    a garbage Basis after Blender's shape-key interpolation).  Where the fabric
    folds back on itself within one UV island (sleeve tube, draped front), the
    globally / island-nearest triangle can be on the WRONG FOLD SIDE — the
    vertex then "fuses with the back face" in 2D.  3D distance alone cannot
    disambiguate the fold sides; 2D position can: the back side unwraps to a
    distant part of the island.

    Algorithm (per vertex, processed BFS-style outward from bound neighbours):
      1. Collect edge-connected neighbours that already have an ok attachment
         (pre-existing verts, or new verts bound in an earlier pass).
      2. Majority-vote the UV island from those neighbours.
      3. Candidate triangles = the neighbours' own triangles ∪ per-island BVH
         find_nearest_range hits (radius ladder).
      4. Among candidates within ``dist3d_slack`` × the best 3D distance, pick
         the one whose 2D barycentric position is closest to the neighbours'
         2D centroid.  Fold-side mistakes lose here by construction.
    Vertices with no bound neighbour anywhere in their connected cluster fall
    back to a plain global-BVH bind (genuinely detached islands of new geometry).

    Returns {vertex_index: Attachment} for every index in *indices*.
    """
    n_tris = len(triangles_3d_world)
    n_tri_isl = len(tri_islands)
    n_tri_2d = len(triangles_2d_world)

    def _pos2d(att: "Attachment") -> Optional[Tuple[float, float]]:
        if not (0 <= att.triangle_index < n_tri_2d):
            return None
        a, b, c = triangles_2d_world[att.triangle_index]
        u, v, w = att.bary
        return (a.x * u + b.x * v + c.x * w, a.y * u + b.y * v + c.y * w)

    bound: dict = {}
    new_set = set(indices)

    def _att_of(j: int) -> Optional["Attachment"]:
        if j in bound:
            return bound[j]
        if j in new_set:
            return None  # unbound new vert — not a usable anchor yet
        if 0 <= j < len(attachments) and attachments[j].is_ok:
            return attachments[j]
        return None

    pending = list(indices)
    # Each pass binds every pending vert that has ≥1 bound neighbour; verts
    # bound this pass anchor the next, so corrections grow inward from the
    # stable boundary of the new region.  len(pending)+1 passes is the worst
    # case (a single chain); normally 2-3 passes suffice.
    for _ in range(len(pending) + 1):
        if not pending:
            break
        progressed = False
        deferred: List[int] = []
        for i in pending:
            nb_atts = []
            for j in edge_neighbors[i] if i < len(edge_neighbors) else ():
                a = _att_of(j)
                if a is not None and 0 <= a.triangle_index < n_tris:
                    nb_atts.append(a)
            if not nb_atts:
                deferred.append(i)
                continue
            bound[i] = _bind_one_new_vert(
                points_world[i], nb_atts, tri_islands,
                triangles_3d_world, _pos2d,
                bvh_3d, per_island_bvh, per_island_remap,
                search_radii, dist3d_slack,
            )
            progressed = True
        pending = deferred
        if not progressed:
            break

    # Truly isolated new geometry (no path to any bound vert) — global BVH.
    for i in pending:
        bound[i] = _attach_via_bvh(points_world[i], bvh_3d, triangles_3d_world)
    return bound


def _bind_one_new_vert(
    p: Vector,
    nb_atts: Sequence["Attachment"],
    tri_islands: Sequence[int],
    triangles_3d_world: Sequence[Triangle],
    pos2d_fn,
    bvh_3d: BVHTree,
    per_island_bvh: Optional[dict],
    per_island_remap: Optional[dict],
    search_radii: Tuple[float, ...],
    dist3d_slack: float,
) -> "Attachment":
    """One vertex of bind_new_verts_3d: candidate gathering + 2D-coherent pick."""
    n_tri_isl = len(tri_islands)

    # ── Majority island from neighbours ─────────────────────────────────────
    votes: dict = {}
    for a in nb_atts:
        if a.triangle_index < n_tri_isl:
            isl = tri_islands[a.triangle_index]
            votes[isl] = votes.get(isl, 0) + 1
    island = max(votes, key=votes.get) if votes else None

    # ── Neighbour 2D centroid (the fold-side disambiguator) ─────────────────
    pts2 = [q for q in (pos2d_fn(a) for a in nb_atts) if q is not None]
    centroid = None
    if pts2:
        centroid = (
            sum(q[0] for q in pts2) / len(pts2),
            sum(q[1] for q in pts2) / len(pts2),
        )

    # ── Local 2D scale: median neighbour distance to their centroid ─────────
    # Used to judge whether the picked candidate's 2D position is plausibly
    # "with" the neighbours, or stranded on the wrong side of a sewn seam.
    scale = None
    if centroid is not None and len(pts2) >= 2:
        ds = sorted(
            ((q[0] - centroid[0]) ** 2 + (q[1] - centroid[1]) ** 2) ** 0.5
            for q in pts2
        )
        scale = ds[len(ds) // 2]

    # ── 3D acceptance tolerance from the anchors' own distances ─────────────
    # A relative slack on best3d is NOT enough near a sewn seam: the new vert
    # can sit EXACTLY ON the wrong fold side's surface (best3d ≈ 0), which
    # would shrink the acceptance window to nothing and exclude the correct
    # side entirely.  But topologically the vert hangs off its anchors, so any
    # surface within ~2× the anchor distance is a legitimate candidate — let
    # the 2D-coherence vote decide among those.
    def _pos3d(att: "Attachment") -> Vector:
        a, b, c = triangles_3d_world[att.triangle_index]
        u, v, w = att.bary
        return a * u + b * v + c * w

    nb3 = sorted((_pos3d(a) - p).length for a in nb_atts)
    tol3d = 2.0 * nb3[len(nb3) // 2] if nb3 else 0.0

    def _evaluate(cand_tris):
        """Pick the 2D-coherent best among cand_tris.
        Returns (tri_idx, bary, dist2d_to_centroid) or None."""
        evals = []  # (tri_idx, dist3d, bary)
        for ti in cand_tris:
            a, b, c = triangles_3d_world[ti]
            loc = closest_point_on_tri(p, a, b, c)
            bary = _barycentric_3d(loc, a, b, c)
            if bary is None:
                continue
            evals.append((ti, (loc - p).length, bary))
        if not evals:
            return None
        best3d = min(e[1] for e in evals)
        thr = max(best3d * dist3d_slack, tol3d) + 1e-9
        accepted = [e for e in evals if e[1] <= thr]
        if centroid is None:
            ti, _d, bary = min(accepted, key=lambda e: e[1])
            return ti, bary, 0.0
        best = None
        best_d2 = float("inf")
        for ti, _d, bary in accepted:
            q = pos2d_fn(Attachment.ok(ti, bary))
            if q is None:
                continue
            d2 = ((q[0] - centroid[0]) ** 2 + (q[1] - centroid[1]) ** 2) ** 0.5
            if d2 < best_d2:
                best_d2 = d2
                best = (ti, bary, d2)
        return best

    # ── Candidate gathering: expand the search radius until the pick is 2D-
    # plausible.  At a sewn UV seam BOTH fold sides are spatially coincident in
    # 3D, so a small radius can return wrong-side triangles ONLY — stopping at
    # the first few hits would leave the 2D-coherence vote with no correct-side
    # option (the "diagonal jump across the island" artefact).  We keep growing
    # the radius until the best candidate's 2D position lands within a few
    # neighbour-spacings of the neighbours' centroid.
    cand_tris = {a.triangle_index for a in nb_atts}
    bvh = remap = None
    if (
        island is not None
        and per_island_bvh is not None
        and per_island_remap is not None
    ):
        bvh = per_island_bvh.get(island)
        remap = per_island_remap.get(island)

    # Extend the radius ladder so it can reach the correct side even when the
    # vert crossed the seam by an edge length or two.
    radii = list(search_radii)
    if tol3d > 0.0 and tol3d * 1.5 > radii[-1]:
        radii.append(tol3d * 1.5)

    pick = None
    if bvh is not None and remap is not None:
        for r in radii:
            for _loc, _nrm, local_idx, _d in bvh.find_nearest_range(p, r):
                if local_idx is not None and local_idx < len(remap):
                    cand_tris.add(remap[local_idx])
            result = _evaluate(cand_tris)
            if result is not None:
                pick = result
                if centroid is None or scale is None:
                    break
                if pick[2] <= max(4.0 * scale, 1e-6):
                    break
        if pick is None:
            # Range queries found nothing (vertex far off-surface, e.g. the
            # garbage spike) — at least include the island-nearest triangle.
            _loc, _nrm, local_idx, _d = bvh.find_nearest(p)
            if local_idx is not None and local_idx < len(remap):
                cand_tris.add(remap[local_idx])
            pick = _evaluate(cand_tris)
    else:
        pick = _evaluate(cand_tris)

    if pick is None:
        return _attach_via_bvh(p, bvh_3d, triangles_3d_world)
    return Attachment.ok(pick[0], pick[1])


def snap_vertices_to_islands(
    points_3d_world: Sequence[Vector],
    existing_attachments: Sequence["Attachment"],
    island_ids: List[int],
    triangles_3d_world: Sequence[Triangle],
) -> List["Attachment"]:
    """Snap each vertex to the nearest Guide 3D surface within its UV island.

    For every vertex that has a valid existing attachment, finds the closest
    point on any Guide 3D triangle that belongs to the SAME UV island, then
    returns an updated Attachment at that clamped position.

    Vertices without a valid attachment (status != ok) are left unchanged.
    This is the core operation behind the 'Snap to Island' operator — it
    prevents UV-island boundary crossings that would break Sync 3D > 2D.

    Parameters
    ----------
    points_3d_world     : current world-space positions (from AC9_3D_Project SK)
    existing_attachments: per-vertex Attachments loaded from the retopo mesh
    island_ids          : island_id per triangle (from detect_triangle_islands)
    triangles_3d_world  : Guide 3D triangles (from extract_triangles_basis)
    """
    n = len(points_3d_world)
    n_ids = len(island_ids)
    out = list(existing_attachments)

    for i in range(n):
        existing = existing_attachments[i] if i < len(existing_attachments) else None
        if existing is None or not existing.is_ok:
            continue  # leave unbound or failed vertices as-is

        ti = existing.triangle_index
        if ti < 0 or ti >= n_ids:
            continue

        island_id = island_ids[ti]
        att = _attach_within_island(points_3d_world[i], island_id, island_ids, triangles_3d_world)
        if att.is_ok:
            out[i] = att

    return out


def snap_vertices_to_islands_bvh(
    points_3d_world: Sequence[Vector],
    existing_attachments: Sequence["Attachment"],
    tri_islands: Sequence[int],
    triangles_3d_world: Sequence[Triangle],
    per_island_bvh: dict,
    per_island_remap: dict,
) -> List["Attachment"]:
    """C-fast replacement for snap_vertices_to_islands.

    Identical semantics — snap each vertex to the nearest Guide 3D point WITHIN
    its existing UV island — but each query goes through the pre-built per-island
    BVHTree (O(log M_island) in C) instead of the brute-force Python scan over
    every guide triangle in _attach_within_island.  For a 703k-triangle guide
    this is the difference between ~1-2 min (the freeze) and a few ms.

    Vertices without a valid attachment (status != ok) are left unchanged, same
    as the brute-force version.
    """
    n = len(points_3d_world)
    n_isl = len(tri_islands)
    out = list(existing_attachments)

    for i in range(n):
        existing = existing_attachments[i] if i < len(existing_attachments) else None
        if existing is None or not existing.is_ok:
            continue
        ti = existing.triangle_index
        if ti < 0 or ti >= n_isl:
            continue
        island = tri_islands[ti]
        bvh = per_island_bvh.get(island)
        remap = per_island_remap.get(island)
        if bvh is None or remap is None:
            continue
        loc, _, local_idx, _ = bvh.find_nearest(points_3d_world[i])
        if loc is None or local_idx is None or local_idx >= len(remap):
            continue
        gti = remap[local_idx]
        a, b, c = triangles_3d_world[gti]
        bary = _barycentric_3d(loc, a, b, c)
        if bary is not None:
            out[i] = Attachment.ok(gti, bary)

    return out


def _attach_within_island(
    point_world: Vector,
    island_id: int,
    island_ids: Sequence[int],
    triangles_3d_world: Sequence[Triangle],
) -> "Attachment":
    """Snap point_world to the nearest Guide 3D surface point that belongs
    to island_id.  Searches all triangles in that island exhaustively.
    """
    best_dist_sq = float("inf")
    best_idx = -1
    best_loc = None

    n_ids = len(island_ids)
    for i, (a, b, c) in enumerate(triangles_3d_world):
        if i >= n_ids or island_ids[i] != island_id:
            continue
        loc = closest_point_on_tri(point_world, a, b, c)
        d = (loc - point_world).length_squared
        if d < best_dist_sq:
            best_dist_sq = d
            best_idx = i
            best_loc = loc

    if best_idx < 0 or best_loc is None:
        return Attachment.failed()

    a, b, c = triangles_3d_world[best_idx]
    bary = _barycentric_3d(best_loc, a, b, c)
    return Attachment.ok(best_idx, bary) if bary is not None else Attachment.failed()


def attach_to_island(
    point_world: Vector,
    island_id: int,
    island_ids: Sequence[int],
    triangles_3d_world: Sequence["Triangle"],
) -> "Attachment":
    """Public API: snap *point_world* to the nearest Guide 3D surface point that
    belongs to *island_id*.  Used by the jump-correction pass in core.py.
    """
    return _attach_within_island(point_world, island_id, island_ids, triangles_3d_world)


def _barycentric_3d(p, a, b, c, eps: float = 1e-12):
    """Barycentric of a point known to lie on (or extremely close to) the plane
    of triangle (a, b, c). Same formula as the XY version, just in 3D.
    """
    v0 = b - a
    v1 = c - a
    v2 = p - a
    d00 = v0.dot(v0)
    d01 = v0.dot(v1)
    d11 = v1.dot(v1)
    d20 = v2.dot(v0)
    d21 = v2.dot(v1)
    denom = d00 * d11 - d01 * d01
    if abs(denom) < eps:
        return None
    v = (d11 * d20 - d01 * d21) / denom
    w = (d00 * d21 - d01 * d20) / denom
    u = 1.0 - v - w
    return (u, v, w)


# ---------------------------------------------------------------------------
# Persistence on retopo mesh (custom attributes, per-vertex)
# ---------------------------------------------------------------------------


def _ensure_attr(mesh, name: str, data_type: str) -> None:
    """Ensure an attribute named `name` exists on the POINT domain with the
    correct data_type AND with a `data` length matching mesh.vertices.

    We saw `IndexError: bpy_prop_collection[index]: index 0 out of range,
    size 0` after save/reload — the attribute came back with length 0 even
    though the mesh had vertices. Easiest robust fix: validate everything,
    and force-recreate if any of (domain, data_type, length) is wrong.

    Doesn't return the attribute. The caller fetches it fresh by name after
    all `_ensure_attr` calls, since creating a later attribute can
    invalidate references to earlier ones.
    """
    n = len(mesh.vertices)
    attr = mesh.attributes.get(name)
    if attr is not None:
        broken = (
            attr.domain != "POINT"
            or attr.data_type != data_type
            or len(attr.data) != n
        )
        if broken:
            mesh.attributes.remove(attr)
            attr = None
    if attr is None:
        mesh.attributes.new(name=name, type=data_type, domain="POINT")


def save_attachments_to_mesh(mesh, attachments: Sequence[Attachment]) -> None:
    """Write all attachments to the retopo mesh as per-vertex custom attributes.

    Uses foreach_set for bulk write (much faster + sidesteps per-element
    refcount / invalidation issues we saw with `attr.data[i].value = ...`).
    """
    n = len(mesh.vertices)
    if len(attachments) != n:
        raise ValueError(
            f"attachments length {len(attachments)} != mesh vertex count {n}"
        )

    _ensure_attr(mesh, ATTR_TRI_IDX, "INT")
    _ensure_attr(mesh, ATTR_BARY_U, "FLOAT")
    _ensure_attr(mesh, ATTR_BARY_V, "FLOAT")
    _ensure_attr(mesh, ATTR_STATUS, "INT")

    # Fetch fresh after all ensures — creating a new attribute can
    # invalidate previously-fetched ones.
    tri_attr = mesh.attributes[ATTR_TRI_IDX]
    u_attr = mesh.attributes[ATTR_BARY_U]
    v_attr = mesh.attributes[ATTR_BARY_V]
    status_attr = mesh.attributes[ATTR_STATUS]

    tri_values = [att.triangle_index for att in attachments]
    u_values = [att.bary[0] for att in attachments]
    v_values = [att.bary[1] for att in attachments]
    status_values = [
        _STR_TO_STATUS.get(att.status, STATUS_NONE) for att in attachments
    ]

    tri_attr.data.foreach_set("value", tri_values)
    u_attr.data.foreach_set("value", u_values)
    v_attr.data.foreach_set("value", v_values)
    status_attr.data.foreach_set("value", status_values)


def load_attachments_from_mesh(mesh) -> List[Attachment]:
    """Read per-vertex attachments back from custom attributes. If any of the
    four attributes is missing or has the wrong length, treat every vertex as
    `none` so the caller falls back to recompute.
    """
    n = len(mesh.vertices)
    tri_attr = mesh.attributes.get(ATTR_TRI_IDX)
    u_attr = mesh.attributes.get(ATTR_BARY_U)
    v_attr = mesh.attributes.get(ATTR_BARY_V)
    status_attr = mesh.attributes.get(ATTR_STATUS)

    attrs = (tri_attr, u_attr, v_attr, status_attr)
    if any(a is None for a in attrs) or any(len(a.data) != n for a in attrs):
        return [Attachment.none() for _ in range(n)]

    tri_values = [0] * n
    u_values = [0.0] * n
    v_values = [0.0] * n
    status_values = [0] * n
    tri_attr.data.foreach_get("value", tri_values)
    u_attr.data.foreach_get("value", u_values)
    v_attr.data.foreach_get("value", v_values)
    status_attr.data.foreach_get("value", status_values)

    out: List[Attachment] = []
    for i in range(n):
        status_str = _STATUS_TO_STR.get(int(status_values[i]), "none")
        if status_str != "ok":
            out.append(Attachment(status=status_str))
            continue
        u = float(u_values[i])
        v = float(v_values[i])
        w = 1.0 - u - v
        out.append(Attachment.ok(int(tri_values[i]), (u, v, w)))
    return out


def clear_attachments_on_mesh(mesh) -> None:
    """Remove our custom attributes from the mesh. Used when the user wants a
    clean slate (e.g. before binding to a different Guide pair).
    """
    for name in (ATTR_TRI_IDX, ATTR_BARY_U, ATTR_BARY_V, ATTR_STATUS, ATTR_IS_BOUNDARY):
        attr = mesh.attributes.get(name)
        if attr is not None:
            mesh.attributes.remove(attr)


# ---------------------------------------------------------------------------
# UV-boundary classification (pin retopo verts that sit on Guide seam edges)
# ---------------------------------------------------------------------------


def classify_boundary_verts(
    attachments: Sequence[Attachment],
    seam_edge_mask: Sequence[Tuple[bool, bool, bool]],
    eps: float = 1e-4,
) -> List[bool]:
    """Mark each retopo vertex whose attachment lies on a Guide seam edge.

    A vertex with bary (u, v, w) on triangle (A, B, C) lies on:
      - edge BC  when  u ≈ 0   → mask[0] is the BC seam flag
      - edge AC  when  v ≈ 0   → mask[1] is the AC seam flag
      - edge AB  when  w ≈ 0   → mask[2] is the AB seam flag

    The vertex is "boundary" if it lies on ANY edge that is flagged as a UV
    seam.  Boundary verts will be pinned during Sync 3D > 2D — their 2D
    position is treated as authoritative (the CLO pattern's outline).
    """
    n_tri = len(seam_edge_mask)
    out: List[bool] = []
    for att in attachments:
        if not att.is_ok:
            out.append(False)
            continue
        ti = att.triangle_index
        if not (0 <= ti < n_tri):
            out.append(False)
            continue
        u, v, w = att.bary
        m = seam_edge_mask[ti]
        on_seam = (
            (abs(u) < eps and m[0])
            or (abs(v) < eps and m[1])
            or (abs(w) < eps and m[2])
        )
        out.append(on_seam)
    return out


# ---------------------------------------------------------------------------
# Boundary alignment: snap retopo verts onto Guide 2D seam edges
# ---------------------------------------------------------------------------


def build_seam_segments_2d(
    triangles_2d_world: Sequence[Triangle],
    seam_edge_mask: Sequence[Tuple[bool, bool, bool]],
) -> List[Tuple[int, int, Vector, Vector]]:
    """List Guide seam edges in 2D world space as (tri_idx, edge_id, A, B).

    edge_id matches `seam_edge_mask` / `classify_boundary_verts`:
      0 → edge BC (bary u = 0), segment endpoints B → C
      1 → edge AC (bary v = 0), segment endpoints A → C
      2 → edge AB (bary w = 0), segment endpoints A → B

    Each seam edge appears once per incident triangle — both copies are valid
    hosts because the snapped point lies on the shared edge and bary will have
    the appropriate component exactly 0 either way.
    """
    segs: List[Tuple[int, int, Vector, Vector]] = []
    n = min(len(triangles_2d_world), len(seam_edge_mask))
    for ti in range(n):
        a, b, c = triangles_2d_world[ti]
        m = seam_edge_mask[ti]
        if m[0]:
            segs.append((ti, 0, Vector((b.x, b.y, 0.0)), Vector((c.x, c.y, 0.0))))
        if m[1]:
            segs.append((ti, 1, Vector((a.x, a.y, 0.0)), Vector((c.x, c.y, 0.0))))
        if m[2]:
            segs.append((ti, 2, Vector((a.x, a.y, 0.0)), Vector((b.x, b.y, 0.0))))
    return segs


def build_bvh_from_segments(
    segments: Sequence[Tuple[int, int, Vector, Vector]],
) -> Optional[BVHTree]:
    """Build a 2D BVH over seam segments.

    BVHTree.FromPolygons needs faces, not segments — we synthesise each as a
    near-degenerate triangle (A, B, midpoint + tiny perpendicular).  find_nearest
    on this BVH returns the candidate segment; the caller then computes the true
    closest point on the line segment to avoid any rounding bias from the
    perpendicular offset.
    """
    if not segments:
        return None
    verts: List = []
    faces: List = []
    for i, (_ti, _eid, a, b) in enumerate(segments):
        dx = b.x - a.x
        dy = b.y - a.y
        ln = (dx * dx + dy * dy) ** 0.5 or 1.0
        # Perpendicular offset is 1e-5 of the segment length — small enough not
        # to bias nearest queries, large enough to keep the triangle non-degenerate.
        nx = -dy / ln * 1e-5
        ny = dx / ln * 1e-5
        mx = (a.x + b.x) * 0.5 + nx
        my = (a.y + b.y) * 0.5 + ny
        base = i * 3
        verts.append((a.x, a.y, 0.0))
        verts.append((b.x, b.y, 0.0))
        verts.append((mx, my, 0.0))
        faces.append((base, base + 1, base + 2))
    return BVHTree.FromPolygons(verts, faces, all_triangles=True)


def _closest_pt_on_segment_xy(p: Vector, a: Vector, b: Vector) -> Tuple[Vector, float]:
    """Closest point on segment AB to point P, in the XY plane (z=0).
    Returns (snapped_xy_vector, t) where t in [0,1] is the segment parameter.
    """
    dx = b.x - a.x
    dy = b.y - a.y
    L2 = dx * dx + dy * dy
    if L2 < 1e-20:
        return Vector((a.x, a.y, 0.0)), 0.0
    t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / L2
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return Vector((a.x + t * dx, a.y + t * dy, 0.0)), t


def _bary_on_edge(edge_id: int, t: float) -> Optional[Tuple[float, float, float]]:
    """Bary (u, v, w) of the snapped point on the given edge.
    t is the segment parameter from `build_seam_segments_2d` (0 = first endpoint).
    """
    if edge_id == 0:   # edge BC, endpoints B(t=0) → C(t=1)
        return (0.0, 1.0 - t, t)
    if edge_id == 1:   # edge AC, endpoints A(t=0) → C(t=1)
        return (1.0 - t, 0.0, t)
    if edge_id == 2:   # edge AB, endpoints A(t=0) → B(t=1)
        return (1.0 - t, t, 0.0)
    return None


def align_boundary_to_seams(
    retopo_points_world: Sequence[Vector],
    existing_attachments: Sequence[Attachment],
    existing_boundary: Sequence[bool],
    triangles_2d_world: Sequence[Triangle],
    seam_edge_mask: Sequence[Tuple[bool, bool, bool]],
    threshold: float,
) -> Tuple[List[Vector], List[Attachment], List[bool], List[int]]:
    """Snap retopo verts within `threshold` (world units) of a Guide 2D seam
    edge onto that edge, and rebuild their Attachment so one bary component is
    exactly 0.  Flags each snapped vertex as boundary.

    Vertices outside the threshold are left untouched — their attachment and
    boundary flag pass through unchanged.

    Returns
    -------
    out_points       : world-space 2D positions (z preserved from input)
    out_attachments  : updated attachments (only differs for aligned verts)
    out_boundary     : boundary flag (OR'd with existing for aligned verts)
    aligned_indices  : indices of vertices that were actually snapped
    """
    n = len(retopo_points_world)
    out_points: List[Vector] = [p.copy() for p in retopo_points_world]
    out_attachments: List[Attachment] = list(existing_attachments)
    while len(out_attachments) < n:
        out_attachments.append(Attachment.none())
    out_boundary: List[bool] = list(existing_boundary)
    while len(out_boundary) < n:
        out_boundary.append(False)
    aligned_indices: List[int] = []

    segments = build_seam_segments_2d(triangles_2d_world, seam_edge_mask)
    seam_bvh = build_bvh_from_segments(segments)
    if seam_bvh is None:
        return out_points, out_attachments, out_boundary, aligned_indices

    thr_sq = threshold * threshold

    for i, p in enumerate(retopo_points_world):
        q = Vector((p.x, p.y, 0.0))
        _loc, _norm, seg_idx, _d = seam_bvh.find_nearest(q)
        if seg_idx is None or seg_idx < 0 or seg_idx >= len(segments):
            continue
        guide_tri, edge_id, a_seg, b_seg = segments[seg_idx]
        snapped, t = _closest_pt_on_segment_xy(q, a_seg, b_seg)
        dx = snapped.x - p.x
        dy = snapped.y - p.y
        if dx * dx + dy * dy > thr_sq:
            continue
        bary = _bary_on_edge(edge_id, t)
        if bary is None:
            continue
        # Preserve original z so the 2D plane height isn't disturbed (the layout
        # ShapeKey isn't necessarily exactly z=0 in world space).
        out_points[i] = Vector((snapped.x, snapped.y, p.z))
        out_attachments[i] = Attachment.ok(guide_tri, bary)
        out_boundary[i] = True
        aligned_indices.append(i)

    return out_points, out_attachments, out_boundary, aligned_indices


def save_boundary_to_mesh(mesh, is_boundary: Sequence[bool]) -> None:
    """Persist the per-vertex boundary flag as a uint8 (0/1) attribute.

    Stored as INT for consistency with ac9_status; we only need 0/1 values.
    """
    n = len(mesh.vertices)
    if len(is_boundary) != n:
        raise ValueError(
            f"is_boundary length {len(is_boundary)} != mesh vertex count {n}"
        )
    _ensure_attr(mesh, ATTR_IS_BOUNDARY, "INT")
    attr = mesh.attributes[ATTR_IS_BOUNDARY]
    attr.data.foreach_set("value", [1 if b else 0 for b in is_boundary])


def load_boundary_from_mesh(mesh) -> List[bool]:
    """Load the per-vertex boundary flag, or return all-False if missing."""
    n = len(mesh.vertices)
    attr = mesh.attributes.get(ATTR_IS_BOUNDARY)
    if attr is None or len(attr.data) != n:
        return [False] * n
    values = [0] * n
    attr.data.foreach_get("value", values)
    return [v != 0 for v in values]
