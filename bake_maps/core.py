"""Guide-map computation + baking for retopo guidance.

Two maps, both computed per Guide vertex, stored as Color Attributes on the
Guide mesh, then baked to a fixed-name image via Cycles EMIT:

Residual Map ("AC9_ResidualMap")
    Signed distance from each Guide 3D vertex to the current retopo surface
    (retopo 2D verts projected onto Guide 3D via the shared barycentric
    pipeline). Red = Guide in front of the retopo surface, blue = behind,
    white = captured, dark gray = outside the retopo's 2D footprint.
    The fixed mm scale makes successive bakes comparable.

Sag Map ("AC9_SagMap")
    Per-pattern-panel (seam-bounded island) PCA plane fit of the Guide 3D
    shape; signed distance from that plane. Mid gray = on plane, white =
    bulges forward, black = sinks back. Iso-lines of this map are the
    natural edge-loop flow lines for low-frequency sag.

Both reuse the CLO Projector's guide cache (triangles + 2D BVH), so a bake
after a projection costs no re-extraction.
"""

import math
import time
from typing import List, Optional, Tuple

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from ..clo_projector.core import _get_guide_cache
from ..clo_projector.guide import (
    validate_guide,
    extract_points_world,
    guide_3d_shapekey,
    _extract_world_co_np,
)
from ..clo_projector.attachment import compute_attachments, apply_attachments_to_3d
from ..clo_projector.islands import detect_triangle_islands

RESIDUAL_ATTR = "AC9_Residual"
SAG_ATTR = "AC9_Sag"
RESIDUAL_IMAGE = "AC9_ResidualMap"
SAG_IMAGE = "AC9_SagMap"

UNCOVERED_COLOR = (0.2, 0.2, 0.2)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _guide_vert_positions(guide_obj, flat_sk: str):
    """(guide 2D world, guide 3D world) as (n,3) float64 arrays."""
    mesh = guide_obj.data
    matrix = guide_obj.matrix_world
    g2d = _extract_world_co_np(mesh, flat_sk, matrix)
    g3d = _extract_world_co_np(mesh, guide_3d_shapekey(mesh), matrix)
    return g2d, g3d


def _write_color_attr(mesh, attr_name: str, colors: np.ndarray) -> None:
    """colors: (n_verts, 4) float array → FLOAT_COLOR point attribute."""
    ca = mesh.color_attributes.get(attr_name)
    if ca is not None and (ca.domain != 'POINT' or ca.data_type != 'FLOAT_COLOR'):
        mesh.color_attributes.remove(ca)
        ca = None
    if ca is None:
        ca = mesh.color_attributes.new(attr_name, 'FLOAT_COLOR', 'POINT')
    ca.data.foreach_set("color", colors.astype(np.float32).ravel())


def _diverging_colors(d: np.ndarray, scale: float) -> np.ndarray:
    """Signed values → red(+)/white(0)/blue(−), saturating at ±scale."""
    n = len(d)
    t = np.clip(d / max(scale, 1e-12), -1.0, 1.0)
    col = np.ones((n, 4))
    pos = t > 0
    col[pos, 1] = 1.0 - t[pos]
    col[pos, 2] = 1.0 - t[pos]
    col[~pos, 0] = 1.0 + t[~pos]
    col[~pos, 1] = 1.0 + t[~pos]
    return col


# ---------------------------------------------------------------------------
# Residual map
# ---------------------------------------------------------------------------


