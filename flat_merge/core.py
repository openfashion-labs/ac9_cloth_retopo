"""2D Retopo Merge — core algorithm.

Merges a *blue* strip mesh (drape-following) into a *green* grid mesh, both
assumed flat (z = 0) and living in the SAME world space (top view). Blue flow
wins: green faces that overlap blue are carved away, then the thin seam band
between the surviving green and the blue boundary is triangulated so the result
contains only quads (green body + blue strips) and triangles (seam band) — no
n-gons.

Algorithm (per-quad clipping)
-----------------------------
1. Build 2D polygons (world XY) for every blue face → ``blue_union`` (shapely),
   optionally grown by ``carve_margin``.
2. Add every blue quad whole (blue flow has priority).
3. For each green quad, decide by intersection with ``blue_union``:
     - no intersection  → keep the quad whole (the bulk of the green grid)
     - fully under blue  → drop it
     - blue crosses it   → clip: ``quad − blue_union`` and triangulate the
       leftover (one or two polygons) with ``mathutils.geometry`` (built-in)
   This clips at *sub-quad* resolution, so thin strips (narrower than a green
   cell) still carve correctly instead of slipping through a centroid test.
4. Assemble a bmesh and weld coincident verts (remove_doubles); clipped-quad
   boundaries land on blue/green edges, stitching the pieces into one mesh.

Known limitation: clipping introduces vertices on a crossed quad's edges that
its un-clipped neighbour doesn't share → T-junctions along the seam. Fine for
a flat layout; would need edge-propagation to be fully watertight.

Dependencies
------------
shapely (2D boolean) — NOT bundled with Blender; install into Blender's Python.
mathutils.geometry.tessellate_polygon — built-in (handles holes).
"""

from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Vector
from mathutils.geometry import delaunay_2d_cdt, tessellate_polygon


class ShapelyMissing(RuntimeError):
    """Raised when shapely can't be imported — surfaced to the user in the UI."""


def _require_shapely():
    try:
        import shapely  # noqa: F401
        from shapely.geometry import Polygon, MultiPolygon, Point  # noqa: F401
        from shapely.ops import unary_union  # noqa: F401
        from shapely.prepared import prep  # noqa: F401
        return shapely
    except Exception as exc:  # pragma: no cover - environment dependent
        raise ShapelyMissing(
            "shapely is required for 2D Retopo Merge but could not be imported. "
            "Install it into Blender's Python, e.g.:\n"
            "  <blender>/python/bin/python -m pip install shapely"
        ) from exc


# ── mesh → 2D polygons ──────────────────────────────────────────────────────

def _face_xy_loops(obj):
    """Yield (face_index, [(x, y), ...]) for every face, in WORLD XY.

    Degenerate faces (< 3 unique points / zero area) are skipped.
    """
    mw = obj.matrix_world
    me = obj.data
    for poly in me.polygons:
        loop = []
        for vi in poly.vertices:
            co = mw @ me.vertices[vi].co
            loop.append((co.x, co.y))
        if len(loop) >= 3:
            yield poly.index, loop


def _signed_area(loop):
    """Shoelace signed area of a 2D coord loop (XY)."""
    a = 0.0
    n = len(loop)
    for i in range(n):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return 0.5 * a


def _build_polygon(loop, Polygon):
    """Make a valid shapely Polygon from a coord loop, or None if degenerate."""
    p = Polygon(loop)
    if not p.is_valid:
        p = p.buffer(0)  # fix self-touching / winding
    if p.is_empty or p.area <= 0.0:
        return None
    return p


def _drape_faces_subdivided(drape_obj, max_len):
    """World-space (material_index, xy-loop) per drape face, with edges longer
    than ``max_len`` split in half (repeatedly). A quad's opposite edge is
    always split together with it — a loop cut — so the strip quads subdivide
    into quads that match the grid density instead of growing T-verts."""
    bm = bmesh.new()
    bm.from_mesh(drape_obj.data)
    bm.transform(drape_obj.matrix_world)
    for _ in range(4):                      # halve until under max_len
        target = {e for e in bm.edges if e.calc_length() > max_len}
        if not target:
            break
        changed = True
        while changed:                      # propagate across quad faces
            changed = False
            for f in bm.faces:
                if len(f.edges) != 4:
                    continue
                es = list(f.edges)
                for i in range(2):
                    e, opp = es[i], es[i + 2]
                    if (e in target) != (opp in target):
                        target.add(e)
                        target.add(opp)
                        changed = True
        bmesh.ops.subdivide_edges(bm, edges=list(target), cuts=1)
    out = []
    for f in bm.faces:
        loop = [(v.co.x, v.co.y) for v in f.verts]
        if len(loop) >= 3:
            out.append((f.material_index, loop))
    bm.free()
    return out


