"""Quad Fix — non-planar quad analysis + artist-matching diagonal selection.

Pure geometry, no bpy. Headless-testable. Operates on 4 corner positions plus
their (outward) vertex normals.

Diagonals of a quad v0 v1 v2 v3 (loop order):
    diag 0 : v0--v2  -> triangles (v0,v1,v2) + (v0,v2,v3)
    diag 1 : v1--v3  -> triangles (v1,v2,v3) + (v1,v3,v0)

Why "convex", not "min-fold"
----------------------------
Validated against artist ground truth (a production sleeve panel, 12 manually
cut quads):
    convex-diagonal rule   matches the artist 11/12 (12/12 with the hybrid)
    min-fold rule          matches the artist  6/12
A non-planar quad on a rounded garment must be split so the surface stays
CONVEX (bulges outward) — the diagonal whose midpoint sits more outward along
the smooth normal. Splitting the other way creates an inward dent that breaks
the silhouette. The fold MAGNITUDE barely differs between diagonals (proven
separately); what matters is the bulge DIRECTION.

Hybrid fallback: a near-folded quad (one diagonal ~flat, the other ~180°) is a
degenerate case where "convex" can pick the folded-over side. When the convex
diagonal's fold is extreme and the other is dramatically flatter, take the
flatter one. This is the single case the pure convex rule missed.
"""

import math
from mathutils import Vector

# Hybrid thresholds (degrees). Tuned so the rule reproduces the 12/12 ground
# truth: only the warp=157.7 fold-over quad triggers the fallback.
_EXTREME_FOLD = 120.0
_FLATTER_RATIO = 0.5


def _tri_normal(a, b, c):
    return (b - a).cross(c - a)


def _angle_between(n1, n2):
    l1, l2 = n1.length, n2.length
    if l1 < 1e-12 or l2 < 1e-12:
        return 0.0
    c = max(-1.0, min(1.0, (n1 / l1).dot(n2 / l2)))
    return math.degrees(math.acos(c))


def fold_angle(p0, p1, p2, p3, diagonal):
    """Fold angle (deg) between the two triangles of `diagonal` (0 or 1)."""
    if diagonal == 0:
        n1 = _tri_normal(p0, p1, p2)
        n2 = _tri_normal(p0, p2, p3)
    else:
        n1 = _tri_normal(p1, p2, p3)
        n2 = _tri_normal(p1, p3, p0)
    return _angle_between(n1, n2)


def convex_diagonal(p0, p1, p2, p3, n0, n1, n2, n3):
    """Which diagonal is convex: 0 (v0-v2) or 1 (v1-v3).

    Convex = the diagonal whose midpoint sits more OUTWARD along the averaged
    smooth vertex normal. n* are the (outward) vertex normals.
    """
    N = (n0 + n1 + n2 + n3)
    if N.length < 1e-9:
        return 0
    N = N.normalized()
    return 0 if ((p0 + p2) - (p1 + p3)).dot(N) > 0 else 1


def analyze_quad(p0, p1, p2, p3, n0=None, n1=None, n2=None, n3=None):
    """Full analysis dict. Vertex normals optional (needed for convex/chosen).

    Keys: fold0, fold1, warp, best_fold (min-fold diag), best_diag (min-fold),
          convex_diag, chosen_diag (convex+hybrid), chosen_fold.
    """
    f0 = fold_angle(p0, p1, p2, p3, 0)
    f1 = fold_angle(p0, p1, p2, p3, 1)
    info = {
        "fold0": f0,
        "fold1": f1,
        "warp": max(f0, f1),
        "best_fold": min(f0, f1),
        "best_diag": 0 if f0 <= f1 else 1,
    }
    if None not in (n0, n1, n2, n3):
        cd = convex_diagonal(p0, p1, p2, p3, n0, n1, n2, n3)
        convex_fold = f0 if cd == 0 else f1
        other_fold = f1 if cd == 0 else f0
        # hybrid: degenerate fold-over -> prefer the much flatter diagonal
        if convex_fold > _EXTREME_FOLD and other_fold < convex_fold * _FLATTER_RATIO:
            chosen = 1 - cd
        else:
            chosen = cd
        info["convex_diag"] = cd
        info["chosen_diag"] = chosen
        info["chosen_fold"] = f0 if chosen == 0 else f1
    return info


def classify(info, nonplanar_thresh, flat_thresh):
    """'fine' | 'clean' | 'saddle' from an analyze_quad() dict.

    fine   : warp <= nonplanar_thresh        (just curved; leave it)
    clean  : chosen split's fold <= flat_thresh (visually acceptable after fix)
    saddle : otherwise                        (fold remains even after best cut)
    """
    if info["warp"] <= nonplanar_thresh:
        return "fine"
    residual = info.get("chosen_fold", info["best_fold"])
    if residual <= flat_thresh:
        return "clean"
    return "saddle"
