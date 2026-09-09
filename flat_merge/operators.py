"""2D Retopo Merge operators."""

import bpy
import bmesh
from bpy.types import Operator

from .. import ui_common as uic
from . import core


class AC9_OT_FlatMerge(Operator):
    bl_idname = "ac9_cloth.flat_merge"
    bl_label = "Merge Drape into Grid"
    bl_description = (
        "Embed the Drape strips into the Grid: clip the grid cells the drape "
        "crosses (drape wins) and fill the seam, then stitch into one mesh"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not uic.experimental_enabled(context):
            return False
        if context.mode != "OBJECT":
            cls.poll_message_set("Exit Edit Mode first (Tab).")
            return False
        p = context.scene.ac9_cloth_retopo.flat_merge
        if p.grid_obj is None or p.drape_obj is None:
            cls.poll_message_set("Set the Drape and Grid objects first.")
            return False
        return True

    def execute(self, context):
        p = context.scene.ac9_cloth_retopo.flat_merge
        grid, drape = p.grid_obj, p.drape_obj

        if grid == drape:
            self.report({"ERROR"}, "Drape and Grid must be different objects.")
            return {"CANCELLED"}

        try:
            result = core.merge_flat(
                grid, drape,
                carve_margin=p.carve_margin,
                carve_cells=p.carve_cells,
                split_drape=p.split_drape,
                merge_distance=p.merge_distance,
                sliver_factor=p.sliver_factor,
                seam_mode=p.seam_mode,
            )
        except core.ShapelyMissing as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        stats = result["stats"]
        if not result["faces"]:
            self.report({"ERROR"}, "Merge produced no faces.")
            return {"CANCELLED"}

        obj = core.build_object(
            result["verts"], result["faces"],
            face_mats=result["face_mats"],
            materials=result["materials"],
            name=p.result_name or "AC9_2D_Merged",
            merge_distance=p.merge_distance,
            cleanup_dist=result["cleanup_dist"],
            fix_tjunctions=p.fix_tjunctions,
            quad_pair=result["quad_pair"],
            quad_angle=p.quad_angle,
            context=context,
        )

        if p.hide_sources:
            grid.hide_set(True)
            drape.hide_set(True)

        # Make the result the active selection.
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        holes = core.count_holes(obj.data)
        obj["ac9_hole_count"] = holes
        n_tri = n_quad = n_ngon = 0
        for poly in obj.data.polygons:
            if poly.loop_total == 3:
                n_tri += 1
            elif poly.loop_total == 4:
                n_quad += 1
            else:
                n_ngon += 1
        msg = (
            "Merged: grid kept {green_kept}, clipped {green_clipped}, "
            "dropped {green_dropped} (of {green_faces}); drape {blue_faces}; "
            "seam faces {seam_faces}".format(**stats)
            + f"  -  result: {n_quad} quads / {n_tri} tris / {n_ngon} n-gons"
        )
        if holes:
            self.report({"WARNING"}, f"{msg}  -  {holes} hole(s)! Use 'Select Holes'.")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class AC9_OT_SelectHoles(Operator):
    bl_idname = "ac9_cloth.select_holes"
    bl_label = "Select Holes"
    bl_description = (
        "Enter Edit Mode and select interior boundary loops (holes) on the "
        "active mesh. The outer outline is ignored. Nothing selected = no holes"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        if not uic.experimental_enabled(context):
            return False
        o = context.active_object
        return o is not None and o.type == "MESH"

    def execute(self, context):
        obj = context.active_object
        if context.mode != "EDIT_MESH":
            bpy.ops.object.mode_set(mode="EDIT")

        bm = bmesh.from_edit_mesh(obj.data)
        for v in bm.verts:
            v.select = False
        for e in bm.edges:
            e.select = False
        for f in bm.faces:
            f.select = False

        holes = core.iter_hole_loops(bm)
        for vl in holes:
            for v in vl:
                v.select = True
        bm.select_flush(True)
        bmesh.update_edit_mesh(obj.data)
        # Edge mode so the hole loops read clearly.
        context.tool_settings.mesh_select_mode = (False, True, False)

        n = len(holes)
        if n == 0:
            self.report({"INFO"}, "No holes found.")
        else:
            self.report({"WARNING"}, f"{n} hole(s) selected.")
        return {"FINISHED"}
