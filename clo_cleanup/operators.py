"""CLO Cleanup operators. All act on the GUIDE (Setup's picker) — the CLO
export, not the Retopo — and on the mode the GUIDE is in, never on whatever
object happens to be active. See _guide.

Guide Object Mode:  clo_inset, flatten_uv_to_sk (in flatten_uv.py)
Guide Edit Mode:    clo_inset_line, clo_tag_selected (selected edges),
                    clo_select_tagged, uv_mirror_self
Either mode:        clo_tag_by_angle, clo_clear_tags, uv_mirror_make_ref,
                    uv_mirror_pairs
"""

import bmesh
import bpy
from bpy.props import EnumProperty

from . import core, uv_mirror
from .. import ui_common as uic

_FLAT_SK_ERROR = "needs a planar shape key (the UV layout as geometry, e.g. UV_Map_Flattened)"

# Appended to the result line when core.sanitize_nonfinite had to repair
# vertices with no real position (NaN). Worth telling: the mesh WAS broken,
# those few vertices now sit at the average of their neighbours rather than
# where the drape had them, and it is the only trace left of a crash the user
# may have seen before this guard existed.
_NONFINITE_NOTE = (" | %d vertex(es) had no valid position (NaN) and were "
                   "moved onto the average of their neighbours - check that "
                   "area of the mesh")


def _props(context):
    return context.scene.ac9_cloth_retopo.clo


def _set_status(context, text):
    _props(context).status = text


def _guide(context):
    """The Guide object (Setup's picker) when it is a mesh, else None.

    Guide Prep works ON the Guide. It used to work on the active object, and
    on the selection in step 1, which made this one panel behave unlike every
    other panel in the tab — the same button meant "the thing I registered"
    everywhere else and "the thing I just clicked" here (2026-09-09). Step 1
    is what gives the Guide its Flat SK, so the gate here stops at "the Guide
    is a mesh"; the Flat SK is checked by _flat_args, where it is needed.
    """
    top = uic.get_top(context)
    guide = top.guide_obj if top is not None else None
    return guide if guide is not None and guide.type == 'MESH' else None


def _poll_guide(cls, context, mode=None):
    """poll() for the Guide Prep operators: the Guide is set and, when `mode`
    is given, the GUIDE is in that mode (context.mode is not consulted: the
    user may be editing something else entirely)."""
    guide = _guide(context)
    if guide is None:
        cls.poll_message_set("Set the Guide first.")
        return False
    if mode == 'EDIT' and guide.mode != 'EDIT':
        cls.poll_message_set("Put the Guide in Edit Mode.")
        return False
    if mode == 'OBJECT' and guide.mode != 'OBJECT':
        cls.poll_message_set("Leave the Guide's Edit Mode first.")
        return False
    # ... and the CONTEXT has to be in Object Mode too, not just the Guide.
    # These operators write the Guide's mesh through bm.to_mesh, and while
    # anything else is in Edit Mode the undo step being recorded is that
    # object's edit-mesh undo - Ctrl+Z would not bring the Guide back
    # (review finding, 2026-09-09).
    if mode == 'OBJECT' and context.mode != 'OBJECT':
        cls.poll_message_set("Leave Edit Mode first (Tab).")
        return False
    return True


def _guide_checked(op, context, mode=None):
    """The Guide, or None with the reason reported. execute() re-checks what
    poll() checked, because an operator can also be called from a script,
    where poll never ran."""
    guide = _guide(context)
    if guide is None:
        op.report({'ERROR'}, "Set the Guide first.")
        return None
    if mode == 'EDIT' and guide.mode != 'EDIT':
        op.report({'ERROR'}, "The Guide must be in Edit Mode.")
        return None
    if mode == 'OBJECT' and guide.mode != 'OBJECT':
        op.report({'ERROR'}, "The Guide must be in Object Mode.")
        return None
    return guide


