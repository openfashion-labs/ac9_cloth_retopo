"""Mesh Edit operators.

AC9_OT_CollapseKeepUV   collapse selected edges to their center, keeping UVs

Runs in Edit Mode on the active mesh object.
"""

import bmesh
import bpy

from .. import ui_common as uic
from . import core


class AC9_OT_CollapseKeepUV(bpy.types.Operator):
    bl_idname = "ac9_cloth.collapse_keep_uv"
    bl_label = "Collapse (Keep UV)"
    bl_description = (
        "Collapse the selected edges to their midpoints like Mesh > Merge > "
        "Collapse, but keep the UVs intact (native Collapse pinches/destroys "
        "the UV island). UVs on every layer follow to the midpoint; UV seams "
        "crossing the collapse stay separate"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not uic.experimental_enabled(context):
            return False
        ob = context.active_object
        return ob is not None and ob.type == "MESH" and ob.mode == "EDIT"

    def execute(self, context):
        ob = context.active_object

        bm = bmesh.from_edit_mesh(ob.data)
        n_groups, n_removed = core.collapse_keep_uv(bm)

        if n_groups == 0:
            self.report({"WARNING"}, "Collapse (Keep UV): no edges selected")
            return {"CANCELLED"}

        # Geometry count changed -> loop_triangles=True is REQUIRED, or Blender
        # segfaults on the next redraw (see memory: bmesh_update_edit_mesh_crash).
        bmesh.update_edit_mesh(ob.data, loop_triangles=True, destructive=True)

        stats = f"{n_groups} collapsed, {n_removed} verts removed"
        context.scene.ac9_cloth_retopo.status_faces = stats
        self.report({"INFO"}, f"Collapse (Keep UV): {stats}")
        return {"FINISHED"}