def compute_residual(
    retopo,
    guide_obj,
    flat_sk: str,
    scale_m: float,
    cover_eps_m: float,
) -> Tuple[Optional[str], Optional[dict]]:
    """Write the residual Color Attribute onto the Guide. Returns (error, stats)."""
    if retopo is None or retopo.type != "MESH":
        return "Retopo must be set (a mesh object).", None
    err = validate_guide(guide_obj, flat_sk)
    if err is not None:
        return err, None
    rmesh = retopo.data
    if len(rmesh.polygons) == 0:
        return "Retopo has no faces.", None

    t0 = time.time()
    cached = _get_guide_cache(guide_obj, flat_sk)

    # Project retopo 2D verts onto Guide 3D (same path as Sync 2D > 3D,
    # but without touching the AC9_3D_Project ShapeKey).
    pts2d = extract_points_world(retopo)
    attachments = compute_attachments(pts2d, cached["tris_2d"], bvh_2d=cached["bvh_2d"])
    proj3d = apply_attachments_to_3d(attachments, pts2d, cached["tris_3d"])

    rpolys = [tuple(p.vertices) for p in rmesh.polygons]
    bvh_surface = BVHTree.FromPolygons(proj3d, rpolys)
    foot_verts = [(p.x, p.y, 0.0) for p in pts2d]
    bvh_foot = BVHTree.FromPolygons(foot_verts, rpolys)

    g2d, g3d = _guide_vert_positions(guide_obj, flat_sk)
    n = len(g3d)
    d = np.zeros(n)
    covered = np.zeros(n, dtype=bool)
    for i in range(n):
        loc, _, _, _ = bvh_foot.find_nearest(
            Vector((g2d[i, 0], g2d[i, 1], 0.0)), cover_eps_m
        )
        if loc is None:
            continue
        loc3, nrm3, _, _ = bvh_surface.find_nearest(Vector(g3d[i]))
        if loc3 is None:
            continue
        covered[i] = True
        vec = Vector(g3d[i]) - loc3
        d[i] = vec.dot(nrm3.normalized()) if nrm3.length > 0 else vec.length

    colors = _diverging_colors(d, scale_m)
    colors[~covered, 0] = UNCOVERED_COLOR[0]
    colors[~covered, 1] = UNCOVERED_COLOR[1]
    colors[~covered, 2] = UNCOVERED_COLOR[2]
    _write_color_attr(guide_obj.data, RESIDUAL_ATTR, colors)

    stats = {"covered": int(covered.sum()), "total": n, "seconds": time.time() - t0}
    if covered.any():
        mm = d[covered] * 1000.0
        stats.update(
            rms_mm=float(np.sqrt((mm ** 2).mean())),
            p90_mm=float(np.percentile(np.abs(mm), 90)),
            max_mm=float(np.abs(mm).max()),
        )
    return None, stats


# ---------------------------------------------------------------------------
# Sag map
# ---------------------------------------------------------------------------

_MIN_ISLAND_VERTS = 16


def compute_sag(
    guide_obj,
    flat_sk: str,
    scale_m: float,
) -> Tuple[Optional[str], Optional[dict]]:
    """Write the plane-fit sag Color Attribute onto the Guide. Returns (error, stats)."""
    err = validate_guide(guide_obj, flat_sk)
    if err is not None:
        return err, None

    t0 = time.time()
    mesh = guide_obj.data
    _, g3d = _guide_vert_positions(guide_obj, flat_sk)
    n = len(g3d)

    # Pattern panels = seam-bounded islands (same definition as the projector
    # overlay). Per-vertex id from any owning triangle.
    island_of_tri = detect_triangle_islands(guide_obj)
    if not island_of_tri:
        return "Guide has no polygons.", None
    vert_island = np.full(n, -1, dtype=np.int64)
    for poly in mesh.polygons:
        isl = island_of_tri[poly.index]
        for vi in poly.vertices:
            if vert_island[vi] < 0:
                vert_island[vi] = isl

    d = np.zeros(n)
    n_islands = 0
    max_abs = 0.0
    for isl in np.unique(vert_island):
        if isl < 0:
            continue
        sel = vert_island == isl
        if sel.sum() < _MIN_ISLAND_VERTS:
            continue
        pts = g3d[sel]
        c = pts.mean(0)
        q = pts - c
        _, evec = np.linalg.eigh(q.T @ q / len(q))
        d[sel] = q @ evec[:, 0]  # signed distance to best-fit plane
        n_islands += 1
        max_abs = max(max_abs, float(np.abs(d[sel]).max()))

    val = np.clip(d / (2.0 * max(scale_m, 1e-12)) + 0.5, 0.0, 1.0)
    colors = np.ones((n, 4))
    colors[:, 0] = colors[:, 1] = colors[:, 2] = val
    _write_color_attr(mesh, SAG_ATTR, colors)

    stats = {
        "islands": n_islands,
        "max_mm": max_abs * 1000.0,
        "seconds": time.time() - t0,
    }
    return None, stats