# ── main entry ──────────────────────────────────────────────────────────────

def merge_flat(grid_obj, drape_obj, *, carve_margin=0.0, carve_cells=0.35,
               merge_distance=1e-4, sliver_factor=0.1, seam_mode="NGON",
               split_drape=True):
    """Merge the flat blue + green meshes. Returns a result dict:

    verts          list[Vector]  (z = 0)
    faces          list[tuple[int, ...]]  (quads from green/blue, tris from clip)
    face_mats      list[int]     merged-material index per face (aligned to faces)
    materials      list[Material|None]  merged material slots
    cleanup_dist   float         edge-collapse distance for sliver removal
    stats          dict          diagnostics for the operator report

    Materials are kept from the sources: blue faces carry blue's material, green
    faces (kept quads + clip tris) carry green's.
    """
    _require_shapely()
    from shapely.geometry import Polygon, MultiPolygon, Point
    from shapely.ops import unary_union
    from shapely.prepared import prep

    # Green cell loops + average cell size (needed for the relative margin and
    # the drape-edge split length) ---------------------------------------------
    green_loops = list(_face_xy_loops(grid_obj))
    if green_loops:
        tot = 0.0
        for _, loop in green_loops:
            tot += abs(_signed_area(loop))
        avg_area = tot / max(len(green_loops), 1)
    else:
        avg_area = 0.0
    cell_edge = avg_area ** 0.5 if avg_area > 0 else 0.0

    # 1. blue faces + union ----------------------------------------------------
    # QUAD mode loop-cuts drape edges longer than ~1.4 grid cells, so the band
    # gets attachment verts at grid density along the whole blue outline (and
    # the drape strips themselves match the grid's quad size).
    drape_slots = drape_obj.data.materials
    if seam_mode == "QUAD" and split_drape and cell_edge > 0.0:
        drape_faces = _drape_faces_subdivided(drape_obj, 1.4 * cell_edge)
    else:
        drape_faces = [(drape_obj.data.polygons[fidx].material_index, loop)
                       for fidx, loop in _face_xy_loops(drape_obj)]

    def drape_mat(mi):
        if len(drape_slots) == 0:
            return None
        return drape_slots[mi] if 0 <= mi < len(drape_slots) else None

    blue_polys = []
    for _mi, loop in drape_faces:
        p = _build_polygon(loop, Polygon)
        if p is not None:
            blue_polys.append(p)
    if not blue_polys:
        raise ValueError("Blue object has no usable faces.")
    blue_union = unary_union(blue_polys)

    # Grow blue so a thin sliver of green is also removed around it (wider,
    # cleaner seam). The blue quads themselves are still added un-grown. In
    # QUAD mode ``carve_cells`` adds a grid-relative margin: green cells that
    # merely come close to the drape also give way, so the refilled band never
    # gets squeezed thinner than a workable quad.
    margin = carve_margin
    if seam_mode == "QUAD":
        margin += max(carve_cells, 0.0) * cell_edge
    carve_union = (blue_union.buffer(margin, join_style=2)
                   if margin > 0.0 else blue_union)
    if seam_mode != "QUAD":
        # Legacy modes clip against the grown footprint directly. QUAD must
        # subtract the UN-grown union so the band stays stitched to the blue
        # quads (the margin only widens the cell-drop decision).
        blue_union = carve_union
    blue_prep = prep(carve_union)

    # Material slots — merge both sources, dedupe by material datablock --------
    materials: list = []           # merged slots (bpy Material or None)
    _mat_idx = {}                  # material name (or None) -> merged slot index

    def merged_mat(mat):
        key = mat.name if mat is not None else None
        i = _mat_idx.get(key, -1)
        if i < 0:
            i = len(materials)
            materials.append(mat)
            _mat_idx[key] = i
        return i

    def src_mat(obj, fidx):
        slots = obj.data.materials
        if len(slots) == 0:
            return None
        mi = obj.data.polygons[fidx].material_index
        return slots[mi] if 0 <= mi < len(slots) else None

    # Output builders ---------------------------------------------------------
    verts: list[Vector] = []
    faces: list[tuple] = []
    face_mats: list[int] = []      # merged-material index, aligned to faces
    index = {}  # (rounded x, y) -> vert index, pre-weld dedupe within this build
    q = max(merge_distance, 1e-9)

    def vid(x, y):
        key = (round(x / q), round(y / q))
        i = index.get(key)
        if i is None:
            i = len(verts)
            verts.append(Vector((x, y, 0.0)))
            index[key] = i
        return i

    def add_loop(loop, mat_idx):
        ids = []
        for (x, y) in loop:
            vi = vid(x, y)
            if not ids or ids[-1] != vi:
                ids.append(vi)
        if len(ids) > 1 and ids[0] == ids[-1]:
            ids.pop()
        if len(ids) >= 3:
            faces.append(tuple(ids))
            face_mats.append(mat_idx)
            return 1
        return 0

    def add_ngon(part, mat_idx):
        """Add a shapely Polygon as a SINGLE face. Holed polys must still be
        triangulated (an n-gon can't carry a hole)."""
        if part.interiors:
            return emit_triangles(part, 1e-12, mat_idx)
        return add_loop(list(part.exterior.coords)[:-1], mat_idx)

    def emit_triangles(part, area_eps, mat_idx):
        """Triangulate a shapely Polygon (holes included) into the face list."""
        n = 0
        ext = list(part.exterior.coords)[:-1]
        loops = [[Vector((x, y, 0.0)) for (x, y) in ext]]
        for ring in part.interiors:
            loops.append([Vector((x, y, 0.0)) for (x, y) in list(ring.coords)[:-1]])
        flat = [v for L in loops for v in L]
        try:
            tris = tessellate_polygon(loops)
        except Exception:
            return 0
        for a, b, c in tris:
            va, vb, vc = flat[a], flat[b], flat[c]
            # drop near-zero-area (collinear) slivers
            area2 = abs((vb.x - va.x) * (vc.y - va.y) - (vc.x - va.x) * (vb.y - va.y))
            if area2 * 0.5 < area_eps:
                continue
            ia, ib, ic = vid(va.x, va.y), vid(vb.x, vb.y), vid(vc.x, vc.y)
            if ia != ib and ib != ic and ia != ic:
                faces.append((ia, ib, ic))
                face_mats.append(mat_idx)
                n += 1
        return n

    def emit_quad_band(part, steiner, area_eps, mat_idx):
        """Triangulate a band polygon with constrained Delaunay. The boundary
        rings carry the green staircase + blue outline verts; ``steiner`` adds
        the grid lattice points inside the band so the triangles match the grid
        density. CDT spreads triangles evenly — unlike the ear-clip fan — so
        the later tris-to-quads pass can pair most of them into quads."""
        rings = [list(part.exterior.coords)[:-1]]
        rings += [list(r.coords)[:-1] for r in part.interiors]
        vcos, edges_in, faces_in = [], [], []
        for k, ring in enumerate(rings):
            ids = list(range(len(vcos), len(vcos) + len(ring)))
            vcos.extend(Vector((x, y)) for (x, y) in ring)
            edges_in.extend((ids[i], ids[(i + 1) % len(ids)]) for i in range(len(ids)))
            if k == 0:
                faces_in.append(ids)
        # Interior grid points. Points sitting (almost) on the boundary are
        # skipped: ring verts already cover them, and a point exactly on a
        # constraint edge would split it → T-junction with the blue quads.
        ppre = prep(part)
        bnd = part.boundary
        n_ring_in = len(vcos)
        for (x, y, tol) in steiner:
            pt = Point(x, y)
            if ppre.contains(pt) and bnd.distance(pt) > tol:
                vcos.append(Vector((x, y)))
        try:
            out = delaunay_2d_cdt(vcos, edges_in, faces_in, 1, 1e-9)
        except Exception:
            return emit_triangles(part, area_eps, mat_idx)
        overts, ofaces = out[0], out[2]
        # Relax the interior (Steiner) verts with a light Laplacian so the
        # paired quads come out evenly shaped. Ring verts are pinned (they are
        # shared with the kept grid / blue quads); a move is only accepted if
        # it stays inside the band.
        movable = [vi for vi, origs in enumerate(out[3])
                   if origs and all(o >= n_ring_in for o in origs)]
        if movable:
            adj = {}
            for (a, b) in out[1]:
                adj.setdefault(a, []).append(b)
                adj.setdefault(b, []).append(a)
            for _ in range(2):
                for vi in movable:
                    ns = adj.get(vi)
                    if not ns:
                        continue
                    ax = sum(overts[j].x for j in ns) / len(ns)
                    ay = sum(overts[j].y for j in ns) / len(ns)
                    nx = 0.5 * (overts[vi].x + ax)
                    ny = 0.5 * (overts[vi].y + ay)
                    if ppre.contains(Point(nx, ny)):
                        overts[vi] = Vector((nx, ny))
        # output_type=1 keeps triangles inside the exterior face — including any
        # holes, which must be filtered back out by centroid containment.
        check_holes = bool(part.interiors)
        n = 0
        for tri in ofaces:
            if len(tri) != 3:
                continue
            pa, pb, pc = (overts[i] for i in tri)
            area2 = abs((pb.x - pa.x) * (pc.y - pa.y) - (pc.x - pa.x) * (pb.y - pa.y))
            if area2 * 0.5 < area_eps:
                continue
            if check_holes and not ppre.contains(
                    Point((pa.x + pb.x + pc.x) / 3.0, (pa.y + pb.y + pc.y) / 3.0)):
                continue
            ia, ib, ic = vid(pa.x, pa.y), vid(pb.x, pb.y), vid(pc.x, pc.y)
            if ia != ib and ib != ic and ia != ic:
                faces.append((ia, ib, ic))
                face_mats.append(mat_idx)
                n += 1
        return n

    # 2. blue quads — added whole, blue flow has priority ---------------------
    for mi, loop in drape_faces:
        add_loop(loop, merged_mat(drape_mat(mi)))

    # 3. green — clip each quad against blue ----------------------------------
    #    clear of blue  → keep quad whole
    #    fully under    → drop
    #    crossed by blue→ (quad − blue), triangulate the remainder
    n_green = kept = clipped = dropped = n_seam = 0
    # Triangulate mode drops true slivers (cleaned later by dissolve); N-gon and
    # Quad modes keep almost everything so junctions don't leave holes.
    if seam_mode == "TRI":
        min_area = max(avg_area * 0.005, 1e-12)
    else:
        min_area = max(avg_area * 1e-4, 1e-12)

    if seam_mode == "QUAD":
        # Quad Fill: cells the (grown) drape touches are removed WHOLE — the
        # kept grid stays pristine quads. The removed area minus the drape is
        # ONE band region whose boundary holds only blue verts + cell corners
        # (per-cell clipping would put grid-line crossings on the blue edges →
        # T-junctions). The band is CDT-triangulated with the swallowed grid
        # lattice points as interior (Steiner) verts, then paired into quads
        # in build_object.
        drop_cells = []     # (shapely poly, coord loop, merged mat idx)
        for fidx, loop in green_loops:
            n_green += 1
            gmat = merged_mat(src_mat(grid_obj, fidx))
            gp = _build_polygon(loop, Polygon)
            if gp is None:
                continue
            if blue_prep.intersects(gp):
                drop_cells.append((gp, loop, gmat))
            else:
                add_loop(loop, gmat)
                kept += 1
        if drop_cells:
            band = unary_union([c[0] for c in drop_cells]).difference(blue_union)
            band_mat = drop_cells[0][2]
            # Steiner points: the grid lattice corners swallowed by the band,
            # plus each dropped cell's centre. The centres keep the CDT point
            # density at ~cell size inside the band, which suppresses the long
            # sliver triangles that otherwise span from the blue outline to a
            # far staircase corner.
            edge_len = avg_area ** 0.5 if avg_area > 0 else 0.0
            tol_corner = max(edge_len * 0.05, 1e-9)   # lattice points: keep
            tol_centre = max(edge_len * 0.30, 1e-9)   # centres: helpers only
            corners, seen = [], set()
            for _, loop, _m in drop_cells:
                cx = sum(x for x, _ in loop) / len(loop)
                cy = sum(y for _, y in loop) / len(loop)
                pts = [(x, y, tol_corner) for (x, y) in loop]
                pts.append((cx, cy, tol_centre))
                for (x, y, tol) in pts:
                    key = (round(x / q), round(y / q))
                    if key not in seen:
                        seen.add(key)
                        corners.append((x, y, tol))
            if band.is_empty:
                parts = []
            elif isinstance(band, MultiPolygon):
                parts = list(band.geoms)
            else:
                parts = [band]
            for part in parts:
                if not isinstance(part, Polygon) or part.area < min_area:
                    continue
                n_seam += emit_quad_band(part, corners, 1e-12, band_mat)
            clipped = len(drop_cells)
    else:
        for fidx, loop in green_loops:
            n_green += 1
            gmat = merged_mat(src_mat(grid_obj, fidx))
            gp = _build_polygon(loop, Polygon)
            if gp is None:
                continue
            if not blue_prep.intersects(gp):
                add_loop(loop, gmat)    # whole quad, clear of blue
                kept += 1
                continue
            remainder = gp.difference(blue_union)
            if remainder.is_empty or remainder.area < min_area:
                dropped += 1            # essentially fully under blue
                continue
            parts = list(remainder.geoms) if isinstance(remainder, MultiPolygon) else [remainder]
            wrote = 0
            for part in parts:
                if not isinstance(part, Polygon) or part.area < min_area:
                    continue
                if seam_mode == "TRI":
                    wrote += emit_triangles(part, min_area, gmat)
                else:
                    wrote += add_ngon(part, gmat)
            if wrote:
                clipped += 1
                n_seam += wrote
            else:
                dropped += 1

    # Sliver cleanup (edge collapse) only makes sense for the TRI seams. N-gon
    # mode: it would chew the seam faces and can open holes. QUAD mode: CDT +
    # Steiner filtering already avoids slivers, and dissolve_degenerate can
    # merge faces into n-gons — defeating the point of the mode.
    edge = avg_area ** 0.5 if avg_area > 0 else 0.0
    cleanup_dist = max(sliver_factor, 0.0) * edge if seam_mode == "TRI" else 0.0

    stats = {
        "green_faces": n_green,
        "green_kept": kept,
        "green_clipped": clipped,
        "green_dropped": dropped,
        "blue_faces": len(blue_polys),
        "seam_faces": n_seam,
    }
    return {
        "verts": verts,
        "faces": faces,
        "face_mats": face_mats,
        "materials": materials,
        "cleanup_dist": cleanup_dist,
        "quad_pair": seam_mode == "QUAD",
        "stats": stats,
    }


