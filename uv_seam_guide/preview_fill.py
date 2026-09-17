"""Throwaway quad fill, so the boundary density can actually be judged.

Whether a panel outline carries the right number of vertices is not something
anyone can see from the outline. It shows up the moment there are faces: too
few and the quads are lozenges, too many and the interior is a waste. So the
loop the user needs is

    Generate boundary -> fill -> look -> Adjust Density -> fill again

and every step of that has to be cheap and reversible.

This is NOT the final topology generator. It makes no attempt at edge flow,
poles, or patch decomposition — it lays a grid inside the outline, triangulates
the gap against the real boundary, and pairs the triangles into quads. The
result is meant to be looked at and thrown away.

Two things make that safe:

  * every vertex and face it creates is flagged in an int layer, so Clear
    removes exactly what was added and nothing the user made
  * the existing boundary vertices are never moved and never added to. They
    encode the sewn correspondence between two panels — one extra vertex on
    one side and the seam no longer matches — so they go into the constrained
    triangulation as fixed constraints and come back out untouched

Panels are separated by the Guide ISLAND each boundary vertex sits on, rather
than by walking loops: two panels whose flat outlines touch would otherwise be
read as one region.

Depends on shapely (bundled as a wheel, see blender_manifest.toml) for point-in-polygon and
for turning a bag of boundary edges into regions.
"""

import math
from collections import defaultdict

import bmesh
from mathutils import Vector
from mathutils.geometry import delaunay_2d_cdt

from . import anchor_segments as anch
from . import face_orient as fo

# One name per DOMAIN — see patch_grid: a shared name loses all but one layer
# when the mesh is saved.
FILL_VERT_LAYER = "ac9_fill_vert"
FILL_EDGE_LAYER = "ac9_fill_edge"
FILL_FACE_LAYER = "ac9_fill_face"


class ShapelyMissing(RuntimeError):
    """Raised when shapely can't be imported — surfaced to the user in the UI."""


def _require_shapely():
    try:
        from shapely.geometry import Polygon, Point  # noqa: F401
        from shapely.ops import polygonize, unary_union  # noqa: F401
        from shapely.prepared import prep  # noqa: F401
        import shapely
        return shapely
    except Exception as exc:  # pragma: no cover - environment dependent
        raise ShapelyMissing(
            "shapely is required for Preview Fill but could not be imported. "
            "Install it into Blender's Python, e.g.:\n"
            "  <blender>/python/bin/python -m pip install shapely"
        ) from exc


# --------------------------------------------------------------------- layers
def _vert_layer(bm, create=True):
    lay = bm.verts.layers.int.get(FILL_VERT_LAYER)
    if lay is None and create:
        lay = bm.verts.layers.int.new(FILL_VERT_LAYER)
    return lay


def _face_layer(bm, create=True):
    lay = bm.faces.layers.int.get(FILL_FACE_LAYER)
    if lay is None and create:
        lay = bm.faces.layers.int.new(FILL_FACE_LAYER)
    return lay


def _edge_layer(bm, create=True):
    lay = bm.edges.layers.int.get(FILL_EDGE_LAYER)
    if lay is None and create:
        lay = bm.edges.layers.int.new(FILL_EDGE_LAYER)
    return lay


def clear_fill(bm, within=None):
    """Remove exactly what a previous fill added. Returns (faces, verts).

    Faces first, then the vertices that were created for the fill — deleting
    those vertices while their faces still exist would take neighbouring faces
    with them.

    `within` is an optional vertex predicate; when given, only geometry whose
    vertices satisfy it is touched (an island is one connected component, so
    testing one vertex of a face or edge is testing the island). Same contract
    as patch_grid.clear_grid — it is what lets a run scoped to the selected
    islands leave every other island's fill alone.
    """
    flay = _face_layer(bm, create=False)
    elay = _edge_layer(bm, create=False)
    vlay = _vert_layer(bm, create=False)
    ok = (lambda v: True) if within is None else within
    n_f = n_v = 0
    # Faces ONLY. 'FACES' looks tempting — it would take the orphaned chords
    # too — but once a panel is filled its boundary edges belong to a face as
    # well, so 'FACES' deletes the whole outline with them (measured: 583
    # vertices down to 35). The chords are removed by their own flag instead.
    if flay is not None:
        dead = [f for f in bm.faces if f[flay] and ok(f.verts[0])]
        n_f = len(dead)
        if dead:
            bmesh.ops.delete(bm, geom=dead, context='FACES_ONLY')
    if vlay is not None:
        bm.verts.ensure_lookup_table()
        dead = [v for v in bm.verts if v[vlay] and ok(v)]
        n_v = len(dead)
        if dead:
            bmesh.ops.delete(bm, geom=dead, context='VERTS')
    if elay is not None:
        dead = [e for e in bm.edges
                if e[elay] and not e.link_faces and ok(e.verts[0])]
        if dead:
            bmesh.ops.delete(bm, geom=dead, context='EDGES')
    return n_f, n_v


