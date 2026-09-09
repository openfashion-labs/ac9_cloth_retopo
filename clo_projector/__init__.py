"""CLO Retopo Projector sub-package.

Exposes the PropertyGroup, operator/panel classes, and
register_extras/unregister_extras for draw-handler setup.
"""

if "properties" in dir():
    import importlib
    # Always (re-)import before reloading so that modules added after the
    # initial session load don't raise NameError on importlib.reload().
    from . import geometry, attachment, guide, islands, core, mirror, gpu_overlay, selection_overlay, handlers, properties, operators, ui
    for _mod in (geometry, attachment, guide, islands, core, mirror, gpu_overlay, selection_overlay, handlers, properties, operators, ui):
        importlib.reload(_mod)
    del _mod
else:
    from . import geometry, attachment, guide, islands, core, mirror, gpu_overlay, selection_overlay, handlers, properties, operators, ui


_OPERATOR_CLASSES = (
    operators.AC9_OT_CreateProjection,
    operators.AC9_OT_RefreshMirror,
    operators.AC9_OT_RemoveMirror,
    operators.AC9_OT_SyncMirrorTo2D,
    operators.AC9_OT_ReverseProjection,
    operators.AC9_OT_BindNewVerts,
    operators.AC9_OT_AlignBoundaryToSeams,
    operators.AC9_OT_InvalidateGuideCache,
    operators.AC9_OT_ClearAttachments,
    operators.AC9_OT_BakeIslandColors,
    operators.AC9_OT_ClearIslandColors,
    operators.AC9_OT_SubdivideRetopo,
    operators.AC9_OT_ToggleGuide3DView,
    operators.AC9_OT_SwapGuideMirror,
)

_PANEL_CLASSES = (
    ui.AC9_PT_MirrorView,
)


def get_prop_class():
    """Return the PropertyGroup class (must be registered before AC9ClothRetopoProps)."""
    return properties.AC9CloProjectorProps


def get_classes():
    """Return operator + panel classes (registered after AC9ClothRetopoProps)."""
    return _OPERATOR_CLASSES + _PANEL_CLASSES


def register_extras():
    """Register the seam GPU overlay, selection-link overlay + auto-bind handler."""
    gpu_overlay.register_draw_handler()
    selection_overlay.register_draw_handler()
    handlers.register()


def unregister_extras():
    """Unregister the seam GPU overlay, selection-link overlay + auto-bind handler."""
    gpu_overlay.unregister_draw_handler()
    selection_overlay.unregister_draw_handler()
    handlers.unregister()
