"""UV seam analysis — Flat-ShapeKey based.

The seam guide shares the CLO Projector Guide as its single source of truth:

  Basis ShapeKey  → 3D garment shape
  Flat ShapeKey   → 2D UV-flattened layout (FlattenUV normalised 0–1 m space,
                    the SAME space the retopo mesh is built in)

CLO's UV-flatten workflow physically *splits* the mesh at every seam, so each
seam edge becomes an open boundary edge.  The two sides of one seam are
coincident in 3D (equal Basis positions) but separated in the flat layout.
We pair boundary edges by their 3D Basis position and read each endpoint's
coordinate in **Flat-SK world space** — no UV map, no scale factor.  Those
coordinates share the retopo's coordinate space directly, which is what makes
the ghost distances meaningful.
"""

from mathutils import Vector


def _closest_point_on_segment_2d(p, a, b):
    """Closest point on segment AB to point P (2D vectors).

    Returns (closest_point, t, distance) where t is the parameter along AB.
    """
    ab = b - a
    denom = ab.dot(ab)
    if denom <= 1e-12:
        return a.copy(), 0.0, (p - a).length
    t = max(0.0, min(1.0, (p - a).dot(ab) / denom))
    q = a + ab * t
    return q, t, (p - q).length


def _flat_key(p, precision):
    return (round(p.x, precision), round(p.y, precision))


def find_boundary_segments_flat(guide_obj, flat_sk_name):
    """Return flat-world (p1, p2) for EVERY open-boundary edge of the guide.

    Unlike find_seam_pairs_flat — which keeps only edges that have a sewn
    partner on another panel (coincident in 3D) — this returns the full CLO
    pattern outline: sewn seams AND free edges (hems, necklines, armhole
    openings). UV Seam Snap uses this so retopo boundary verts can land on the
    whole outline, not just the sewn seams.

    Returns a list of (Vector, Vector) in Flat-SK world space.
    """
    import bmesh as _bmesh

    mesh = guide_obj.data
    matrix = guide_obj.matrix_world

    if mesh.shape_keys is None or flat_sk_name not in mesh.shape_keys.key_blocks:
        raise RuntimeError(
            f"Flat ShapeKey '{flat_sk_name}' not found on guide '{guide_obj.name}'."
        )
    flat = mesh.shape_keys.key_blocks[flat_sk_name]
    n = len(mesh.vertices)
    flat_co = [matrix @ flat.data[i].co for i in range(n)]

    bm = _bmesh.new()
    bm.from_mesh(mesh)
    bm.edges.ensure_lookup_table()
    segments = []
    for e in bm.edges:
        if e.is_boundary:
            i1 = e.verts[0].index
            i2 = e.verts[1].index
            segments.append((flat_co[i1].copy(), flat_co[i2].copy()))
    bm.free()
    return segments


def find_crease_segments_flat(guide_obj, flat_sk_name):
    """Flat-world (p1, p2) for every Guide edge tagged as a fold by CLO
    Cleanup's Find Folds / Tag Selected Edges (edge INT attribute
    'ac9_crease_kind' != 0) — the crease the Inset Line band was built on.

    Skips the tags an older Inset Line spread onto its band: an edge counts
    only if it is not itself an inset row ('ac9_inset_row') and neither end
    touches one, which leaves the band's middle row. Inset Line now re-tags
    the middle row alone, so on a freshly prepared Guide the filter changes
    nothing. Returns [] when the Guide carries no tags.
    """
    mesh = guide_obj.data
    n_e = len(mesh.edges)
    kind = mesh.attributes.get("ac9_crease_kind")
    if kind is None or kind.domain != 'EDGE' or len(kind.data) != n_e:
        return []
    kinds = [0] * n_e
    kind.data.foreach_get("value", kinds)
    rows = [0] * n_e
    row = mesh.attributes.get("ac9_inset_row")
    if row is not None and row.domain == 'EDGE' and len(row.data) == n_e:
        row.data.foreach_get("value", rows)
    ev = [0] * (2 * n_e)
    mesh.edges.foreach_get("vertices", ev)
    on_row = set()
    for i in range(n_e):
        if rows[i]:
            on_row.add(ev[2 * i])
            on_row.add(ev[2 * i + 1])
    if mesh.shape_keys is None or flat_sk_name not in mesh.shape_keys.key_blocks:
        raise RuntimeError(
            f"Flat ShapeKey '{flat_sk_name}' not found on guide '{guide_obj.name}'."
        )
    flat = mesh.shape_keys.key_blocks[flat_sk_name].data
    matrix = guide_obj.matrix_world
    segments = []
    for i in range(n_e):
        if not kinds[i] or rows[i]:
            continue
        a, b = ev[2 * i], ev[2 * i + 1]
        if a in on_row or b in on_row:
            continue
        segments.append((matrix @ flat[a].co, matrix @ flat[b].co))
    return segments


def find_seam_pairs_flat(guide_obj, flat_sk_name, precision_3d=4, precision_flat=4,
                         match_distance=0.0):
    """Find seam pairs on the CLO Projector Guide, endpoints in Flat-SK world space.

    Strategy (split-mesh, the CLO/MD case):
      1. Read Basis (3D) and the flat ShapeKey, both transformed by the guide's
         matrix_world so the result is in world space.
      2. Group open boundary edges by their rounded Basis *world* position.
      3. Any group with two distinct flat edges is a seam pair; store the two
         sides' endpoints as flat-space (world) Vectors.

    match_distance > 0 switches step 2 to KDTree proximity matching: two
    boundary edges pair when BOTH endpoints lie within match_distance of each
    other in Basis world space. This catches layered seams — a pocket sewn onto
    a body panel sits a few mm above it in 3D, so its seam verts are close but
    NOT coincident, and exact-position grouping misses them entirely.
    match_distance == 0 keeps the legacy exact (rounded) matching.

    Returns a list of dicts: {a_p1, a_p2, b_p1, b_p2}  (mathutils.Vector, world).
    The A and B sides are the two opposite seam edges; b is the mirror of a.
    """
    import bmesh as _bmesh

    mesh = guide_obj.data
    matrix = guide_obj.matrix_world

    if mesh.shape_keys is None or flat_sk_name not in mesh.shape_keys.key_blocks:
        raise RuntimeError(
            f"Flat ShapeKey '{flat_sk_name}' not found on guide '{guide_obj.name}'."
        )
    kb = mesh.shape_keys.key_blocks
    flat = kb[flat_sk_name]
    basis = kb["Basis"] if "Basis" in kb else None

    n = len(mesh.vertices)
    if basis is not None:
        basis_co = [matrix @ basis.data[i].co for i in range(n)]
    else:
        basis_co = [matrix @ mesh.vertices[i].co for i in range(n)]
    flat_co = [matrix @ flat.data[i].co for i in range(n)]

    bm = _bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    boundary_edges = []
    for e in bm.edges:
        if e.is_boundary:
            boundary_edges.append((e.verts[0].index, e.verts[1].index))
    bm.free()

    if match_distance and match_distance > 0.0:
        return _pairs_by_tolerance(
            boundary_edges, basis_co, flat_co, match_distance, precision_flat
        )

    def _pos_key(co):
        return (round(co.x, precision_3d),
                round(co.y, precision_3d),
                round(co.z, precision_3d))

    # Group boundary edges by their 3D Basis position signature.
    edges_by_3d: dict = {}
    for i1, i2 in boundary_edges:
        k1 = _pos_key(basis_co[i1])
        k2 = _pos_key(basis_co[i2])
        if k1 > k2:
            k1, k2 = k2, k1
            i1, i2 = i2, i1
        edges_by_3d.setdefault((k1, k2), []).append(
            (flat_co[i1].copy(), flat_co[i2].copy())
        )

    pairs = []
    for _pos, instances in edges_by_3d.items():
        if len(instances) < 2:
            continue
        # Deduplicate by flat coordinate (each side of the seam is distinct).
        seen = set()
        unique = []
        for p1, p2 in instances:
            k = (_flat_key(p1, precision_flat), _flat_key(p2, precision_flat))
            if k not in seen:
                seen.add(k)
                unique.append((p1, p2))
        if len(unique) < 2:
            continue
        for i in range(len(unique)):
            for j in range(i + 1, len(unique)):
                (a1, a2), (b1, b2) = unique[i], unique[j]
                pairs.append({
                    "a_p1": a1, "a_p2": a2,
                    "b_p1": b1, "b_p2": b2,
                })
    return pairs


