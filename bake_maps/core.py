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

import os
import tempfile
import time
from contextlib import contextmanager
from typing import Optional, Tuple

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
# Image BASE names. The image actually baked is "<base>_<Guide name>" — see
# image_name() and the MAP_BASES block below for why.
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


_ISOLATABLE = {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'VOLUME'}


def _isolate_render(view_layer, obj):
    """Hide every other object from the render for the duration of the bake,
    and return what to restore.

    bpy.ops.object.bake renders the SCENE and writes the result into the
    target's UVs, so Cycles syncs and builds a BVH over everything with
    hide_render off — the other five garments, the avatar body, its face.
    That is invisible in the result (the AO node is only_local, and the other
    shaders here read the target's own geometry or a colour attribute) but
    not in the time: measured on the production skirt file, 13 objects and
    1.71M faces, AO pass at 1024, whole scene against Guide only —
    CPU 24.3 s -> 16.6 s (-32%), GPU 3.1 s -> 2.7 s (-13%), with the two maps
    differing by RMS 0.0000 / max 0.0000.

    Note that hide_render, not viewport visibility, is what a bake reads: a
    Guide hidden behind the eye icon is still in the bake, which is why the
    saving is there to be had in the first place.
    """
    saved = []
    for other in view_layer.objects:
        if other is obj or other.type not in _ISOLATABLE or other.hide_render:
            continue
        saved.append(other)
        other.hide_render = True
    return saved


def _restore_render(saved) -> None:
    for other in saved:
        try:
            other.hide_render = False
        except ReferenceError:
            pass  # the object went away during the bake


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


# The bake settings a Guide Map depends on, and the value each one needs.
#
# They live on the SCENE, so they carry whatever the user last baked. The one
# that bites is Selected to Active: leave it on after baking a normal map from
# a high-poly (the normal thing to do in this very workflow) and every Guide
# Map afterwards dies with "No valid selected objects", because this add-on
# selects exactly one object and that mode wants a source plus a target.
# Measured on the production skirt file: with the flag on, even a hand-rolled
# EMIT bake of a freshly added cube fails; with it off, both the cube and the
# Guide bake. The others are the same class of trap — Target on Color
# Attributes would write into vertex colours instead of the image, and Bake
# from Multires takes a different code path entirely.
_BAKE_SETTINGS = {
    "use_selected_to_active": False,
    "target": 'IMAGE_TEXTURES',
    "use_multires": False,
    "margin_type": 'ADJACENT_FACES',
}


def _force_bake_settings(bake) -> dict:
    """Put the scene's bake settings into the state this add-on needs, and
    return what to put back."""
    saved = {}
    for name, value in _BAKE_SETTINGS.items():
        if not hasattr(bake, name):
            continue          # a Blender version that does not have it
        saved[name] = getattr(bake, name)
        try:
            setattr(bake, name, value)
        except (AttributeError, TypeError, ValueError):
            saved.pop(name, None)
    return saved


def _restore_bake_settings(bake, saved: dict) -> None:
    for name, value in saved.items():
        try:
            setattr(bake, name, value)
        except (AttributeError, TypeError, ValueError):
            pass


def _view3d_spaces():
    """Every 3D viewport of the current screen, or [] in background mode."""
    screen = getattr(bpy.context, "screen", None)
    if screen is None:
        return []
    out = []
    for area in screen.areas:
        if area.type != 'VIEW_3D':
            continue
        for space in area.spaces:
            if space.type == 'VIEW_3D':
                out.append(space)
    return out


def _enter_local_views(obj):
    """Put *obj* into every viewport that is in local view (the / isolate),
    and return what to restore.

    A Guide left out of the isolate is not part of that viewport's evaluated
    set, and the bake cannot reach it — which is exactly how the user hits
    this: isolate the retopo, then press Bake.
    """
    saved = []
    for space in _view3d_spaces():
        if getattr(space, "local_view", None) is None:
            continue
        try:
            was_in = obj.local_view_get(space)
        except Exception:
            continue
        if not was_in:
            try:
                obj.local_view_set(space, True)
            except Exception:
                continue
        saved.append((obj, space, was_in))
    return saved


def _restore_local_views(saved) -> None:
    for ob, space, was_in in saved:
        try:
            ob.local_view_set(space, was_in)
        except Exception:
            pass


