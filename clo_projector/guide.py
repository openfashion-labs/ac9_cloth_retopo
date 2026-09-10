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

import math
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


def guide_all_tris(mesh) -> bool:
    """True when every polygon of *mesh* is a triangle.

    O(1): the loops of a mesh are the polygons' corners end to end, so the
    loop total is the sum of the polygons' vertex counts. Every polygon has
    at least 3, so the total equals 3 x the polygon count only when none has
    more. Cheap enough to call from a panel's draw() on a 258k-face Guide,
    which a per-polygon loop is not (verified headless on Blender 5.0.1:
    cube 6/24, icosphere 80/240, ngon 1/7).
    """
    return len(mesh.loops) == 3 * len(mesh.polygons)


def count_non_tris(mesh) -> Tuple[int, int]:
    """(how many polygons are not triangles, index of the first one).

    The slow path: only called to WRITE the error, never to decide whether
    there is one — guide_all_tris() does that.
    """
    bad = [p.index for p in mesh.polygons if len(p.vertices) != 3]
    return len(bad), (bad[0] if bad else -1)


#: The flat/real ratio has to land inside this window for the Flat SK to be
#: usable. Measured on real layouts (AUDIT_density_independence_2026-09-08.md
#: §8-B): 1.0023 and 1.1237 for per-item-maximised UV, 0.3318 for a whole
#: outfit packed into one UV square. A Flat SK built while the object still
#: carried an unapplied scale reads ~0.0003 — three orders of magnitude out,
#: which is what this window is for. It is deliberately wide: a legitimately
#: tight or generous packing must not trip it.
FLAT_PER_REAL_MIN = 0.05
FLAT_PER_REAL_MAX = 20.0

#: Object scale this far from 1.0 counts as unapplied.
SCALE_EPS = 1e-4

#: How far the flat layout's plane may tilt out of world XY before the 2D
#: readers break. Only float noise is meant to fit under it: the layout is
#: either axis-aligned or it is not (see object_rotation_error).
PLANE_EPS = 1e-3
#: Points sampled from the Flat SK for the world-plane check. The layout is a
#: plane, so a few thousand points pin it as well as half a million do.
PLANE_SAMPLE = 4096


_nonfinite_cache = {}


def invalidate_nonfinite(guide_obj=None) -> None:
    """Drop the memoised non-finite census for `guide_obj`, or all of them."""
    if guide_obj is None:
        _nonfinite_cache.clear()
        return
    uid = guide_obj.data.session_uid
    for k in [k for k in _nonfinite_cache if k[0] == uid]:
        del _nonfinite_cache[k]


