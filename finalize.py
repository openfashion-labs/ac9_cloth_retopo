"""Finalize — the public "done" button: bake the retopo's 2D + Guide
projection into one plain, self-contained mesh object ready to leave the
add-on behind.

The retopo stays flat and AC9-owned for the rest of the workflow; Finalize
never touches it. It reads the same projection core.py uses everywhere else
(``run_forward_projection``) to get each vertex's 3D position on the Guide,
copies the retopo into a new ``<Retopo>_Final`` object, writes those 3D
positions into it, lays the UV out from the retopo's own flat (u, v) layout
so the two never disagree, then strips every AC9 shape key / attribute /
custom property the copy inherited from the retopo. The result has no
ShapeKeys and no ``ac9_*`` data at all — nothing left for 'Clear All' to find.
"""

import bpy

from . import ui_common as uic
from .clo_projector import core


def _flat_layout_xy(retopo):
    """Return [(x, y), ...] — the retopo's current flat (2D) layout, one pair
    per vertex, read from the Basis ShapeKey when one exists (mesh.vertices
    can otherwise reflect whichever ShapeKey is active) and from the mesh
    vertices directly when it has none.
    """
    mesh = retopo.data
    sk = mesh.shape_keys
    if sk is not None and sk.key_blocks:
        basis = sk.key_blocks[0].data
        return [(c.co.x, c.co.y) for c in basis]
    return [(v.co.x, v.co.y) for v in mesh.vertices]


