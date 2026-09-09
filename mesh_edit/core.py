"""Mesh Edit core — UV-preserving collapse.

Blender's native Collapse (Mesh > Merge > Collapse) welds each selected edge's
two verts to their midpoint but does NOT carry the per-loop UVs sensibly — the
UV corners collapse to one side, pinching/destroying the island. This rebuilds
the same geometry result while keeping UVs intact.

How UV is preserved
-------------------
A collapse merges, per edge, two vertices into one. The UV equivalent is: for
every face corner that survives around the merged vertex, set its UV to the
average of the corners that come together there — but ONLY across corners that
were UV-continuous (no seam between them). So:

  * a UV seam crossing the collapse edge  -> the two islands stay separate,
    each averaged to its own midpoint
  * no seam                               -> both sides meet at the midpoint

This is run independently per UV layer (the seam layout can differ per layer).
The geometry weld is done once at the end with bmesh.ops.weld_verts, which
already preserves all surviving loop data; our pre-pass only nudges the active
UVs to the midpoint so there is no half-edge UV discontinuity at the seam.

Verified headless on Adel_Retopo_16.blend (Body_all_retopo.002, 3 edges):
geometry delta matches native Collapse exactly (-3 verts / -4 faces) while the
total UV area is unchanged (native loses area = pinch). See the conversation
that introduced this module.
"""

import bmesh
from mathutils import Vector
from collections import defaultdict

# Two loops at one vertex count as the same UV vertex (no seam) when their UVs
# match within this tolerance. UV space is 0..1, so 1e-4 is sub-texel.
UV_EPS = 1e-4


def _components(sel_edges):
    """Union-find the selected verts into collapse groups (one weld per group).

    Mirrors native Collapse: each connected run of selected edges becomes a
    single vertex at the group's centroid.
    """
    parent = {}

    def find(x):
        r = x
        while parent.get(r, r) != r:
            r = parent[r]
        while parent.get(x, x) != x:
            parent[x], x = r, parent[x]
        return r

    def union(a, b):
        parent[find(a)] = find(b)

    vset = set()
    for e in sel_edges:
        union(e.verts[0], e.verts[1])
        vset.update(e.verts)

    comps = defaultdict(list)
    for v in vset:
        comps[find(v)].append(v)
    return list(comps.values()), vset


def _average_uv_layer(uv_layer, sel_edges, vset):
    """For one UV layer, average each UV-island's corners around the collapse.

    Builds a loop-level union-find: corners that are UV-continuous (2a) or that
    are the two endpoints of a collapsing edge within one face (2b) end up in
    the same group, then every group is set to its mean UV.
    """
    lp = {}

    def lfind(x):
        r = x
        while lp.get(r, r) != r:
            r = lp[r]
        while lp.get(x, x) != x:
            lp[x], x = r, lp[x]
        return r

    def lunion(a, b):
        lp[lfind(a)] = lfind(b)

    def loops_at(v, e):
        return [l for f in e.link_faces for l in f.loops if l.vert is v]

    # 2a) around each collapsing vertex, join loops that share an edge with no
    #     UV seam (their UVs already coincide).
    for v in vset:
        for e in v.link_edges:
            ls = loops_at(v, e)
            if len(ls) == 2 and (ls[0][uv_layer].uv - ls[1][uv_layer].uv).length < UV_EPS:
                lunion(ls[0], ls[1])

    # 2b) for each collapsing edge, join the two endpoint corners *within the
    #     same face* — they become one point after the weld. Done per face so a
    #     seam down the edge keeps the two faces' islands separate.
    for e in sel_edges:
        v1, v2 = e.verts
        for f in e.link_faces:
            l1 = next((l for l in f.loops if l.vert is v1), None)
            l2 = next((l for l in f.loops if l.vert is v2), None)
            if l1 and l2:
                lunion(l1, l2)

    groups = defaultdict(list)
    for v in vset:
        for l in v.link_loops:
            groups[lfind(l)].append(l)
    for g in groups.values():
        avg = sum((l[uv_layer].uv for l in g), Vector((0, 0))) / len(g)
        for l in g:
            l[uv_layer].uv = avg


def collapse_keep_uv(bm):
    """Collapse the selected edges of `bm` to their centroids, preserving UVs.

    Returns (n_groups, n_removed_verts). Caller is responsible for the
    bmesh.update_edit_mesh(loop_triangles=True) afterwards (geometry changes).
    """
    sel_edges = [e for e in bm.edges if e.select]
    if not sel_edges:
        return 0, 0

    comps, vset = _components(sel_edges)

    # Preserve every UV layer, not just the active one (this mesh family ships
    # UVMap + UVMap_nail; other loop data is preserved by weld_verts itself).
    for uv_layer in bm.loops.layers.uv.values():
        _average_uv_layer(uv_layer, sel_edges, vset)

    # Geometry: move keeper to centroid, weld the rest onto it. The keeper must
    # NOT appear as a key in targetmap — a self-map (keeper->keeper) makes
    # weld_verts treat it as a vert-to-remove and tears holes in the mesh.
    targetmap = {}
    n_removed = 0
    for vs in comps:
        center = sum((v.co for v in vs), Vector()) / len(vs)
        keeper = vs[0]
        keeper.co = center
        for v in vs:
            if v is not keeper:
                targetmap[v] = keeper
                n_removed += 1

    bmesh.ops.weld_verts(bm, targetmap=targetmap)
    return len(comps), n_removed
