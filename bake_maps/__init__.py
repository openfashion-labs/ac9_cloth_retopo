"""Guide Maps sub-package.

Bakes retopo-guidance maps from the shared Guide object:

  Residual Map — signed distance from the Guide 3D surface to the current
                 retopo (projected 2D→3D via the CLO Projector pipeline).
                 The iteration loop: edit → re-bake → add edges where colored.
  Sag Map      — per-panel plane-fit deviation (low-frequency "tawami") of
                 the Guide itself. Iso-lines = edge-loop flow guides.
  Drape Maps   — Ambient Occlusion and Curvature, baked off the Guide's 3D
                 shape onto its flat 2D layout in one click, plus their
                 product: a shaded "atari" for cutting panel boundaries with
                 the Knife. The product is baked (not composited in nodes) so
                 that Solid > Texture shading can display it; the mix ratio is
                 a bake setting.

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
    operators.AC9_OT_AddTransparentMaterial,
    operators.AC9_OT_RemoveTransparentMaterial,
    operators.AC9_OT_DeleteMapImage,
    operators.AC9_OT_DeleteGuideMaps,
    operators.AC9_OT_DeleteAllMaps,
)

_PANEL_CLASSES = (
    ui.AC9_PT_BakeMaps,
    ui.AC9_PT_BakedMapsList,
    ui.AC9_PT_BakeMapsSettings,
)


def get_prop_class():
    """Return the PropertyGroup class (must be registered before AC9ClothRetopoProps)."""
    return properties.AC9BakeMapsProps


def get_classes():
    """Return operator + panel classes (registered after AC9ClothRetopoProps)."""
    return _OPERATOR_CLASSES + _PANEL_CLASSES


import bpy  # noqa: E402  (kept below the reload block on purpose)


@bpy.app.handlers.persistent
def _purge_session_maps(_dummy):
    """A map that was baked without "Keep in file" comes back from a reload
    as a black image (measured), and a black map reads as a broken bake — so
    drop those datablocks on load instead. Maps the user saved to disk
    themselves are left alone (see core.purge_session_maps)."""
    try:
        dropped = core.purge_session_maps()
    except Exception as exc:                      # never break file loading
        print(f"[AC9 Cloth Retopo] map purge skipped: {exc}")
        return
    if dropped:
        print(f"[AC9 Cloth Retopo] dropped {len(dropped)} session-only "
              f"map image(s): {', '.join(dropped)}")


def register_extras():
    """No draw handlers — only the load_post purge of session-only maps."""
    if _purge_session_maps not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_purge_session_maps)


def unregister_extras():
    if _purge_session_maps in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_purge_session_maps)