def _pairs_by_tolerance(boundary_edges, basis_co, flat_co, dist, precision_flat):
    """Pair boundary edges whose Basis endpoints lie within `dist` of each other.

    Guards: edges sharing a mesh vertex never pair (consecutive outline edges);
    identical flat coordinates are duplicates of the same side; and the two
    sides must be farther than `dist` apart in FLAT space — the flat layout is
    near-isometric, so same-outline neighbours within `dist` in 3D are also
    within `dist` in flat, while true partners sit on another panel far away.
    That last guard is what stops a high-poly outline (edge length << dist)
    from chain-pairing with itself.

    Layered seams whose 3D gap varies beyond a usable `dist` (e.g. a pocket
    draped over the body) are NOT handled here — mark them with Mark Sharp and
    use find_marked_seam_pairs instead.
    """
    from mathutils import kdtree

    bnd_of_vert: dict = {}
    for ei, (i1, i2) in enumerate(boundary_edges):
        bnd_of_vert.setdefault(i1, []).append(ei)
        bnd_of_vert.setdefault(i2, []).append(ei)

    vert_ids = set(bnd_of_vert)
    kd = kdtree.KDTree(len(vert_ids))
    for i in vert_ids:
        kd.insert(basis_co[i], i)
    kd.balance()

    near_cache: dict = {}

    def _near(i):
        if i not in near_cache:
            near_cache[i] = {idx for (_co, idx, _d) in kd.find_range(basis_co[i], dist)}
        return near_cache[i]

    def _far_in_flat(i1, i2, j1, j2):
        ma = (flat_co[i1] + flat_co[i2]) * 0.5
        mb = (flat_co[j1] + flat_co[j2]) * 0.5
        return (ma - mb).length > dist

    pairs = []
    for ei, (i1, i2) in enumerate(boundary_edges):
        n1 = _near(i1)
        n2 = _near(i2)

        # ── boundary ↔ boundary: best partner per opposite side ──
        # On a high-poly guide (edge length << dist) one edge sees many
        # near-diagonal partners sliding along the same opposite outline.
        # Cluster accepted candidates by flat midpoint and keep the best
        # (smallest endpoint-distance sum) per cluster — one partner per
        # opposite panel, so N-way junctions still keep every side.
        candidates = set()
        for v in n1:
            candidates.update(bnd_of_vert.get(v, ()))
        accepted = []
        for ej in candidates:
            if ej <= ei:
                continue  # each unordered pair once
            j1, j2 = boundary_edges[ej]
            if i1 in (j1, j2) or i2 in (j1, j2):
                continue  # consecutive edges of the same outline
            # Both endpoints must match, in either orientation.
            if j1 in n1 and j2 in n2:
                pass
            elif j2 in n1 and j1 in n2:
                j1, j2 = j2, j1
            else:
                continue
            ka = {_flat_key(flat_co[i1], precision_flat),
                  _flat_key(flat_co[i2], precision_flat)}
            kb = {_flat_key(flat_co[j1], precision_flat),
                  _flat_key(flat_co[j2], precision_flat)}
            if ka == kb:
                continue  # same flat edge — a duplicate, not an opposite side
            if not _far_in_flat(i1, i2, j1, j2):
                continue  # same-outline neighbour, not an opposite side
            score = ((basis_co[i1] - basis_co[j1]).length
                     + (basis_co[i2] - basis_co[j2]).length)
            accepted.append((score, j1, j2))
        accepted.sort(key=lambda t: t[0])
        kept_mids = []
        for _score, j1, j2 in accepted:
            mb = (flat_co[j1] + flat_co[j2]) * 0.5
            if any((mb - m).length < 2.0 * dist for m in kept_mids):
                continue  # same opposite side as an already-kept partner
            kept_mids.append(mb)
            pairs.append({
                "a_p1": flat_co[i1].copy(), "a_p2": flat_co[i2].copy(),
                "b_p1": flat_co[j1].copy(), "b_p2": flat_co[j2].copy(),
            })
    return pairs


def find_marked_seam_pairs(guide_obj, flat_sk_name, max_distance=0.02,
                           ignore_inset_rows=True):
    """Project Mark Sharp edges onto the nearest OTHER island and pair them.

    ignore_inset_rows: skip the sharp edges CLO Cleanup's insets built (edge
    attribute ac9_inset_row): the inner row of Inset Pieces runs 1 mm inside
    every outline and the outer rows of an Inset Line band flank every fold,
    all marked Sharp so their normals stay put. Read as marked seams they
    draw a second outline 1 mm inside the real one and project every fold
    band onto the neighbouring panels — measured on an inset jacket: 7429
    sharp edges, 7418 of them inset rows, 11 the user's.

    The explicit route for layered seams: geometric inference cannot reliably
    detect a pocket sewn onto a body panel (the 3D gap varies with drape from
    ~0 to >1 cm, so any distance threshold either misses edges or sprays false
    pairs elsewhere). Instead the user marks the pocket outline with
    Mark Sharp on the GUIDE mesh; each marked edge's endpoints are projected
    (BVH nearest-surface, within max_distance) onto the closest island other
    than their own, and the hit is mapped Basis→Flat through the hit
    triangle's barycentric coordinates. This works even when the partner panel
    has no vertex line along the seam, and produces zero false positives.

    Returns a list of pair dicts in the same {a_p1, a_p2, b_p1, b_p2} format
    as find_seam_pairs_flat (a = marked edge, b = projected partner), so the
    result feeds the same overlay / ghost / snap pipeline.
    """
    from mathutils.bvhtree import BVHTree
    from mathutils.geometry import barycentric_transform

    mesh = guide_obj.data
    matrix = guide_obj.matrix_world

    # ALL sharp edges — boundary AND interior. Interior marks are a feature:
    # the user can draw an arbitrary line on one panel and have it projected
    # onto nearby panels. The flip side: importers commonly tag interior
    # crease lines Sharp (this jacket came in with 278), and those project
    # too — clear unwanted ones on the guide (select the loop → Clear Sharp).
    row_attr = mesh.attributes.get("ac9_inset_row") if ignore_inset_rows else None
    if row_attr is not None and (row_attr.domain != 'EDGE' or row_attr.data_type != 'INT'):
        row_attr = None
    sharp_edges = [
        (int(e.vertices[0]), int(e.vertices[1]))
        for e in mesh.edges
        if e.use_edge_sharp and not (row_attr is not None and row_attr.data[e.index].value)
    ]
    if not sharp_edges:
        return []

    if mesh.shape_keys is None or flat_sk_name not in mesh.shape_keys.key_blocks:
        raise RuntimeError(
            f"Flat ShapeKey '{flat_sk_name}' not found on guide '{guide_obj.name}'."
        )
    kb = mesh.shape_keys.key_blocks
    flat = kb[flat_sk_name]
    basis = kb.get("Basis")

    n = len(mesh.vertices)
    if basis is not None:
        basis_co = [matrix @ basis.data[i].co for i in range(n)]
    else:
        basis_co = [matrix @ mesh.vertices[i].co for i in range(n)]
    flat_co = [matrix @ flat.data[i].co for i in range(n)]

    # ── Vertex-connected islands (union-find over triangle vertices) ──
    mesh.calc_loop_triangles()
    tris = mesh.loop_triangles

    parent = list(range(n))

    def _find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for t in tris:
        v0, v1, v2 = t.vertices
        r0 = _find(v0)
        r1 = _find(v1)
        if r1 != r0:
            parent[r1] = r0
        r2 = _find(v2)
        r0 = _find(v0)
        if r2 != r0:
            parent[r2] = r0

    # ── One BVH per island over Basis-space triangles ──
    tris_by_island: dict = {}
    for ti, t in enumerate(tris):
        tris_by_island.setdefault(_find(t.vertices[0]), []).append(ti)

    bvhs: dict = {}
    remaps: dict = {}
    for isl, tri_ids in tris_by_island.items():
        verts = []
        faces = []
        for local, ti in enumerate(tri_ids):
            v0, v1, v2 = tris[ti].vertices
            base = local * 3
            verts.append(basis_co[v0])
            verts.append(basis_co[v1])
            verts.append(basis_co[v2])
            faces.append((base, base + 1, base + 2))
        bvhs[isl] = BVHTree.FromPolygons(verts, faces, all_triangles=True)
        remaps[isl] = tri_ids

    # ── Project each marked vertex onto EVERY other island in range ──
    # Not just the nearest: layered stacks (body / pocket / flap) put several
    # panels within max_distance of one marked edge — the pocket's top edge is
    # closer to the flap hovering above it than to the body behind it, yet the
    # user wants the line on BOTH. Marking is explicit, so projecting to every
    # reachable island adds no false positives.
    marked_verts = {v for e in sharp_edges for v in e}
    proj: dict = {}   # vert index -> {island: flat-space Vector}
    for v in marked_verts:
        own = _find(v)
        p = basis_co[v]
        per_island: dict = {}
        for isl, bvh in bvhs.items():
            if isl == own:
                continue
            loc, _normal, local_idx, _d = bvh.find_nearest(p, max_distance)
            if loc is None:
                continue
            ti = remaps[isl][local_idx]
            v0, v1, v2 = tris[ti].vertices
            per_island[isl] = barycentric_transform(
                loc,
                basis_co[v0], basis_co[v1], basis_co[v2],
                flat_co[v0], flat_co[v1], flat_co[v2],
            )
        if per_island:
            proj[v] = per_island

    pairs = []
    for a, b in sharp_edges:
        pa = proj.get(a)
        pb = proj.get(b)
        if pa is None or pb is None:
            continue
        for isl in pa.keys() & pb.keys():
            pairs.append({
                "a_p1": flat_co[a].copy(), "a_p2": flat_co[b].copy(),
                "b_p1": pa[isl].copy(), "b_p2": pb[isl].copy(),
            })
    return pairs


