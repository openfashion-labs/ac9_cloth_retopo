"""CLO Cleanup operators. All act on the active mesh object (the CLO export,
not the Retopo).

Object Mode:  clo_inset
Edit Mode:    clo_inset_line, clo_tag_selected (selected edges),
              clo_select_tagged
Either mode:  clo_tag_by_angle, clo_clear_tags
"""

import bmesh
import bpy
from bpy.props import EnumProperty

from . import core

_FLAT_SK_ERROR = "needs a planar shape key (the UV layout as geometry, e.g. UV_Map_Flattened)"


def _props(context):
    return context.scene.ac9_cloth_retopo.clo


def _set_status(context, text):
    _props(context).status = text


def _mesh_active(context):
    ob = context.active_object
    return ob is not None and ob.type == 'MESH'


def _flat_args(ob, bm):
    """(flat_key, basis_key, active_key) when the mesh carries a planar shape
    key (its UV layout as geometry), else None: the insets then run in 3D."""
    sk = ob.data.shape_keys
    if sk is None or sk.reference_key is None:
        return None
    ref = sk.reference_key.name
    name = core.find_flat_layer(bm, exclude=(ref,))
    if name is None:
        return None
    active = ob.active_shape_key.name if ob.active_shape_key is not None else ref
    return (name, ref, active)


def _bm_read(ob):
    """(bm, is_edit): a bmesh of the active object in either mode."""
    if ob.mode == 'EDIT':
        return bmesh.from_edit_mesh(ob.data), True
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    return bm, False


def _bm_commit(bm, ob, is_edit, destructive):
    if is_edit:
        # Geometry count changed -> loop_triangles=True is REQUIRED, or Blender
        # segfaults on the next redraw.
        bmesh.update_edit_mesh(ob.data, loop_triangles=destructive, destructive=destructive)
    else:
        bm.to_mesh(ob.data)
        bm.free()
        ob.data.update()