def _flat_args(ob, bm):
    """(flat_key, basis_key, active_key) when the mesh carries a planar shape
    key (its UV layout as geometry), else None: the insets then run in 3D.

    Deliberately does NOT check the object's scale, unlike the Guide gate in
    clo_projector.guide.validate_guide. Nothing in clo_cleanup reads
    matrix_world (verified: no reference anywhere in the package) — every
    comparison is local-against-local, so an unapplied object scale cancels
    out, and the one absolute tolerance left is derived from the mesh's own
    edge lengths (core.on_segment_tol). Blocking here would refuse a cleanup
    that is actually correct; the flat-space readers that genuinely break on an
    unapplied scale gate on validate_guide instead.
    """
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
        return _poll_guide(cls, context, 'OBJECT')

    def execute(self, context):
        ob = _guide_checked(self, context, 'OBJECT')
        if ob is None:
            return {'CANCELLED'}
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
        flay = bm.verts.layers.shape.get(flat[0])
        blay = bm.verts.layers.shape.get(flat[1])
        flat_bnd = core.flat_boundary_edge_median(bm, flay)
        # Width is a fabric dimension but the inset runs on the flat shape, so
        # scale it by the UV packing (AUDIT §8-B): the same 1 mm means 3.02x the
        # fabric distance between a per-item-maximised layout and a whole outfit
        # packed into one UV square.
        width = p.band_width * core.flat_scale_bm(bm, flay, blay)
        with uic.ProgressScope(wm) as prog:
            stats = core.inset_pieces(bm, width, sharp_inner=True,
                                      progress=uic.ProgressThrottle(wm),
                                      flat=flat)
            bm.to_mesh(ob.data)
            bm.free()
            ob.data.update()
            prog.update(1.0)
        desync = stats.get('desync', 0)
        zero_len = stats.get('zero_len', 0)
        # How thick the band is against the outline's own vertex spacing. This
        # is REPORTED, not thresholded: the tool's normal operating point is
        # already about half the outline spacing (measured 0.508 on the jacket
        # and 0.452 on the shirt this was developed against), so "width < outline
        # spacing" is not a fault condition — an earlier version warned on that
        # and fired on the reference data itself. There is no measured point
        # where a thin band starts failing, so no line is drawn here; the number
        # is shown and the only thing warned about is an unambiguous no-op.
        band_ratio = (width / flat_bnd) if flat_bnd > 0.0 else 0.0
        no_absorb = stats['absorbed'] == 0
        msg = (f"Inset: {stats['pieces']} pieces, {stats['absorbed']} vertices absorbed, "
               f"{stats['slivers']} needles removed, {stats.get('flipped', 0)} outline slivers merged, "
               f"{stats.get('corners', 0)} corners widened, {stats.get('slits', 0)} slit tips, "
               f"{stats['strip_faces']} strip faces (flat: {flat[0]}) "
               f"| seam resync (whole mesh): +{stats.get('resync_split', 0)} verts, "
               f"{stats.get('resync_zero', 0)} zero-length welded, "
               f"desync left {desync} / zero-length {zero_len}")
        msg += (f" | band {band_ratio:.2f}x the outline spacing "
                f"({width * 1000:.2f}mm flat vs {flat_bnd * 1000:.2f}mm)")
        if no_absorb:
            msg += (" | nothing was absorbed: Width is far below what this "
                    "mesh's outline can express. Raise Width, or export the "
                    "garment at a smaller CLO particle distance")
        nonfinite = stats.get('nonfinite', 0)
        if nonfinite:
            msg += _NONFINITE_NOTE % nonfinite
            # The Setup blocker reads a memoised census keyed on the vertex
            # count, so a repair that did not change the count would leave
            # the blocker up (review finding, 2026-09-09).
            from ..clo_projector import guide as _pguide
            _pguide.invalidate_nonfinite(ob)
        _set_status(context, msg)
        self.report({'WARNING'} if (desync or zero_len or no_absorb or nonfinite)
                    else {'INFO'}, msg)
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
        return _poll_guide(cls, context, 'EDIT')

    def execute(self, context):
        ob = _guide_checked(self, context, 'EDIT')
        if ob is None:
            return {'CANCELLED'}
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
        note = ""
        # A selection covering most of the mesh is not a fold line — it is
        # the all-selected state Edit Mode was entered with (measured
        # 2026-09-05: every face of the export insetted from one click). Treat
        # it as no selection.
        if len(bm.verts) and len(sel) > 0.5 * len(bm.verts):
            note = f" ({len(sel)} vertices were selected — most of the mesh; used the tagged folds instead)"
            sel = []
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
        # Same conversion as Inset Pieces: Width is a fabric dimension, the
        # inset runs on the flat shape, so scale it by the UV packing.
        width = p.band_width * core.flat_scale_bm(
            bm, bm.verts.layers.shape.get(flat[0]),
            bm.verts.layers.shape.get(flat[1]))
        with uic.ProgressScope(wm) as prog:
            stats = core.inset_line(bm, sel, width, extend=extend,
                                    min_dihedral_deg=p.fold_min_dihedral,
                                    profile=p.line_profile,
                                    progress=uic.ProgressThrottle(wm),
                                    flat=flat)
            bmesh.update_edit_mesh(ob.data, loop_triangles=True, destructive=True)
            prog.update(1.0)
        if stats['bevel_faces'] == 0:
            msg = (f"Inset Line: nothing to inset ({stats['line_verts']} line vertices, "
                   f"{stats['trimmed']} trimmed at the outline)")
            _set_status(context, msg)
            self.report({'WARNING'}, msg)
            return {'CANCELLED'}
        desync = stats.get('desync', 0)
        zero_len = stats.get('zero_len', 0)
        msg = (f"Inset Line: {stats['line_verts']} line vertices, {stats['repaired']} edges "
               f"repaired, {stats['absorbed']} absorbed, {stats['slivers']} needles removed, "
               f"{stats.get('lines', 0)} lines, band {stats['width_achieved'] / max(width, 1e-12) * 100:.0f}% of Width "
               f"(narrowest {stats.get('width_min', 0.0) / max(width, 1e-12) * 100:.0f}%) (flat: {flat[0]}){note} "
               f"| seam resync (whole mesh): +{stats.get('resync_split', 0)} verts, "
               f"{stats.get('resync_zero', 0)} zero-length welded, "
               f"desync left {desync} / zero-length {zero_len}")
        nonfinite = stats.get('nonfinite', 0)
        if nonfinite:
            msg += _NONFINITE_NOTE % nonfinite
            # The Setup blocker reads a memoised census keyed on the vertex
            # count, so a repair that did not change the count would leave
            # the blocker up (review finding, 2026-09-09).
            from ..clo_projector import guide as _pguide
            _pguide.invalidate_nonfinite(ob)
        _set_status(context, msg)
        self.report({'WARNING'} if (desync or zero_len or nonfinite) else {'INFO'}, msg)
        return {'FINISHED'}


