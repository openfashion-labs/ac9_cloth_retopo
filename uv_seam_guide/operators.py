"""Operators for the UV Seam Guide tool."""

import json
import math
import time

import bpy
import bmesh
from bpy.props import BoolProperty
from bpy.types import Operator
from bpy_extras.view3d_utils import region_2d_to_location_3d
from mathutils import Vector

from .. import ui_common as uic
from ..clo_projector import guide as _pguide
from .analysis import (
    detect_island_symmetry,
    detect_island_symmetry_cached,
    detect_mirror_pairs,
    detect_mirror_pairs_cached,
    find_boundary_segments_flat,
    find_crease_segments_flat,
    find_marked_seam_pairs,
    find_seam_pairs_flat,
)
from .ghost import build_pair_kd, build_segment_kd, nearest_segment_point
from .gpu_overlay import (
    _batches,
    _cache,
    clear_ghost_batches,
    clear_mirror_pair_batches,
    clear_seam_batches,
    clear_symmetry_batches,
    build_ghost_batches,
    build_crease_batch,
    build_free_edge_batch,
    build_mirror_pair_batches,
    build_seam_batches,
    build_snap_ring_batch,
    build_symmetry_batches,
    rebuild_ghosts,
    _tag_redraw_3d,
)


def _get_top(context):
    """Return the top-level AC9ClothRetopoProps, or None."""
    return getattr(context.scene, "ac9_cloth_retopo", None)


def _write_report(text_name, lines, guide, retopo):
    """Write a report Text, stamped with the inputs it was made from.

    One Text per report, rewritten on every run: the tables are read right
    after pressing the button, so keeping one per garment would only leave
    stale ones lying around. What the header buys is knowing WHOSE table is
    on screen — with several garments in a file, an unlabelled table is
    unreadable a minute later.
    """
    stamp = time.strftime("%Y-%m-%d %H:%M")
    guide_name = guide.name if guide is not None else "-"
    retopo_name = retopo.name if retopo is not None else "-"
    header = [f"# Guide: {guide_name}   Retopo: {retopo_name}   {stamp}", ""]
    text = bpy.data.texts.get(text_name) or bpy.data.texts.new(text_name)
    text.clear()
    text.write("\n".join(header + list(lines)))
    return text


def _resolve_guide(top):
    """Return (guide_obj, flat_sk_name, error) from the shared top-level props.

    The Guide is shared between the seam guide and the CLO Projector so seam
    pairs and the retopo live in the same Flat-SK space.

    The presence checks are answered here so the messages stay short for the
    two settings the user sets by hand; everything about the Guide's own state
    is delegated to the projector's validate_guide, which is the one gate every
    reader of the Flat SK has to agree on. That matters for the scale checks it
    carries: a Flat SK built under an unapplied object scale is 1000x out in
    world space, and this module reads flat-space distances all over (ghosts,
    symmetry scan, seam pairing), so letting it through here would produce
    silently wrong overlays rather than an error.
    """
    guide = top.guide_obj
    flat_sk = top.guide_flat_shapekey
    if guide is None:
        return None, "", "Guide is not set."
    if guide.type != 'MESH':
        return None, "", "Guide must be a mesh object."
    if not flat_sk:
        return None, "", "Flat SK is not set."
    if guide.data.shape_keys is None or flat_sk not in guide.data.shape_keys.key_blocks:
        return None, "", f"Flat ShapeKey '{flat_sk}' not found on Guide '{guide.name}'."
    err = _pguide.validate_guide(guide, flat_sk)
    if err is not None:
        return None, "", err
    return guide, flat_sk, None


def _flat_len(real_len, guide, flat_sk):
    """A real-space length in the flat layout's own units.

    Every tolerance below that is compared against flat coordinates -- how close
    a retopo boundary vertex is to a Guide seam, how straight a stretch of
    outline is -- is authored as a real fabric dimension and has to be scaled by
    the UV packing before use. Create Flat SK stores the raw UV as geometry with
    no normalisation, so the flat layout's scale is whatever the packing happens
    to be: measured 1.0023 for a jacket whose UV fills the square against 0.3318
    for a whole outfit packed into one square, a factor of 3.02 (AUDIT
    §8-B / §8-D-1). Without this the same setting means three times the fabric
    distance on the second Guide.

    Spacing is deliberately NOT routed through here: division_parameters divides
    by 3D arc length because the two sides of a gathered seam have the same 3D
    length but differ in the pattern by up to 18.6% (measured), and dividing by
    the pattern length would give them different vertex counts and break the
    1:1 seam match.
    """
    return real_len * _pguide.flat_scale(guide, flat_sk)


def _analyze_into_cache(guide, flat_sk, precision, z_offset, match_distance=0.0,
                        marked_distance=0.0):
    """Run seam-pair detection and refresh the seam cache + KDTree + batches."""
    pairs = find_seam_pairs_flat(
        guide, flat_sk, precision_flat=int(precision), match_distance=match_distance
    )
    # Explicit layered seams: Mark Sharp edges on the guide projected onto the
    # nearest other panel (pocket → body). No-op when nothing is marked.
    if marked_distance > 0.0:
        marked = find_marked_seam_pairs(guide, flat_sk, max_distance=marked_distance)
        if marked:
            pairs.extend(marked)
            print(f"[AC9 Seam] +{len(marked)} marked (sharp) seam pairs.")
    _cache["pairs"] = pairs
    _cache["pair_kd"], _cache["pair_kd_entries"] = build_pair_kd(pairs)
    # Full pattern outline (sewn seams + free edges) for UV Seam Snap.
    segments = find_boundary_segments_flat(guide, flat_sk)

    def _seg_key(p1, p2):
        return frozenset(((round(p1.x, 6), round(p1.y, 6)),
                          (round(p2.x, 6), round(p2.y, 6))))

    # FREE edges = boundary edges that are not a side of any sewn pair. Cut
    # them out BEFORE the internal sew lines are appended below, or a pocket
    # attach line (an interior edge, never a free edge) would land in the set.
    pair_keys = set()
    for pr in pairs:
        pair_keys.add(_seg_key(pr["a_p1"], pr["a_p2"]))
        pair_keys.add(_seg_key(pr["b_p1"], pr["b_p2"]))
    free_segments = [(p1.copy(), p2.copy()) for p1, p2 in segments
                     if _seg_key(p1, p2) not in pair_keys]
    _cache["free_segments"] = free_segments
    _cache["free_kd"] = build_segment_kd(free_segments) if free_segments else None

    # Internal sew lines (e.g. the pocket attach line on the body panel) are
    # pair sides that are NOT boundary edges — add them so UV Seam Snap can
    # land body-side retopo verts on them too.
    seen_segs = {_seg_key(p1, p2) for p1, p2 in segments}
    for pr in pairs:
        for p1, p2 in ((pr["a_p1"], pr["a_p2"]), (pr["b_p1"], pr["b_p2"])):
            k = _seg_key(p1, p2)
            if k not in seen_segs:
                seen_segs.add(k)
                segments.append((p1.copy(), p2.copy()))
    _cache["boundary_segments"] = segments
    _cache["boundary_kd"] = build_segment_kd(segments) if segments else None
    # Find Folds creases tagged on the Guide (CLO Cleanup): the Creases
    # overlay and the crease pool of Outline Snap read these.
    crease_segments = find_crease_segments_flat(guide, flat_sk)
    _cache["crease_segments"] = crease_segments
    _cache["crease_kd"] = build_segment_kd(crease_segments) if crease_segments else None
    build_crease_batch(crease_segments, z_offset)
    _cache["seam_dirty"] = False
    build_seam_batches(pairs, z_offset)
    build_free_edge_batch(free_segments, z_offset)
    print(f"[AC9 Seam] {len(free_segments)} free boundary edges "
          f"(no sewn partner) of {len(segments)} outline segments.")
    print(f"[AC9 Seam] {len(pairs)} seam pairs from guide '{guide.name}' (Flat SK '{flat_sk}').")
    return pairs


class AC9_OT_analyze_uv_seams(Operator):
    bl_idname = "ac9_cloth.analyze_uv_seam_pairs"
    bl_label = "Analyze Seams"
    bl_description = (
        "Find the sewn seam pairs on the Guide (in Flat SK space) and build the "
        "seam overlay. Ghosts, snapping and the seam overlays all read this "
        "result. It is cleared on file open and on Reload Scripts"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        top = _get_top(context)
        if top is None:
            self.report({'ERROR'}, "AC9 Cloth Retopo properties not found.")
            return {'CANCELLED'}
        props = top.seam
        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        wm = context.window_manager
        with uic.ProgressScope(wm) as prog:
            try:
                prog.update(0.10)
                pairs = _analyze_into_cache(guide, flat_sk, props.precision, props.z_offset,
                                           props.match_distance_3d,
                                           props.marked_project_distance if props.use_marked_seams else 0.0)
                prog.update(0.90)
            except Exception as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}

        context.scene["ac9_cloth_retopo_seam_pair_count"] = len(pairs)

        if props.live_ghost_update and top.retopo_obj is not None:
            rebuild_ghosts(props, top.retopo_obj)

        _tag_redraw_3d(context)
        self.report({'INFO'}, f"Found {len(pairs)} seam pair edges (Flat-SK space).")
        return {'FINISHED'}


class AC9_OT_refresh_ghosts(Operator):
    bl_idname = "ac9_cloth.refresh_ghosts"
    bl_label = "Refresh Ghosts"
    bl_description = (
        "Recompute the opposite-side ghost points from the retopo's current "
        "boundary. Runs Analyze Seams first if no seam analysis is loaded"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        top = _get_top(context)
        if top is None:
            self.report({'ERROR'}, "AC9 Cloth Retopo properties not found.")
            return {'CANCELLED'}
        props = top.seam
        retopo_obj = top.retopo_obj

        if retopo_obj is None:
            self.report({'ERROR'}, "Retopo is not set.")
            return {'CANCELLED'}
        if retopo_obj.type != 'MESH':
            self.report({'ERROR'}, "Retopo must be a mesh object.")
            return {'CANCELLED'}

        wm = context.window_manager
        with uic.ProgressScope(wm) as prog:
            if not _cache["pairs"]:
                guide, flat_sk, err = _resolve_guide(top)
                if err is not None:
                    self.report({'ERROR'}, err)
                    return {'CANCELLED'}
                try:
                    pairs = _analyze_into_cache(guide, flat_sk, props.precision, props.z_offset,
                                           props.match_distance_3d,
                                           props.marked_project_distance if props.use_marked_seams else 0.0)
                except Exception as e:
                    self.report({'ERROR'}, str(e))
                    return {'CANCELLED'}
                context.scene["ac9_cloth_retopo_seam_pair_count"] = len(pairs)
            prog.update(0.1)

            count, total = rebuild_ghosts(
                props, retopo_obj,
                progress=uic.ProgressThrottle(wm, lo=10, hi=100),
            )
        _tag_redraw_3d(context)
        n_free = sum(1 for r in _cache["ghost_results"]
                     if r.get("side") == "free")
        note = f" ({n_free} on free edges)" if n_free else ""
        self.report(
            {'INFO'},
            f"Ghost overlay: {count} ghosts{note} from {total} boundary "
            f"retopo vertices.",
        )
        return {'FINISHED'}


def _get_retopo_selected_world(retopo_obj):
    """World-space positions of selected verts on the RETOPO object.

    Reads the live BMesh if the retopo is itself in Edit Mode, otherwise the
    stored mesh selection.  Always uses retopo_obj — never context.edit_object —
    so the diagnostic targets the right mesh even when another object (e.g. the
    high-poly guide) is the one open in Edit Mode.
    """
    matrix = retopo_obj.matrix_world
    out = []
    if retopo_obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(retopo_obj.data)
        bm.verts.ensure_lookup_table()
        for v in bm.verts:
            if v.select:
                out.append(matrix @ v.co)
    else:
        for v in retopo_obj.data.vertices:
            if v.select:
                out.append(matrix @ v.co)
    return out


class AC9_OT_inspect_seam_vertex(Operator):
    bl_idname = "ac9_cloth.inspect_seam_vertex"
    bl_label = "Inspect Selected Vertex"
    bl_description = (
        "Select one vertex on the RETOPO mesh (the object set as 'Retopo', "
        "not the guide) and run this to print its matched seam pair to the system "
        "console. Use it to confirm a known pair lands where you expect"
    )
    bl_options = {'REGISTER'}

    def execute(self, context):
        top = _get_top(context)
        if top is None or top.retopo_obj is None:
            self.report({'ERROR'}, "Retopo is not set.")
            return {'CANCELLED'}
        props = top.seam
        retopo = top.retopo_obj

        if not _cache["pairs"]:
            guide, flat_sk, err = _resolve_guide(top)
            if err is not None:
                self.report({'ERROR'}, err)
                return {'CANCELLED'}
            try:
                _analyze_into_cache(guide, flat_sk, props.precision, props.z_offset,
                                       props.match_distance_3d,
                                       props.marked_project_distance if props.use_marked_seams else 0.0)
            except Exception as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}

        sel = _get_retopo_selected_world(retopo)
        if len(sel) != 1:
            self.report(
                {'ERROR'},
                f"Select exactly one vertex on the RETOPO mesh '{retopo.name}' "
                f"(currently {len(sel)} selected).",
            )
            return {'CANCELLED'}

        co_world = sel[0]
        from .ghost import find_opposite_points_for_retopo_vertices
        res = find_opposite_points_for_retopo_vertices(
            [co_world], _cache["pairs"], max_distance=1e9,
            kd=_cache["pair_kd"], entries=_cache["pair_kd_entries"],
            n_candidates=64,
        )
        if not res:
            self.report({'WARNING'}, "No seam match found for this vertex.")
            return {'FINISHED'}
        r = res[0]
        msg = (
            f"vert(world)=({co_world.x:.4f}, {co_world.y:.4f})  →  "
            f"opposite=({r['opposite_pt'].x:.4f}, {r['opposite_pt'].y:.4f})  "
            f"dist={r['distance']:.5f}  side={r['side']}"
        )
        print("[AC9 Seam Inspect]", msg)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def _bbox(points):
    if not points:
        return None
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


class AC9_OT_diagnose_spaces(Operator):
    bl_idname = "ac9_cloth.diagnose_seam_spaces"
    bl_label = "Diagnose Spaces"
    bl_description = (
        "Print the world-space bounding boxes of the detected seam pairs, the "
        "retopo boundary verts, and the guide's flat layout to the system console. "
        "If the seam box and retopo box do not overlap, they are in different "
        "coordinate spaces (a matrix_world / normalisation mismatch)"
    )
    bl_options = {'REGISTER'}

    def execute(self, context):
        top = _get_top(context)
        if top is None or top.retopo_obj is None:
            self.report({'ERROR'}, "Retopo is not set.")
            return {'CANCELLED'}
        props = top.seam
        retopo = top.retopo_obj

        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        if not _cache["pairs"]:
            try:
                _analyze_into_cache(guide, flat_sk, props.precision, props.z_offset,
                                       props.match_distance_3d,
                                       props.marked_project_distance if props.use_marked_seams else 0.0)
            except Exception as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}

        from mathutils import Vector
        seam_pts = []
        for p in _cache["pairs"]:
            seam_pts += [p["a_p1"], p["a_p2"], p["b_p1"], p["b_p2"]]
        seam_box = _bbox(seam_pts)

        from .ghost import get_retopo_boundary_vertex_positions
        retopo_pts = get_retopo_boundary_vertex_positions(retopo)
        retopo_box = _bbox(retopo_pts)

        gm = guide.matrix_world
        mesh = guide.data
        flat_data = mesh.shape_keys.key_blocks[flat_sk].data
        flat_pts = [gm @ flat_data[i].co for i in range(len(mesh.vertices))]
        flat_box = _bbox(flat_pts)

        def _fmt(b):
            if b is None:
                return "EMPTY"
            return f"x[{b[0]:.4f},{b[2]:.4f}] y[{b[1]:.4f},{b[3]:.4f}]"

        print("=" * 60)
        print("[AC9 Seam Diagnose]")
        print(f"  guide '{guide.name}'  retopo '{retopo.name}'")
        print(f"  guide.matrix_world identity? {gm == gm.Identity(4)}")
        print(f"  retopo.matrix_world identity? "
              f"{retopo.matrix_world == retopo.matrix_world.Identity(4)}")
        print(f"  seam-pair box   (world): {_fmt(seam_box)}  ({len(_cache['pairs'])} pairs)")
        print(f"  guide-flat box  (world): {_fmt(flat_box)}")
        print(f"  retopo bnd box  (world): {_fmt(retopo_box)}  ({len(retopo_pts)} bnd verts)")
        print("=" * 60)

        overlap = "UNKNOWN"
        if seam_box and retopo_box:
            sep_x = seam_box[0] > retopo_box[2] or retopo_box[0] > seam_box[2]
            sep_y = seam_box[1] > retopo_box[3] or retopo_box[1] > seam_box[3]
            overlap = "NO — different spaces!" if (sep_x or sep_y) else "yes (overlap)"
        self.report({'INFO'}, f"Seam/retopo boxes overlap: {overlap}. See console.")
        return {'FINISHED'}


class AC9_OT_clear_uv_seam_guides(Operator):
    bl_idname = "ac9_cloth.clear_uv_seam_guides"
    bl_label = "Clear Seams"
    bl_description = "Clear the seam analysis: cached seam pairs, seam overlay and ghosts"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # One list, in gpu_overlay — this copy used to leave the Creases
        # batch behind (and the ghosts' snap rings with it).
        clear_seam_batches()
        clear_ghost_batches()
        for key in ("ac9_cloth_retopo_seam_pair_count", "ac9_cloth_retopo_ghost_count"):
            if key in context.scene:
                del context.scene[key]
        _tag_redraw_3d(context)
        self.report({'INFO'}, "Seam guide overlay cleared.")
        return {'FINISHED'}


class AC9_OT_clear_ghost_points(Operator):
    bl_idname = "ac9_cloth.clear_ghost_points"
    bl_label = "Clear Ghost Points"
    bl_description = "Clear the ghost point overlay"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        clear_ghost_batches()
        if "ac9_cloth_retopo_ghost_count" in context.scene:
            del context.scene["ac9_cloth_retopo_ghost_count"]
        _tag_redraw_3d(context)
        self.report({'INFO'}, "Ghost overlay cleared.")
        return {'FINISHED'}


