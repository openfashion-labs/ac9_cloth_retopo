"""Operators for the Guide Maps tool.

Residual and Sag compute a per-vertex Color Attribute on the Guide and bake
that; Drape bakes two Cycles shaders (AO and Pointiness) straight off the
Guide's 3D shape. Each map lands in an image named after the Guide it came
from ("AC9_SagMap_<Guide>"), overwritten on every re-bake of THAT Guide — so
an open Image Editor refreshes in place, and garments worked on side by side
do not overwrite each other. The delete operators at the bottom back the
Baked Maps list.
"""

import bpy

from . import core
from . import preview as _preview
from ..clo_projector import guide as _pguide


def _top(context):
    return context.scene.ac9_cloth_retopo


def _with_note(msg: str, resolution: int) -> str:
    """Append the slow-bake note to a result line, when it applies."""
    note = core.slow_bake_warning(resolution)
    return f"{msg} · {note}" if note else msg


def _resync_preview(context):
    """Re-point the Preview Plane after the image set changed."""
    top = _top(context)
    _preview.sync_preview_image(top.maps.preview_map, top.guide_obj)


class AC9_OT_BakeResidualMap(bpy.types.Operator):
    """Bake the residual map: signed distance from the Guide surface to the
    current retopo (red = Guide in front, blue = behind, white = captured,
    dark gray = not yet covered by the retopo). Written to the image
    'AC9_ResidualMap_<Guide name>' — keep it open in an Image Editor and it
    refreshes on every re-bake. Object Mode only"""

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
                # Coverage is tested in the flat layout (a Guide vertex against
                # the retopo's 2D footprint), so a fabric millimetre has to be
                # scaled by the UV packing first -- the same setting means 3.02x
                # the fabric distance between a per-item-maximised layout and a
                # whole outfit packed into one square (AUDIT §8-B). residual_
                # scale_mm above is a 3D distance and is left alone.
                cover_eps_m=(p.cover_eps_mm / 1000.0) * _pguide.flat_scale(
                    top.guide_obj, top.guide_flat_shapekey),
            )
            if err is not None:
                self.report({"ERROR"}, err)
                return {"CANCELLED"}

            # The bake call itself (bpy.ops.object.bake) is atomic — Blender
            # gives no intra-bake progress — so this only brackets it.
            wm.progress_update(15)
            core.follow_guide_rename(top.guide_obj)
            img_name = core.image_name('RESIDUAL', top.guide_obj)
            err = core.bake_to_image(
                context,
                top.guide_obj,
                img_name,
                int(p.resolution),
                attr_name=core.RESIDUAL_ATTR,
                keep_in_file=p.keep_residual_in_file,
            )
            wm.progress_update(85)
            if err is not None:
                self.report({"ERROR"}, err)
                return {"CANCELLED"}

            # The map is keyed on the Guide, but it measures a Retopo — note
            # which one, so the Baked Maps list can say "vs <retopo>".
            img = bpy.data.images.get(img_name)
            if img is not None:
                img[core.RETOPO_PROP] = top.retopo_obj.name

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
                _preview.sync_preview_image('RESIDUAL', top.guide_obj)
            wm.progress_update(100)
        finally:
            wm.progress_end()
        self.report({"INFO"}, _with_note(
            f"Residual map → image '{img_name}' · {msg}", int(p.resolution)))
        return {"FINISHED"}


class AC9_OT_BakeSagMap(bpy.types.Operator):
    """Bake the sag map: per-panel plane-fit deviation of the Guide 3D shape
    (white = bulges forward, black = sinks back, mid gray = flat). Iso-lines
    show where to flow edge loops for low-frequency sag. Written to the image
    'AC9_SagMap_<Guide name>'. Object Mode only"""

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
            core.follow_guide_rename(top.guide_obj)
            img_name = core.image_name('SAG', top.guide_obj)
            err = core.bake_to_image(
                context,
                top.guide_obj,
                img_name,
                int(p.resolution),
                attr_name=core.SAG_ATTR,
                keep_in_file=p.keep_sag_in_file,
            )
            wm.progress_update(85)
            if err is not None:
                self.report({"ERROR"}, err)
                return {"CANCELLED"}

            msg = f"{stats['islands']} panels · max deviation {stats['max_mm']:.1f}mm"
            top.status_maps = f"Sag: {msg}"
            if top.maps.preview_map == 'SAG':
                _preview.sync_preview_image('SAG', top.guide_obj)
            wm.progress_update(100)
        finally:
            wm.progress_end()
        self.report({"INFO"}, _with_note(
            f"Sag map → image '{img_name}' · {msg}", int(p.resolution)))
        return {"FINISHED"}


class AC9_OT_BakeDrapeMap(bpy.types.Operator):
    """Bake the drape reference off the Guide's 3D shape onto its flat
    layout: Ambient Occlusion and Curvature (Geometry Pointiness), and their
    product into 'AC9_DrapeMap_<Guide name>' — the same two passes as baking
    them by hand plus the shader multiply, in one click. The
    two passes are deleted again once the product exists (see 'Keep
    Passes'). The product is baked rather than
    left to a node so that Solid > Texture viewport shading can show it.
    Use it as the 'atari' while cutting 2D panel boundaries with the Knife.
    The Guide's Flat SK is blended out for the bake and restored afterwards.
    Object Mode only"""

    bl_idname = "ac9_cloth.bake_drape_map"
    bl_label = "Bake Drape Maps"
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
            # Two atomic bpy.ops.object.bake calls inside (AO, then
            # Curvature) — no intra-bake percent is possible, so this only
            # brackets the pair.
            wm.progress_update(15)
            core.follow_guide_rename(top.guide_obj)
            err = core.bake_drape_maps(
                context, top.guide_obj, int(p.resolution),
                p.ao_distance_mm / 1000.0, p.drape_ao_mix,
                keep_in_file=p.keep_drape_in_file,
                keep_passes=p.keep_drape_passes,
            )
            wm.progress_update(85)
            if err is not None:
                self.report({"ERROR"}, err)
                return {"CANCELLED"}

            passes = ("; the passes are kept as separate images"
                      if p.keep_drape_passes else
                      "; the AO / Curvature passes were dropped")
            msg = (f"AO ({p.ao_distance_mm:.0f} mm) x Curvature "
                   f"(mix {p.drape_ao_mix:.2f}) baked to "
                   f"'{core.image_name('DRAPE', top.guide_obj)}'{passes}.")
            top.status_maps = f"Drape: {msg}"
            if p.preview_map in {'AO', 'CURVATURE', 'DRAPE'}:
                _preview.sync_preview_image(p.preview_map, top.guide_obj)
            wm.progress_update(100)
        finally:
            wm.progress_end()
        self.report({"INFO"}, _with_note(msg, int(p.resolution)))
        return {"FINISHED"}