# ---------------------------------------------------------------------------
# Bake (Color Attribute → image, Cycles EMIT)
# ---------------------------------------------------------------------------


def _enable_collections_for(view_layer, obj):
    """Make every layer-collection chain containing *obj* visible/included.

    Returns a list of (layer_collection, exclude, lc_hide, coll_hide) tuples
    for restoring afterwards. Needed because baking requires the object to be
    in the view layer — excluded/hidden parent collections silently break
    bpy.ops.object.bake with 'No valid selected objects'.
    """
    saved = []

    def walk(lc):
        found = False
        for child in lc.children:
            if walk(child):
                found = True
        if obj.name in lc.collection.objects or found:
            saved.append((lc, lc.exclude, lc.hide_viewport, lc.collection.hide_viewport))
            lc.exclude = False
            lc.hide_viewport = False
            lc.collection.hide_viewport = False
            return True
        return False

    walk(view_layer.layer_collection)
    return saved


def bake_to_image(
    context,
    obj,
    image_name: str,
    resolution: int,
    margin: int = 8,
    attr_name: Optional[str] = None,
    bake_type: str = 'EMIT',
    samples: Optional[int] = None,
) -> Optional[str]:
    """Bake *obj* into the named image (created or resized as needed) using
    the active UV map. All scene/material state is restored afterwards.
    Returns an error string or None.

    attr_name=None bakes a native Cycles pass (e.g. bake_type='AO') straight
    off the object's own geometry — no shader graph needed for that. Passing
    attr_name instead wires that Color Attribute through Attribute->Emission
    (bake_type must stay 'EMIT'), the same path the Residual/Sag maps use.

    samples=None picks a sane default for the bake_type: an Attribute->Emission
    bake is an exact passthrough (no ray variance, 1 sample is correct), a
    native pass like AO is a noisy Monte-Carlo estimate that needs more.
    """
    if samples is None:
        samples = 1 if bake_type == 'EMIT' else 64
    if context.mode != 'OBJECT':
        return "Switch to Object Mode to bake."
    mesh = obj.data
    if not mesh.uv_layers:
        return f"'{obj.name}' has no UV map — baking needs one."
    if attr_name is not None and mesh.color_attributes.get(attr_name) is None:
        return f"Color attribute '{attr_name}' missing — compute it first."

    img = bpy.data.images.get(image_name)
    if img is None:
        img = bpy.data.images.new(
            image_name, resolution, resolution, alpha=False, float_buffer=True
        )
        img.colorspace_settings.name = 'Non-Color'
    elif tuple(img.size) != (resolution, resolution):
        img.scale(resolution, resolution)

    scene = context.scene
    view_layer = context.view_layer

    # ── save state ──────────────────────────────────────────────────────────
    prev_engine = scene.render.engine
    prev_samples = getattr(scene.cycles, "samples", None)
    prev_margin = scene.render.bake.margin
    prev_use_clear = scene.render.bake.use_clear
    prev_active = view_layer.objects.active
    prev_selected = [o for o in view_layer.objects if o.select_get()]
    try:
        prev_hide = obj.hide_get()
    except RuntimeError:
        prev_hide = False  # not in the view layer yet (excluded collection)
    prev_hide_render = obj.hide_render
    prev_mats = [slot.material for slot in obj.material_slots]
    had_slots = bool(obj.material_slots)

    bake_mat = bpy.data.materials.new("AC9_BakeMap_Temp")
    bake_mat.use_nodes = True
    nt = bake_mat.node_tree
    nt.nodes.clear()
    out_node = nt.nodes.new('ShaderNodeOutputMaterial')
    if attr_name is not None:
        emit = nt.nodes.new('ShaderNodeEmission')
        attr = nt.nodes.new('ShaderNodeAttribute')
        attr.attribute_name = attr_name
        nt.links.new(attr.outputs['Color'], emit.inputs['Color'])
        nt.links.new(emit.outputs[0], out_node.inputs[0])
    # A native pass (AO, …) needs no shader graph — the bake type computes it
    # from the object's geometry directly, ignoring Surface entirely. The
    # Image Texture node below is still required: it is where bpy.ops.object
    # .bake writes the result, native or attribute-driven alike.
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.image = img
    nt.nodes.active = tex

    saved_collections = []
    try:
        scene.render.engine = 'CYCLES'
        if hasattr(scene, "cycles"):
            scene.cycles.samples = samples
        scene.render.bake.margin = margin
        scene.render.bake.use_clear = True

        saved_collections = _enable_collections_for(view_layer, obj)
        obj.hide_set(False)
        obj.hide_render = False
        for o in prev_selected:
            o.select_set(False)
        obj.select_set(True)
        view_layer.objects.active = obj

        if had_slots:
            for slot in obj.material_slots:
                slot.material = bake_mat
        else:
            mesh.materials.append(bake_mat)

        result = bpy.ops.object.bake(type=bake_type)
        if 'FINISHED' not in result:
            return f"Bake did not finish ({result})."

        # Survive .blend save/reload: fake-user keeps the datablock alive,
        # pack() embeds the pixels (generated images are otherwise dropped).
        # Re-pack every bake so the embedded copy is the fresh result.
        img.use_fake_user = True
        try:
            img.pack()
        except RuntimeError:
            pass  # packing is a convenience; the bake itself succeeded
        return None
    except RuntimeError as exc:
        return f"Bake failed: {exc}"
    finally:
        # ── restore state ────────────────────────────────────────────────────
        if had_slots:
            for slot, mat in zip(obj.material_slots, prev_mats):
                slot.material = mat
        else:
            idx = mesh.materials.find(bake_mat.name)
            if idx >= 0:
                mesh.materials.pop(index=idx)
        bpy.data.materials.remove(bake_mat)

        scene.render.engine = prev_engine
        if prev_samples is not None and hasattr(scene, "cycles"):
            scene.cycles.samples = prev_samples
        scene.render.bake.margin = prev_margin
        scene.render.bake.use_clear = prev_use_clear

        obj.select_set(False)
        for o in prev_selected:
            try:
                o.select_set(True)
            except RuntimeError:
                pass
        view_layer.objects.active = prev_active
        try:
            obj.hide_set(prev_hide)
        except RuntimeError:
            pass
        obj.hide_render = prev_hide_render
        for lc, exclude, lc_hide, coll_hide in saved_collections:
            lc.exclude = exclude
            lc.hide_viewport = lc_hide
            lc.collection.hide_viewport = coll_hide


