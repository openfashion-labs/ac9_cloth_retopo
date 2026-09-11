"""3D Mirror — a read-only viewer object that shows the retopo's projected 3D
shape while the retopo itself stays flat (2D) and authoritative.

Why this exists
---------------
The old workflow morphed ONE object between 2D (Basis) and 3D (AC9_3D_Project
ShapeKey) by flipping the ShapeKey value, and let the user edit in either
state.  Editing in 3D forced the fragile reverse-projection path
(run_reverse_projection + new-vert spike repair) and you could never see the
2D layout and the 3D form at the same time.

The mirror model fixes both:
  * The retopo object is the single source of truth and is ONLY ever edited in
    2D.  Topology + position edits happen on the flat layout, where they are
    easy and lossless.
  * AC9_3D_Mirror is a separate, plain mesh object positioned on the garment.
    Its geometry is a pure function of (retopo 2D + Guide) — rebuilt from
    scratch every Refresh.  It is a VIEWER: any edit the user makes to it is
    silently overwritten on the next Refresh. Only its vertex *selection* and
    the Refresh-time ``ac9_src_2d`` snapshot are read back by the
    selection-correspondence overlay.

Topology correspondence
-----------------------
The ordinary mirror mesh is a 1:1 copy of the retopo topology. A reversible
subdivision preview is the explicit exception: original vertices keep their
indices and subdivided children follow them, while ``ac9_preview_levels`` on
the Mirror mesh records that state. The 2D->3D selection overlay is geometric
and therefore does not depend on either topology.

Two refresh paths
-----------------
* Object Mode: run_forward_projection writes the retopo's AC9_3D_Project
  ShapeKey + attachments (reused by the experimental reverse tools), and the
  mirror copies those coords.  Topology comes from retopo.data.polygons.
* Edit Mode (retopo active): reads live positions AND topology straight from
  the retopo's edit bmesh and projects them with core.compute_forward_world,
  writing ONLY the mirror.  The retopo mesh/ShapeKey are never touched in Edit
  Mode (doing so is what crashed the old Sync operators), so this is safe.

Both paths also copy the retopo's HIDDEN geometry (Edit Mode H / Alt H) onto
the mirror, one direction only — and so does the live sync that runs off the
depsgraph while either object is in Edit Mode.  See _read_hidden / sync_hidden.
"""

import bpy

from . import core
from .core import ProjectionResult
from .guide import extract_points_world, get_basis_local, validate_guide

try:
    import numpy as _np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


# Custom property on the mirror object pointing back to its retopo's name, and
# the object/mesh name suffix.  Name-based linking is re-established on every
# refresh, so a rename of the retopo is healed the next time Refresh is pressed.
MIRROR_PROP = "ac9_mirror_of"
MIRROR_SUFFIX = "_AC93DMirror"
PREVIEW_LEVELS_PROP = "ac9_preview_levels"
PREVIEW_DIRTY_PROP = "ac9_preview_dirty"
SRC_2D_ATTR = "ac9_src_2d"
SRC_2D_VALID_ATTR = "ac9_src_2d_valid"
SRC_2D_COUNT_PROP = "ac9_src_2d_count"


def preview_levels(mirror) -> int:
    """Explicit preview state; topology counts are deliberately irrelevant."""
    if mirror is None or mirror.type != "MESH":
        return 0
    return max(0, int(mirror.data.get(PREVIEW_LEVELS_PROP, 0)))


def preview_is_dirty(mirror) -> bool:
    return bool(mirror is not None and mirror.type == "MESH"
                and mirror.data.get(PREVIEW_DIRTY_PROP, False))


def mark_preview_dirty(retopo) -> bool:
    """Mark an existing subdiv preview stale. Return whether state changed."""
    mirror = find_mirror(retopo)
    if preview_levels(mirror) <= 0 or preview_is_dirty(mirror):
        return False
    mirror.data[PREVIEW_DIRTY_PROP] = True
    return True


def _set_preview_state(mirror, levels=0, dirty=False):
    mirror.data[PREVIEW_LEVELS_PROP] = max(0, int(levels))
    mirror.data[PREVIEW_DIRTY_PROP] = bool(dirty) if levels else False