class AC9_OT_FinalizeRetopo(bpy.types.Operator):
    """Bake the current Retopo + Guide projection into a new, plain mesh
    object ('<Retopo>_Final'): 3D shape from the Guide projection, UV from
    the 2D retopo layout, no ShapeKeys, no AC9 data. The Retopo itself is
    left untouched (still flat, still editable) — run again any time"""

    bl_idname = "ac9_cloth.finalize_retopo"
    bl_label = "Finalize"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            cls.poll_message_set("Exit Edit Mode first (Tab).")
            return False
        top = getattr(getattr(context, "scene", None), "ac9_cloth_retopo", None)
        if top is None:
            return False
        if top.retopo_obj is None or top.retopo_obj.type != 'MESH':
            cls.poll_message_set("Set the Retopo first.")
            return False
        if top.guide_obj is None:
            cls.poll_message_set("Set the Guide first.")
            return False
        if not top.guide_flat_shapekey:
            cls.poll_message_set("Set the Guide's Flat SK first.")
            return False
        return True

    def _close_seams(self, context, top, retopo, positions_local, wm):
        """Pull matched seam pairs onto one 3D point. Returns (msg, pairs).

        Runs its own seam analysis rather than reading the overlay's cache, and
        takes SEWN pairs only. Marked (layered) seams must not come in here:
        those are a pocket outline projected onto the body panel, and the two
        sides are genuinely at different places in 3D — find_marked_seam_pairs
        measures that gap at anywhere from ~0 to over 1 cm depending on drape.
        Pulling them together would press the pocket flat into the body. The
        cache holds whichever mix the user last analysed with, so it cannot be
        trusted here; measured with marked pairs included, one vertex of this
        garment was moved 6.027 mm instead of the 0.428 mm the real seams ask
        for.

        Never fails the Finalize: a seam analysis that cannot run is reported
        in the result line and the projected positions go through untouched.
        The deliverable is still correct, just with the gaps it always had.
        """
        if not top.proj.finalize_close_seams:
            return "", []

        from .uv_seam_guide import seam_close
        from .uv_seam_guide import analysis as seam_analysis
        from .clo_projector import guide as pguide

        guide_obj = top.guide_obj
        flat_sk = top.guide_flat_shapekey
        props = top.seam
        try:
            pairs = seam_analysis.find_seam_pairs_flat(
                guide_obj, flat_sk,
                precision_flat=int(props.precision),
                match_distance=props.match_distance_3d,
            )
            if not pairs:
                return ", no sewn seams found", []

            fs = pguide.flat_scale(guide_obj, flat_sk)
            matched, n_unmatched = seam_close.find_matched_seam_pairs(
                guide_obj, flat_sk, retopo, pairs,
                max_distance=props.max_distance_uv * fs,
                bond_distance=props.bond_distance * fs,
                progress=uic.ProgressThrottle(wm, lo=70, hi=78),
            )
        except Exception as exc:                       # noqa: BLE001
            print(f"[AC9] Finalize: seam close skipped — {exc}")
            return ", seam close skipped (see console)", []

        n_groups, n_moved, max_shift = seam_close.coincide(
            positions_local, matched)
        msg = f", closed {n_groups} seam group(s)"
        if n_moved:
            msg += f" (moved {n_moved} verts, max {max_shift * 1000.0:.3f} mm)"
        if n_unmatched:
            # These are the red ghosts. Nothing to be coincident WITH, so they
            # stay where they are — and stay a hole in the deliverable.
            msg += f", {n_unmatched} unpaired left open"
        return msg, matched

    def execute(self, context):
        top = context.scene.ac9_cloth_retopo
        retopo = top.retopo_obj
        guide_obj = top.guide_obj
        flat_sk = top.guide_flat_shapekey

        # Capture the 2D layout BEFORE projecting — projection only writes the
        # AC9_3D_Project ShapeKey, never the Basis, but reading it first keeps
        # this step independent of what the projection below does.
        layout_xy = _flat_layout_xy(retopo)

        wm = context.window_manager
        with uic.ProgressScope(wm):
            result = core.run_forward_projection(
                context, retopo, guide_obj, flat_sk,
                overwrite_shapekey=True, clear_failed_group=True,
                select_failed=False, incremental=False,
                progress=uic.ProgressThrottle(wm, lo=0, hi=70),
            )
            if not result.success:
                self.report({'ERROR'}, result.error or "Projection failed.")
                return {'CANCELLED'}
            wm.progress_update(70)

            n = len(retopo.data.vertices)
            sk = retopo.data.shape_keys.key_blocks[core.SHAPEKEY_NAME]
            positions_local = [sk.data[i].co.copy() for i in range(n)]

            # Each vertex was projected onto the Guide on its own, so the two
            # sides of a sewn seam land on separate points and the deliverable
            # ships a crack. Pull matched pairs onto one point before the copy
            # is written. See uv_seam_guide.seam_close for the measurements.
            seam_msg, seam_pairs_found = self._close_seams(
                context, top, retopo, positions_local, wm)
            wm.progress_update(78)

            # Keep the retopo displayed flat (2D), same as Refresh Mirror.
            retopo.active_shape_key_index = 0
            retopo.data.shape_keys.key_blocks[core.SHAPEKEY_NAME].value = 0.0

            me = retopo.data.copy()
            final = bpy.data.objects.new(retopo.name + "_Final", me)
            colls = retopo.users_collection or (context.scene.collection,)
            for coll in colls:
                coll.objects.link(final)
            final.matrix_world = retopo.matrix_world.copy()
            # data.copy() names the mesh after the retopo with a numeric
            # suffix, so the deliverable's object and its mesh read as two
            # different things in the Properties editor. Blender appends its
            # own suffix here too if the name is taken, and both stay in step.
            me.name = final.name

            # Strip everything the copy inherited from the retopo: ShapeKeys,
            # then AC9 attribute layers and custom properties.
            final.shape_key_clear()

            co_flat = [0.0] * (n * 3)
            for i, p in enumerate(positions_local):
                co_flat[3 * i], co_flat[3 * i + 1], co_flat[3 * i + 2] = p.x, p.y, p.z
            me.vertices.foreach_set("co", co_flat)
            wm.progress_update(85)

            uv = me.uv_layers.active or me.uv_layers.new(name="UVMap")
            uv_flat = [0.0] * (len(me.loops) * 2)
            for loop in me.loops:
                x, y = layout_xy[loop.vertex_index]
                uv_flat[2 * loop.index], uv_flat[2 * loop.index + 1] = x, y
            uv.data.foreach_set("uv", uv_flat)

            for attr_name in [a.name for a in me.attributes]:
                if attr_name.startswith("ac9_"):
                    try:
                        me.attributes.remove(me.attributes[attr_name])
                    except Exception:
                        pass  # some attributes (active UV/color, etc.) refuse removal

            for key in [k for k in final.keys() if k.startswith("ac9_")]:
                del final[key]

            # The projector records the Guide's triangle count on the retopo's
            # MESH datablock (ac9_guide_ntris), and data.copy() carries
            # mesh-level custom properties over exactly like the attribute
            # layers above — the object-level loop never sees them.
            for key in [k for k in me.keys() if k.startswith("ac9_")]:
                del me[key]

            # Vertex group names live on the mesh, so the copy shows the
            # projector's AC9_Project_Failed marker (and the NK_ name it used
            # before the rename) even on a brand-new object, weightless and
            # meaningless outside the working retopo. Only the add-on's own
            # groups go: anything the user put there is theirs.
            for group in [g for g in final.vertex_groups
                          if g.name.startswith(("AC9_", "NK_"))]:
                final.vertex_groups.remove(group)

            # Material slots ride along in data.copy() the same way attributes
            # do, and the add-on puts working materials on the retopo itself
            # (the Guide Maps ghost that makes it see-through, Island
            # Colours). Every name the add-on persists carries the AC9_ prefix
            # by convention, so that is the test. Without this a deliverable
            # leaves with a half-transparent viewport material on it.
            for i in reversed([i for i, m in enumerate(me.materials)
                               if m is not None and m.name.startswith("AC9_")]):
                me.materials.pop(index=i)

            # Smooth, like the mirror viewer and like the Guide it was
            # projected onto. The copy inherits the retopo's shading, and the
            # retopo is a working mesh nobody shades — measured 0 of 3,508
            # polygons smooth on a finished garment, so the deliverable came
            # out faceted and every normal map baked against it inherited the
            # facets.
            if len(me.polygons):
                me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))

            # Welding is the opt-in half and runs last, on the copy only: the
            # positions are already exactly equal, so the merge cannot reach
            # anything but the pairs it was given.
            n_welded = 0
            if (top.proj.finalize_close_seams and top.proj.finalize_weld_seams
                    and seam_pairs_found):
                from .uv_seam_guide import seam_close
                n_welded = seam_close.weld(me, seam_pairs_found)
                if n_welded:
                    seam_msg += f", welded {n_welded}"

            me.update()

            for obj in context.view_layer.objects:
                obj.select_set(False)
            final.select_set(True)
            context.view_layer.objects.active = final
            wm.progress_update(100)

        # Report the n-gons, do not block on them. Judging whether an n-gon is
        # a defect or a finished flat part is the user's call, not this
        # add-on's — it does retopology, not QA.
        n_ngon, largest_ngon = core.ngon_stats(me)
        ngon_msg = (f", {n_ngon} n-gon(s) (largest {largest_ngon}v)"
                    if n_ngon else "")
        msg = (f"Finalize: {final.name}, {len(me.vertices)} verts{ngon_msg}, "
               f"UV from 2D layout{seam_msg}")
        top.status_guide = msg
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def get_classes():
    return (AC9_OT_FinalizeRetopo,)