# ---------------------------------------------------------------------------
# Drape map (AO x Curvature — the "atari" for 2D knife-cutting)
# ---------------------------------------------------------------------------
#
# What the user was doing by hand: bake AO and a curvature/cavity pass off
# the Guide, multiply them in the shader, and read the result on the flat
# 2D layout as a shaded reference of the garment's drape while cutting panel
# boundaries. This wires an equivalent of both into one button and one
# image, so a non-technical user never opens the Shader Editor.
#
# AO is computed by hand (per-vertex hemisphere raycast against the Guide's
# own BVH) rather than via native bpy.ops.object.bake(type='AO'): verified
# headless on Blender 5.0.1 CPU that the native AO bake type reliably
# returns all-zero on every attempt (retried 3x, with a depsgraph update,
# with world.light_settings.ao_factor/distance and view_layer.use_ao/
# use_pass_ambient_occlusion all forced on) while NORMAL/POSITION bake types
# on the identical setup return correct, non-uniform data — so this is an
# AO-specific gap in this environment (background-mode/CPU Cycles), not a
# setup mistake. Matches the addon's own documented headless trap for GPU-
# adjacent features (see blender-inspect skill notes on Simple Bake). The
# self-contained raycast keeps the whole Drape Map path verifiable headless,
# same as Residual/Sag already are, instead of shipping an unverifiable
# native-bake path on faith.

CURVATURE_ATTR = "AC9_Curvature"
AO_ATTR = "AC9_AO"
DRAPE_IMAGE = "AC9_DrapeMap"
_DRAPE_AO_TEMP = "AC9_DrapeMap_AO_tmp"
_DRAPE_CURVATURE_TEMP = "AC9_DrapeMap_Curvature_tmp"


def _fibonacci_hemisphere(n: int) -> List[Vector]:
    """n unit directions spread roughly evenly over the +Z hemisphere."""
    golden = math.pi * (3.0 - math.sqrt(5.0))
    out = []
    for i in range(n):
        z = (i + 0.5) / n
        r = math.sqrt(max(0.0, 1.0 - z * z))
        theta = golden * i
        out.append(Vector((r * math.cos(theta), r * math.sin(theta), z)))
    return out


