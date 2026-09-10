"""Guide Separate sub-property group.

Registered as a nested PointerProperty on AC9ClothRetopoProps.separate.
Which 3D shape the projector reads (Basis or AC9_Separated) is the shared
top-level ``guide_3d_source``; these are the separation's own tuning values.
"""

from bpy.props import BoolProperty, FloatProperty, IntProperty
from bpy.types import PropertyGroup


class AC9GuideSeparateProps(PropertyGroup):

    gap_mm: FloatProperty(
        name="Gap",
        description=(
            "Minimum distance (mm) to open between layers of the Guide that "
            "touch each other. Set it above the cage / ray distance you bake "
            "with; a few millimetres suits garment-scale folds. The Solidify "
            "thickness is added automatically when 'Include Solidify' is on"
        ),
        default=4.0,
        min=0.1,
        soft_max=20.0,
        precision=1,
    )
    smooth_radius_mm: FloatProperty(
        name="Smooth Radius",
        description=(
            "How far (mm) the opening is spread across the surface around "
            "each contact. Wider keeps the drape smoother but moves more of "
            "it; narrower is more local but starts to show as bumps. About "
            "ten times the gap is a good default"
        ),
        default=35.0,
        min=1.0,
        soft_max=100.0,
        precision=0,
    )
    max_iterations: IntProperty(
        name="Max Iterations",
        description=(
            "Upper bound on push-and-smooth rounds. The pass stops early "
            "when every contact has the gap, or when the unresolved count "
            "stops improving (a Solidify thicker than the layer spacing)"
        ),
        default=30,
        min=1,
        soft_max=100,
    )
    include_solidify: BoolProperty(
        name="Include Solidify",
        description=(
            "Read the thickness of the Guide's Solidify modifier and keep "
            "the shell it adds clear of the neighbouring layers too. Turn "
            "off when you bake without Solidify"
        ),
        default=True,
    )
