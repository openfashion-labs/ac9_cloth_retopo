"""Mesh Edit sub-package.

General mesh-editing helpers that preserve UVs. First tool: Collapse (Keep UV),
a drop-in replacement for Mesh > Merge > Collapse that keeps the UV layout.

NOTE: this is NOT Cloth-specific — it's parked in ac9_cloth_retopo for now and
will likely be moved into a shared mesh-tools addon later.

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
    operators.AC9_OT_CollapseKeepUV,
)

# No panel of its own: ui.draw_mesh_edit_tools is composed into the root
# Faces panel.
_PANEL_CLASSES = ()


def get_prop_class():
    return properties.AC9MeshEditProps


def get_classes():
    return _OPERATOR_CLASSES + _PANEL_CLASSES


def register_extras():
    """No draw handlers for this tool."""
    pass


def unregister_extras():
    pass
