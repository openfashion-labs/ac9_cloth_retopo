"""2D Retopo Merge sub-package.

Merges a blue drape-strip mesh into a green grid mesh (both flat, z=0), with
blue flow taking priority and a triangulated seam band (quads + tris, no
n-gons).

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
    operators.AC9_OT_FlatMerge,
    operators.AC9_OT_SelectHoles,
)

_PANEL_CLASSES = (
    ui.AC9_PT_FlatMerge,
    ui.AC9_PT_FlatMergeSettings,
)


def get_prop_class():
    """Return the PropertyGroup class (must be registered before AC9ClothRetopoProps)."""
    return properties.AC9FlatMergeProps


def get_classes():
    """Return operator + panel classes (registered after AC9ClothRetopoProps)."""
    return _OPERATOR_CLASSES + _PANEL_CLASSES


def register_extras():
    """No draw handlers for this tool."""
    pass


def unregister_extras():
    pass