# Rays the AO node traces per shading sample, and the Cycles sample count
# layered on top for pixel antialiasing. Same two-level split SimpleBake
# uses (its ao_sample_count / boosted_sample_count), at the values the
# production scene was baked with for the ray count.
# Rays per pixel = AO_BAKE_SAMPLES x AO_NODE_SAMPLES, and that product is
# the whole cost of the AO pass on a CPU (measured on the production skirt
# Guide, 202k verts, 1024px: 2048 rays/px = 27.6 s, 1024 = 15.6 s, 512 =
# 9.6 s — linear). Against a reference at twice the budget the error is RMS
# 0.0018 at 2048 rays, 0.0029 at 1024 and 0.0049 at 512, on a map whose own
# std is 0.379 — so 1024 rays/px costs 0.8% of the signal and half the time.
#
# Cycles caps the AO node's own samples at 128 (measured: 512 and 128 give
# bit-identical maps in the same time, 64 halves the time), so the earlier
# 512 here was silently running as 128. 64 x 16 = the 1024 rays/px above.
AO_NODE_SAMPLES = 64
AO_BAKE_SAMPLES = 16

# Pointiness values that map to pure black and pure white in the Curvature
# pass. Copied from SimpleBake's specials.blend ColorRamp and deliberately
# NOT auto-scaled per mesh: a fixed window is what makes two bakes of two
# different Guides (or the same Guide at two stages) readable side by side.
POINTINESS_RAMP = (0.35, 0.6364)


def _build_emit_shader(nt, out_node, attr_name, shader, ao_distance_m):
    """Wire *out_node*'s Surface for an EMIT bake. No-op when attr_name and
    shader are both None — a native Cycles pass computes from geometry and
    ignores Surface entirely."""
    if attr_name is None and shader is None:
        return
    emit = nt.nodes.new('ShaderNodeEmission')
    nt.links.new(emit.outputs[0], out_node.inputs[0])

    if attr_name is not None:
        attr = nt.nodes.new('ShaderNodeAttribute')
        attr.attribute_name = attr_name
        nt.links.new(attr.outputs['Color'], emit.inputs['Color'])
    elif shader == 'AO':
        ao = nt.nodes.new('ShaderNodeAmbientOcclusion')
        ao.samples = AO_NODE_SAMPLES
        ao.inside = False
        # Guide-only occlusion: the body is not part of the drape, and letting
        # it cast into the map buries the folds under a body-shaped shadow.
        ao.only_local = True
        ao.inputs['Distance'].default_value = ao_distance_m
        nt.links.new(ao.outputs['Color'], emit.inputs['Color'])
    elif shader == 'POINTINESS':
        geo = nt.nodes.new('ShaderNodeNewGeometry')
        ramp = nt.nodes.new('ShaderNodeValToRGB')
        ramp.color_ramp.interpolation = 'LINEAR'
        low, high = POINTINESS_RAMP
        ramp.color_ramp.elements[0].position = low
        ramp.color_ramp.elements[1].position = high
        nt.links.new(geo.outputs['Pointiness'], ramp.inputs['Fac'])
        nt.links.new(ramp.outputs['Color'], emit.inputs['Color'])
    else:
        raise ValueError(f"unknown shader {shader!r}")