def _select_only_tagged(bm):
    """Selection := the crease-tagged edges (and their vertices). Returns the
    count. Works on an edit bmesh and on a bmesh.new() copy alike (the flags
    are written back by to_mesh)."""
    tags = core.tagged_edges(bm)
    for f in bm.faces:
        f.select = False
    for e in bm.edges:
        e.select = False
    for v in bm.verts:
        v.select = False
    n = 0
    for e, k in tags.items():
        if k == core.KIND_CREASE:
            e.select = True
            for v in e.verts:
                v.select = True
            n += 1
    bm.select_flush_mode()
    return n


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
        return _poll_guide(cls, context)

    def execute(self, context):
        ob = _guide_checked(self, context)
        if ob is None:
            return {'CANCELLED'}
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
        # Show the result as the selection, replacing whatever was selected:
        # Inset Line reads the selection as "the line", and an all-selected
        # mesh left over from entering Edit Mode was insetted whole once
        # (2026-09-05). Same as the Show button.
        _select_only_tagged(bm)
        _bm_commit(bm, ob, is_edit, destructive=False)
        msg = f"Tag: {n} crease edges by angle (selected)"
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
        return _poll_guide(cls, context, 'EDIT')

    def execute(self, context):
        ob = _guide_checked(self, context, 'EDIT')
        if ob is None:
            return {'CANCELLED'}
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
        return _poll_guide(cls, context)

    def execute(self, context):
        ob = _guide_checked(self, context)
        if ob is None:
            return {'CANCELLED'}
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
        return _poll_guide(cls, context, 'EDIT')

    def execute(self, context):
        ob = _guide_checked(self, context, 'EDIT')
        if ob is None:
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(ob.data)
        n = _select_only_tagged(bm)
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
        self.report({'INFO'}, f"{n} tagged edges selected")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# UV Mirror (Prepare step 5) — see uv_mirror.py