def nonfinite_vertex_count(guide_obj, flat_shapekey_name="") -> int:
    """How many of the Guide's vertices have no real position: a NaN (or inf)
    in mesh.vertices, in the Basis ShapeKey, or in the flat ShapeKey.

    Such a vertex is not a small error, it is a hole in the data, and it does
    not fail where you can see it: measured 2026-09-09, five of them on a
    trouser Guide made Analyze Guide die inside numpy with "arange: cannot
    compute length" (the island's coordinate mean is NaN, so the symmetry
    scan's window is NaN), and Inset Line die with "KeyError: (nan, nan,
    nan)". The source was barycentric_transform over a zero-area flat
    triangle; the guard is now in clo_cleanup.core, and this is the census
    that keeps the rest of the add-on from ever reading one.

    Memoised per (mesh, flat key name, vertex count): the scan is a numpy
    pass (measured 3-8 ms from 22k to 277k vertices), which is nothing on a
    button press but far too much for a panel draw(). The vertex count in the
    key makes every operation that adds or removes a vertex re-scan by
    itself; invalidate_nonfinite() covers a same-count edit.
    """
    if guide_obj is None or guide_obj.type != "MESH":
        return 0
    mesh = guide_obj.data
    n = len(mesh.vertices)
    key = (mesh.session_uid, flat_shapekey_name or "", n)
    hit = _nonfinite_cache.get(key)
    if hit is not None:
        return hit
    if not _HAS_NUMPY or n == 0:
        return 0
    co = _np.empty(n * 3, dtype=_np.float32)
    mesh.vertices.foreach_get("co", co)
    bad = ~_np.isfinite(co.reshape(n, 3)).all(axis=1)
    # UVs too: FlatSession.end() derives them from the flat position, so a
    # NaN vertex left a NaN UV behind, and Create Flat SK reads the UV to
    # build the Flat SK - a repair that skipped the UVs would put the NaN
    # straight back (review finding, 2026-09-09).
    # Best-effort: mesh.uv_layers.active.data reads back EMPTY in some states
    # (measured on a 2.1M-loop Guide in Blender 5.0 while another object was
    # in Edit Mode: len(mesh.loops) was 2111409 and the UV collection 0), and
    # a census must never be the thing that raises. Repair Guide does the UV
    # pass on a bmesh, where the data is always there, so a skip here only
    # costs the blocker one press of lead time.
    uv_layer = mesh.uv_layers.active
    m = len(mesh.loops)
    if uv_layer is not None and m and len(uv_layer.data) == m:
        try:
            uv = _np.empty(m * 2, dtype=_np.float32)
            uv_layer.data.foreach_get("uv", uv)
            bad_loops = ~_np.isfinite(uv.reshape(m, 2)).all(axis=1)
            if bad_loops.any():
                lv = _np.empty(m, dtype=_np.int32)
                mesh.loops.foreach_get("vertex_index", lv)
                bad[lv[bad_loops]] = True
        except (TypeError, RuntimeError):
            pass
    keys = getattr(mesh.shape_keys, "key_blocks", None)
    if keys is not None:
        names = [guide_3d_shapekey(mesh), flat_shapekey_name]
        ref = mesh.shape_keys.reference_key
        if ref is not None:
            names.append(ref.name)
        for name in {x for x in names if x}:
            kb = keys.get(name)
            if kb is None or len(kb.data) != n:
                continue
            a = _np.empty(n * 3, dtype=_np.float32)
            kb.data.foreach_get("co", a)
            bad |= ~_np.isfinite(a.reshape(n, 3)).all(axis=1)
    count = int(bad.sum())
    _nonfinite_cache[key] = count
    return count


def nonfinite_vertex_error(guide_obj, flat_shapekey_name) -> Optional[str]:
    """Error string when the Guide holds vertices with no real position."""
    n = nonfinite_vertex_count(guide_obj, flat_shapekey_name)
    if not n:
        return None
    return (
        f"Guide '{guide_obj.name}' has {n} vertex(es) with no valid position "
        f"(NaN). Nothing can be measured against them — press 'Repair Guide' "
        f"in Setup, which moves each one onto the average of its finite "
        f"neighbours, then check that part of the mesh."
    )


def object_scale_error(guide_obj) -> Optional[str]:
    """Error string when `guide_obj`'s world scale is not 1, else None.

    Create Flat SK writes the raw UV into the ShapeKey's LOCAL coordinates, so
    the flat layout's world size is (UV size x object scale). At scale 0.001 --
    what an FBX import leaves behind -- a 413 mm layout lands as 0.41 mm in
    world space and every flat-space distance in the addon becomes meaningless
    (measured: the symmetry axis scan step of 1 mm ends up 2.4x the whole
    layout). Nothing about the Basis is wrong in that state: the mesh data is
    in millimetre units and the 0.001 scale brings it to real size correctly.
    Only the flat side cannot cope, so the message asks for Apply Scale rather
    than claiming the file is broken.
    """
    if guide_obj is None:
        return None
    s = guide_obj.matrix_world.to_scale()
    if all(abs(v - 1.0) <= SCALE_EPS for v in s):
        return None
    return (
        f"Guide '{guide_obj.name}' has an unapplied object scale "
        f"({s.x:.4g}, {s.y:.4g}, {s.z:.4g}). Create Flat SK stores the UV layout "
        f"as local coordinates, so that scale shrinks the flat layout in world "
        f"space and every flat-space distance (retopo spacing, ghost radius, "
        f"symmetry scan) is read at the wrong size. Apply it — "
        f"Object > Apply > Scale — and then create the Flat SK again. The 3D "
        f"side is fine either way; only the flat layout is affected."
    )