def bake_to_image(
    context,
    obj,
    image_name: str,
    resolution: int,
    margin: int = 8,
    attr_name: Optional[str] = None,
    bake_type: str = 'EMIT',
    samples: Optional[int] = None,
    shader: Optional[str] = None,
    ao_distance_m: float = 0.1,
    keep_in_file: bool = True,
) -> Optional[str]:
    """Bake *obj* into the named image (created or resized as needed) using
    the active UV map. All scene/material state is restored afterwards.
    Returns an error string or None.

    The shader driving the EMIT bake is chosen by exactly one of:
      attr_name  — a Color Attribute through Attribute->Emission (Residual/Sag)
      shader='AO'         — Ambient Occlusion node -> Emission (Drape)
      shader='POINTINESS' — Geometry Pointiness -> ColorRamp -> Emission (Drape)
    attr_name=None and shader=None bakes a native Cycles pass instead
    (bake_type then names it), with no shader graph at all.

    samples=None picks a sane default: an Attribute->Emission or Pointiness
    bake is an exact passthrough (no ray variance, 1 sample is correct); the
    AO node traces rays per shading sample and needs pixel-level averaging on
    top, so it gets AO_BAKE_SAMPLES.

    keep_in_file packs the pixels into the .blend (and fake-users the image)
    so the map survives a reload; without it the image is session-only. The
    two move together deliberately: a fake user with NO packed pixels is the
    one state that leaves a black image behind after a reload (measured), so
    it is never used. The pack goes through PNG, which costs about 3 MB for a
    2K map instead of 50 — see _pack_compressed.
    """
    if samples is None:
        if shader == 'AO':
            samples = AO_BAKE_SAMPLES
        elif bake_type == 'EMIT':
            samples = 1
        else:
            samples = 64
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
    prev_device = getattr(scene.cycles, "device", None)
    prev_samples = getattr(scene.cycles, "samples", None)
    prev_margin = scene.render.bake.margin
    prev_use_clear = scene.render.bake.use_clear
    saved_bake = {}
    prev_active = view_layer.objects.active
    prev_selected = [o for o in view_layer.objects if o.select_get()]
    try:
        prev_hide = obj.hide_get()
    except RuntimeError:
        prev_hide = False  # not in the view layer yet (excluded collection)
    prev_hide_render = obj.hide_render
    # Three more ways the object can be out of reach of bpy.ops.object.bake,
    # all of them normal things to do to a Guide while retopologising:
    #   hide_viewport ("Disable in Viewports", the monitor icon) — measured:
    #       the bake fails with "poll() failed, context is incorrect"
    #   hide_select — select_set() cannot select an unselectable object, and
    #       the bake then has no target
    #   local view (the / isolate the user works in) — an object outside it
    #       is not in the viewport's evaluated set
    prev_hide_viewport = obj.hide_viewport
    prev_hide_select = obj.hide_select
    prev_local_view = _enter_local_views(obj)
    # Only for the shader bakes this add-on does (see _isolate_render): a
    # native Cycles pass could legitimately depend on the rest of the scene,
    # so leave the scene alone for those.
    isolate = bake_type == 'EMIT'
    isolated = []
    prev_mats = [slot.material for slot in obj.material_slots]
    had_slots = bool(obj.material_slots)

    bake_mat = bpy.data.materials.new("AC9_BakeMap_Temp")
    bake_mat.use_nodes = True
    nt = bake_mat.node_tree
    nt.nodes.clear()
    out_node = nt.nodes.new('ShaderNodeOutputMaterial')
    _build_emit_shader(nt, out_node, attr_name, shader, ao_distance_m)
    # A native pass needs no shader graph — the bake type computes it from the
    # object's geometry directly, ignoring Surface entirely. The Image Texture
    # node below is still required either way: it is where bpy.ops.object.bake
    # writes the result.
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.image = img
    nt.nodes.active = tex

    saved_collections = []
    try:
        scene.render.engine = 'CYCLES'
        if hasattr(scene, "cycles"):
            scene.cycles.samples = samples
            # A .blend that has never rendered with Cycles carries device =
            # CPU, and nothing in this add-on ever set it: the AO pass then
            # traces its rays (4.3 billion at 2048 with the current budget)
            # on the CPU. Measured on the production skirt Guide: 2 min 54 s
            # with the interface frozen, against 13.6 s on the GPU. Restored
            # in the finally below, like the engine itself.
            if bake_on_gpu():
                scene.cycles.device = 'GPU'

        scene.render.bake.margin = margin
        scene.render.bake.use_clear = True
        saved_bake = _force_bake_settings(scene.render.bake)

        saved_collections = _enable_collections_for(view_layer, obj)
        if isolate:
            isolated = _isolate_render(view_layer, obj)
        obj.hide_set(False)
        obj.hide_render = False
        obj.hide_viewport = False
        obj.hide_select = False
        for o in prev_selected:
            o.select_set(False)
        obj.select_set(True)
        view_layer.objects.active = obj

        if had_slots:
            for slot in obj.material_slots:
                slot.material = bake_mat
        else:
            mesh.materials.append(bake_mat)

        # The console is the only place a message can still appear once
        # bpy.ops.object.bake starts: it is called in EXEC, so the interface
        # is blocked until it returns and no report is drawn before then.
        note = slow_bake_warning(resolution)
        if note:
            print(f"[AC9 Cloth Retopo] {note}")
        forced_gpu = getattr(scene.cycles, "device", 'CPU') == 'GPU' \
            and prev_device != 'GPU'
        result = bpy.ops.object.bake(type=bake_type)
        if 'FINISHED' not in result:
            return f"Bake did not finish ({result}).{_gpu_hint(forced_gpu)}"

        _apply_persistence(img, keep_in_file)
        return None
    except RuntimeError as exc:
        return f"Bake failed: {exc}{_gpu_hint(locals().get('forced_gpu', False))}"
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
        if hasattr(scene, "cycles"):
            if prev_device is not None:
                scene.cycles.device = prev_device
            if prev_samples is not None:
                scene.cycles.samples = prev_samples
        scene.render.bake.margin = prev_margin
        scene.render.bake.use_clear = prev_use_clear
        _restore_bake_settings(scene.render.bake, saved_bake)

        _restore_render(isolated)
        obj.hide_viewport = prev_hide_viewport
        obj.hide_select = prev_hide_select
        _restore_local_views(prev_local_view)
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
# Drape maps (AO and Curvature — the "atari" for 2D knife-cutting)
# ---------------------------------------------------------------------------
#
# What the user was doing by hand, read straight out of the production scene
# 260609_Retopo_JacketClose_01.blend: bake AO and Curvature off the garment
# with SimpleBake, then multiply the two in the shader (Mix node, MULTIPLY,
# Factor 1.0) and read the result on the flat 2D layout as a shaded drape
# reference while cutting panel boundaries.
#
# Both passes are produced the way SimpleBake's "specials" materials produce
# them, because that is the look the user already works from:
#
#   AO         Ambient Occlusion node -> Emission, baked as EMIT.
#              only_local is ON: the Guide occludes itself and nothing else,
#              so the body mesh never darkens the map (the folds are the
#              signal; a body shadow is not).
#   Curvature  Geometry Pointiness -> ColorRamp -> Emission, baked as EMIT.
#              The ramp positions are FIXED (POINTINESS_RAMP, the reference
#              scene's own values) rather than auto-scaled per mesh, so
#              successive bakes stay directly comparable.
#
# Verified headless on Blender 5.0.1, background mode, CPU Cycles (Suzanne
# subdiv 2, 7958 verts, 512px, --factory-startup): the AO node -> Emission
# -> EMIT path returns real, distance-dependent data (covered-pixel mean
# 0.870 / 0.921 / 0.961 and std 0.221 / 0.177 / 0.130 at Distance 1.0 /
# 0.15 / 0.05 m), and Pointiness -> ColorRamp returns mean 0.559 std 0.073 —
# the same profile as the production Curvature map (mean 0.531 std 0.052).
# The addon's earlier note that "native AO baking returns all-zero headless"
# was about bake_type='AO', a different code path in Cycles; driving the AO
# NODE through an emission shader is unaffected by that gap.
#
# Measured against what this replaced, on the production JacketOpen Guide
# (283k verts, 1024px, GPU), covered pixels only:
#
#                     old (per-vertex)   new (shader)   SimpleBake reference
#   AO       mean          0.6516          0.6473            0.6503
#            std           0.3212          0.3512            0.3276
#   Curv     mean          0.8700          0.5339            0.5329
#            std           0.2390          0.0777            0.0772
#   time             27.7 s total       3.4 s total
#
# The old AO was broadly right and merely slow. The old Curvature was not:
# at mean 0.87 it had washed almost white, which is the failure its own
# auto-scale was meant to avoid — the fixed Pointiness ramp lands on the
# reference to three decimal places instead.
#
# The product is baked into a third image rather than left to a Mix node in
# the Preview Plane's material: Solid > Texture viewport shading draws the
# material's active Image Texture node directly and never evaluates the node
# tree, so a node-side multiply is invisible in exactly the shading mode this
# map is looked at in. The ratio is a bake parameter instead (ao_factor).
# AO and Curvature come out as images of their own, so each is inspectable in
# Solid on its own — but they are deleted again once the product exists
# ("Keep Passes" holds on to them). Nothing else needs them: there is no
# recombine-only step, the Drape button always re-bakes both passes, and an
# unpacked 2K float pair sits on 128 MiB of RAM per garment (measured) for a
# file cost of zero.
#
# The maps are 32-bit float in memory — the Curvature pass only spans about a
# quarter of the 0..1 range (measured std 0.078), so the multiply below 1.0
# compresses it further and 8-bit arithmetic would band. What goes INTO the
# .blend is a 16-bit PNG of the result (see _pack_compressed): ~3 MB per 2K
# map, against 50 MB for the raw float buffer. Keeping a
# map in the file is therefore cheap enough to be the default ("Keep in
# file"), and session-only stays available for anyone who would rather press
# Bake again — the whole set re-bakes in under 5 s at 2048 on the production
# JacketOpen Guide (283k verts, GPU).

