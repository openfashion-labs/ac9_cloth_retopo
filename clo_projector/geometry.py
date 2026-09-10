"""Pure geometric primitives. Depends only on mathutils (no bpy).

Kept separate so it can be reused across features and unit-tested without
a Blender context.
"""

from typing import Optional, Tuple


# Operate on anything with .x and .y attributes (mathutils.Vector qualifies).
# Returning tuples keeps the module free of mathutils-specific construction.
BaryCoord = Tuple[float, float, float]


# Below this |signed 2*area| a triangle carries no usable barycentric frame in
# XY. Shared by barycentric_xy and by the Guide 2D BVH filter
# (attachment.build_bvh_2d) so that "the BVH returned it" implies "it can be
# inverted": both must test the SAME quantity via cross_xy, or a triangle can
# pass one test and fail the other at the threshold.
#
# Do not raise this: on a real CLO jacket Guide, 33 retopo vertices had a
# nearest triangle with |cross| < 1e-6 and those are legitimate ~0.7 mm-wide
# triangles. The genuinely degenerate ones measured 2e-13 and below, so 1e-12
# separates them by five orders of magnitude.
DEGENERATE_XY_EPS = 1e-12


def cross_xy(a, b, c) -> float:
    """Signed 2 * XY area of triangle (a, b, c) — barycentric_xy's denominator.

    Exposed so degeneracy can be tested with the exact same expression and
    vertex order the inversion uses, making the two decisions agree bit for
    bit rather than merely to within rounding.
    """
    return (b.x - a.x) * (c.y - a.y) - (c.x - a.x) * (b.y - a.y)


def barycentric_xy(p, a, b, c, eps: float = DEGENERATE_XY_EPS) -> Optional[BaryCoord]:
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

    denom = cross_xy(a, b, c)  # signed 2 * triangle area
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
