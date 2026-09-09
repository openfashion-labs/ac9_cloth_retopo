"""Guide mesh validation and world-space triangle extraction.

Bridges bpy mesh data into the pure types consumed by attachment.py.

A single Guide Object is used for both 2D and 3D data:
  - Guide 3D  = Basis ShapeKey  (or mesh.vertices when no ShapeKeys), or the
                AC9_Separated ShapeKey when the shared '3D Source' selects it
                (guide_3d_shapekey() resolves which)
  - Guide 2D  = a named flat ShapeKey (e.g. "UVMap_Flattened" at value 1.0)

ShapeKey data is read directly — no depsgraph evaluation needed.
This means modifier stacks on the guide are intentionally ignored, which is
fine for typical CLO/MD garment guides that carry no deforming modifiers.

The retopo object is read from its Basis (base mesh), since the ShapeKey
we write back is indexed against the base mesh.
"""

from typing import List, Optional, Tuple

import bpy
from mathutils import Vector

from .attachment import Triangle

try:
    import numpy as _np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


# ShapeKey written by guide_separate: the Guide with its self-touching layers
# pushed apart for baking. Defined here (not in guide_separate) because every
# reader of the Guide's 3D shape resolves it through guide_3d_shapekey().
SEPARATED_SK_NAME = "AC9_Separated"


def guide_3d_shapekey(mesh) -> str:
    """Name of the ShapeKey that is the Guide's 3D shape right now.

    "Basis" unless the shared setting guide_3d_source is 'SEPARATED' AND the
    mesh carries AC9_Separated. Every 3D read of the Guide (projection
    triangles, Guide Maps, overlays) goes through this so they all agree.
    """
    if mesh.shape_keys is None or SEPARATED_SK_NAME not in mesh.shape_keys.key_blocks:
        return "Basis"
    try:
        top = bpy.context.scene.ac9_cloth_retopo
        if top.guide_3d_source == 'SEPARATED':
            return SEPARATED_SK_NAME
    except Exception:
        pass
    return "Basis"


def validate_guide(guide_obj, flat_shapekey_name) -> Optional[str]:
    """Return a human-readable error string, or None if the guide is usable.

    Checks:
      - guide_obj is a triangulated MESH
      - flat_shapekey_name names an existing ShapeKey on that mesh
    """
    if guide_obj is None or guide_obj.type != "MESH":
        return "Guide Object must be a mesh."
    if not flat_shapekey_name:
        return "Flat ShapeKey name must not be empty. Enter the ShapeKey name used for UV flattening."

    mesh = guide_obj.data
    if mesh.shape_keys is None:
        return (
            f"Guide Object '{guide_obj.name}' has no ShapeKeys. "
            f"Add a ShapeKey named '{flat_shapekey_name}' for the UV-flat layout."
        )
    if flat_shapekey_name not in mesh.shape_keys.key_blocks:
        available = ", ".join(
            kb.name for kb in mesh.shape_keys.key_blocks if kb.name != "Basis"
        ) or "none"
        return (
            f"ShapeKey '{flat_shapekey_name}' not found on '{guide_obj.name}'. "
            f"Available non-Basis keys: {available}"
        )
    for i, poly in enumerate(mesh.polygons):
        if len(poly.vertices) != 3:
            return (
                f"Guide polygon {i} has {len(poly.vertices)} vertices. "
                f"Triangulate the guide mesh first (MVP supports tris only)."
            )
    return None


def _extract_world_co_np(mesh, sk_name_or_none, matrix):
    """Bulk-read ShapeKey/vertex coords via foreach_get, transform with numpy.

    Returns (n_verts, 3) float64 world-space array.  ~10–20× faster than a
    Python loop of  matrix @ sk_data[i].co  for large meshes.
    """
    n = len(mesh.vertices)
    co = _np.empty(n * 3, dtype=_np.float32)
    if (
        sk_name_or_none
        and mesh.shape_keys is not None
        and sk_name_or_none in mesh.shape_keys.key_blocks
    ):
        mesh.shape_keys.key_blocks[sk_name_or_none].data.foreach_get("co", co)
    elif mesh.shape_keys is not None and "Basis" in mesh.shape_keys.key_blocks:
        mesh.shape_keys.key_blocks["Basis"].data.foreach_get("co", co)
    else:
        mesh.vertices.foreach_get("co", co)

    co = co.reshape(n, 3).astype(_np.float64)
    m = _np.array(matrix, dtype=_np.float64)
    return co @ m[:3, :3].T + m[:3, 3]  # (n, 3) world-space


def _extract_tri_verts_np(mesh):
    """Return (n_tris, 3) int32 polygon vertex indices in polygon order.

    Uses loop_start + loops.vertex_index bulk reads — safe for all-tris meshes
    (already enforced by validate_guide) and preserves polygon ordering so
    stored attachment.triangle_index values stay valid.
    """
    n_polys = len(mesh.polygons)
    n_loops = len(mesh.loops)

    loop_starts = _np.empty(n_polys, dtype=_np.int32)
    mesh.polygons.foreach_get("loop_start", loop_starts)

    loop_verts = _np.empty(n_loops, dtype=_np.int32)
    mesh.loops.foreach_get("vertex_index", loop_verts)

    return _np.stack(
        [loop_verts[loop_starts], loop_verts[loop_starts + 1], loop_verts[loop_starts + 2]],
        axis=1,
    )


def _triangles_from_np(world_co, tri_verts) -> List[Triangle]:
    """Convert numpy arrays to List[Triangle] (list of Vector 3-tuples)."""
    return [
        (
            Vector(world_co[tri_verts[i, 0]]),
            Vector(world_co[tri_verts[i, 1]]),
            Vector(world_co[tri_verts[i, 2]]),
        )
        for i in range(len(tri_verts))
    ]


