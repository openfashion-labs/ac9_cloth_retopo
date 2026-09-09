"""Cut a panel by hand, grid what the cuts made.

The four-corner patch grid (`patch_grid`) can fill a patch, but a garment panel
is not a patch: it is several. Asking the user to reduce a panel outline to
four corners does not work — measured on a production Guide, cutting each
outline at its corners gave twelve sides on the two largest panels, and no
choice of four of them describes the shape. A body panel with an armhole and a
stepped hem simply is not a quadrilateral.

So the division stays the user's, the way it is when this is done by hand:

    Fill Regions  ->  cut freely with the Knife  ->  Grid Regions

Filling first is what makes the Knife usable at all: it cuts faces, and a bare
boundary has none. One n-gon per closed region does it — measured, 20 regions
of up to 76 vertices, `mesh.validate()` clean — and `bmesh.ops.edgenet_fill`
builds them all in one call, before OR after the cuts exist, so the same button
re-fills whatever the current cuts describe.

WHERE THE CORNERS COME FROM
---------------------------
Once there are cuts, most corners are no longer a guess: a vertex where three
or more of the panel's edges meet is where the user's cut lands, and that is a
corner by construction. Only the outline's own corners still need saying, and
they fall into two kinds that behave differently:

  * a DEEP REFLEX corner (a stepped hem, the inside of an armhole) must be a
    patch corner. A region containing one cannot be mapped to a square without
    the map folding — exactly the failure already measured on the two concave
    panels, 8 of 150 core nodes landing outside the outline. If a region still
    has more than four of these it needs another cut, and that is reported
    rather than guessed at
  * a decorative reflex corner is not. The teeth of a zigzag hem are reflex
    every other vertex (thirteen on one production panel) and asking for a cut
    line at each would be unusable. They are absorbed by the boundary band,
    which works because the side curve the grid is mapped from is smoothed
    first

Depth alone does NOT separate those two, which is worth knowing before trying:
measured over a 20 mm window, the zigzag teeth are 10-15 mm deep and the
notches that do matter 22-26 mm — overlapping ranges. What separates them is
how the depth GROWS when the window widens. A real corner keeps getting deeper
because the outline never comes back; a tooth saturates because it does:

    armhole notch    21.8 mm -> 41.5 mm over 20 -> 40 mm window   ratio 1.90
    stepped hem      26.4    -> 49.7                              ratio 1.88
    zigzag tooth     14.8    -> 18.8                              ratio 1.27
    tooth, 2nd       12.5    -> 16.2                              ratio 1.30

So a reflex corner is mandatory when it is both deep enough to matter and still
deepening at twice the window
  * a CONVEX corner can sit in the middle of a side. The grid maps a side from
    its actual curve, so a collar point or the tip of a lapel costs nothing —
    it does not have to become a patch corner

That leaves the case of a region with fewer than four corners, where something
has to be chosen. The choice is bounded: only convex outline turns, sharpest
first, only as many as the shortfall, never touching what the user marked, and
every one of them reported with its position.

INTERIOR LINES ARE SHARED
-------------------------
A cut line borders two regions, so its vertex count cannot be decided by
whichever region is gridded first: the left region may want thirteen divisions
along it and the right one ten, and there is one set of vertices. Every
interior line is therefore divided up front, in one pass, from the spacing —
and since a cut is not a seam, that division is free to choose. The user's own
cut vertices are kept: they are the shape of the line.
"""

import math
from collections import defaultdict

import bmesh
from mathutils import Vector
from mathutils.geometry import intersect_line_line_2d

from . import face_orient as fo
from . import patch_grid as pg
from . import topology_corner as tc

SCAFFOLD_LAYER = "ac9_scaffold"
LINE_LAYER = "ac9_line_divide"
SCOPE_LAYER = "ac9_scope_tmp"   # per-run only: marks the islands being worked on


# --------------------------------------------------------------------- layers
def scaffold_layer(bm, create=True):
    lay = bm.faces.layers.int.get(SCAFFOLD_LAYER)
    if lay is None and create:
        lay = bm.faces.layers.int.new(SCAFFOLD_LAYER)
    return lay


def line_layer(bm, create=True):
    """Marks the vertices THIS tool put on a cut line, not the user's own.

    A cut line has to be divided before the regions either side of it can be
    gridded, and that division belongs to the grid, not to the drawing: Clear
    takes it back off so the next run can divide at a different spacing. Without
    that, a re-grid could only ever add vertices to a line, never fewer — the
    same trap Generate has with the boundary.
    """
    lay = bm.verts.layers.int.get(LINE_LAYER)
    if lay is None and create:
        lay = bm.verts.layers.int.new(LINE_LAYER)
    return lay


# ---------------------------------------------------------------------- scope
def mark_scope(bm, selected_only):
    """Flag the vertices this run may touch. Returns how many.

    Object Mode works the whole object. In Edit Mode the user is looking at
    one island at a time, so the run is limited to the islands that hold any
    selected vertex, edge or face — the rest keep whatever grid they have.
    The flag is a vertex int layer, created here BEFORE any vertex reference
    is held (creating a layer invalidates them), read back through
    `scope_test` (which fetches the layer afresh, since later layers shift
    it), and removed by `unmark_scope` before the mesh is written.
    """
    lay = bm.verts.layers.int.get(SCOPE_LAYER)
    if lay is None:
        lay = bm.verts.layers.int.new(SCOPE_LAYER)
    bm.verts.ensure_lookup_table()
    if not selected_only:
        for v in bm.verts:
            v[lay] = 1
        return len(bm.verts)
    seeds = [v for v in bm.verts if v.select]
    for e in bm.edges:
        if e.select:
            seeds.extend(e.verts)
    for f in bm.faces:
        if f.select:
            seeds.extend(f.verts)
    seen = set()
    stack = list(seeds)
    while stack:
        v = stack.pop()
        if v in seen:
            continue
        seen.add(v)
        for e in v.link_edges:
            o = e.other_vert(v)
            if o not in seen:
                stack.append(o)
    for v in bm.verts:
        v[lay] = 1 if v in seen else 0
    return len(seen)


def scope_test(bm):
    """Vertex predicate for the current scope, or None when nothing is marked."""
    lay = bm.verts.layers.int.get(SCOPE_LAYER)
    if lay is None:
        return None
    return lambda v: bool(v[lay])


def unmark_scope(bm):
    lay = bm.verts.layers.int.get(SCOPE_LAYER)
    if lay is not None:
        bm.verts.layers.int.remove(lay)


def flush_selection_down(bm):
    """Make face selection visible in vertex/edge select modes too."""
    for f in bm.faces:
        if f.select:
            for v in f.verts:
                v.select = True
            for e in f.edges:
                e.select = True


# ----------------------------------------------------------------------- fill
def _prune_open_chains(edges):
    """Drop wire edges that end at a degree-1 vertex, repeatedly.

    An open chain (a pocket outline whose top edge is deliberately unsewn, a
    cut line that never reached the outline) cannot bound a face. Left in the
    net it derails the face walk: the walk would run out along the spur and
    back, visiting the spur's vertices twice.
    """
    edges = set(edges)
    while True:
        deg = defaultdict(int)
        for e in edges:
            deg[e.verts[0]] += 1
            deg[e.verts[1]] += 1
        dead = [e for e in edges if deg[e.verts[0]] == 1 or deg[e.verts[1]] == 1]
        if not dead:
            return edges
        edges.difference_update(dead)


