"""CLO Cleanup sub-package.

Makes a CLO / Marvelous Designer export shade cleanly along its pattern-piece
outlines and internal fold lines before it is used as the bake source for a
retopo. The export is treated as a disposable high-poly: topology is changed
(absorb + inset, diagonal flips, a bevel band along a tagged fold line).

The work is done on a planar shape key (the UV layout laid out as geometry)
when the mesh has one, so every offset is an exact 2D parallel; the result is
mapped back onto the untouched 3D surface (see core.FlatSession). Without
such a shape key the same steps run in 3D directly.

Solidify stays a LIVE modifier the whole way through: the export doubles as
the 2D/3D retopo Guide, and applying it would break that.

Workflow, top to bottom in the panel:
  1 Folds   tag every edge whose dihedral angle is at least Crease Min Angle
            as a fold line (Find Folds), or mark / untag edges by hand
  2 Lines   inset a fold line (the current Edit Mode selection, or the tagged
            edges when nothing is selected) to both sides by Width — this
            must run before step 3, while the outline is still untouched, so
            the line's band reaches all the way to it
  3 Pieces  per pattern piece, absorb the vertices near the outline and inset
            the outline by Width, so a vertex row runs parallel to it — the
            parallel-internal-line trick, done in Blender
  4 UV      (uv_mirror.py) Reference: snapshot the pattern UV. Then, after
            welding + stitching darts + unwrapping ONE side by hand, Pairs
            copies the edit onto every mirror-partner island and Self onto
            the other half of a cut-on-fold island — matched through the
            reference UV (barycentric), so topology need not agree

Exposes the PropertyGroup, operator/panel classes, and register hooks.
"""

if "properties" in dir():
    import importlib
    from . import core, uv_mirror, properties, operators, ui
    for _mod in (core, uv_mirror, properties, operators, ui):
        importlib.reload(_mod)
    del _mod
else:
    from . import core, uv_mirror, properties, operators, ui


_OPERATOR_CLASSES = (
    operators.AC9_OT_CloInset,
    operators.AC9_OT_CloInsetLine,
    operators.AC9_OT_CloTagByAngle,
    operators.AC9_OT_CloTagSelected,
    operators.AC9_OT_CloClearTags,
    operators.AC9_OT_CloSelectTagged,
    operators.AC9_OT_UvMirrorMakeRef,
    operators.AC9_OT_UvMirrorPairs,
    operators.AC9_OT_UvMirrorSelf,
)

_PANEL_CLASSES = (
    ui.AC9_PT_CloCleanup,
    ui.AC9_PT_CloCleanupSettings,   # child of AC9_PT_CloCleanup
)


def get_prop_class():
    return properties.AC9CloCleanupProps


def get_classes():
    return _OPERATOR_CLASSES + _PANEL_CLASSES


def register_extras():
    """No draw handlers for this tool."""
    pass


def unregister_extras():
    pass
