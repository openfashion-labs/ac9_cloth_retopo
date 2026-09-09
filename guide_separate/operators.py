"""Operators for Guide Separate.

  ac9_cloth.separate_guide         Check (dry run: colours only) / Separate
  ac9_cloth.clear_guide_separation Remove the ShapeKey and the colours
"""

import bpy
from bpy.props import BoolProperty

from .. import ui_common as uic
from ..clo_projector import core as pcore
from . import core


def _top(context):
    return context.scene.ac9_cloth_retopo


def _progress(context):
    wm = context.window_manager
    wm.progress_begin(0, 100)

    def cb(i, total):
        wm.progress_update(int(100 * (i + 1) / max(total, 1)))

    return wm, cb


class AC9_OT_SeparateGuide(bpy.types.Operator):
    """Open a minimum gap between layers of the Guide that touch each other
    (pleats, wrap folds), so ray-cast bakes stop hitting the neighbouring
    layer. The result is the ShapeKey 'AC9_Separated' on the Guide; the
    Basis keeps the original drape. The projector and Guide Maps then read
    the separated shape while '3D Source' is set to it. Check only measures
    and colours the Guide (AC9_Gap: red = touching, yellow = under the gap,
    green = clear). Object Mode only"""

    bl_idname = "ac9_cloth.separate_guide"
    bl_label = "Separate Self-Contact"
    bl_options = {"REGISTER", "UNDO"}

    apply: BoolProperty(
        name="Apply",
        description="Off: measure and colour only. On: build the AC9_Separated ShapeKey",
        default=True,
        options={"SKIP_SAVE"},
    )

    @classmethod
    def poll(cls, context):
        if not uic.experimental_enabled(context):
            return False
        top = _top(context)
        if context.mode != "OBJECT":
            cls.poll_message_set("Object Mode only.")
            return False
        return top.guide_obj is not None and bool(top.guide_flat_shapekey)

    def execute(self, context):
        top = _top(context)
        p = top.separate
        guide = top.guide_obj
        flat_sk = top.guide_flat_shapekey
        gap_m = p.gap_mm / 1000.0

        if not self.apply:
            err, st = core.check_gap(guide, flat_sk, gap_m, p.include_solidify,
                                     source_sk="Basis")
            if err is not None:
                self.report({"ERROR"}, err)
                return {"CANCELLED"}
            thick = f" · Solidify {st.thickness_mm:.1f}mm" if st.thickness_mm > 0 else ""
            msg = (f"Contact: {st.contact_before:,} verts closer than {p.gap_mm:.1f}mm"
                   f" (min {st.min_gap_mm:.2f}mm){thick}")
            top.status_guide = msg
            self.report({"INFO"}, msg)
            return {"FINISHED"}

        wm, cb = _progress(context)
        try:
            err, st = core.run_separate(
                guide, flat_sk, gap_m,
                p.smooth_radius_mm / 1000.0,
                p.max_iterations,
                p.include_solidify,
                progress=cb,
            )
        finally:
            wm.progress_end()
        if err is not None:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}

        # The separated shape is what the user asked for: read it from now on.
        pcore.invalidate_guide_cache(guide)
        top.guide_3d_source = 'SEPARATED'
        core.sync_separated_value(guide, flat_sk, True)

        thick = f" · Solidify {st.thickness_mm:.1f}mm" if st.thickness_mm > 0 else ""
        if st.converged:
            head = f"Separated: {st.contact_before:,} contacts → gap ≥ {p.gap_mm:.1f}mm"
        else:
            head = (f"Separated: {st.unresolved:,} of {st.contact_before:,} contacts still "
                    f"under {p.gap_mm:.1f}mm (min {st.min_gap_mm:.2f}mm)")
        msg = (f"{head} · moved ≤ {st.max_disp_mm:.1f}mm · normals p99 "
               f"{st.normal_p99_deg:.1f}° · {st.iterations} it{thick}")
        top.status_guide = msg
        level = "INFO" if st.converged else "WARNING"
        if not st.converged and st.thickness_mm > 0:
            msg += " — thickness likely exceeds the layer spacing"
        self.report({level}, msg)
        return {"FINISHED"}


class AC9_OT_ClearGuideSeparation(bpy.types.Operator):
    """Remove the AC9_Separated ShapeKey and the AC9_Gap colours from the
    Guide and read the Basis again"""

    bl_idname = "ac9_cloth.clear_guide_separation"
    bl_label = "Clear Separation"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not uic.experimental_enabled(context):
            return False
        top = _top(context)
        if context.mode != "OBJECT":
            return False
        guide = top.guide_obj
        return guide is not None and (
            core.has_separation(guide)
            or guide.data.color_attributes.get(core.GAP_ATTR) is not None
        )

    def execute(self, context):
        top = _top(context)
        guide = top.guide_obj
        removed_key, removed_attr = core.remove_separation(guide)
        top.guide_3d_source = 'BASIS'
        pcore.invalidate_guide_cache(guide)
        top.status_guide = ""
        parts = []
        if removed_key:
            parts.append(f"ShapeKey '{core.SEPARATED_SK_NAME}'")
        if removed_attr:
            parts.append(f"colours '{core.GAP_ATTR}'")
        self.report({"INFO"}, "Removed " + (", ".join(parts) if parts else "nothing"))
        return {"FINISHED"}