def _compute_islands(mesh):
    """Union-find over mesh edges. Returns {root: [vertex indices]}.

    Shared by detect_island_symmetry and detect_mirror_pairs — both need the
    same "one UV island == one connected component" grouping (CLO's
    FlattenUV physically splits the mesh at every seam).
    """
    from collections import defaultdict

    n = len(mesh.vertices)
    parent = list(range(n))

    def _find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for e in mesh.edges:
        ra, rb = _find(int(e.vertices[0])), _find(int(e.vertices[1]))
        if ra != rb:
            parent[ra] = rb
    islands = defaultdict(list)
    for i in range(n):
        islands[_find(i)].append(i)
    return islands


def _compute_boundary_mask(mesh):
    """Bool array (len == vertex count): True for verts on an open-boundary
    edge (used by exactly one polygon). Shared boundary-vertex test.
    """
    from collections import defaultdict

    import numpy as np

    n = len(mesh.vertices)
    edge_face_count = defaultdict(int)
    for poly in mesh.polygons:
        for ek in poly.edge_keys:
            edge_face_count[ek] += 1
    boundary = np.zeros(n, dtype=bool)
    for ek, cnt in edge_face_count.items():
        if cnt == 1:
            boundary[ek[0]] = True
            boundary[ek[1]] = True
    return boundary


def _compute_boundary_edges(mesh):
    """List of (v0, v1) for every open-boundary edge (used by exactly one
    polygon) — the outline as SEGMENTS rather than as loose points.

    detect_mirror_pairs measures point-to-segment distance against these, so
    that two panels of the same shape score the same however differently
    their outlines happen to be subdivided.
    """
    from collections import defaultdict

    edge_face_count = defaultdict(int)
    for poly in mesh.polygons:
        for ek in poly.edge_keys:
            edge_face_count[ek] += 1
    return [ek for ek, cnt in edge_face_count.items() if cnt == 1]


def _compute_boundary_mask_and_edges(mesh):
    """(boundary_mask, boundary_edges) in ONE pass over mesh.polygons.

    Same results as calling _compute_boundary_mask then _compute_boundary_
    edges, but without building edge_face_count twice. detect_mirror_pairs
    always wants both; measured on a production Guide (154k polygons), the
    two separate passes cost ~309ms + ~307ms — nearly a quarter of the whole
    function — for identical work done twice. detect_island_symmetry also
    wants both now (the edges feed its point-to-segment axis refinement) —
    kept separate from the two single-purpose functions above only because
    some other callers still want the mask alone.
    """
    from collections import defaultdict

    import numpy as np

    n = len(mesh.vertices)
    edge_face_count = defaultdict(int)
    for poly in mesh.polygons:
        for ek in poly.edge_keys:
            edge_face_count[ek] += 1
    boundary = np.zeros(n, dtype=bool)
    edges = []
    for ek, cnt in edge_face_count.items():
        if cnt == 1:
            boundary[ek[0]] = True
            boundary[ek[1]] = True
            edges.append(ek)
    return boundary, edges


# ---------------------------------------------------------------------------
# Caches — detect_mirror_pairs and the island grouping it depends on are
# expensive on a real drape mesh (measured: 2.4s / 0.2s on a 79k-vertex,
# 154k-polygon production Guide) but the GUIDE rarely changes during a
# retopo session, so recomputing them on every Phase 2 Replace click (which
# only ever touches the RETOPO mesh) was pure waste — measured cost of the
# actual mesh edit + 3D projection for a Replace: under 50ms combined.
# ---------------------------------------------------------------------------

_island_cache = {}
_mirror_pair_cache = {}


def _island_cache_key(mesh):
    """Topology-only — no matrix_world, no tolerance. Connected components
    depend on nothing else, so the key itself works for a Guide mesh or a
    Retopo mesh alike.

    DO NOT call compute_islands_cached on a Retopo mesh that Phase 2 island
    replacement can touch. This key only invalidates when vertex/edge/
    polygon COUNTS change, and replace_retopo_island deletes N and creates
    N back — same counts, different vertices, cache returns the stale
    islands dict. Measured: caused a second consecutive Replace to look up
    an island root that no longer existed. Safe for a Guide mesh (which
    Phase 2 never edits) or any Retopo use where you are certain no
    count-preserving edit happened in between; when in doubt, call
    analysis._compute_islands directly — a Retopo-sized mesh costs under
    1ms uncached anyway (measured: 0.5ms / 604 verts), so there is nothing
    to gain from caching it there, only this to lose.
    """
    return (mesh.session_uid, len(mesh.vertices), len(mesh.edges), len(mesh.polygons))


def compute_islands_cached(mesh):
    """Cached _compute_islands — see _island_cache_key's docstring for the
    one case (a Retopo mesh Phase 2 can replace islands on) where this is
    unsafe to use. Returns the SAME dict object on a repeat call with an
    unchanged mesh — callers must not mutate it.
    """
    key = _island_cache_key(mesh)
    islands = _island_cache.get(key)
    if islands is None:
        islands = _compute_islands(mesh)
        _island_cache[key] = islands
        if len(_island_cache) > 16:
            _island_cache.clear()  # simple bound; a session rarely juggles this many
    return islands


def _mirror_pair_cache_key(guide_obj, flat_sk_name, tolerance, min_boundary_verts,
                           count_ratio_slack, descriptor_gate, rotation_coarse,
                           refine_deg, outline_samples):
    mesh = guide_obj.data
    # world-space matters here (unlike the island cache above): the flat
    # coordinates detect_mirror_pairs scores are matrix_world @ local co, so
    # moving/rotating/scaling the Guide object changes centroid_a/centroid_b
    # (and therefore where Phase 2 places new vertices) even though the mesh
    # data itself, and so session_uid/V/E/F, are untouched.
    mat = tuple(v for row in guide_obj.matrix_world for v in row)
    return (mesh.session_uid, flat_sk_name, len(mesh.vertices), len(mesh.edges),
            len(mesh.polygons), mat, float(tolerance), min_boundary_verts,
            count_ratio_slack, descriptor_gate, rotation_coarse, refine_deg,
            outline_samples)


def invalidate_mirror_pair_cache(guide_obj=None):
    """Drop cached detect_mirror_pairs_cached results. Pass a Guide to drop
    only its entries (matched by mesh session_uid); omit to clear
    everything — used on file load, where session_uids from the previous
    file mean nothing.
    """
    if guide_obj is None:
        _mirror_pair_cache.clear()
        return
    uid = guide_obj.data.session_uid
    for k in [k for k in _mirror_pair_cache if k[0] == uid]:
        del _mirror_pair_cache[k]


def detect_mirror_pairs_cached(guide_obj, flat_sk_name, tolerance=0.02,
                               min_boundary_verts=6, count_ratio_slack=0.2,
                               descriptor_gate=0.06, rotation_coarse=72,
                               refine_deg=5.0, outline_samples=240, force=False,
                               progress=None):
    """Cache-aware wrapper around detect_mirror_pairs.

    The cache key (_mirror_pair_cache_key) covers everything the computation
    depends on: mesh identity + vertex/edge/polygon counts + matrix_world +
    every tuning parameter. A real topology edit, a moved/rotated/scaled
    Guide object, or a different tolerance all auto-invalidate. What it does
    NOT catch is a vertex moved WITHOUT changing the counts above (e.g. a
    stray drag on a Guide vertex) — force=True is the escape hatch for that,
    and is what the "Detect Mirror Pairs" button always passes, so a manual
    re-detect is never stale.

    Returns are the SAME objects held in the cache, not a copy — do not
    mutate pairs / rejections / any pair's verts_a / verts_b / centroid_a /
    centroid_b in place. Nothing in this codebase currently does; keep it
    that way, or copy first.
    """
    key = _mirror_pair_cache_key(guide_obj, flat_sk_name, tolerance,
                                 min_boundary_verts, count_ratio_slack,
                                 descriptor_gate, rotation_coarse, refine_deg,
                                 outline_samples)
    if not force:
        cached = _mirror_pair_cache.get(key)
        if cached is not None:
            return cached
    result = detect_mirror_pairs(
        guide_obj, flat_sk_name, tolerance=tolerance,
        min_boundary_verts=min_boundary_verts,
        count_ratio_slack=count_ratio_slack, descriptor_gate=descriptor_gate,
        rotation_coarse=rotation_coarse, refine_deg=refine_deg,
        outline_samples=outline_samples, progress=progress,
    )
    _mirror_pair_cache[key] = result
    if len(_mirror_pair_cache) > 8:
        # Keep this Guide's family, drop anything from a different Guide /
        # a previous data-block generation of the same object.
        keep_uid = key[0]
        for k in [k for k in _mirror_pair_cache if k[0] != keep_uid]:
            del _mirror_pair_cache[k]
    return result


# Axis-offset refinement (detect_island_symmetry). The centroid/nearest-
# vertex approach these replace is biased by how densely each stretch of an
# island's boundary happens to be subdivided — measured on a production
# garment Guide, a 10mm+ axis-position error on an island whose two sides
# were digitised at different densities (mean segment 0.57mm one side, 0.66mm
# the other). _SYM_VERDICT_MULT converts the user-facing `tolerance` (still
# the nearest-vertex-RMS scale from the old metric, unchanged for anyone with
# an existing value dialled in) into the tighter point-to-segment scale real
# folds cluster under — measured: every genuine fold landed under 0.4x
# tolerance, the nearest false positive at ~2.8x.
_SYM_VERDICT_MULT = 0.4
_SYM_SCAN_HALF_WIN = 0.003
_SYM_SCAN_STEP = 0.001
_SYM_SCAN_MAX_PTS = 200
_SYM_GOLDEN_MAX_PTS = 2000
_SYM_GOLDEN_BRACKET = 0.001
_SYM_GOLDEN_TOL = 5e-6
_SYM_GOLDEN_MAX_ITER = 25
_SYM_EXPAND_HALF_WIN = 0.015
_SYM_EDGE_MARGIN = 0.0005


