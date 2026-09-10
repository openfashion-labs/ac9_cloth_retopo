"""Guide Separate sub-package.

Pushes self-touching layers of the Guide apart by a minimum gap and stores
the result as the ShapeKey AC9_Separated, so ray-cast bakes (normal, AO,
curvature — in any baker) stop hitting the neighbouring drape layer. The
projector and Guide Maps read the separated shape while the shared
'3D Source' is set to it; the Basis stays the original drape for export.

Exposes the PropertyGroup, operator/panel classes, and the
register_extras/unregister_extras hooks (no draw handlers needed here).
"""

if "properties" in dir():
    import importlib
    from . import core, properties, operators, ui
    for _mod in (core, properties, operators, ui):
        importlib.reload(_mod)
    del _mod
else:
    from . import core, properties, operators, ui


_OPERATOR_CLASSES = (
    operators.AC9_OT_SeparateGuide,
    operators.AC9_OT_ClearGuideSeparation,
)

_PANEL_CLASSES = (
    ui.AC9_PT_GuideSeparateSettings,
)


def get_prop_class():
    """Return the PropertyGroup class (must be registered before AC9ClothRetopoProps)."""
    return properties.AC9GuideSeparateProps


def get_classes():
    """Return operator + panel classes (registered after AC9ClothRetopoProps
    and after clo_projector's panels — the settings panel is a child of
    AC9_PT_MirrorView)."""
    return _OPERATOR_CLASSES + _PANEL_CLASSES


def register_extras():
    pass


def unregister_extras():
    pass
