"""UV island detection for the CLO Projector overlay.

Guide meshes are pre-triangulated (all-triangle, validated by validate_guide),
so polygon index == triangle index == attachment.triangle_index throughout.

We flood-fill polygon adjacency, stopping at seam edges (edge.use_seam), to
assign an island ID to every polygon.

Reads base mesh data directly — no depsgraph evaluation needed, since seam
edges and polygon connectivity are topology attributes that don't change with
ShapeKey values or modifiers.
"""

from typing import List


def detect_triangle_islands(guide_obj) -> List[int]:
    """Return island_id for each triangle of guide_obj.

    Returns a list of length == polygon count of the base mesh.
    Index i corresponds to triangle index i (same ordering as
    extract_triangles_basis / extract_triangles_flat).

    Island IDs are 0-based consecutive integers.
    If the mesh has no seam edges all polygons share island 0.
    Returns an empty list when the mesh has no polygons.
    """
    mesh = guide_obj.data

    n_polys = len(mesh.polygons)
    if n_polys == 0:
        return []

    # ── Seam edge set (always normalised: smaller vertex index first) ────────
    seam_keys: set = set()
    for e in mesh.edges:
        if e.use_seam:
            a, b = int(e.vertices[0]), int(e.vertices[1])
            seam_keys.add((a, b) if a < b else (b, a))

    # ── Edge → neighbour polygon map ─────────────────────────────────────────
    # polygon.edge_keys returns sorted (min, max) tuples per Blender convention.
    edge_polys: dict = {}
    for poly in mesh.polygons:
        for ek in poly.edge_keys:
            if ek not in edge_polys:
                edge_polys[ek] = []
            edge_polys[ek].append(poly.index)

    # ── Iterative flood-fill (DFS with explicit stack) ───────────────────────
    island_of: List[int] = [-1] * n_polys
    current_island = 0

    for seed in range(n_polys):
        if island_of[seed] != -1:
            continue
        stack = [seed]
        island_of[seed] = current_island
        while stack:
            curr = stack.pop()
            for ek in mesh.polygons[curr].edge_keys:
                # Normalise just in case (edge_keys should already be sorted).
                norm_ek = (ek[0], ek[1]) if ek[0] < ek[1] else (ek[1], ek[0])
                if norm_ek in seam_keys:
                    continue
                for nbr in edge_polys.get(ek, ()):
                    if island_of[nbr] == -1:
                        island_of[nbr] = current_island
                        stack.append(nbr)
        current_island += 1

    return island_of