def find_mirror(retopo):
    """Return the existing mirror object for *retopo*, or None.

    Matched by the MIRROR_PROP custom property rather than by name so the link
    survives a mirror rename.
    """
    if retopo is None:
        return None
    name = retopo.name
    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj.get(MIRROR_PROP) == name:
            return obj
    return None


def _connectivity(faces):
    """Order-independent connectivity signature: a frozenset of per-face
    frozensets of vertex indices.  Detects re-triangulation / edge-rotate /
    connect-vertex edits that change WHICH verts form a face WITHOUT changing
    the vert or face counts — the case a plain count check misses (it left the
    mirror showing stale faces).
    """
    return frozenset(frozenset(int(i) for i in f) for f in faces)


def _mesh_topology_matches(mesh, n_verts, faces) -> bool:
    """True if *mesh* has the same vert count, face count, AND connectivity as
    (*n_verts*, *faces*).  Connectivity — not just counts — so a constant-count
    topology change still forces a mirror rebuild.
    """
    if len(mesh.vertices) != n_verts or len(mesh.polygons) != len(faces):
        return False
    return _connectivity(p.vertices for p in mesh.polygons) == _connectivity(faces)


def _build_mirror_mesh(name, n_verts, faces):
    """Create a fresh plain mesh with *n_verts* verts and *faces* (each an index
    sequence; quads/ngons allowed).  Coordinates are placeholders; refresh
    writes the real 3D positions.  All polygons are flagged smooth so the
    viewer never looks faceted.
    """
    verts = [(0.0, 0.0, 0.0)] * n_verts
    me = bpy.data.meshes.new(name + MIRROR_SUFFIX)
    me.from_pydata(verts, [], [list(f) for f in faces])
    if len(me.polygons):
        me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    me.update()
    return me


def _ensure_mirror_topology(context, retopo, n_verts, faces):
    """Return a mirror object whose topology matches (*n_verts*, *faces*),
    creating or rebuilding it as needed.  Links it into the retopo's
    collection(s) and aligns its world matrix so retopo-local 3D coords land on
    the garment.
    """
    mirror = find_mirror(retopo)

    if mirror is None:
        me = _build_mirror_mesh(retopo.name, n_verts, faces)
        mirror = bpy.data.objects.new(retopo.name + MIRROR_SUFFIX, me)
        colls = retopo.users_collection or (context.scene.collection,)
        for coll in colls:
            coll.objects.link(mirror)
    elif not _mesh_topology_matches(mirror.data, n_verts, faces):
        old = mirror.data
        mirror.data = _build_mirror_mesh(retopo.name, n_verts, faces)
        if old.users == 0:
            bpy.data.meshes.remove(old)

    # (Re)establish the link + transform every refresh so a retopo rename or a
    # moved retopo is healed.
    mirror[MIRROR_PROP] = retopo.name
    mirror.matrix_world = retopo.matrix_world.copy()
    # Viewer hygiene: keep it out of renders and selectable for correspondence.
    # Guarded, because Object.hide_render / hide_select fire their RNA update
    # even when the value does not change, and that update tags a depsgraph
    # RELATIONS rebuild: every object in the scene is re-tagged and its GPU
    # batches are rebuilt.  Unguarded that happened on EVERY Refresh, so a
    # visible 172k-vertex Guide was re-uploaded each press (~0.6 ms headless,
    # seconds in the viewport).  Writing only on an actual change costs 0 tags.
    if mirror.hide_render is not True:
        mirror.hide_render = True
    if mirror.hide_select is not False:
        mirror.hide_select = False
    return mirror


def _write_coords_local(mirror, local_coords):
    """Write a list of mathutils.Vector (mirror-local) into the mirror verts."""
    n = len(mirror.data.vertices)
    if _HAS_NUMPY:
        co = _np.empty(n * 3, dtype=_np.float32)
        for i, c in enumerate(local_coords):
            co[3 * i] = c.x
            co[3 * i + 1] = c.y
            co[3 * i + 2] = c.z
        mirror.data.vertices.foreach_set("co", co)
    else:
        for i, c in enumerate(local_coords):
            mirror.data.vertices[i].co = c
    # loop_triangles=True is required after any vertex write so the C-side
    # cache stays valid — see reference_bmesh_update_edit_mesh_crash.
    mirror.data.update()