AO_IMAGE = "AC9_AOMap"
CURVATURE_IMAGE = "AC9_CurvatureMap"
DRAPE_IMAGE = "AC9_DrapeMap"

# ---------------------------------------------------------------------------
# Per-Guide map identity
#
# Several garments (jacket, pants, skirt...) are retopologised side by side in
# one file, with the Guide / Retopo pointers swapped between them. One fixed
# name per map would mean every garment overwrites the previous one's bakes,
# so the image is named "<base>_<Guide name>" and every lookup goes through
# image_name(). Older files carry the bare base name; the matchers below
# accept that (and a numbered copy), so those are still found, listed and
# cleaned up.
#
# The key is the GUIDE alone, including for the Residual map, which is really
# a Guide-vs-Retopo distance: it is the map you re-bake over and over while
# editing one retopo, so a copy per retopo has little value, and a second key
# would split the Baked Maps list along two axes. Which retopo a residual was
# measured against is recorded in RETOPO_PROP on the image instead.
# ---------------------------------------------------------------------------

MAP_BASES = {
    'RESIDUAL': RESIDUAL_IMAGE,
    'SAG': SAG_IMAGE,
    'AO': AO_IMAGE,
    'CURVATURE': CURVATURE_IMAGE,
    'DRAPE': DRAPE_IMAGE,
}
MAP_LABELS = {
    'RESIDUAL': "Residual", 'SAG': "Sag", 'AO': "AO",
    'CURVATURE': "Curvature", 'DRAPE': "Drape",
}
# On the Guide: the object name its maps are currently named after, so a
# rename can carry them along instead of orphaning them.
MAP_KEY_PROP = "ac9_map_key"
# On a Residual image: which retopo it was measured against.
RETOPO_PROP = "ac9_retopo"
# Blender 5.0 ID name limit, measured: 255 characters.
_ID_NAME_MAX = 255