class AC9_OT_ghost_force_bond(Operator):
    bl_idname = "ac9_cloth.ghost_force_bond"
    bl_label = "Force Bond Selected"
    bl_description = (
        "Snap each SELECTED retopo vertex exactly onto its nearest ghost when "
        "within Snap Distance (the same radius as Ghost Snap on G). "
        "Non-destructive: selected verts with no ghost in range — and all "
        "unselected verts — are left untouched. Use to finish seams you placed "
        "roughly but forgot to snap exactly. A free edge's ghost is the point "
        "on the outline the vertex belongs on, fixed at the last Refresh: it "
        "does not follow a vertex as you drag it, so to walk a vertex along a "
        "hem use Outline Snap instead"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if context.mode != 'EDIT_MESH':
            return False
        top = getattr(getattr(context, 'scene', None), 'ac9_cloth_retopo', None)
        if top is None:
            return False
        return bool(_cache["ghost_results"])

    def execute(self, context):
        obj = context.edit_object
        if obj is None:
            self.report({'ERROR'}, "No active edit object.")
            return {'CANCELLED'}
        top = _get_top(context)
        props = top.seam
        ghosts = _cache["ghost_results"]
        if not ghosts:
            self.report({'WARNING'}, "No ghosts — run Refresh Ghosts first.")
            return {'CANCELLED'}

        # The pull radius is Snap Distance — the radius the user already tuned
        # for the interactive Ghost Snap — not Bond Distance. Bond Distance is
        # the 1 mm classification threshold (placed → green); using it here
        # made Force Bond silently skip every vertex placed by hand a few mm
        # off its ghost, which read as "the button does nothing".
        # Real fabric mm -> flat layout, like every other length that ends up
        # measured against the Flat SK. Without it the pull radius meant 3.02x
        # the fabric distance on a tightly packed layout (AUDIT 8-B).
        bond = props.snap_distance * _pguide.scene_flat_scale()
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        matrix = obj.matrix_world
        inv = matrix.inverted()

        # "Already there" is the green threshold: a vertex within Bond
        # Distance of its nearest ghost IS that ghost's placed partner, so
        # pulling it moves it by less than the tolerance that called it
        # placed. Counted apart from `moved` because the two read completely
        # differently to the user: one is "I did something", the other is
        # "there was nothing to do here". Reported without that arithmetic
        # (a vertex can carry a second ghost of the other kind a millimetre
        # away — see rebuild_ghosts on anchors — and naming distances that
        # Force Bond did not act on only invites chasing them).
        on_it = props.bond_distance * _pguide.scene_flat_scale()

        moved = 0
        already = 0
        skipped = 0
        for v in bm.verts:
            if not v.select:
                continue
            wc = matrix @ v.co
            best_d = float('inf')
            best_g = None
            for ghost in ghosts:
                g = ghost["opposite_pt"]
                d = math.hypot(wc.x - g.x, wc.y - g.y)
                if d < best_d:
                    best_d = d
                    best_g = g
            if best_g is None or best_d > bond:
                skipped += 1
                continue
            if best_d <= on_it:
                already += 1
                continue
            # Match XY exactly; keep the vert's own Z (flat layout is planar,
            # same as Ghost Snap Move).
            lp = inv @ Vector((best_g.x, best_g.y, wc.z))
            v.co.x = lp.x
            v.co.y = lp.y
            moved += 1

        # Vertices only moved — no topology change — so the default update is
        # safe (no loop_triangles rebuild needed).
        bmesh.update_edit_mesh(obj.data)

        # Re-classify so the just-bonded ghosts flip to green immediately.
        rebuild_ghosts(props, top.retopo_obj)
        _tag_redraw_3d(context)
        parts = [f"Force-bonded {moved} vertex(es)"]
        if already:
            parts.append(f"{already} was/were already on their nearest ghost")
        if skipped:
            parts.append(f"{skipped} had no ghost within Snap Distance "
                         f"({bond * 1000:.1f} mm)")
        self.report({'INFO'} if moved else {'WARNING'},
                    "; ".join(parts) + ".")
        return {'FINISHED'}


def _density_is_on(cls, context):
    """False (with the reason) when the Density family is switched off.

    Adjust Density (+-1 / Count / Spacing), Even Out, Pins and Corners are
    Experimental since 2026-09-09: they went unused through the production
    run, and the family is closed — the two attributes it writes
    (ac9_density_pin, ac9_topo_corner) are read by span_density alone, so
    hiding them takes nothing away from Generate, Match, Status or Grid.
    """
    if not uic.experimental_enabled(context):
        cls.poll_message_set(
            "Density, Pins and Corners are Experimental "
            "(Add-on Preferences > Experimental tools).")
        return False
    return True


def _run_sub_op(op, label):
    """Call a sub-operator and answer True/False instead of raising.

    `bpy.ops.*()` RAISES RuntimeError when the operator it called reported an
    ERROR and returned CANCELLED — it does not hand back {'CANCELLED'} — so
    `'FINISHED' in bpy.ops...()` never got the chance to be False, and the
    "partial failure is reported, not hidden" handling below it never ran.
    What the user saw instead was two or three nested Python tracebacks
    (measured 2026-09-09 on a Guide holding NaN: the innermost message,
    "arange: cannot compute length", was the only useful line in 20).
    """
    try:
        return 'FINISHED' in op('EXEC_DEFAULT')
    except RuntimeError as exc:
        # The sub-operator has already reported its own message to the user;
        # this only records which step it was.
        print("[AC9] %s failed: %s" % (label, exc))
        return False


class AC9_OT_detect_symmetry(Operator):
    bl_idname = "ac9_cloth.detect_island_symmetry"
    bl_label = "Detect Folds"
    bl_description = (
        "Find self-symmetric (cut-on-fold) UV islands on the Guide and draw "
        "each one's fold (centre) line. Tests every island's outline shape for "
        "bilateral symmetry — independent of 3D drape and UV placement. "
        "Self-symmetric panels (back body, waistband, plackets, cuffs) get a "
        "fold line; left/right twins (sleeves, front panels) do not — those are "
        "found by Detect Twins"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        top = _get_top(context)
        if top is None:
            self.report({'ERROR'}, "AC9 Cloth Retopo properties not found.")
            return {'CANCELLED'}
        props = top.seam
        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        wm = context.window_manager
        with uic.ProgressScope(wm) as prog:
            try:
                segments, n_tested, n_sym, folds = detect_island_symmetry_cached(
                    guide, flat_sk, tolerance=props.symmetry_tolerance, force=True,
                    progress=prog.update,
                )
            except Exception as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}

        _cache["symmetry_segments"] = segments
        _cache["symmetry_kd"] = build_segment_kd(segments) if segments else None
        # Symmetrize Island reads this: which Guide island root each fold
        # line belongs to, and its axis (a line through "a"/"b" — NOT world
        # x=0/y=0; the axis passes through the island's own centroid).
        _cache["symmetry_folds"] = folds
        build_symmetry_batches(segments, props.z_offset)
        context.scene["ac9_cloth_retopo_symmetry_count"] = n_sym

        print(f"[AC9 Seam] symmetry: {n_sym}/{n_tested} islands self-symmetric "
              f"(tol {props.symmetry_tolerance:.4f}).")
        _tag_redraw_3d(context)
        msg = f"{n_sym} of {n_tested} islands are self-symmetric (fold lines drawn)."
        # Tell, don't force: a detection that found something the user can't
        # currently see (overlay toggled off, e.g. by an "All Off" preset)
        # has bitten this project before — a real detection was mistaken for
        # a miss because Fold Lines happened to be off. Forcing the toggle
        # on would fight that same "All Off" preset instead; a message is
        # enough for the user to flip it themselves.
        if n_sym > 0 and not props.show_symmetry_axis:
            msg += " Fold Lines overlay is currently OFF — enable it to see them."
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AC9_OT_clear_symmetry(Operator):
    bl_idname = "ac9_cloth.clear_island_symmetry"
    bl_label = "Clear Folds"
    bl_description = "Clear the detected fold lines"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        clear_symmetry_batches()
        if "ac9_cloth_retopo_symmetry_count" in context.scene:
            del context.scene["ac9_cloth_retopo_symmetry_count"]
        _tag_redraw_3d(context)
        self.report({'INFO'}, "Fold-line overlay cleared.")
        return {'FINISHED'}


class AC9_OT_detect_mirror_pairs(Operator):
    bl_idname = "ac9_cloth.detect_mirror_pairs"
    bl_label = "Detect Twins"
    bl_description = (
        "Find twins: separate UV islands that are left/right reflections of "
        "each other (e.g. a left sleeve and a right sleeve), by matching "
        "outline shape in the flat pattern layout. Different from Detect "
        "Folds, which finds a fold line WITHIN one island. Pattern pieces are "
        "reflected as FLAT PATTERNS — the 3D drape of the two sides differs "
        "even when the pattern is identical, so the test is 2D only"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        top = _get_top(context)
        if top is None:
            self.report({'ERROR'}, "AC9 Cloth Retopo properties not found.")
            return {'CANCELLED'}
        props = top.seam
        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        wm = context.window_manager
        with uic.ProgressScope(wm) as prog:
            try:
                # force=True: this button IS the manual re-detect escape hatch
                # for a Guide edit the cache key can't see (see
                # detect_mirror_pairs_cached's docstring) — it must never serve
                # a stale answer, and updates the cache other operators
                # (Replace Partner Island) read from.
                pairs, n_tested, rejections = detect_mirror_pairs_cached(
                    guide, flat_sk, tolerance=props.mirror_pair_tolerance, force=True,
                    progress=prog.update,
                )
            except Exception as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}

        # Overlay: connector between the two outlines' Flat-SK centroids,
        # which detection already computed as its alignment anchors.
        segments = [(p["centroid_a"], p["centroid_b"]) for p in pairs]

        # "mirror_pairs" is the detection RESULT (roots, vertex indices, rms
        # and the glide-reflection transform) — what island replacement will
        # read. "mirror_pair_segments" is purely the overlay picture.
        _cache["mirror_pairs"] = pairs
        _cache["mirror_pair_segments"] = segments
        _cache["mirror_pair_rejections"] = rejections
        _cache["mirror_pair_count"] = len(pairs)
        context.scene["ac9_cloth_retopo_mirror_pair_count"] = len(pairs)
        context.scene["ac9_cloth_retopo_mirror_pair_tested"] = n_tested
        build_mirror_pair_batches(segments, props.z_offset)

        n_mismatched_topo = sum(1 for p in pairs if not p["topo_match"])
        n_same_handed = sum(1 for p in pairs if p["same_handed"])
        print(f"[AC9 Seam] mirror pairs: {len(pairs)} found among {n_tested} "
              f"testable islands (tol {props.mirror_pair_tolerance:.4f}); "
              f"{n_mismatched_topo} pair(s) have mismatched vertex/boundary "
              f"counts and need reconciling before they can be mirrored.")
        for p in pairs:
            print(f"[AC9 Seam]   pair {p['root_a']}<->{p['root_b']} "
                  f"rms={p['rms']:.5f} rot_rms={p['rot_rms']:.5f} "
                  f"theta={p['theta_deg']:.1f}deg topo_match={p['topo_match']}"
                  + ("  SAME-HANDED (duplicate copy, not a mirror?)"
                     if p["same_handed"] else ""))
        for r in rejections:
            print(f"[AC9 Seam]   unpaired: {r}")
        _tag_redraw_3d(context)
        msg = (f"{len(pairs)} twin pair(s) among {n_tested} islands "
               f"({len(rejections)} unpaired — see console).")
        if n_same_handed:
            msg += f" {n_same_handed} look same-handed, check console."
        # Tell, don't force — see AC9_OT_detect_symmetry's matching comment.
        if pairs and not getattr(props, "show_mirror_pairs", False):
            msg += " Twin Lines overlay is currently OFF — enable it to see them."
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AC9_OT_analyze_guide(Operator):
    """Run both of the Guide-only symmetry analyses in one click.

    Detect Symmetry finds fold lines WITHIN a single island (a self-
    symmetric panel — back body, waistband, placket, cuff). Detect Mirror
    Pairs finds separate island PAIRS that mirror each other (sleeves,
    front panels). They test genuinely different geometric relationships
    and feed different downstream consumers (fold lines are a visual/snap
    guide; mirror pairs feed Phase 2 island replacement), so this does NOT
    merge their logic or their tolerances — it just calls both operators
    in sequence with independent error handling, so one UI button covers
    the "read the Guide" step that previously needed two separate clicks.
    """

    bl_idname = "ac9_cloth.analyze_guide"
    bl_label = "Analyze Symmetry"
    bl_description = (
        "Detect both kinds of symmetry on the Guide's flat pattern: folds "
        "(a mirror axis within a single island) and twins (separate left/right "
        "islands that reflect each other). Runs Detect Folds and Detect Twins. "
        "Generate Boundary uses the folds to place vertices on the axis; "
        "Symmetrize and Replace Twin Island rebuild one side from the other"
    )
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        top = _get_top(context)
        if top is None or top.guide_obj is None:
            cls.poll_message_set("Set the Guide first.")
            return False
        return True

    def execute(self, context):
        wm = context.window_manager
        with uic.ProgressScope(wm):
            with uic.ProgressScope(wm, 0, 50):
                ok_sym = _run_sub_op(bpy.ops.ac9_cloth.detect_island_symmetry,
                                     "Detect Folds")
            with uic.ProgressScope(wm, 50, 100):
                ok_mirror = _run_sub_op(bpy.ops.ac9_cloth.detect_mirror_pairs,
                                        "Detect Twins")

        if not ok_sym and not ok_mirror:
            self.report({'ERROR'}, "Both analyses failed — see the messages above.")
            return {'CANCELLED'}
        if not ok_sym or not ok_mirror:
            self.report({'WARNING'},
                        f"One analysis failed (folds ok={ok_sym}, twins "
                        f"ok={ok_mirror}) — see the messages above.")
            return {'FINISHED'}

        # This button's own report REPLACES the sub-operators' — Blender
        # doesn't chain them — so an "overlay is off" notice either of them
        # generated (see AC9_OT_detect_symmetry / AC9_OT_detect_mirror_pairs)
        # never reaches the user through this, the button they actually
        # press. Re-derive it here from the same scene counters those
        # operators just wrote.
        top = _get_top(context)
        props = top.seam
        n_sym = context.scene.get("ac9_cloth_retopo_symmetry_count", 0)
        n_pairs = context.scene.get("ac9_cloth_retopo_mirror_pair_count", 0)
        hidden = []
        if n_sym > 0 and not props.show_symmetry_axis:
            hidden.append("Fold Lines")
        if n_pairs > 0 and not getattr(props, "show_mirror_pairs", False):
            hidden.append("Twin Lines")
        msg = "Symmetry analysis complete."
        if hidden:
            msg += f" {' and '.join(hidden)} overlay is OFF — enable to see the results."
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AC9_OT_clear_guide_analysis(Operator):
    bl_idname = "ac9_cloth.clear_guide_analysis"
    bl_label = "Clear Symmetry"
    bl_description = "Clear the symmetry analysis: fold lines and twin pairs"
    bl_options = {'REGISTER'}

    def execute(self, context):
        bpy.ops.ac9_cloth.clear_island_symmetry('EXEC_DEFAULT')
        bpy.ops.ac9_cloth.clear_mirror_pairs('EXEC_DEFAULT')
        return {'FINISHED'}


class AC9_OT_analyze_all(Operator):
    """The Setup panel's one Analyze button: Seams, then Symmetry (folds +
    twins), then Anchors — all read-only on the Guide.

    Delegates to the three existing operators via bpy.ops and adds no cache
    handling of its own: Analyze Symmetry keeps its manual-re-detect
    semantics (detect_island_symmetry_cached(force=True) recomputes and
    refreshes the shared memo cache that Generate / Symmetrize / Replace
    read), so pressing this is exactly what pressing the three buttons in
    turn used to be. Partial failure is reported, not hidden.
    """

    bl_idname = "ac9_cloth.analyze_all"
    bl_label = "Analyze Guide"
    bl_description = (
        "Run every Guide analysis in one go: Analyze Seams (sewn seam pairs — "
        "what the overlays, ghosts and snapping read), Analyze Symmetry (folds "
        "and twins) and Analyze Anchors (span divisions). Read-only on the "
        "Guide; nothing touches the retopo. The seam analysis is cleared on "
        "file open and on Reload Scripts, so run this again then"
    )
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        top = _get_top(context)
        if top is None or top.guide_obj is None or not top.guide_flat_shapekey:
            cls.poll_message_set("Set the Guide and Flat SK first.")
            return False
        return True

    def execute(self, context):
        # Several seconds on a production Guide (symmetry alone ~4s), and the
        # UI cannot redraw meanwhile — the progress cursor is the one thing
        # Blender does update inside a blocking operator.
        wm = context.window_manager
        with uic.ProgressScope(wm):
            with uic.ProgressScope(wm, 0, 35):
                ok_seams = _run_sub_op(bpy.ops.ac9_cloth.analyze_uv_seam_pairs,
                                       "Analyze Seams")
            with uic.ProgressScope(wm, 35, 85):
                ok_sym = _run_sub_op(bpy.ops.ac9_cloth.analyze_guide,
                                     "Analyze Symmetry")
            with uic.ProgressScope(wm, 85, 100):
                ok_anchors = _run_sub_op(bpy.ops.ac9_cloth.analyze_anchors,
                                         "Analyze Anchors")

        failed = [name for ok, name in ((ok_seams, "Seams"), (ok_sym, "Symmetry"),
                                        (ok_anchors, "Anchors")) if not ok]
        if len(failed) == 3:
            self.report({'ERROR'}, "Every analysis failed — see the messages above.")
            return {'CANCELLED'}

        # The sub-operators' own reports are replaced by this one, so restate
        # the counts they wrote (scene counters + overlay cache).
        scene = context.scene
        n_pairs = scene.get("ac9_cloth_retopo_seam_pair_count", 0)
        n_folds = scene.get("ac9_cloth_retopo_symmetry_count", 0)
        n_twins = scene.get("ac9_cloth_retopo_mirror_pair_count", 0)
        n_anchors = len(_cache["anchors"])
        msg = (f"Analyzed: {n_pairs} seam pairs, {n_folds} folds, "
               f"{n_twins} twins, {n_anchors} anchors.")
        if failed:
            msg += f" Failed: {', '.join(failed)} — see the messages above."
            self.report({'WARNING'}, msg)
            return {'FINISHED'}
        top = _get_top(context)
        props = top.seam
        hidden = []
        if n_folds > 0 and not props.show_symmetry_axis:
            hidden.append("Fold Lines")
        if n_twins > 0 and not props.show_mirror_pairs:
            hidden.append("Twin Lines")
        if hidden:
            msg += f" {' and '.join(hidden)} overlay is OFF — enable to see the results."
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AC9_OT_clear_analysis(Operator):
    bl_idname = "ac9_cloth.clear_analysis"
    bl_label = "Clear Analysis"
    bl_description = (
        "Clear every Guide analysis: seam pairs (and the ghosts built from "
        "them), folds, twins and anchors. Overlays that draw them go blank "
        "until the next Analyze"
    )
    bl_options = {'REGISTER'}

    def execute(self, context):
        bpy.ops.ac9_cloth.clear_uv_seam_guides('EXEC_DEFAULT')
        bpy.ops.ac9_cloth.clear_guide_analysis('EXEC_DEFAULT')
        _cache["anchors"] = []
        _cache["anchor_indices"] = []
        _cache["anchor_spans"] = 0
        _cache["anchor_dirty"] = False
        _batches["anchors"] = None
        _tag_redraw_3d(context)
        self.report({'INFO'}, "Guide analysis cleared.")
        return {'FINISHED'}


class AC9_OT_clear_mirror_pairs(Operator):
    bl_idname = "ac9_cloth.clear_mirror_pairs"
    bl_label = "Clear Twins"
    bl_description = "Clear the detected twin pairs"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        clear_mirror_pair_batches()
        for key in ("ac9_cloth_retopo_mirror_pair_count",
                    "ac9_cloth_retopo_mirror_pair_tested"):
            if key in context.scene:
                del context.scene[key]
        _tag_redraw_3d(context)
        self.report({'INFO'}, "Twin pairs cleared.")
        return {'FINISHED'}


class AC9_OT_mirror_pair_retopo_map(Operator):
    """Read-only diagnostic for the Guide->Retopo island correspondence that
    Phase 2 (island replacement, not yet implemented) will rely on.

    Runs Detect Mirror Pairs first if it hasn't been run yet, then matches
    every Retopo island to its Guide island (mutual nearest-neighbour on
    island centroids) and translates the detected Guide-root pairs into
    Retopo-root pairs. Nothing is written to the mesh — this only reports.
    """

    bl_idname = "ac9_cloth.mirror_pair_retopo_map"
    bl_label = "Diagnose Twin Mapping"
    bl_description = (
        "Read-only: match every Retopo island to its Guide island and report "
        "the result (full table in a Text datablock). Runs Detect Twins "
        "first if needed. Nothing is written to the mesh"
    )
    bl_options = {'REGISTER', 'UNDO'}

    TEXT_NAME = "AC9_MirrorPairRetopoMap"

    select_unmatched: bpy.props.BoolProperty(
        name="Select Unmatched Retopo Verts",
        description=(
            "Select the retopo vertices on any island that failed to match a "
            "Guide island, so they can be found in the viewport"
        ),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        top = _get_top(context)
        if top is None or top.guide_obj is None or top.retopo_obj is None:
            cls.poll_message_set("Set the Guide and Retopo first.")
            return False
        return True

    def execute(self, context):
        top = _get_top(context)
        props = top.seam
        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        retopo = top.retopo_obj

        pairs = _cache.get("mirror_pairs")
        if not pairs:
            try:
                pairs, _n_tested, _rej = detect_mirror_pairs_cached(
                    guide, flat_sk, tolerance=props.mirror_pair_tolerance,
                )
            except Exception as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}

        from . import retopo_seam_sync as rss

        try:
            mapping, unmatched_retopo, unmatched_guide = rss.map_retopo_islands_to_guide(
                guide, retopo, flat_sk,
            )
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        translated, skipped = rss.translate_mirror_pairs_to_retopo(pairs, mapping)

        from .analysis import _compute_islands
        retopo_islands = _compute_islands(retopo.data)

        # ── Select the unmatched retopo islands' vertices ──────────────────
        selected = 0
        if self.select_unmatched and unmatched_retopo:
            islands = retopo_islands
            bad_verts = set()
            for u in unmatched_retopo:
                bad_verts.update(islands.get(u["root"], ()))
            mesh = retopo.data
            for v in mesh.vertices:
                v.select = v.index in bad_verts
            for e in mesh.edges:
                e.select = False
            for f in mesh.polygons:
                f.select = False
            mesh.update()
            selected = len(bad_verts)
            try:
                context.view_layer.objects.active = retopo
                retopo.select_set(True)
            except Exception:
                pass

        # ── Report text ──────────────────────────────────────────────────
        n_retopo_mismatch = sum(
            1 for p in translated
            if len(retopo_islands.get(p["retopo_root_a"], ()))
            != len(retopo_islands.get(p["retopo_root_b"], ())))

        lines = [
            "AC9 Cloth Retopo — twin pair Retopo mapping",
            f"Guide : {guide.name}",
            f"Retopo: {retopo.name}",
            "",
            f"Retopo islands matched to a Guide island : {len(mapping)}",
            f"Retopo islands unmatched                 : {len(unmatched_retopo)}",
            f"Guide islands with no matched Retopo island: {len(unmatched_guide)}",
            f"Retopo pairs actually needing Phase 2 (unequal vertex counts): "
            f"{n_retopo_mismatch} of {len(translated)}",
        ]
        if mapping:
            worst = max(mapping.values(), key=lambda gd: gd[1])
            lines.append(f"worst match distance (mm)                : {worst[1]*1000:.2f}")
        lines.append("")
        if unmatched_retopo:
            lines.append("Unmatched Retopo islands:")
            for u in unmatched_retopo:
                lines.append(f"  root {u['root']}: {u['reason']}"
                             + (f"  (nearest guide root {u.get('best_guide_root')}, "
                                f"{u.get('best_dist', 0)*1000:.2f}mm)"
                                if "best_dist" in u else ""))
            lines.append("")
        if unmatched_guide:
            lines.append("Guide islands with no Retopo island yet (not retopologised):")
            for u in unmatched_guide:
                lines.append(f"  root {u['root']}: nearest retopo root "
                             f"{u['best_retopo_root']}, "
                             + (f"{u['best_dist']*1000:.2f}mm" if u['best_dist'] is not None
                                else "n/a"))
            lines.append("")

        lines.append(f"Mirror pairs translated to Retopo roots: {len(translated)}")
        lines.append(f"Mirror pairs skipped (one/both sides not retopologised): {len(skipped)}")
        lines.append("")
        lines.append(
            "'retopo_match' is the one that matters for Phase 2 — whether YOUR "
            "retopo's two islands already have the same vertex count. 'guide_"
            "topo_match' is a DIFFERENT thing: whether the Guide's own dense "
            "drape-mesh panels have matching vertex/boundary counts, which says "
            "nothing about your retopo and can disagree with retopo_match in "
            "either direction (measured on a production file: 4 of 7 pairs "
            "disagreed) — it is shown for reference only, not as a to-do signal."
        )
        lines.append("")
        lines.append(f"{'guide a<->b':<18}{'retopo a<->b':<16}{'retopo v a/b':<14}"
                     f"{'retopo_match':<14}{'guide_topo_match':<18}{'same_handed':<12}")
        lines.append("-" * 92)
        for p in translated:
            guide_col = f"{p['root_a']}<->{p['root_b']}"
            retopo_col = f"{p['retopo_root_a']}<->{p['retopo_root_b']}"
            rva = len(retopo_islands.get(p["retopo_root_a"], ()))
            rvb = len(retopo_islands.get(p["retopo_root_b"], ()))
            retopo_match = (rva == rvb)
            rverts_col = f"{rva}/{rvb}"
            marker = "" if retopo_match else "  <-- needs Phase 2"
            lines.append(
                f"{guide_col:<18}{retopo_col:<16}{rverts_col:<14}"
                f"{str(retopo_match):<14}{str(p['topo_match']):<18}"
                f"{str(p['same_handed']):<12}{marker}"
            )
        if skipped:
            lines.append("")
            lines.append("Skipped (missing Retopo island on the noted side(s)):")
            for s in skipped:
                p = s["pair"]
                lines.append(f"  guide {p['root_a']}<->{p['root_b']}  missing: {s['missing']}")

        n_same_handed = sum(1 for p in translated if p["same_handed"])
        if n_same_handed:
            lines.append("")
            lines.append(f"WARNING: {n_same_handed} translated pair(s) are same_handed "
                         f"(look like a duplicated panel, not a mirror — do NOT mirror "
                         f"these blindly in Phase 2).")

        _write_report(self.TEXT_NAME, lines, guide, retopo)

        msg = (f"{len(mapping)} Retopo island(s) matched, "
               f"{len(unmatched_retopo)} unmatched, "
               f"{n_retopo_mismatch} of {len(translated)} pair(s) actually need Phase 2"
               f"{f', {n_same_handed} same-handed' if n_same_handed else ''}.")
        if selected:
            msg += f" Selected {selected} vert(s) on unmatched islands."
        msg += f" Full table in Text '{self.TEXT_NAME}'."
        level = "WARNING" if (unmatched_retopo or n_same_handed) else "INFO"
        self.report({level}, msg)
        return {'FINISHED'}


class AC9_OT_replace_mirror_island(Operator):
    """Phase 2: replace a mismatched mirror-pair island's topology with a
    transformed copy of its partner's, so both sides finally match.

    Source island = whichever island the currently SELECTED retopo vertex
    belongs to (works from either Edit or Object Mode selection) — pick a
    vertex on the side you trust, run this, and the OTHER side gets
    replaced. Guide-side detection reuses analysis.detect_mirror_pairs_
    cached's cache (keyed on the Guide's identity/topology/transform — see
    its docstring), not a stale in-memory blob left over from whenever
    Detect Mirror Pairs was last clicked: an actual Guide edit invalidates
    it automatically. The Retopo-side mapping right after it is NEVER
    cached — it has to see every edit this very operator makes, and is
    cheap enough (island-count-scale) that recomputing it is not a cost
    worth avoiding the way the Guide-side shape match is.

    Destructive: deletes and recreates every vertex/face on the OTHER
    (target) island. No confirmation prompt — Ctrl+Z undoes it like any
    other operator, which is the standard way back in this addon.
    """

    bl_idname = "ac9_cloth.replace_mirror_island"
    bl_label = "Replace Twin Island"
    bl_description = (
        "Replace the twin of the SELECTED vertex's island with a reflected "
        "copy of it, so left/right topology finally matches. Select a vertex "
        "on the side you trust. Destructive — deletes and recreates the OTHER "
        "island. Undo (Ctrl+Z) to revert"
    )
    bl_options = {'REGISTER', 'UNDO'}

    src_vert: bpy.props.IntProperty(default=-1, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        top = _get_top(context)
        if top is None or top.guide_obj is None or top.retopo_obj is None:
            cls.poll_message_set("Set the Guide and Retopo first.")
            return False
        return True

    def invoke(self, context, event):
        top = _get_top(context)
        retopo = top.retopo_obj
        if retopo.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(retopo.data)
            sel = [v.index for v in bm.verts if v.select]
        else:
            sel = [v.index for v in retopo.data.vertices if v.select]
        if not sel:
            self.report({'ERROR'}, "Select a vertex on the SOURCE island first "
                                   "(the side you trust — its partner gets replaced).")
            return {'CANCELLED'}
        self.src_vert = sel[0]
        return self.execute(context)

    def execute(self, context):
        if self.src_vert < 0:
            self.report({'ERROR'}, "No source vertex recorded — run this from the "
                                   "button, not directly from the redo panel after "
                                   "a mesh edit.")
            return {'CANCELLED'}

        top = _get_top(context)
        props = top.seam
        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        retopo = top.retopo_obj

        was_edit = retopo.mode == 'EDIT'
        if was_edit:
            bpy.ops.object.mode_set(mode='OBJECT')

        wm = context.window_manager
        # Opened by hand: the finally below has to restore Edit Mode, and
        # the bar should close after it, not before.
        _progress_ctx = uic.ProgressScope(wm)
        _progress_ctx.__enter__()
        try:
            if self.src_vert >= len(retopo.data.vertices):
                self.report({'ERROR'}, "Recorded source vertex no longer exists "
                                       "(mesh changed) — reselect and retry.")
                return {'CANCELLED'}

            from .analysis import _compute_islands
            from . import retopo_seam_sync as rss
            from ..clo_projector import core as projector_core

            try:
                # force=False: reuses the cached Guide-side detection when
                # nothing about the Guide has changed (the common case — see
                # detect_mirror_pairs_cached's docstring). Guide-side shape
                # matching measured at ~2.4s on a production file; the
                # actual replacement below is under 50ms, so re-deriving
                # this on every click was the entire complaint this caching
                # exists to fix. The Retopo-side mapping right after this
                # is NOT cached — it must see every edit Replace itself
                # makes, and is cheap regardless (island-count-scale, not
                # Guide-scale).
                wm.progress_update(5)
                pairs, _n_tested, _rej = detect_mirror_pairs_cached(
                    guide, flat_sk, tolerance=props.mirror_pair_tolerance)
                wm.progress_update(60)
                mapping, _unmatched_r, _unmatched_g = rss.map_retopo_islands_to_guide(
                    guide, retopo, flat_sk)
                wm.progress_update(75)
            except Exception as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}
            translated, _skipped = rss.translate_mirror_pairs_to_retopo(pairs, mapping)

            # NEVER cached for a Retopo mesh — Phase 2 replacement deletes
            # and recreates the same vertex/edge/face COUNTS, which is
            # exactly what the cache key can't tell apart from "unchanged"
            # (see _island_centroids' docstring in retopo_seam_sync.py).
            islands = _compute_islands(retopo.data)
            src_root = None
            for root, idxs in islands.items():
                if self.src_vert in idxs:
                    src_root = root
                    break
            if src_root is None:
                self.report({'ERROR'}, "Could not find the selected vertex's island.")
                return {'CANCELLED'}

            pair, source_is_a = None, None
            for p in translated:
                if p["retopo_root_a"] == src_root:
                    pair, source_is_a = p, True
                    break
                if p["retopo_root_b"] == src_root:
                    pair, source_is_a = p, False
                    break
            if pair is None:
                self.report({'ERROR'}, "The selected vertex's island is not part of "
                                       "any detected, fully-retopologised twin pair "
                                       "(maybe that panel isn't retopologised on both "
                                       "sides yet, or Analyze Symmetry hasn't been run). "
                                       "Search 'Diagnose Twin Mapping' (F3) for a "
                                       "detailed breakdown.")
                return {'CANCELLED'}
            if pair["same_handed"]:
                self.report({'ERROR'}, "This pair looks SAME-HANDED (a duplicated "
                                       "panel, not a left/right reflection) — refusing "
                                       "to replace. Check the Twin Lines overlay.")
                return {'CANCELLED'}

            if source_is_a:
                src_r, dst_r = pair["retopo_root_a"], pair["retopo_root_b"]
                centroid_src, centroid_dst = pair["centroid_a"], pair["centroid_b"]
            else:
                src_r, dst_r = pair["retopo_root_b"], pair["retopo_root_a"]
                centroid_src, centroid_dst = pair["centroid_b"], pair["centroid_a"]
            # Reverse direction reuses the SAME theta (see analysis.py's
            # comment where it stores both directions) — no sign flip here.
            theta = pair["theta_deg"]

            try:
                stats = rss.replace_retopo_island(
                    retopo, src_r, dst_r, theta, centroid_src, centroid_dst)
            except RuntimeError as e:
                self.report({'ERROR'}, f"Replacement refused: {e}")
                return {'CANCELLED'}

            proj = projector_core.run_forward_projection(
                context, retopo, guide, flat_sk, incremental=True,
                progress=uic.ProgressThrottle(wm, lo=80, hi=100))

            _cache["mirror_pairs"] = []
            _cache["mirror_pair_segments"] = []
            _cache["mirror_pair_rejections"] = []

            msg = (f"Replaced island {dst_r}: -{stats['deleted_verts']}v/"
                   f"{stats['deleted_faces']}f +{stats['created_verts']}v/"
                   f"{stats['created_faces']}f (size ratio {stats['size_ratio']:.2f}). "
                   f"Projected {proj.new_verts_computed} new vert(s) to 3D, "
                   f"{proj.failed} failed.")
            level = 'WARNING' if proj.failed else 'INFO'
            self.report({level}, msg)
            return {'FINISHED'}
        finally:
            _progress_ctx.__exit__(None, None, None)
            if was_edit:
                bpy.ops.object.mode_set(mode='EDIT')


SYMMETRIZE_AXIS_ITEMS = [
    ('AUTO', "Auto",
     "Pick the fold that still has work: the side-to-side asymmetric one; "
     "refuses when both are equally asymmetric"),
    ('VERTICAL', "Vertical axis (mirror left-right)",
     "Fold along the panel's vertical centre line, so the left half is "
     "rebuilt from the right half (or the other way round)"),
    ('HORIZONTAL', "Horizontal axis (mirror top-bottom)",
     "Fold along the panel's horizontal centre line, so the bottom half is "
     "rebuilt from the top half (or the other way round)"),
]

# What each axis key does, in the words the report uses.
SYMMETRIZE_AXIS_LABEL = {
    'VERTICAL': "vertical axis, mirror left-right",
    'HORIZONTAL': "horizontal axis, mirror top-bottom",
}

# Below this difference in mirror coverage, Auto cannot tell the two axes
# apart and refuses rather than guessing (see _select_symmetrize_fold).
_SYM_AUTO_COVERAGE_MARGIN = 0.02


def _fold_axis_key(fold):
    """'HORIZONTAL' or 'VERTICAL' for a detect_island_symmetry fold.

    Named by what the fold DOES, not by how the line lies: a fold LINE that
    runs left-right (|d.x| > |d.y|) mirrors top<->bottom, so it is the
    HORIZONTAL axis.
    """
    d = fold["b"] - fold["a"]
    return 'HORIZONTAL' if abs(d.x) > abs(d.y) else 'VERTICAL'


def _select_symmetrize_fold(retopo, island_idxs, candidates, axis, tolerance):
    """Choose which of an island's fold axes to symmetrize across.

    `candidates` are the folds already filtered to this retopo island (a
    doubly symmetric panel — a waistband — has two: detect_island_symmetry
    tests a horizontal and a vertical fold and appends EVERY passing one, in
    that order, so taking the first would always mean top<->bottom).

    Returns (fold, axis_key, error) — exactly one of `fold`/`error` is set.

    'VERTICAL'/'HORIZONTAL' take that axis outright. 'AUTO' takes the only
    candidate when there is one, and otherwise compares how far each axis is
    from being mirrored ALREADY in the retopo (analysis.fold_mirror_coverage)
    and picks the one with work left to do — the lower coverage. When the two
    are within _SYM_AUTO_COVERAGE_MARGIN it refuses: on a retopo that is
    already symmetric both ways (a uniformly gridded band) either answer is a
    destructive no-op, and silently picking one is worse than asking.
    """
    from .analysis import fold_mirror_coverage

    by_key = {}
    for f in candidates:
        by_key.setdefault(_fold_axis_key(f), f)

    if axis in ('VERTICAL', 'HORIZONTAL'):
        fold = by_key.get(axis)
        if fold is None:
            return None, axis, (
                f"No {axis.lower()} fold axis detected on this island "
                f"(tolerance {tolerance:.4f}) — try the other axis, or raise "
                f"Symmetry Tolerance.")
        return fold, axis, None

    if len(by_key) == 1:
        key, fold = next(iter(by_key.items()))
        return fold, key, None

    scored = [(fold_mirror_coverage(retopo, island_idxs, f["a"], f["b"]), k, f)
              for k, f in by_key.items()]
    scored.sort(key=lambda t: t[0])
    if scored[1][0] - scored[0][0] < _SYM_AUTO_COVERAGE_MARGIN:
        return None, None, (
            "Both fold axes are equally (a)symmetric — choose Vertical or "
            "Horizontal.")
    return scored[0][2], scored[0][1], None


class AC9_OT_symmetrize_island(Operator):
    """Symmetrize a single self-symmetric Retopo island (one panel with
    BOTH halves in the same connected component, joined along a shared
    centre seam — a back yoke, waistband) — the one-island counterpart to
    Replace Partner Island, which only handles two SEPARATE islands.

    Source (kept) side = whichever side of the fold axis the currently
    SELECTED retopo vertex is on. Everything is re-detected from scratch on
    execute (Detect Symmetry + the same Guide->Retopo mapping Replace
    Partner Island uses), so a stale cache can't misplace the axis.

    WHICH fold axis is the `axis` property: a panel can pass the symmetry
    test both ways (a waistband is symmetric left-right AND top-bottom), and
    only one of the two is the copy the user meant. 'AUTO' picks the axis the
    retopo is NOT already mirrored across; see _select_symmetrize_fold.
    """

    bl_idname = "ac9_cloth.symmetrize_island"
    bl_label = "Symmetrize Island"
    bl_description = (
        "Make a self-symmetric panel (one island with a fold — see Detect "
        "Folds) match itself: keep the side the SELECTED vertex is on, "
        "rebuild the other side as its reflection across the fold. "
        "Destructive — the other side is deleted and recreated. Undo (Ctrl+Z) "
        "to revert"
    )
    bl_options = {'REGISTER', 'UNDO'}

    src_vert: bpy.props.IntProperty(default=-1, options={'HIDDEN'})
    axis: bpy.props.EnumProperty(
        name="Fold Axis",
        description=(
            "Which fold axis to mirror across. A panel can be symmetric both "
            "ways (a waistband: left-right AND top-bottom), and only one of "
            "them is the copy you meant"
        ),
        items=SYMMETRIZE_AXIS_ITEMS,
        default='AUTO',
    )

    @classmethod
    def poll(cls, context):
        top = _get_top(context)
        if top is None or top.guide_obj is None or top.retopo_obj is None:
            cls.poll_message_set("Set the Guide and Retopo first.")
            return False
        return True

    def invoke(self, context, event):
        top = _get_top(context)
        retopo = top.retopo_obj
        if retopo.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(retopo.data)
            sel = [v.index for v in bm.verts if v.select]
        else:
            sel = [v.index for v in retopo.data.vertices if v.select]
        if not sel:
            self.report({'ERROR'}, "Select a vertex on the SIDE YOU WANT TO KEEP first "
                                   "(its mirror image gets rebuilt on the other side).")
            return {'CANCELLED'}
        self.src_vert = sel[0]
        return self.execute(context)

    def execute(self, context):
        if self.src_vert < 0:
            self.report({'ERROR'}, "No source vertex recorded — run this from the "
                                   "button, not directly from the redo panel after "
                                   "a mesh edit.")
            return {'CANCELLED'}

        top = _get_top(context)
        props = top.seam
        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        retopo = top.retopo_obj

        was_edit = retopo.mode == 'EDIT'
        if was_edit:
            bpy.ops.object.mode_set(mode='OBJECT')

        try:
            if self.src_vert >= len(retopo.data.vertices):
                self.report({'ERROR'}, "Recorded source vertex no longer exists "
                                       "(mesh changed) — reselect and retry.")
                return {'CANCELLED'}

            from .analysis import _compute_islands, detect_island_symmetry_cached
            from . import retopo_seam_sync as rss
            from ..clo_projector import core as projector_core
            from ..clo_projector.attachment import ATTR_STATUS, STATUS_NONE

            try:
                # force=False: reuse Analyze Guide's cached detection when
                # the Guide hasn't changed since (the common case — this
                # button doesn't edit the Guide). See detect_island_
                # symmetry_cached's docstring for what invalidates it.
                segs, _n_tested, _n_sym, folds = detect_island_symmetry_cached(
                    guide, flat_sk, tolerance=props.symmetry_tolerance)
                mapping, _unmatched_r, _unmatched_g = rss.map_retopo_islands_to_guide(
                    guide, retopo, flat_sk)
            except Exception as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}

            if not folds:
                self.report({'ERROR'}, "No fold lines detected on the Guide. Run "
                                       "'Analyze Symmetry' first.")
                return {'CANCELLED'}

            # 1:1 by construction (mapping only holds mutual matches — see
            # map_retopo_islands_to_guide's docstring), same reasoning
            # translate_mirror_pairs_to_retopo relies on.
            guide_to_retopo = {g: r for r, (g, _d) in mapping.items()}

            islands = _compute_islands(retopo.data)
            src_root = None
            for root, idxs in islands.items():
                if self.src_vert in idxs:
                    src_root = root
                    break
            if src_root is None:
                self.report({'ERROR'}, "Could not find the selected vertex's island.")
                return {'CANCELLED'}

            # ALL of this island's folds, not just the first: a doubly
            # symmetric panel has two, and detect_island_symmetry always
            # lists the horizontal one first (see _select_symmetrize_fold).
            candidates = [f for f in folds
                          if guide_to_retopo.get(f["root"]) == src_root]
            if not candidates:
                self.report({'ERROR'}, "The selected vertex's island has no detected "
                                       "fold line (not self-symmetric, or not mapped "
                                       "to a Guide island yet).")
                return {'CANCELLED'}

            fold, axis_key, axis_err = _select_symmetrize_fold(
                retopo, islands[src_root], candidates, self.axis,
                props.symmetry_tolerance)
            if axis_err is not None:
                self.report({'ERROR'}, axis_err)
                return {'CANCELLED'}
            axis_note = f" ({SYMMETRIZE_AXIS_LABEL[axis_key]})"

            mat = retopo.matrix_world
            fold_a, fold_b = fold["a"], fold["b"]
            d = (fold_b - fold_a)
            if d.length < 1e-9:
                self.report({'ERROR'}, "Fold axis has zero length — cannot symmetrize.")
                return {'CANCELLED'}
            d = d.normalized()
            from mathutils import Vector as _V
            normal = _V((-d.y, d.x, 0.0))
            src_world = mat @ retopo.data.vertices[self.src_vert].co
            sign = (src_world - fold_a).dot(normal)
            # 0.5mm, the same band symmetrize_retopo_island treats as "on the
            # axis" — the refined axis sits a micron or two off the true
            # centre, so a 1e-9 test let a centre-seam vertex through and the
            # kept side came out of the rounding noise.
            if abs(sign) < 0.0005:
                self.report({'ERROR'}, "The selected vertex sits on the fold axis — "
                                       "select a vertex clearly on the side to keep.")
                return {'CANCELLED'}
            keep_sign = 1.0 if sign > 0 else -1.0

            try:
                stats = rss.symmetrize_retopo_island(
                    retopo, src_root, fold_a, fold_b, keep_sign)
            except RuntimeError as e:
                self.report({'ERROR'}, f"Symmetrize refused: {e}")
                return {'CANCELLED'}

            # Axis vertices were snapped (moved) but keep their pre-snap
            # attachment status — flag them "needs projection" so an
            # incremental pass actually recomputes them. New vertices don't
            # need this (a fresh bmesh vertex's ac9_status already defaults
            # to STATUS_NONE) but flagging them too is harmless and cheap.
            # See symmetrize_retopo_island's docstring for the full reasoning.
            status_attr = retopo.data.attributes.get(ATTR_STATUS)
            if status_attr is not None:
                for idx in stats["axis_vert_indices"] + stats["new_vert_indices"]:
                    status_attr.data[idx].value = STATUS_NONE
                proj = projector_core.run_forward_projection(
                    context, retopo, guide, flat_sk, incremental=True)
            else:
                # No attachment data on this mesh yet — nothing to flag
                # incrementally, fall back to a full pass.
                proj = projector_core.run_forward_projection(
                    context, retopo, guide, flat_sk, incremental=False)

            # Refresh rather than discard — the fold this Symmetrize just
            # used stays valid (Symmetrize doesn't move the axis, only what's
            # either side of it), and other islands' folds are untouched.
            # Discarding here used to blank the Fold Lines overlay on every
            # single Symmetrize click, right when the user most wants to
            # see it.
            _cache["symmetry_segments"] = segs
            _cache["symmetry_folds"] = folds
            build_symmetry_batches(segs, props.z_offset)
            _tag_redraw_3d(context)

            msg = (f"Symmetrized: kept {stats['kept_verts']}v, -{stats['deleted_verts']}v/"
                   f"{stats['deleted_faces']}f +{stats['created_verts']}v/"
                   f"{stats['created_faces']}f, {stats['axis_verts']} axis vert(s) "
                   f"snapped (max {stats['max_axis_snap']*1000:.3f}mm). "
                   f"Projected {proj.projected}/{proj.total}, {proj.failed} failed."
                   f"{axis_note}")
            level = 'WARNING' if proj.failed else 'INFO'
            self.report({level}, msg)
            return {'FINISHED'}
        finally:
            if was_edit:
                bpy.ops.object.mode_set(mode='EDIT')