def _write_src2d_mesh(mirror, local_2d):
    """Write each Mirror vertex's generating Retopo-local 2D coordinate."""
    mesh = mirror.data
    attr = mesh.attributes.get(SRC_2D_ATTR)
    if attr is not None and (attr.data_type != "FLOAT_VECTOR"
                             or attr.domain != "POINT"):
        mesh.attributes.remove(attr)
        attr = None
    if attr is None:
        attr = mesh.attributes.new(SRC_2D_ATTR, "FLOAT_VECTOR", "POINT")
    n = len(mesh.vertices)
    if len(local_2d) != n:
        raise ValueError(
            f"{SRC_2D_ATTR}: {len(local_2d)} source points for {n} vertices"
        )
    if _HAS_NUMPY:
        values = _np.empty(n * 3, dtype=_np.float32)
        for index, co in enumerate(local_2d):
            values[3 * index:3 * index + 3] = (co.x, co.y, co.z)
        attr.data.foreach_set("vector", values)
    else:
        for item, co in zip(attr.data, local_2d):
            item.vector = co

    valid = mesh.attributes.get(SRC_2D_VALID_ATTR)
    if valid is not None and (valid.data_type != "INT"
                              or valid.domain != "POINT"):
        mesh.attributes.remove(valid)
        valid = None
    if valid is None:
        valid = mesh.attributes.new(SRC_2D_VALID_ATTR, "INT", "POINT")
    valid.data.foreach_set("value", [1] * n)
    mesh[SRC_2D_COUNT_PROP] = n
    mesh.update()


def _write_src2d_bmesh(bm, local_2d):
    """Edit-BMesh counterpart of :func:`_write_src2d_mesh`."""
    if len(local_2d) != len(bm.verts):
        raise ValueError(
            f"{SRC_2D_ATTR}: {len(local_2d)} source points for "
            f"{len(bm.verts)} edit vertices"
        )
    layer = bm.verts.layers.float_vector.get(SRC_2D_ATTR)
    if layer is None:
        layer = bm.verts.layers.float_vector.new(SRC_2D_ATTR)
    valid_layer = bm.verts.layers.int.get(SRC_2D_VALID_ATTR)
    if valid_layer is None:
        valid_layer = bm.verts.layers.int.new(SRC_2D_VALID_ATTR)
    for vert, co in zip(bm.verts, local_2d):
        vert[layer] = co
        vert[valid_layer] = 1


# ---------------------------------------------------------------------------
# Hidden geometry (Edit Mode H / Alt H) — copied from the retopo
# ---------------------------------------------------------------------------
# The retopo is the source of truth for what is hidden, exactly as it is for
# shape and topology: hide a few faces on the 2D layout to get them out of the
# way and the same faces go hidden on the mirror.  One direction only — hiding
# on the mirror (a viewer) never travels back; it is undone by the next sync.
#
# It happens live: handlers._deferred_hide_sync calls sync_hidden() a moment
# after any edit-mesh change, so H and Alt+H land without pressing anything.
# Refresh ends with the same call, which covers the Object-Mode case the live
# path deliberately skips.
#
# Blender draws geometry as hidden in EDIT Mode only, so this is visible while
# the mirror is in Edit Mode (the "both in Edit Mode" flow); in Object Mode the
# mirror keeps drawing the whole mesh, hide flags or not.
#
# Only the hide flags are copied — SELECTION is deliberately left alone.  An
# earlier version also deselected what it hid, which turned the live sync into
# a loop: Blender's own select flush re-selects a hidden face whose (visible)
# verts are selected, so every pass found a difference, wrote, re-fired the
# depsgraph and came straight back.  Blender tolerating hidden+selected is the
# answer to whether it matters.  It also keeps this module away from
# MeshEdge.select, which hard-crashes Blender 5.0.1 (access violation in
# rna_MeshEdge_select_set) on a from_pydata mesh — which every mirror is.