def detect_island_symmetry(guide_obj, flat_sk_name, tolerance=0.015,
                           min_boundary_verts=6, progress=None):
    """Find bilaterally-symmetric (cut-on-fold) UV islands; return their fold lines.

    UV islands are the guide mesh's connected components — CLO's FlattenUV
    physically splits the mesh at every seam, so one component == one UV island.
    For each island we take its OUTLINE (open-boundary) vertices in Flat-SK
    world 2D and test bilateral symmetry across the X axis and the Y axis
    ONLY (through the island's own centroid, refined — see below) — EITHER
    axis under `tolerance` becomes a fold (centre) line — a doubly-symmetric
    panel (square body, waistband) yields both.

    `progress`, if given, is called with a 0..1 fraction as this walks the
    islands (measured 2.4-4s on a production Guide). Defaults to None.

    The axis position along the tested direction is not just the raw
    centroid: it is refined by minimising the point-to-SEGMENT (not nearest-
    vertex) reflection RMS, searched from a window bracketing the plain
    vertex-mean and an arc-length-weighted boundary mean (a coarse 1mm scan
    for a starting point, then golden-section search to ~5 micron). Point-to-
    segment rather than point-to-vertex is what makes this subdivision-
    invariant (see _outline_distance_sq's docstring) — a plain centroid or a
    nearest-vertex RMS both get pulled toward whichever side of the outline
    happens to carry more vertices, which is exactly what produced the 10mm
    error above. The verdict itself uses this same refined RMS (see
    _SYM_VERDICT_MULT above), not a separate nearest-vertex measurement —
    keeping position and pass/fail on one metric matters: a production
    island passed the OLD nearest-vertex verdict at 0.01424 against a 0.015
    tolerance while sitting on a 10mm-wrong axis; measuring both from the
    same refined optimum could not produce that split.

    Why only X and Y, not an arbitrary angle
    -----------------------------------------
    An earlier version swept all 180 degrees (90 coarse steps + local
    refinement) and kept every local RMS minimum under tolerance, on the
    theory that CLO could lay a symmetric panel out at any rotation. Measured
    against a production jacket Guide (18 islands): every one of the 12
    islands that passed did so with its best angle at EXACTLY 0.0 or 90.0
    degrees — not close to axis-aligned, bit-identical to it — and testing
    X/Y alone reproduced the full sweep's pass/fail verdict on all 18 islands
    with zero misses and zero false positives. The sweep wasn't buying any
    correctness on real data, only ~45x the cost (18 islands x up to 400
    points x 90 angles x local refinement, vs. 2 angles x the full point set
    with no subsampling needed once the angle search is gone).

    The sweep also wasn't a pure safety net: on the 6 islands that failed,
    its best-fit angle often landed at a tilted, non-axis value (e.g. 14 or
    84 degrees) that was NOT a real fold line, just the least-bad local
    minimum — exactly the "visibly tilted fold" failure mode a past version
    of this function was rewritten to fix by moving off PCA. A rotated
    symmetric panel would still go undetected by X/Y-only (untested on real
    data — CLO's FlattenUV convention for this is unverified); the
    conservative response is a diagnostic re-run of the full sweep
    (_dev_tests/diag_missing_fold_line.py) if that's ever suspected, not
    carrying the sweep's cost and its false-positive risk on every call.

    Boundary-only is essential and was confirmed empirically: a filled point
    cloud is so dense that reflected points always land near *some* vertex, so
    every island reads as symmetric (density, not shape). The outline is the
    only reliable discriminator. A self-symmetric panel (back body, waistband,
    placket, cuff) shows one axis ~0.00-0.01 while the other is high; a true
    left/right-mirror pair (sleeve, front opening) fails both — those are
    detect_mirror_pairs' job, not this self-symmetry pass.

    Returns (segments, n_tested, n_symmetric, folds):
      segments    — list of (Vector, Vector) fold lines in Flat-SK world space,
                    clipped to each symmetric island's extent (Z = 0; the flat
                    layout is planar and the overlay forces its own Z anyway).
      n_tested    — islands large enough to test
      n_symmetric — islands that passed the tolerance
      folds       — [{"root", "a", "b", "rms"}] per symmetric axis — "a"/"b"
                    are the same points as the matching `segments` entry,
                    plus the island's root, for a caller (Symmetrize) that
                    needs to know WHICH island each fold line belongs to and
                    re-derive the axis as a line through "a"/"b" (NOT world
                    x=0 or y=0 — the axis passes through the island's own
                    refined centre, which is wherever CLO happened to pack
                    that panel in the layout). "rms" is the normalised point-
                    to-segment reflection RMS at the refined optimum (the
                    same value the verdict was judged on), for a caller that
                    wants to distinguish a crisp fold from one that only just
                    cleared the bar.
    """
    import numpy as np
    from mathutils import Vector

    mesh = guide_obj.data
    matrix = guide_obj.matrix_world
    if mesh.shape_keys is None or flat_sk_name not in mesh.shape_keys.key_blocks:
        raise RuntimeError(
            f"Flat ShapeKey '{flat_sk_name}' not found on guide '{guide_obj.name}'."
        )
    flat = mesh.shape_keys.key_blocks[flat_sk_name]
    n = len(mesh.vertices)
    if n == 0:
        return [], 0, 0, []

    fco = np.empty((n, 2), dtype=np.float64)
    for i in range(n):
        co = matrix @ flat.data[i].co
        fco[i, 0] = co.x
        fco[i, 1] = co.y

    # ── UV islands = connected components over mesh edges (union-find) ──
    islands = compute_islands_cached(mesh)

    # ── Boundary verts + edges: endpoints/edges used by exactly one polygon ──
    boundary, bedges = _compute_boundary_mask_and_edges(mesh)

    # Group boundary edges by island ONCE rather than re-scanning the whole
    # mesh's edge list per island (O(edges) instead of O(edges x islands)).
    # Measured on a production Guide (6321 boundary edges, 17 tested
    # islands): this grouping pass itself costs ~25ms either way and made no
    # measurable difference to the function's total time (3955ms before this
    # grouping was added, 3982ms after) — the per-island rescan was cheap
    # enough at this island count not to matter. Kept anyway as a bound
    # against a Guide with many more islands, where repeating an O(edges)
    # scan per island would not stay cheap. A boundary edge's two ends are
    # always in the same island (it belongs to exactly one polygon, which
    # cannot straddle a connected-component boundary), so keying off either
    # endpoint's root is exact.
    vert_root = {}
    for root, idxs in islands.items():
        for v in idxs:
            vert_root[v] = root
    edges_by_root = {}
    for a, b in bedges:
        edges_by_root.setdefault(vert_root[a], []).append((a, b))

    segments = []
    folds = []
    n_tested = 0
    n_symmetric = 0
    tick = progress if callable(progress) else None
    n_islands = len(islands)
    for isl_i, (root, idxs) in enumerate(islands.items()):
        if tick is not None and n_islands:
            tick(isl_i / n_islands)
        bidx = [i for i in idxs if boundary[i]]
        if len(bidx) < min_boundary_verts:
            continue
        seg_pairs = edges_by_root.get(root, ())
        if not seg_pairs:
            continue
        n_tested += 1
        pts = fco[bidx]
        seg_p = fco[[a for a, _b in seg_pairs]]
        seg_q = fco[[b for _a, b in seg_pairs]]
        seg_d = seg_q - seg_p
        seg_dd = np.maximum((seg_d ** 2).sum(axis=1), 1e-18)
        seg_len = np.sqrt(seg_dd)

        c = pts.mean(axis=0)
        q = pts - c
        bb = pts.max(axis=0) - pts.min(axis=0)
        diag = float(np.hypot(bb[0], bb[1])) or 1.0

        axes = []  # (angle, col, x_star, rms_norm)
        for angle, col in ((0.0, 1), (np.pi / 2.0, 0)):
            c1 = float(pts[:, col].mean())
            c2 = float(((seg_p[:, col] + seg_q[:, col]) * 0.5 * seg_len).sum()
                       / seg_len.sum())
            x_star, edge = _refine_axis_offset(pts, seg_p, seg_d, seg_dd,
                                               col, c1, c2)
            if edge:
                continue  # ran to the expanded window's own edge: not a fold
            rms_norm = _axis_reflect_rms(pts, seg_p, seg_d, seg_dd,
                                         x_star, col) / diag
            if rms_norm < _SYM_VERDICT_MULT * tolerance:
                axes.append((angle, col, x_star, rms_norm))

        if not axes:
            continue
        n_symmetric += 1  # island counts once, however many fold axes it has

        for angle, col, x_star, rms_norm in axes:
            fold = np.array([np.cos(angle), np.sin(angle)])
            c_refined = c.copy()
            c_refined[col] = x_star
            t = q @ fold
            a_pt = c_refined + fold * t.min()
            b_pt = c_refined + fold * t.max()
            av, bv = (Vector((a_pt[0], a_pt[1], 0.0)),
                     Vector((b_pt[0], b_pt[1], 0.0)))
            segments.append((av, bv))
            folds.append({"root": root, "a": av, "b": bv, "rms": rms_norm})

    if tick is not None:
        tick(1.0)
    return segments, n_tested, n_symmetric, folds


