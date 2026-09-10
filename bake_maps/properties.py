"""Guide Maps sub-property group.

Registered as a nested PointerProperty on AC9ClothRetopoProps.maps.
Uses the shared top-level Retopo Mesh / Guide / Flat SK pickers.
"""

from bpy.props import BoolProperty, EnumProperty, FloatProperty
from bpy.types import PropertyGroup

from . import preview as _preview


def _guide_of(context):
    top = getattr(context.scene, "ac9_cloth_retopo", None)
    return top.guide_obj if top is not None else None


def _update_preview_map(self, context):
    _preview.sync_preview_image(self.preview_map, _guide_of(context))


# Module-level tuples: an items callback that builds fresh strings on every
# call can have them collected while Blender still points at them.
_PREVIEW_ITEMS_FULL = (
    ('RESIDUAL', "Residual", "Show the Residual Map"),
    ('SAG', "Sag", "Show the Sag Map"),
    ('AO', "AO", "Show the drape AO pass"),
    ('CURVATURE', "Curvature", "Show the drape Curvature pass"),
    ('DRAPE', "Drape", "Show the combined Drape map — Curvature x AO, mixed "
                       "at bake time by 'AO Mix'"),
)
_PREVIEW_ITEMS_PRODUCT = tuple(
    it for it in _PREVIEW_ITEMS_FULL if it[0] not in {'AO', 'CURVATURE'}
)


def _preview_items(self, context):
    """The two drape passes are only offered while they are kept — with Keep
    Passes off they are deleted as soon as the product is baked, so an entry
    for them could only ever show an empty plane."""
    return _PREVIEW_ITEMS_FULL if self.keep_drape_passes else _PREVIEW_ITEMS_PRODUCT


def _keep_passes_update(self, context):
    if not self.keep_drape_passes and self.preview_map in {'AO', 'CURVATURE'}:
        self.preview_map = 'DRAPE'   # its own update re-syncs the plane


_KEEP_DESC = (
    "Pack this map's pixels into the .blend so it is still there after "
    "reopening the file. The pack is a 16-bit PNG, not the raw float buffer, "
    "so it costs about 3 MB per 2K map instead of 50 (measured), for a "
    "quantisation of 7.7e-06 on a map that is looked at rather than "
    "measured. Off = session-only: the image is dropped on load and the map "
    "has to be re-baked (the whole drape set re-bakes in under 5 s at 2K on "
    "a 283k-vert Guide)"
)


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
            "How far (mm of fabric) a Guide vertex may sit from the retopo's "
            "2D footprint and still count as covered. Outside this the "
            "residual map shows neutral dark gray. The test itself runs in "
            "the flat layout, whose scale depends on how the UV is packed, so "
            "this is converted before use — the same number means the same "
            "fabric distance whatever the packing"
        ),
        default=2.0,
        min=0.0,
        soft_max=20.0,
        precision=1,
    )

    ao_distance_mm: FloatProperty(
        name="AO Distance",
        description=(
            "How far (mm) the drape AO looks for occluders — the scale of "
            "detail the map reports. Around a fold's own width it draws "
            "folds; far above that it only reports how enclosed a region is, "
            "and any panel sewn flat onto another (pocket, placket, tab) "
            "goes solid black because its neighbour is well inside the "
            "distance. The Guide occludes itself only — the body never "
            "darkens it"
        ),
        default=30.0,
        min=1.0,
        soft_max=1000.0,
        precision=0,
    )

    drape_ao_mix: FloatProperty(
        name="AO Mix",
        description=(
            "How much of the AO goes into the combined Drape map, which is "
            "Curvature x (1 - mix + mix x AO) — the same arithmetic as a Mix "
            "node set to MULTIPLY with this as its Factor. At 1.0 the AO's "
            "dark folds bury the Curvature creases you are actually cutting "
            "along, so it is eased off by default"
        ),
        default=0.7,
        min=0.0,
        max=1.0,
    )

    keep_residual_in_file: BoolProperty(
        name="Keep in file", description=_KEEP_DESC, default=True,
    )
    keep_sag_in_file: BoolProperty(
        name="Keep in file", description=_KEEP_DESC, default=True,
    )
    keep_drape_in_file: BoolProperty(
        name="Keep in file", description=_KEEP_DESC, default=True,
    )

    keep_drape_passes: BoolProperty(
        name="Keep Passes",
        description=(
            "Keep the AO and Curvature passes as images of their own after "
            "the Drape map has been built from them, so each can be looked "
            "at on its own in Solid. They are deleted by default: nothing "
            "re-reads them (pressing Bake always re-bakes both passes), and "
            "the pair costs 128 MiB of RAM per garment at 2K (measured). "
            "They are session-only either way"
        ),
        default=False,
        update=_keep_passes_update,
    )

    # No `default` — Blender does not accept one alongside a dynamic items
    # callback; the first entry (Residual) is the default.
    preview_map: EnumProperty(
        name="Preview",
        description="Which baked map of the current Guide the Preview Plane displays",
        items=_preview_items,
        update=_update_preview_map,
    )
    # The result line lives on the top-level props (status_maps).