def _read_hidden(retopo):
    """Return the retopo's hidden geometry as
    ``(vert_indices, edge_keys, face_keys)``, or None when nothing is hidden.

    Vertices are keyed by INDEX — the mirror is a 1:1 vertex copy by
    construction.  Edges and faces are keyed by frozenset-of-vertex-indices
    instead, because their ORDER is not guaranteed to match: an existing mirror
    mesh is kept whenever its connectivity matches as a SET
    (_mesh_topology_matches), and its edges are never copied — they come into
    being implicitly from from_pydata / faces.new.

    Read from the live edit bmesh while the retopo is in Edit Mode, so an H
    pressed a second ago counts without leaving Edit Mode.
    """
    if retopo.mode == "EDIT":
        import bmesh
        bm = bmesh.from_edit_mesh(retopo.data)
        verts = {v.index for v in bm.verts if v.hide}
        edges = {frozenset((e.verts[0].index, e.verts[1].index))
                 for e in bm.edges if e.hide}
        faces = {frozenset(v.index for v in f.verts) for f in bm.faces if f.hide}
    else:
        me = retopo.data
        # The .hide_* attributes are created lazily, the first time something
        # is hidden.  None of them present means nothing ever was, which skips
        # the per-element scan on the overwhelmingly common Refresh.
        if all(me.attributes.get(name) is None
               for name in (".hide_vert", ".hide_edge", ".hide_poly")):
            return None
        hv = [False] * len(me.vertices)
        he = [False] * len(me.edges)
        hf = [False] * len(me.polygons)
        me.vertices.foreach_get("hide", hv)
        me.edges.foreach_get("hide", he)
        me.polygons.foreach_get("hide", hf)
        verts = {i for i, h in enumerate(hv) if h}
        edges = {frozenset(me.edges[i].vertices) for i, h in enumerate(he) if h}
        faces = {frozenset(me.polygons[i].vertices)
                 for i, h in enumerate(hf) if h}

    if not verts and not edges and not faces:
        return None
    return verts, edges, faces


def _apply_hidden_mesh(mirror, hidden):
    """Write *hidden* (from _read_hidden) onto the mirror's OBJECT-mode mesh.
    Returns True if anything actually changed.

    Nothing hidden on either side is the free path: it neither scans nor
    writes, so it does not create the .hide_* attributes on a mesh that never
    had them.
    """
    me = mirror.data
    nv, ne, nf = len(me.vertices), len(me.edges), len(me.polygons)
    if hidden is None:
        if all(me.attributes.get(name) is None
               for name in (".hide_vert", ".hide_edge", ".hide_poly")):
            return False
        vflags = [False] * nv
        eflags = [False] * ne
        fflags = [False] * nf
    else:
        hverts, hedges, hfaces = hidden
        vflags = [i in hverts for i in range(nv)]
        eflags = [frozenset(e.vertices) in hedges for e in me.edges]
        fflags = [frozenset(p.vertices) in hfaces for p in me.polygons]

    # Compare before writing: the live sync runs off the depsgraph, and a write
    # here re-fires it — so a no-op pass has to stay a no-op all the way down.
    cur_v, cur_e, cur_f = [False] * nv, [False] * ne, [False] * nf
    me.vertices.foreach_get("hide", cur_v)
    me.edges.foreach_get("hide", cur_e)
    me.polygons.foreach_get("hide", cur_f)
    if (cur_v, cur_e, cur_f) == (vflags, eflags, fflags):
        return False

    me.vertices.foreach_set("hide", vflags)
    me.edges.foreach_set("hide", eflags)
    me.polygons.foreach_set("hide", fflags)
    me.update()
    return True


def _apply_hidden_bmesh(bm, hidden):
    """Same, through the mirror's edit bmesh (the mirror is in Edit Mode).
    Returns True if anything actually changed — the caller only has to push an
    update_edit_mesh (and re-fire the depsgraph) when it did.

    Writes ``.hide`` directly rather than calling ``hide_set()``: hide_set
    flushes to neighbouring geometry (measured on 5.0: hiding a face also hides
    the edges only that face used), which would hide more than the retopo does.
    What is copied is a state Blender itself produced, so copying it element by
    element keeps it valid — mesh.validate() finds nothing to fix.
    """
    bm.verts.index_update()
    changed = False

    if hidden is None:
        for seq in (bm.verts, bm.edges, bm.faces):
            for elem in seq:
                if elem.hide:
                    elem.hide = False
                    changed = True
        return changed

    hverts, hedges, hfaces = hidden
    for i, v in enumerate(bm.verts):
        h = i in hverts
        if v.hide != h:
            v.hide = h
            changed = True
    for e in bm.edges:
        h = frozenset((e.verts[0].index, e.verts[1].index)) in hedges
        if e.hide != h:
            e.hide = h
            changed = True
    for f in bm.faces:
        h = frozenset(v.index for v in f.verts) in hfaces
        if f.hide != h:
            f.hide = h
            changed = True
    return changed


