"""Quad Fix sub-property group (AC9ClothRetopoProps.quad_fix)."""

from bpy.props import BoolProperty, FloatProperty
from bpy.types import PropertyGroup


class AC9QuadFixProps(PropertyGroup):

    nonplanar_angle: FloatProperty(
        name="Warp Threshold",
        description=(
            "A quad counts as non-planar when its warp (the LARGER of its two "
            "diagonal fold angles) exceeds this. Matches the 'Non-planar Angle' "
            "of a typical mesh checker. Quads below this are left alone"
        ),
        default=25.0,
        min=0.0,
        soft_max=90.0,
        precision=1,
    )

    flat_angle: FloatProperty(
        name="Flat-After-Fix",
        description=(
            "If the BEST diagonal's fold drops below this, the quad is a simple "
            "bend — splitting along that diagonal makes it visually flat "
            "('clean'). Above this it is a true saddle/twist where some fold "
            "remains no matter how you cut"
        ),
        default=5.0,
        min=0.0,
        soft_max=45.0,
        precision=1,
    )

    only_selected: BoolProperty(
        name="Only Selected Faces",
        description=(
            "Operate only on currently selected quads (the workflow: select "
            "non-planar faces in your checker, then fix). When off, scan every "
            "quad in the mesh"
        ),
        default=True,
    )

    fix_saddles: BoolProperty(
        name="Also Split Saddles",
        description=(
            "Saddles can't be made flat by a single cut. When ON they are still "
            "split along their least-bad diagonal (least visible). When OFF only "
            "the cleanly-fixable simple folds are split, and saddles are left "
            "for you to inspect/handle by hand"
        ),
        default=True,
    )
    # The result line lives on the top-level props (status_faces).