class AC9_OT_ghost_snap_move(Operator):
    bl_idname = "ac9_cloth.ghost_snap_move"
    bl_label = "Ghost Snap Move"
    bl_description = (
        "Move selected retopo vertices; snaps only to ghost points when within "
        "Snap Distance. Activated by G when Ghost Snap Mode is ON. A free "
        "edge's ghost is a fixed point on the outline from the last Refresh, "
        "not a line that follows the drag — use Outline Snap to slide a vertex "
        "along a hem"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if context.mode != 'EDIT_MESH':
            return False
        top = getattr(getattr(context, 'scene', None), 'ac9_cloth_retopo', None)
        if top is None:
            return False
        return top.seam.ghost_snap_mode and bool(_cache["ghost_results"])

    def invoke(self, context, event):
        obj = context.edit_object
        if obj is None:
            self.report({'ERROR'}, "No active edit object.")
            return {'CANCELLED'}

        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        selected = [v for v in bm.verts if v.select]
        if not selected:
            self.report({'ERROR'}, "No vertices selected.")
            return {'CANCELLED'}

        self.edit_obj = obj
        self.bm = bm
        self.initial_positions = [(v.index, v.co.copy()) for v in selected]
        self.initial_mx = event.mouse_region_x
        self.initial_my = event.mouse_region_y
        self.depth_co = sum((v.co for v in selected), Vector()) / len(selected)

        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Ghost Snap Move  |  LMB / Enter: Confirm   RMB / Esc: Cancel"
        )
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            self._update(context, event)
        elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            self._end(context, cancel=False)
            return {'FINISHED'}
        elif event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            self._end(context, cancel=True)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def _delta_3d(self, context, mx, my):
        region = context.region
        rv3d   = context.region_data
        if region is None or rv3d is None:
            return None
        p0 = region_2d_to_location_3d(region, rv3d, (self.initial_mx, self.initial_my), self.depth_co)
        p1 = region_2d_to_location_3d(region, rv3d, (mx, my), self.depth_co)
        if p0 is None or p1 is None:
            return None
        return p1 - p0

    def _update(self, context, event):
        top   = _get_top(context)
        if top is None:
            return
        props  = top.seam
        max_d  = props.snap_distance * _pguide.scene_flat_scale()

        # Ghost points live in WORLD (Flat-SK) space; bmesh co is object-local.
        matrix = self.edit_obj.matrix_world
        inv    = matrix.inverted()

        delta = self._delta_3d(context, event.mouse_region_x, event.mouse_region_y)
        if delta is None:
            return

        ghosts = _cache["ghost_results"]

        # Each selected vertex snaps INDEPENDENTLY to its OWN nearest ghost when
        # within Snap Distance — so a whole boundary loop lands on its matching
        # ghosts in one drag.  (Previously a single global offset was applied
        # rigidly to every vertex, which just translated the selection.)
        any_snap = False
        nearest_target = None   # representative target for the feedback ring
        nearest_d = float('inf')

        for idx, initial_co in self.initial_positions:
            v = self.bm.verts[idx]
            local = initial_co.copy()
            local.x += delta.x
            local.y += delta.y
            wc = matrix @ local

            best_d = float('inf')
            best_g = None
            for ghost in ghosts:
                g = ghost["opposite_pt"]
                d = math.sqrt((wc.x - g.x) ** 2 + (wc.y - g.y) ** 2)
                if d < best_d:
                    best_d = d
                    best_g = g

            if best_g is not None and best_d <= max_d:
                lp = inv @ Vector((best_g.x, best_g.y, wc.z))
                v.co.x = lp.x
                v.co.y = lp.y
                any_snap = True
                if best_d < nearest_d:
                    nearest_d = best_d
                    nearest_target = Vector((best_g.x, best_g.y, props.z_offset + 0.02))
            else:
                v.co.x = local.x
                v.co.y = local.y

        _cache["snap_active"]    = any_snap
        _cache["snap_target_3d"] = nearest_target if any_snap else None
        if any_snap and nearest_target is not None:
            build_snap_ring_batch(nearest_target, props.ghost_cross_size * 2.0)
        else:
            _batches["snap_ring"] = None

        bmesh.update_edit_mesh(self.edit_obj.data, loop_triangles=False)
        _tag_redraw_3d(context)

    def _end(self, context, cancel):
        if cancel:
            for idx, initial_co in self.initial_positions:
                v = self.bm.verts[idx]
                v.co.x = initial_co.x
                v.co.y = initial_co.y
        _cache["snap_active"]    = False
        _cache["snap_target_3d"] = None
        _batches["snap_ring"]    = None
        bmesh.update_edit_mesh(self.edit_obj.data)
        context.workspace.status_text_set(None)
        _tag_redraw_3d(context)