def trace_planar_regions(edges, mat):
    """Every bounded face of the planar wire graph, as ordered vertex rings.

    Standard half-edge walk: at each vertex the neighbours are sorted by
    angle (world XY), and a walk arriving along u->v leaves along the
    neighbour just clockwise of u — the "take the tightest left turn" rule —
    so every directed edge is used exactly once and each closed walk is one
    face. Bounded faces come out counter-clockwise (positive signed area);
    each connected component's unbounded outer face comes out clockwise and
    is dropped by that sign alone, which also handles several disconnected
    panels in one call.

    Why not bmesh.ops.edgenet_fill on the whole net: measured on the user's
    production file, one global edgenet_fill over ~680 wire edges (25 real
    regions) merged 5 regions into 2 faces that ignored the cut lines
    between them, dropped 2 regions entirely (no face, no report — they
    just went dark), and put 11 faces where no region had been on the
    previous pass. Inserting 56 collinear vertices on the cut lines was
    enough to change which regions it got wrong. Filling each closed loop
    on its own filled all 25 — so the loops are fine; the global heuristic
    is not. This walk is deterministic and has no such interaction.

    A closed loop nested inside a region (a sewn-shut inner outline) still
    becomes its own face overlapping the outer region's, as before — this is
    not a hole-aware fill. Returns (rings, dropped) where `dropped` counts
    walks skipped because they revisit a vertex (a pinch point).
    """
    edges = _prune_open_chains(edges)
    if not edges:
        return [], 0
    adj = defaultdict(list)
    for e in edges:
        a, b = e.verts
        adj[a].append(b)
        adj[b].append(a)

    xy = {}
    for v in adj:
        w = mat @ v.co
        xy[v] = Vector((w.x, w.y))

    order = {}
    for v, nbrs in adj.items():
        nbrs.sort(key=lambda n: math.atan2(xy[n].y - xy[v].y, xy[n].x - xy[v].x))
        order[v] = {n: i for i, n in enumerate(nbrs)}

    used = set()
    rings, dropped = [], 0
    for u in adj:
        for v in adj[u]:
            if (u, v) in used:
                continue
            ring = []
            a, b = u, v
            while (a, b) not in used:
                used.add((a, b))
                ring.append(a)
                nbrs = adj[b]
                # arriving at b from a: leave along the neighbour just
                # clockwise of a in b's angular order (tightest left turn)
                k = order[b][a]
                a, b = b, nbrs[(k - 1) % len(nbrs)]
            if len(ring) < 3:
                continue
            area = 0.0
            for i in range(len(ring)):
                p, q = xy[ring[i]], xy[ring[(i + 1) % len(ring)]]
                area += p.x * q.y - q.x * p.y
            if area <= 0.0:
                continue          # the component's outer face
            if len(set(ring)) != len(ring):
                dropped += 1      # pinched walk — not a valid face ring
                continue
            rings.append(ring)
    return rings, dropped


def _interior_probe(ring, mat, step=1e-4):
    """A point just inside the ring: 0.1 mm along the inward bisector at a
    convex corner. Robust where a centroid is not (a concave region's
    centroid can lie outside it, inside a neighbour)."""
    xy = []
    for v in ring:
        w = mat @ v.co
        xy.append(Vector((w.x, w.y)))
    n = len(xy)
    area = 0.0
    for i in range(n):
        p, q = xy[i], xy[(i + 1) % n]
        area += p.x * q.y - q.x * p.y
    sign = 1.0 if area >= 0.0 else -1.0
    for i in range(n):
        a, b, c = xy[i - 1], xy[i], xy[(i + 1) % n]
        d0, d1 = (b - a), (c - b)
        if d0.length < 1e-12 or d1.length < 1e-12:
            continue
        cross = d0.x * d1.y - d0.y * d1.x
        if cross * sign <= 0.0:
            continue              # reflex corner: bisector points outward
        bis = (-d0.normalized() + d1.normalized())
        if bis.length < 1e-12:
            continue
        return b + bis.normalized() * step
    return sum(xy, Vector((0.0, 0.0))) / n


def fill_regions(bm, mat, within=None):
    """One n-gon per closed EMPTY region of the edge net. Returns a report.

    `within`: optional vertex predicate limiting the net to some islands.

    The tracer graph is every edge with at most one face — wire edges AND the
    boundary edges of faces that already exist. That second part is what
    makes a region next to an existing face fillable at all: its shared side
    is a face-boundary edge, and a net of wire edges only never closes that
    loop. Measured on the user's production file: three cells of a centre
    strip sat dark through every Fill and Grid run — reported nowhere — because
    their left side was shared with twelve untagged faces the user had made
    by hand. With those edges in the graph the tracer finds them.

    The graph also traces the union of any adjacent existing faces as one
    ring (their shared edges carry two faces and are not in the graph), so
    every ring is probed at a point just inside a convex corner and skipped
    when that point already lies under a face. Faces are built ring by ring
    from trace_planar_regions (see its docstring for why not one
    edgenet_fill).

    `mat` is the retopo object's matrix_world, needed both to sort the walk
    by angle and to orient the new faces consistently — see
    face_orient.orient_faces_up's docstring on why
    bmesh.ops.recalc_face_normals doesn't work on a flat, open panel.
    """
    lay = scaffold_layer(bm)          # before any reference is held
    ok = (lambda v: True) if within is None else within
    graph = [e for e in bm.edges if len(e.link_faces) <= 1 and ok(e.verts[0])]
    if not any(not e.link_faces for e in graph):
        return {"faces": 0, "sizes": [], "open_edges": 0, "pinched": 0, "occupied": 0}
    rings, dropped = trace_planar_regions(graph, mat)

    existing = []
    for f in bm.faces:
        pts = [((mat @ v.co).x, (mat @ v.co).y) for v in f.verts]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        existing.append((min(xs), min(ys), max(xs), max(ys), pts))

    def occupied(pt):
        for x0, y0, x1, y1, pts in existing:
            if x0 <= pt.x <= x1 and y0 <= pt.y <= y1 and pg._point_in_ring(pt.x, pt.y, pts):
                return True
        return False

    faces = []
    skipped_occupied = 0
    for ring in rings:
        if existing and occupied(_interior_probe(ring, mat)):
            skipped_occupied += 1
            continue
        try:
            f = bm.faces.new(ring)
        except ValueError:
            continue
        f[lay] = 1
        faces.append(f)
    if faces:
        fo.orient_faces_up(faces, mat)
    return {
        "faces": len(faces),
        "sizes": sorted((len(f.verts) for f in faces), reverse=True),
        "open_edges": sum(1 for e in bm.edges if not e.link_faces),
        "pinched": dropped,
        "occupied": skipped_occupied,
    }


def clear_scaffold(bm, within=None):
    """Remove the n-gons, leaving every edge (the user's cuts included)."""
    lay = scaffold_layer(bm, create=False)
    if lay is None:
        return 0
    ok = (lambda v: True) if within is None else within
    dead = [f for f in bm.faces if f[lay] and ok(f.verts[0])]
    if dead:
        bmesh.ops.delete(bm, geom=dead, context='FACES_ONLY')
    return len(dead)


# ------------------------------------------------------------------- panels
def panel_components(bm, ours=None):
    """Group faces into panels. Returns (comp_of_face, edges_of_comp).

    Two faces are the same panel when an interior edge joins them, so a panel
    is what the cuts divided rather than anything read back from the Guide.

    `ours` is the set of edges this tool created. They are left out of the
    per-panel edge sets, which is what the corner rule reads: our own grid
    edges are not cut lines and must not make corners.
    """
    parent = {}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for f in bm.faces:
        parent.setdefault(f.index, f.index)
    for e in bm.edges:
        if len(e.link_faces) == 2:
            union(e.link_faces[0].index, e.link_faces[1].index)

    comp_of = {}
    for f in bm.faces:
        comp_of[f.index] = find(f.index)
    edges_of = defaultdict(set)
    for f in bm.faces:
        c = comp_of[f.index]
        for e in f.edges:
            if ours and e in ours:
                continue
            edges_of[c].add(e)
    return comp_of, edges_of


# ------------------------------------------------------------------ corners
def loop_turns(ring_xy):
    """Turn angle and convexity at every vertex of a closed ring.

    Convexity is measured against the ring's own winding, so it does not
    matter which way round the face was built. Returns [(deg, convex)].
    """
    n = len(ring_xy)
    area = 0.0
    for i in range(n):
        p, q = ring_xy[i], ring_xy[(i + 1) % n]
        area += p.x * q.y - q.x * p.y
    sign = 1.0 if area >= 0.0 else -1.0
    out = []
    for i in range(n):
        a, b, c = ring_xy[i - 1], ring_xy[i], ring_xy[(i + 1) % n]
        d0, d1 = b - a, c - b
        if d0.length < 1e-12 or d1.length < 1e-12:
            out.append((0.0, True))
            continue
        d0n, d1n = d0.normalized(), d1.normalized()
        dot = max(-1.0, min(1.0, d0n.dot(d1n)))
        deg = math.degrees(math.acos(dot))
        cross = d0n.x * d1n.y - d0n.y * d1n.x
        out.append((deg, cross * sign >= 0.0))
    return out


