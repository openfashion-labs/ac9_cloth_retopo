"""Create Flat SK — native replacement for the third-party "UV Flatten Tool"
add-on's "Generate Seams" mode, so a fresh Guide needs no extra install.

Splits the GUIDE along its UV island boundaries (bmesh only: no
``bpy.ops.uv.*`` and no area-type switching, both of which break headless)
and writes the (u, v, 0) layout into a ShapeKey named ``"<uv_name>_Flattened"``,
then points Setup's "Flat SK" at it.

Step 1 of Guide Prep, and it runs on the Guide (Setup's picker) like every
other button in the tab. It used to run over ``context.selected_objects`` so
several CLO pieces could be flattened in one press; that made this the only
place in the add-on where the selection decided what was touched, and the tab
read as two different tools (2026-09-09 — asked for by the same user who had
asked for the selection behaviour in the first place). Several objects are
flattened by pointing the Guide at each in turn, which is a step that has to
happen anyway before anything else can be done to them.

Island-boundary detection reimplements what ``uv.seams_from_islands`` does,
without leaving Edit Mode or touching the UV editor context:

  - an edge with 0 or 1 linked faces is a mesh boundary — trivially a UV
    island boundary too, nothing to split
  - an edge with 2+ linked faces is a boundary when, for either of its
    vertices, that vertex's UV differs between the faces on the two sides
  - an edge already marked ``seam`` counts regardless (a hand-placed seam
    should still end up split)

Every detected edge gets ``edge.seam = True`` (so the result is visible in
the UV editor, matching the old tool), but only edges with 2+ linked faces
are handed to ``bmesh.ops.split_edges`` — a boundary edge has nothing to
split.
"""

import math

import bmesh
import bpy
from bpy.props import BoolProperty, StringProperty

from . import ui_common as uic
from .clo_projector import guide as _pguide


def _top(context):
    return context.scene.ac9_cloth_retopo


def _uv_finite(uv) -> bool:
    return math.isfinite(uv[0]) and math.isfinite(uv[1])


def _flat_layer_name(bm, wanted):
    """`wanted` if the bmesh has that shape layer, else None."""
    if wanted and wanted in bm.verts.layers.shape.keys():
        return wanted
    return None


def _uv_groups(vert, uv_layer, tol=1e-6):
    """Group `vert`'s loops by UV value (greedy, `tol` per axis).
    Returns [[BMLoop, ...], ...], one list per distinct UV."""
    groups = []
    for l in vert.link_loops:
        uv = l[uv_layer].uv
        for g in groups:
            ref = g[0][uv_layer].uv
            if abs(ref.x - uv.x) <= tol and abs(ref.y - uv.y) <= tol:
                g.append(l)
                break
        else:
            groups.append([l])
    return groups


def _split_bowtie_verts(bm, uv_layer):
    """Separate every vertex whose loops still carry more than one UV value
    after split_edges. Each extra UV group's faces get their own vertex via
    bmesh.utils.vert_separate on the edges those faces use at the vertex.
    Returns the number of vertices separated."""
    count = 0
    for v in list(bm.verts):
        groups = _uv_groups(v, uv_layer)
        if len(groups) < 2:
            continue
        for g in groups[1:]:
            faces = {l.face for l in g}
            edges = [e for e in v.link_edges
                     if e.link_faces and all(f in faces for f in e.link_faces)]
            if edges:
                bmesh.utils.vert_separate(v, edges)
        count += 1
    return count