def compute_ao(guide_obj, samples: int = 24, distance_m: float = 0.05,
               bias_m: float = 0.0005) -> Optional[str]:
    """Write a per-vertex hemisphere-raycast AO Color Attribute onto the
    Guide (white = exposed, dark = enclosed by nearby Guide surface).
    Returns an error string or None. See the module note above for why this
    is a hand-rolled raycast rather than the native Cycles AO bake type.
    """
    mesh = guide_obj.data
    if len(mesh.polygons) == 0:
        return "Guide has no polygons."
    mat = guide_obj.matrix_world
    nmat = mat.inverted().transposed().to_3x3()

    tri_verts: List[Vector] = []
    tri_faces: List[Tuple[int, int, int]] = []
    for poly in mesh.polygons:
        idx = poly.vertices
        base = len(tri_verts)
        tri_verts.extend(mat @ mesh.vertices[i].co for i in idx)
        for k in range(1, len(idx) - 1):
            tri_faces.append((base, base + k, base + k + 1))
    bvh = BVHTree.FromPolygons(tri_verts, tri_faces)

    dirs = _fibonacci_hemisphere(max(1, samples))
    n = len(mesh.vertices)
    ao = np.ones(n, dtype=np.float64)
    up = Vector((0.0, 0.0, 1.0))
    alt_up = Vector((1.0, 0.0, 0.0))
    for vi in range(n):
        v = mesh.vertices[vi]
        wn = (nmat @ v.normal)
        if wn.length < 1e-9:
            continue
        wn.normalize()
        ref = alt_up if abs(wn.z) > 0.99 else up
        tangent = ref.cross(wn)
        if tangent.length < 1e-9:
            continue
        tangent.normalize()
        bitangent = wn.cross(tangent)
        origin = (mat @ v.co) + wn * bias_m

        hits = 0
        for d in dirs:
            ray_dir = tangent * d.x + bitangent * d.y + wn * d.z
            loc, _nrm, _idx, _dist = bvh.ray_cast(origin, ray_dir, distance_m)
            if loc is not None:
                hits += 1
        ao[vi] = 1.0 - hits / len(dirs)

    colors = np.ones((n, 4))
    colors[:, 0] = colors[:, 1] = colors[:, 2] = ao
    _write_color_attr(mesh, AO_ATTR, colors)
    return None


