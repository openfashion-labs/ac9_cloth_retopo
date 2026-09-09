"""2D Retopo Merge sub-property group.

Registered as a nested PointerProperty on AC9ClothRetopoProps.flat_merge.

This tool is SELF-CONTAINED: it does NOT use the shared top-level Retopo Mesh /
Guide / Flat SK pickers (those are for the 3D-projection tools). It only needs
two flat (z=0) meshes living in the same world space — a grid mesh and a drape
strip mesh.
"""

import math

import bpy
from bpy.props import (
    BoolProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty,
)
from bpy.types import Object, PropertyGroup


def _poll_mesh(self, obj) -> bool:
    return obj.type == "MESH"


class AC9FlatMergeProps(PropertyGroup):

    # ── Seam fill mode ───────────────────────────────────────────────────────
    seam_mode: EnumProperty(
        name="Seam Fill",
        description="How to fill a grid cell that the drape strips cross",
        items=[
            ("QUAD", "Quad Fill",
             "Refill the seam band with quad-dominant topology: crossed cells "
             "are removed whole, the band is Delaunay-triangulated from its "
             "boundary verts and the triangles are paired into quads. Closest "
             "to a hand-made seam; a few triangles remain where parity forces "
             "them"),
            ("NGON", "N-gon (pure merge)",
             "Keep each crossed grid cell as a single face (n-gon). Cleanest, "
             "easiest to hand-edit, fewest holes — but introduces n-gons"),
            ("TRI", "Triangulate",
             "Fan-triangulate each crossed cell. Quads + tris only, no n-gons, "
             "but lots of triangles and the odd sliver"),
        ],
        default="QUAD",
    )
    quad_angle: FloatProperty(
        name="Quad Pairing Angle",
        description=(
            "Max shape distortion allowed when pairing two seam triangles into "
            "a quad (Quad Fill mode). Higher pairs more triangles but allows "
            "more skewed quads; lower leaves more triangles in the seam"
        ),
        default=math.pi,    # 180° — fully quad-greedy (relaxation keeps the
                            # paired quads well-shaped). Dial down if a curve
                            # gives skewed quads
        min=0.0,
        max=math.pi,
        subtype="ANGLE",
    )

    # ── Inputs ───────────────────────────────────────────────────────────────
    drape_obj: PointerProperty(
        name="Drape",
        description=(
            "Flat strip mesh following the drape (wrinkle) lines. Kept whole — "
            "the drape flow has priority and is embedded into the Grid"
        ),
        type=Object,
        poll=_poll_mesh,
    )
    grid_obj: PointerProperty(
        name="Grid",
        description=(
            "Flat quad-grid mesh (the clean uniform layout). Cells the Drape "
            "crosses are clipped/filled; the rest is preserved as quads. Both "
            "meshes must be flat (z = 0) and in the same world space"
        ),
        type=Object,
        poll=_poll_mesh,
    )

    # ── Options ──────────────────────────────────────────────────────────────
    carve_cells: FloatProperty(
        name="Give Way",
        description=(
            "Quad Fill: also remove green cells that come within this fraction "
            "of a cell of the drape, so the refilled seam band never gets "
            "squeezed thinner than a workable quad. 0 = only cells the drape "
            "actually touches give way"
        ),
        default=0.35,
        min=0.0,
        soft_max=1.0,
        precision=2,
        step=5,
    )
    split_drape: BoolProperty(
        name="Split Long Drape Edges",
        description=(
            "Quad Fill: loop-cut drape edges longer than ~1.4 grid cells before "
            "merging, so the seam band gets attachment verts at grid density "
            "along the whole drape outline (the drape strips themselves are "
            "subdivided to match the grid). Off = keep the drape mesh untouched"
        ),
        default=True,
    )
    carve_margin: FloatProperty(
        name="Carve Margin",
        description=(
            "Grow the Drape footprint by this distance before clipping the Grid. "
            "Larger removes a wider strip of grid around the drape (cleaner seam, "
            "fewer slivers). 0 = clip the grid exactly at the drape edge"
        ),
        default=0.0,
        min=0.0,
        soft_max=0.05,
        precision=4,
        step=0.01,
        unit="LENGTH",
    )
    sliver_factor: FloatProperty(
        name="Sliver Cleanup",
        description=(
            "Collapse sliver triangles whose short edge is below this fraction "
            "of the average grid cell edge. Removes the thin black wedges along "
            "the seam. 0 = off. Too high will eat real detail"
        ),
        default=0.1,
        min=0.0,
        soft_max=0.5,
        precision=3,
        step=1,
    )
    merge_distance: FloatProperty(
        name="Weld Distance",
        description=(
            "Coincident verts closer than this are welded when stitching the "
            "three pieces (grid / drape / seam band) into one mesh"
        ),
        default=0.0001,
        min=0.0000001,
        soft_max=0.01,
        precision=5,
        step=0.001,
        unit="LENGTH",
    )
    fix_tjunctions: BoolProperty(
        name="Fix T-Junctions",
        description=(
            "Stitch vertices that sit on the middle of a neighbouring face's "
            "edge (the 'fake' verts) by splitting that edge so the vertex is "
            "genuinely shared. Makes the mesh watertight — no overlaps/gaps when "
            "you move verts. Adds a collinear vertex to the neighbour (so a quad "
            "may become a 5-gon)"
        ),
        default=True,
    )
    result_name: StringProperty(
        name="Result Name",
        description="Name for the merged mesh object",
        default="AC9_2D_Merged",
    )
    hide_sources: BoolProperty(
        name="Hide Sources After Merge",
        description="Hide the Drape and Grid source objects once the merge succeeds",
        default=True,
    )