def image_name(map_key: str, guide_obj) -> Optional[str]:
    """Image name for one map of one Guide; None for an unknown key or no
    Guide."""
    base = MAP_BASES.get(map_key)
    if base is None or guide_obj is None:
        return None
    return f"{base}_{guide_obj.name}"[:_ID_NAME_MAX]


def find_image(map_key: str, guide_obj):
    """The existing image for one map of one Guide, or None."""
    name = image_name(map_key, guide_obj)
    return bpy.data.images.get(name) if name else None


def _matches_base(name: str, base: str) -> bool:
    return (name == base or name.startswith(base + "_")
            or name.startswith(base + "."))


def is_map_image(name: str) -> bool:
    """True for an image this tool bakes: a bare base name (older files), a
    per-Guide "<base>_<Guide>", or a numbered copy "<base>.001"."""
    return any(_matches_base(name, b) for b in MAP_BASES.values())


def map_images():
    """Every map image in the file, as a list of (image, map_key)."""
    out = []
    for img in bpy.data.images:
        for key, base in MAP_BASES.items():
            if _matches_base(img.name, base):
                out.append((img, key))
                break
    return out


def map_guide_label(img_name: str) -> str:
    """The Guide name a map image is keyed on ("" for an older unsuffixed
    image)."""
    for base in MAP_BASES.values():
        if img_name.startswith(base + "_"):
            return img_name[len(base) + 1:]
    return ""