def reflex_depth(ring_xy, i, window):
    """How far ring[i] sits off the chord spanning `window` either side of it.

    Walk `window` of arc length each way, take the chord between where you
    land, and measure the distance from ring[i] to it. Used twice per corner,
    at one window and at double, because it is the growth between the two that
    tells a structural corner from a decorative one (see the module docstring
    for the measured numbers).
    """
    n = len(ring_xy)
    p = ring_xy[i]

    def walk(step):
        acc = 0.0
        k = i
        while acc < window:
            nxt = (k + step) % n
            if nxt == i:
                break
            acc += (ring_xy[nxt] - ring_xy[k]).length
            k = nxt
        return ring_xy[k]

    a, b = walk(-1), walk(1)
    ab = b - a
    if ab.length < 1e-12:
        return 0.0
    t = max(0.0, min(1.0, (p - a).dot(ab) / ab.dot(ab)))
    return (p - (a + ab * t)).length


def classify_corners(face, comp_edges, corner_flags, concave_deg=20.0,
                     convex_deg=30.0, depth_window=0.02, depth_min=0.01,
                     growth_min=1.6, pass_deg=None):
    """Which vertices of a region are corners, and why.

    Returns a dict with the ring, the mandatory set (cut junctions and reflex
    outline corners), what the user marked, and the convex turns available to
    make up a shortfall.

    A junction only counts when the ring actually turns there (`pass_deg`,
    default = concave_deg). A cut that ends in a T on a straight line is a
    junction for the two regions it separates — the ring turns 90 degrees
    there — but the region on the far side of the line runs straight through
    it. Making that straight point a patch corner produced the 11.9-degree
    sliver at a T on a production file, and on a panel with a pocket it would
    have made the region above the pocket a patch of three short sides and
    one whole outline. The straight-through junction stays a vertex of its
    side, which is all it ever was.
    """
    if pass_deg is None:
        pass_deg = concave_deg
    ring = [l.vert for l in face.loops]
    ring_xy = [Vector((v.co.x, v.co.y)) for v in ring]
    turns = loop_turns(ring_xy)

    junction, reflex, marked, convex, shallow = [], [], [], [], []
    passthrough = []
    depths = {}
    for i, v in enumerate(ring):
        # Only the user's own edges make a junction. Counting our own grid
        # edges turned every grid vertex on a shared border into a corner —
        # measured on the user's file as regions reporting fifteen mandatory
        # corners after a second run.
        deg = sum(1 for e in v.link_edges if e in comp_edges)
        t, is_convex = turns[i]
        if deg >= 3 and t >= pass_deg:
            junction.append(i)
        elif deg >= 3:
            passthrough.append(i)
        elif not is_convex and t >= concave_deg:
            d = reflex_depth(ring_xy, i, depth_window)
            d2 = reflex_depth(ring_xy, i, depth_window * 2.0)
            growth = (d2 / d) if d > 1e-9 else 0.0
            depths[i] = (d, growth)
            if d >= depth_min and growth >= growth_min:
                reflex.append(i)
            else:
                shallow.append(i)
        if v.index in corner_flags:
            marked.append(i)
        if is_convex:
            (convex if t >= convex_deg else shallow).append(i)
    # sharpest first, and every convex turn is a candidate when a region would
    # otherwise be short of four: a smooth strip has no 30-degree corner but
    # still has to start somewhere.
    spare = sorted((i for i, (t, c) in enumerate(turns)
                    if c and i not in convex),
                   key=lambda i: -turns[i][0])
    return {
        "ring": ring,
        "turns": turns,
        "junction": junction,
        "reflex": reflex,
        "marked": marked,
        "convex": sorted(convex, key=lambda i: -turns[i][0]),
        "spare": spare,
        "reflex_depth": depths,
        "shallow_reflex": [i for i in depths if i not in reflex],
        "passthrough": passthrough,
    }


def choose_corners(info, want=4):
    """Settle on `want` corner positions. Returns (indices, note).

    Mandatory first (cut junctions, then reflex outline corners); anything the
    user marked next; then, only if still short, the sharpest convex turns.
    More mandatory corners than `want` means the region needs another cut, and
    that is reported rather than resolved by dropping one.
    """
    mand = sorted(set(info["junction"]) | set(info["reflex"]))
    if len(mand) > want:
        return None, ("%d corners that have to be patch corners (%d where a cut "
                      "line lands, %d structural notches) — cut this region "
                      "again" % (len(mand), len(info["junction"]),
                                 len(info["reflex"])))
    picked = list(mand)
    added_marked = []
    for i in info["marked"]:
        if len(picked) >= want:
            break
        if i not in picked:
            picked.append(i)
            added_marked.append(i)
    auto, relaxed = [], []
    for i in info["convex"]:
        if len(picked) >= want:
            break
        if i not in picked:
            picked.append(i)
            auto.append(i)
    # Still short: take the sharpest turns there are, below the threshold. A
    # long smooth strip has no real corner anywhere, and refusing it would be
    # worse than starting the grid at its sharpest point.
    for i in info["spare"]:
        if len(picked) >= want:
            break
        if i not in picked:
            picked.append(i)
            auto.append(i)
            relaxed.append(i)
    if len(picked) < want:
        return None, ("only %d corner(s) of %d could be found on a %d-vertex "
                      "region" % (len(picked), want, len(info["ring"])))
    return sorted(picked), {"mandatory": mand, "marked": added_marked,
                            "auto": auto, "relaxed": relaxed}


# ------------------------------------------------------- interior line divide
def interior_chains(bm, comp_edges=None):
    """The cut lines, as chains of interior edges between their end points.

    A chain ends where the interior graph does: at the outline, at a crossing,
    or at a T. The vertices the user's cut path put in the middle of a line
    have interior degree 2 and the chain runs straight through them — they are
    the shape of the line and are kept.
    """
    inner = [e for e in bm.edges if len(e.link_faces) == 2
             and (comp_edges is None or e in comp_edges)]
    if not inner:
        return []
    at = defaultdict(list)
    for e in inner:
        at[e.verts[0]].append(e)
        at[e.verts[1]].append(e)

    def is_node(v):
        return len(at[v]) != 2

    used = set()
    chains = []
    for e in inner:
        if e in used:
            continue
        # walk out both ways from this edge to the nearest nodes
        chain = [e]
        used.add(e)
        for end in (0, 1):
            v = e.verts[end]
            cur = e
            while not is_node(v):
                nxt = next((x for x in at[v] if x is not cur), None)
                if nxt is None or nxt in used:
                    break
                used.add(nxt)
                if end == 0:
                    chain.insert(0, nxt)
                else:
                    chain.append(nxt)
                cur = nxt
                v = nxt.other_vert(v)
        chains.append(chain)
    return chains


def divide_interior_lines(bm, spacing_mm, comp_edges=None):
    """Give every cut line the vertex count the spacing asks for.

    Done in one pass BEFORE any region is gridded: a line borders two regions
    and there is only one set of vertices to share. Cuts are distributed
    across the line's own segments in proportion to their length, so a long
    segment gets more of them, and no existing vertex is moved or removed.
    """
    target = spacing_mm / 1000.0
    if target <= 0.0:
        return {"chains": 0, "cuts": 0}
    lay = line_layer(bm)          # created before any reference is held
    by_cuts = defaultdict(list)
    n_chains = 0
    for chain in interior_chains(bm, comp_edges):
        n_chains += 1
        lengths = [(e, (e.verts[0].co - e.verts[1].co).length) for e in chain]
        total = sum(l for _e, l in lengths)
        want = max(1, int(round(total / target)))
        have = len(chain)
        if want <= have:
            continue
        extra = want - have
        # proportional share, largest remainder first
        share = [(e, l / total * extra) for e, l in lengths]
        cuts = {e: int(math.floor(x)) for e, x in share}
        left = extra - sum(cuts.values())
        for e, x in sorted(share, key=lambda s: -(s[1] - math.floor(s[1]))):
            if left <= 0:
                break
            cuts[e] += 1
            left -= 1
        for e, c in cuts.items():
            if c > 0:
                by_cuts[c].append(e)
    total_cuts = 0
    for c, edges in sorted(by_cuts.items()):
        res = bmesh.ops.subdivide_edges(bm, edges=edges, cuts=c,
                                        use_grid_fill=False)
        for g in res.get("geom_inner", ()):
            if isinstance(g, bmesh.types.BMVert):
                g[lay] = 1
        total_cuts += c * len(edges)
    return {"chains": n_chains, "cuts": total_cuts}


