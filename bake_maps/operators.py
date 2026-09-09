"""Operators for the Guide Maps tool.

Both operators: compute a per-vertex Color Attribute on the Guide, then bake
it to a fixed-name image (overwritten every run, so an open Image Editor
showing it refreshes in place).
"""

import bpy

from . import core
from . import preview as _preview


def _top(context):
    return context.scene.ac9_cloth_retopo


class AC9_OT_BakeResidualMap(bpy.types.Operator):
    """Bake the residual map: signed distance from the Guide surface to the
    current retopo (red = Guide in front, blue = behind, white = captured,
    dark gray = not yet covered by the retopo). Written to the image
    'AC9_ResidualMap' — keep it open in an Image Editor and it refreshes on
    every re-bake. Object Mode only"""

    bl_idname = "ac9_cloth.bake_residual_map"
    bl_label = "Bake Residual Map"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        top = _top(context)
        return (
            context.mode == "OBJECT"
            and top.retopo_obj is not None
            and top.guide_obj is not None
        )

    def execute(self, context):
        top = _top(context)
        p = top.maps

        wm = context.window_manager
        wm.progress_begin(0, 100)
        try:
            wm.progress_update(5)
            err, stats = core.compute_residual(
                top.retopo_obj,
                top.guide_obj,
                top.guide_flat_shapekey,
                scale_m=p.residual_scale_mm / 1000.0,
                cover_eps_m=p.cover_eps_mm / 1000.0,
            )
            if err is not None:
                self.report({"ERROR"}, err)
                return {"CANCELLED"}

            # The bake call itself (bpy.ops.object.bake) is atomic — Blender
            # gives no intra-bake progress — so this only brackets it.
            wm.progress_update(15)
            err = core.bake_to_image(
                context,
                top.guide_obj,
                core.RESIDUAL_IMAGE,
                int(p.resolution),
                attr_name=core.RESIDUAL_ATTR,
            )
            wm.progress_update(85)
            if err is not None:
                self.report({"ERROR"}, err)
                return {"CANCELLED"}

            if "rms_mm" in stats:
                msg = (
                    f"RMS {stats['rms_mm']:.1f}mm · p90 {stats['p90_mm']:.1f}mm · "
                    f"max {stats['max_mm']:.1f}mm · "
                    f"covered {stats['covered']:,}/{stats['total']:,} verts"
                )
            else:
                msg = "No Guide vertices covered by the retopo footprint."
            top.status_maps = f"Residual: {msg}"
            if top.maps.preview_map == 'RESIDUAL':
                _preview.sync_preview_image('RESIDUAL')
            wm.progress_update(100)
        finally:
            wm.progress_end()
        self.report({"INFO"}, f"Residual map → image '{core.RESIDUAL_IMAGE}' · {msg}")
        return {"FINISHED"}


class AC9_OT_BakeSagMap(bpy.types.Operator):
    """Bake the sag map: per-panel plane-fit deviation of the Guide 3D shape
    (white = bulges forward, black = sinks back, mid gray = flat). Iso-lines
    show where to flow edge loops for low-frequency sag. Written to the image
    'AC9_SagMap'. Object Mode only"""

    bl_idname = "ac9_cloth.bake_sag_map"
    bl_label = "Bake Sag Map"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        top = _top(context)
        return context.mode == "OBJECT" and top.guide_obj is not None

    def execute(self, context):
        top = _top(context)
        p = top.maps

        wm = context.window_manager
        wm.progress_begin(0, 100)
        try:
            wm.progress_update(5)
            err, stats = core.compute_sag(
                top.guide_obj,
                top.guide_flat_shapekey,
                scale_m=p.sag_scale_mm / 1000.0,
            )
            if err is not None:
                self.report({"ERROR"}, err)
                return {"CANCELLED"}

            # bpy.ops.object.bake is atomic — no intra-bake percent possible.
            wm.progress_update(15)
            err = core.bake_to_image(
                context,
                top.guide_obj,
                core.SAG_IMAGE,
                int(p.resolution),
                attr_name=core.SAG_ATTR,
            )
            wm.progress_update(85)
            if err is not None:
                self.report({"ERROR"}, err)
                return {"CANCELLED"}

            msg = f"{stats['islands']} panels · max deviation {stats['max_mm']:.1f}mm"
            top.status_maps = f"Sag: {msg}"
            if top.maps.preview_map == 'SAG':
                _preview.sync_preview_image('SAG')
            wm.progress_update(100)
        finally:
            wm.progress_end()
        self.report({"INFO"}, f"Sag map → image '{core.SAG_IMAGE}' · {msg}")
        return {"FINISHED"}


class AC9_OT_BakeDrapeMap(bpy.types.Operator):
    """Bake a shaded drape reference onto the Guide's flat layout: Ambient
    Occlusion x Curvature (Dirty Vertex Colors), the same combination as
    manually baking both in Substance/Blender and multiplying them in the
    shader — done here in one click. Written to the image 'AC9_DrapeMap'.
    Use it as the 'atari' (visual reference) while cutting 2D panel
    boundaries with the Knife. Object Mode only"""

    bl_idname = "ac9_cloth.bake_drape_map"
    bl_label = "Bake Drape Map"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        top = _top(context)
        return context.mode == "OBJECT" and top.guide_obj is not None

    def execute(self, context):
        top = _top(context)
        p = top.maps

        wm = context.window_manager
        wm.progress_begin(0, 100)
        try:
            wm.progress_update(5)
            # Two atomic bpy.ops.object.bake calls inside (curvature, then
            # AO) — no intra-bake percent possible, so this only brackets
            # the pair.
            wm.progress_update(15)
            err = core.bake_drape_map(context, top.guide_obj, int(p.resolution))
            wm.progress_update(85)
            if err is not None:
                self.report({"ERROR"}, err)
                return {"CANCELLED"}

            msg = f"AO x Curvature baked to '{core.DRAPE_IMAGE}'."
            top.status_maps = f"Drape: {msg}"
            if top.maps.preview_map == 'DRAPE':
                _preview.sync_preview_image('DRAPE')
            wm.progress_update(100)
        finally:
            wm.progress_end()
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class AC9_OT_BakePreviewPlane(bpy.types.Operator):
    """Create (or reuse) a 1x1m plane — 'AC9_BakePreview' — in Flat SK
    space, shaded with the map chosen by 'Preview' so it can be inspected in
    the viewport without an Image Editor open. Switches every 3D viewport
    from Solid to Solid > Texture shading if it was in Solid, so the map is
    actually visible. Object Mode only"""

    bl_idname = "ac9_cloth.bake_preview_plane"
    bl_label = "Preview Plane"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def execute(self, context):
        top = _top(context)
        map_key = top.maps.preview_map

        obj = _preview.get_or_create_plane(context, map_key)
        shading_changed = _preview.set_material_shading(context)

        for o in context.view_layer.objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        image_name = _preview.image_name_for(map_key)
        img = bpy.data.images.get(image_name) if image_name else None
        shown = img.name if img is not None else "no map baked yet"
        top.status_maps = f"Preview plane: {shown}"
        msg = f"Preview plane ready: {shown}"
        if shading_changed:
            msg += " — viewport: Solid colour = Texture, X-Ray on"
        self.report({"INFO"}, msg)
        return {"FINISHED"}
