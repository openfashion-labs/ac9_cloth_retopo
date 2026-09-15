"""Close the 3D gap between the two sides of a sewn seam.

Finalize projects every retopo vertex onto the Guide independently. Two
vertices that sit on opposite sides of the same seam are the same physical
spot on the garment, but they travel through the projection separately and
land on separate points, so the deliverable ships a crack along every seam.

Measured on a production garment (Finalize's own projection, 318 paired seam
vertices): max 0.857 mm, mean 0.071 mm, 20.8% over 0.1 mm. The retopo is not
at fault — the Guide itself has it. CLO sews panel edges without making them
coincident: over 400 seam pairs of the same Guide the two sides sat up to
2.061 mm apart (mean 0.941 mm). The retopo faithfully reproduces that.

So the fix is to pull each matched pair onto one shared point. That is a pure
position edit — no vertex is created, removed or merged — so the Final keeps
its topology, its UV islands stay split, and a separate Merge by Distance can
still weld it later if that is wanted.

Only MATCHED pairs are touched: a vertex whose ghost is unplaced (red) has no
counterpart to be coincident with, and guessing one would drag it onto
whatever happens to be nearest. Those are reported instead.
"""

from mathutils import Vector
from mathutils.kdtree import KDTree

from . import ghost as _ghost


def find_matched_seam_pairs(guide, flat_sk, retopo, seam_pairs, *,
                            max_distance, bond_distance, progress=None):
    """Pair up retopo vertices that sit on opposite sides of the same seam.

    guide / flat_sk  — the Guide and its flat ShapeKey name
    retopo           — the retopo Object (read in its flat 2D layout)
    seam_pairs       — the Guide's seam pair list (analysis.find_seam_pairs_flat)
    max_distance     — Max Seam Distance, already converted to flat units
    bond_distance    — Bond Distance, already converted to flat units
    progress         — optional callable taking a 0..1 fraction

    Returns (pairs, n_unmatched) where `pairs` is a list of (i, j) retopo
    vertex index tuples, i < j, each tuple appearing once. `n_unmatched` counts
    boundary vertices that got a sewn ghost with nobody on the other side —
    the red crosses, the holes the user still has to fill.

    The pairing runs entirely in the FLAT layout, because that is where the
    seam correspondence is defined. The correction it feeds is applied in 3D.
    """
    if not seam_pairs:
        return [], 0

    mesh = retopo.data
    mw = retopo.matrix_world
    flat = [mw @ v.co for v in mesh.vertices]
    if not flat:
        return [], 0

    # Every vertex is a candidate PARTNER, not just boundary ones: a pocket is
    # sewn into the body as an interior loop, so the vertex that answers a
    # ghost there is never on the open boundary. Same reasoning as
    # gpu_overlay._classify_ghosts_placed.
    kd_all = KDTree(len(flat))
    for i, q in enumerate(flat):
        kd_all.insert((q.x, q.y, 0.0), i)
    kd_all.balance()

    # Ghost sources are boundary vertices only — that is where seams live.
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(mesh)
    try:
        bm.edges.ensure_lookup_table()
        boundary = set()
        for e in bm.edges:
            if len(e.link_faces) <= 1:
                boundary.add(e.verts[0].index)
                boundary.add(e.verts[1].index)
    finally:
        bm.free()
    boundary = sorted(boundary)
    if not boundary:
        return [], 0

    kd_pairs, entries = _ghost.build_pair_kd(seam_pairs)

    # Collect CLAIMS first: "vertex i thinks vertex j is its counterpart".
    # A claim on its own is not a pair. Several vertices can land on the same
    # opposite point — doubled verts, or one side simply denser than the other
    # — and taking every claim at face value chains them into one group through
    # their shared partner. Measured on a production trouser panel: 223 groups
    # holding 512 vertices, one of which would have been dragged 30.561 mm to
    # its group's centroid. Vertices that far apart are not the same spot.
    claims = {}
    n_no_partner = 0
    total = len(boundary)
    for n, i in enumerate(boundary):
        co = flat[i]
        # Sewn ghosts only. A free edge's "partner" is the vertex's own foot on
        # the outline, so feeding it here would pair a vertex with itself.
        sewn = _ghost.find_all_opposite_points(
            co, seam_pairs, max_distance=max_distance,
            kd=kd_pairs, entries=entries,
        )
        for cand in sewn:
            op = cand["opposite_pt"]
            _loc, j, dist = kd_all.find((op.x, op.y, 0.0))
            if j is None or dist is None or dist > bond_distance or j == i:
                n_no_partner += 1
                continue
            claims.setdefault(i, set()).add(j)
        if progress is not None and total:
            progress((n + 1) / total)

    # A pair survives only when the claim is MUTUAL: i points at j AND j points
    # back at i. That is what "the same physical spot" means here, and it is
    # the rule that breaks the chains. At a genuine three-panel junction all
    # three vertices claim each other, so the triangle survives intact and the
    # group is correctly three wide. Where one side is denser, only the vertex
    # the partner also chose is kept; the extra one stays unpaired, which is
    # exactly what its red ghost already says.
    found = set()
    n_one_sided = 0
    for i, targets in claims.items():
        for j in targets:
            if i in claims.get(j, ()):
                found.add((i, j) if i < j else (j, i))
            else:
                n_one_sided += 1

    return sorted(found), n_no_partner + n_one_sided


