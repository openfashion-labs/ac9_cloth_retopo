"""Topology Corners — the boundary vertices the user pins the edge flow to.

The final topology generator does not divide a panel by looking at its shape.
It divides it at CORNERS the user names, cuts the outline into segments between
them, and grids each patch. A corner in that sense is a decision, not a
measurement:

  * the right angle at the bottom of a hem IS one, and so is a collar point —
    those a curvature test finds
  * a point halfway down a side seam can be one too, because that is where the
    flow should change. Nothing about the geometry there says so

So detection can only ever propose. `find_corner_anchors` already measures the
first kind (77 of them on a production Guide, stable anywhere between 30 and
45 degrees), and this module lets that seed a set the user then edits.

The flag lives on the RETOPO mesh, in an integer attribute, so it survives
saving and reloading and travels with the mesh rather than with the session.

It is deliberately NOT the anchor set. Anchors are DERIVED: nothing stores
them, every run reads the Guide and works them out again. In practice that is
stable — the Guide's boundary does not change unless the pattern is re-imported
or re-flattened — but stable is not the same as fixed, and the anchor set has
already moved once under a rule change with the Guide untouched. A corner is
the user's decision and has to survive anything of the sort, so it is stored
rather than derived. The two are meant to be read together: anchors say where
the sewing divides the outline, corners say where the flow may turn.
"""

import math

import bmesh
from mathutils import kdtree

from . import anchor_segments as anch

ATTR = "ac9_topo_corner"


def layer(bm, create=True):
    lay = bm.verts.layers.int.get(ATTR)
    if lay is None and create:
        lay = bm.verts.layers.int.new(ATTR)
    return lay


def mark(bm, verts, value=1):
    """Flag (or unflag) the given BMVerts. Returns how many changed.

    Creating the layer has to happen BEFORE the vertices are looked at:
    adding a custom-data layer reallocates the vertex custom data and every
    BMVert reference already held goes stale (measured: ReferenceError
    "BMesh data of type BMVert has been removed" on the first Mark on a mesh
    that had no layer yet). So on that first call the vertices are re-fetched
    by index.
    """
    if layer(bm, create=False) is None:
        bm.verts.index_update()
        idx = [v.index for v in verts]
        layer(bm)
        bm.verts.ensure_lookup_table()
        verts = [bm.verts[i] for i in idx]
    lay = layer(bm)
    n = 0
    for v in verts:
        if v[lay] != value:
            v[lay] = value
            n += 1
    return n


def clear_all(bm):
    lay = layer(bm, create=False)
    if lay is None:
        return 0
    n = 0
    for v in bm.verts:
        if v[lay]:
            v[lay] = 0
            n += 1
    return n


# In Edit Mode the mesh datablock's attribute array is EMPTY — the live data
# lives in the edit bmesh, and update_from_editmode() does not fill it in
# (measured: the edit bmesh carried the flag on 82 verts while
# mesh.attributes[ATTR].data had length 0). Reading the datablock there is why
# marking a corner by hand appeared to do nothing: the overlay was handed an
# empty list and the report said "0 corner(s)". So every reader goes through
# the object and picks its source from the mode.

def _read(retopo):
    """Yield (world position,) for each flagged vertex, whatever the mode."""
    mat = retopo.matrix_world
    if retopo.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(retopo.data)
        lay = layer(bm, create=False)
        if lay is None:
            return []
        return [mat @ v.co for v in bm.verts if v[lay]]
    mesh = retopo.data
    attr = mesh.attributes.get(ATTR)
    if attr is None:
        return []
    n = len(mesh.vertices)
    return [mat @ mesh.vertices[i].co
            for i, d in enumerate(attr.data) if d.value and i < n]


def count(retopo):
    """How many corners the object carries, in either mode."""
    if hasattr(retopo, "attributes"):      # a Mesh was passed, not an Object
        attr = retopo.attributes.get(ATTR)
        return 0 if attr is None else sum(1 for d in attr.data if d.value)
    return len(_read(retopo))


