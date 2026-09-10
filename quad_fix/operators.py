"""Quad Fix operators.

AC9_OT_FixNonplanarQuads     split non-planar quads along their best diagonal
AC9_OT_SelectQuadsByType     select only clean / saddle quads (for inspection)

Both run in Edit Mode on the active mesh object.
"""

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty

from .. import ui_common as uic
from . import core


def _gather_quads(bm, only_selected):
    """Yield (face, info) for every 4-vert face in scope.

    info comes from core.analyze_quad with vertex normals, so it carries the
    convex/hybrid chosen diagonal. Uses local-space co + vertex normals
    (consistent space; the convex test is rotation-invariant)."""
    for f in bm.faces:
        if len(f.verts) != 4:
            continue
        if only_selected and not f.select:
            continue
        vs = f.verts
        info = core.analyze_quad(
            vs[0].co, vs[1].co, vs[2].co, vs[3].co,
            vs[0].normal, vs[1].normal, vs[2].normal, vs[3].normal,
        )
        yield f, info


def _classify_all(bm, props):
    """Return dict face -> (verdict, info). Verdict in fine/clean/saddle."""
    out = {}
    for f, info in _gather_quads(bm, props.only_selected):
        verdict = core.classify(info, props.nonplanar_angle, props.flat_angle)
        out[f] = (verdict, info)
    return out


class AC9_OT_FixNonplanarQuads(bpy.types.Operator):
    bl_idname = "ac9_cloth.fix_nonplanar_quads"
    bl_label = "Fix Non-planar Quads"
    bl_description = (
        "Triangulate each non-planar quad along the CONVEX diagonal (the one "
        "the artist would pick — outward bulge, matches the rounded surface). "
        "Use 'Alternate' + Undo to A/B the other diagonal and see what goes wrong"
    )
    bl_options = {"REGISTER", "UNDO"}

    alternate: BoolProperty(
        name="Alternate Diagonal",
        description=(
            "Split along the OTHER (non-convex) diagonal instead. For previewing "
            "the 'wrong' cut: run Fix, orbit, Undo, run this, orbit, compare"
        ),
        default=False,
    )

    @classmethod
    def poll(cls, context):
        if not uic.experimental_enabled(context):
            return False
        ob = context.active_object
        return ob is not None and ob.type == "MESH" and ob.mode == "EDIT"

    def execute(self, context):
        ob = context.active_object
        props = context.scene.ac9_cloth_retopo.quad_fix

        bm = bmesh.from_edit_mesh(ob.data)
        bm.normal_update()  # convex test needs up-to-date vertex normals

        # When the user hand-picked faces, TRUST the selection: the eye already
        # judged them problematic, so don't re-gate by the warp threshold (a
        # silhouette-bad quad can have a modest warp). Only the whole-mesh scan
        # uses the threshold, to avoid triangulating the entire mesh. A nearly
        # planar quad (warp ~0) is still skipped as a pointless split.
        thr = 0.5 if props.only_selected else props.nonplanar_angle

        n_clean = n_saddle = n_fine = 0
        to_split = []  # (face, va_index, vb_index)
        for f, info in _gather_quads(bm, props.only_selected):
            verdict = core.classify(info, thr, props.flat_angle)
            if verdict == "fine":
                n_fine += 1
                continue
            if verdict == "clean":
                n_clean += 1
            else:
                n_saddle += 1
                # fix_saddles only gates the whole-mesh scan; a hand-selected
                # saddle is always processed (the user picked it on purpose).
                if not props.fix_saddles and not props.only_selected:
                    continue
            d = info["chosen_diag"]
            if self.alternate:
                d = 1 - d
            # d 0 -> connect verts 0 & 2 ; 1 -> verts 1 & 3
            to_split.append((f, d, d + 2))

        n_split = 0
        for f, ia, ib in to_split:
            # f.verts may be re-fetched fresh each time; face_split needs the
            # actual BMVert objects currently on this face.
            if not f.is_valid:
                continue
            vs = f.verts
            try:
                bmesh.utils.face_split(f, vs[ia], vs[ib])
                n_split += 1
            except (ValueError, RuntimeError):
                pass

        # Geometry count changed -> loop_triangles=True is REQUIRED, or Blender
        # segfaults on the next redraw (see memory: bmesh_update_edit_mesh_crash).
        bmesh.update_edit_mesh(ob.data, loop_triangles=True, destructive=True)

        which = "ALT (non-convex)" if self.alternate else "convex"
        stats = (
            f"{n_split} split [{which}]  "
            f"(clean {n_clean}, saddle {n_saddle}"
            f"{'' if props.fix_saddles else ' left'}), "
            f"{n_fine} fine skipped"
        )
        context.scene.ac9_cloth_retopo.status_faces = stats
        self.report({"INFO"}, f"Quad Fix: {stats}")
        return {"FINISHED"}


class AC9_OT_SelectQuadsByType(bpy.types.Operator):
    bl_idname = "ac9_cloth.select_quads_by_type"
    bl_label = "Select Quads by Type"
    bl_description = (
        "Deselect all, then select only the quads of the chosen kind. Use it to "
        "SEE how many of the flagged faces are simple folds vs real saddles "
        "before fixing"
    )
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(
        name="Kind",
        items=(
            ("clean", "Clean (simple fold)", "Splittable to flat"),
            ("saddle", "Saddle (true twist)", "Fold remains after any split"),
            ("nonplanar", "All non-planar", "Both clean and saddle"),
        ),
        default="saddle",
    )

    @classmethod
    def poll(cls, context):
        if not uic.experimental_enabled(context):
            return False
        ob = context.active_object
        return ob is not None and ob.type == "MESH" and ob.mode == "EDIT"

    def execute(self, context):
        ob = context.active_object
        props = context.scene.ac9_cloth_retopo.quad_fix

        bm = bmesh.from_edit_mesh(ob.data)
        bm.normal_update()
        # Classify over the whole mesh regardless of current selection, so this
        # is a real "show me where they are" rather than a subset of selection.
        saved = props.only_selected
        props.only_selected = False
        try:
            verdicts = _classify_all(bm, props)
        finally:
            props.only_selected = saved

        # Clean slate: deselect ALL element types (not just faces) so no stray
        # verts/edges linger. Face select mode keeps the result face-only.
        context.tool_settings.mesh_select_mode = (False, False, True)
        for v in bm.verts:
            v.select_set(False)
        for e in bm.edges:
            e.select_set(False)
        for f in bm.faces:
            f.select_set(False)
        bm.select_history.clear()

        want = {"clean": {"clean"}, "saddle": {"saddle"},
                "nonplanar": {"clean", "saddle"}}[self.kind]
        n = 0
        for f, (verdict, _info) in verdicts.items():
            if verdict in want:
                f.select_set(True)  # flushes DOWN to this face's edges/verts only
                n += 1

        # NOTE: do NOT call bm.select_flush(True) — it flushes UPWARD and would
        # auto-select triangles wedged between selected quads (all their verts
        # end up selected). face.select_set already handles the downward flush.
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
        self.report({"INFO"}, f"Selected {n} {self.kind} quad(s)")
        return {"FINISHED"}