def coincide(positions, pairs):
    """Move every paired position onto its group's centroid, in place.

    `positions` is a list of Vector (the projected 3D coordinates, one per
    retopo vertex); `pairs` is what find_matched_seam_pairs returned.

    Pairs are merged into GROUPS first. At a three-panel junction the same
    physical spot carries three vertices and they arrive here as three
    overlapping pairs; averaging each pair on its own would leave all three
    at different places, and in a different place depending on which pair was
    processed last. Union-find makes the result order-independent.

    Returns (n_groups, n_moved, max_shift) — max_shift being the largest
    distance any single vertex travelled, which is half the widest gap closed.
    """
    if not pairs:
        return 0, 0, 0.0

    parent = {}

    def find(x):
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for i, j in pairs:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    groups = {}
    for v in parent:
        groups.setdefault(find(v), []).append(v)

    n_moved = 0
    max_shift = 0.0
    for members in groups.values():
        if len(members) < 2:
            continue
        c = Vector((0.0, 0.0, 0.0))
        for v in members:
            c += positions[v]
        c /= len(members)
        for v in members:
            shift = (positions[v] - c).length
            if shift > 0.0:
                n_moved += 1
                if shift > max_shift:
                    max_shift = shift
            positions[v] = c.copy()

    return len([g for g in groups.values() if len(g) >= 2]), n_moved, max_shift


def weld(mesh, pairs):
    """Merge each group of coincident seam vertices into one vertex.

    Separate from `coincide` on purpose. Coinciding is a repair — it removes a
    crack the projection introduced and changes nothing else. Welding changes
    the topology, and the deliverable goes to baking next, where the two sides
    of a seam are wanted as separate vertices; re-splitting a welded mesh by UV
    island is work, while welding an un-welded one is one Merge by Distance.
    So this is the OFF-by-default half.

    Run it AFTER `coincide`, so the members of a group are exactly equal and
    the merge cannot pick up anything else.

    Returns the number of vertices removed.
    """
    if not pairs:
        return 0

    import bmesh
    bm = bmesh.new()
    bm.from_mesh(mesh)
    try:
        bm.verts.ensure_lookup_table()
        n_before = len(bm.verts)

        parent = {}

        def find(x):
            parent.setdefault(x, x)
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:
                parent[x], x = root, parent[x]
            return root

        for i, j in pairs:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        groups = {}
        for v in parent:
            groups.setdefault(find(v), []).append(v)

        # targetmap maps each vertex to the ONE it collapses into. A vertex
        # must never map to itself — bmesh.ops.weld_verts corrupts the mesh
        # when a target is also a key pointing at itself.
        targetmap = {}
        for members in groups.values():
            if len(members) < 2:
                continue
            keep = bm.verts[min(members)]
            for v in members:
                if v != min(members):
                    targetmap[bm.verts[v]] = keep
        if not targetmap:
            return 0

        bmesh.ops.weld_verts(bm, targetmap=targetmap)
        bm.to_mesh(mesh)
        mesh.update()
        return n_before - len(bm.verts)
    finally:
        bm.free()