#: Memo for flat_per_real: mesh session_uid + V/E counts + the two ShapeKey
#: names. A topology edit invalidates it; a vertex dragged without changing the
#: counts does not, which is harmless (the value is a median over every edge, so
#: one vertex cannot move it), and re-creating the Flat SK clears it explicitly
#: -- see invalidate_flat_per_real, wired into core.invalidate_guide_cache.
#:
#: matrix_world is deliberately NOT part of the key, unlike the seam-analysis
#: caches next door. This is a RATIO of two lengths measured through the same
#: matrix, so the matrix cancels: measured 0.331809 at scale 1 and 0.331807 at
#: scale 0.001 on the same Guide. Keying on it would make the memo miss on every
#: frame while the user drags the Guide, and the consumers include overlay draw
#: code -- a ~100 ms recompute per frame. (A non-uniform scale would make the
#: ratio direction-dependent and so not quite invariant, but validate_guide
#: refuses any Guide whose scale is not 1 before that can matter.)
#:
#: The memo exists because the measurement is a pass over every edge (order of
#: 100 ms on a production jacket; the figure moved 94-105 ms across runs and the
#: machine had a second headless Blender on it, so read it as a magnitude, not a
#: number) while its consumers include per-operator tolerance
#: conversions that read it more than once per run, and overlay code that reads
#: it per redraw.
_flat_per_real_cache = {}


def invalidate_flat_per_real(guide_obj=None) -> None:
    """Drop memoised ratios for `guide_obj`, or all of them."""
    if guide_obj is None:
        _flat_per_real_cache.clear()
        return
    uid = guide_obj.data.session_uid
    for k in [k for k in _flat_per_real_cache if k[0] == uid]:
        del _flat_per_real_cache[k]


def flat_per_real(guide_obj, flat_shapekey_name, source_sk=None) -> Optional[float]:
    """Median |flat edge| / |3D edge| over the Guide's edges, or None.

    The one number that converts a real-world millimetre into the flat layout's
    units. Needed because Create Flat SK writes the raw UV coordinates as
    geometry with no normalisation, so the flat layout's scale is whatever the
    UV packing happens to be: measured 1.0023 for a jacket whose UV fills the
    square, 0.3318 for the same jacket packed with a whole outfit — a factor of
    3.02 between two layouts of the same garment.

    One global number is enough: on the density ladder the per-island medians
    agree to +-0.2% (jacket), +-1.3% (pants), +-0.8% (skirt), and the ratio does
    not move with mesh density (0.3318 at CLO particle distance 2 and at 5).
    It IS per object, though — 1.1237 for shirts against 1.0022 for a jacket in
    the same file, because each was maximised separately — so never cache it
    scene-wide.

    The +-3% spread of the per-edge ratio is the fabric's own stretch between
    the flat pattern and the draped shape; the median is the right summary.

    CONTRACT -- do not redefine this as a per-region or per-edge quantity.
    It is one number describing the UV PACKING's scale, nothing more. Callers
    use it in both directions and rely on the deviation being left OUT:
      * real mm -> flat  (x r): retopo spacing, inset width, coverage margin.
      * flat -> real mm  (/ r): guide_separate's layer test, which asks "same
        cloth or another layer?" and answers it with distance ALONG the cloth.
        There the local flat/3D deviation is signal, not error -- where a seam
        is gathered one side is up to 18.6% longer in the pattern (measured),
        and that stretch of cloth genuinely IS further away along the fabric
        even though 3D says it is close. A per-region ratio would divide that
        signal out and the layer test would silently stop seeing it.
    If a region-local cloth metric is ever needed, add a separate function.

    Returns None when the mesh has no usable edges. Degenerate 3D edges are
    skipped: a CLO export carries a handful of sub-micron edges (21 of them in
    a production jacket, shortest 60 nm) and a ratio against those is noise.
    """
    if not _HAS_NUMPY or guide_obj is None or guide_obj.type != "MESH":
        return None
    mesh = guide_obj.data
    if not mesh.edges:
        return None
    if source_sk is None:
        source_sk = guide_3d_shapekey(mesh)
    m = guide_obj.matrix_world
    key = (mesh.session_uid, len(mesh.vertices), len(mesh.edges),
           flat_shapekey_name, source_sk)
    hit = _flat_per_real_cache.get(key)
    if hit is not None:
        return hit
    co3 = _extract_world_co_np(mesh, source_sk, m)
    co2 = _extract_world_co_np(mesh, flat_shapekey_name, m)
    ev = _np.empty(len(mesh.edges) * 2, dtype=_np.int32)
    mesh.edges.foreach_get("vertices", ev)
    ev = ev.reshape(-1, 2)
    d3 = _np.linalg.norm(co3[ev[:, 0]] - co3[ev[:, 1]], axis=1)
    d2 = _np.linalg.norm(co2[ev[:, 0]] - co2[ev[:, 1]], axis=1)
    ok = d3 > 1e-9
    if not ok.any():
        return None
    r = float(_np.median(d2[ok] / d3[ok]))
    if len(_flat_per_real_cache) > 8:
        _flat_per_real_cache.clear()
    _flat_per_real_cache[key] = r
    return r