def undivide_interior_lines(bm, within=None):
    """Take this tool's own cut-line divisions back off. Returns how many went.

    Only vertices flagged by `divide_interior_lines` are touched, and only
    those that are still nothing but a division: a flagged vertex the user has
    since run another cut line into is theirs now, so it is adopted (unflagged
    and kept). Removing one of those destroyed every edge at the junction —
    measured on a synthetic three-line hub, 3 edges down to 0, and on the
    user's own file, 18 of their edges gone after a few runs.

    The join itself is `dissolve_verts`, not a delete: deleting a vertex takes
    its faces with it, which is what made the region count fall on every run.
    """
    lay = line_layer(bm, create=False)
    if lay is None:
        return 0
    ok = (lambda v: True) if within is None else within
    dead = []
    for v in bm.verts:
        if not v[lay] or not ok(v):
            continue
        if len(v.link_edges) != 2:
            v[lay] = 0          # the user built on it; it is not ours any more
            continue
        dead.append(v)
    if dead:
        bmesh.ops.dissolve_verts(bm, verts=dead)
    return len(dead)


# --------------------------------------------------- sides and their counts
def edge_between(a, b):
    for e in a.link_edges:
        if e.other_vert(a) is b:
            return e
    return None


def sides_of_ring(ring, corner_verts):
    """Cut a face's vertex ring at the corners. Returns [list of BMVert]."""
    idx = [i for i, v in enumerate(ring) if v in corner_verts]
    if len(idx) < 2:
        return []
    sides = []
    for k in range(len(idx)):
        a, b = idx[k], idx[(k + 1) % len(idx)]
        sides.append(ring[a:b + 1] if b > a else ring[a:] + ring[:b + 1])
    return sides


def _split_at_junctions(side, comp_edges):
    """A side as the cut lines it is made of: cut at every interior vertex
    where another of the user's cut lines arrives (a pass-through junction,
    see classify_corners). A plain side comes back as one chain."""
    parts, cur = [], [side[0]]
    for v in side[1:]:
        cur.append(v)
        if v is not side[-1] and \
                sum(1 for e in v.link_edges if e in comp_edges) >= 3:
            parts.append(cur)
            cur = [v]
    parts.append(cur)
    return parts


def side_edges(side):
    out = []
    for i in range(len(side) - 1):
        e = edge_between(side[i], side[i + 1])
        if e is not None:
            out.append(e)
    return out


def side_key(edges):
    return frozenset(edges)


