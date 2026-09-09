"""Force a flat retopo panel's faces to a consistent Z-up winding.

Retopo panels are flat 2D sheets stored in the mesh's real (Basis-space)
coordinates — see retopo_seam_sync.py's module comment on Phase 2 island
replacement. bmesh.ops.recalc_face_normals infers "outward" from
enclosed-volume topology, which has no meaning for an open, zero-volume
sheet: on a flat panel its result is effectively arbitrary (observed —
islands flipping independently of each other after Fill/Grid Regions on the
same panel). A flat sheet has one unambiguous convention instead: the
signed XY area of each face, evaluated in WORLD space, must be positive
(CCW as seen from +Z, i.e. "faces up").
"""


def orient_faces_up(faces, mat, eps=1e-12):
    """Flip any face whose world-space XY winding is CW (negative signed area).

    `mat` MUST be the mesh object's matrix_world — judging winding in local
    space breaks the moment the object carries a rotation or a negative-
    scale axis. Degenerate/near-zero-area faces are left untouched (their
    winding sign is noise, not signal) and counted separately rather than
    flipped.

    Returns (flipped, degenerate) counts.
    """
    flipped = 0
    degenerate = 0
    for f in faces:
        if not f.is_valid:
            continue
        pts = [mat @ v.co for v in f.verts]
        n = len(pts)
        area = 0.0
        for i in range(n):
            x0, y0 = pts[i].x, pts[i].y
            x1, y1 = pts[(i + 1) % n].x, pts[(i + 1) % n].y
            area += x0 * y1 - x1 * y0
        area *= 0.5
        if abs(area) <= eps:
            degenerate += 1
            continue
        if area < 0.0:
            f.normal_flip()
            flipped += 1
    return flipped, degenerate