def flat_scale(guide_obj, flat_shapekey_name, source_sk=None) -> float:
    """flat_per_real, or 1.0 when it cannot be measured. Never None, never 0.

    Multiply a real-space length by this to get the flat layout's own units;
    divide to go the other way. Any unit works -- it is a pure scale factor, so
    metres in gives metres out. Call it ONCE per operator run and reuse the
    number: the measurement is a pass over every edge -- order of 100 ms on a
    production jacket, which is nothing next to an operator but four orders of
    magnitude over what a panel draw() or an overlay redraw can afford (the
    guide_ready check it sits behind costs 0.002 ms).

    The 1.0 fallback is the pre-conversion behaviour -- treating the flat layout
    as if it were already at real scale -- which is very nearly true for the
    per-item-maximised UV packing this addon was developed against (measured
    r = 1.0023 on a jacket, 1.1237 on a shirt) and wrong by 3.02x for a whole
    outfit packed into one UV square (0.3318).
    """
    r = flat_per_real(guide_obj, flat_shapekey_name, source_sk)
    if r is None or not (r > 0.0):
        return 1.0
    return r


def scene_flat_scale(scene=None) -> float:
    """flat_scale for whatever Guide the add-on's shared inputs point at.

    For the callers that have no Guide argument to hand and no business
    growing one: the GPU overlays (rebuilt from a draw handler and from
    property callbacks) and the modal snap operators. They all measure in the
    flat layout, while their settings -- Max Seam Distance, Bond Distance,
    Snap Distance -- are written in real fabric millimetres like every other
    length in the add-on, so they cross that boundary here.

    Cheap enough for a redraw: flat_per_real is memoised (measured 104.58 ms
    cold, 0.031 ms warm), and the memo survives dragging a slider because its
    key is the mesh and the two ShapeKeys, not matrix_world.

    1.0 -- the pre-conversion behaviour -- when there is no Guide set, no Flat
    SK chosen, or the ratio cannot be measured.
    """
    import bpy
    if scene is None:
        scene = getattr(bpy.context, "scene", None)
    top = getattr(scene, "ac9_cloth_retopo", None)
    if top is None:
        return 1.0
    guide_obj = top.guide_obj
    flat_sk = top.guide_flat_shapekey
    if guide_obj is None or not flat_sk:
        return 1.0
    return flat_scale(guide_obj, flat_sk)