def extract_triangles_basis(guide_obj) -> List[Triangle]:
    """World-space triangles of the Guide's 3D shape (see guide_3d_shapekey).

    Falls back to mesh.vertices.co when no ShapeKeys exist.
    Uses numpy bulk reads when available for large CLO meshes.
    """
    mesh   = guide_obj.data
    matrix = guide_obj.matrix_world
    sk3d   = guide_3d_shapekey(mesh)

    if _HAS_NUMPY:
        world_co  = _extract_world_co_np(mesh, sk3d, matrix)
        tri_verts = _extract_tri_verts_np(mesh)
        return _triangles_from_np(world_co, tri_verts)

    if mesh.shape_keys is not None and sk3d in mesh.shape_keys.key_blocks:
        basis_data = mesh.shape_keys.key_blocks[sk3d].data
        world_verts = [matrix @ basis_data[i].co for i in range(len(mesh.vertices))]
    else:
        world_verts = [matrix @ v.co for v in mesh.vertices]

    return [
        (world_verts[p.vertices[0]], world_verts[p.vertices[1]], world_verts[p.vertices[2]])
        for p in mesh.polygons
    ]


def extract_triangles_flat(guide_obj, shapekey_name) -> List[Triangle]:
    """World-space triangles from the UV-flat ShapeKey.

    Caller must have already validated that shapekey_name exists.
    Uses numpy bulk reads when available.
    """
    mesh   = guide_obj.data
    matrix = guide_obj.matrix_world

    if _HAS_NUMPY:
        world_co  = _extract_world_co_np(mesh, shapekey_name, matrix)
        tri_verts = _extract_tri_verts_np(mesh)
        return _triangles_from_np(world_co, tri_verts)

    sk_data = mesh.shape_keys.key_blocks[shapekey_name].data
    world_verts = [matrix @ sk_data[i].co for i in range(len(mesh.vertices))]

    return [
        (world_verts[p.vertices[0]], world_verts[p.vertices[1]], world_verts[p.vertices[2]])
        for p in mesh.polygons
    ]


def build_seam_edge_mask(guide_obj) -> List[Tuple[bool, bool, bool]]:
    """Per-triangle UV-seam edge mask, aligned to barycentric coordinates.

    For each Guide triangle (A, B, C) returns a 3-tuple indicating whether
    each edge of the triangle is flagged as a UV seam:

        mask[i][0]  ↔  edge BC  (vert b — vert c)  ↔  bary u  near 0
        mask[i][1]  ↔  edge AC  (vert a — vert c)  ↔  bary v  near 0
        mask[i][2]  ↔  edge AB  (vert a — vert b)  ↔  bary w  near 0

    Used by classify_boundary_verts to decide whether a retopo vertex sits on
    a Guide UV seam edge (and therefore should be pinned during reverse sync).
    """
    mesh = guide_obj.data

    seam_keys: set = set()
    for e in mesh.edges:
        if e.use_seam:
            a, b = int(e.vertices[0]), int(e.vertices[1])
            seam_keys.add((a, b) if a < b else (b, a))

    mask: List[Tuple[bool, bool, bool]] = []
    for poly in mesh.polygons:
        pv = poly.vertices
        v0, v1, v2 = int(pv[0]), int(pv[1]), int(pv[2])
        e_bc = (v1, v2) if v1 < v2 else (v2, v1)
        e_ac = (v0, v2) if v0 < v2 else (v2, v0)
        e_ab = (v0, v1) if v0 < v1 else (v1, v0)
        mask.append((
            e_bc in seam_keys,
            e_ac in seam_keys,
            e_ab in seam_keys,
        ))
    return mask


def extract_points_world(obj) -> List[Vector]:
    """World-space vertex positions for the retopo object, from its BASE mesh
    (not evaluated). The AC9_3D_Project ShapeKey we write back is indexed
    against this same base mesh, so we want them in lockstep.

    When a Basis ShapeKey exists, evaluation uses shape_keys["Basis"].data —
    `mesh.vertices.co` becomes a stale "fallback" that Python writes don't
    propagate into. So we prefer Basis ShapeKey data when present.
    """
    matrix = obj.matrix_world
    coords = get_basis_local(obj)
    return [matrix @ co for co in coords]


def get_basis_local(obj):
    """Read Basis vertex positions in local space, preferring Basis ShapeKey
    data over `mesh.vertices.co` when a Basis ShapeKey exists.

    Returns a list of mathutils.Vector copies so callers can mutate freely.
    """
    mesh = obj.data
    if mesh.shape_keys is not None and "Basis" in mesh.shape_keys.key_blocks:
        basis = mesh.shape_keys.key_blocks["Basis"]
        return [v.co.copy() for v in basis.data]
    return [v.co.copy() for v in mesh.vertices]


def set_basis_local(obj, coords) -> None:
    """Write Basis vertex positions (local space). Updates **both**
    `mesh.vertices.co` and the Basis ShapeKey's data when one exists.

    Why both: Blender's depsgraph evaluation reads Basis ShapeKey data when
    shape keys are present, so writes to `mesh.vertices.co` alone produce no
    visible change. But `mesh.vertices.co` is what Edit Mode entry rebuilds
    the BMesh from, and what other tooling tends to read, so we keep it in
    sync as well.
    """
    mesh = obj.data
    for i, co in enumerate(coords):
        mesh.vertices[i].co = co
    if mesh.shape_keys is not None and "Basis" in mesh.shape_keys.key_blocks:
        basis = mesh.shape_keys.key_blocks["Basis"]
        for i, co in enumerate(coords):
            basis.data[i].co = co