class AC9_OT_uv_seam_snap_move(Operator):
    bl_idname = "ac9_cloth.uv_seam_snap_move"
    bl_label = "Outline Snap Move"
    bl_description = (
        "Move selected retopo vertices; each snaps to the nearest point on the "
        "full Guide pattern outline (sewn seams + free edges) when within Snap "
        "Distance. Activated by G when Outline Snap Mode is ON. Use it to drop "
        "boundary verts onto the pattern outline"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if context.mode != 'EDIT_MESH':
            return False
        top = getattr(getattr(context, 'scene', None), 'ac9_cloth_retopo', None)
        if top is None:
            return False
        if not top.seam.uv_snap_mode:
            return False
        return (bool(_cache["boundary_segments"]) or bool(_cache["symmetry_segments"])
                or bool(_cache["crease_segments"]))

    def invoke(self, context, event):
        obj = context.edit_object
        if obj is None:
            self.report({'ERROR'}, "No active edit object.")
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        selected = [v for v in bm.verts if v.select]
        if not selected:
            self.report({'ERROR'}, "No vertices selected.")
            return {'CANCELLED'}

        # Make sure the snap KDTrees are ready (analyze / detect build them).
        if _cache["boundary_kd"] is None and _cache["boundary_segments"]:
            _cache["boundary_kd"] = build_segment_kd(_cache["boundary_segments"])
        if _cache["symmetry_kd"] is None and _cache["symmetry_segments"]:
            _cache["symmetry_kd"] = build_segment_kd(_cache["symmetry_segments"])
        if _cache["crease_kd"] is None and _cache["crease_segments"]:
            _cache["crease_kd"] = build_segment_kd(_cache["crease_segments"])

        self.edit_obj = obj
        self.bm = bm
        self.initial_positions = [(v.index, v.co.copy()) for v in selected]
        self.initial_mx = event.mouse_region_x
        self.initial_my = event.mouse_region_y
        self.depth_co = sum((v.co for v in selected), Vector()) / len(selected)

        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Outline Snap Move  |  LMB / Enter: Confirm   RMB / Esc: Cancel"
        )
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            self._update(context, event)
        elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            self._end(context, cancel=False)
            return {'FINISHED'}
        elif event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            self._end(context, cancel=True)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def _delta_3d(self, context, mx, my):
        region = context.region
        rv3d   = context.region_data
        if region is None or rv3d is None:
            return None
        p0 = region_2d_to_location_3d(region, rv3d, (self.initial_mx, self.initial_my), self.depth_co)
        p1 = region_2d_to_location_3d(region, rv3d, (mx, my), self.depth_co)
        if p0 is None or p1 is None:
            return None
        return p1 - p0

    def _update(self, context, event):
        top = _get_top(context)
        if top is None:
            return
        props = top.seam
        max_d = props.snap_distance * _pguide.scene_flat_scale()

        matrix = self.edit_obj.matrix_world
        inv    = matrix.inverted()

        delta = self._delta_3d(context, event.mouse_region_x, event.mouse_region_y)
        if delta is None:
            return

        segments = _cache["boundary_segments"]
        seg_kd   = _cache["boundary_kd"]
        fold_segments = _cache["symmetry_segments"] if props.snap_to_fold else []
        fold_kd = _cache["symmetry_kd"]
        crease_segments = _cache["crease_segments"] if props.snap_to_crease else []
        crease_kd = _cache["crease_kd"]

        # Each selected vertex snaps INDEPENDENTLY to its own nearest boundary
        # line — ideal for laying a boundary loop onto the CLO outline. Snaps to
        # the FULL pattern outline (sewn seams + free edges like hems/openings),
        # not just sewn seam pairs, so every boundary vert has a target. When
        # Snap to Fold Lines is on, detected fold (centre) lines are also targets
        # — so the centre column of verts lands on a cut-on-fold line.
        for idx, initial_co in self.initial_positions:
            v = self.bm.verts[idx]
            local = initial_co.copy()
            local.x += delta.x
            local.y += delta.y
            world = matrix @ local
            pt, d = nearest_segment_point(world, segments, kd=seg_kd)
            if fold_segments:
                fpt, fd = nearest_segment_point(world, fold_segments, kd=fold_kd)
                if fpt is not None and fd < d:
                    pt, d = fpt, fd
            if crease_segments:
                cpt, cd = nearest_segment_point(world, crease_segments, kd=crease_kd)
                if cpt is not None and cd < d:
                    pt, d = cpt, cd
            if pt is not None and d <= max_d:
                lp = inv @ Vector((pt.x, pt.y, world.z))
                v.co.x = lp.x
                v.co.y = lp.y
            else:
                v.co.x = local.x
                v.co.y = local.y

        bmesh.update_edit_mesh(self.edit_obj.data, loop_triangles=False)
        _tag_redraw_3d(context)

    def _end(self, context, cancel):
        if cancel:
            for idx, initial_co in self.initial_positions:
                v = self.bm.verts[idx]
                v.co.x = initial_co.x
                v.co.y = initial_co.y
        bmesh.update_edit_mesh(self.edit_obj.data)
        context.workspace.status_text_set(None)
        _tag_redraw_3d(context)



def _match_seams_skip_notes(res):
    """English report sentences for each `skipped_reasons` bucket, plus a
    note on split_onto when the run actually created vertices.

    One sentence per reason so a user hitting only one of them is not handed
    a wall of text about the other two.
    """
    reasons = res.get("skipped_reasons", {})
    notes = []
    n = reasons.get("misaligned", 0)
    if n:
        notes.append(
            f"{n} seam(s) hold vertices on both sides that the other lacks "
            f"— they need repositioning, not more vertices, so Match left "
            f"them alone. Run Status to see them."
        )
    n = reasons.get("needs_removal", 0)
    if n:
        notes.append(
            f"{n} seam(s): the selected side is the sparser one, so "
            f"matching it would mean removing vertices from the partner, "
            f"which Match does not do. Dissolve the extra vertices by hand, "
            f"then Match again."
        )
    n = reasons.get("source_not_authored", 0)
    if n:
        notes.append(
            f"{n} seam(s): the selected side has no vertices between its "
            f"anchors to copy from."
        )
    applied = res.get("applied")
    if applied and applied.get("split_onto", 0) > 0:
        notes.append(
            f"{applied['split_onto']} of them landed on an existing edge "
            f"and split it."
        )
    return notes