def drop_gpu_texture(img) -> None:
    """Let go of the image's GPU texture.

    Every code path here that repacks, rewrites or deletes an image runs
    while a viewport in Solid > Texture (the Preview Plane) or an Image
    Editor may be drawing that very image. None of that was reachable in the
    headless tests, which have no GPU texture at all, so the free is done
    explicitly rather than trusted to the notifier.

    gl_free() only. buffers_free() must NOT be used here: on an image whose
    pixels exist only in memory — which is every map between the bake and
    the pack — it throws the pixels away. Measured: calling it right after
    _combine_drape wrote the product left the map at 0.99999 error against
    the arithmetic, i.e. blank.
    """
    fn = getattr(img, "gl_free", None)
    if fn is None:
        return
    try:
        fn()
    except Exception:
        pass


def _pack_compressed(img) -> None:
    """Embed the map in the .blend as PNG instead of a raw float buffer.

    pack() on a generated float image embeds the buffer as it stands: 50.4 MB
    for a 2K map. Writing it out as PNG first and packing THAT costs 2.8 MB
    for the same map (measured on a synthetic 2K map shaped like a Drape bake:
    smooth field x thin crease lines) — these maps are smooth, so lossless
    compression works on them.

    Blender writes the PNG at 16 bits, and pack() re-reads the image from it,
    so the in-memory float copy carries that quantisation from here on:
    measured 7.7e-06 of the 0..1 range against the float arithmetic on a
    production bake. 65536 levels on a map that is looked at, never measured.

    JPEG would be 0.18 MB, and is deliberately not used: its error (max 0.060
    of the 0..1 range, measured) lands on the pixels either side of the crease
    lines — the lines the Drape map exists to be cut along. Spending 2.6 MB
    beats blurring the thing you are aiming at.

    The PNG only exists as the packed copy: it is written to a temp file, that
    file is packed, and the path is dropped again, so the image stays
    self-contained inside the .blend.
    """
    tmp = os.path.join(tempfile.gettempdir(),
                       f"ac9_map_{os.getpid()}_{id(img):x}.png")
    drop_gpu_texture(img)
    try:
        img.file_format = 'PNG'
        img.filepath_raw = tmp
        img.save()
        img.pack()
    except (RuntimeError, OSError) as exc:
        # Never lose the bake over the packing format.
        print(f"[AC9 Cloth Retopo] PNG pack failed ({exc}); packing raw.")
        try:
            img.pack()
        except RuntimeError:
            pass
    finally:
        img.filepath_raw = ""
        try:
            img.filepath = ""
        except Exception:
            pass
        try:
            os.remove(tmp)
        except OSError:
            pass


def _apply_persistence(img, keep_in_file: bool) -> None:
    """Fake user and packed pixels move together — see bake_to_image's
    docstring. Re-pack on every bake so the embedded copy is the fresh
    result, and unpack when the setting was switched off after an earlier
    bake packed it, or that copy would stay in the file for good."""
    img.use_fake_user = keep_in_file
    if keep_in_file:
        _pack_compressed(img)
    elif img.packed_file is not None:
        try:
            img.unpack(method='REMOVE')
        except RuntimeError:
            pass


def follow_guide_rename(guide_obj) -> int:
    """Carry a Guide's existing maps over to its current name. Returns how
    many images were renamed.

    Guarded on the old name no longer naming an object: Shift+D copies the
    idprop too, so without that check a duplicate's first bake would rename
    the ORIGINAL Guide's maps onto itself and the two would share one set.
    """
    if guide_obj is None:
        return 0
    old = guide_obj.get(MAP_KEY_PROP)
    new = guide_obj.name
    n = 0
    if old and old != new and bpy.data.objects.get(old) is None:
        for base in MAP_BASES.values():
            img = bpy.data.images.get(f"{base}_{old}"[:_ID_NAME_MAX])
            if img is None:
                continue
            wanted = f"{base}_{new}"[:_ID_NAME_MAX]
            if bpy.data.images.get(wanted) is None:
                img.name = wanted
                n += 1
    guide_obj[MAP_KEY_PROP] = new
    return n