# ---------------------------------------------------------------------------

def _uv_mirror_enabled(cls, context):
    """False (with the reason) when the Experimental switch is off.

    UV Mirror is Experimental since 2026-09-09. Steps 1-4 of Guide Prep are
    each one press; step 5 only makes sense inside a hand sequence carried
    out elsewhere (weld, stitch the dart in the UV editor, unwrap ONE side),
    and it has had little testing. The gate is on the operators, not just on
    the panel, so F3 does not reach them either.
    """
    if not uic.experimental_enabled(context):
        cls.poll_message_set(
            "UV Mirror is Experimental "
            "(Add-on Preferences > Experimental tools).")
        return False
    return True


def _active_uv_name(ob):
    uv = ob.data.uv_layers.active
    return uv.name if uv is not None else ""


class AC9_OT_UvMirrorMakeRef(bpy.types.Operator):
    bl_idname = "ac9_cloth.uv_mirror_make_ref"
    bl_label = "Make Reference UV"
    bl_description = ("Copy the active UV layer into '" + uv_mirror.REF_UV_NAME +
                      "' (the untouched pattern layout). Do this BEFORE stitching darts / "
                      "unwrapping; Mirror Pairs / Mirror Self match through it")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _uv_mirror_enabled(cls, context):
            return False
        if not _poll_guide(cls, context):
            return False
        if _guide(context).data.uv_layers.active is None:
            cls.poll_message_set("The Guide has no active UV layer.")
            return False
        return True

    def execute(self, context):
        ob = _guide_checked(self, context)
        if ob is None:
            return {'CANCELLED'}
        src = _active_uv_name(ob)
        if src == uv_mirror.REF_UV_NAME:
            self.report({'ERROR'}, "The active UV layer IS the reference layer — make the working layer active.")
            return {'CANCELLED'}
        bm, is_edit = _bm_read(ob)
        n = uv_mirror.make_reference_uv(bm, src)
        _bm_commit(bm, ob, is_edit, destructive=False)
        msg = f"Reference UV: '{src}' -> '{uv_mirror.REF_UV_NAME}' ({n} corners)"
        _set_status(context, msg)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AC9_OT_UvMirrorPairs(bpy.types.Operator):
    bl_idname = "ac9_cloth.uv_mirror_pairs"
    bl_label = "Mirror UV to Partner Islands"
    bl_description = ("Copy one island's UV onto its left/right partner as a mirror image, "
                      "interpolated across differing topology (pairs detected on the reference UV). "
                      "Edit Mode: selected vertices mark the CORRECT side (their pairs only); nothing "
                      "selected, or Object Mode: every pair runs and the side that differs from the "
                      "reference wins")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _uv_mirror_enabled(cls, context):
            return False
        if not _poll_guide(cls, context):
            return False
        if uv_mirror.REF_UV_NAME not in _guide(context).data.uv_layers:
            cls.poll_message_set("Run Make Reference UV first.")
            return False
        return True

    def execute(self, context):
        ob = _guide_checked(self, context)
        if ob is None:
            return {'CANCELLED'}
        bm, is_edit = _bm_read(ob)
        wm = context.window_manager
        _progress_ctx = uic.ProgressScope(wm)
        _progress_ctx.__enter__()
        space = None
        try:
            space = uv_mirror.UVSpace(bm, uv_mirror.REF_UV_NAME, _active_uv_name(ob))
            space.build_temp(context)
            # Selection marks the correct side — Edit Mode only, where the user
            # can actually see it. Object Mode: every pair, edited side wins.
            selected_roots = ({space.root_of_tv[space.tv_of_loop(l)]
                               for v in bm.verts if v.select for l in v.link_loops}
                              if is_edit else set())
            st = space.mirror_pairs(progress=uic.ProgressThrottle(wm, lo=0, hi=80),
                                    selected_roots=selected_roots)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        finally:
            if space is not None:
                space.free_temp()
            _progress_ctx.__exit__(None, None, None)
        _bm_commit(bm, ob, is_edit, destructive=False)
        if st["by_selection"]:
            skipped = (f"skipped {st['skipped_unselected']} unselected, "
                       f"{st['skipped_both']} both-selected")
        else:
            skipped = (f"skipped {st['skipped_unchanged']} unedited, "
                       f"{st['skipped_both']} both-edited")
        msg = (f"Mirror Pairs ({'selection' if st['by_selection'] else 'edited side'}): "
               f"{st['mirrored']}/{st['n_pairs']} pairs mirrored, {st['verts_written']} corners written; "
               f"{skipped}, {st['skipped_same_handed']} same-handed"
               + (f"; {st['failed']} lookups failed" if st['failed'] else ""))
        _set_status(context, msg)
        self.report({'INFO' if st['mirrored'] else 'WARNING'}, msg)
        return {'FINISHED'}