def flat_per_real_error(guide_obj, flat_shapekey_name) -> Optional[str]:
    """Error string when the Flat SK's scale is implausible, else None.

    Catches the case object_scale_error cannot: a Flat SK created while the
    scale was unapplied, and the scale applied afterwards. Apply Scale
    transforms ShapeKeys too, so the flat layout stays exactly as wrong as it
    was while the object now passes the scale check. The ratio is the only
    thing that still shows it.
    """
    r = flat_per_real(guide_obj, flat_shapekey_name)
    if r is None or FLAT_PER_REAL_MIN <= r <= FLAT_PER_REAL_MAX:
        return None
    return (
        f"Guide '{guide_obj.name}' has a Flat ShapeKey "
        f"('{flat_shapekey_name}') whose scale is {r:.6g}x the 3D shape — far "
        f"outside the plausible range for a UV layout "
        f"({FLAT_PER_REAL_MIN}-{FLAT_PER_REAL_MAX}x). It was almost certainly "
        f"created while the object still carried an unapplied scale. Apply the "
        f"scale (Object > Apply > Scale) and create the Flat SK again — "
        f"applying it now does not repair the existing key."
    )


def object_rotation_error(guide_obj) -> Optional[str]:
    """Error string when the object's rotation tilts the flat layout out of
    world XY, else None. Free: reads matrix_world only, like
    object_scale_error.

    Create Flat SK writes the UV into the ShapeKey's LOCAL coordinates, so the
    layout is a plane in the object's local XY. Everything downstream reads it
    in WORLD space and uses .x / .y only, so an unapplied rotation -- X 90
    degrees is what an FBX import leaves behind -- puts the layout in world XZ
    and every one of those readers sees a LINE.

    Measured on the raw-FBX density ladder (2026-09-08): with X 90 unapplied,
    the flat layout's world extent was 412.99 x 0.00 x 453.47 mm, and
      * detect_island_symmetry collapsed every island to a horizontal line, so
        reflecting it across a horizontal axis landed on itself: rms/diagonal
        2.8e-07 to 5.1e-05 against a 6.0e-03 verdict -- 18 of 18 islands
        "symmetric", all false.
      * find_axis_breaks builds its normal as (-d.y, d.x, 0); with d.y == 0 the
        signed distance is identically zero, so EVERY boundary vertex counted
        as sitting on the fold axis. Generate then pinned every one of them and
        delivered the Guide's own vertex spacing: 20 mm asked, 2.960 mm out.
    The same file's lengths were all correct -- flat_per_real and the spacing
    are length-based, so rotation cancels. That is why this hid for months:
    only the readers that look at COMPONENTS break.

    Applying the rotation does NOT repair an existing Flat SK (Apply Rotation
    transforms ShapeKeys too, so the key keeps the same world plane), hence the
    message asks for both steps in order.

    An in-plane rotation (about world Z) is NOT caught here: the layout stays
    in world XY and every distance stays right. It only costs sensitivity ---
    detect_island_symmetry tests the X and Y axes alone, on the measured
    grounds that CLO lays symmetric panels out axis-aligned, so a layout turned
    within its plane loses folds rather than inventing them (a false negative,
    which is the safe direction).
    """
    if guide_obj is None:
        return None
    col = guide_obj.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))
    if col.length < 1e-12:
        return None  # degenerate matrix; the scale gate has that case
    if abs(col.normalized().z) >= 1.0 - PLANE_EPS:
        return None
    tilt = math.degrees(math.acos(min(1.0, abs(col.normalized().z))))
    return (
        f"Guide '{guide_obj.name}' has an unapplied rotation that tilts the "
        f"flat layout {tilt:.1f} degrees out of the world XY plane "
        f"(rotation_euler {tuple(round(math.degrees(a), 1) for a in guide_obj.rotation_euler)}). "
        f"Create Flat SK stores the UV layout as local coordinates, so the "
        f"layout ends up in world XZ and every reader that looks at x / y sees "
        f"a line: symmetry detection reports every island as symmetric, and "
        f"Generate lays a vertex on every Guide vertex instead of at the "
        f"spacing you asked for. Apply the rotation (Object > Apply > "
        f"Rotation) and create the Flat SK again — applying it now does not "
        f"repair the existing key."
    )