# ------------------------------------------------------------------- regions
def island_of_boundary(guide, retopo, flat_sk, bm, match_distance=0.0,
                       tol=0.0008):
    """retopo vertex index -> the Guide island its flat position sits on.

    Matched against the Guide's boundary EDGES, not its vertices. Generation
    places a retopo vertex at an arbitrary arc-length parameter along a span,
    so it usually lands BETWEEN two Guide vertices — up to half a Guide edge
    away from either. A vertex-to-vertex lookup therefore misses most of them:
    measured on the production file, 340 of 583 found, which fragmented every
    panel outline and left 17 of 19 panels unfillable.
    """
    from mathutils import kdtree

    mesh = guide.data
    flat_co = anch.get_flat_co(guide, flat_sk)
    islands = anch._vertex_islands(guide)

    # An island too small to be a panel is CLO export debris — a lone
    # triangle left where two pieces met — and must not be a candidate: it
    # sits exactly on a real panel's outline, so the outline vertices nearest
    # to it are captured by the debris and the real panel's ring loses the
    # edge between them (measured 2026-09-16: a 3-vertex island 1.1 mm across
    # at the tie's corner took 2 of 36 outline verts, "no closed region from
    # 35 edges", and the tie was the one panel Preview Fill never filled).
    # A panel is never smaller than 10 x the match tolerance across.
    min_extent = tol * 10.0
    lo, hi = {}, {}
    for vi, isl in enumerate(islands):
        co = flat_co[vi]
        if isl not in lo:
            lo[isl] = [co.x, co.y]
            hi[isl] = [co.x, co.y]
            continue
        l, h = lo[isl], hi[isl]
        if co.x < l[0]: l[0] = co.x
        if co.y < l[1]: l[1] = co.y
        if co.x > h[0]: h[0] = co.x
        if co.y > h[1]: h[1] = co.y
    debris = {isl for isl in lo
              if max(hi[isl][0] - lo[isl][0], hi[isl][1] - lo[isl][1]) < min_extent}

    gbm = bmesh.new()
    gbm.from_mesh(mesh)
    gbm.edges.ensure_lookup_table()
    bedges = [(e.verts[0].index, e.verts[1].index)
              for e in gbm.edges
              if e.is_boundary and islands[e.verts[0].index] not in debris]
    gbm.free()
    if not bedges:
        return {}

    at_vert = defaultdict(list)
    longest = 0.0
    for ei, (a, b) in enumerate(bedges):
        at_vert[a].append(ei)
        at_vert[b].append(ei)
        longest = max(longest, (flat_co[a] - flat_co[b]).length)

    gverts = sorted({v for e in bedges for v in e})
    kd = kdtree.KDTree(len(gverts))
    for k, v in enumerate(gverts):
        kd.insert(flat_co[v], v)
    kd.balance()
    radius = tol + longest

    def _seg_dist(p, a, b):
        ab = b - a
        d2 = ab.dot(ab)
        if d2 <= 1e-18:
            return (p - a).length
        t = max(0.0, min(1.0, (p - a).dot(ab) / d2))
        return (p - (a + ab * t)).length

    mat = retopo.matrix_world
    out = {}
    for v in bm.verts:
        p = mat @ v.co
        cand = set()
        for _co, idx, _d in kd.find_range(p, radius):
            cand.update(at_vert[idx])
        best, best_d = None, tol
        for ei in cand:
            a, b = bedges[ei]
            d = _seg_dist(p, flat_co[a], flat_co[b])
            if d <= best_d:
                best_d, best = d, ei
        if best is not None:
            out[v.index] = islands[bedges[best][0]]
    return out