def sync_hidden(retopo, mirror=None):
    """Copy the retopo's hidden geometry onto its mirror right now, whichever
    mode each of them is in.  Returns True if the mirror changed.

    This is the live path (handlers._deferred_hide_sync, which runs a moment
    after any edit-mesh change) as well as the tail of a Refresh.  It writes
    nothing when the two already agree, which is what keeps the live path from
    feeding its own depsgraph updates back to itself.
    """
    if retopo is None or retopo.type != "MESH":
        return False
    if mirror is None:
        mirror = find_mirror(retopo)
    if mirror is None:
        return False
    # A subdiv preview has child topology with no parent-face mapping in v1.
    # Copying only index-compatible vertex flags would create the invalid
    # half-hidden state called out by the design review, so skip all domains.
    if preview_levels(mirror) > 0:
        return False

    hidden = _read_hidden(retopo)
    if mirror.mode != "EDIT":
        return _apply_hidden_mesh(mirror, hidden)

    import bmesh
    bm = bmesh.from_edit_mesh(mirror.data)
    if not _apply_hidden_bmesh(bm, hidden):
        return False
    # Flags only — no geometry was added or removed, so loop_triangles can stay
    # False here (the crash it guards against is a stale triangle cache after a
    # vert/face count change).
    bmesh.update_edit_mesh(mirror.data, loop_triangles=False, destructive=False)
    return True


# ---------------------------------------------------------------------------
# Object-Mode refresh (full forward projection, persists on the retopo)
# ---------------------------------------------------------------------------


def refresh_mirror(context, retopo, guide_obj, flat_sk, progress=None,
                   preview_levels_override=None):
    """Recompute the forward projection on *retopo*, then rebuild the mirror to
    match.  Returns (ProjectionResult, mirror_obj | None).  Object Mode only.

    Uses a full (non-incremental) forward recompute: it is BVH-cached and fast,
    and recomputing every vertex sidesteps the new-2D-vert attachment-inherit
    subtlety entirely (verts added in 2D Edit Mode just bind correctly here).

    `progress`, if given, is called with a 0..1 fraction (forwarded into
    run_forward_projection). Defaults to None (no-op).
    """
    result = core.run_forward_projection(
        context,
        retopo,
        guide_obj,
        flat_sk,
        overwrite_shapekey=True,
        clear_failed_group=True,
        select_failed=False,
        incremental=False,
        progress=progress,
    )
    if not result.success:
        return result, None

    old_mirror = find_mirror(retopo)
    levels = (preview_levels(old_mirror) if preview_levels_override is None
              else max(0, int(preview_levels_override)))

    if levels <= 0:
        n = len(retopo.data.vertices)
        faces = [tuple(p.vertices) for p in retopo.data.polygons]
        mirror = _ensure_mirror_topology(context, retopo, n, faces)
        sk = retopo.data.shape_keys.key_blocks[core.SHAPEKEY_NAME]
        _write_coords_local(mirror, [sk.data[i].co.copy() for i in range(n)])
        _write_src2d_mesh(mirror, get_basis_local(retopo))
        _set_preview_state(mirror, 0)
        _apply_hidden_mesh(mirror, _read_hidden(retopo))
        return result, mirror

    # Preview uses a disposable full copy and the exact destructive pipeline.
    # No mesh/cache survives this call except the one ordinary Mirror mesh.
    tmp_obj = retopo.copy()
    tmp_mesh = retopo.data.copy()
    tmp_obj.data = tmp_mesh
    tmp_obj.name = "AC9_SubdivPreview_Temp"
    tmp_mesh.name = "AC9_SubdivPreview_Temp"
    collection = (retopo.users_collection[0] if retopo.users_collection
                  else context.scene.collection)
    collection.objects.link(tmp_obj)
    try:
        from .operators import subdivide_and_project
        preview_result, _n_old, n_new, _snapped = subdivide_and_project(
            context, tmp_obj, guide_obj, flat_sk,
            levels=levels, snap_boundary=True, snap_distance=0.02,
        )
        if not preview_result.success:
            return preview_result, old_mirror
        faces = [tuple(p.vertices) for p in tmp_mesh.polygons]
        mirror = _ensure_mirror_topology(context, retopo, n_new, faces)
        sk = tmp_mesh.shape_keys.key_blocks[core.SHAPEKEY_NAME]
        _write_coords_local(mirror, [sk.data[i].co.copy() for i in range(n_new)])
        _write_src2d_mesh(mirror, get_basis_local(tmp_obj))
        _set_preview_state(mirror, levels)
        # Hidden propagation is intentionally skipped for preview topology.
        return preview_result, mirror
    finally:
        if tmp_obj.name in bpy.data.objects:
            bpy.data.objects.remove(tmp_obj, do_unlink=True)
        if tmp_mesh.name in bpy.data.meshes and tmp_mesh.users == 0:
            bpy.data.meshes.remove(tmp_mesh)