def _gpu_hint(forced_gpu: bool) -> str:
    """What to try next when a bake we moved onto the GPU fails.

    The add-on cannot know how much VRAM a given machine has, or whether its
    driver will hold up under a 4K bake, so a failure has to name the switch
    that puts the bake back on the device the .blend carries.
    """
    if not forced_gpu:
        return ""
    return (" This bake was moved to the GPU: try a lower Resolution, or turn "
            "off 'Bake on GPU when available' in the add-on preferences to "
            "use the scene's own device.")


def gpu_available() -> bool:
    """True when Cycles has a non-CPU device switched on in Preferences.

    Only the question "is there one" matters here: which backend it is
    (OPTIX / CUDA / HIP / METAL / ONEAPI) is already the user's choice in
    Preferences > System, and scene.cycles.device only takes 'CPU' or 'GPU'.
    """
    try:
        cp = bpy.context.preferences.addons["cycles"].preferences
    except (KeyError, AttributeError):
        return False
    kind = getattr(cp, "compute_device_type", 'NONE')
    if kind in {'NONE', None}:
        return False
    try:
        cp.get_devices()
    except Exception:
        pass
    try:
        return any(d.use and d.type != 'CPU' for d in cp.devices)
    except Exception:
        return False


# The add-on's own key in preferences.addons. Installed as an extension the
# package is "bl_ext.user_default.ac9_cloth_retopo.bake_maps", so the add-on
# is this module's parent — not the first component, which is "bl_ext".
_ADDON_PKG = __package__.rpartition(".")[0]


def addon_prefs():
    """The add-on's preferences, or None when it is not registered as an
    add-on at all (a bare `--python` script run, and the headless tests)."""
    try:
        return bpy.context.preferences.addons[_ADDON_PKG].preferences
    except (KeyError, AttributeError):
        return None


def bake_on_gpu() -> bool:
    """Whether this bake should switch the scene to the GPU: the add-on
    preference says so and a GPU is actually set up."""
    prefs = addon_prefs()
    if prefs is None:
        # Preferences unavailable (a bare `--python` run): keep the old
        # behaviour of using whatever the scene carries.
        return False
    if not getattr(prefs, "bake_on_gpu", True):
        return False
    return gpu_available()


def slow_bake_warning(resolution: int) -> str:
    """The line to show BEFORE a bake that is going to freeze the interface
    for minutes, or "" when it will not.

    The interface is blocked for the whole of bpy.ops.object.bake (it is
    called in EXEC, not as a job), so on the CPU a 2048 map still reads as a
    hang: measured 67.6 s on a 202k-vert Guide (it was 2 min 54 s in the real
    GUI run before the ray budget was halved and the scene isolated), against
    12.4 s on the GPU.
    """
    if resolution < 2048 or bake_on_gpu():
        return ""
    # Deliberately no duration: it is the user's CPU, and the figure measured
    # on one machine would be wrong for most of them. What they need to know
    # is that the interface will be frozen and that there is a faster path.
    return (f"CPU bake at {resolution}: the interface stays frozen until it "
            f"finishes (no progress, no cancel). Enable a GPU in "
            f"Preferences > System, or use 1024.")


def packed_megabytes(img) -> float:
    """What this image costs in the .blend right now: its packed size in MB,
    0.0 when it is session-only (nothing packed)."""
    pf = img.packed_file
    return (pf.size / 1e6) if pf is not None else 0.0


def purge_session_maps() -> list:
    """Drop map images that cannot carry their pixels through a reload —
    unpacked, with no file on disk. Called from load_post: such an image is
    already black by then (measured), so nothing is lost, and the
    alternative is a black datablock that reads as a broken bake.

    A map the user saved to disk themselves (a real filepath) is kept — that
    one reloads with its pixels.
    """
    doomed = [img for img, _key in map_images()
              if img.packed_file is None and not img.filepath]
    names = [img.name for img in doomed]
    for img in doomed:
        bpy.data.images.remove(img)
    return names


