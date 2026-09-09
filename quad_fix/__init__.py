"""Quad Fix sub-package.

Repairs non-planar quads by inserting the *right* diagonal edge. A non-planar
quad usually isn't a true twist — it's a simple bend that the mesh's fixed
tessellation diagonal happens to cut across. Re-splitting along the diagonal
with the smaller fold flattens those instantly; genuine saddles are split to
their least-visible orientation and reported separately.

Exposes the PropertyGroup, operator/panel classes, and register hooks.
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
    operators.AC9_OT_FixNonplanarQuads,
    operators.AC9_OT_SelectQuadsByType,
)

_PANEL_CLASSES = (
    ui.AC9_PT_FaceSettings,   # child of the root Faces panel
)


def get_prop_class():
    return properties.AC9QuadFixProps


def get_classes():
    return _OPERATOR_CLASSES + _PANEL_CLASSES


def register_extras():
    """No draw handlers for this tool."""
    pass


def unregister_extras():
    pass