class AC9_OT_UvMirrorSelf(bpy.types.Operator):
    bl_idname = "ac9_cloth.uv_mirror_self"
    bl_label = "Mirror UV Across Fold (keep selected side)"
    bl_description = ("One cut-on-fold island: select ONE vertex on the side to keep; the other "
                      "half gets that half's edited UV reflected across the fold line. Edit Mode")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _uv_mirror_enabled(cls, context):
            return False
        if not _poll_guide(cls, context, 'EDIT'):
            return False
        if uv_mirror.REF_UV_NAME not in _guide(context).data.uv_layers:
            cls.poll_message_set("Run Make Reference UV first.")
            return False
        return True

    def execute(self, context):
        ob = _guide_checked(self, context, 'EDIT')
        if ob is None:
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(ob.data)
        sel = [v for v in bm.verts if v.select]
        if len(sel) != 1:
            self.report({'ERROR'}, f"Select exactly one vertex on the side to keep ({len(sel)} selected).")
            return {'CANCELLED'}
        loops = sel[0].link_loops
        if not loops:
            self.report({'ERROR'}, "The selected vertex has no faces.")
            return {'CANCELLED'}
        wm = context.window_manager
        _progress_ctx = uic.ProgressScope(wm)
        _progress_ctx.__enter__()
        space = None
        try:
            space = uv_mirror.UVSpace(bm, uv_mirror.REF_UV_NAME, _active_uv_name(ob))
            space.build_temp(context)
            roots = {space.root_of_tv[space.tv_of_loop(l)] for l in loops}
            if len(roots) != 1:
                raise RuntimeError("The selected vertex sits on a seam between islands — pick an interior vertex.")
            st = space.mirror_self(loops[0], progress=uic.ProgressThrottle(wm, lo=0, hi=80))
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        finally:
            if space is not None:
                space.free_temp()
            _progress_ctx.__exit__(None, None, None)
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
        msg = (f"Mirror Self: {st['verts_written']} corners written, {st['kept']} kept, "
               f"{st['axis']} on the fold" + (f"; {st['failed']} lookups failed" if st['failed'] else ""))
        _set_status(context, msg)
        self.report({'INFO'}, msg)
        return {'FINISHED'}
