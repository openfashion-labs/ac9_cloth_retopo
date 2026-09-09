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
        wm.progress_begin(0, 100)
        try:
            result = core.run_forward_projection(
                context, retopo, guide_obj, flat_sk,
                overwrite_shapekey=True, clear_failed_group=True,
                select_failed=False, incremental=False,
                progress=lambda f: wm.progress_update(int(f * 70)),
            )
            if not result.success:
                self.report({'ERROR'}, result.error or "Projection failed.")
                return {'CANCELLED'}
            wm.progress_update(70)

            n = len(retopo.data.vertices)
            sk = retopo.data.shape_keys.key_blocks[core.SHAPEKEY_NAME]
            positions_local = [sk.data[i].co.copy() for i in range(n)]

            # Keep the retopo displayed flat (2D), same as Refresh Mirror.
            retopo.active_shape_key_index = 0
            retopo.data.shape_keys.key_blocks[core.SHAPEKEY_NAME].value = 0.0

            me = retopo.data.copy()
            final = bpy.data.objects.new(retopo.name + "_Final", me)
            colls = retopo.users_collection or (context.scene.collection,)
            for coll in colls:
                coll.objects.link(final)
            final.matrix_world = retopo.matrix_world.copy()

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

            me.update()

            for obj in context.view_layer.objects:
                obj.select_set(False)
            final.select_set(True)
            context.view_layer.objects.active = final
            wm.progress_update(100)
        finally:
            wm.progress_end()

        msg = f"Finalize: {final.name}, {n} verts, UV from 2D layout"
        top.status_guide = msg
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def get_classes():
    return (AC9_OT_FinalizeRetopo,)