def weld_t_junctions(bm, tol, max_passes=8):
    """Split edges that have a stray vertex sitting on them (T-junctions) so the
    vertex becomes shared, then weld. Returns the number of splits performed.

    A T-junction here = a vertex lying on the interior of another edge's segment
    (within ``tol`` perpendicular) that the edge doesn't already reference. It
    arises because clipping inserts blue-crossing points on a green edge that the
    neighbouring un-clipped quad doesn't have.
    """
    from mathutils.kdtree import KDTree

    total = 0
    for _ in range(max_passes):
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        n = len(bm.verts)
        if n == 0:
            break
        kd = KDTree(n)
        for i, v in enumerate(bm.verts):
            kd.insert(v.co, i)
        kd.balance()

        # collect at most one split per edge this pass (avoids touching the same
        # edge twice before it is re-evaluated); extra coincident verts on one
        # edge are picked up in later passes.
        jobs = []  # (edge, vert_on_edge, factor_from_v0)
        used_edges = set()
        for e in bm.edges:
            v0, v1 = e.verts
            a, b = v0.co, v1.co
            ab = b - a
            L2 = ab.length_squared
            if L2 <= 0.0:
                continue
            L = L2 ** 0.5
            mid = (a + b) * 0.5
            best = None  # (param, vert)
            for (co, idx, _d) in kd.find_range(mid, L * 0.5 + tol):
                v = bm.verts[idx]
                if v is v0 or v is v1:
                    continue
                t = (co - a).dot(ab) / L2
                if t <= tol / L or t >= 1.0 - tol / L:
                    continue  # at / past an endpoint — not a T
                if ((a + ab * t) - co).length > tol:
                    continue  # not on the segment line
                if best is None or t < best[0]:
                    best = (t, v)
            if best is not None and e not in used_edges:
                jobs.append((e, best[1], best[0]))
                used_edges.add(e)

        if not jobs:
            break
        for e, vtarget, fac in jobs:
            try:
                _new_e, new_v = bmesh.utils.edge_split(e, e.verts[0], fac)
            except (ValueError, RuntimeError):
                continue
            new_v.co = vtarget.co  # land exactly on the stray vert, weld merges
            total += 1
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=max(tol, 1e-9))
    return total


