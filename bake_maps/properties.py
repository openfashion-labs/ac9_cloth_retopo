"""Guide Maps sub-property group.

Registered as a nested PointerProperty on AC9ClothRetopoProps.maps.
Uses the shared top-level Retopo Mesh / Guide / Flat SK pickers.
"""

from bpy.props import EnumProperty, FloatProperty
from bpy.types import PropertyGroup

from . import preview as _preview


def _update_preview_map(self, context):
    _preview.sync_preview_image(self.preview_map)


class AC9BakeMapsProps(PropertyGroup):

    resolution: EnumProperty(
        name="Resolution",
        description="Size of the baked map images (square)",
        items=(
            ("1024", "1024", "1K — fast preview"),
            ("2048", "2048", "2K — recommended"),
            ("4096", "4096", "4K — slow, for final inspection"),
        ),
        default="2048",
    )

    residual_scale_mm: FloatProperty(
        name="Residual Scale",
        description=(
            "Distance (mm) at which the residual map saturates to full "
            "red/blue. Keep it FIXED across iterations so successive bakes "
            "are directly comparable — 'whiter than last time' then really "
            "means the retopo got closer"
        ),
        default=10.0,
        min=0.1,
        soft_max=50.0,
        precision=1,
    )

    sag_scale_mm: FloatProperty(
        name="Sag Scale",
        description=(
            "Plane-fit deviation (mm) mapped to pure white/black in the sag "
            "map. Mid gray = on the panel's best-fit plane"
        ),
        default=20.0,
        min=0.1,
        soft_max=100.0,
        precision=1,
    )

    cover_eps_mm: FloatProperty(
        name="Coverage Margin",
        description=(
            "How far (mm, in the 2D flat layout) a Guide vertex may sit from "
            "the retopo's 2D footprint and still count as covered. Outside "
            "this the residual map shows neutral dark gray"
        ),
        default=2.0,
        min=0.0,
        soft_max=20.0,
        precision=1,
    )

    preview_map: EnumProperty(
        name="Preview",
        description="Which baked map the Preview Plane displays",
        items=(
            ('RESIDUAL', "Residual", "Show the Residual Map (AC9_ResidualMap)"),
            ('SAG', "Sag", "Show the Sag Map (AC9_SagMap)"),
            ('DRAPE', "Drape", "Show the Drape Map (AC9_DrapeMap)"),
        ),
        default='RESIDUAL',
        update=_update_preview_map,
    )
    # The result line lives on the top-level props (status_maps).
