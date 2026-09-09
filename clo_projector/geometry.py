"""Pure geometric primitives. Depends only on mathutils (no bpy).

Kept separate so it can be reused across features and unit-tested without
a Blender context.
"""

from typing import Optional, Tuple


# Operate on anything with .x and .y attributes (mathutils.Vector qualifies).
# Returning tuples keeps the module free of mathutils-specific construction.
BaryCoord = Tuple[float, float, float]


def barycentric_xy(p, a, b, c, eps: float = 1e-12) -> Optional[BaryCoord]:
    """Compute barycentric coordinates (u, v, w) of p with respect to triangle
    (a, b, c) projected onto the XY plane.

    Returns (u, v, w) such that p.xy ≈ u*a.xy + v*b.xy + w*c.xy and u+v+w ≈ 1.
    Returns None if the triangle is degenerate in XY (zero area).
    """
    v0x = b.x - a.x
    v0y = b.y - a.y
    v1x = c.x - a.x
    v1y = c.y - a.y
    v2x = p.x - a.x
    v2y = p.y - a.y

    denom = v0x * v1y - v1x * v0y  # signed 2 * triangle area
    if abs(denom) < eps:
        return None

    v = (v2x * v1y - v1x * v2y) / denom
    w = (v0x * v2y - v2x * v0y) / denom
    u = 1.0 - v - w
    return (u, v, w)


def is_inside_xy(bary: Optional[BaryCoord], eps: float = 1e-6) -> bool:
    """True if barycentric coordinates correspond to a point inside (or on the
    edge of) the triangle.
    """
    if bary is None:
        return False
    u, v, w = bary
    return u >= -eps and v >= -eps and w >= -eps


def triangle_bbox_xy(a, b, c) -> Tuple[float, float, float, float]:
    """Axis-aligned bounding box in XY: (min_x, min_y, max_x, max_y)."""
    return (
        min(a.x, b.x, c.x),
        min(a.y, b.y, c.y),
        max(a.x, b.x, c.x),
        max(a.y, b.y, c.y),
    )