# ---------------------------------------------------------------------------
# Edit-Mode refresh (live read from the edit bmesh, writes ONLY the mirror)
# ---------------------------------------------------------------------------


def _read_retopo_geometry(retopo):
    """Return (points_world, faces) for the retopo, reading the LIVE edit-mesh
    when it is in Edit Mode (so unsaved edits are reflected) and the committed
    mesh otherwise.  faces are index sequences (quads/ngons preserved).
    """
    if retopo.mode == "EDIT":
        import bmesh
        bm = bmesh.from_edit_mesh(retopo.data)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        matrix = retopo.matrix_world
        points_world = [matrix @ v.co for v in bm.verts]
        faces = [[v.index for v in f.verts] for f in bm.faces]
        return points_world, faces
    points_world = extract_points_world(retopo)
    faces = [tuple(p.vertices) for p in retopo.data.polygons]
    return points_world, faces


def _write_mirror_editmode(mirror, new_world, src2d_local, faces, hidden):
    """Write 3D coords (and topology) into the mirror while it is itself in
    Edit Mode.

    Edit-mode meshes are driven by their edit bmesh, not mesh.vertices, so we
    must write the bmesh.  When vert + face counts already match we only update
    coordinates (preserving the user's mirror selection — the whole point of
    keeping both objects in Edit Mode).  When the topology changed (a cut / new
    verts on the retopo), we rebuild the bmesh geometry — verts AND faces — to
    match (selection is necessarily reset).
    """
    import bmesh
    bm = bmesh.from_edit_mesh(mirror.data)
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    inv = mirror.matrix_world.inverted()
    n = len(new_world)

    # Coord-only fast path requires identical CONNECTIVITY, not just counts —
    # an edge-rotate / connect-vertex edit keeps counts but changes faces, and
    # would otherwise leave the mirror showing the old faces.
    topo_same = (
        len(bm.verts) == n
        and len(bm.faces) == len(faces)
        and _connectivity([v.index for v in f.verts] for f in bm.faces)
        == _connectivity(faces)
    )
    if topo_same:
        for i in range(n):
            bm.verts[i].co = inv @ new_world[i]
        _write_src2d_bmesh(bm, src2d_local)
        mirror.data[SRC_2D_COUNT_PROP] = n
        _apply_hidden_bmesh(bm, hidden)
        # Recompute normals: moving every vert from the flat layout onto the 3D
        # surface leaves the cached edit-mode normals stale, so the mirror draws
        # solid-black until Edit Mode is left. normal_update() fixes it live.
        bm.normal_update()
        bmesh.update_edit_mesh(mirror.data, loop_triangles=True, destructive=False)
        return

    # Topology changed while the mirror is in Edit Mode — rebuild verts + faces
    # to match the retopo's live topology read by the caller.
    bm.clear()
    vlist = [bm.verts.new(inv @ p) for p in new_world]
    bm.verts.ensure_lookup_table()
    for f in faces:
        try:
            face = bm.faces.new([vlist[idx] for idx in f])
            face.smooth = True
        except (ValueError, IndexError):
            # Duplicate/degenerate face — skip it rather than abort the refresh.
            pass
    _write_src2d_bmesh(bm, src2d_local)
    mirror.data[SRC_2D_COUNT_PROP] = n
    _apply_hidden_bmesh(bm, hidden)
    bm.normal_update()
    bmesh.update_edit_mesh(mirror.data, loop_triangles=True, destructive=True)


