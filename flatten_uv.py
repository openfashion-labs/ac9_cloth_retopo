"""Create Flat SK — native replacement for the third-party "UV Flatten Tool"
add-on's "Generate Seams" mode, so a fresh Guide needs no extra install.

Splits every selected mesh along its UV island boundaries (bmesh only: no
``bpy.ops.uv.*`` and no area-type switching, both of which break headless)
and writes the (u, v, 0) layout into a ShapeKey named ``"<uv_name>_Flattened"``.
Runs over ``context.selected_objects`` (Prepare's step 1, before Cleanup's
Folds / Lines / Pieces), not just the Guide — a CLO export is usually several
pieces, each its own object.

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

import bmesh
import bpy
from bpy.props import BoolProperty, StringProperty


def _top(context):
    return context.scene.ac9_cloth_retopo


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

    return verts_before, verts_after, seam_count, mismatches, sk_name


class AC9_OT_FlattenUVToSK(bpy.types.Operator):
    """Split every selected mesh along its UV island seams and store the
    flattened (u, v, 0) layout as a ShapeKey — the native equivalent of the
    third-party "UV Flatten Tool" add-on's "Generate Seams" mode. Objects
    without a UV layer are skipped. If the Guide (Setup's picker) is among
    the selection, its "Flat SK" is updated to the new key name"""

    bl_idname = "ac9_cloth.flatten_uv_to_sk"
    bl_label = "Create Flat SK"
    bl_options = {'REGISTER', 'UNDO'}

    uv_layer: StringProperty(
        name="UV Layer",
        description="UV layer to flatten. Empty uses each mesh's active UV layer",
        default="",
    )
    triangulate: BoolProperty(
        name="Triangulate",
        description=(
            "Triangulate each mesh before flattening. CLO Projector requires "
            "an all-triangle Guide (validate_guide rejects any n-gon or "
            "quad), so this is on by default — turn it off only if the mesh "
            "is already triangulated and you want to keep its exact topology"
        ),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            cls.poll_message_set("Create Flat SK runs in Object Mode.")
            return False
        if not any(o.type == 'MESH' for o in context.selected_objects):
            cls.poll_message_set("Select at least one mesh.")
            return False
        return True

    def execute(self, context):
        top = _top(context)
        guide = top.guide_obj if top is not None else None

        mesh_objs = [o for o in context.selected_objects if o.type == 'MESH']

        processed = 0
        skipped = 0
        total_before = 0
        total_after = 0
        total_mismatches = 0
        last_sk_name = ""

        wm = context.window_manager
        wm.progress_begin(0, 100)
        n_objs = len(mesh_objs)
        try:
            for i, obj in enumerate(mesh_objs):
                result = flatten_object(obj, self.uv_layer, self.triangulate)
                if result is None:
                    skipped += 1
                else:
                    verts_before, verts_after, seam_count, mismatches, sk_name = result
                    processed += 1
                    total_before += verts_before
                    total_after += verts_after
                    total_mismatches += mismatches
                    last_sk_name = sk_name
                    if guide is not None and obj == guide:
                        top.guide_flat_shapekey = sk_name
                if n_objs:
                    wm.progress_update(int((i + 1) / n_objs * 100))
        finally:
            wm.progress_end()

        msg = (f"Flat SK: {processed} objects, "
               f"{total_before:,} -> {total_after:,} verts")
        if skipped:
            msg += f" ({skipped} skipped: no UV)"
        if total_mismatches:
            msg += f", {total_mismatches} UV mismatches (unexpected)"

        if top is not None:
            top.clo.status = msg

        if processed == 0:
            self.report({'WARNING'}, msg)
            return {'CANCELLED'}
        self.report({'WARNING' if total_mismatches else 'INFO'}, msg)
        return {'FINISHED'}


def get_classes():
    return (AC9_OT_FlattenUVToSK,)