@contextmanager
def _guide_in_3d(guide_obj):
    """Temporarily blend the Guide back to its 3D shape, then restore every
    ShapeKey value (and mute flag) on the way out.

    Cycles bakes the EVALUATED mesh, so with the Flat SK at 1.0 — this
    addon's normal 2D working view — an AO or Pointiness bake would read a
    dead-flat sheet: no folds, no creases, a uniform map. UVs are not
    affected by shape keys, so zeroing the flat key gives 3D-shaped shading
    laid out on the flat pattern UVs, which is exactly what the hand-made
    reference maps in the production scene contain.
    """
    keys = guide_obj.data.shape_keys
    if keys is None:
        yield
        return
    target = guide_3d_shapekey(guide_obj.data)
    saved = [(kb, kb.value, kb.mute) for kb in keys.key_blocks]
    try:
        for kb in keys.key_blocks:
            if kb.name == target:
                kb.mute = False
                kb.value = 1.0
            else:
                kb.value = 0.0
        bpy.context.view_layer.update()
        yield
    finally:
        for kb, value, mute in saved:
            kb.value = value
            kb.mute = mute
        bpy.context.view_layer.update()


def _read_pixels(img) -> np.ndarray:
    buf = np.empty(len(img.pixels), dtype=np.float32)
    img.pixels.foreach_get(buf)
    return buf


def _combine_drape(guide_obj, resolution: int, ao_factor: float,
                   keep_in_file: bool) -> None:
    """Write Curvature x AO into the Guide's Drape image.

    Same arithmetic as a Mix node set to MULTIPLY with Factor = ao_factor and
    Curvature in A, AO in B:  A * (1 - f + f * B). At f = 1 that is a plain
    multiply; below 1 the AO's contribution is eased off, which is the point —
    a full-strength multiply buries the Curvature creases (the thing you
    actually cut along) under the AO's dark folds.
    """
    curv = _read_pixels(find_image('CURVATURE', guide_obj))
    ao = _read_pixels(find_image('AO', guide_obj))
    combined = np.clip(curv * (1.0 - ao_factor + ao_factor * ao), 0.0, 1.0)
    combined[3::4] = 1.0  # opaque — alpha means nothing for either source pass

    name = image_name('DRAPE', guide_obj)
    img = bpy.data.images.get(name)
    if img is None:
        img = bpy.data.images.new(
            name, resolution, resolution, alpha=False, float_buffer=True
        )
        img.colorspace_settings.name = 'Non-Color'
    elif tuple(img.size) != (resolution, resolution):
        img.scale(resolution, resolution)
    img.pixels.foreach_set(combined)
    img.update()
    # The product is written straight into the pixels, which a viewport in
    # Solid > Texture would otherwise keep drawing from its old GPU copy.
    # _pack_compressed does this too, but only when the map is kept in file.
    drop_gpu_texture(img)
    _apply_persistence(img, keep_in_file)


def bake_drape_maps(
    context,
    guide_obj,
    resolution: int,
    ao_distance_m: float,
    ao_factor: float = 0.7,
    margin: int = 8,
    keep_in_file: bool = True,
    keep_passes: bool = False,
) -> Optional[str]:
    """Bake the drape reference off the Guide's 3D shape onto its flat UV
    layout: the AO and Curvature passes, and their product in the Drape map.
    Returns an error string or None.

    The two passes are session-only whatever `keep_in_file` says (they are
    always re-baked together with the product, never on their own), and are
    deleted again once the product exists unless `keep_passes` keeps them
    around to be inspected.
    """
    try:
        with _guide_in_3d(guide_obj):
            err = bake_to_image(
                context, guide_obj, image_name('AO', guide_obj), resolution,
                margin, shader='AO', ao_distance_m=ao_distance_m,
                keep_in_file=False,
            )
            if err is not None:
                return err
            err = bake_to_image(
                context, guide_obj, image_name('CURVATURE', guide_obj),
                resolution, margin, shader='POINTINESS', keep_in_file=False,
            )
            if err is not None:
                return err
        _combine_drape(guide_obj, resolution, ao_factor, keep_in_file)
    finally:
        # In the finally, not after the combine: an AO pass that succeeded
        # before the Curvature pass failed would otherwise be left behind as
        # a stray "AO · session" row in Baked Maps that nothing can use (with
        # Keep Passes off it is not even offered in the Preview).
        if not keep_passes:
            # Removing the ID also clears any Image Texture node pointing at
            # it (measured), so the Preview Plane cannot be left showing a
            # dead image.
            for key in ('AO', 'CURVATURE'):
                img = find_image(key, guide_obj)
                if img is not None:
                    drop_gpu_texture(img)
                    bpy.data.images.remove(img)
    return None
