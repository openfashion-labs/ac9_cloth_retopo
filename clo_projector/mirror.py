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
    silently overwritten on the next Refresh.  Only its vertex *selection* is
    read back (the selection-correspondence overlay).

Topology correspondence
-----------------------
The mirror mesh is built as a 1:1 copy of the retopo's topology (same vertex
indexing and faces), so mirror.vertices[i] always corresponds to
retopo.vertices[i].  This is what makes selection correspondence and per-vertex
mapping trivial.  When the retopo's vert/face count changes, the mirror mesh is
rebuilt.

Two refresh paths
-----------------
* Object Mode: run_forward_projection writes the retopo's AC9_3D_Project
  ShapeKey + attachments (reused by the experimental reverse tools), and the
  mirror copies those coords.  Topology comes from retopo.data.polygons.
* Edit Mode (retopo active): reads live positions AND topology straight from
  the retopo's edit bmesh and projects them with core.compute_forward_world,
  writing ONLY the mirror.  The retopo mesh/ShapeKey are never touched in Edit
  Mode (doing so is what crashed the old Sync operators), so this is safe.
"""

import bpy

from . import core
from .core import ProjectionResult
from .guide import extract_points_world, validate_guide

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
    mirror.hide_render = True
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


# ---------------------------------------------------------------------------
# Object-Mode refresh (full forward projection, persists on the retopo)
# ---------------------------------------------------------------------------


def refresh_mirror(context, retopo, guide_obj, flat_sk, progress=None):
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

    n = len(retopo.data.vertices)
    faces = [tuple(p.vertices) for p in retopo.data.polygons]
    mirror = _ensure_mirror_topology(context, retopo, n, faces)

    # Copy the retopo's AC9_3D_Project ShapeKey (local coords) into the mirror.
    # Mirror shares the retopo's world matrix, so these reproduce the garment
    # world positions.
    sk = retopo.data.shape_keys.key_blocks[core.SHAPEKEY_NAME]
    _write_coords_local(mirror, [sk.data[i].co.copy() for i in range(n)])
    return result, mirror


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


def _write_mirror_editmode(mirror, new_world, faces):
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

    new_world, _attachments, failed = core.compute_forward_world(
        points_world, guide_obj, flat_sk, progress=progress
    )

    mirror = find_mirror(retopo)
    if mirror is not None and mirror.mode == "EDIT":
        _write_mirror_editmode(mirror, new_world, faces)
    else:
        mirror = _ensure_mirror_topology(context, retopo, n, faces)
        inv = mirror.matrix_world.inverted()
        _write_coords_local(mirror, [inv @ p for p in new_world])

    return (
        ProjectionResult(success=True, total=n, projected=n - failed, failed=failed),
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