class AC9_OT_CloInset(bpy.types.Operator):
    bl_idname = "ac9_cloth.clo_inset"
    bl_label = "Inset Pieces"
    bl_description = (
        "Step 3. Run after Inset Line. For every pattern piece: absorb the "
        "vertices closer than Width to the outline (collapsed onto the "
        "outline), then inset the outline by Width so a vertex row runs "
        "parallel to it, seams and free edges alike. The parallel-internal-"
        "line trick, done in Blender on the raw CLO export, on the flat "
        "shape key. Object Mode"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _mesh_active(context) and context.active_object.mode == 'OBJECT'

    def execute(self, context):
        ob = context.active_object
        p = _props(context)
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bm.verts.ensure_lookup_table()
        flat = _flat_args(ob, bm)
        if flat is None:
            bm.free()
            self.report({'ERROR'}, f"Inset Pieces {_FLAT_SK_ERROR}")
            return {'CANCELLED'}
        wm = context.window_manager
        wm.progress_begin(0, 100)
        try:
            stats = core.inset_pieces(bm, p.band_width, sharp_inner=True,
                                      progress=lambda f: wm.progress_update(int(f * 100)),
                                      flat=flat)
            bm.to_mesh(ob.data)
            bm.free()
            ob.data.update()
            wm.progress_update(100)
        finally:
            wm.progress_end()
        msg = (f"Inset: {stats['pieces']} pieces, {stats['absorbed']} vertices absorbed, "
               f"{stats['slivers']} needles removed, {stats.get('flipped', 0)} outline slivers merged, "
               f"{stats.get('corners', 0)} corners widened, {stats.get('slits', 0)} slit tips, "
               f"{stats['strip_faces']} strip faces (flat: {flat[0]})")
        _set_status(context, msg)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AC9_OT_CloInsetLine(bpy.types.Operator):
    bl_idname = "ac9_cloth.clo_inset_line"
    bl_label = "Inset Line"
    bl_description = (
        "Step 2. Inset a fold line to both sides: absorb every original "
        "vertex closer than Width onto it, then bevel the line into two rows "
        "parallel to it at Width on each side, with the crease itself kept "
        "as the middle row. Uses the selected vertices when 2 or more are "
        "selected (walked outward with Extend Along Fold); otherwise the "
        "crease edges tagged by Find Folds. Run before Inset Pieces (the "
        "band needs to reach an outline that has not been inset yet). Edit "
        "Mode"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _mesh_active(context) and context.active_object.mode == 'EDIT'

    def execute(self, context):
        ob = context.active_object
        p = _props(context)
        bm = bmesh.from_edit_mesh(ob.data)
        # Layers first: adding a custom data layer reallocates and invalidates
        # element references taken before it (see clo_tag_selected).
        core.prepare_layers(bm)
        flat = _flat_args(ob, bm)
        if flat is None:
            self.report({'ERROR'}, f"Inset Line {_FLAT_SK_ERROR}")
            return {'CANCELLED'}
        sel = [v for v in bm.verts if v.select]
        extend = p.fold_extend
        if len(sel) < 2:
            # No selection: fall back to the tagged crease edges. The tag
            # already defines the whole line, so it is not walked outward.
            tagged = core.tagged_edges(bm)
            sel = list({v for e in tagged for v in e.verts})
            extend = False
        if len(sel) < 2:
            self.report({'WARNING'},
                        "Inset Line: select at least 2 fold-line vertices, or tag a crease first (Find Folds)")
            return {'CANCELLED'}
        wm = context.window_manager
        wm.progress_begin(0, 100)
        try:
            stats = core.inset_line(bm, sel, p.band_width, extend=extend,
                                    min_dihedral_deg=p.fold_min_dihedral,
                                    profile=p.line_profile,
                                    progress=lambda f: wm.progress_update(int(f * 100)),
                                    flat=flat)
            bmesh.update_edit_mesh(ob.data, loop_triangles=True, destructive=True)
            wm.progress_update(100)
        finally:
            wm.progress_end()
        if stats['bevel_faces'] == 0:
            msg = (f"Inset Line: nothing to inset ({stats['line_verts']} line vertices, "
                   f"{stats['trimmed']} trimmed at the outline)")
            _set_status(context, msg)
            self.report({'WARNING'}, msg)
            return {'CANCELLED'}
        msg = (f"Inset Line: {stats['line_verts']} line vertices, {stats['repaired']} edges "
               f"repaired, {stats['absorbed']} absorbed, {stats['slivers']} needles removed, "
               f"{stats.get('lines', 0)} lines, band {stats['width_achieved'] / max(p.band_width, 1e-12) * 100:.0f}% of Width "
               f"(narrowest {stats.get('width_min', 0.0) / max(p.band_width, 1e-12) * 100:.0f}%) (flat: {flat[0]})")
        _set_status(context, msg)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AC9_OT_CloTagByAngle(bpy.types.Operator):
    bl_idname = "ac9_cloth.clo_tag_by_angle"
    bl_label = "Find Folds"
    bl_description = (
        "Step 1. Tag every edge whose two faces meet at Crease Min Angle or "
        "more as a crease (fold lines and, on a mesh that already has "
        "thickness, its rim edges at 90 degrees). With a planar shape key, "
        "measured on the Basis shape regardless of which key is displayed. A "
        "live Solidify modifier is not seen here, and that is fine: its rims "
        "are built from the outline the Inset step has already regularised"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _mesh_active(context)

    def execute(self, context):
        ob = context.active_object
        bm, is_edit = _bm_read(ob)
        flat = _flat_args(ob, bm)
        session = None
        if flat:
            # Layers first: FlatSession.__init__ creates PIECE_LAYER if it is
            # not there yet, which reallocates and invalidates element
            # references taken before it (see clo_tag_selected / clo_inset_line).
            core.prepare_layers(bm)
            session = core.FlatSession(bm, *flat)
            session.enter_3d()
        bm.normal_update()
        n = core.tag_creases_by_angle(bm, _props(context).tag_min_angle)
        if session is not None:
            session.restore()
        _bm_commit(bm, ob, is_edit, destructive=False)
        msg = f"Tag: {n} crease edges by angle"
        _set_status(context, msg)
        self.report({'INFO'} if n else {'WARNING'}, msg)
        return {'FINISHED'}


class AC9_OT_CloTagSelected(bpy.types.Operator):
    bl_idname = "ac9_cloth.clo_tag_selected"
    bl_label = "Tag Selected Edges"
    bl_description = (
        "Mark the selected edges by hand as a crease (Inset Line will pick "
        "them up), or untag them. Edit Mode"
    )
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(
        name="Kind",
        items=(
            ('CREASE', "Crease", "A fold line: Inset Line will pick it up"),
            ('NONE', "Untag", "Remove the tag from the selected edges"),
        ),
        default='CREASE',
    )

    @classmethod
    def poll(cls, context):
        return _mesh_active(context) and context.active_object.mode == 'EDIT'

    def execute(self, context):
        ob = context.active_object
        bm = bmesh.from_edit_mesh(ob.data)
        # Make the layer first: adding a custom data layer reallocates, which
        # invalidates BMEdge references taken before it (the very first tag on
        # a fresh mesh raised ReferenceError otherwise).
        core.kind_layer(bm)
        edges = [e for e in bm.edges if e.select]
        if not edges:
            self.report({'WARNING'}, "Tag: no edges selected")
            return {'CANCELLED'}
        kind = {'CREASE': core.KIND_CREASE, 'NONE': core.KIND_NONE}[self.kind]
        n = core.tag_edges(bm, edges, kind)
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
        msg = f"Tag: {n} edges -> {self.kind.lower()}"
        _set_status(context, msg)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AC9_OT_CloClearTags(bpy.types.Operator):
    bl_idname = "ac9_cloth.clo_clear_tags"
    bl_label = "Clear Tags"
    bl_description = "Remove every crease tag (the ac9_crease_kind edge attribute)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _mesh_active(context)

    def execute(self, context):
        ob = context.active_object
        bm, is_edit = _bm_read(ob)
        core.clear_tags(bm)
        _bm_commit(bm, ob, is_edit, destructive=False)
        _set_status(context, "Tags cleared")
        return {'FINISHED'}


class AC9_OT_CloSelectTagged(bpy.types.Operator):
    bl_idname = "ac9_cloth.clo_select_tagged"
    bl_label = "Select Tagged Edges"
    bl_description = "Select the crease-tagged edges, to see what Inset Line will pick up. Edit Mode"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _mesh_active(context) and context.active_object.mode == 'EDIT'

    def execute(self, context):
        ob = context.active_object
        bm = bmesh.from_edit_mesh(ob.data)
        tags = core.tagged_edges(bm)
        for e in bm.edges:
            e.select = False
        for v in bm.verts:
            v.select = False
        n = 0
        for e, k in tags.items():
            if k == core.KIND_CREASE:
                e.select = True
                n += 1
        bm.select_flush_mode()
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
        self.report({'INFO'}, f"{n} tagged edges selected")
        return {'FINISHED'}