def _all_boundary_loops(bm):
    """Return boundary edge loops as ordered vertex lists. A boundary edge has
    exactly one linked face (the outer outline + any interior holes)."""
    from collections import defaultdict

    bedges = [e for e in bm.edges if len(e.link_faces) == 1]
    vmap = defaultdict(list)
    for e in bedges:
        vmap[e.verts[0]].append(e)
        vmap[e.verts[1]].append(e)

    visited = set()
    loops = []
    for e0 in bedges:
        if e0 in visited:
            continue
        loop_v = []
        e, v = e0, e0.verts[0]
        while e is not None and e not in visited:
            visited.add(e)
            loop_v.append(v)
            v = e.other_vert(v)
            nxt = None
            for ne in vmap[v]:
                if ne not in visited and ne is not e:
                    nxt = ne
                    break
            e = nxt
        if len(loop_v) >= 3:
            loops.append(loop_v)
    return loops


def iter_hole_loops(bm):
    """Boundary loops that are interior HOLES (everything except the single
    largest loop, taken to be the outer outline). Each is an ordered vert list.
    """
    loops = _all_boundary_loops(bm)
    if len(loops) <= 1:
        return []

    def area(vl):
        a = 0.0
        n = len(vl)
        for i in range(n):
            x1, y1 = vl[i].co.x, vl[i].co.y
            x2, y2 = vl[(i + 1) % n].co.x, vl[(i + 1) % n].co.y
            a += x1 * y2 - x2 * y1
        return abs(a) * 0.5

    outer = max(range(len(loops)), key=lambda i: area(loops[i]))
    return [vl for i, vl in enumerate(loops) if i != outer]