def positions(retopo):
    """World-space positions of the flagged vertices, for the overlay."""
    return _read(retopo)


def selected_corners(bm):
    """The flagged vertices among the current selection."""
    lay = layer(bm, create=False)
    if lay is None:
        return []
    return [v for v in bm.verts if v.select and v[lay]]


def _flat_turn(vns, flat_co, v):
    """How far the outline turns at v, in the flat layout. 0 is straight."""
    nbs = vns.get(v)
    if not nbs or len(nbs) != 2:
        return None
    a = flat_co[nbs[0]] - flat_co[v]
    b = flat_co[nbs[1]] - flat_co[v]
    if a.length < 1e-9 or b.length < 1e-9:
        return None
    c = max(-1.0, min(1.0, a.normalized().dot(b.normalized())))
    return 180.0 - math.degrees(math.acos(c))


def sharp_anchors(bvs, vns, parts, flat_co, angle_deg):
    """Anchors that also turn a corner — the ones find_corner_anchors drops.

    `find_corner_anchors` skips any vertex with two or more sewing partners,
    because for its own purpose (forced division points when generating the
    boundary) an anchor is already a division point and adding it again would
    be redundant. For a TOPOLOGY corner that exclusion is wrong, and it is
    wrong exactly where it matters most: measured on a production Guide, all
    thirteen sharp turns on each of the two largest panels sit on Guide
    vertices with three to five partners, so those panels came out with no
    corner at all while small panels carried seven or eight.

    Of the 141 Guide boundary vertices turning 45 degrees or more, 35 are free
    edge, 46 have one partner, and 60 have two or more. This function is those
    60 (those of them that are anchors), and adding them takes the proposal
    from 77 to 141 and the panels with nothing to propose from 3 to 0. It
    cannot affect boundary generation: nothing here writes to the anchor set.
    """
    if flat_co is None or angle_deg <= 0.0:
        return set()
    anchors = anch.find_anchors(bvs, vns, parts)
    return set(v for v in anchors
               if (_flat_turn(vns, flat_co, v) or 0.0) >= angle_deg)


def detect_from_guide(guide, retopo, flat_sk, angle_deg=45.0,
                      match_distance=0.0, tol=0.0005, replace=False,
                      include_junctions=True):
    """Propose corners from the Guide's pattern geometry.

    Reuses the same measurement the boundary generator pins its divisions with
    (`find_corner_anchors`), so a proposed corner is guaranteed to have a retopo
    vertex sitting on it — measured on a production file, 0 of 77 pinned
    corners were missing one. Anything that does not find its vertex is
    reported rather than silently dropped.

    replace=False adds to what the user already marked; True starts over.
    Returns (marked, missing, cleared, junctions) — the last being how many of
    the proposals came from a seam junction rather than a plain corner.
    """
    bvs, vns, parts = anch.find_vertex_partners(
        guide, match_distance=match_distance)
    flat_co = anch.get_flat_co(guide, flat_sk)
    corners = set(anch.find_corner_anchors(bvs, vns, parts, flat_co,
                                          angle_deg=angle_deg))
    junctions = set()
    if include_junctions:
        junctions = sharp_anchors(bvs, vns, parts, flat_co, angle_deg) - corners
        corners |= junctions

    mesh = retopo.data
    mat = retopo.matrix_world
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()

    cleared = clear_all(bm) if replace else 0
    lay = layer(bm)

    kd = kdtree.KDTree(len(bm.verts))
    for v in bm.verts:
        kd.insert(mat @ v.co, v.index)
    kd.balance()

    marked = 0
    missing = 0
    for gv in corners:
        hit = kd.find_range(flat_co[gv], tol)
        if not hit:
            missing += 1
            continue
        _co, idx, _d = min(hit, key=lambda h: h[2])
        v = bm.verts[idx]
        if not v[lay]:
            v[lay] = 1
            marked += 1

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return marked, missing, cleared, len(junctions)