class AC9_OT_MatchSeams(bpy.types.Operator):
    """Give a sewn seam's sparser side the vertices its partner already has.

    Generate lays down BOTH sides of a seam that has nothing on either side
    yet. Match is the other half: it looks at a seam where one side already
    holds vertices the other lacks, and adds only the missing ones to the
    sparser side, at the matching position along the seam — so the two
    sides correspond one-to-one and coincide in 3D. Add-only: nothing is
    moved or deleted.

    With no selection, every sewn seam is matched, and whichever side holds
    more vertices is taken as the source. With a selection — an edge or
    vertex on the Retopo in Edit Mode — only the seam(s) touching that
    selection are matched, and the selected side is forced as the source,
    even where the automatic rule would have picked the other one.

    A new vertex that would land on an existing boundary edge splits that
    edge instead of dangling unconnected from it. A seam where BOTH sides
    hold vertices the other lacks is left untouched and reported — it needs
    repositioning, not more vertices
    """

    bl_idname = "ac9_cloth.match_seams"
    bl_label = "Match Seams"
    bl_description = (
        "Add the vertices a seam's sparser side is missing, copied from its "
        "sewn partner at the matching position along the seam. Select the "
        "side you trust on the Retopo in Edit Mode: only the seam(s) that "
        "selection touches run, and the selected side is the source. "
        "Add-only — never moves or deletes existing vertices. A new vertex "
        "that lands on an existing edge splits it. A seam where both sides "
        "hold vertices the other lacks is left alone and reported"
    )
    bl_options = {"REGISTER", "UNDO"}

    apply: bpy.props.BoolProperty(
        name="Apply",
        description=(
            "Actually create the vertices. Leave OFF to only report what "
            "would be added"
        ),
        default=False,
    )
    source_spans: bpy.props.StringProperty(default="", options={"HIDDEN"})
    every_seam: bpy.props.BoolProperty(
        name="Every Seam",
        description=(
            "Match every sewn seam of the garment, taking whichever side "
            "holds more vertices as the source, instead of the seam(s) the "
            "selection names. No button leads here: it can add vertices "
            "across panels you have not built yet, and the selection is what "
            "makes Match a decision rather than a sweep"
        ),
        default=False,
    )

    @classmethod
    def poll(cls, context):
        top = _get_top(context)
        if top is None:
            return False
        if top.guide_obj is None:
            cls.poll_message_set("Set the Guide first.")
            return False
        if top.retopo_obj is None:
            cls.poll_message_set("Set the Retopo first.")
            return False
        # Edit Mode only. Without a selection this operator matches EVERY
        # sewn seam of the garment, taking whichever side holds more vertices
        # as the source, and adds to the other one — dozens of vertices from
        # one press, in panels the user has not built yet. Nobody reaches for
        # that; what they do is pick the side they trust and match that seam
        # (2026-09-09). The run itself still needs Object Mode internally and
        # switches back, see execute().
        if top.retopo_obj.type != "MESH" or top.retopo_obj.mode != "EDIT":
            cls.poll_message_set(
                "Select the side you trust on the Retopo in Edit Mode.")
            return False
        return True

    @staticmethod
    def _spans_from_selection(top, guide, flat_sk):
        """The spans the Retopo's Edit Mode selection names, or None.

        Read here rather than in an invoke(): invoke is not called in
        background mode (no window), so anything that only happened there
        could not be tested headless and every EXEC caller skipped it. The
        read has to happen while the Retopo is still in Edit Mode, which is
        why execute() calls this before it switches to Object Mode.
        """
        retopo = top.retopo_obj
        if retopo is None or retopo.type != "MESH" or retopo.mode != "EDIT":
            return None
        from . import span_density as sd
        props = top.seam
        ctx = sd.build_context(
            guide, retopo, flat_sk,
            tol=_flat_len(props.retopo_sync_tol, guide, flat_sk))
        bm = bmesh.from_edit_mesh(retopo.data)
        picked, _unmatched, _on_anchor = sd.spans_from_selection(
            bm, ctx, retopo.matrix_world)
        if not picked:
            return None
        return {tuple(ctx.spans[si]) for si in picked}

    def execute(self, context):
        top = _get_top(context)
        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        retopo = top.retopo_obj
        if retopo.type != "MESH":
            self.report({"ERROR"}, "Retopo must be a mesh object.")
            return {"CANCELLED"}

        props = top.seam
        from . import retopo_seam_sync

        source_spans = None
        if self.every_seam:
            # Means what it says: the selection is not consulted at all.
            # (Reading it anyway made the flag a no-op whenever something
            # happened to be selected - review finding, 2026-09-09.)
            pass
        elif self.source_spans:
            # An explicit override (a script, or the redo panel re-running
            # what the last press picked).
            try:
                raw = json.loads(self.source_spans)
                source_spans = {tuple(t) for t in raw}
            except Exception:
                source_spans = None
        else:
            source_spans = self._spans_from_selection(top, guide, flat_sk)

        # No selection means "every sewn seam, fuller side wins", which can
        # add vertices right across a garment from one press. That is not what
        # anyone reaches for (2026-09-09), so it now takes the explicit
        # `every_seam` opt-in and no button passes it.
        if source_spans is None and not self.every_seam:
            self.report(
                {"ERROR"},
                "Match Seams: select the side you trust on the Retopo in Edit "
                "Mode first — the selected side is what gets copied to its "
                "partner.")
            return {"CANCELLED"}

        was_edit = retopo.mode == "EDIT"
        if was_edit:
            bpy.ops.object.mode_set(mode="OBJECT")

        wm = context.window_manager
        _progress_ctx = uic.ProgressScope(wm)
        _progress_ctx.__enter__()
        try:
            wm.progress_update(10)
            res = retopo_seam_sync.run_retopo_seam_sync(
                guide, retopo, flat_sk,
                tol=_flat_len(props.retopo_sync_tol, guide, flat_sk),
                dry_run=not self.apply,
                source_spans=source_spans,
            )
            wm.progress_update(100)
        except Exception as exc:
            self.report({"ERROR"}, f"Match Seams failed: {exc}")
            return {"CANCELLED"}
        finally:
            _progress_ctx.__exit__(None, None, None)
            if was_edit:
                bpy.ops.object.mode_set(mode="EDIT")

        n = res["missing_total"]
        chains = res["new_chains"]
        off_mm = res["worst_source_offset"] * 1000.0
        skipped_total = res["skipped_interleaved"]
        skip_notes = _match_seams_skip_notes(res)
        skip_note = (" " + " ".join(skip_notes)) if skip_notes else ""
        source_note = (
            f"Selected side taken as the source for {res['jobs']} seam(s). "
            if source_spans else ""
        )

        if n == 0:
            top.status_boundary = (
                f"Nothing to add ({skipped_total} need attention)"
                if skipped_total else "Every sewn seam already matches."
            )
            self.report(
                {"WARNING"} if skipped_total else {"INFO"},
                (source_note + "Nothing to add." + skip_note) if skipped_total
                else (source_note + "Retopo seams already match on both sides."),
            )
            return {"FINISHED"}

        shape = f"{n} vertex/vertices over {res['jobs']} seam side(s)"
        if chains:
            shape += f", {chains} of them a whole new chain"

        if not self.apply:
            top.status_boundary = f"Would add {shape}"
            msg = source_note + f"Would add {shape}. Enable Apply to create them."
            if off_mm > 1.0:
                msg += (f" Note: the source side sits up to {off_mm:.1f}mm off "
                        f"the Guide outline; snapping it first gives a cleaner "
                        f"match.")
            msg += skip_note
            self.report({"WARNING"} if skipped_total else {"INFO"}, msg)
            return {"FINISHED"}

        ap = res["applied"]
        top.status_boundary = f"Added {ap['created']} vertex/vertices"
        msg = (source_note +
               f"Added {ap['created']} vertex/vertices "
               f"({ap['new_chains']} new chain(s), {ap['extended']} extension(s), "
               f"{ap['inserted_mid']} mid-chain).")
        if ap["ngons_created"]:
            msg += (f" {ap['ngons_created']} face(s) became n-gons where a "
                    f"vertex landed mid-chain — worth cleaning up.")
        msg += skip_note
        self.report({"WARNING"} if skipped_total else {"INFO"}, msg)
        return {"FINISHED"}