def refresh_mirror_editmode(context, retopo, guide_obj, flat_sk, progress=None):
    """Refresh while one or both of {retopo, mirror} are in Edit Mode — the
    "both in Edit Mode" flow: click on the 3D mirror, edit on the 2D retopo,
    press Refresh, all without ever leaving Edit Mode.

    * Source (retopo): read live from its edit bmesh when in Edit Mode, else
      from the committed mesh.  The retopo mesh / ShapeKey are NEVER written
      here (unsafe in Edit Mode — the old Sync crash).
    * Target (mirror): written through its edit bmesh when IT is in Edit Mode,
      otherwise through the object mesh (with topology rebuild as needed).

    `progress`, if given, is called with a 0..1 fraction across the
    per-vertex attachment lookup. Defaults to None (no-op).

    Returns (ProjectionResult, mirror_obj | None).
    """
    err = validate_guide(guide_obj, flat_sk)
    if err is not None:
        return ProjectionResult(success=False, error=err), None

    points_world, faces = _read_retopo_geometry(retopo)
    n = len(points_world)
    if n == 0:
        return ProjectionResult(success=False, error="Retopo has no vertices."), None

    new_world, attachments, failed = core.compute_forward_world(
        points_world, guide_obj, flat_sk, progress=progress
    )
    failed_indices = tuple(i for i, a in enumerate(attachments) if not a.is_ok)

    hidden = _read_hidden(retopo)
    retopo_inv = retopo.matrix_world.inverted()
    src2d_local = [retopo_inv @ point for point in points_world]
    mirror = find_mirror(retopo)
    if mirror is not None and mirror.mode == "EDIT":
        _write_mirror_editmode(mirror, new_world, src2d_local, faces, hidden)
    else:
        mirror = _ensure_mirror_topology(context, retopo, n, faces)
        inv = mirror.matrix_world.inverted()
        _write_coords_local(mirror, [inv @ p for p in new_world])
        _write_src2d_mesh(mirror, src2d_local)
        _apply_hidden_mesh(mirror, hidden)

    # Edit-mode Refresh always returns to the base topology. Preview generation
    # needs the Object-mode operator pipeline and is exposed only there.
    _set_preview_state(mirror, 0)

    return (
        ProjectionResult(
            success=True, total=n, projected=n - failed, failed=failed,
            failed_indices=failed_indices,
        ),
        mirror,
    )


def mirror_has_unsynced_edits(retopo, guide_obj, flat_sk, tolerance=1e-5) -> bool:
    """True if the mirror's current vertex positions differ from what a fresh
    Refresh would compute from the retopo's current 2D layout.

    Used to guard auto-refresh: the "Apply 3D Edits > 2D" (experimental) flow
    lets the user move mirror verts in 3D and leaves them un-applied until
    they press Apply. An automatic Refresh in that window would silently
    overwrite those moves (mirror.py's own contract: "any edit the user makes
    to it is silently overwritten on the next Refresh") — this check lets a
    caller skip that Refresh instead of losing the pending edit.

    A topology mismatch (retopo grew/shrank since the mirror was last built)
    is NOT considered a pending edit — there's nothing on the mirror worth
    protecting in that case, and refreshing to rebuild it is exactly right.
    """
    mirror = find_mirror(retopo)
    if mirror is None:
        return False
    if preview_levels(mirror) > 0:
        return False
    n = len(retopo.data.vertices)
    if len(mirror.data.vertices) != n:
        return False

    err = validate_guide(guide_obj, flat_sk)
    if err is not None:
        return False

    points_world = extract_points_world(retopo)
    expected_world, _attachments, _failed = core.compute_forward_world(
        points_world, guide_obj, flat_sk
    )
    mw = mirror.matrix_world
    for i in range(n):
        actual = mw @ mirror.data.vertices[i].co
        if (actual - expected_world[i]).length > tolerance:
            return True
    return False


def remove_mirror(retopo):
    """Delete the mirror object + its mesh for *retopo*.  Returns True if one
    was removed.
    """
    mirror = find_mirror(retopo)
    if mirror is None:
        return False
    me = mirror.data
    bpy.data.objects.remove(mirror)
    if me is not None and me.users == 0:
        bpy.data.meshes.remove(me)
    return True
