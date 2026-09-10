"""CLO Projector sub-property group.

Registered as a nested PointerProperty on AC9ClothRetopoProps.proj.

Guide 2D and Guide 3D are a single object whose ShapeKeys encode both states:
  Basis ShapeKey (value 0)          → Guide 3D (original 3-D garment shape)
  guide_flat_shapekey (value 1)     → Guide 2D (UV-flattened layout)

Island colouring is handled by baking a Color Attribute directly onto the
Guide mesh (AC9_OT_BakeIslandColors).  The GPU overlay is used only for
UV seam lines, which benefit from always-on-top line rendering.
"""

import bpy
from bpy.props import BoolProperty, FloatProperty, StringProperty
from bpy.types import Object, PropertyGroup

_ISLAND_MAT_NAME = "AC9_Island_Colors"


def _overlay_toggle(self, context):
    """Invalidate overlay batches when relevant props change."""
    from .gpu_overlay import invalidate, invalidate_boundary
    invalidate()
    invalidate_boundary()


def _tag_redraw_3d_prop(self, context):
    """Redraw all 3D viewports when a draw-only property changes."""
    from .selection_overlay import invalidate_selection
    invalidate_selection()


def _boundary_update(self, context):
    """Invalidate only the boundary overlay (e.g. cross-size change)."""
    from .gpu_overlay import invalidate_boundary
    invalidate_boundary()


def _update_island_alpha(self, context):
    """Live-update the AC9_Island_Colors material alpha without a full re-bake."""
    mat = bpy.data.materials.get(_ISLAND_MAT_NAME)
    if mat is None or not mat.use_nodes:
        return
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            node.inputs["Alpha"].default_value = self.island_bake_alpha
            return


def _poll_mesh(self, obj) -> bool:
    return obj.type == "MESH"


class AC9CloProjectorProps(PropertyGroup):

    # ── Guide ──────────────────────────────────────────────────────────────
    # Guide object + Flat SK moved to the top-level AC9ClothRetopoProps
    # (top.guide_obj / top.guide_flat_shapekey) since both tools share them.

    # ── Options ────────────────────────────────────────────────────────────
    overwrite_shapekey: BoolProperty(
        name="Overwrite Existing ShapeKey",
        description="Replace 'AC9_3D_Project' if it already exists on the retopo object",
        default=True,
    )
    clear_failed_group: BoolProperty(
        name="Clear Previous Failed Group",
        description="Reset the AC9_Project_Failed vertex group before populating it",
        default=True,
    )
    select_failed: BoolProperty(
        name="Select Failed Vertices",
        description=(
            "Select unprojected vertices in Object Mode. Switch to Edit Mode "
            "afterwards to see them highlighted"
        ),
        default=False,
    )

    auto_bind_new_verts: BoolProperty(
        name="Auto Bind New Verts",
        description=(
            "When leaving Edit Mode in the 3D state, automatically bind any "
            "vertices that were just created (no stored attachment) to the "
            "Guide surface and repair their 2D Basis position. Prevents new "
            "verts from flying away or fusing with the back face. Turn off to "
            "bind manually with 'Bind New 3D Verts'"
        ),
        default=True,
    )

    show_selection_link: BoolProperty(
        name="Selection Link",
        description=(
            "Draw orange markers on the partner object at the vertices "
            "corresponding to the current selection. Select on the 2D retopo to "
            "see where they are on the 3D mirror — or select on the mirror to "
            "find them in the 2D layout. Works with vertex / edge / face / loop "
            "/ shortest-path selections"
        ),
        default=True,
        update=_tag_redraw_3d_prop,
    )
    selection_point_size: FloatProperty(
        name="Marker Size",
        description="Pixel size of the selection-correspondence markers",
        default=9.0,
        min=2.0,
        soft_max=24.0,
        update=_tag_redraw_3d_prop,
    )

    show_experimental: BoolProperty(
        name="Experimental: in-place 3D edit",
        description=(
            "Show the legacy in-place 3D editing tools (Sync 2D>3D / Sync "
            "3D>2D / Bind New Verts). The recommended workflow is to edit the "
            "2D retopo and press 'Refresh Mirror' instead — these tools are "
            "kept for advanced 3D move-only tweaks and may shift vertices"
        ),
        default=False,
    )

    # ── Boundary alignment (pre-process before reverse sync) ───────────────
    show_boundary_verts: BoolProperty(
        name="Show Boundary Verts",
        description=(
            "Draw a green + cross at every retopo vertex flagged as boundary "
            "(ac9_is_boundary = True). The flag is written by Sync 2D>3D / "
            "Sync 3D>2D / Align Boundary to Outline, so this only shows after "
            "you have run one of those at least once"
        ),
        default=False,
        update=_overlay_toggle,
    )
    boundary_cross_size: FloatProperty(
        name="Cross Size",
        description="Arm length of the boundary-vert cross marker (world units)",
        default=0.008,
        min=0.0001,
        soft_max=0.05,
        precision=4,
        unit="LENGTH",
        update=_boundary_update,
    )
    align_boundary_threshold: FloatProperty(
        name="Align Threshold",
        description=(
            "Maximum 2D world-space distance from the Guide's pattern outline for "
            "a retopo vertex to be snapped during 'Align Boundary to Outline'. "
            "FlattenUV-to-ShapeKey normalises UVs into a 0..1 m grid, so a few "
            "millimetres is typical. Too large risks classifying interior verts "
            "as boundary"
        ),
        default=0.003,
        min=0.0,
        soft_max=0.05,
        precision=4,
        step=0.01,
        unit="LENGTH",
    )

    # ── Island Colors ──────────────────────────────────────────────────────
    island_bake_alpha: FloatProperty(
        name="Alpha",
        description=(
            "Transparency of the baked island colours in Material Preview mode. "
            "1.0 = fully opaque · lower values let you see through the Guide to the Retopo. "
            "Drag the slider — the material updates live without re-baking."
        ),
        default=0.7,
        min=0.05,
        max=1.0,
        step=5,
        update=_update_island_alpha,
    )

    # ── Seam Lines Overlay ─────────────────────────────────────────────────
    show_seam_lines: BoolProperty(
        name="Show Outline",
        description=(
            "Draw the Guide's full pattern outline as white lines over the "
            "Guide mesh: every open-boundary edge (the pattern outline — sewn "
            "seams and free edges alike), plus any UV-seam-marked edge. Always "
            "on top, so it stays visible in Solid mode, and follows the Guide's "
            "current ShapeKey blend (2D ↔ 3D). The cyan Seams overlay shows "
            "only the sewn pairs"
        ),
        default=True,
        update=_overlay_toggle,
    )
