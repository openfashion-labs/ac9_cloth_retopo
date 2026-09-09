"""UV Seam Guide sub-package.

Exposes the PropertyGroup, operator/panel classes, and
register_extras/unregister_extras for draw-handler and keymap setup.
"""

if "properties" in dir():
    import importlib
    from . import (analysis, anchor_segments, connect_rows, ghost, gpu_overlay,
                   panel_regions, patch_grid, preview_fill, properties, operators,
                   retopo_seam_sync, span_density, topology_corner, ui)
    for _mod in (analysis, anchor_segments, connect_rows, ghost, gpu_overlay,
                 panel_regions, patch_grid, preview_fill, properties, operators,
                 retopo_seam_sync, span_density, topology_corner, ui):
        importlib.reload(_mod)
    del _mod
else:
    from . import (analysis, anchor_segments, connect_rows, ghost, gpu_overlay,
                   panel_regions, patch_grid, preview_fill, properties, operators,
                   retopo_seam_sync, span_density, topology_corner, ui)


_OPERATOR_CLASSES = (
    operators.AC9_OT_analyze_uv_seams,
    operators.AC9_OT_refresh_ghosts,
    operators.AC9_OT_inspect_seam_vertex,
    operators.AC9_OT_diagnose_spaces,
    operators.AC9_OT_clear_uv_seam_guides,
    operators.AC9_OT_clear_ghost_points,
    operators.AC9_OT_ghost_force_bond,
    operators.AC9_OT_detect_symmetry,
    operators.AC9_OT_clear_symmetry,
    operators.AC9_OT_detect_mirror_pairs,
    operators.AC9_OT_clear_mirror_pairs,
    operators.AC9_OT_analyze_guide,
    operators.AC9_OT_clear_guide_analysis,
    operators.AC9_OT_analyze_all,
    operators.AC9_OT_clear_analysis,
    operators.AC9_OT_mirror_pair_retopo_map,
    operators.AC9_OT_replace_mirror_island,
    operators.AC9_OT_symmetrize_island,
    operators.AC9_OT_ghost_snap_move,
    operators.AC9_OT_uv_seam_snap_move,
    operators.AC9_OT_SyncRetopoSeams,
    operators.AC9_OT_SyncSelectedVertex,
    operators.AC9_OT_SeamStatusReport,
    operators.AC9_OT_GenerateSeamChain,
    operators.AC9_OT_ResampleSpanDensity,
    operators.AC9_OT_EvenOutSpanDensity,
    operators.AC9_OT_AnalyzeAnchors,
    operators.AC9_OT_PreviewFill,
    operators.AC9_OT_ClearPreviewFill,
    operators.AC9_OT_ConnectLooseEnds,
    operators.AC9_OT_ConnectRows,
    operators.AC9_OT_FillRegions,
    operators.AC9_OT_GridRegions,
    operators.AC9_OT_PatchGrid,
    operators.AC9_OT_ClearPatchGrid,
    operators.AC9_OT_MarkTopologyCorner,
    operators.AC9_OT_ClearTopologyCorner,
    operators.AC9_OT_DetectCornerCandidates,
    operators.AC9_OT_MarkDensityPin,
    operators.AC9_OT_ClearDensityPin,
)

_PANEL_CLASSES = (
    ui.AC9_PT_AnalysisSettings,   # child of the root Setup panel
    ui.AC9_PT_Boundary,
    ui.AC9_PT_BoundarySettings,
)


def get_prop_class():
    """Return the PropertyGroup class (must be registered before AC9ClothRetopoProps)."""
    return properties.AC9SeamGuideProps


def get_classes():
    """Return operator + panel classes (registered after AC9ClothRetopoProps)."""
    return _OPERATOR_CLASSES + _PANEL_CLASSES


def register_extras():
    """Register draw handler, load_post handler, and keymap."""
    gpu_overlay.register_draw_handler()
    gpu_overlay.register_keymap()


def unregister_extras():
    """Unregister draw handler, depsgraph handler, and keymap."""
    gpu_overlay.unregister_keymap()
    gpu_overlay.unregister_draw_handler()
