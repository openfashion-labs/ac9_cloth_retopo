"""Guide Maps sub-package.

Bakes retopo-guidance maps from the shared Guide object:

  Residual Map — signed distance from the Guide 3D surface to the current
                 retopo (projected 2D→3D via the CLO Projector pipeline).
                 The iteration loop: edit → re-bake → add edges where colored.
  Sag Map      — per-panel plane-fit deviation (low-frequency "tawami") of
                 the Guide itself. Iso-lines = edge-loop flow guides.
  Drape Map    — Ambient Occlusion x Curvature, baked and combined onto the
                 Guide's flat 2D layout in one click: a shaded "atari" for
                 cutting panel boundaries with the Knife.

Exposes the PropertyGroup, operator/panel classes, and the
register_extras/unregister_extras hooks (no draw handlers needed here).
"""

if "properties" in dir():
    import importlib
    from . import core, preview, properties, operators, ui
    for _mod in (core, preview, properties, operators, ui):
        importlib.reload(_mod)
    del _mod
else:
    from . import core, preview, properties, operators, ui


_OPERATOR_CLASSES = (
    operators.AC9_OT_BakeResidualMap,
    operators.AC9_OT_BakeSagMap,
    operators.AC9_OT_BakeDrapeMap,
    operators.AC9_OT_BakePreviewPlane,
)

_PANEL_CLASSES = (
    ui.AC9_PT_BakeMaps,
    ui.AC9_PT_BakeMapsSettings,
)


def get_prop_class():
    """Return the PropertyGroup class (must be registered before AC9ClothRetopoProps)."""
    return properties.AC9BakeMapsProps


def get_classes():
    """Return operator + panel classes (registered after AC9ClothRetopoProps)."""
    return _OPERATOR_CLASSES + _PANEL_CLASSES


def register_extras():
    """No draw handlers for this tool."""
    pass


def unregister_extras():
    pass