def fold_mirror_coverage(obj, vert_indices, fold_a, fold_b, eps=0.0005):
    """How much of `vert_indices` is ALREADY mirrored across a fold line.

    Returns the fraction (0.0-1.0) of those vertices whose reflection across
    the line through `fold_a`/`fold_b` lands within `eps` of some vertex of
    the same set. Distances are measured in the flat XY plane in world space
    (`obj.matrix_world @ v.co`, Z ignored) — the same space `fold_a`/`fold_b`
    and symmetrize_retopo_island live in, and the same default `eps`
    (0.5 mm) that function uses to classify axis vertices.

    This scores the RETOPO's topology, not the Guide's outline: a panel whose
    Guide is symmetric about two axes (a waistband: left-right AND top-bottom)
    gives detect_island_symmetry two fold axes, and only this tells them
    apart by which one the retopo has not been mirrored across yet — the one
    with the LOWER coverage is the one with work left to do. A vertex sitting
    on the axis reflects onto itself and so always counts as covered, which
    is what we want: a shared centre seam is symmetric by construction.

    Deliberately a fresh, standalone helper: the fold DETECTION maths
    (_axis_reflect_rms and friends) measures reflected boundary points
    against outline SEGMENTS, which says nothing about whether vertices pair
    up one-to-one.
    """
    from mathutils import Vector, kdtree

    idxs = list(vert_indices)
    if not idxs:
        return 0.0
    d = fold_b - fold_a
    if d.length < 1e-9:
        return 0.0
    d = d.normalized()
    normal = Vector((-d.y, d.x, 0.0))
    a = Vector((fold_a.x, fold_a.y, 0.0))

    matrix = obj.matrix_world
    verts = obj.data.vertices
    pts = []
    for i in idxs:
        co = matrix @ verts[i].co
        pts.append(Vector((co.x, co.y, 0.0)))

    kd = kdtree.KDTree(len(pts))
    for k, p in enumerate(pts):
        kd.insert(p, k)
    kd.balance()

    hit = 0
    for p in pts:
        s = (p - a).dot(normal)
        _co, _i, dist = kd.find(p - normal * (2.0 * s))
        if dist is not None and dist <= eps:
            hit += 1
    return hit / len(pts)


_symmetry_cache = {}


def invalidate_symmetry_cache(guide_obj=None):
    """Drop cached detect_island_symmetry_cached results. Pass a Guide to
    drop only its entries (matched by mesh session_uid); omit to clear
    everything — used on file load, where session_uids from the previous
    file mean nothing. Same contract as invalidate_mirror_pair_cache.
    """
    if guide_obj is None:
        _symmetry_cache.clear()
        return
    uid = guide_obj.data.session_uid
    for k in [k for k in _symmetry_cache if k[0] == uid]:
        del _symmetry_cache[k]


def _symmetry_cache_key(guide_obj, flat_sk_name, tolerance, min_boundary_verts):
    mesh = guide_obj.data
    # world-space matters (same reasoning as _mirror_pair_cache_key): the
    # flat coordinates this scores are matrix_world @ local co.
    mat = tuple(v for row in guide_obj.matrix_world for v in row)
    return (mesh.session_uid, flat_sk_name, len(mesh.vertices), len(mesh.edges),
            len(mesh.polygons), mat, float(tolerance), min_boundary_verts)


def symmetry_cache_has(guide_obj, flat_sk_name, tolerance=0.015,
                       min_boundary_verts=6):
    """True if detect_island_symmetry_cached would return an already-cached
    result WITHOUT computing anything.

    detect_island_symmetry_cached(force=False) still runs the full
    detect_island_symmetry (measured 2.4-4s on a production Guide) on a
    cache MISS — it is a "cache-aware wrapper", not a "never compute
    without being asked" one, which is the right default for a caller like
    Symmetrize Island that re-detects on every execute anyway. Adjust
    Density is different: it is a per-click density tool, not a one-off
    analysis button, so paying that cost silently on every first press
    (and again after any cache-invalidating edit) would surprise a user
    who never asked for fold-axis pinning at all. This lets such a caller
    check first and skip hard_us entirely (with a one-line report, per the
    "silent gap" lesson in B-3 of the boundary-generation handoff notes)
    rather than triggering the compute or duplicating detect_island_
    symmetry's cost itself.
    """
    key = _symmetry_cache_key(guide_obj, flat_sk_name, tolerance, min_boundary_verts)
    return key in _symmetry_cache


def detect_island_symmetry_cached(guide_obj, flat_sk_name, tolerance=0.015,
                                  min_boundary_verts=6, force=False, progress=None):
    """Cache-aware wrapper around detect_island_symmetry.

    Same caching contract as detect_mirror_pairs_cached (see its docstring
    for the full reasoning): the key covers mesh identity + V/E/F counts +
    matrix_world + tolerance, so a real topology edit or a moved/rotated/
    scaled Guide auto-invalidates. A vertex dragged WITHOUT changing those
    counts does NOT — force=True is the escape hatch, and is what a
    user-facing "Detect Symmetry"/"Analyze Guide" button should always
    pass, so a manual re-detect is never stale. An automated caller (e.g.
    Symmetrize Island, which re-detects on every execute purely to avoid a
    stale axis) should instead call with force=False and rely on the key —
    that's the whole point of this wrapper existing.

    Returns are the SAME objects held in the cache — do not mutate.
    """
    key = _symmetry_cache_key(guide_obj, flat_sk_name, tolerance, min_boundary_verts)
    if not force:
        cached = _symmetry_cache.get(key)
        if cached is not None:
            return cached
    result = detect_island_symmetry(
        guide_obj, flat_sk_name, tolerance=tolerance,
        min_boundary_verts=min_boundary_verts, progress=progress)
    _symmetry_cache[key] = result
    if len(_symmetry_cache) > 8:
        keep_uid = key[0]
        for k in [k for k in _symmetry_cache if k[0] != keep_uid]:
            del _symmetry_cache[k]
    return result


def _outline_distance_sq(pts, seg_p, seg_d, seg_dd):
    """Squared distance from each point in `pts` to the nearest SEGMENT of an
    outline, vectorised over both.

    pts    (m, 2) query points
    seg_p  (k, 2) segment start points
    seg_d  (k, 2) segment vectors (end - start)
    seg_dd (k,)   squared segment lengths, clamped away from zero

    Point-to-segment rather than point-to-point on purpose: nearest-VERTEX
    distance depends on how finely each outline happens to be subdivided, so
    an extra vertex along an otherwise identical edge shows up as shape
    mismatch. Since reconciling left/right panels whose subdivision has
    drifted apart is exactly what this feature is for, the metric must not
    penalise that. Distance to the outline POLYLINE is subdivision-invariant.

    Returns (m,) squared distances.
    """
    import numpy as np

    dx = pts[:, None, 0] - seg_p[None, :, 0]
    dy = pts[:, None, 1] - seg_p[None, :, 1]
    t = (dx * seg_d[None, :, 0] + dy * seg_d[None, :, 1]) / seg_dd[None, :]
    np.clip(t, 0.0, 1.0, out=t)
    cx = dx - t * seg_d[None, :, 0]
    cy = dy - t * seg_d[None, :, 1]
    return (cx * cx + cy * cy).min(axis=1)


def _outline_distance_sq_chunked(pts, seg_p, seg_d, seg_dd, chunk=256):
    """Same result as _outline_distance_sq, CHUNK query points at a time.

    detect_island_symmetry's axis-offset search (_refine_axis_offset) calls
    this once per RMS evaluation — dozens of times per fold axis — and the
    full (m, k) matrix _outline_distance_sq builds is thrown away immediately
    after the per-point min, so a production island (>1000 boundary points
    AND segments) means a >1M-entry temporary on every single evaluation.
    Chunking bounds that temporary to chunk x k regardless of m. Left as a
    separate function rather than changing _outline_distance_sq itself,
    which detect_mirror_pairs also calls and which is fine at its call
    volume (once per candidate pair, not dozens of times per search).
    """
    import numpy as np

    m = len(pts)
    out = np.empty(m)
    for s in range(0, m, chunk):
        blk = pts[s:s + chunk]
        dx = blk[:, None, 0] - seg_p[None, :, 0]
        dy = blk[:, None, 1] - seg_p[None, :, 1]
        t = (dx * seg_d[None, :, 0] + dy * seg_d[None, :, 1]) / seg_dd[None, :]
        np.clip(t, 0.0, 1.0, out=t)
        cx = dx - t * seg_d[None, :, 0]
        cy = dy - t * seg_d[None, :, 1]
        out[s:s + chunk] = (cx * cx + cy * cy).min(axis=1)
    return out