class AC9_OT_SyncSelectedVertex(bpy.types.Operator):
    """Sync just the selected vertex/vertices onto their seam partner

    The targeted counterpart to Match Seams: instead of mirroring
    every seam on the mesh, this acts only on the vertices you have
    selected — typically one or two knife-cut points just added to follow
    a fabric wrinkle. It creates the matching vertex on the seam's other
    side (or finds it, if already there) and Density-Pins both sides of
    the pair, so a later Adjust Density run cannot slide or remove either
    half. A selected vertex on a FREE edge (a hem, a neckline) has no other
    side to match, so it is simply pinned. Works from either Edit or Object
    Mode — it reads the current selection, then does the actual work in
    Object Mode
    """

    bl_idname = "ac9_cloth.sync_selected_vertex"
    bl_label = "Sync Selected Vertex"
    bl_options = {"REGISTER", "UNDO"}

    apply: bpy.props.BoolProperty(
        name="Apply",
        description=(
            "Actually create the vertex and mark the pins. Leave OFF to "
            "only report what would happen"
        ),
        default=False,
    )
    pin: bpy.props.BoolProperty(
        name="Pin",
        description=(
            "Also Density-Pin both sides of every pair this touches, so "
            "Adjust Density leaves them exactly where they are"
        ),
        default=True,
    )
    sel_indices: bpy.props.StringProperty(default="", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        top = _get_top(context)
        if top is None:
            return False
        if top.guide_obj is None:
            cls.poll_message_set("Set the Guide first.")
            return False
        if top.retopo_obj is None:
            cls.poll_message_set("Set the Retopo first.")
            return False
        return True

    def invoke(self, context, event):
        top = _get_top(context)
        retopo = top.retopo_obj
        if retopo.mode == "EDIT":
            bm = bmesh.from_edit_mesh(retopo.data)
            sel = [v.index for v in bm.verts if v.select]
        else:
            sel = [v.index for v in retopo.data.vertices if v.select]
        if not sel:
            self.report({"ERROR"}, "Select the knife-cut vertex/vertices on "
                                   "the Retopo first.")
            return {"CANCELLED"}
        self.sel_indices = ",".join(str(i) for i in sel)
        return self.execute(context)

    def execute(self, context):
        if not self.sel_indices:
            self.report({"ERROR"}, "No selection recorded — run this from the "
                                   "button, not directly from the redo panel.")
            return {"CANCELLED"}

        top = _get_top(context)
        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        retopo = top.retopo_obj
        if retopo.type != "MESH":
            self.report({"ERROR"}, "Retopo must be a mesh object.")
            return {"CANCELLED"}

        was_edit = retopo.mode == "EDIT"
        if was_edit:
            bpy.ops.object.mode_set(mode="OBJECT")

        selected = [int(i) for i in self.sel_indices.split(",")]
        n_verts = len(retopo.data.vertices)
        selected = [i for i in selected if i < n_verts]

        props = top.seam
        from . import retopo_seam_sync

        wm = context.window_manager
        _progress_ctx = uic.ProgressScope(wm)
        _progress_ctx.__enter__()
        try:
            wm.progress_update(10)
            res = retopo_seam_sync.run_selected_vertex_sync(
                guide, retopo, flat_sk, selected,
                tol=_flat_len(props.retopo_sync_tol, guide, flat_sk),
                pin=self.pin,
                dry_run=not self.apply,
            )
            wm.progress_update(100)
        except Exception as exc:
            self.report({"ERROR"}, f"Sync Selected Vertex failed: {exc}")
            return {"CANCELLED"}
        finally:
            _progress_ctx.__exit__(None, None, None)
            if was_edit:
                bpy.ops.object.mode_set(mode="EDIT")

        unmatched_note = (
            f" {res['unmatched']} of {res['selected']} selected vertex/vertices "
            f"are not on the Guide outline at all and were left alone."
            if res["unmatched"] else ""
        )
        # A free-edge vertex has no partner to create — but it is still a
        # deliberate boundary point, so it gets pinned rather than skipped in
        # silence, which is what used to happen.
        free_note = (
            f" {res['free_only']} on free edges: "
            f"{'pinned' if (self.apply and self.pin) else 'would be pinned'} "
            f"(no partner needed)."
            if res.get("free_only") else ""
        )

        if not self.apply:
            shape = f"{res['missing_total']} new vertex/vertices over " \
                    f"{res['jobs']} seam(s), {res['already_synced']} pair(s) " \
                    f"already matching"
            top.status_boundary = f"Would sync: {shape}"
            self.report({"INFO"}, f"Would add {shape}.{free_note}"
                                  f"{unmatched_note} "
                                  f"Enable Apply to create/pin them.")
            return {"FINISHED"}

        if res["applied"] is None:
            top.status_boundary = "Nothing to sync"
            self.report({"WARNING" if res["unmatched"] else "INFO"},
                        f"Nothing to sync — every selected vertex on a seam "
                        f"already matches.{free_note}{unmatched_note}")
            return {"FINISHED"}

        ap = res["applied"]
        top.status_boundary = (
            f"Added {ap['created']} vertex/vertices, pinned {ap['pinned']}"
        )
        msg = (f"Added {ap['created']} vertex/vertices "
               f"({ap['new_chains']} new chain(s), {ap['extended']} extension(s), "
               f"{ap['inserted_mid']} mid-chain), pinned {ap['pinned']}.")
        if ap["ngons_created"]:
            msg += (f" {ap['ngons_created']} face(s) became n-gons where a "
                    f"vertex landed mid-chain — worth cleaning up.")
        msg += free_note
        msg += unmatched_note
        _refresh_density_pin_overlay(context, retopo)
        self.report({"WARNING"} if res["unmatched"] else {"INFO"}, msg)
        return {"FINISHED"}


class AC9_OT_SeamStatusReport(bpy.types.Operator):
    """Check whether the retopo actually pairs up on every sewn seam.

    Seams come from the GUIDE, not the retopo: each one is an anchor span
    pair on the Guide's pattern outline. Every sewn seam on the Guide is
    checked — there is nothing to select first. The retopo is then measured
    against them.

    Two numbers decide the verdict, and both are reported so a "matched"
    result can be checked rather than taken on trust:

      align  — how far apart corresponding vertices sit ALONG the seam. This
               is what has to be near zero for "coincident in 3D means sewn"
               to hold downstream, and over 1 mm of it is what "misaligned"
               (purple) means. Blank when the two sides hold different
               counts, since then there is no correspondence to measure.
      offset — how far the retopo strays from the Guide outline it should be
               lying on. A seam can pair perfectly and still sit off the
               pattern
    """

    bl_idname = "ac9_cloth.seam_status_report"
    bl_label = "Seam Status"
    # Written as a colour legend with explicit newlines rather than one
    # paragraph: this tooltip exists to be read WHILE looking at the coloured
    # outline, and a five-line wall of prose is not something you can scan
    # for "what does purple mean". Blender honours \n in a description (its
    # own bl_operators/anim.py and rigify do the same).
    bl_description = (
        "Check the whole Guide outline against the retopo.\n"
        "\n"
        "SEWN SEAMS\n"
        "• green = both sides pair up\n"
        "• red = the two sides hold a different NUMBER of vertices\n"
        "    (Match Seams or Generate fills that in)\n"
        "• purple = same number, but out of line along the seam\n"
        "    (those vertices have to be moved by hand)\n"
        "• orange = one side only\n"
        "\n"
        "FREE EDGES — hems, openings, no partner side\n"
        "• teal = the retopo sits on the outline\n"
        "• purple = it has drifted more than 1 mm off it\n"
        "\n"
        "Colours them in the viewport (Seam Status overlay), selects the "
        "vertices on the problem seams, and writes the full table to the "
        "Text 'AC9_SeamStatus'. Also counts T-junctions (a vertex lying on "
        "an edge it is not joined to)"
    )
    bl_options = {"REGISTER", "UNDO"}

    TEXT_NAME = "AC9_SeamStatus"

    select_problems: bpy.props.BoolProperty(
        name="Select Problem Seams",
        description=(
            "Select the retopo vertices on every seam that is mismatched or "
            "authored on one side only, and on every free-edge run that has "
            "drifted off the outline, so they can be found in the viewport "
            "without relying on the overlay"
        ),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        top = _get_top(context)
        if top is None or top.guide_obj is None or top.retopo_obj is None:
            cls.poll_message_set("Set the Guide and Retopo first.")
            return False
        if context.mode != "OBJECT":
            cls.poll_message_set("Exit Edit Mode first (Tab).")
            return False
        return True

    def execute(self, context):
        top = _get_top(context)
        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        retopo = top.retopo_obj
        props = top.seam

        from . import retopo_seam_sync as rss
        from . import anchor_segments as anch
        from . import gpu_overlay as ov

        wm = context.window_manager
        _progress_ctx = uic.ProgressScope(wm)
        _progress_ctx.__enter__()
        try:
            wm.progress_update(10)
            rows, summary = rss.classify_seam_pairs(
                guide, retopo, flat_sk, tol=_flat_len(props.retopo_sync_tol, guide, flat_sk)
            )
            wm.progress_update(50)
            # Free edges are half the panel boundary and classify_seam_pairs
            # cannot see them at all (it only walks sewn pairs), so they get
            # their own pass rather than being left out of the report.
            frows, fsummary = rss.classify_free_runs(
                guide, retopo, flat_sk, tol=_flat_len(props.retopo_sync_tol, guide, flat_sk),
                corner_angle_deg=props.gen_corner_angle_deg,
            )
            wm.progress_update(70)
            tjs = rss.find_t_junctions(retopo)
            wm.progress_update(80)
        except Exception as exc:
            self.report({"ERROR"}, f"Seam status failed: {exc}")
            return {"CANCELLED"}
        finally:
            _progress_ctx.__exit__(None, None, None)

        problem_states = rss.STATE_BAD + (rss.STATE_ONE_SIDED,)

        # ── Overlay: both sides of each seam, coloured by verdict ──────────
        flat_co = anch.get_flat_co(guide, flat_sk)
        segments = {}
        for r in rows:
            if r["state"] == rss.STATE_UNTOUCHED:
                continue
            bucket = segments.setdefault(r["state"], [])
            for span in (r["span_a"], r["span_b"]):
                for i in range(len(span) - 1):
                    bucket.append((flat_co[span[i]], flat_co[span[i + 1]]))
        for r in frows:
            bucket = segments.setdefault(r["state"], [])
            span = r["span"]
            for i in range(len(span) - 1):
                bucket.append((flat_co[span[i]], flat_co[span[i + 1]]))
        ov._cache["seam_status_segments"] = segments
        ov.build_seam_status_batches(segments, props.z_offset)
        ov._tag_redraw_3d(context)
        drawn = {s: (ov._batches.get(f"status_{s}") is not None)
                 for s in ov.STATUS_COLORS}

        # ── Select the problem vertices on the retopo ──────────────────────
        selected = 0
        if self.select_problems:
            problem_verts = set()
            for r in rows:
                if r["state"] in problem_states:
                    problem_verts.update(r["verts_a"])
                    problem_verts.update(r["verts_b"])
            for r in frows:
                if r["state"] == rss.STATE_FREE_OFF:
                    problem_verts.update(r["verts"])
            problem_verts.update(i for i, _edge in tjs)
            mesh = retopo.data
            for v in mesh.vertices:
                v.select = v.index in problem_verts
            for e in mesh.edges:
                e.select = False
            for f in mesh.polygons:
                f.select = False
            mesh.update()
            selected = len(problem_verts)
            try:
                context.view_layer.objects.active = retopo
                retopo.select_set(True)
            except Exception:
                pass

        # ── Report text ────────────────────────────────────────────────────
        def _fmt(v, width, prec):
            return f"{'':>{width}}" if v is None else f"{v:>{width}.{prec}f}"

        lines = [
            "AC9 Cloth Retopo — seam status",
            f"Guide : {guide.name}",
            f"Retopo: {retopo.name}",
            "",
            "Seams are the Guide's sewn anchor spans; every one is checked.",
            "Free edges (hems, openings) have no partner side, so they are",
            "checked instead against the Guide outline they should lie on,",
            "one row per continuous run — the same runs Generate divides.",
            "",
            f"sewn seams            : {summary['total']}",
            f"  matched             : {summary[rss.STATE_MATCHED]}",
            f"  counts differ       : {summary[rss.STATE_MISMATCH]}",
            f"  same count, out of line: {summary[rss.STATE_MISALIGNED]}",
            f"  one side only       : {summary[rss.STATE_ONE_SIDED]}",
            f"  no retopo yet       : {summary[rss.STATE_UNTOUCHED]}",
            "",
            f"free-edge runs        : {fsummary['runs']} "
            f"({fsummary['total']} with retopo on them)",
            f"  on the outline      : {fsummary[rss.STATE_FREE_OK]}",
            f"  off the outline     : {fsummary[rss.STATE_FREE_OFF]}",
            "",
            f"worst alignment along seam : {summary['worst_align_mm']:.3f} mm",
            f"worst offset from outline  : {summary['worst_offset_mm']:.3f} mm",
            f"seams whose counts differ  : {summary['unmeasurable']} "
            f"(alignment not measurable)",
            f"T-junctions (vertex on an edge it is not joined to): {len(tjs)}",
            "",
            f"retopo verts selected      : {selected}",
            f"overlay batches built      : "
            f"{', '.join(k for k, v in drawn.items() if v) or 'none'}",
            "",
            f"{'state':<11}{'a':>5}{'b':>5}{'align mm':>11}"
            f"{'offset mm':>11}{'seam mm':>10}",
            "-" * 53,
        ]
        for r in rows:
            if r["state"] == rss.STATE_UNTOUCHED:
                continue
            lines.append(
                f"{r['state']:<11}{r['count_a']:>5}{r['count_b']:>5}"
                f"{_fmt(r['align_mm'], 11, 3)}{r['offset_mm']:>11.2f}"
                f"{r['length_mm']:>10.0f}"
            )
        if summary[rss.STATE_UNTOUCHED]:
            lines.append("")
            lines.append(f"({summary[rss.STATE_UNTOUCHED]} seam(s) with no retopo "
                         f"yet are omitted from the table.)")

        if frows:
            lines += [
                "",
                "Free edges — one row per continuous run",
                f"{'state':<11}{'verts':>7}{'offset mm':>11}{'run mm':>10}",
                "-" * 39,
            ]
            for r in frows:
                lines.append(
                    f"{r['state']:<11}{r['count']:>7}{r['offset_mm']:>11.2f}"
                    f"{r['length_mm']:>10.0f}"
                )
        empty_free = fsummary["runs"] - fsummary["total"]
        if empty_free:
            lines.append("")
            lines.append(f"({empty_free} free-edge run(s) with no retopo yet "
                         f"are omitted from the table.)")

        if tjs:
            lines += ["", "T-junctions"]
            for i, (a, b) in tjs:
                lines.append(f"  v{i} on edge ({a}, {b})")

        _write_report(self.TEXT_NAME, lines, guide, retopo)

        msg = (f"{summary[rss.STATE_MATCHED]} matched, "
               f"{summary[rss.STATE_MISMATCH]} with differing counts, "
               f"{summary[rss.STATE_MISALIGNED]} out of line, "
               f"{summary[rss.STATE_ONE_SIDED]} one-sided "
               f"({summary[rss.STATE_UNTOUCHED]} untouched); "
               f"free {fsummary['total']} "
               f"({fsummary[rss.STATE_FREE_OFF]} off outline).")
        if tjs:
            msg += f" {len(tjs)} T-junction(s)."
        if selected:
            msg += f" Selected {selected} vert(s) on the problem seams."
        msg += f" Full table in Text '{self.TEXT_NAME}'."
        top.status_boundary = (
            f"{summary[rss.STATE_MATCHED]} ok / "
            f"{summary[rss.STATE_MISMATCH]} count / "
            f"{summary[rss.STATE_MISALIGNED]} line / "
            f"{summary[rss.STATE_ONE_SIDED]} half / "
            f"free {fsummary[rss.STATE_FREE_OFF]} off"
            f" / {len(tjs)} T"
        )
        level = ("WARNING" if (summary[rss.STATE_MISMATCH]
                               or summary[rss.STATE_MISALIGNED]
                               or fsummary[rss.STATE_FREE_OFF]
                               or tjs) else "INFO")
        self.report({level}, msg)
        return {"FINISHED"}


class AC9_OT_GenerateSeamChain(bpy.types.Operator):
    """Create retopo boundary on seams that have none yet.

    Match Seams can only mirror what one side already has, so a seam
    nobody has started stays empty. This lays down BOTH sides at once from
    the Guide: each side gets its vertices at the same positions along the
    seam, which puts them on the same points in 3D, ready for the faces to be
    built inward.

    Settings live in the panel above the button
    """

    bl_idname = "ac9_cloth.generate_seam_chain"
    bl_label = "Generate Boundary"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        top = _get_top(context)
        if top is None or top.guide_obj is None or top.retopo_obj is None:
            cls.poll_message_set("Set the Guide and Retopo first.")
            return False
        if context.mode != "OBJECT":
            cls.poll_message_set("Exit Edit Mode first (Tab).")
            return False
        return True

    def execute(self, context):
        top = _get_top(context)
        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        retopo = top.retopo_obj
        props = top.seam

        from . import retopo_seam_sync as rss

        nearest_to = None
        if props.gen_scope == 'CURSOR':
            nearest_to = context.scene.cursor.location.copy()

        wm = context.window_manager
        with uic.ProgressScope(wm) as prog:
            try:
                res = rss.run_generate_seam_chains(
                    guide, retopo, flat_sk,
                    count=props.gen_count,
                    spacing_mm=(props.gen_spacing_mm
                                if props.gen_divide_by == 'SPACING' else 0.0),
                    nearest_to=nearest_to,
                    min_seam_mm=props.gen_min_seam_mm,
                    corner_angle_deg=props.gen_corner_angle_deg,
                    symmetry_tolerance=props.symmetry_tolerance,
                    tol=_flat_len(props.retopo_sync_tol, guide, flat_sk),
                    targets=props.gen_targets,
                    # Flat-space (the straightness test runs on flat_co), so
                    # scaled by the packing -- unlike spacing_mm above, which is
                    # real 3D arc length and must NOT be scaled.
                    straight_tol_mm=(
                        _flat_len(props.gen_straight_tol_mm, guide, flat_sk)
                        if props.gen_divide_by == 'SPACING' else 0.0),
                    dry_run=False,
                    progress=prog.update,
                )
            except Exception as exc:
                self.report({"ERROR"}, f"Generate seam chain failed: {exc}")
                return {"CANCELLED"}

        notes = []
        if res["too_short"]:
            notes.append(f"{res['too_short']} under {props.gen_min_seam_mm:.0f}mm "
                         f"got their endpoints only (not divided)")
        if res["ambiguous"]:
            notes.append(f"{res['ambiguous']} whose flat outline folds back on "
                         f"itself (a zero-width slit cannot be divided)")
        if res.get("corners"):
            notes.append(f"{res['corners']} pattern corners pinned at "
                         f"{props.gen_corner_angle_deg:.0f}deg or sharper")
        if res.get("straight_dropped"):
            notes.append(f"{res['straight_dropped']} vertices left out on "
                         f"straight stretches (within "
                         f"{props.gen_straight_tol_mm:.1f}mm of a line)")
        if res.get("axis_mismatches"):
            notes.append(f"{res['axis_mismatches']} seam(s) skipped fold-axis "
                         f"pinning — the two sides' own symmetry points did not "
                         f"agree (assembled off-centre?)")
        note = f" Note: {'; '.join(notes)}." if notes else ""

        created = res["applied"]["created"] if res["applied"] else 0
        lengths = res["seam_lengths_mm"]

        # Report what was CREATED, not what was considered. A seam shorter
        # than the spacing works out to just its two anchors, and those
        # already exist because the neighbouring seams share them — so it is
        # entirely possible to pick a seam and add nothing to it.
        if created == 0:
            reason = ""
            if res["empty_seams"] and lengths:
                if props.gen_divide_by == 'SPACING' and lengths[0] < props.gen_spacing_mm:
                    reason = (f" The seam found is {lengths[0]:.0f}mm long, "
                              f"shorter than the {props.gen_spacing_mm:.0f}mm "
                              f"spacing, so it works out to just its two "
                              f"anchors — which already exist. Lower the "
                              f"spacing or switch to Count.")
                else:
                    reason = (" Its vertices are already there.")
            elif (not res["empty_seams"] and not res["free_edges"]
                    and not res.get("inherited_seams")):
                reason = " Nothing without retopo was found."
            top.status_boundary = "Generated nothing"
            self.report({"WARNING"}, f"Added no vertices.{reason}{note}")
            return {"FINISHED"}

        # A seam with retopo on one side only is divided by its partner, not
        # by the spacing — worth naming separately, since that is the part
        # the spacing setting has no say over.
        inherited = res.get("inherited_seams", 0)
        msg = (f"Added {created} vertices: {res['empty_seams']} seam(s) and "
               f"{res['free_edges']} free edge(s), {res['jobs']} chain(s).")
        if inherited:
            msg += (f" {inherited} one-sided seam(s) took their division from "
                    f"the side already built.")
        if res.get("dropped_on_edge"):
            msg += (f" {res['dropped_on_edge']} position(s) already had an "
                    f"edge running through them and were left alone — Match "
                    f"adds those if you want them.")
        if res["picked_distance"] is not None:
            msg += (f" Nearest seam was {res['picked_distance'] * 1000:.0f}mm "
                    f"from the cursor, {lengths[0]:.0f}mm long.")
        msg += note
        top.status_boundary = (
            f"Generated {res['empty_seams']} seam(s), {created} vert(s)"
        )
        self.report({"INFO"}, msg)
        return {"FINISHED"}


def _refresh_topo_corner_overlay(context, retopo):
    """Push the current corner set into the overlay cache."""
    from . import topology_corner as tc
    _cache["topo_corners"] = tc.positions(retopo)
    _cache["topo_corner_dirty"] = True
    _tag_redraw_3d(context)
    return len(_cache["topo_corners"])


def _refresh_density_pin_overlay(context, retopo):
    """Push the current density-pin set into the overlay cache."""
    from . import density_pin as dp
    _cache["density_pins"] = dp.positions(retopo)
    _cache["density_pin_dirty"] = True
    _tag_redraw_3d(context)
    return len(_cache["density_pins"])


class AC9_OT_MarkTopologyCorner(Operator):
    """Pin the selected vertices as topology corners

    A corner is where the edge flow is allowed to change direction. The
    generator cuts the outline at these and grids each patch between them, so
    marking one is a design decision, not a measurement: a point halfway down
    a side seam is a perfectly good corner even though nothing about the shape
    there says so
    """

    bl_idname = "ac9_cloth.mark_topology_corner"
    bl_label = "Mark Corner"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not _density_is_on(cls, context):
            return False
        top = _get_top(context)
        if top is None or top.retopo_obj is None:
            cls.poll_message_set("Set the Retopo first.")
            return False
        if top.retopo_obj.mode != 'EDIT':
            cls.poll_message_set("Select vertices on the Retopo in Edit Mode.")
            return False
        return True

    def execute(self, context):
        from . import topology_corner as tc
        top = _get_top(context)
        retopo = top.retopo_obj
        bm = bmesh.from_edit_mesh(retopo.data)
        sel = [v for v in bm.verts if v.select]
        if not sel:
            self.report({"WARNING"}, "Nothing selected.")
            return {"CANCELLED"}
        n = tc.mark(bm, sel, 1)
        bmesh.update_edit_mesh(retopo.data, loop_triangles=False,
                               destructive=False)
        retopo.update_from_editmode()
        total = _refresh_topo_corner_overlay(context, retopo)
        top.status_boundary = f"{total} corner(s)"
        self.report({"INFO"}, f"Marked {n} corner(s); {total} in total.")
        return {"FINISHED"}


class AC9_OT_ClearTopologyCorner(Operator):
    """Unpin the selected topology corners, or all of them"""

    bl_idname = "ac9_cloth.clear_topology_corner"
    bl_label = "Clear Corner"
    bl_options = {"REGISTER", "UNDO"}

    all_corners: bpy.props.BoolProperty(
        name="All",
        description="Clear every corner on the mesh, not just the selection",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        if not _density_is_on(cls, context):
            return False
        top = _get_top(context)
        if top is None or top.retopo_obj is None:
            cls.poll_message_set("Set the Retopo first.")
            return False
        if top.retopo_obj.mode != 'EDIT':
            cls.poll_message_set("Select vertices on the Retopo in Edit Mode.")
            return False
        return True

    def execute(self, context):
        from . import topology_corner as tc
        top = _get_top(context)
        retopo = top.retopo_obj
        bm = bmesh.from_edit_mesh(retopo.data)
        if self.all_corners:
            n = tc.clear_all(bm)
        else:
            sel = [v for v in bm.verts if v.select]
            if not sel:
                self.report({"WARNING"}, "Nothing selected.")
                return {"CANCELLED"}
            n = tc.mark(bm, sel, 0)
        bmesh.update_edit_mesh(retopo.data, loop_triangles=False,
                               destructive=False)
        retopo.update_from_editmode()
        total = _refresh_topo_corner_overlay(context, retopo)
        top.status_boundary = f"{total} corner(s)"
        self.report({"INFO"}, f"Cleared {n} corner(s); {total} left.")
        return {"FINISHED"}


class AC9_OT_DetectCornerCandidates(Operator):
    """Propose topology corners from the pattern's own geometry

    Uses the same measurement the boundary generator pins its divisions with,
    so every proposal already has a vertex sitting on it. It only proposes:
    add and remove by hand afterwards
    """

    bl_idname = "ac9_cloth.detect_corner_candidates"
    bl_label = "Detect Corners"
    bl_options = {"REGISTER", "UNDO"}

    replace: bpy.props.BoolProperty(
        name="Replace",
        description="Start from scratch instead of adding to what is marked",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        if not _density_is_on(cls, context):
            return False
        top = _get_top(context)
        if top is None or top.guide_obj is None or top.retopo_obj is None:
            cls.poll_message_set("Set the Guide and Retopo first.")
            return False
        if top.retopo_obj.mode != 'OBJECT':
            cls.poll_message_set("Leave Edit Mode first (Tab).")
            return False
        return True

    def execute(self, context):
        from . import topology_corner as tc
        top = _get_top(context)
        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        retopo = top.retopo_obj
        props = top.seam
        try:
            marked, missing, cleared, junctions = tc.detect_from_guide(
                guide, retopo, flat_sk,
                angle_deg=props.topo_corner_detect_deg,
                match_distance=props.match_distance_3d,
                replace=self.replace,
            )
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Corner detection failed: {exc}")
            return {"CANCELLED"}

        total = _refresh_topo_corner_overlay(context, retopo)
        top.status_boundary = f"{total} corner(s)"
        if not props.show_topo_corners:
            props.show_topo_corners = True
        note = ""
        if missing:
            note = (f" {missing} had no retopo vertex to sit on. Generate the "
                    f"boundary with a Corner Angle at least this sharp first.")
        self.report({"INFO"},
                    f"Proposed {marked} corner(s) at "
                    f"{props.topo_corner_detect_deg:.0f}deg "
                    f"({junctions} of them at seam junctions); {total} in "
                    f"total.{note}")
        return {"FINISHED"}


class AC9_OT_MarkDensityPin(Operator):
    """Pin the selected vertices against Adjust Density

    A pin protects a vertex from Adjust Density (Count/Spacing and Step
    alike): it is never deleted, dissolved, or slid to a new position when
    everything around it is re-laid-out. The typical use is a knife-cut
    vertex added to follow a fabric wrinkle in the outline — pin it and a
    later density change leaves it exactly where you put it. A pin is only
    safe when the SAME 3D position is also pinned on the seam's other
    side — Sync + Pin Selected does both sides at once
    """

    bl_idname = "ac9_cloth.mark_density_pin"
    bl_label = "Mark Pin"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not _density_is_on(cls, context):
            return False
        top = _get_top(context)
        if top is None or top.retopo_obj is None:
            cls.poll_message_set("Set the Retopo first.")
            return False
        if top.retopo_obj.mode != 'EDIT':
            cls.poll_message_set("Select vertices on the Retopo in Edit Mode.")
            return False
        return True

    def execute(self, context):
        from . import density_pin as dp
        top = _get_top(context)
        retopo = top.retopo_obj
        bm = bmesh.from_edit_mesh(retopo.data)
        sel = [v for v in bm.verts if v.select]
        if not sel:
            self.report({"WARNING"}, "Nothing selected.")
            return {"CANCELLED"}
        n = dp.mark(bm, sel, 1)
        bmesh.update_edit_mesh(retopo.data, loop_triangles=False,
                               destructive=False)
        retopo.update_from_editmode()
        total = _refresh_density_pin_overlay(context, retopo)
        top.status_boundary = f"{total} pin(s)"
        self.report({"INFO"}, f"Marked {n} pin(s); {total} in total.")
        return {"FINISHED"}


class AC9_OT_ClearDensityPin(Operator):
    """Unpin the selected density pins, or all of them"""

    bl_idname = "ac9_cloth.clear_density_pin"
    bl_label = "Clear Pin"
    bl_options = {"REGISTER", "UNDO"}

    all_pins: bpy.props.BoolProperty(
        name="All",
        description="Clear every pin on the mesh, not just the selection",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        if not _density_is_on(cls, context):
            return False
        top = _get_top(context)
        if top is None or top.retopo_obj is None:
            cls.poll_message_set("Set the Retopo first.")
            return False
        if top.retopo_obj.mode != 'EDIT':
            cls.poll_message_set("Select vertices on the Retopo in Edit Mode.")
            return False
        return True

    def execute(self, context):
        from . import density_pin as dp
        top = _get_top(context)
        retopo = top.retopo_obj
        bm = bmesh.from_edit_mesh(retopo.data)
        if self.all_pins:
            n = dp.clear_all(bm)
        else:
            sel = [v for v in bm.verts if v.select]
            if not sel:
                self.report({"WARNING"}, "Nothing selected.")
                return {"CANCELLED"}
            n = dp.mark(bm, sel, 0)
        bmesh.update_edit_mesh(retopo.data, loop_triangles=False,
                               destructive=False)
        retopo.update_from_editmode()
        total = _refresh_density_pin_overlay(context, retopo)
        top.status_boundary = f"{total} pin(s)"
        self.report({"INFO"}, f"Cleared {n} pin(s); {total} left.")
        return {"FINISHED"}


class AC9_OT_PreviewFill(Operator):
    """Fill panels with a throwaway quad mesh, to judge the boundary density

    Object Mode fills every panel. Edit Mode fills only the islands holding a
    selection, in place (the retopo stays in Edit Mode) — same scope rule as
    Grid Regions, and every other island keeps the fill it already had.

    Whether the outline carries the right number of vertices only becomes
    visible once there are faces. This lays a grid inside each panel, leaves
    every boundary vertex exactly where it is, and flags what it made so
    Clear takes it all back. It is not the final topology — look at it,
    adjust the density, run it again. Adjust Density clears it by itself
    whenever it has to rebuild a span
    """

    bl_idname = "ac9_cloth.preview_fill"
    bl_label = "Preview Fill"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        top = _get_top(context)
        if top is None or top.guide_obj is None or top.retopo_obj is None:
            cls.poll_message_set("Set the Guide and Retopo first.")
            return False
        if top.retopo_obj.mode not in {'OBJECT', 'EDIT'}:
            cls.poll_message_set("Retopo must be in Object or Edit Mode.")
            return False
        # Out of Experimental since 2026-09-09. shapely ships as a wheel now,
        # so this only fires where PyPI has no wheel (windows-arm64) or the
        # wheel did not install; the Faces panel says the same thing.
        if not uic.shapely_available():
            cls.poll_message_set(
                "Preview Fill needs shapely, which ships with the add-on.")
            return False
        return True

    def execute(self, context):
        top = _get_top(context)
        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        retopo = top.retopo_obj
        props = top.seam

        from . import preview_fill as pfill

        # Real fabric mm -> the flat layout's own mm. The boundary this fill
        # has to match was generated by division_parameters, which divides by
        # 3D arc length (real mm); the fill measures in the flat layout. They
        # only agree when the UV packing happens to be 1:1 with real scale --
        # measured 21.95 mm boundary against 20.00 mm interior on a shirt whose
        # ratio is 1.1245. See AUDIT §8-D-1.
        target = ((props.fill_target_mm or props.gen_spacing_mm)
                  * _pguide.flat_scale(guide, flat_sk))
        # Edit Mode is scoped to the selection, same as Grid Regions: the
        # user is working one panel at a time there, and a whole-mesh fill
        # would bury the rest of the layout in throwaway geometry.
        selected_only = retopo.mode == 'EDIT'
        wm = context.window_manager
        try:
            with uic.ProgressScope(wm):
                rep = pfill.run_preview_fill(
                    guide, retopo, flat_sk, target,
                    margin_factor=props.fill_margin_factor,
                    match_distance=props.match_distance_3d,
                    selected_only=selected_only,
                    progress=uic.ProgressThrottle(wm),
                )
        except pfill.ShapelyMissing as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Preview fill failed: {exc}")
            return {"CANCELLED"}

        if selected_only and rep.get("scope_verts", 1) == 0:
            self.report({"WARNING"},
                        "Nothing selected — select any part of an island.")
            return {"CANCELLED"}

        quads = rep.get("quads", 0)
        tris = rep.get("tris", 0)
        where = " (selected islands)" if selected_only else ""
        top.status_boundary = (f"Preview: {rep['regions']} panel(s), {quads} quads / "
                               f"{tris} tris at {target:.0f}mm{where}")
        note = ""
        if rep["skipped"]:
            note = (f" {rep['skipped']} panel(s) skipped — their outline does "
                    f"not close.")
        self.report({"INFO"},
                    f"Filled {rep['regions']} panel(s){where}: {quads} quads, "
                    f"{tris} tris, {rep['verts']} interior verts.{note}")
        return {"FINISHED"}


class AC9_OT_ClearPreviewFill(Operator):
    """Remove the preview fill, leaving the boundary exactly as it was

    Takes off every panel's fill, in Object Mode and in Edit Mode alike —
    the selection does not narrow it (Auto Fill is the scoped half)
    """

    bl_idname = "ac9_cloth.clear_preview_fill"
    bl_label = "Clear Fill"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        top = _get_top(context)
        if top is None or top.retopo_obj is None:
            cls.poll_message_set("Set the Retopo first.")
            return False
        if top.retopo_obj.mode not in {'OBJECT', 'EDIT'}:
            cls.poll_message_set("Retopo must be in Object or Edit Mode.")
            return False
        # Clearing needs no shapely: it only deletes what a fill left behind,
        # so it stays available to tidy up after a Blender that can run the
        # fill (a shared .blend opened where the wheel is missing).
        return True

    def execute(self, context):
        top = _get_top(context)
        from . import preview_fill as pfill
        res = pfill.run_clear_fill(top.retopo_obj)
        top.status_boundary = "Preview Fill cleared"
        self.report({"INFO"},
                    f"Removed {res['faces']} face(s) and {res['verts']} "
                    f"interior vert(s).")
        return {"FINISHED"}


def _regions_poll(cls, context):
    """Shared poll for Fill / Grid / Clear Regions.

    Object Mode runs on the whole object. Edit Mode runs on the islands that
    hold a selection, so the Retopo must be the object being edited.
    """
    top = _get_top(context)
    if top is None or top.retopo_obj is None:
        cls.poll_message_set("Set the Retopo first.")
        return False
    ob = top.retopo_obj
    if ob.mode == 'EDIT' and context.view_layer.objects.active is not ob:
        cls.poll_message_set("Edit the Retopo object itself, or leave Edit Mode.")
        return False
    if ob.mode not in ('OBJECT', 'EDIT'):
        cls.poll_message_set("Leave the current mode first (Tab).")
        return False
    return True


def _run_scoped(ob, fn):
    """Run `fn(selected_only)` on `ob`, stepping out of Edit Mode and back.

    The region tools build a bmesh from the mesh data, so an Edit Mode
    session is committed first and reopened after; selection (which is what
    the run uses as its scope and what it leaves behind as its result) lives
    in the mesh data and survives both switches. Returns (result, was_edit).
    """
    edit = ob.mode == 'EDIT'
    if edit:
        bpy.ops.object.mode_set(mode='OBJECT')
    try:
        return fn(edit), edit
    finally:
        if edit:
            bpy.ops.object.mode_set(mode='EDIT')


class AC9_OT_FillRegions(Operator):
    """Put one n-gon in every closed region, so the Knife has something to cut

    The Knife cuts faces and a bare boundary has none. This fills each closed
    region with a single face — no vertices added, nothing moved — and Clear
    takes them back off. Run it again after cutting and it re-fills whatever
    the cuts now describe. In Edit Mode only the islands holding a selection
    are filled
    """

    bl_idname = "ac9_cloth.fill_regions"
    bl_label = "Fill Regions"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        # Legacy: superseded by Connect (fill) / Grid Regions; not drawn in
        # any panel, reachable only via F3 — keep it behind Experimental.
        if not uic.experimental_enabled(context):
            return False
        return _regions_poll(cls, context)

    def execute(self, context):
        from . import panel_regions as prg
        top = _get_top(context)
        try:
            rep, edit = _run_scoped(
                top.retopo_obj,
                lambda sel: prg.run_fill_regions(top.retopo_obj,
                                                 selected_only=sel))
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Fill Regions failed: {exc}")
            return {"CANCELLED"}
        if edit and rep.get("scope_verts", 1) == 0:
            self.report({"WARNING"},
                        "Nothing selected — select any part of an island.")
            return {"CANCELLED"}
        if not rep["faces"]:
            self.report({"WARNING"},
                        "Nothing to fill — every region already has a face.")
            return {"FINISHED"}
        note = ""
        if rep["open_edges"]:
            note = (f" {rep['open_edges']} edge(s) are still not part of any "
                    f"region — their outline does not close.")
        top.status_faces = f"Filled {rep['faces']} region(s)"
        self.report({"INFO"},
                    f"Filled {rep['faces']} region(s), largest "
                    f"{rep['sizes'][0]} vertices. Cut them with the Knife "
                    f"(K).{note}")
        return {"FINISHED"}


class AC9_OT_GridRegions(Operator):
    """Grid every region the cuts made, taking the corners from the cuts

    A vertex where a cut line lands is a corner by construction, so most of
    them need no marking. Structural notches — a stepped hem, the inside of an
    armhole — are found by measurement and count as corners too. Where a region
    is still short of four, the sharpest convex turns make up the difference
    and each one is reported.

    Cut lines are divided first, once, so the regions either side of one share
    its vertices. The boundary is never moved or added to. In Edit Mode only
    the islands holding a selection are gridded; the rest are left as they are
    """

    bl_idname = "ac9_cloth.grid_regions"
    bl_label = "Grid Regions"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not uic.experimental_enabled(context):
            return False
        return _regions_poll(cls, context)

    def execute(self, context):
        from . import panel_regions as prg
        top = _get_top(context)
        props = top.seam
        # Real fabric mm -> flat layout mm, so the interior matches the
        # boundary division_parameters produced in real 3D mm. See AUDIT §8-D-1.
        target = ((props.grid_target_mm or props.gen_spacing_mm)
                  * _pguide.flat_scale(top.guide_obj, top.guide_flat_shapekey))
        wm = context.window_manager
        try:
            with uic.ProgressScope(wm):
                rep, edit = _run_scoped(
                    top.retopo_obj,
                    lambda sel: prg.run_grid_regions(
                        top.retopo_obj, target,
                        smooth_passes=props.grid_smooth_passes,
                        band=props.grid_band_fallback, selected_only=sel,
                        progress=uic.ProgressThrottle(wm)))
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Grid Regions failed: {exc}")
            return {"CANCELLED"}
        if edit and rep.get("scope_verts", 1) == 0:
            self.report({"WARNING"},
                        "Nothing selected — select any part of an island.")
            return {"CANCELLED"}

        where = " (selected islands)" if edit else ""
        top.status_faces = (f"{rep['gridded']}/{rep['regions']} region(s), "
                           f"{rep['quads']} quads / {rep['tris']} tris at "
                           f"{target:.0f}mm{where}")
        if rep["regions"] == 0:
            self.report({"WARNING"},
                        "No region to grid — press Connect first.")
            return {"FINISHED"}
        if rep["gridded"] == 0:
            why = rep["skipped"][0] if rep["skipped"] else "no region qualified"
            self.report({"WARNING"}, f"Nothing gridded. First reason: {why}")
            return {"FINISHED"}
        note = ""
        if rep["skipped"]:
            note = (f" {len(rep['skipped'])} region(s) left alone — now "
                    "selected (Tab into Edit Mode to see which).")
        if rep.get("loose_ends"):
            note += (f" {rep['loose_ends']} open line end(s) take no part in "
                     f"any region — press Connect to join them first.")
        if rep["self_intersecting"]:
            note += (f" {len(rep['self_intersecting'])} banded region(s) "
                    "crossed themselves — now selected, check these.")
        if rep["tris"]:
            note += (f" The {rep['tris']} band triangle(s) are selected.")
        if rep.get("kinks"):
            note += (f" {len(rep['kinks'])} sharp mid-side turn(s) — vertex "
                     "selected; a cut from there would straighten the rows.")
        auto = len(rep["auto_corners"])
        self.report({"INFO"},
                    f"Gridded {rep['gridded']} of {rep['regions']} region(s): "
                    f"{rep['matched']} as a pure grid, "
                    f"{rep.get('strips', 0)} as straight rungs, "
                    f"{rep['banded']} with a band. "
                    f"{rep['quads']} quads, {rep['tris']} tris, "
                    f"{rep['verts']} interior verts, {auto} corner(s) chosen "
                    f"automatically.{note}")
        # Also as individual WARNING reports: unlike print(), these land in
        # Blender's own Info Log (drag down the top strip), reachable
        # without a system console.
        for line in rep["skipped"]:
            self.report({"WARNING"}, f"Grid Regions: {line}")
        for line in rep["self_intersecting"]:
            self.report({"WARNING"}, f"Grid Regions: {line} — now selected")
        for line in rep.get("kinks", ()):
            self.report({"WARNING"}, f"Grid Regions: {line}")
        for line in rep["auto_corners"]:
            print(f"[AC9] Grid Regions chose a corner at {line}")
        return {"FINISHED"}


class AC9_OT_PatchGrid(Operator):
    """Grid every panel whose outline carries exactly four marked corners

    A structured lattice goes inside the outline and is stitched to it side by
    side, so the interior has real rows and columns instead of a triangulation.
    The boundary is never moved and never added to: opposite sides of a panel
    almost never carry the same number of vertices (a zigzag hem needs more
    than the smooth edge facing it), and that difference becomes triangles in
    the band next to the boundary rather than a change to the seam.

    Panels with more or fewer than four corners are listed and left alone —
    dividing an outline into patches comes next
    """

    bl_idname = "ac9_cloth.patch_grid"
    bl_label = "Patch Grid"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        # Legacy: superseded by Connect (fill) / Grid Regions; not drawn in
        # any panel, reachable only via F3 — keep it behind Experimental.
        if not uic.experimental_enabled(context):
            return False
        top = _get_top(context)
        if top is None or top.guide_obj is None or top.retopo_obj is None:
            cls.poll_message_set("Set the Guide and Retopo first.")
            return False
        if top.retopo_obj.mode != 'OBJECT':
            cls.poll_message_set("Leave Edit Mode first (Tab).")
            return False
        return True

    def execute(self, context):
        top = _get_top(context)
        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        retopo = top.retopo_obj
        props = top.seam

        from . import patch_grid as pgrid

        # Real fabric mm -> flat layout mm. See AUDIT §8-D-1.
        target = ((props.grid_target_mm or props.gen_spacing_mm)
                  * _pguide.flat_scale(guide, flat_sk))
        wm = context.window_manager
        try:
            with uic.ProgressScope(wm):
                rep = pgrid.run_patch_grid(
                    guide, retopo, flat_sk, target,
                    smooth_passes=props.grid_smooth_passes,
                    match_distance=props.match_distance_3d,
                    progress=uic.ProgressThrottle(wm),
                )
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Patch grid failed: {exc}")
            return {"CANCELLED"}

        top.status_faces = (f"{rep['gridded']}/{rep['panels']} panel(s), "
                           f"{rep['quads']} quads / {rep['tris']} tris at "
                           f"{target:.0f}mm")
        if rep["gridded"] == 0:
            why = rep["skipped"][0] if rep["skipped"] else "no panel qualified"
            self.report({"WARNING"},
                        f"Nothing gridded ({rep['panels']} panel(s) seen). "
                        f"First reason: {why}")
            return {"FINISHED"}
        note = ""
        if rep["skipped"]:
            note = f" {len(rep['skipped'])} panel(s) left alone."
        self.report({"INFO"},
                    f"Gridded {rep['gridded']} of {rep['panels']} panel(s): "
                    f"{rep['quads']} quads, {rep['tris']} tris, "
                    f"{rep['verts']} interior verts.{note}")
        return {"FINISHED"}


class AC9_OT_ClearPatchGrid(Operator):
    """Remove the grid and the scaffold, keeping the boundary and the cuts

    Takes back everything this tool made — grid faces, their interior
    vertices, the scaffold n-gons and the divisions it put on the cut lines —
    and leaves the boundary and the user's own cut lines untouched. Clearing
    the cut-line divisions is what lets the next run use a different spacing.
    In Edit Mode only the islands holding a selection are cleared
    """

    bl_idname = "ac9_cloth.clear_patch_grid"
    bl_label = "Clear Regions"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        # NOT gated on Experimental, unlike grid_regions itself: this also
        # removes the scaffold n-gons Connect makes, and Connect is a normal
        # tool. Gating it (2026-09-09) left the default UI with no way to
        # take a Connect back. With Grid off there is simply no grid to
        # remove and the report says 0.
        return _regions_poll(cls, context)

    def execute(self, context):
        top = _get_top(context)
        from . import panel_regions as prg
        try:
            res, edit = _run_scoped(
                top.retopo_obj,
                lambda sel: prg.run_clear_regions(top.retopo_obj,
                                                  selected_only=sel))
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Clear Regions failed: {exc}")
            return {"CANCELLED"}
        if edit and res.get("scope_verts", 1) == 0:
            self.report({"WARNING"},
                        "Nothing selected — select any part of an island.")
            return {"CANCELLED"}
        top.status_faces = ("Regions cleared (selected islands)" if edit
                            else "Regions cleared")
        self.report({"INFO"},
                    f"Removed {res['grid_faces']} grid face(s), "
                    f"{res['grid_verts']} interior vert(s), "
                    f"{res['scaffold_faces']} scaffold face(s) and "
                    f"{res['line_verts']} cut-line division(s). Cuts kept.")
        return {"FINISHED"}


class AC9_OT_ConnectLooseEnds(Operator):
    """Extend every open line until it meets an edge, and join it there

    A pocket outline generated as an open U, or a seam line that ends in the
    middle of the panel, closes no region: Fill covers it and the Knife
    cannot start from it. This runs each loose end on along its last segment
    to the first edge in its way and adds that edge, shortest first, so ends
    that stop just short of each other meet. The outline is never split — a
    ray that reaches it joins the nearest existing boundary vertex, so no
    seam needs re-syncing. After this the lines are part of the faces and
    can be cut from with the Knife like any other edge
    """

    bl_idname = "ac9_cloth.connect_loose_ends"
    bl_label = "Connect Loose Ends"
    bl_options = {"REGISTER", "UNDO"}

    extend_selected: BoolProperty(
        name="Extend Selected",
        description="Extend from the selected vertices instead of from every loose end",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return _regions_poll(cls, context)

    def execute(self, context):
        from . import panel_regions as prg
        top = _get_top(context)
        extend = bool(self.extend_selected)
        wm = context.window_manager
        try:
            with uic.ProgressScope(wm):
                rep, edit = _run_scoped(
                    top.retopo_obj,
                    lambda sel: prg.run_connect_loose_ends(
                        top.retopo_obj, selected_only=sel,
                        extend_selected=extend and sel,
                        progress=uic.ProgressThrottle(wm)))
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Connect Loose Ends failed: {exc}")
            return {"CANCELLED"}
        if edit and rep.get("scope_verts", 1) == 0:
            self.report({"WARNING"},
                        "Nothing selected — select any part of an island.")
            return {"CANCELLED"}
        n = len(rep["connected"])
        if n == 0:
            why = ""
            if rep["unresolved"]:
                why = " First reason: %s at (%.0f, %.0f) mm." % (
                    rep["unresolved"][0][1], *rep["unresolved"][0][0])
            if rep.get("stubs"):
                why += (" %d stub(s) under 5 mm (Knife overshoots) removed."
                        % len(rep["stubs"]))
            if extend:
                self.report({"WARNING"},
                            f"Nothing extended — select a loose end or a "
                            f"cut-line corner.{why}")
            else:
                top.status_faces = f"Filled {rep['faces']} region(s)"
                self.report({"INFO"},
                            f"Filled {rep['faces']} region(s); no open line "
                            f"to connect.{why} Cut them with the Knife (K).")
            return {"FINISHED"}
        msg = (f"Connected {n} end(s): {rep['split']} split an edge, "
               f"{rep['reused']} met a vertex. Filled {rep['faces']} region(s).")
        if rep.get("crossings"):
            msg += (f" {len(rep['crossings'])} crossing(s) with other cuts "
                    f"given a vertex.")
        if rep.get("pinched"):
            msg += (f" {rep['pinched']} region(s) could not be closed — "
                    f"lines crossing without a vertex?")
        if rep["outline"]:
            msg += (f" {len(rep['outline'])} reached the outline at an "
                    f"existing boundary vertex (no new seam vertex).")
        if rep["unresolved"]:
            msg += f" {len(rep['unresolved'])} end(s) left alone."
        if rep.get("stubs"):
            pts = ", ".join("(%.0f, %.0f)" % p for p in rep["stubs"][:6])
            msg += (f" {len(rep['stubs'])} stub(s) under 5 mm removed at {pts}"
                    f" mm (Knife overshoots).")
        top.status_faces = f"Connected {n} loose end(s)"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class AC9_OT_ConnectRows(Operator):
    """Run a rung between each pair of vertices of two selected lines

    Select the EDGES of two lines in Edit Mode — two cut lines down a panel,
    or a cut line and the outline beside it — and each vertex of one is
    joined to its partner on the other, cutting straight through whatever
    faces lie between them (the same thing J does by hand, once per pair).
    The two lines are lined up end to end automatically, so it does not
    matter which direction either was drawn in.

    If one line carries fewer vertices than the other, the missing ones are
    added to it, spread into its widest gaps and taken from the longer line's
    own spacing; its existing vertices are not moved. The outline is never
    divided this way — its vertices are paired with the sewn partner side in
    3D, so use Adjust Density on it instead
    """

    bl_idname = "ac9_cloth.connect_rows"
    bl_label = "Connect Rows"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        top = _get_top(context)
        if top is None or top.retopo_obj is None:
            cls.poll_message_set("Set the Retopo first.")
            return False
        if context.mode != 'EDIT_MESH':
            cls.poll_message_set("Select two lines on the Retopo in Edit Mode.")
            return False
        ob = top.retopo_obj
        if ob.mode != 'EDIT' or context.view_layer.objects.active is not ob:
            cls.poll_message_set("Edit the Retopo object itself.")
            return False
        return True

    def execute(self, context):
        from . import connect_rows as crw

        top = _get_top(context)
        retopo = top.retopo_obj
        mesh = retopo.data

        bm = bmesh.from_edit_mesh(mesh)
        selected = [e for e in bm.edges if e.select]
        if not selected:
            self.report({"ERROR"},
                        "Nothing selected — select the edges of two lines.")
            return {"CANCELLED"}
        try:
            line_a, line_b, plan, _swapped = crw.plan_rungs(bm, selected)
        except crw.ChainError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        # Everything above only read the mesh; the edits start here.
        inserted = 0
        if plan:
            try:
                line_a, added = crw.apply_insertions(bm, line_a, plan)
            except crw.ChainError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            inserted = len(added)
            bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)

        if not crw.check_monotonic(
                crw.arc_params([v.co.copy() for v in line_a]),
                crw.arc_params([v.co.copy() for v in line_b])):
            self.report({"ERROR"},
                        "The rungs would cross — the two lines could not be "
                        "matched up.")
            return {"CANCELLED"}

        # Coordinates, not vertices: vert_connect_path re-allocates the mesh,
        # so every BMVert held across one call is dangling afterwards.
        pairs = [(a.co.copy(), b.co.copy()) for a, b in zip(line_a, line_b)]

        tool = context.tool_settings
        prev_select_mode = tuple(tool.mesh_select_mode)
        tool.mesh_select_mode = (True, False, False)

        wm = context.window_manager
        connected = skipped = failed = 0
        _progress_ctx = uic.ProgressScope(wm)
        _progress_ctx.__enter__()
        prog = uic.ProgressThrottle(wm)
        try:
            n_pairs = len(pairs)
            for index, (pos_a, pos_b) in enumerate(pairs):
                prog(index / max(1, n_pairs))
                bm = bmesh.from_edit_mesh(mesh)
                bm.verts.ensure_lookup_table()
                bm.edges.ensure_lookup_table()
                bm.faces.ensure_lookup_table()
                vert_a = crw.find_vertex_by_position(bm, pos_a)
                vert_b = crw.find_vertex_by_position(bm, pos_b)
                if vert_a is None or vert_b is None:
                    failed += 1
                    continue
                if bm.edges.get((vert_a, vert_b)) is not None:
                    skipped += 1
                    continue
                for v in bm.verts:
                    v.select_set(False)
                for e in bm.edges:
                    e.select_set(False)
                for f in bm.faces:
                    f.select_set(False)
                bm.select_history.clear()
                vert_a.select_set(True)
                vert_b.select_set(True)
                bm.select_history.add(vert_a)
                bm.select_history.add(vert_b)
                bmesh.update_edit_mesh(mesh, loop_triangles=False,
                                       destructive=False)
                # From here bm / vert_a / vert_b must not be touched again.
                result = bpy.ops.mesh.vert_connect_path()
                if 'FINISHED' in result:
                    connected += 1
                else:
                    failed += 1
        finally:
            _progress_ctx.__exit__(None, None, None)
            tool.mesh_select_mode = prev_select_mode

        # Leave the rungs selected, so what happened is visible.
        bm = bmesh.from_edit_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        for v in bm.verts:
            v.select_set(False)
        for e in bm.edges:
            e.select_set(False)
        for f in bm.faces:
            f.select_set(False)
        bm.select_history.clear()
        for pos_a, pos_b in pairs:
            vert_a = crw.find_vertex_by_position(bm, pos_a)
            vert_b = crw.find_vertex_by_position(bm, pos_b)
            if vert_a is None or vert_b is None:
                continue
            edge = bm.edges.get((vert_a, vert_b))
            if edge is not None:
                # select_set on an edge takes its two vertices with it; a
                # select_flush(True) on top would drag in every OTHER edge
                # whose ends happen to be selected — the lines themselves.
                edge.select_set(True)
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)

        msg = f"Connect Rows: {connected} rung(s)"
        if inserted:
            msg += f", {inserted} vertex(es) added to the shorter line"
        if skipped:
            msg += f", {skipped} already joined"
        if failed:
            msg += f", {failed} failed"
        top.status_faces = msg
        self.report({"WARNING"} if connected == 0 else {"INFO"}, msg)
        return {"FINISHED"}


class AC9_OT_ResampleSpanDensity(Operator):
    """Change the selected seam span(s) to a different vertex count.

    Select retopo boundary EDGES in Edit Mode — an edge identifies the span it
    runs along, and the sewn partner side comes with it, so one side is enough.

    ALL THREE modes now try the NON-destructive path first (span_density.
    resample_groups_incremental — local subdivide/pointmerge edits, each
    one validated against every affected face before the mesh is touched).
    Count and Spacing used to go straight to the destructive rebuild
    (delete every interior vertex, lay fresh ones down) regardless of
    whether a safer route existed — changed 2026-09-02 after the user
    pointed out that Step already solves the "how do I do this without
    losing faces" problem, so Count/Spacing reusing that solution rather
    than a separate always-destructive path was the obvious fix. Only a
    GROUP the non-destructive pass could not find any safe plan for at all
    (a fan of interior faces sharing one hub vertex is the textbook case:
    changing that vertex ring's count degenerates some triangle no matter
    which vertex moves where) falls back to the destructive rebuild for
    just that group — see the two-phase call in execute() below.
    """

    bl_idname = "ac9_cloth.resample_span_density"
    bl_label = "Adjust Density"
    bl_description = (
        "Change how many vertices the selected span(s) carry — both sides of "
        "the sewn seam together, so they keep matching in 3D. Select boundary "
        "edges (or one interior boundary vertex) on the Retopo in Edit Mode. "
        "Tries a local edit that keeps every face first, and rebuilds a span "
        "only where no safe edit exists (which also clears the Preview Fill). "
        "Pins and Corners stay exactly where they are"
    )
    bl_options = {"REGISTER", "UNDO"}

    mode: bpy.props.EnumProperty(
        name="Mode",
        items=[
            ('DELTA', "Step", "Add or remove vertices relative to the count now"),
            ('COUNT', "Count", "Give the span exactly this many vertices"),
            ('SPACING', "Spacing", "Pick the count so vertices land this far apart"),
        ],
        default='DELTA',
    )
    delta: bpy.props.IntProperty(
        name="Step", description="How many vertices to add (negative removes)",
        default=1,
    )

    @classmethod
    def poll(cls, context):
        if not _density_is_on(cls, context):
            return False
        top = _get_top(context)
        if top is None or top.guide_obj is None or top.retopo_obj is None:
            cls.poll_message_set("Set the Guide and Retopo first.")
            return False
        retopo = top.retopo_obj
        if retopo.mode != 'EDIT':
            cls.poll_message_set("Select boundary edges on the Retopo in Edit Mode.")
            return False
        return True

    def execute(self, context):
        top = _get_top(context)
        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        retopo = top.retopo_obj
        props = top.seam

        from . import span_density as sd

        wm = context.window_manager
        with uic.ProgressScope(wm) as prog:
            # Push the live edit-mode geometry into the mesh datablock first: the
            # Guide analysis reads retopo.data through its own bmesh, which would
            # otherwise see the state as of the last Tab out.
            retopo.update_from_editmode()
            prog.update(0.05)

            try:
                ctx = sd.build_context(guide, retopo, flat_sk,
                                       match_distance=props.match_distance_3d,
                                       tol=_flat_len(props.retopo_sync_tol, guide, flat_sk))
            except Exception as exc:
                self.report({"ERROR"}, f"Span analysis failed: {exc}")
                return {"CANCELLED"}

            bm = bmesh.from_edit_mesh(retopo.data)
            bm.verts.ensure_lookup_table()
            bm.edges.ensure_lookup_table()

            step_only = self.mode == 'DELTA'
            # Preview Fill is no longer cleared up front for Count/Spacing: the
            # non-destructive pass tried below (see the two-phase call further
            # down) leaves it untouched whenever it succeeds outright, and
            # clearing it here unconditionally would be needless rework on
            # exactly the runs that never needed the destructive fallback at
            # all. It is only cleared right before that fallback actually runs.

            picked, unmatched, on_anchor = sd.spans_from_selection(
                bm, ctx, retopo.matrix_world)
            if not picked:
                if on_anchor:
                    hint = (f" {on_anchor} selected vertex/vertices sit ON an "
                            f"anchor, which belongs to every span meeting there — "
                            f"select a second vertex along the span you mean, or "
                            f"one that isn't an anchor.")
                elif unmatched:
                    hint = (" The selection crosses an anchor or sits off the "
                            "Guide outline.")
                else:
                    hint = " Select boundary vertices or edges along a seam."
                self.report({"WARNING"}, f"No span identified.{hint}")
                return {"CANCELLED"}

            groups = sd.group_with_partners(ctx, picked)
            prog.update(0.70)
            # DELTA baselines come from the actual boundary chains, not the raw
            # assignment — knife-cut spurs near an anchor pad the assignment and
            # made "-1" a silent no-op on padded pairs (see sd.chain_counts).
            counts = sd.chain_counts(bm, ctx, groups)
            targets = [sd.target_count(ctx, g, self.mode, delta=self.delta,
                                       count=props.dens_count,
                                       spacing_mm=props.dens_spacing_mm,
                                       counts=counts)
                       for g in groups]

            befores = [max(counts.get(si, ctx.count(si)) for si in g)
                       for g in groups]
            # Nothing to do is worth saying out loud: a "-1" on a span already at
            # its two anchors silently doing nothing reads as a broken button.
            if all(b == t for b, t in zip(befores, targets)):
                top.status_boundary = f"{len(groups)} span(s) already at target"
                self.report({"WARNING"},
                            f"{len(groups)} span(s) already at "
                            f"{targets[0] if targets else 0} vertices; nothing changed.")
                return {"CANCELLED"}

            for v in bm.verts:
                v.select_set(False)

            fallback_stats = None
            try:
                stats = sd.resample_groups_incremental(
                    bm, ctx, groups, targets, retopo.matrix_world)
                if not step_only and stats["fallback_groups"]:
                    # Phase B: only the groups the non-destructive pass found NO
                    # safe plan for at all fall back to the destructive rebuild
                    # — re-resolve context first, since Phase A's own edits (on
                    # OTHER groups) already shifted vertex indices out from
                    # under ctx.assign.
                    retopo.update_from_editmode()
                    ctx = sd.build_context(guide, retopo, flat_sk,
                                           match_distance=props.match_distance_3d,
                                           tol=_flat_len(props.retopo_sync_tol, guide, flat_sk))
                    bm = bmesh.from_edit_mesh(retopo.data)
                    bm.verts.ensure_lookup_table()
                    bm.edges.ensure_lookup_table()

                    # Same reasoning the old always-destructive path had for
                    # clearing Preview Fill up front — deferred until we know
                    # the destructive rebuild is actually going to run, since
                    # every group Phase A already solved never needed this.
                    from . import preview_fill as pfill
                    n_f, n_v = pfill.clear_fill(bm)
                    if n_f or n_v:
                        bm.verts.ensure_lookup_table()
                        bm.edges.ensure_lookup_table()

                    fb_groups = [g for g, _t in stats["fallback_groups"]]
                    fb_targets = [t for _g, t in stats["fallback_groups"]]
                    fallback_stats = sd.resample_groups(
                        bm, ctx, fb_groups, fb_targets, retopo.matrix_world,
                        mode=self.mode, spacing_mm=props.dens_spacing_mm)
            except Exception as exc:
                self.report({"ERROR"}, f"Resample failed: {exc}")
                return {"CANCELLED"}

            bm.select_flush(True)
            # Geometry count changed -> loop_triangles=True is REQUIRED, or Blender
            # segfaults on the next redraw (see memory: bmesh_update_edit_mesh_crash).
            bmesh.update_edit_mesh(retopo.data, loop_triangles=True, destructive=True)

        span_pairs = len(groups)
        changed = ", ".join(f"{b}->{t}"
                            for b, t in list(zip(befores, targets))[:4])
        if len(groups) > 4:
            changed += ", ..."

        if step_only:
            top.status_boundary = (f"{span_pairs} span group(s): {changed}  "
                                f"(+{stats['inserted']} / -{stats['removed']}, "
                                f"0 faces deleted)")
            notes = []
            if stats["skipped_ambiguous"]:
                notes.append(f"{stats['skipped_ambiguous']} folded span(s) skipped")
            if stats["skipped_broken_chain"]:
                notes.append(f"{stats['skipped_broken_chain']} span(s) had a "
                             f"changed chain mid-edit, skipped")
            if stats["skipped_at_floor"]:
                notes.append(f"{stats['skipped_at_floor']} already at the "
                             f"2-vertex floor")
            if stats["skipped_ungenerated_partner"]:
                notes.append(f"{stats['skipped_ungenerated_partner']} span(s) "
                             f"skipped (partner side not generated yet)")
            if stats["skipped_no_safe_edit"]:
                notes.append(f"{stats['skipped_no_safe_edit']} span(s) skipped: "
                             f"no way to add/remove a vertex there without "
                             f"destroying or folding a face — clear or re-run "
                             f"Fill around that seam, then retry")
            if unmatched:
                notes.append(f"{unmatched} selected edge(s) matched no span")
            if stats["hand_made_faces_touched"]:
                notes.append(f"{stats['hand_made_faces_touched']} hand-made "
                             f"face(s) reshaped, not deleted")
            if stats["stopped_face_loss"]:
                notes.append(f"WARNING: stopped early on {stats['stopped_face_loss']} "
                             f"span(s) to avoid deleting a face — check the mesh")
            note = ("  " + "; ".join(notes)) if notes else ""
            self.report({"INFO"},
                        f"Resampled {stats['spans']} span side(s) in {span_pairs} "
                        f"group(s): {changed}. "
                        f"+{stats['inserted']} / -{stats['removed']} vertices, "
                        f"0 faces deleted.{note}")
        else:
            # Count/Spacing: `stats` is the non-destructive pass everything
            # went through first; `fallback_stats` (may be None) is the
            # destructive rebuild that ran ONLY on the groups Phase A found
            # no safe plan for at all — see the two-phase call above.
            n_fallback_groups = len(stats["fallback_groups"])
            fb_created = fallback_stats["created"] if fallback_stats else 0
            fb_removed = fallback_stats["removed"] if fallback_stats else 0
            faces_lost = fallback_stats["faces_lost"] if fallback_stats else 0
            total_created = stats["inserted"] + fb_created
            total_removed = stats["removed"] + fb_removed
            total_spans = stats["spans"] + (fallback_stats["spans"] if fallback_stats else 0)

            top.status_boundary = (f"{span_pairs} span group(s): {changed}  "
                                f"(+{total_created} / -{total_removed}, "
                                f"{faces_lost} face(s) lost)")
            notes = []
            if stats["skipped_ambiguous"]:
                notes.append(f"{stats['skipped_ambiguous']} folded span(s) skipped")
            if stats["skipped_broken_chain"]:
                notes.append(f"{stats['skipped_broken_chain']} span(s) had a "
                             f"changed chain mid-edit, skipped")
            if stats["skipped_ungenerated_partner"]:
                notes.append(f"{stats['skipped_ungenerated_partner']} span(s) "
                             f"skipped (partner side not generated yet)")
            if n_fallback_groups:
                notes.append(f"{n_fallback_groups} group(s) had no safe "
                             f"non-destructive plan, rebuilt destructively "
                             f"instead ({faces_lost} face(s) lost there)")
            if fallback_stats and fallback_stats["welded"]:
                notes.append(f"{fallback_stats['welded']} welded")
            if unmatched:
                notes.append(f"{unmatched} selected edge(s) matched no span")
            if stats["hand_made_faces_touched"]:
                notes.append(f"{stats['hand_made_faces_touched']} hand-made "
                             f"face(s) reshaped, not deleted")
            if stats["stopped_face_loss"]:
                notes.append(f"WARNING: stopped early on {stats['stopped_face_loss']} "
                             f"span(s) to avoid deleting a face — check the mesh")
            note = ("  " + "; ".join(notes)) if notes else ""
            self.report({"INFO"},
                        f"Resampled {total_spans} span side(s) in {span_pairs} "
                        f"group(s): {changed}. "
                        f"+{total_created} / -{total_removed} vertices, "
                        f"{faces_lost} face(s) lost.{note}")
        return {"FINISHED"}


class AC9_OT_EvenOutSpanDensity(Operator):
    """Straighten the selected seam span(s) back to evenly-spaced u — same
    vertex count, no face touched.

    "Phase 0" of the Adjust Density redesign (Opus+Fable design review,
    2026-09-01 session): unlike Resample Span Density, this NEVER deletes or
    creates a vertex, edge, or face — it only moves existing interior
    vertices, the same way Ghost Snap Move does. Safe to run after Grid
    Regions / Patch Grid / hand-cutting with the Knife has filled the span's
    interior with faces.

    Select retopo boundary EDGES in Edit Mode — an edge identifies the span
    it runs along, and the sewn partner side comes with it automatically.
    """

    bl_idname = "ac9_cloth.even_out_span_density"
    bl_label = "Even Out Density"
    bl_description = (
        "Re-space the selected span's vertices evenly along the seam, both "
        "sides together. Only moves existing vertices — never adds or removes "
        "one, never touches a face. Select boundary edges on the Retopo in "
        "Edit Mode"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not _density_is_on(cls, context):
            return False
        top = _get_top(context)
        if top is None or top.guide_obj is None or top.retopo_obj is None:
            cls.poll_message_set("Set the Guide and Retopo first.")
            return False
        retopo = top.retopo_obj
        if retopo.mode != 'EDIT':
            cls.poll_message_set("Select boundary edges on the Retopo in Edit Mode.")
            return False
        return True

    def execute(self, context):
        top = _get_top(context)
        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        retopo = top.retopo_obj
        props = top.seam

        from . import span_density as sd

        wm = context.window_manager
        with uic.ProgressScope(wm) as prog:
            retopo.update_from_editmode()
            prog.update(0.05)

            try:
                ctx = sd.build_context(guide, retopo, flat_sk,
                                       match_distance=props.match_distance_3d,
                                       tol=_flat_len(props.retopo_sync_tol, guide, flat_sk))
            except Exception as exc:
                self.report({"ERROR"}, f"Span analysis failed: {exc}")
                return {"CANCELLED"}

            bm = bmesh.from_edit_mesh(retopo.data)
            bm.verts.ensure_lookup_table()
            bm.edges.ensure_lookup_table()

            # No preview-fill clear here on purpose — that is exactly the face
            # loss this operator exists to avoid (see Resample Span Density's
            # comment on the same clear, and this class's docstring).

            picked, unmatched, on_anchor = sd.spans_from_selection(
                bm, ctx, retopo.matrix_world)
            if not picked:
                if on_anchor:
                    hint = (f" {on_anchor} selected vertex/vertices sit ON an "
                            f"anchor, which belongs to every span meeting there — "
                            f"select a second vertex along the span you mean, or "
                            f"one that isn't an anchor.")
                elif unmatched:
                    hint = (" The selection crosses an anchor or sits off the "
                            "Guide outline.")
                else:
                    hint = " Select boundary vertices or edges along a seam."
                self.report({"WARNING"}, f"No span identified.{hint}")
                return {"CANCELLED"}

            groups = sd.group_with_partners(ctx, picked)
            prog.update(0.75)

            for v in bm.verts:
                v.select_set(False)

            try:
                stats = sd.even_out_groups(bm, ctx, groups, retopo.matrix_world)
            except Exception as exc:
                self.report({"ERROR"}, f"Even Out failed: {exc}")
                return {"CANCELLED"}

            bm.select_flush(True)
            # No geometry count change, but positions did -- same crash guard
            # Resample Span Density uses (see memory: bmesh_update_edit_mesh_crash).
            bmesh.update_edit_mesh(retopo.data, loop_triangles=True, destructive=True)

        notes = []
        if stats["skipped_ambiguous"]:
            notes.append(f"{stats['skipped_ambiguous']} folded span(s) skipped")
        if stats["skipped_too_short"]:
            notes.append(f"{stats['skipped_too_short']} span(s) had no interior to straighten")
        if stats["skipped_broken_chain"]:
            notes.append(f"{stats['skipped_broken_chain']} span(s) had a "
                         f"fragmented boundary chain, skipped")
        if unmatched:
            notes.append(f"{unmatched} selected edge(s) matched no span")
        note = ("  " + "; ".join(notes)) if notes else ""
        top.status_boundary = f"Evened out {stats['spans']} span side(s), {stats['moved']} vert(s) moved"
        self.report({"INFO"},
                    f"Evened out {stats['spans']} span side(s): "
                    f"{stats['moved']} vertex/vertices repositioned, "
                    f"0 faces touched.{note}")
        return {"FINISHED"}


class AC9_OT_AnalyzeAnchors(Operator):
    """Find the Guide's anchor points and show where the spans divide.

    Anchors are the boundary points pinned in 3D by the sewing itself: where
    three or more panels meet, and where a sewn seam turns into a free edge.
    They are what cut the pattern outline into spans, and a span is the unit
    every density edit works in — so this is the map for Adjust Density.

    Read-only: nothing on the Guide or the retopo is touched
    """

    bl_idname = "ac9_cloth.analyze_anchors"
    bl_label = "Analyze Anchors"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        top = _get_top(context)
        if top is None or top.guide_obj is None:
            cls.poll_message_set("Set the Guide first.")
            return False
        return True

    def execute(self, context):
        top = _get_top(context)
        guide, flat_sk, err = _resolve_guide(top)
        if err is not None:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        props = top.seam

        from . import anchor_segments as anch
        from .gpu_overlay import build_anchor_batch

        wm = context.window_manager
        with uic.ProgressScope(wm) as prog:
            try:
                bverts, vns, parts = anch.find_vertex_partners(
                    guide, match_distance=props.match_distance_3d)
                prog.update(0.40)
                _a, _b, basis_co = anch._collect_boundary(guide)
                flat_co = anch.get_flat_co(guide, flat_sk)
                prog.update(0.70)
                anchors = anch.find_anchors(bverts, vns, parts)
                spans = anch.build_spans(vns, anchors)
                prog.update(0.90)
            except Exception as exc:
                self.report({"ERROR"}, f"Anchor analysis failed: {exc}")
                return {"CANCELLED"}

        # One marker per POSITION, not per vertex: the two sides of a seam meet
        # at the same 3D anchor but are separate vertices, and in the flat
        # layout they land on different panels — both are worth showing, so
        # dedupe on the flat position rather than on the 3D one.
        seen = set()
        points = []
        indices = []
        for v in anchors:
            p = flat_co[v]
            key = (round(p.x, 6), round(p.y, 6))
            if key in seen:
                continue
            seen.add(key)
            points.append(p)
            indices.append(v)

        _cache["anchors"] = points
        _cache["anchor_indices"] = indices
        _cache["anchor_spans"] = len(spans)
        _cache["anchor_dirty"] = False
        build_anchor_batch(points, props.z_offset, props.anchor_cross_size)

        if not props.show_anchors:
            props.show_anchors = True
        _tag_redraw_3d(context)
        self.report({"INFO"},
                    f"Anchors: {len(points)} marker(s) from {len(anchors)} "
                    f"anchor vert(s); boundary divides into {len(spans)} spans.")
        return {"FINISHED"}