def compute_curvature(guide_obj) -> Optional[str]:
    """Write a per-vertex concavity Color Attribute onto the Guide (concave
    creases/seams dark, convex or flat areas white). Returns an error string
    or None.

    Computed directly from the Guide's Basis (3D) shape-key data — NOT via
    bpy.ops.paint.vertex_color_dirt, which reads the EVALUATED (shape-key-
    blended) mesh rather than raw Basis data. Verified headless two ways:
    (1) a synthetic cube flattened by a second shape key at value=1 came
    back uniformly 0.0 (no curvature) from the native operator even with
    view_layer.update() forced first; (2) the real production Guide (355k
    verts, Flat SK at value=1 — this addon's normal 2D working view) had
    AC9_Curvature read back as a flat 1.0 with zero standard deviation
    across every vertex once baked. A flat 2D layout has no curvature by
    definition, and there is no reliable way found to force the operator to
    evaluate Basis instead — same lesson as compute_ao's native-AO-bake
    replacement below. Vertex position vs. its neighbours' centroid,
    projected onto the vertex normal, is a standard discrete curvature
    proxy; only the concave (inward) part is used, auto-scaled to this
    mesh's own 90th-percentile concavity so the result needs no per-Guide
    tuning.
    """
    mesh = guide_obj.data
    n = len(mesh.vertices)
    if n == 0:
        return "Guide has no vertices."
    mat = guide_obj.matrix_world
    basis3d = _extract_world_co_np(mesh, guide_3d_shapekey(mesh), mat)

    normal_sum = np.zeros((n, 3), dtype=np.float64)
    for poly in mesh.polygons:
        idx = poly.vertices
        pts = basis3d[list(idx)]
        for k in range(1, len(idx) - 1):
            a, b, c = pts[0], pts[k], pts[k + 1]
            fn = np.cross(b - a, c - a)
            for vi in (idx[0], idx[k], idx[k + 1]):
                normal_sum[vi] += fn
    lengths = np.linalg.norm(normal_sum, axis=1, keepdims=True)
    safe_lengths = np.where(lengths < 1e-12, 1.0, lengths)
    normals = normal_sum / safe_lengths

    neighbor_sum = np.zeros((n, 3), dtype=np.float64)
    neighbor_count = np.zeros(n, dtype=np.int64)
    edge_v = np.empty((len(mesh.edges), 2), dtype=np.int64)
    mesh.edges.foreach_get("vertices", edge_v.ravel())
    for a, b in edge_v:
        neighbor_sum[a] += basis3d[b]
        neighbor_count[a] += 1
        neighbor_sum[b] += basis3d[a]
        neighbor_count[b] += 1

    has_nb = neighbor_count > 0
    centroid = basis3d.copy()
    safe_count = np.where(has_nb, neighbor_count, 1)[:, None]
    centroid[has_nb] = neighbor_sum[has_nb] / safe_count[has_nb]
    disp = basis3d - centroid
    signed = np.einsum('ij,ij->i', disp, normals)
    concavity = np.clip(-signed, 0.0, None)
    # Scale from the 90th percentile of the CONCAVE vertices only, not of
    # the whole mesh: seams/creases are typically a small fraction of a
    # garment's surface, so the 90th percentile of the full (mostly-zero)
    # array lands at 0 and washes the whole map to white — verified
    # headless on a synthetic sphere with a 4-vertex dent (0.6% of verts):
    # a whole-mesh percentile scale produced uniform 1.0, this fixed it.
    concave_vals = concavity[concavity > 1e-12]
    scale = float(np.percentile(concave_vals, 90)) if len(concave_vals) else 0.0
    val = 1.0 - np.clip(concavity / scale, 0.0, 1.0) if scale > 1e-12 else np.ones(n)
    val = np.where(has_nb, val, 1.0)

    colors = np.ones((n, 4))
    colors[:, 0] = colors[:, 1] = colors[:, 2] = val
    _write_color_attr(mesh, CURVATURE_ATTR, colors)
    return None


def _read_pixels(img) -> np.ndarray:
    buf = np.empty(len(img.pixels), dtype=np.float32)
    img.pixels.foreach_get(buf)
    return buf


def bake_drape_map(context, guide_obj, resolution: int, margin: int = 8) -> Optional[str]:
    """AO x Curvature, baked and combined into one 'AC9_DrapeMap' image on
    the Guide's flat UV layout. Returns an error string or None.
    """
    err = compute_curvature(guide_obj)
    if err is not None:
        return err
    err = compute_ao(guide_obj)
    if err is not None:
        return err

    err = bake_to_image(context, guide_obj, _DRAPE_CURVATURE_TEMP, resolution,
                        margin, attr_name=CURVATURE_ATTR, bake_type='EMIT')
    if err is not None:
        return err
    err = bake_to_image(context, guide_obj, _DRAPE_AO_TEMP, resolution,
                        margin, attr_name=AO_ATTR, bake_type='EMIT')
    if err is not None:
        return err

    curv_img = bpy.data.images[_DRAPE_CURVATURE_TEMP]
    ao_img = bpy.data.images[_DRAPE_AO_TEMP]
    combined = np.clip(_read_pixels(curv_img) * _read_pixels(ao_img), 0.0, 1.0)
    combined[3::4] = 1.0  # opaque — alpha has no meaning for either source pass

    drape_img = bpy.data.images.get(DRAPE_IMAGE)
    if drape_img is None:
        drape_img = bpy.data.images.new(
            DRAPE_IMAGE, resolution, resolution, alpha=False, float_buffer=True
        )
        drape_img.colorspace_settings.name = 'Non-Color'
    elif tuple(drape_img.size) != (resolution, resolution):
        drape_img.scale(resolution, resolution)
    drape_img.pixels.foreach_set(combined)
    drape_img.update()
    drape_img.use_fake_user = True
    try:
        drape_img.pack()
    except RuntimeError:
        pass

    bpy.data.images.remove(curv_img)
    bpy.data.images.remove(ao_img)
    return None