def assign_intervals(regions, spacing_mm):
    """Choose how finely every cut line is divided, so grids can be band-free.

    A patch is a pure grid only when its opposite sides carry the same number
    of edges. The outline's count is fixed — a boundary vertex is half of a
    sewn pair — but a CUT is ours and its count is free, so the two constraints
    per region (side0 == side2, side1 == side3) can usually be satisfied by
    choosing the cuts well.

    Opposite sides are therefore merged into equivalence classes, and each
    class takes the count of whatever outline side it contains. A class holding
    two outline sides of different counts cannot be satisfied by any division —
    that is the sewing disagreeing with itself across the panel — and the
    regions on it keep the banded construction instead of being forced.

    Returns (target_by_key, conflicts) where a target of None means unresolved.
    """
    parent = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # The unit of division is the CUT LINE, not the side. A side that runs
    # straight through a junction (a "composite" side, see classify_corners)
    # is two or more cut lines end to end; each of them is a plain side of
    # the region across it and is divided there, or on its own by spacing if
    # no region owns it (a connector into a region that was skipped). The
    # composite side's count is then whatever its lines add up to, so it does
    # not pair with the side opposite — that region takes a band or a strip.
    # Dividing the composite side as one target put the same edge in two
    # plans (subdivide_edges refused) and, once that was blocked, left a
    # 53 mm connector undivided next to 10 mm edges: three sub-15-degree
    # slivers in the band beside it. Measured on the pocket panel.
    info = {}
    for reg in regions:
        keys = reg["side_keys"]
        comp = reg.get("composite") or [False] * len(keys)
        chains = reg.get("chains") or [[(k, e)] for k, e in
                                       zip(keys, reg["side_edges"])]
        for side_chains in chains:
            for k, edges in side_chains:
                if k in info or not edges:
                    continue
                outline = all(len(e.link_faces) == 1 for e in edges)
                length = sum((e.verts[0].co - e.verts[1].co).length
                             for e in edges)
                info[k] = {"outline": outline, "fixed": outline,
                           "count": len(edges), "length": length}
        if len(keys) == 4:
            for a, b in ((0, 2), (1, 3)):
                if not comp[a] and not comp[b]:
                    union(keys[a], keys[b])

    groups = defaultdict(list)
    for k in info:
        groups[find(k)].append(k)

    target = {}
    conflicts = []
    spacing = spacing_mm / 1000.0
    for root, members in groups.items():
        fixed = sorted(set(info[k]["count"] for k in members
                           if info[k]["fixed"]))
        if len(fixed) > 1:
            # The sewing disagrees with itself across this chain of regions, so
            # no division satisfies every one of them. Leaving the cut lines
            # alone was much worse than picking a value: undivided, they came
            # out as one-edge sides against five-edge ones and the core fanned
            # into them — the star bursts in the user's screenshot. The median
            # of the fixed counts minimises the total mismatch.
            conflicts.append({"counts": fixed, "sides": len(members)})
            t = fixed[len(fixed) // 2]
            for k in members:
                target[k] = t
            continue
        if fixed:
            t = fixed[0]
        else:
            avg = sum(info[k]["length"] for k in members) / len(members)
            t = max(1, int(round(avg / spacing))) if spacing > 0 else 1
        for k in members:
            # A cut line already carrying more of the user's own vertices than
            # the target cannot be made coarser: those vertices are the shape
            # of the line. Raise the whole class to fit instead.
            if not info[k]["fixed"]:
                t = max(t, info[k]["count"]) if info[k]["count"] > t else t
        for k in members:
            target[k] = t
    return target, conflicts, info


def divide_to_targets(bm, regions, target, info):
    """Subdivide every cut line to the count the assignment gave it."""
    lay = line_layer(bm)
    plan = defaultdict(list)          # cuts -> [edges]
    done = set()
    for reg in regions:
        chains = reg.get("chains") or [[(k, e)] for k, e in
                                       zip(reg["side_keys"], reg["side_edges"])]
        for k, edges in (c for side in chains for c in side):
            if k in done or not edges:
                continue
            done.add(k)
            t = target.get(k)
            if t is None or info[k]["fixed"]:
                continue
            have = len(edges)
            if t <= have:
                continue
            extra = t - have
            lens = [(e, (e.verts[0].co - e.verts[1].co).length) for e in edges]
            total = sum(l for _e, l in lens) or 1.0
            share = [(e, l / total * extra) for e, l in lens]
            cuts = {e: int(math.floor(x)) for e, x in share}
            left = extra - sum(cuts.values())
            for e, x in sorted(share, key=lambda p: -(p[1] - math.floor(p[1]))):
                if left <= 0:
                    break
                cuts[e] += 1
                left -= 1
            for e, c in cuts.items():
                if c > 0:
                    plan[c].append(e)
    n = 0
    for c, edges in sorted(plan.items()):
        res = bmesh.ops.subdivide_edges(bm, edges=edges, cuts=c,
                                        use_grid_fill=False)
        for g in res.get("geom_inner", ()):
            if isinstance(g, bmesh.types.BMVert):
                g[lay] = 1
        n += c * len(edges)
    return n


# ------------------------------------------------------------------ gridding
def collect_regions(bm, slay, corner_flags, mat, target_mm, concave_deg,
                    convex_deg, depth_window_factor, depth_min_factor,
                    elay=None, kink_deg=45.0, within=None):
    """Every scaffold region with its four sides. Returns (regions, skipped,
    auto).

    Run twice per grid: once before the cut lines are divided, to work out what
    count each of them needs, and once after, to build on the divided geometry.
    Dividing only ever adds valence-two vertices, so the corners it finds are
    the same both times.

    Each skipped entry is (face, reason): the caller selects the face so a
    Tab into Edit Mode shows exactly which n-gon needs another cut, instead
    of the reason being reachable only via the system console.
    """
    ours = set(e for e in bm.edges if elay is not None and e[elay])
    comp_of, edges_of = panel_components(bm, ours=ours)
    regions, skipped, auto = [], [], []
    ok = (lambda v: True) if within is None else within
    for face in [f for f in bm.faces if f[slay] and ok(f.verts[0])]:
        ring = [l.vert for l in face.loops]
        centre = Vector((0.0, 0.0))
        for v in ring:
            w = mat @ v.co
            centre += Vector((w.x, w.y))
        centre /= max(1, len(ring))
        tag = "region at (%.0f, %.0f) mm, %dv" % (centre.x * 1000,
                                                  centre.y * 1000, len(ring))
        if len(ring) < 4:
            skipped.append((face, "%s: only %d vertices — too small to grid"
                            % (tag, len(ring))))
            continue
        comp_edges = edges_of.get(comp_of.get(face.index), set())
        info = classify_corners(
            face, comp_edges, corner_flags,
            concave_deg=concave_deg, convex_deg=convex_deg,
            depth_window=target_mm / 1000.0 * depth_window_factor,
            depth_min=target_mm / 1000.0 * depth_min_factor)
        picked, note = choose_corners(info)
        if picked is None:
            skipped.append((face, "%s: %s" % (tag, note)))
            continue
        corner_verts = set(ring[i] for i in picked)
        for i in note["auto"]:
            p = mat @ ring[i].co
            auto.append("(%.1f, %.1f) mm  turn %.0f deg"
                        % (p.x * 1000, p.y * 1000, info["turns"][i][0]))
        sides = sides_of_ring(ring, corner_verts)
        if len(sides) != 4:
            skipped.append((face, "%s: %d sides after cutting at the corners"
                            % (tag, len(sides))))
            continue
        se = [side_edges(sd) for sd in sides]
        # A side that runs straight THROUGH a junction (see classify_corners,
        # pass_deg) is the concatenation of two or more cut lines, each of
        # them a plain side of some other region. Its count is whatever those
        # lines add up to, never a target of its own — dividing it as a whole
        # would put the same edge in two plans (measured: subdivide_edges
        # refusing "the same BMEdge used multiple times" on a pocket panel).
        chains, composite = [], []
        for sd in sides:
            parts = _split_at_junctions(sd, comp_edges)
            composite.append(len(parts) > 1)
            chains.append([(side_key(side_edges(c)), side_edges(c))
                           for c in parts])
        # A convex turn left in the middle of a side is legal — the map
        # follows the side's curve — but it is not free: the single interior
        # edge leaving that vertex splits its turn between two quads, so a
        # 67-degree shoulder point mid-side comes out as two ~57-degree cells
        # and every row inherits the kink. Measured on a production 7x7
        # region: swapping the interior technique (Coons / Laplace / Winslow
        # / arc-length TFI) moved the worst angle by under 2 degrees. The
        # cure is a cut from that point, so say where it is.
        kinks = []
        for i in info["convex"]:
            if i in picked:
                continue
            t = info["turns"][i][0]
            if t >= kink_deg:
                p = mat @ ring[i].co
                kinks.append((p.x * 1000, p.y * 1000, t, ring[i]))
        regions.append({
            "face": face, "tag": tag, "corners": corner_verts, "ring": ring,
            "sides": sides, "side_edges": se,
            "side_keys": [side_key(x) for x in se], "note": note,
            "kinks": kinks, "composite": composite, "chains": chains,
        })
    return regions, skipped, auto


def _face_self_intersects(face, mat):
    """True if any two non-adjacent edges of face's boundary cross in world
    XY. The band construction (grid_one_patch) can fold this way on a
    small/irregular region — the property's own description already
    documented "the band crosses itself" as a measured failure mode, but
    left it undetected. Measured on a production file: 1 of 1336 faces
    after enabling band fallback (0.07%) — real, but rare enough to flag
    rather than to keep band fallback off by default for.
    """
    pts = [mat @ v.co for v in face.verts]
    n = len(pts)
    if n < 4:
        return False

    def ccw(p, q, r):
        return (r.y - p.y) * (q.x - p.x) - (q.y - p.y) * (r.x - p.x)

    def segs_cross(a, b, c, d):
        d1, d2 = ccw(c, d, a), ccw(c, d, b)
        d3, d4 = ccw(a, b, c), ccw(a, b, d)
        return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))

    edges = [(pts[i], pts[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue  # adjacent, sharing a vertex through the wrap-around
            if segs_cross(*edges[i], *edges[j]):
                return True
    return False


def run_grid_regions(retopo, target_mm, smooth_passes=6, concave_deg=20.0,
                     convex_deg=30.0, divide_lines=True,
                     depth_window_factor=1.0, depth_min_factor=0.5,
                     cells_from='COUNTS', matched=True, band=True,
                     selected_only=False):
    """Grid every region. Returns a report.

    The order is the whole design:

        clear ours -> rebuild the scaffold -> read the corners and sides
          -> assign every cut line one count, so opposite sides match
          -> DROP THE SCAFFOLD -> divide the cut lines -> rebuild the scaffold
          -> grid each region

    The scaffold has to come off before the division: `subdivide_edges` re-cuts
    any face whose edges it touches, and dividing with the n-gons in place
    shattered them into four-vertex quads and grew the region count on every
    run (measured on the user's file: 49 regions, then 69).

    A region whose opposite sides match is gridded with no band at all — every
    row runs from a real boundary vertex through to its partner opposite — and
    comes out pure quads. A region where only the short sides match is
    laddered (grid_strip_patch). The rest keep the core-and-band construction.

    `selected_only` limits the run to the islands holding a selection (Edit
    Mode); everything else on the object is left exactly as it is.
    """
    me = retopo.data
    mat = retopo.matrix_world
    bm = bmesh.new()
    bm.from_mesh(me)
    report = {"regions": 0, "gridded": 0, "quads": 0, "tris": 0, "verts": 0,
              "skipped": [], "per_region": [], "auto_corners": [],
              "matched": 0, "banded": 0, "strips": 0, "self_intersecting": [],
              "kinks": [], "scope_verts": 0}
    report["scope_verts"] = mark_scope(bm, selected_only)
    within = scope_test(bm)
    if report["scope_verts"] == 0:
        unmark_scope(bm)
        bm.free()
        return report
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    # Cleared up front so only THIS run's failed regions end up selected —
    # a stale selection from before the run would otherwise mix in and the
    # "where" signal (Tab into Edit Mode to see it) would lie.
    for v in bm.verts:
        if within(v):
            v.select = False
    for e in bm.edges:
        if within(e.verts[0]):
            e.select = False
    for f in bm.faces:
        if within(f.verts[0]):
            f.select = False

    # 0. start from the user's edges every time
    cleared = pg.clear_grid(bm, within=within)
    bm.verts.ensure_lookup_table()
    cleared_scaffold = clear_scaffold(bm, within=within)
    bm.verts.ensure_lookup_table()
    report["cleared"] = {"faces": cleared[0], "verts": cleared[1],
                         "scaffold": cleared_scaffold}
    report["undivided"] = undivide_interior_lines(bm, within=within)

    def refresh():
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.verts.index_update()
        bm.edges.index_update()
        bm.faces.index_update()

    refresh()
    fill_regions(bm, mat, within=within)
    refresh()

    slay = scaffold_layer(bm)
    vlay = pg._vert_layer(bm)
    flay = pg._face_layer(bm)
    elay = pg._edge_layer(bm)
    clay = tc.layer(bm, create=False)
    within = scope_test(bm)           # layers were added: fetch afresh
    corner_flags = set() if clay is None else set(
        v.index for v in bm.verts if v[clay])
    inv = mat.inverted()

    args = (slay, corner_flags, mat, target_mm, concave_deg, convex_deg,
            depth_window_factor, depth_min_factor)

    # ------------------------------------ 1. what count does each cut line need
    regions, skipped, auto = collect_regions(bm, *args, elay=elay,
                                             within=within)
    target, conflicts, sinfo = assign_intervals(regions, target_mm)
    report["conflicts"] = conflicts

    # ------------------------- 2. divide on bare wire, then rebuild the scaffold
    if divide_lines:
        clear_scaffold(bm, within=within)
        refresh()
        report["divided"] = divide_to_targets(bm, regions, target, sinfo)
        refresh()
        within = scope_test(bm)       # divide may have added a layer
        fill_regions(bm, mat, within=within)
        refresh()
        regions, skipped, auto = collect_regions(bm, *args, elay=elay,
                                                 within=within)

    report["regions"] = len(regions) + len(skipped)
    for face, reason in skipped:
        face.select = True
        report["skipped"].append(reason)
    report["auto_corners"].extend(auto)

    # ------------------------------------------------- 3. grid, region by region
    pre_edges = set(frozenset((e.verts[0], e.verts[1])) for e in bm.edges)
    new_faces, dead_faces = [], []

    def snapshot(sides):
        """Index -> position / vertex for the patch builders, read afresh.

        Indices do not survive this loop: a failed attempt deletes the
        vertices it made, the next bmesh op renumbers every vertex in storage
        order, and the freed slots are reused by the next region's vertices —
        so an original vertex can come out with a HIGHER index than the table
        built before the loop knew of (measured: KeyError on a ring vertex,
        four regions after a rollback, on a pocket panel). One table per
        attempt costs a millisecond and cannot go stale.
        """
        bm.verts.index_update()
        Wd = {v.index: mat @ v.co for v in bm.verts}
        return (Wd, {v.index: v for v in bm.verts},
                [[v.index for v in sd] for sd in sides])

    for reg in regions:
        sides = reg["sides"]
        ring = reg["ring"]
        W, vref, sides_idx = snapshot(sides)
        poly = pg.build_region_polygon([(W[v.index].x, W[v.index].y) for v in ring])

        counts = [len(sd) - 1 for sd in sides]
        rep, faces = None, []
        if matched and counts[0] == counts[2] and counts[1] == counts[3]:
            rep, faces = pg.grid_matched_patch(bm, sides_idx, W, vref, inv,
                                               vlay, poly=poly,
                                               smooth_passes=smooth_passes)
            if rep["status"] != "ok":
                faces = []
                W, vref, sides_idx = snapshot(sides)
        if not faces and band and rep is None:
            # Short sides match, long sides do not: straight rungs from each
            # boundary vertex to its partner, a triangle per unpaired one.
            rep_s, faces = pg.grid_strip_patch(bm, sides_idx, W, vref, inv,
                                               vlay, poly=poly)
            if rep_s["status"] == "ok":
                rep = rep_s
            elif rep_s["status"] != "not a strip":
                rep = rep_s          # a real failure; the band may still do it
            if not faces:
                W, vref, sides_idx = snapshot(sides)
        if not faces and band:
            rep2, faces = pg.grid_one_patch(bm, sides_idx, W, vref, inv,
                                            target_mm, vlay, poly=poly,
                                            smooth_passes=smooth_passes,
                                            cells_from=cells_from)
            if rep is not None and rep["status"] != "ok":
                rep2["matched_failed"] = rep["status"]
            rep = rep2
        elif not faces and rep is None:
            # Band disabled: say what would make this region griddable rather
            # than filling it with a construction that crosses itself.
            rep = {"status": ("opposite sides carry %d vs %d and %d vs %d — "
                              "adjust the cut or the boundary density"
                              % (counts[0], counts[2], counts[1], counts[3])),
                   "quads": 0, "tris": 0, "verts": 0, "kind": "refused",
                   "sides": counts}
        rep["tag"] = reg["tag"]
        rep["counts"] = counts
        rep["corner_kinds"] = {"mand": len(reg["note"]["mandatory"]),
                               "mark": len(reg["note"]["marked"]),
                               "auto": len(reg["note"]["auto"]),
                               "relax": len(reg["note"]["relaxed"])}
        report["per_region"].append(rep)
        # Reported for refused regions too: the region most in need of a cut
        # is the one the count mismatch just refused, and a cut from the kink
        # is what tends to fix both. The vertex is selected so it can be
        # found in Edit Mode without reading coordinates off the log.
        for x, y, t, kv in reg.get("kinks", ()):
            if kv.is_valid:
                kv.select = True
            report["kinks"].append(
                "%s: a %.0f-degree convex turn at (%.0f, %.0f) mm sits in the "
                "middle of a side — its two cells come out at ~%.0f degrees "
                "and every row inherits the kink; a cut from that point would "
                "straighten them" % (reg["tag"], t, x, y, (180.0 - t) / 2.0))
        if rep["status"] != "ok":
            reg["face"].select = True
            report["skipped"].append("%s: %s" % (reg["tag"], rep["status"]))
            continue
        made_q = made_t = 0
        region_faces = []
        for vs in faces:
            if len(set(vs)) != len(vs):
                continue
            try:
                f = bm.faces.new(vs)
            except ValueError:
                continue
            f[flay] = 1
            new_faces.append(f)
            region_faces.append(f)
            if len(vs) == 4:
                made_q += 1
            else:
                # The band absorbs a count mismatch with one row of
                # triangles; selecting them says where that row landed.
                made_t += 1
                f.select = True
        rep["quads"], rep["tris"] = made_q, made_t

        # grid_one_patch returns kind "core" or "ladder" (never "banded" —
        # that string never matched anything here, so this check silently
        # never ran; caught only because a human looked at the actual
        # result, not because any automated check flagged it).
        # grid_matched_patch returns kind "matched".
        if rep.get("kind") in ("core", "ladder", "strip"):
            bad = [f for f in region_faces if _face_self_intersects(f, mat)]
            if bad:
                for f in bad:
                    f.select = True
                report["self_intersecting"].append(
                    "%s: %d of %d band face(s) cross themselves"
                    % (reg["tag"], len(bad), len(region_faces)))
        report["quads"] += made_q
        report["tris"] += made_t
        report["verts"] += rep.get("verts", 0)
        report["gridded"] += 1
        if rep.get("kind") == "matched":
            report["matched"] += 1
        elif rep.get("kind") == "strip":
            report["strips"] += 1
        else:
            report["banded"] += 1
        dead_faces.append(reg["face"])

    dead = [f for f in dead_faces if f.is_valid]
    if dead:
        bmesh.ops.delete(bm, geom=dead, context='FACES_ONLY')
    for e in bm.edges:
        if frozenset((e.verts[0], e.verts[1])) not in pre_edges:
            e[elay] = 1
    live = [f for f in new_faces if f.is_valid]
    if live:
        fo.orient_faces_up(live, mat)
    # Open lines (a pocket outline whose ends were never connected) take no
    # part in any region and the grid runs straight over them. Count them so
    # the operator can say "press Connect" instead of leaving the user to
    # notice a pocket that is not there.
    report["loose_ends"] = sum(
        1 for v in bm.verts
        if within(v) and len(v.link_edges) == 1 and not v.link_faces
        and loose_chain_mm(v, mat) >= MIN_CHAIN_MM)
    flush_selection_down(bm)
    unmark_scope(bm)

    bm.to_mesh(me)
    bm.free()
    me.update()
    return report


def run_fill_regions(retopo, selected_only=False):
    me = retopo.data
    bm = bmesh.new()
    bm.from_mesh(me)
    n_scope = mark_scope(bm, selected_only)
    bm.verts.ensure_lookup_table()
    if n_scope == 0:
        unmark_scope(bm)
        bm.free()
        return {"faces": 0, "sizes": [], "open_edges": 0, "pinched": 0,
                "occupied": 0, "scope_verts": 0}
    rep = fill_regions(bm, retopo.matrix_world, within=scope_test(bm))
    rep["scope_verts"] = n_scope
    unmark_scope(bm)
    bm.to_mesh(me)
    bm.free()
    me.update()
    return rep


def run_clear_regions(retopo, selected_only=False):
    """Take the scaffold n-gons and any grid back off, keeping every edge."""
    me = retopo.data
    bm = bmesh.new()
    bm.from_mesh(me)
    n_scope = mark_scope(bm, selected_only)
    within = scope_test(bm)
    bm.verts.ensure_lookup_table()
    n_grid_f, n_grid_v = pg.clear_grid(bm, within=within)
    bm.verts.ensure_lookup_table()
    n_scaffold = clear_scaffold(bm, within=within)
    bm.verts.ensure_lookup_table()
    n_line = undivide_interior_lines(bm, within=within)
    unmark_scope(bm)
    bm.to_mesh(me)
    bm.free()
    me.update()
    return {"grid_faces": n_grid_f, "grid_verts": n_grid_v,
            "scaffold_faces": n_scaffold, "line_verts": n_line,
            "scope_verts": n_scope}


# ------------------------------------------------------------------ loose ends
MIN_CHAIN_MM = 5.0   # an open chain shorter than this is a Knife overshoot, not a cut


def loose_chain_verts(v):
    """The vertices of the open chain from loose end `v` up to (excluding)
    its first node — the vertex with other than two edges. A chain that ends
    in another loose end is loose altogether, so that end is included too.
    Used to remove Knife-overshoot stubs whole, never leaving a loose vertex.
    """
    verts = []
    prev, cur = None, v
    while True:
        nxt = [e for e in cur.link_edges if e.other_vert(cur) is not prev]
        if len(cur.link_edges) > 2 or (prev is not None and len(cur.link_edges) != 2):
            return verts
        verts.append(cur)
        if not nxt:
            return verts
        prev, cur = cur, nxt[0].other_vert(cur)


def loose_chain_mm(v, mat):
    """Length in mm of the open chain from loose end `v` back to its first node
    (a vertex with other than two edges), capped at 10 m."""
    def xy(w):
        c = mat @ w.co
        return Vector((c.x, c.y))
    total = 0.0
    prev, cur = None, v
    while True:
        nxt = [e for e in cur.link_edges if e.other_vert(cur) is not prev]
        if len(cur.link_edges) > 2 or not nxt or (prev is not None and len(cur.link_edges) != 2):
            return total * 1000.0
        e = nxt[0]
        total += (xy(cur) - xy(e.other_vert(cur))).length
        prev, cur = cur, e.other_vert(cur)
        if total > 10.0:
            return total * 1000.0


def _faces_xy(bm, mat, ok):
    out = []
    for f in bm.faces:
        if not ok(f.verts[0]):
            continue
        pts = [((mat @ v.co).x, (mat @ v.co).y) for v in f.verts]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        out.append((min(xs), min(ys), max(xs), max(ys), pts))
    return out


def _inside_any(pt, faces_xy):
    for x0, y0, x1, y1, pts in faces_xy:
        if x0 <= pt.x <= x1 and y0 <= pt.y <= y1 and pg._point_in_ring(pt.x, pt.y, pts):
            return True
    return False


def _clear_vert_tags(bm, v):
    """A vertex this tool made is a plain cut vertex: no pin, no corner flag,
    no line-division tag (edge_split interpolates every int layer onto it)."""
    scope_lay = bm.verts.layers.int.get(SCOPE_LAYER)
    for lay in bm.verts.layers.int.values():
        v[lay] = 0
    if scope_lay is not None:
        v[scope_lay] = 1


def planarize_wires(bm, mat, ok=None, snap_mm=2.0):
    """Give every crossing of an open chain with another edge a vertex.

    The Knife cuts through the cuts it is drawn across — but only where a
    face is under it. A line drawn while its region had no face (a pocket
    outline over an unfilled panel) crosses existing cuts with nothing at the
    crossing, and a wire graph with an unmarked crossing is not planar: the
    face walk returns to a vertex it has seen and the region is dropped.
    Measured: a flap seam drawn across a vertical cut lost both regions
    beside it, silently, once its ends were connected.

    Each crossing splits both edges and welds the two new vertices (an end of
    the chain that lies ON another edge becomes a T there instead). Returns
    the crossing points in mm. Run before the ends are connected.
    """
    ok = (lambda v: True) if ok is None else ok
    snap = snap_mm / 1000.0

    def xy(v):
        c = mat @ v.co
        return Vector((c.x, c.y))

    out = []
    guard = 0
    while guard < 500:
        guard += 1
        wires = [e for e in bm.edges if not e.link_faces and ok(e.verts[0])]
        others = [e for e in bm.edges if ok(e.verts[0])]
        found = None
        for w in wires:
            a, b = xy(w.verts[0]), xy(w.verts[1])
            for e in others:
                if e is w or set(e.verts) & set(w.verts):
                    continue
                c, d = xy(e.verts[0]), xy(e.verts[1])
                hit = intersect_line_line_2d(a, b, c, d)
                if hit is None:
                    continue
                found = (w, e, hit, a, b, c, d)
                break
            if found:
                break
        if not found:
            break
        w, e, hit, a, b, c, d = found
        # the vertex on the chain: an end within snap, else a split
        dw = [(hit - a).length, (hit - b).length]
        if min(dw) < snap:
            vw = w.verts[0] if dw[0] < dw[1] else w.verts[1]
        else:
            _ne, vw = bmesh.utils.edge_split(w, w.verts[0], dw[0] / max(1e-12, (b - a).length))
            _clear_vert_tags(bm, vw)
        # the vertex on the other edge likewise
        de = [(hit - c).length, (hit - d).length]
        if min(de) < snap:
            ve = e.verts[0] if de[0] < de[1] else e.verts[1]
        else:
            _ne, ve = bmesh.utils.edge_split(e, e.verts[0], de[0] / max(1e-12, (d - c).length))
            _clear_vert_tags(bm, ve)
        if vw is ve:
            continue
        co = ve.co.copy()
        bmesh.ops.pointmerge(bm, verts=[vw, ve], merge_co=co)
        out.append((hit.x * 1000.0, hit.y * 1000.0))
    return out


def connect_loose_ends(bm, mat, sources=None, snap_mm=2.0, within=None,
                       min_chain_mm=MIN_CHAIN_MM):
    """Extend a cut that stopped short until it meets something, and join it.

    A pocket outline is drawn as an open U — left side, bottom, right side —
    and the flap's seam as a short line above it. Neither closes a region, so
    the face walk prunes them and the grid ignores the pocket altogether
    (measured: eight loose ends on a production file, the pocket gridded over
    as if it were not there). The user could cut from each end to the outline
    by hand; this does that cut: from every loose end, a ray along the chain's
    last segment, and an edge from the end to the first thing the ray meets.
    The ray, not the nearest point: the nearest point is often sideways, and a
    slanted connector shears every row of the grid it borders. The ray is the
    line a hand would cut.

    ORDER MATTERS. Each end is resolved against the edges that exist at that
    moment, shortest ray first, and the connectors already made are targets
    for the ones still pending. Measured on the production pocket: the U's
    sides stop 3 mm short of the flap line's ends, so resolved independently
    they miss it and run 100 mm to the far outline; resolved after the flap
    line has been extended, they meet its connector 13 mm away, exactly where
    a hand would have joined them.

    `sources`: None = every loose end in scope (degree one, no faces). Or an
    explicit list of vertices to extend from — a loose end gets its one ray,
    a degree-two vertex (a pocket's bottom corner) gets one along each of its
    edges. A ray is only fired if it starts inside a region (the scaffold
    faces must be present: run fill_regions first), so a corner of the outline
    never shoots out of the panel.

    A loose chain shorter than `min_chain_mm` end to end is a stub — a Knife
    stroke that overshot by a millimetre — not a cut that stopped short.
    Measured: six 1 mm stubs on a collar, each of which would otherwise have
    been run 60 mm across the panel. They are reported, not connected.

    A hit within `snap_mm` of an edge's end reuses that vertex; otherwise the
    edge is split there. Splitting interpolates every integer attribute onto
    the new vertex (a pin, a corner flag, a line-division tag), so they are
    cleared — the vertex is a plain cut vertex.

    The OUTLINE is never split. A boundary vertex is half of a sewn pair, and
    a new one would need its partner made on the other panel (Sync Selected
    Vertex) before the seam agrees again — one more step for a connector the
    user will most likely cut over by hand anyway. So a ray that reaches the
    outline joins the nearer of the two existing outline vertices instead:
    the connector leans by up to half a boundary interval, and the seam is
    left exactly as Generate made it.
    """
    ok = (lambda v: True) if within is None else within
    snap = snap_mm / 1000.0
    report = {"rays": 0, "connected": [], "split": 0, "reused": 0,
              "outline": [], "unresolved": [], "targets": [], "stubs": []}

    def xy(v):
        c = mat @ v.co
        return Vector((c.x, c.y))

    faces_xy = _faces_xy(bm, mat, ok)

    rays = []     # (vert, other_vert): the ray runs other -> vert and on
    if sources is None:
        for v in bm.verts:
            if ok(v) and len(v.link_edges) == 1 and not v.link_faces:
                if loose_chain_mm(v, mat) < min_chain_mm:
                    report["stubs"].append(v)
                    continue
                rays.append((v, v.link_edges[0].other_vert(v)))
    else:
        for v in sources:
            if not ok(v):
                continue
            if len(v.link_edges) not in (1, 2):
                report["unresolved"].append(
                    (v, "meets %d edges already" % len(v.link_edges)))
            elif all(len(e.link_faces) == 1 for e in v.link_edges):
                # an outline vertex: its edges each carry one face. Extending
                # the outline's own direction never makes a cut, and a whole
                # selected island must not become a hundred rays.
                report["unresolved"].append((v, "is on the outline"))
            else:
                for e in v.link_edges:
                    rays.append((v, e.other_vert(v)))
    # a ray has to start inside a region, or it is leaving the panel
    live = []
    for v, o in rays:
        d = xy(v) - xy(o)
        if d.length < 1e-9:
            continue
        d.normalize()
        if faces_xy and not _inside_any(xy(v) + d * 1e-4, faces_xy):
            report["unresolved"].append((v, "its continuation leaves the panel"))
            continue
        live.append((v, o))
    report["rays"] = len(live)

    def first_hit(v, o):
        origin = xy(v)
        d = (origin - xy(o)).normalized()
        far = origin + d * 100.0
        best = None
        for e in bm.edges:
            if not ok(e.verts[0]) or v in e.verts:
                continue
            a, b = xy(e.verts[0]), xy(e.verts[1])
            hit = intersect_line_line_2d(origin, far, a, b)
            if hit is None:
                continue
            t = (hit - origin).dot(d)
            if t <= 1e-6:
                continue
            if best is None or t < best[0]:
                best = (t, e, hit)
        return best

    pending = list(live)
    while pending:
        best = None
        for ray in pending:
            r = first_hit(*ray)
            if r is not None and (best is None or r[0] < best[1][0]):
                best = (ray, r)
        if best is None:
            for v, _o in pending:
                report["unresolved"].append((v, "nothing in its way"))
            break
        (v, o), (t, e, hit) = best
        pending.remove((v, o))
        a, b = e.verts[0], e.verts[1]
        kind = len(e.link_faces)      # 0 open chain, 1 outline, 2 cut line
        da = (hit - xy(a)).length
        db = (hit - xy(b)).length
        if min(da, db) < snap or kind == 1:
            tgt = a if da < db else b
            how = "reused"
        else:
            fac = da / max(1e-12, (xy(b) - xy(a)).length)
            _ne, tgt = bmesh.utils.edge_split(e, a, fac)
            _clear_vert_tags(bm, tgt)
            how = "split"
        if tgt is v or tgt is o or bm.edges.get((v, tgt)) is not None:
            report["unresolved"].append((v, "would fold back on its own chain"))
            continue
        bm.edges.new((v, tgt))
        report[how] += 1
        report["connected"].append((v, tgt, kind, how, t * 1000.0))
        report["targets"].append(tgt)
        if kind == 1:
            report["outline"].append(tgt)

    # Stubs are Knife overshoots (measured: six 1 mm stubs on a collar). They
    # used to be reported for the user to hunt down by hand; now they go, whole
    # chain, so no 1 mm wire edge or loose vertex is left behind. The report
    # keeps their positions (mm) so the message can still say where they were.
    dead, seen = [], set()
    for v in report["stubs"]:
        for w in loose_chain_verts(v):
            if id(w) not in seen:
                seen.add(id(w))
                dead.append(w)
    report["stubs"] = [(xy(v).x * 1000.0, xy(v).y * 1000.0) for v in report["stubs"]]
    report["stubs_deleted"] = len(dead)
    if dead:
        bmesh.ops.delete(bm, geom=dead, context='VERTS')
    return report


def run_connect_loose_ends(retopo, selected_only=False, extend_selected=False,
                           snap_mm=2.0):
    """Connect loose ends (or extend the selected vertices) on the retopo.

    Same step 0 as Grid Regions — the grid, the scaffold and the line
    divisions come off first, so the rays see the user's own geometry and the
    connectors land on it, not on a division that the next Clear would take
    away. The scaffold is then rebuilt (the rays need it to know what is
    inside), the ends connected, and the scaffold rebuilt once more so the
    new regions have faces for the Knife and for Grid.
    """
    me = retopo.data
    mat = retopo.matrix_world
    bm = bmesh.new()
    bm.from_mesh(me)
    n_scope = mark_scope(bm, selected_only)
    within = scope_test(bm)
    bm.verts.ensure_lookup_table()
    if n_scope == 0:
        unmark_scope(bm)
        bm.free()
        return {"scope_verts": 0, "rays": 0, "connected": [], "split": 0,
                "reused": 0, "outline": [], "unresolved": [], "faces": 0,
                "open_edges": 0, "pinched": 0, "crossings": [], "stubs": []}
    sources = None
    if extend_selected:
        sources = [v for v in bm.verts if v.select and within(v)]
    pg.clear_grid(bm, within=within)
    bm.verts.ensure_lookup_table()
    clear_scaffold(bm, within=within)
    bm.verts.ensure_lookup_table()
    undivide_interior_lines(bm, within=within)
    bm.verts.ensure_lookup_table()
    crossings = planarize_wires(bm, mat, ok=within, snap_mm=snap_mm)
    bm.verts.ensure_lookup_table()
    if sources is not None:
        sources = [v for v in sources if v.is_valid]
    fill_regions(bm, mat, within=within)
    rep = connect_loose_ends(bm, mat, sources=sources, snap_mm=snap_mm,
                             within=within)
    clear_scaffold(bm, within=within)
    bm.verts.ensure_lookup_table()
    fill = fill_regions(bm, mat, within=within)
    # leave the join points selected — and only them
    for v in bm.verts:
        if within(v):
            v.select = False
    for e in bm.edges:
        if within(e.verts[0]):
            e.select = False
    for f in bm.faces:
        if within(f.verts[0]):
            f.select = False
    for v in rep["targets"]:
        if v.is_valid:
            v.select = True

    def mm(v):
        c = mat @ v.co
        return (round(c.x * 1000.0, 1), round(c.y * 1000.0, 1))

    out = {
        "scope_verts": n_scope,
        "rays": rep["rays"],
        "connected": [(kind, how, mmlen)
                      for _v, _t, kind, how, mmlen in rep["connected"]],
        "split": rep["split"],
        "reused": rep["reused"],
        "outline": [mm(v) for v in rep["outline"] if v.is_valid],
        "unresolved": [(mm(v), why) for v, why in rep["unresolved"]
                       if v.is_valid],
        "faces": fill["faces"],
        "open_edges": fill["open_edges"],
        "pinched": fill["pinched"],
        "crossings": crossings,
        "stubs": list(rep["stubs"]),
        "stubs_deleted": rep.get("stubs_deleted", 0),
    }
    unmark_scope(bm)
    bm.to_mesh(me)
    bm.free()
    me.update()
    return out