def _axis_reflect_rms(pts, seg_p, seg_d, seg_dd, coord, col):
    """Point-to-segment reflection RMS for a fold axis at `coord` along
    `col` (0=X, 1=Y) — reflect every point of `pts` across that axis-aligned
    line and measure its distance to the (unreflected) outline segments.
    Not normalised by island size; callers divide by diag themselves.
    """
    import numpy as np

    r = pts.copy()
    r[:, col] = 2.0 * coord - r[:, col]
    return float(np.sqrt(_outline_distance_sq_chunked(r, seg_p, seg_d, seg_dd).mean()))


def _refine_axis_offset(pts, seg_p, seg_d, seg_dd, col, c1, c2):
    """Offset along `col` that minimises the point-to-segment reflection RMS.

    `c1`/`c2` are two starting candidates (the plain vertex mean and an arc-
    length-weighted boundary mean) that bracket where the true axis is
    expected to sit — a coarse 1mm scan across [min(c1,c2)-3mm,
    max(c1,c2)+3mm] (point set subsampled to _SYM_SCAN_MAX_PTS for this pass
    only) finds a starting point, then golden-section search on the FULL
    point set (capped at _SYM_GOLDEN_MAX_PTS for pathologically dense
    islands) narrows it to ~5 micron.

    If the result sits within _SYM_EDGE_MARGIN of the scan window's own
    edge, the window was too narrow to contain the true minimum — retried
    ONCE with a wider window (_SYM_EXPAND_HALF_WIN around the c1/c2
    midpoint). Still on that wider window's edge afterward means this
    island's outline does not actually have a clean minimum near either
    candidate; the caller treats that as "not a fold" rather than reporting
    a number pinned to an arbitrary window boundary.

    Returns (x_star, edge) — edge True means the second case above: refusal,
    not just imprecision.
    """
    import numpy as np

    def _search_window(lo, hi):
        stride = max(1, len(pts) // _SYM_SCAN_MAX_PTS)
        spts = pts[::stride]
        xs = np.arange(lo, hi + 1e-12, _SYM_SCAN_STEP)
        vals = [_axis_reflect_rms(spts, seg_p, seg_d, seg_dd, float(x), col)
               for x in xs]
        x0 = float(xs[int(np.argmin(vals))])

        gpts = pts
        if len(gpts) > _SYM_GOLDEN_MAX_PTS:
            gstride = max(1, len(gpts) // _SYM_GOLDEN_MAX_PTS)
            gpts = gpts[::gstride]

        g = (np.sqrt(5.0) - 1.0) / 2.0
        a, b = x0 - _SYM_GOLDEN_BRACKET, x0 + _SYM_GOLDEN_BRACKET
        c = b - g * (b - a)
        d = a + g * (b - a)
        fc = _axis_reflect_rms(gpts, seg_p, seg_d, seg_dd, c, col)
        fd = _axis_reflect_rms(gpts, seg_p, seg_d, seg_dd, d, col)
        it = 0
        while (b - a) > _SYM_GOLDEN_TOL and it < _SYM_GOLDEN_MAX_ITER:
            it += 1
            if fc < fd:
                b, d, fd = d, c, fc
                c = b - g * (b - a)
                fc = _axis_reflect_rms(gpts, seg_p, seg_d, seg_dd, c, col)
            else:
                a, c, fc = c, d, fd
                d = a + g * (b - a)
                fd = _axis_reflect_rms(gpts, seg_p, seg_d, seg_dd, d, col)
        x_star = 0.5 * (a + b)
        on_edge = x_star < lo + _SYM_EDGE_MARGIN or x_star > hi - _SYM_EDGE_MARGIN
        return x_star, on_edge

    lo = min(c1, c2) - _SYM_SCAN_HALF_WIN
    hi = max(c1, c2) + _SYM_SCAN_HALF_WIN
    x_star, on_edge = _search_window(lo, hi)
    if not on_edge:
        return x_star, False

    mid = 0.5 * (c1 + c2)
    lo, hi = mid - _SYM_EXPAND_HALF_WIN, mid + _SYM_EXPAND_HALF_WIN
    x_star, on_edge = _search_window(lo, hi)
    return x_star, on_edge


def _mirror_align_rms(qa_query, qb_outline, scale, coarse=72, refine_deg=5.0,
                      reflect=True, tie_eps=1e-3):
    """Best RMS aligning outline A's points onto outline B's polyline in 2D.

    Both sides must already be centred on their own centroid. `qb_outline` is
    the (seg_p, seg_d, seg_dd) triple built by _outline_segments. `scale`
    normalises the result and must be rotation-invariant (see the size
    measure chosen in detect_mirror_pairs) or the returned figure would
    depend on how the panels happen to be oriented in the UV pack.

    When `reflect` is True a single fixed flip (x -> -x) is applied to A
    before the rotation sweep: every orientation-REVERSING isometry of the
    plane factors as one fixed reflection composed with a rotation, so
    sweeping theta over [0, 2pi) after that flip covers every possible mirror
    alignment, whatever angle the UV packer placed the two panels at. With
    `reflect` False the sweep covers the orientation-PRESERVING alignments
    instead, which is how a same-handed duplicate panel is told apart from a
    real mirror partner.

    EVERY local minimum of the sweep is refined and returned, not just the
    winner, because a panel that is itself close to symmetric aligns equally
    well at more than one angle — typically theta and theta+180. Outline shape
    cannot separate those: the two outlines superimpose either way. They are
    not interchangeable downstream though, since they induce different vertex
    correspondences (measured: picking the wrong one of a tied pair left a
    0.81 mm vertex residual on a 117 mm panel whose outline matched to 0.0000).
    The caller breaks such ties with information the outline does not carry —
    see _vertex_chamfer.

    Returns (best_rms / scale, best_theta_degrees, tied_thetas_degrees) where
    tied_thetas are every refined minimum within `tie_eps` of the best,
    best first.
    """
    import numpy as np

    a = qa_query.copy()
    if reflect:
        a[:, 0] *= -1.0
    seg_p, seg_d, seg_dd = qb_outline

    def _rms(theta):
        ct, st = np.cos(theta), np.sin(theta)
        posed = np.empty_like(a)
        posed[:, 0] = a[:, 0] * ct - a[:, 1] * st
        posed[:, 1] = a[:, 0] * st + a[:, 1] * ct
        return float(np.sqrt(_outline_distance_sq(posed, seg_p, seg_d, seg_dd).mean()))

    thetas = np.linspace(0.0, 2.0 * np.pi, coarse, endpoint=False)
    vals = [_rms(t) for t in thetas]
    step = np.radians(refine_deg) / 10.0

    refined = []
    for k in range(coarse):
        v = vals[k]
        if v > vals[(k - 1) % coarse] or v > vals[(k + 1) % coarse]:
            continue                      # not a local minimum (sweep is mod 2pi)
        t_k, r_k = float(thetas[k]), v
        for j in range(-10, 11):
            t = t_k + step * j
            r = _rms(t)
            if r < r_k:
                r_k, t_k = r, t
        refined.append((r_k, t_k))
    if not refined:                       # perfectly flat sweep — degenerate outline
        refined = [(float(vals[0]), float(thetas[0]))]

    refined.sort(key=lambda rt: rt[0])
    best_r, best_t = refined[0]
    inv_scale = 1.0 / (scale or 1.0)
    tied = [float(np.degrees(t) % 360.0)
            for r, t in refined if r * inv_scale <= best_r * inv_scale + tie_eps]
    return best_r * inv_scale, float(np.degrees(best_t) % 360.0), tied


def _vertex_chamfer(va, vb, theta_deg, reflect=True):
    """Symmetrised point-to-point RMS between two centred boundary-VERTEX sets
    under the alignment (optional flip, then rotate by theta_deg).

    Deliberately vertex-to-vertex, unlike the outline metric: it is used only
    to break ties between alignments that the outline scores equally, and what
    distinguishes them is precisely where the vertices land. A panel mirrored
    onto its partner has to line its vertices up, not just its silhouette.
    """
    import numpy as np

    a = va.copy()
    if reflect:
        a[:, 0] *= -1.0
    th = np.radians(theta_deg)
    ct, st = np.cos(th), np.sin(th)
    posed = np.empty_like(a)
    posed[:, 0] = a[:, 0] * ct - a[:, 1] * st
    posed[:, 1] = a[:, 0] * st + a[:, 1] * ct

    d2 = ((posed[:, None, 0] - vb[None, :, 0]) ** 2
          + (posed[:, None, 1] - vb[None, :, 1]) ** 2)
    fwd = float(np.sqrt(d2.min(axis=1).mean()))
    bwd = float(np.sqrt(d2.min(axis=0).mean()))
    return max(fwd, bwd)


def _outline_segments(coords2d, edges, centroid):
    """Build the (seg_p, seg_d, seg_dd) triple for one island's outline,
    expressed relative to `centroid`.
    """
    import numpy as np

    p = np.array([coords2d[a] for a, _b in edges], dtype=np.float64) - centroid
    q = np.array([coords2d[b] for _a, b in edges], dtype=np.float64) - centroid
    d = q - p
    dd = np.maximum((d * d).sum(axis=1), 1e-24)
    return p, d, dd


def _outline_centroid_and_samples(coords2d, edges, n_samples=240):
    """Return (centroid, samples) for one island's outline.

    Neither may be derived from the outline's VERTICES, because vertex-derived
    quantities are not invariant to how finely the outline happens to be
    subdivided: splitting one boundary edge in two does not change the shape
    at all, yet it moves a vertex-average centroid by roughly radius/N and
    reweights a vertex-quantile descriptor. Measured on a 12-vertex synthetic
    panel, that alone put a genuine mirror pair at RMS 0.0209 — outside a
    0.02 tolerance — purely because its partner carried one extra vertex.
    Reconciling panels whose subdivision has drifted apart is the point of
    this feature, so its shape measures have to ignore subdivision entirely.

    centroid is the EXACT length-weighted centroid of the polyline,
    sum(midpoint_i * len_i) / sum(len_i): subdivision-invariant, and
    independent of the order the segments arrive in. It has to be exact
    because it is the alignment anchor — estimating it from the samples
    instead left a residual of up to 1.6 mm on a 258 mm panel, since which
    points the sampling lands on does depend on segment order.

    samples are n_samples points spaced evenly by ARC LENGTH, used for the
    radial descriptor and as the query set. Their positions carry that same
    order dependence, but there it only shows up as O(1/n) jitter in
    statistics taken over them (measured: RMS 0.00010 vs 0.00011).

    The sampling walks the arc-length measure rather than the loop: cumulative
    segment lengths partition [0, L] whatever order the segments come in, so
    no boundary-loop traversal — and no assumption of a single closed loop —
    is needed. Panels with holes work unchanged.
    """
    import numpy as np

    p = np.array([coords2d[a] for a, _b in edges], dtype=np.float64)
    q = np.array([coords2d[b] for _a, b in edges], dtype=np.float64)
    d = q - p
    lengths = np.hypot(d[:, 0], d[:, 1])
    total = float(lengths.sum())
    if total <= 0.0 or n_samples < 1:
        return p.mean(axis=0), p.copy()

    centroid = ((p + q) * 0.5 * lengths[:, None]).sum(axis=0) / total

    cum = np.cumsum(lengths)
    seg_start = cum - lengths
    s = (np.arange(n_samples) + 0.5) * (total / n_samples)
    idx = np.clip(np.searchsorted(cum, s), 0, len(lengths) - 1)
    t = (s - seg_start[idx]) / np.maximum(lengths[idx], 1e-24)
    return centroid, p[idx] + t[:, None] * d[idx]

# How much better the rotation-only fit has to be than the reflected fit
# before a pair is called same-handed (a duplicated panel rather than a
# left/right reflection). A bare `rot_rms < rms` is meaningless on a panel
# whose own outline is symmetric — a rectangular band, a cuff, a strap —
# because a 180-degree rotation superimposes it just as well as a
# reflection does, so both residuals collapse onto the same numerical
# noise and the verdict flips seed to seed. Measured headless on synthetic
# bands (8x2 grid, +/-1e-4 jitter, 5 seeds each):
#
#   panel outline            rot_rms / rms      correct verdict
#   symmetric, mirrored       0.89 .. 1.38      not same-handed
#   symmetric, duplicated     0.94 .. 1.10      indistinguishable (same shape)
#   handed*, mirrored          125 .. 2960      not same-handed
#   handed*, duplicated      0.001 .. 0.009     same-handed
#   (* one corner chamfered by 1.25% of the panel length)
#
# Real duplicates sit two to three orders of magnitude below 1 and genuine
# reflections two to three above, so anything in the middle separates them;
# 0.5 keeps ~50x of margin on the side that matters (refusing a legitimate
# reflection is the failure the user actually hits). A truly handed panel
# duplicated without reflecting cannot reach here in the first place unless
# its reflected fit is still inside `tolerance`.
_SAME_HANDED_RATIO = 0.5

# ...and how bad the reflected fit has to be in ABSOLUTE terms (normalised
# RMS, same units as `tolerance`) before the ratio above is allowed to
# decide anything. The ratio alone still misfires when both fits are
# numerically exact: a panel duplicated with no coordinate drift at all
# scored rms 5.3e-9 against rot_rms 2.5e-16 headless — a ratio of 5e-8, yet
# both alignments are perfect and the two panels are therefore congruent
# either way, so refusing to mirror is wrong. Real handedness measured at
# rms 2.0e-3 for an asymmetry of 1.25% of the panel length, so this floor
# keeps ~20x of margin below the subtlest duplicate worth catching.
_SAME_HANDED_FLOOR = 1e-4


def detect_mirror_pairs(guide_obj, flat_sk_name, tolerance=0.02,
                        min_boundary_verts=6, count_ratio_slack=0.2,
                        descriptor_gate=0.06, rotation_coarse=72,
                        refine_deg=5.0, outline_samples=240, progress=None):
    """Find pairs of UV islands that are mirror images of each other — a left
    sleeve island and a right sleeve island, say — as opposed to
    detect_island_symmetry's single-island self-symmetry test (which finds a
    fold line WITHIN one island, like a back-body panel).

    `progress`, if given, is called with a 0..1 fraction as this walks the
    O(islands^2) candidate-pair sweep below. Defaults to None.

    Why this runs in Flat-SK 2D and NOT in 3D
    -----------------------------------------
    Left/right panels are mirror images **as flat sewing patterns**. They are
    NOT mirror images in 3D: the garment's 3D shape comes out of cloth
    simulation, and the two sides drape differently even when cut from
    identical pattern pieces. A 3D reflection test therefore measures drape
    asymmetry, not pattern symmetry.

    Measured on a production jacket guide (18 testable islands, 7 true pairs),
    normalised reflection RMS per pair:

        pair (verts)      2D (this function)    3D (earlier attempt)
        18174 / 18174           0.000000              0.00537
         8439 /  8421           0.000000              0.00536
         5102 /  5102           0.000000              0.00718
         2102 /  2102           0.000000              0.01111
          872 /   872           0.000000              0.02424  <- 3D missed it
         1130 /  1130           0.000000              0.01112
          463 /   461           0.000001              0.01091
        closest FALSE pair      0.044892              (mixed in with the above)

    In 2D every true pair lands at numerically zero while the nearest false
    candidate sits at 0.0449 — a separation of ~69000x, so the threshold is
    not a delicate setting; anything from ~1e-5 to ~0.04 returns the same
    seven pairs. The 3D formulation spread true pairs up to 0.024, i.e. into
    the same range as false candidates, and pushed the 872-vertex pair outside
    a 0.02 tolerance altogether even though the retopo for that panel had
    already been mirrored by hand (proof it is a true pair). This is the same
    reasoning detect_island_symmetry already follows: it tests outline SHAPE
    in Flat-SK 2D.

    No global mirror axis
    ---------------------
    A 3D garment has one mirror plane; a 2D pattern layout does not. Each pair
    is scored under its own best alignment, found by centring both outlines on
    their centroid and sweeping the rotation after one fixed flip. So there is
    no axis object to configure, and no need to exclude self-symmetric islands
    (with no shared axis, a fold-on-itself panel cannot crowd out a real
    partner the way it could in the 3D formulation — it simply fails to match
    any island whose shape differs).

    On the measured file the winning rotation was 0 degrees for all seven
    pairs — CLO's FlattenUV lays mirrored panels out as a plain x-flip with no
    relative rotation — but the sweep is kept so a packer that does rotate
    pieces still works.

    Resolving the orientation of a symmetric panel
    ---------------------------------------------
    A panel that is itself close to bilaterally symmetric (a waistband, cuff,
    strap, placket — common, and 2 of the 7 measured pairs) superimposes on
    its partner at more than one angle, typically theta and theta+180. Outline
    shape scores both as a perfect fit, yet they are different maps: theta+180
    swaps the panel end for end, so a retopo mirrored through it would land
    every vertex reversed along the panel while the silhouette still matched —
    a failure that looks right and only shows up later as broken seam vertex
    counts against the neighbouring panels. Measured on the 872-vertex pair:
    outline RMS 0.000000 either way, but vertex-to-vertex residual 0.8114 mm
    at theta+180 against 0.0001 mm at theta, on a 117 mm panel.

    So every tied alignment is kept and the choice is made on the boundary
    VERTICES (_vertex_chamfer) — the quantity that actually separates them.
    Where the vertex distribution is symmetric too, the alignments are
    genuinely equivalent and either answer is correct. A tie that persisted on
    the boundary vertices while differing in the interior would not be caught
    here; that is believed unlikely and has not been tested.

    Metric and pruning
    ------------------
    Every shape quantity treats the outline as a CURVE, never as a bag of
    vertices, so that a panel subdivided more finely than its partner scores
    the same: distance is point-to-SEGMENT against the partner's polyline
    (_outline_distance_sq), the alignment anchor is the exact length-weighted
    centroid, and the descriptor is taken over an arc-length resampling
    (_outline_centroid_and_samples). Getting any one of those wrong is enough
    to break it — a vertex-average centroid alone put a genuine synthetic pair
    at 0.0209, outside a 0.02 tolerance.

    Pruning runs in increasing cost before the sweep: boundary-vertex count
    ratio, size ratio, then a rotation- and reflection-invariant descriptor
    (quantiles of the centroid-to-outline radius over the arc-length samples,
    normalised by the size measure). On the measured file this removed 146 of
    153 candidate pairs and left exactly the 7 true ones, discarding no true
    pair. Count pruning is INTENTIONALLY loose (+/- `count_ratio_slack`,
    default 20%) rather than exact: reconciling islands whose vertex counts
    have drifted apart is the point of the replacement step downstream, so
    demanding an exact count here would make the very islands that need fixing
    undetectable. Each pair carries `topo_match` so a caller can tell an
    already-identical pair from one that needs reconciling.

    Returns (pairs, n_tested, rejections):
      pairs — list of dicts, one per mutual-best pair inside `tolerance`:
        root_a, root_b     island ids (union-find roots)
        verts_a, verts_b   vertex indices into guide_obj.data.vertices
        rms                symmetrised mirror RMS (max of both directions)
        rot_rms            best rotation-only RMS, no reflection
        same_handed        True when the rotation-only fit is clearly
                           better than the reflected one (rot_rms <
                           rms * _SAME_HANDED_RATIO), i.e. the two panels
                           look like duplicate copies of one piece rather
                           than a mirrored left/right pair — worth
                           surfacing, since mirroring one onto the other
                           would then be wrong. The margin is what makes
                           this mean anything on a self-symmetric panel;
                           see _SAME_HANDED_RATIO and _SAME_HANDED_FLOOR
        theta_deg          rotation applied after the flip, degrees
        centroid_a/_b      Flat-SK world centroids of the two outlines
        topo_match         vertex AND boundary counts are identical
      The transform taking a point on A to its place on B is
        p_b = R(theta_deg) @ diag(-1, 1) @ (p_a - centroid_a) + centroid_b
      i.e. exactly the glide reflection the island-replacement step needs, so
      it does not have to be fitted again there.

      n_tested — islands with at least `min_boundary_verts` boundary vertices.
      rejections — why an island ended up unpaired, for UI diagnostics:
        {root, reason} where reason is 'too_few_boundary_verts' (+ n_boundary)
        or 'no_mutual_match' (+ best_candidate_root, best_rms — None when
        nothing survived pruning at all).
    """
    import numpy as np
    from mathutils import Vector

    mesh = guide_obj.data
    matrix = guide_obj.matrix_world
    if mesh.shape_keys is None or flat_sk_name not in mesh.shape_keys.key_blocks:
        raise RuntimeError(
            f"Flat ShapeKey '{flat_sk_name}' not found on guide '{guide_obj.name}'."
        )
    flat = mesh.shape_keys.key_blocks[flat_sk_name]

    n = len(mesh.vertices)
    if n == 0:
        return [], 0, []

    fco = np.empty((n, 2), dtype=np.float64)
    for i in range(n):
        co = matrix @ flat.data[i].co
        fco[i, 0], fco[i, 1] = co.x, co.y

    islands = compute_islands_cached(mesh)
    boundary, boundary_edges = _compute_boundary_mask_and_edges(mesh)

    # Group outline segments by island (either endpoint identifies it).
    root_of_vert = {}
    for root, idxs in islands.items():
        for i in idxs:
            root_of_vert[i] = root
    edges_by_root = {}
    for a, b in boundary_edges:
        edges_by_root.setdefault(root_of_vert[a], []).append((a, b))

    entries = []
    rejections = []
    for root, idxs in islands.items():
        bidx = [i for i in idxs if boundary[i]]
        edges = edges_by_root.get(root, ())
        if len(bidx) < min_boundary_verts or not edges:
            if bidx:
                rejections.append({
                    "root": root, "reason": "too_few_boundary_verts",
                    "n_boundary": len(bidx),
                })
            continue
        # Shape quantities come from the outline as a CURVE — its exact
        # length-weighted centroid and an arc-length resampling — never from
        # its vertices; see _outline_centroid_and_samples for why.
        c, samples = _outline_centroid_and_samples(fco, edges, outline_samples)
        q = samples - c
        radius = np.hypot(q[:, 0], q[:, 1])
        # The size measure must also be ROTATION-INVARIANT. An axis-aligned
        # bbox diagonal is not: the same panel placed at 45 degrees reports a
        # diagonal ~1.3x larger, which made the ratio gate below reject
        # rotated partners outright — the pruning would have thrown away
        # exactly the pairs the rotation sweep exists to catch. Twice the
        # maximum centroid radius is rotation-invariant and stays numerically
        # close to the bbox diagonal (they are equal for a disc), so
        # tolerances keep the same magnitude. Taken over the outline VERTICES
        # here, not the samples: on straight segments the farthest point of
        # the polyline is always a vertex, so this is exact rather than
        # sampling-dependent, and inserting a vertex mid-edge cannot change it.
        vq = fco[bidx] - c
        scale = float(2.0 * np.hypot(vq[:, 0], vq[:, 1]).max()) or 1.0
        descriptor = np.quantile(radius, [0.1, 0.25, 0.5, 0.75, 0.9, 1.0]) / scale
        entries.append({
            "root": root, "idxs": idxs, "n_bnd": len(bidx), "n_verts": len(idxs),
            "qs": q, "vq": vq, "outline": _outline_segments(fco, edges, c),
            "scale": scale, "centroid": c, "descriptor": descriptor,
        })

    n_tested = len(entries)

    def _prune_ok(ea, eb):
        lo, hi = sorted((ea["n_bnd"], eb["n_bnd"]))
        if lo == 0 or hi > lo * (1.0 + count_ratio_slack):
            return False
        lo, hi = sorted((ea["scale"], eb["scale"]))
        if hi > lo * 1.15:
            return False
        gap = float(np.max(np.abs(ea["descriptor"] - eb["descriptor"])))
        return gap <= descriptor_gate

    best_for = {}   # root -> dict describing its best partner so far
    tick = progress if callable(progress) else None
    n_entries = len(entries)
    for i in range(n_entries):
        if tick is not None and n_entries:
            tick(i / n_entries)
        for j in range(i + 1, len(entries)):
            ea, eb = entries[i], entries[j]
            if not _prune_ok(ea, eb):
                continue
            scale = max(ea["scale"], eb["scale"])
            # Symmetrise: a one-directional distance under-penalises a small
            # outline that happens to sit on top of a larger one's polyline.
            r_ab, theta, tied = _mirror_align_rms(
                ea["qs"], eb["outline"], scale, rotation_coarse, refine_deg, True)
            r_ba, _, _ = _mirror_align_rms(
                eb["qs"], ea["outline"], scale, rotation_coarse, refine_deg, True)
            rms = max(r_ab, r_ba)
            if rms >= tolerance:
                continue
            # A near-symmetric panel's outline fits at several angles at once
            # (theta and theta+180 typically). Those give different vertex
            # correspondences, so settle it on the vertices.
            if len(tied) > 1:
                theta = min(tied, key=lambda t: _vertex_chamfer(
                    ea["vq"], eb["vq"], t, reflect=True))
            o_ab, _, _ = _mirror_align_rms(
                ea["qs"], eb["outline"], scale, rotation_coarse, refine_deg, False)
            o_ba, _, _ = _mirror_align_rms(
                eb["qs"], ea["outline"], scale, rotation_coarse, refine_deg, False)
            rot_rms = max(o_ab, o_ba)
            topo_match = (ea["n_bnd"] == eb["n_bnd"]
                          and ea["n_verts"] == eb["n_verts"])
            # The reverse (b->a) direction reuses the SAME theta, not
            # (360-theta)%360 — a reflection followed by a rotation and its
            # own inverse commute that way (F.R(-t) == R(t).F for a fixed
            # reflection F), confirmed numerically (round-trip error 2.8e-15
            # with the same theta vs 20.0 with 360-theta on random inputs).
            # Invisible on the one production file measured so far: all 7
            # pairs there have theta=0, and 360-0 == 0.
            for src, dst, th in ((ea, eb, theta), (eb, ea, theta)):
                cur = best_for.get(src["root"])
                if cur is None or rms < cur["rms"]:
                    best_for[src["root"]] = {
                        "other": dst["root"], "rms": rms, "rot_rms": rot_rms,
                        "theta_deg": th, "topo_match": topo_match,
                    }

    entry_by_root = {e["root"]: e for e in entries}
    pairs = []
    seen = set()
    for ra, info in best_for.items():
        rb = info["other"]
        if ra in seen or rb in seen:
            continue
        back = best_for.get(rb)
        if back is None or back["other"] != ra:
            continue    # not mutual-best — leave it for the pair that is
        seen.add(ra)
        seen.add(rb)
        ea, eb = entry_by_root[ra], entry_by_root[rb]
        pairs.append({
            "root_a": ra, "root_b": rb,
            "verts_a": ea["idxs"], "verts_b": eb["idxs"],
            "rms": info["rms"], "rot_rms": info["rot_rms"],
            "same_handed": (info["rms"] > _SAME_HANDED_FLOOR
                            and info["rot_rms"]
                            < info["rms"] * _SAME_HANDED_RATIO),
            "theta_deg": info["theta_deg"],
            "centroid_a": Vector((float(ea["centroid"][0]),
                                  float(ea["centroid"][1]), 0.0)),
            "centroid_b": Vector((float(eb["centroid"][0]),
                                  float(eb["centroid"][1]), 0.0)),
            "topo_match": info["topo_match"],
        })

    for e in entries:
        if e["root"] in seen:
            continue
        cand = best_for.get(e["root"])
        rejections.append({
            "root": e["root"], "reason": "no_mutual_match",
            "best_candidate_root": cand["other"] if cand else None,
            "best_rms": cand["rms"] if cand else None,
        })

    if tick is not None:
        tick(1.0)
    return pairs, n_tested, rejections
