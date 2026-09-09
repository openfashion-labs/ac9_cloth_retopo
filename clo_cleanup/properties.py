"""CLO Cleanup sub-property group (AC9ClothRetopoProps.clo).

All distances are in the mesh's LOCAL units and shown in scene units
(subtype DISTANCE). The defaults assume a mesh in metres (1 mm = 0.001).
"""

from bpy.props import BoolProperty, FloatProperty, StringProperty
from bpy.types import PropertyGroup


class AC9CloCleanupProps(PropertyGroup):

    fold_extend: BoolProperty(
        name="Extend Along Fold",
        description=(
            "Before repairing, walk from the two ends of the selection along "
            "the fold line (following the crease and its direction, jumping "
            "over missing edges), so two selected vertices are enough to "
            "repair the whole line. Off: only the selected vertices are repaired"
        ),
        default=True,
    )

    fold_min_dihedral: FloatProperty(
        name="Fold Min Angle",
        description=(
            "When extending, an edge counts as part of the fold line only if "
            "the angle between its two faces is at least this (degrees). Lower "
            "it for very soft folds"
        ),
        default=6.0,
        min=0.0,
        soft_max=90.0,
        precision=1,
    )

    tag_min_angle: FloatProperty(
        name="Crease Min Angle",
        description=(
            "Find Folds marks every edge whose two faces meet at this angle "
            "or more (degrees). 60 catches Solidify rims (90) and pressed folds "
            "while leaving drape wrinkles alone"
        ),
        default=60.0,
        min=0.0,
        max=180.0,
        precision=1,
    )

    band_width: FloatProperty(
        name="Width",
        description=(
            "Half-width of the inset (mesh units): the new vertex row is "
            "placed this far in from the outline (Inset Pieces) or on each "
            "side of a fold line (Inset Line), and every original vertex "
            "closer than this is absorbed first. Larger = softer shading "
            "gradient"
        ),
        default=0.001,
        min=0.00001,
        soft_max=0.01,
        precision=4,
        subtype='DISTANCE',
    )

    line_profile: FloatProperty(
        name="Line Profile",
        description=(
            "Inset Line only. Shape of the band across the fold: 1.0 keeps "
            "the crease as it is (the middle row stays on the old edge, the "
            "two sides are flat); 0.5 rounds the fold over the band's width"
        ),
        default=1.0,
        min=0.0,
        max=1.0,
        precision=2,
    )

    status: StringProperty(name="CLO Cleanup Result", default="")