def flatten_object(obj, uv_name, triangulate):
    """Flatten one mesh object's UV layer into a ShapeKey.

    Returns (verts_before, verts_after, seam_count, mismatches), or None if
    `uv_name` (or the mesh's active UV layer, when `uv_name` is empty) does
    not resolve to an actual UV layer.
    """
    mesh = obj.data

    if uv_name:
        if uv_name not in mesh.uv_layers:
            return None
    else:
        active = mesh.uv_layers.active
        if active is None:
            return None
        uv_name = active.name

    verts_before = len(mesh.vertices)

    bm = bmesh.new()
    bm.from_mesh(mesh)  # carries existing shape-key layers through too

    if triangulate:
        bmesh.ops.triangulate(bm, faces=bm.faces[:])

    uv_layer = bm.loops.layers.uv.get(uv_name)
    if uv_layer is None:
        bm.free()
        return None

    # --- detect UV island boundaries, seam them, collect what to split
    to_split = []
    seam_count = 0
    for e in bm.edges:
        linked = e.link_faces
        detected = e.seam or len(linked) < 2
        if not detected:
            vert_uv = {}
            for l in e.link_loops:
                nxt = l.link_loop_next
                for v, uv in ((l.vert, l[uv_layer].uv),
                             (nxt.vert, nxt[uv_layer].uv)):
                    prev = vert_uv.get(v)
                    if prev is None:
                        vert_uv[v] = (uv[0], uv[1])
                    elif abs(prev[0] - uv[0]) > 1e-6 or abs(prev[1] - uv[1]) > 1e-6:
                        detected = True
        if detected:
            e.seam = True
            seam_count += 1
            if len(linked) >= 2:
                to_split.append(e)

    bmesh.ops.split_edges(bm, edges=to_split)

    # --- "bowtie" vertices: two UV islands touching at ONE vertex with no
    # shared edge. split_edges cannot separate those (there is no edge to
    # split), so the vertex would keep one island's UV and drag the other
    # island's faces across the layout as long slivers. Measured on a
    # production jacket after a partial Merge by Distance across left/right
    # panels of differing topology: 19 such vertices, 64 sliver edges (20x
    # the median flat edge length). Separate the vertex per UV value instead.
    bowties = _split_bowtie_verts(bm, uv_layer)

    # --- one UV per vertex now (verify) and build the flat coordinates
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()

    flat_co = [0.0] * (len(bm.verts) * 3)
    mismatches = 0
    for v in bm.verts:
        uv_val = None
        for l in v.link_loops:
            uv = l[uv_layer].uv
            if uv_val is None:
                uv_val = (uv[0], uv[1])
            elif abs(uv_val[0] - uv[0]) > 1e-6 or abs(uv_val[1] - uv[1]) > 1e-6:
                mismatches += 1
        if uv_val is None:  # isolated vertex, no loops at all
            uv_val = (0.0, 0.0)
        i = v.index * 3
        flat_co[i], flat_co[i + 1], flat_co[i + 2] = uv_val[0], uv_val[1], 0.0

    verts_after = len(bm.verts)

    bm.to_mesh(mesh)
    mesh.update()
    bm.free()

    if mesh.shape_keys is None:
        obj.shape_key_add(name="Basis", from_mix=False)

    sk_name = f"{uv_name}_Flattened"
    kb = mesh.shape_keys.key_blocks.get(sk_name)
    if kb is None:
        kb = obj.shape_key_add(name=sk_name, from_mix=False)
    kb.data.foreach_set("co", flat_co)
    kb.value = 1.0  # show the flat layout right after creating it
    mesh.update()

    return verts_before, verts_after, seam_count, mismatches, sk_name, bowties