def flat_plane_error(guide_obj, flat_shapekey_name) -> Optional[str]:
    """Error string when the Flat SK itself does not lie in a world-XY plane.

    The pair of object_rotation_error, exactly as flat_per_real_error is the
    pair of object_scale_error: it catches the case the free matrix check
    cannot -- a Flat SK created while the object was rotated, with the rotation
    applied afterwards. Apply Rotation transforms ShapeKeys too, so the key
    keeps its wrong world plane while the object's matrix starts passing.

    Costs a pass over PLANE_SAMPLE points, so it belongs in validate_guide
    (operator time), never in a panel draw().
    """
    if guide_obj is None or guide_obj.type != "MESH":
        return None
    sk = guide_obj.data.shape_keys
    if sk is None or flat_shapekey_name not in sk.key_blocks:
        return None
    data = sk.key_blocks[flat_shapekey_name].data
    n = len(data)
    if n < 3:
        return None
    mw = guide_obj.matrix_world
    step = max(1, n // PLANE_SAMPLE)
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for i in range(0, n, step):
        w = mw @ data[i].co
        for k in range(3):
            if w[k] < lo[k]:
                lo[k] = w[k]
            if w[k] > hi[k]:
                hi[k] = w[k]
    ext = [hi[k] - lo[k] for k in range(3)]
    span = max(ext[0], ext[1], ext[2])
    if span <= 0.0 or ext[2] <= PLANE_EPS * span:
        return None
    return (
        f"Guide '{guide_obj.name}' has a Flat ShapeKey "
        f"('{flat_shapekey_name}') that does not lie in the world XY plane: "
        f"its world extent is {ext[0]*1000:.2f} x {ext[1]*1000:.2f} x "
        f"{ext[2]*1000:.2f} mm. Every 2D reader in the addon uses x / y only, "
        f"so a layout standing up in Z is read as a line — symmetry detection "
        f"then reports every island as symmetric and Generate ignores the "
        f"spacing. It was almost certainly created while the object carried an "
        f"unapplied rotation. Apply the rotation (Object > Apply > Rotation) "
        f"and create the Flat SK again — applying it now does not repair the "
        f"existing key."
    )


def validate_guide(guide_obj, flat_shapekey_name) -> Optional[str]:
    """Return a human-readable error string, or None if the guide is usable.

    Checks:
      - guide_obj is a triangulated MESH
      - flat_shapekey_name names an existing ShapeKey on that mesh
      - no vertex has a non-finite position (see nonfinite_vertex_error): a
        NaN reaches numpy and mathutils as a cryptic failure deep inside an
        analysis, so it is refused at the gate with the repair named
      - the object's scale is applied, and the Flat SK's scale is plausible
        (see object_scale_error / flat_per_real_error)
      - the object's rotation is applied, and the Flat SK actually lies in the
        world XY plane the 2D readers assume
        (see object_rotation_error / flat_plane_error)
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
    err = nonfinite_vertex_error(guide_obj, flat_shapekey_name)
    if err is not None:
        return err
    if not guide_all_tris(mesh):
        n, first = count_non_tris(mesh)
        return (
            f"Guide '{guide_obj.name}' has {n} face(s) that are not triangles "
            f"(first: polygon {first}). Press 'Triangulate Guide' in Setup. "
            f"The projection reads the Guide as triangles (barycentric), so a "
            f"quad has no single answer; triangulating adds no vertex and "
            f"moves none, and leaves the 2D layout — and your retopo — as is."
        )
    err = object_scale_error(guide_obj)
    if err is not None:
        return err
    err = object_rotation_error(guide_obj)
    if err is not None:
        return err
    err = flat_per_real_error(guide_obj, flat_shapekey_name)
    if err is not None:
        return err
    return flat_plane_error(guide_obj, flat_shapekey_name)


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