def count_holes(me):
    """Count interior hole loops in a mesh datablock (object space)."""
    bm = bmesh.new()
    bm.from_mesh(me)
    n = len(iter_hole_loops(bm))
    bm.free()
    return n


def build_object(verts, faces, *, face_mats=None, materials=None,
                 name="AC9_2D_Merged", merge_distance=1e-4, cleanup_dist=0.0,
                 fix_tjunctions=True, quad_pair=False, quad_angle=1.92,
                 context=None):
    """Create a mesh object from verts/faces (world space): assign materials,
    weld doubles, collapse sliver edges, pair seam triangles into quads
    (``quad_pair``), resolve T-junctions, unify normals to +Z. Returns the new
    Object, linked to the active collection."""
    context = context or bpy.context
    me = bpy.data.meshes.new(name)
    me.from_pydata([v[:] for v in verts], [], faces)

    # material slots + per-face index (set before bmesh so it carries through)
    if materials:
        for m in materials:
            me.materials.append(m)
        if face_mats and len(face_mats) == len(me.polygons):
            me.polygons.foreach_set("material_index", face_mats)
    me.update()

    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=max(merge_distance, 1e-9))
    # Collapse sliver triangles (the thin black wedges) — merges their short
    # edge into a neighbour rather than leaving a hole.
    if cleanup_dist > 0.0:
        bmesh.ops.dissolve_degenerate(bm, dist=cleanup_dist, edges=bm.edges)
    # Pair the seam triangles into quads (Quad Fill mode). Only triangles are
    # touched — kept grid/drape quads pass through untouched. The flat mesh is
    # coplanar, so only the shape angle limits which pairs are allowed.
    if quad_pair:
        tris = [f for f in bm.faces if len(f.verts) == 3 and f.is_valid]
        if tris:
            ret = bmesh.ops.join_triangles(
                bm, faces=tris,
                angle_face_threshold=3.15,
                angle_shape_threshold=quad_angle,
                cmp_materials=True,
            )
            # Quality guard: greedy pairing can produce a quad with a (near-)
            # collinear corner — flat-shading dents in 3D. Split those back
            # into their triangles (BEAUTY picks the better diagonal). Only
            # quads created by the pairing are touched, never source quads.
            bad = [f for f in ret.get("faces", [])
                   if f.is_valid and len(f.verts) == 4
                   and min(l.calc_angle() for l in f.loops) < math.radians(14)]
            if bad:
                bmesh.ops.triangulate(bm, faces=bad, quad_method="BEAUTY")
    # Stitch T-junctions so every vertex is genuinely shared (no "fake" verts).
    if fix_tjunctions:
        weld_t_junctions(bm, tol=max(merge_distance, 1e-6))
    # Flat mesh: make every face point +Z so nothing shows as a red backface.
    bm.normal_update()
    flip = [f for f in bm.faces if f.normal.z < 0.0]
    if flip:
        bmesh.ops.reverse_faces(bm, faces=flip)
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new(name, me)
    context.collection.objects.link(obj)
    return obj