class AC9_OT_BakePreviewPlane(bpy.types.Operator):
    """Create (or reuse) a 1x1m plane — 'AC9_BakePreview' — in Flat SK
    space, shaded with the map chosen by 'Preview' so it can be inspected in
    the viewport without an Image Editor open. Switches each 3D viewport that
    is in Solid to Solid > Texture, and turns the Retopology overlay on.

    The plane sits 5 mm BELOW z = 0, so anything else lying flat at z = 0 —
    the Guide in its Flat SK pose, the flat retopo, a hand-made bake board —
    covers it when you look down from above. Hide those by hand; this button
    deliberately does not touch other objects' visibility. Object Mode only"""

    bl_idname = "ac9_cloth.bake_preview_plane"
    bl_label = "Preview Plane"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def execute(self, context):
        top = _top(context)
        map_key = top.maps.preview_map

        obj = _preview.get_or_create_plane(context, map_key, top.guide_obj)
        shading_note = _preview.set_material_shading(context)

        for o in context.view_layer.objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        shown = _preview.describe_preview(map_key, top.guide_obj)
        top.status_maps = f"Preview plane: {shown}"
        msg = f"Preview plane ready: {shown}"
        if shading_note:
            msg += f" — viewport: {shading_note}"
        # The plane is below z = 0; the flat Guide and retopo are AT z = 0 and
        # hide it from above. Say so, since X-Ray no longer papers over it.
        msg += ". Hide anything lying flat at z=0 (Guide / retopo) to see it"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Deleting baked maps
#
# A packed 2K map costs ~45 MB in the .blend (measured) and there is one set
# per Guide, so a file with five garments in it needs a way to throw maps
# away that is not "open the Outliner in Blender File mode". Clear All is the
# other end of the scale: it removes every trace of the add-on. These three
# only ever touch images this tool baked (core.is_map_image).
# ---------------------------------------------------------------------------


def _delete_images(images) -> tuple:
    """Remove the given map images. Returns (count, megabytes reclaimed)."""
    mb = sum(core.packed_megabytes(img) for img in images)
    n = 0
    for img in images:
        bpy.data.images.remove(img)
        n += 1
    return n, mb


class AC9_OT_DeleteMapImage(bpy.types.Operator):
    """Delete this baked map image. Re-bake to get it back — nothing else
    reads it, and any Image Texture node pointing at it is cleared"""

    bl_idname = "ac9_cloth.delete_map_image"
    bl_label = "Delete Map"
    bl_options = {"REGISTER", "INTERNAL"}

    image_name: bpy.props.StringProperty(name="Image", default="")

    def execute(self, context):
        img = bpy.data.images.get(self.image_name)
        if img is None:
            self.report({"WARNING"}, f"'{self.image_name}' is already gone.")
            return {"CANCELLED"}
        if not core.is_map_image(img.name):
            self.report({"ERROR"},
                        f"'{img.name}' is not a Guide Maps image — not deleting it.")
            return {"CANCELLED"}
        name = img.name
        _n, mb = _delete_images([img])
        _resync_preview(context)
        self.report({"INFO"}, f"Deleted '{name}' ({mb:.0f} MB).")
        return {"FINISHED"}


class AC9_OT_DeleteGuideMaps(bpy.types.Operator):
    """Delete every baked map belonging to this Guide"""

    bl_idname = "ac9_cloth.delete_guide_maps"
    bl_label = "Delete This Guide's Maps"
    bl_options = {"REGISTER", "INTERNAL"}

    guide_name: bpy.props.StringProperty(name="Guide", default="")

    def execute(self, context):
        doomed = [img for img, _k in core.map_images()
                  if core.map_guide_label(img.name) == self.guide_name]
        if not doomed:
            self.report({"WARNING"}, "No maps for that Guide.")
            return {"CANCELLED"}
        n, mb = _delete_images(doomed)
        _resync_preview(context)
        label = self.guide_name or "unkeyed"
        self.report({"INFO"}, f"Deleted {n} map(s) of '{label}' ({mb:.0f} MB).")
        return {"FINISHED"}


class AC9_OT_DeleteAllMaps(bpy.types.Operator):
    """Delete every baked Guide Map in this file, for all Guides. The maps
    are diagnostics — re-bake the ones you still need"""

    bl_idname = "ac9_cloth.delete_all_maps"
    bl_label = "Delete All Maps"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        doomed = [img for img, _k in core.map_images()]
        if not doomed:
            self.report({"WARNING"}, "No baked maps in this file.")
            return {"CANCELLED"}
        n, mb = _delete_images(doomed)
        _resync_preview(context)
        self.report({"INFO"}, f"Deleted {n} map image(s) ({mb:.0f} MB).")
        return {"FINISHED"}