class AC9_OT_FlattenUVToSK(bpy.types.Operator):
    """Step 1. Split the Guide along its UV island seams and store the
    flattened (u, v, 0) layout as a ShapeKey — the native equivalent of the
    third-party "UV Flatten Tool" add-on's "Generate Seams" mode. Setup's
    "Flat SK" is pointed at the new key, and every analysis derived from the
    old one is dropped. Works on the Guide, in the Guide's Object Mode"""

    bl_idname = "ac9_cloth.flatten_uv_to_sk"
    bl_label = "Create Flat SK"
    bl_options = {'REGISTER', 'UNDO'}

    uv_layer: StringProperty(
        name="UV Layer",
        description="UV layer to flatten. Empty uses the Guide's active UV layer",
        default="",
    )
    triangulate: BoolProperty(
        name="Triangulate",
        description=(
            "Triangulate the Guide before flattening. CLO Projector requires "
            "an all-triangle Guide (validate_guide rejects any n-gon or "
            "quad), so this is on by default — turn it off only if the Guide "
            "is already triangulated and you want to keep its exact topology"
        ),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        top = _top(context)
        guide = top.guide_obj if top is not None else None
        if guide is None or guide.type != 'MESH':
            cls.poll_message_set("Set the Guide first.")
            return False
        if guide.mode != 'OBJECT':
            cls.poll_message_set("Leave the Guide's Edit Mode first.")
            return False
        return True

    def execute(self, context):
        top = _top(context)
        guide = top.guide_obj if top is not None else None
        if guide is None or guide.type != 'MESH':
            self.report({'ERROR'}, "Set the Guide first.")
            return {'CANCELLED'}
        if guide.mode != 'OBJECT':
            self.report({'ERROR'}, "The Guide must be in Object Mode.")
            return {'CANCELLED'}

        # An unapplied object scale has to be refused HERE rather than warned
        # about, because the order is not recoverable: this writes the raw UV
        # into the ShapeKey's local coordinates, so the scale shrinks the flat
        # layout in world space (measured: a 413 mm layout lands as 0.41 mm at
        # scale 0.001, what an FBX import leaves behind), and applying the
        # scale afterwards scales the ShapeKey too — the key stays exactly as
        # wrong while the object starts passing the scale check. The only fix
        # is to apply the scale and flatten again, so say so before the work.
        if _pguide.object_scale_error(guide) is not None:
            s = guide.matrix_world.to_scale()
            self.report(
                {'ERROR'},
                f"Create Flat SK: the Guide '{guide.name}' has an unapplied "
                f"object scale ({s.x:.4g}, {s.y:.4g}, {s.z:.4g}). Apply it "
                f"(Object > Apply > Scale) first: the flat layout is stored in "
                f"local coordinates, so the scale would shrink it in world "
                f"space and applying it afterwards does not repair the key. "
                f"The 3D shape is fine either way; only the flat layout is "
                f"affected.")
            return {'CANCELLED'}

        wm = context.window_manager
        with uic.ProgressScope(wm):
            result = flatten_object(guide, self.uv_layer, self.triangulate)

        if result is None:
            msg = (f"Create Flat SK: '{guide.name}' has no UV layer to "
                   f"flatten" + (f" ('{self.uv_layer}')" if self.uv_layer else ""))
            if top is not None:
                top.clo.status = msg
            self.report({'WARNING'}, msg)
            return {'CANCELLED'}

        verts_before, verts_after, _seams, mismatches, sk_name, bowties = result
        top.guide_flat_shapekey = sk_name
        # The Guide's flat layout is the space every seam pair, ghost, fold and
        # anchor is measured in, so re-making it invalidates all of them.
        # Assigning guide_flat_shapekey above only resets them when the NAME
        # changes; re-flattening to the same name moves the coordinates and
        # used to leave the old analysis in place.
        from .uv_seam_guide import gpu_overlay as _seam_overlay
        from .clo_projector import core as _pcore
        from .clo_projector import gpu_overlay as _proj_overlay
        _seam_overlay.reset_guide_derived(context.scene)
        _proj_overlay.reset_guide_derived()
        _pcore.invalidate_guide_cache(guide)

        msg = (f"Flat SK '{sk_name}' on '{guide.name}': "
               f"{verts_before:,} -> {verts_after:,} verts")
        if bowties:
            msg += f", {bowties} point-contact verts separated"
        if mismatches:
            msg += f", {mismatches} UV mismatches (unexpected)"
        top.clo.status = msg
        self.report({'WARNING' if mismatches else 'INFO'}, msg)
        return {'FINISHED'}


class AC9_OT_RepairGuideNonfinite(bpy.types.Operator):
    """Put every Guide vertex that has no valid position (NaN) back onto the
    average of its finite neighbours, in mesh coordinates and in every
    ShapeKey. A NaN vertex is a hole in the data, not a small error: nothing
    can be measured against it, so the analyses fail deep inside numpy or
    mathutils instead of at the button (measured: "arange: cannot compute
    length" from Analyze Guide, "KeyError: (nan, nan, nan)" from Inset Line).
    Check that part of the mesh afterwards — the repaired vertices are within
    a neighbour's distance of where the drape had them, not exactly on it"""

    bl_idname = "ac9_cloth.repair_guide_nonfinite"
    bl_label = "Repair Guide"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        top = _top(context)
        guide = top.guide_obj if top is not None else None
        if guide is None or guide.type != 'MESH':
            cls.poll_message_set("Set the Guide first.")
            return False
        if guide.mode != 'OBJECT':
            cls.poll_message_set("Leave the Guide's Edit Mode first.")
            return False
        return True

    def execute(self, context):
        from .clo_cleanup import core as _cc

        top = _top(context)
        guide = top.guide_obj if top is not None else None
        if guide is None or guide.type != 'MESH':
            self.report({'ERROR'}, "Set the Guide first.")
            return {'CANCELLED'}
        if guide.mode != 'OBJECT':
            self.report({'ERROR'}, "The Guide must be in Object Mode.")
            return {'CANCELLED'}

        mesh = guide.data
        n_before = _pguide.nonfinite_vertex_count(guide, top.guide_flat_shapekey)
        bm = bmesh.new()
        bm.from_mesh(mesh)          # carries the shape-key layers through
        n_verts_before = len(bm.verts)
        repaired = _cc.sanitize_nonfinite(bm, layers=None)
        # The UVs need the same treatment, and they are the reason this
        # matters: Create Flat SK builds the Flat SK FROM the UV, so a repair
        # that left a NaN UV in place would hand the NaN straight back on the
        # next press (review finding, 2026-09-09). A vertex whose flat
        # ShapeKey is now finite gives the UV back exactly; otherwise the
        # finite UVs around the loop do.
        uv_lay = bm.loops.layers.uv.active
        uv_fixed = 0
        if uv_lay is not None:
            flat_name = _flat_layer_name(bm, top.guide_flat_shapekey)
            flat_lay = (bm.verts.layers.shape.get(flat_name)
                        if flat_name else None)
            for v in bm.verts:
                bad = [l for l in v.link_loops
                       if not _uv_finite(l[uv_lay].uv)]
                if not bad:
                    continue
                good = [l[uv_lay].uv.copy() for l in v.link_loops
                        if _uv_finite(l[uv_lay].uv)]
                if flat_lay is not None and _cc.co_finite(v[flat_lay]):
                    fix = v[flat_lay].xy
                elif good:
                    fix = good[0]
                    for extra in good[1:]:
                        fix = fix + extra
                    fix = fix / len(good)
                else:
                    continue
                for l in bad:
                    l[uv_lay].uv = fix
                    uv_fixed += 1
        deleted = n_verts_before - len(bm.verts)
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()

        # The census is memoised, and every reader's cache was built on the
        # old coordinates.
        _pguide.invalidate_nonfinite(guide)
        _pguide.invalidate_flat_per_real(guide)
        from .uv_seam_guide import gpu_overlay as _seam_overlay
        from .clo_projector import core as _pcore
        from .clo_projector import gpu_overlay as _proj_overlay
        _seam_overlay.reset_guide_derived(context.scene)
        _proj_overlay.reset_guide_derived()
        _pcore.invalidate_guide_cache(guide)

        if not repaired:
            msg = f"Repair Guide: '{guide.name}' had no invalid position"
            if top is not None:
                # Same result line as its neighbour Triangulate Guide: both
                # are Guide repairs offered from Setup's blockers.
                top.clo.status = msg
            self.report({'INFO'}, msg)
            return {'CANCELLED'}
        msg = (f"Repair Guide: {repaired} vertex(es) on '{guide.name}' had no "
               f"valid position (NaN) and were moved onto the average of "
               f"their finite neighbours")
        if uv_fixed:
            msg += f"; {uv_fixed} UV corner(s) repaired with them"
        if deleted:
            msg += (f"; {deleted} with no finite neighbour at all were "
                    f"deleted - a Retopo built against this Guide has stored "
                    f"triangle indices that no longer line up, so re-run "
                    f"Refresh Mirror and check it")
        msg += " - check that part of the mesh, then Analyze Guide again"
        if n_before != repaired:
            msg += f" (census said {n_before})"
        if top is not None:
            top.clo.status = msg
        self.report({'WARNING'}, msg)
        return {'FINISHED'}


class AC9_OT_TriangulateGuide(bpy.types.Operator):
    """Triangulate the faces of the Guide that are not triangles already.

    The projector reads the Guide as triangles (a retopo vertex is stored as
    a triangle index plus barycentric weights), so an all-triangle Guide is a
    hard requirement — validate_guide rejects anything else and 3D Mirror
    stops. Create Flat SK triangulates as it runs, but a Guide can pick up
    n-gons afterwards, so this is the repair: no vertex is added or moved,
    the Flat SK and the 2D layout the retopo was built against are untouched,
    only a diagonal is added inside each offending face"""

    bl_idname = "ac9_cloth.triangulate_guide"
    bl_label = "Triangulate Guide"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        top = _top(context)
        guide = top.guide_obj if top is not None else None
        if guide is None or guide.type != 'MESH':
            cls.poll_message_set("Set the Guide first.")
            return False
        if guide.mode != 'OBJECT':
            cls.poll_message_set("Leave the Guide's Edit Mode first.")
            return False
        return True

    def execute(self, context):
        from .clo_projector.guide import count_non_tris, guide_all_tris

        top = _top(context)
        guide = top.guide_obj
        mesh = guide.data

        if guide_all_tris(mesh):
            self.report({'INFO'}, f"'{guide.name}' is already all triangles.")
            return {'CANCELLED'}
        n_before, _first = count_non_tris(mesh)

        bm = bmesh.new()
        bm.from_mesh(mesh)      # carries the shape-key layers through
        ngons = [f for f in bm.faces if len(f.verts) > 3]
        bmesh.ops.triangulate(bm, faces=ngons, quad_method='BEAUTY',
                              ngon_method='BEAUTY')
        bm.normal_update()
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()

        # Triangulating renumbers the polygons, and a stored attachment is a
        # polygon index — leave them and a retopo vertex would read a
        # different triangle's corners. The cache is keyed on the polygon
        # count so it drops itself, but the on-mesh attachments do not.
        from .clo_projector import core as _pcore
        from .clo_projector.attachment import clear_attachments_on_mesh
        _pcore.invalidate_guide_cache(guide)
        retopo = top.retopo_obj
        cleared = retopo is not None and retopo.type == 'MESH'
        if cleared:
            clear_attachments_on_mesh(retopo.data)

        msg = f"Triangulated {n_before} face(s) on '{guide.name}'"
        msg += "; stored attachments cleared" if cleared else ""
        if top is not None:
            top.clo.status = msg
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def get_classes():
    return (AC9_OT_FlattenUVToSK, AC9_OT_RepairGuideNonfinite,
            AC9_OT_TriangulateGuide)