def _regions_for_island(edges_xy, shp):
    """Turn a bag of boundary segments into filled regions (with holes)."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import polygonize, unary_union

    lines = [LineString([a, b]) for a, b in edges_xy]
    polys = list(polygonize(unary_union(lines)))
    if not polys:
        return []
    # A region wholly inside another is a hole in it, not a region of its own:
    # a pocket opening, a buttonhole. Filling both would double up.
    polys.sort(key=lambda p: p.area, reverse=True)
    out = []
    for p in polys:
        parent = next((q for q in out if q.contains(p)), None)
        if parent is None:
            out.append(p)
        else:
            out[out.index(parent)] = Polygon(
                parent.exterior, list(parent.interiors) + [p.exterior])
    return out


# ---------------------------------------------------------------------- fill
def _grid_points(poly, target, margin, prep_poly):
    """Interior lattice at `target` spacing, kept `margin` clear of the edge.

    Offset by half a cell from the bounding box so the lattice does not line
    up with a straight panel edge and produce a row of zero-area slivers.
    """
    minx, miny, maxx, maxy = poly.bounds
    pts = []
    from shapely.geometry import Point
    y = miny + target * 0.5
    while y < maxy:
        x = minx + target * 0.5
        while x < maxx:
            p = Point(x, y)
            if prep_poly.contains(p) and poly.exterior.distance(p) >= margin:
                if all(r.distance(p) >= margin for r in poly.interiors):
                    pts.append((x, y))
            x += target
        y += target
    return pts


def fill_regions(bm, retopo, island_of, target_mm, margin_factor=0.6,
                 quad_angle=math.pi, epsilon=1e-6, within=None,
                 progress=None):
    """Fill panel regions with a quad-dominant mesh. Returns a report.

    target_mm is the wanted interior edge length, in the FLAT layout's
    millimetres (this fills the retopo, which lives in the flat layout).

    The boundary it has to match was generated by
    anchor_segments.division_parameters, which divides by 3D arc length -- real
    fabric millimetres. The two therefore agree only after the caller converts
    with clo_projector.guide.flat_scale; handing both the same raw
    setting is the bug AUDIT §8-D-1 records, and it went unnoticed because the
    Guide it was developed against happens to have a flat layout at 1.0023x
    real scale (a shirt in the same file is at 1.1245x, and there the boundary
    came out 21.95 mm against a 20.00 mm interior).

    `within` is an optional vertex predicate limiting which panels are filled
    — the same scope contract every other region tool in this package honours
    (see panel_regions.mark_scope / scope_test). Without it every panel is
    filled, which is what Object Mode wants; in Edit Mode the user is looking
    at one panel at a time and the run must leave the rest alone.
    """
    shp = _require_shapely()
    from shapely.geometry import Point

    mat = retopo.matrix_world
    inv = mat.inverted()
    vlay = _vert_layer(bm)
    flay = _face_layer(bm)

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    # Hold BMVert references, not indices. Creating a vertex invalidates the
    # index table (measured: IndexError "outdated internal index table" as soon
    # as a second panel was reached), but the references themselves stay good.
    vref = {v.index: v for v in bm.verts}
    elay = _edge_layer(bm)
    pre_edges = {frozenset((e.verts[0], e.verts[1])) for e in bm.edges}

    # Boundary edges, grouped by the panel both ends agree on.
    by_island = defaultdict(list)
    for e in bm.edges:
        if len(e.link_faces) > 1:
            continue
        a, b = e.verts[0].index, e.verts[1].index
        ia, ib = island_of.get(a), island_of.get(b)
        if ia is None or ib is None:
            continue
        # Two panels laid out touching share an outline edge. Dropping it
        # broke the ring on BOTH of them (measured: island 2620 left with two
        # loose ends and no closed region). Give it to each.
        by_island[ia].append((a, b))
        if ib != ia:
            by_island[ib].append((a, b))

    target = target_mm / 1000.0
    margin = target * margin_factor
    report = {"regions": 0, "verts": 0, "faces": 0, "skipped": 0,
              "islands": len(by_island)}
    new_faces = []

    report["skipped_out_of_scope"] = 0
    n_islands = len(by_island)
    for i_isl, (isl, edges) in enumerate(by_island.items()):
        if progress is not None and n_islands:
            progress((i_isl + 1) / n_islands)
        # Out of scope: not one of this panel's outline vertices is marked.
        if within is not None and not any(
                within(vref[a]) or within(vref[b]) for a, b in edges):
            report["skipped_out_of_scope"] += 1
            continue
        pos = {}
        for a, b in edges:
            pos[a] = vref[a].co
            pos[b] = vref[b].co
        edges_xy = [((mat @ pos[a]).x, (mat @ pos[a]).y,
                     (mat @ pos[b]).x, (mat @ pos[b]).y) for a, b in edges]
        edges_xy = [((x0, y0), (x1, y1)) for x0, y0, x1, y1 in edges_xy]
        try:
            polys = _regions_for_island(edges_xy, shp)
        except Exception as exc:  # noqa: BLE001 — one bad panel must not stop the rest
            report["skipped"] += 1
            report.setdefault("why", []).append(
                f"isl{isl}: {type(exc).__name__}: {exc}")
            continue
        if not polys:
            report["skipped"] += 1
            report.setdefault("why", []).append(
                f"isl{isl}: no closed region from {len(edges)} edges")
            continue

        # world-XY -> existing vertex index, so the CDT reuses real vertices
        key_of = {}
        for a, b in edges:
            for i in (a, b):
                w = mat @ vref[i].co
                key_of[(round(w.x, 7), round(w.y, 7))] = i

        for poly in polys:
            # A sliver region is a triangulation artefact, not a panel.
            if poly.area < (target * 0.25) ** 2:
                continue
            rings = [list(poly.exterior.coords)[:-1]]
            rings += [list(r.coords)[:-1] for r in poly.interiors]

            coords, cdt_edges, orig_vert = [], [], []
            for ring in rings:
                start = len(coords)
                for (x, y) in ring:
                    coords.append(Vector((x, y)))
                    orig_vert.append(key_of.get((round(x, 7), round(y, 7))))
                n = len(ring)
                for k in range(n):
                    cdt_edges.append((start + k, start + (k + 1) % n))
            n_boundary = len(coords)

            prep_poly = shp.prepared.prep(poly)
            for (x, y) in _grid_points(poly, target, margin, prep_poly):
                coords.append(Vector((x, y)))
                orig_vert.append(None)

            try:
                ovc, _oe, of, ov_orig, _oeo, _ofo = delaunay_2d_cdt(
                    coords, cdt_edges, [], 1, epsilon, True)
            except Exception as exc:  # noqa: BLE001
                report["skipped"] += 1
                report.setdefault("why", []).append(
                    f"isl{isl}: CDT {type(exc).__name__}: {exc}")
                continue

            # Output vertex -> a bmesh vertex. Anything that came from an
            # existing boundary vertex reuses it AT ITS ORIGINAL POSITION —
            # the CDT is allowed to nudge coordinates, and a nudged seam
            # vertex is a broken seam.
            vmap = []
            for k, co in enumerate(ovc):
                src = [i for i in ov_orig[k] if i < n_boundary]
                reuse = None
                for i in src:
                    if orig_vert[i] is not None:
                        reuse = orig_vert[i]
                        break
                if reuse is not None:
                    vmap.append(vref[reuse])
                else:
                    nv = bm.verts.new(inv @ Vector((co.x, co.y, 0.0)))
                    nv[vlay] = 1
                    vmap.append(nv)
                    report["verts"] += 1

            made = 0
            for tri in of:
                if len(tri) < 3:
                    continue
                c = Vector((0.0, 0.0))
                for i in tri:
                    c += Vector((ovc[i].x, ovc[i].y))
                c /= len(tri)
                # A hole is bounded by constraint edges too, and output_type 1
                # fills it. Drop anything whose centre is not in the region.
                if not prep_poly.contains(Point(c.x, c.y)):
                    continue
                vs = [vmap[i] for i in tri]
                if len(set(vs)) != len(vs):
                    continue
                try:
                    f = bm.faces.new(vs)
                except ValueError:
                    continue   # already exists
                f[flay] = 1
                new_faces.append(f)
                made += 1
            report["faces"] += made
            report["regions"] += 1

    # Anything strung between two existing boundary vertices is the fill's
    # doing and has to go with it.
    for e in bm.edges:
        if frozenset((e.verts[0], e.verts[1])) not in pre_edges:
            e[elay] = 1

    if new_faces:
        bmesh.ops.join_triangles(
            bm, faces=new_faces,
            cmp_seam=False, cmp_sharp=False, cmp_uvs=False, cmp_vcols=False,
            cmp_materials=False,
            angle_face_threshold=quad_angle, angle_shape_threshold=quad_angle,
            topology_influence=0.0, deselect_joined=False)
        bm.faces.ensure_lookup_table()
        # join_triangles replaces some of new_faces with merged quads, so
        # re-select by the fill's own marker layer rather than reusing the
        # (now partly stale) new_faces list. See face_orient's docstring on
        # why this — not recalc_face_normals — is the right call on a flat,
        # open panel.
        fo.orient_faces_up([f for f in bm.faces if f[flay]], mat)
        report["faces_after_quads"] = sum(1 for f in bm.faces if f[flay])
        report["quads"] = sum(1 for f in bm.faces if f[flay] and len(f.verts) == 4)
        report["tris"] = sum(1 for f in bm.faces if f[flay] and len(f.verts) == 3)
    return report


def _fill_bm(bm, guide, retopo, flat_sk, target_mm, margin_factor, quad_angle,
             match_distance, selected_only, progress=None):
    """Clear a previous fill and lay a fresh one into *bm*. Returns the report.

    The scope layer is created FIRST: creating a layer reallocates custom
    data and invalidates every BMVert reference held afterwards (the trap
    fill_regions documents), and `scope_test` re-fetches it by name because
    the later fill layers shift it.
    """
    from . import panel_regions as prg
    n_scope = prg.mark_scope(bm, selected_only)
    within = prg.scope_test(bm) if selected_only else None
    if selected_only and n_scope == 0:
        prg.unmark_scope(bm)
        return {"regions": 0, "verts": 0, "faces": 0, "skipped": 0,
                "islands": 0, "quads": 0, "tris": 0, "cleared_faces": 0,
                "cleared_verts": 0, "scope_verts": 0}

    bm.verts.ensure_lookup_table()
    cleared_f, cleared_v = clear_fill(bm, within=within)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()

    island_of = island_of_boundary(guide, retopo, flat_sk, bm,
                                   match_distance=match_distance)
    report = fill_regions(bm, retopo, island_of, target_mm,
                          margin_factor=margin_factor, quad_angle=quad_angle,
                          within=within, progress=progress)
    report["cleared_faces"] = cleared_f
    report["cleared_verts"] = cleared_v
    report["scope_verts"] = n_scope
    prg.unmark_scope(bm)
    return report


def run_preview_fill(guide, retopo, flat_sk, target_mm, margin_factor=0.6,
                     quad_angle=math.pi, match_distance=0.0,
                     selected_only=False, progress=None):
    """Clear a previous fill and lay a fresh one. Returns the report.

    Object Mode: the mesh is read into a fresh bmesh and written back.
    Edit Mode  : works on the live edit-mesh in place, the retopo stays in
    Edit Mode. Geometry is added, so update_edit_mesh needs
    loop_triangles=True — False segfaults Blender.

    selected_only limits the run to the islands holding a selection, the same
    contract Fill Regions and Grid Regions have in Edit Mode. Every other
    island keeps whatever fill it already had.

    `progress`, if given, is called with a 0..1 fraction once per panel — pass
    a ui_common.ProgressThrottle, never a raw progress_update.
    """
    me = retopo.data
    if retopo.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(me)
        report = _fill_bm(bm, guide, retopo, flat_sk, target_mm, margin_factor,
                          quad_angle, match_distance, selected_only, progress)
        bmesh.update_edit_mesh(me, loop_triangles=True, destructive=True)
        return report
    bm = bmesh.new()
    bm.from_mesh(me)
    report = _fill_bm(bm, guide, retopo, flat_sk, target_mm, margin_factor,
                      quad_angle, match_distance, selected_only, progress)
    bm.to_mesh(me)
    bm.free()
    me.update()
    return report


def run_clear_fill(retopo):
    """Remove the preview fill — all of it. Object Mode, or Edit Mode in place.

    Deliberately NOT scoped to the selection, unlike Fill. The fill is
    throwaway geometry and × means "take it off"; a Clear that left some of it
    behind depending on what happened to be selected is a worse button, and
    scoping it needed a second scope-layer create/remove on the live
    edit-mesh, which measured unreliable (the flood-fill seeds picked up
    selection state left by the preceding fill and the scope leaked into
    neighbouring islands). Fill scopes because it ADDS geometry that would
    otherwise bury the rest of the layout; Clear only removes its own marks,
    so there is nothing to protect.
    """
    me = retopo.data
    if retopo.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(me)
        bm.verts.ensure_lookup_table()
        n_f, n_v = clear_fill(bm)
        bmesh.update_edit_mesh(me, loop_triangles=True, destructive=True)
        return {"faces": n_f, "verts": n_v}
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()
    n_f, n_v = clear_fill(bm)
    bm.to_mesh(me)
    bm.free()
    me.update()
    return {"faces": n_f, "verts": n_v}
