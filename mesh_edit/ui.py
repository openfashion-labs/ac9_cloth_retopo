"""Mesh Edit panel — general mesh edit helpers that keep UVs.

Currently: Collapse (Keep UV). Not Cloth-specific; parked here for now and
likely to move to a shared mesh-tools addon later.
"""

import bpy

from .. import ui_common as uic


def draw_mesh_edit_tools(layout, context, p):
    """Edit Mode tools that keep UVs. Currently one row; composed into the
    root Faces panel (no panel of its own)."""
    col = layout.column(align=True)
    row = uic.labeled_row(col, "Collapse")
    row.scale_y = 1.2
    row.operator("ac9_cloth.collapse_keep_uv", text="Keep UV",
                 icon='AUTOMERGE_ON')
