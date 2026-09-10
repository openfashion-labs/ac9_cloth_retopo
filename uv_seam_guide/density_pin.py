"""Density Pins — retopo vertices the user has decided Adjust Density must
never remove or reflow, even though nothing about their geometry marks them
as special.

The motivating case (2026-09-02 design discussion): a knife cut added to the
outline to follow a fabric wrinkle sits, topologically, in the middle of an
ordinary stretch between two anchors — indistinguishable from a plain
generated vertex once a face is attached to it. _span_chain's automatic
stub/spur detection (see span_density.py) still has to run and still has to
be trusted for the vertices the user never thought about (measured: 8 of 20
sewn pairs on a production file carried unintentional spurs), so it stays.
But automatic detection and an explicit pin answer two different questions
and neither substitutes for the other:

  * _span_chain asks "is this vertex actually ON the boundary chain, or is
    it a dead-end stub the assignment picked up by accident" — a geometric/
    topological test, correct for accidental leftovers.
  * A pin asks "did the user PUT this vertex here on purpose, and must it
    survive a density change" — impossible to derive from geometry once a
    face is attached (a cut origin on the outline has NO structural
    difference from a plain chain vertex at that point), the same reason
    topology_corner.py's flag exists rather than being detected outright.

So a pinned vertex is always INSIDE the chain (never excluded like a spur),
just protected from removal and from being slid off the exact spot the user
put it — see span_density.resample_groups' and _plan_span_edit's use of this
module's `pinned_indices`.

Deliberately a SEPARATE attribute from topology_corner.ATTR ("ac9_topo_
corner"), not a shared one: detect_from_guide(replace=True) clears every
existing corner flag before re-proposing (that is the corner workflow's
whole point — Detect Candidates starting fresh). A knife-cut pin riding on
the same flag would vanish the next time someone clicks Detect Candidates,
which has nothing to do with density edits at all. Adjust Density still
RESPECTS an existing topology_corner flag as a pin (a user-placed corner is
exactly the kind of "must not move" vertex this module protects) — see
pinned_indices below — it just never writes to that attribute itself.
"""

import bmesh

ATTR = "ac9_density_pin"


def layer(bm, create=True):
    lay = bm.verts.layers.int.get(ATTR)
    if lay is None and create:
        lay = bm.verts.layers.int.new(ATTR)
    return lay


def mark(bm, verts, value=1):
    """Flag (or unflag) the given BMVerts. Returns how many changed.

    Creating the layer has to happen BEFORE the vertices are looked at:
    adding a custom-data layer reallocates the vertex custom data and every
    BMVert reference already held goes stale (same measured ReferenceError
    as topology_corner.mark — "BMesh data of type BMVert has been removed"
    on the first Mark on a mesh that had no layer yet). So on that first
    call the vertices are re-fetched by index.
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


def clear(bm, verts):
    return mark(bm, verts, value=0)


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


# Same Edit Mode trap as topology_corner.py: the mesh datablock's attribute
# array is EMPTY while in Edit Mode (the live data lives in the edit bmesh,
# and update_from_editmode() does not fill it in) -- every reader below goes
# through the object and picks its source from the mode, exactly like
# topology_corner._read.

def _read(retopo):
    """Yield (world position,) for each pinned vertex, whatever the mode."""
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
    """How many pins the object carries, in either mode."""
    if hasattr(retopo, "attributes"):      # a Mesh was passed, not an Object
        attr = retopo.attributes.get(ATTR)
        return 0 if attr is None else sum(1 for d in attr.data if d.value)
    return len(_read(retopo))


def positions(retopo):
    """World-space positions of the pinned vertices, for the overlay."""
    return _read(retopo)


def selected_pins(bm):
    """The flagged vertices among the current selection."""
    lay = layer(bm, create=False)
    if lay is None:
        return []
    return [v for v in bm.verts if v.select and v[lay]]


def clear_new_vertex(bm, v):
    """Force both this module's own flag and topology_corner's OFF on a
    single freshly created vertex.

    A vertex bmesh.ops.subdivide_edges (or bisect_plane) creates on an
    existing edge INTERPOLATES its neighbours' custom-data layers onto it
    rather than defaulting to 0 -- including INT layers, which round an
    average up (measured 2026-09-02, span_density._insert_entry: a vertex
    inserted right next to a density-pinned vertex came back pinned too,
    turning one pin into two on the very next Step). A brand new vertex is
    by definition never something the user placed on purpose, so every
    resample path that creates one via a split-style bmesh op (not a bare
    bm.verts.new(), which zero-initialises custom data correctly on its
    own) must call this on it before moving on.
    """
    from . import topology_corner as tc
    for name in (ATTR, tc.ATTR):
        lay = bm.verts.layers.int.get(name)
        if lay is not None:
            v[lay] = 0


def pinned_indices_bm(bm):
    """Vertex indices this bmesh currently protects from Adjust Density —
    this module's own ac9_density_pin flag UNION topology_corner's
    ac9_topo_corner flag (see module docstring on why Adjust Density
    respects a corner without ever writing one).

    Reads the BMESH's own custom-data layers, not mesh.attributes — the
    Edit Mode trap topology_corner.py's module comment describes (the mesh
    datablock's attribute arrays read back EMPTY while in Edit Mode; the
    live data lives only in the edit bmesh) bit span_density.resample_
    groups directly the first time this was written: it reads retopo.data
    through bmesh.from_edit_mesh, so a mesh.attributes-based reader always
    saw zero pins, silently discarding every one the user had just marked
    in the same Edit Mode session (measured 2026-09-02, test_density_pin.py
    — a pin marked, then immediately used in the same session, vanished on
    the very next Count change). This is the only correct reader for a
    caller already holding a bmesh; pinned_indices below is for a caller
    that only has mesh data (Object Mode).

    Returns a plain set of ints, safe to hold across further bmesh edits
    (unlike a BMVert reference).
    """
    from . import topology_corner as tc
    out = set()
    for name in (ATTR, tc.ATTR):
        lay = bm.verts.layers.int.get(name)
        if lay is None:
            continue
        out.update(v.index for v in bm.verts if v[lay])
    return out


def pinned_indices(mesh):
    """Same as pinned_indices_bm, but for a caller that only has mesh data
    (Object Mode) rather than an open bmesh — reads mesh.attributes
    directly, which is empty (see pinned_indices_bm's docstring) while the
    object is in Edit Mode. Use pinned_indices_bm instead whenever a bmesh
    is already open (span_density.py always is).
    """
    from . import topology_corner as tc
    out = set()
    for name in (ATTR, tc.ATTR):
        attr = mesh.attributes.get(name)
        if attr is None:
            continue
        n = len(mesh.vertices)
        out.update(i for i, d in enumerate(attr.data) if d.value and i < n)
    return out
