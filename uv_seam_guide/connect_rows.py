"""Connect Rows — cut a rung through the faces between two cut lines.

Two cut lines run down a panel with faces (n-gons, or whatever the last Fill
left) between them, and the region between the lines needs dividing into a
row of quads. Doing it by hand is: select a vertex on the left line and its
partner on the right, press J, repeat — `mesh.vert_connect_path` is the only
thing that cuts a straight path through a face without caring how many
vertices that face has. This module does the bookkeeping around that loop.

WHY THE LOOP IS WRITTEN THE WAY IT IS
-------------------------------------
`vert_connect_path` is an operator, so it re-tessellates and re-allocates:
every BMVert / BMEdge reference held across one J call is dangling
afterwards. The rungs are therefore planned as COORDINATE pairs, and each
pass of the loop re-acquires the edit bmesh and re-finds its two vertices by
position. The operator also reads the selection and the select history, so
both are cleared and rebuilt for every single rung.

UNEQUAL VERTEX COUNTS
---------------------
The two lines rarely carry the same number of vertices. The shorter line is
the one that gets the missing vertices: each vertex of the longer line has a
normalised arc-length parameter t in [0, 1], and the shorter line's existing
vertices keep their own t, so the only choice is WHICH of the longer line's
parameters to copy over. Greedy farthest-first — repeatedly take the t whose
distance to the nearest already-used parameter is largest — spreads the new
vertices into the gaps instead of bunching them at one end, and it leaves the
user's own cut vertices exactly where they are.

The outline is never the line that gets split: its vertices are paired with
the sewn partner side in 3D, and adding one here would break that pairing
(that is Adjust Density's job, which edits both sides of a seam together).
"""

import bmesh

# Distance under which two vertices count as the same one when re-finding them
# by position after a J call (metres, so 1 micron).
POSITION_EPSILON = 1e-6

# Written by the CLO Projector's Sync 2D>3D on every boundary vertex; see
# clo_projector/attachment.py ATTR_IS_BOUNDARY. Read only, by name, so this
# module does not pull the projector in.
BOUNDARY_LAYER = "ac9_is_boundary"

# A planned insertion this close to an existing vertex would make a
# zero-length edge — refuse the run instead.
MIN_SPLIT_FAC = 1e-6


class ChainError(RuntimeError):
    """The selected edges are not two plain open lines."""


# ------------------------------------------------------------------ selection
def edge_components(edges):
    """Split `edges` into connected components (lists of BMEdge)."""
    edge_set = set(edges)
    remaining = set(edges)
    components = []
    while remaining:
        start = remaining.pop()
        component = {start}
        stack = [start]
        while stack:
            edge = stack.pop()
            for vert in edge.verts:
                for linked in vert.link_edges:
                    if linked in edge_set and linked not in component:
                        component.add(linked)
                        remaining.discard(linked)
                        stack.append(linked)
        components.append(component)
    return components


def ordered_chain(edges):
    """Return the component's vertices walked end to end.

    Raises ChainError when the component branches, is a closed loop, or
    cannot be walked in one pass.
    """
    verts = set()
    for edge in edges:
        verts.update(edge.verts)
    adjacency = {v: [] for v in verts}
    for edge in edges:
        a, b = edge.verts
        adjacency[a].append(b)
        adjacency[b].append(a)
    for neighbors in adjacency.values():
        if len(neighbors) > 2:
            raise ChainError(
                "A selected line branches. Select two plain lines, "
                "each running from one end to the other.")
    endpoints = [v for v, n in adjacency.items() if len(n) == 1]
    if len(endpoints) != 2:
        raise ChainError(
            "Each line must be open, with two ends. A closed loop cannot be "
            "matched up with another line.")
    result = []
    previous = None
    current = endpoints[0]
    while True:
        result.append(current)
        candidates = [v for v in adjacency[current] if v is not previous]
        if not candidates:
            break
        previous = current
        current = candidates[0]
    if len(result) != len(verts):
        raise ChainError("A selected line could not be followed to its end.")
    return result


def find_vertex_by_position(bm, target, eps=POSITION_EPSILON):
    """The vertex at `target`, or None. Used to survive a J call."""
    best_vert = None
    best_distance = float("inf")
    for vert in bm.verts:
        d = (vert.co - target).length_squared
        if d < best_distance:
            best_distance = d
            best_vert = vert
    if best_vert is None or best_distance > eps ** 2:
        return None
    return best_vert


# -------------------------------------------------------------- orientation
def chain_distance(coords_a, coords_b):
    """Summed squared distance of two equal-length coordinate lists."""
    return sum((a - b).length_squared for a, b in zip(coords_a, coords_b))


def arc_params(coords):
    """Normalised cumulative arc length of a polyline: [0.0, ..., 1.0].

    A line whose vertices all sit on one point (never a real cut, but the
    division below must not blow up on it) comes back evenly spaced.
    """
    n = len(coords)
    if n < 2:
        return [0.0] * n
    cumulative = [0.0]
    for a, b in zip(coords, coords[1:]):
        cumulative.append(cumulative[-1] + (b - a).length)
    total = cumulative[-1]
    if total <= 0.0:
        return [i / (n - 1) for i in range(n)]
    return [c / total for c in cumulative]


def resample(coords, params, samples):
    """`samples` points spread evenly along the polyline by parameter."""
    out = []
    for i in range(samples):
        t = i / (samples - 1) if samples > 1 else 0.0
        j = 0
        while j < len(params) - 2 and params[j + 1] < t:
            j += 1
        span = params[j + 1] - params[j]
        local = 0.0 if span <= 0.0 else (t - params[j]) / span
        out.append(coords[j].lerp(coords[j + 1], min(1.0, max(0.0, local))))
    return out


def orient_b_to_a(coords_a, coords_b):
    """Return coords_b, reversed if that lines it up with coords_a better.

    Equal counts use the plain vertex-to-vertex test; unequal counts compare
    the two lines resampled to the same number of points, which is the same
    test generalised (and reduces to it when the counts agree).
    """
    reversed_b = list(reversed(coords_b))
    if len(coords_a) == len(coords_b):
        if chain_distance(coords_a, reversed_b) < chain_distance(coords_a, coords_b):
            return reversed_b, True
        return list(coords_b), False
    samples = max(len(coords_a), len(coords_b))
    sa = resample(coords_a, arc_params(coords_a), samples)
    fwd = resample(coords_b, arc_params(coords_b), samples)
    rev = resample(reversed_b, arc_params(reversed_b), samples)
    if chain_distance(sa, rev) < chain_distance(sa, fwd):
        return reversed_b, True
    return list(coords_b), False


# ---------------------------------------------------------------- insertion
def plan_insertions(short_params, long_params):
    """Which of `long_params` to copy into the shorter line, and where.

    Greedy farthest-first: the parameter with the largest distance to the
    nearest already-used one wins, and joins the used set. Returns a list of
    (segment_index, local_fac, t) sorted by t, where segment_index is the
    index of the shorter line's ORIGINAL segment holding t and local_fac the
    position within that original segment.

    Raises ChainError when a wanted parameter falls on top of a vertex that
    is already there (the split would make a zero-length edge).
    """
    need = len(long_params) - len(short_params)
    if need <= 0:
        return []
    used = list(short_params)
    remaining = list(long_params)
    chosen = []
    for _ in range(need):
        best_t = None
        best_gap = -1.0
        for t in remaining:
            gap = min(abs(t - u) for u in used)
            if gap > best_gap:
                best_gap = gap
                best_t = t
        if best_t is None or best_gap <= MIN_SPLIT_FAC:
            raise ChainError(
                "The two lines cannot be matched up: the vertices missing "
                "from the shorter line would land on top of vertices it "
                "already has.")
        chosen.append(best_t)
        used.append(best_t)
        remaining.remove(best_t)
    out = []
    for t in sorted(chosen):
        i = 0
        while i < len(short_params) - 2 and short_params[i + 1] < t:
            i += 1
        span = short_params[i + 1] - short_params[i]
        fac = 0.0 if span <= 0.0 else (t - short_params[i]) / span
        if fac < MIN_SPLIT_FAC or fac > 1.0 - MIN_SPLIT_FAC:
            raise ChainError(
                "The two lines cannot be matched up: a vertex the shorter "
                "line needs would land on a vertex it already has.")
        out.append((i, fac, t))
    return out


def clear_vert_tags(bm, v):
    """A vertex this tool made is a plain cut vertex: no pin, no corner flag,
    no boundary flag (edge_split interpolates every int layer onto it).

    Same reasoning as panel_regions._clear_vert_tags, without that module's
    per-run scope marker.
    """
    for lay in bm.verts.layers.int.values():
        v[lay] = 0


def apply_insertions(bm, ordered, plan):
    """Split the shorter line per `plan`; return its new ordered vertex list.

    Runs entirely in bmesh (no operator), so the BMVert references in
    `ordered` stay valid throughout.
    """
    by_segment = {}
    for i, fac, _t in plan:
        by_segment.setdefault(i, []).append(fac)
    result = []
    added = []
    for i in range(len(ordered) - 1):
        result.append(ordered[i])
        fracs = sorted(by_segment.get(i, []))
        if not fracs:
            continue
        current = ordered[i]
        end = ordered[i + 1]
        done = 0.0
        for fac in fracs:
            edge = bm.edges.get((current, end))
            if edge is None:
                raise ChainError(
                    "The shorter line changed while it was being divided.")
            local = (fac - done) / max(MIN_SPLIT_FAC, 1.0 - done)
            _new_edge, new_vert = bmesh.utils.edge_split(edge, current, local)
            clear_vert_tags(bm, new_vert)
            result.append(new_vert)
            added.append(new_vert)
            current = new_vert
            done = fac
    result.append(ordered[-1])
    return result, added


def check_monotonic(params_a, params_b):
    """True when zipping the two parameter lists gives non-crossing rungs.

    Both lists must have the same length and both must run forwards: a rung
    joins the i-th vertex of each line, so any inversion would cross the
    rungs beside it.
    """
    if len(params_a) != len(params_b):
        return False
    for params in (params_a, params_b):
        for p, q in zip(params, params[1:]):
            if q < p:
                return False
    return True


def all_boundary(bm, verts):
    """True when every vertex carries the outline flag (and the flag exists)."""
    lay = bm.verts.layers.int.get(BOUNDARY_LAYER)
    if lay is None or not verts:
        return False
    return all(v[lay] != 0 for v in verts)


def plan_rungs(bm, selected_edges):
    """Everything that happens before the mesh is touched.

    Returns (chain_short, chain_long, plan, swapped) where `plan` is the
    insertion plan for `chain_short` (empty when the counts already match)
    and `swapped` says whether chain_short came from the second selected
    line. Raises ChainError with a user-facing message on every refusal.
    """
    components = edge_components(selected_edges)
    if len(components) != 2:
        raise ChainError(
            f"{len(components)} line(s) selected. Select exactly two lines "
            f"(their edges) to run rungs between.")
    chain_a = ordered_chain(components[0])
    chain_b = ordered_chain(components[1])
    coords_a = [v.co.copy() for v in chain_a]
    coords_b = [v.co.copy() for v in chain_b]
    _oriented, flipped = orient_b_to_a(coords_a, coords_b)
    if flipped:
        chain_b = list(reversed(chain_b))
    if len(chain_a) == len(chain_b):
        return chain_a, chain_b, [], False
    swapped = len(chain_b) < len(chain_a)
    short_chain = chain_b if swapped else chain_a
    long_chain = chain_a if swapped else chain_b
    if all_boundary(bm, short_chain):
        raise ChainError(
            f"The shorter line is the outline ({len(short_chain)} vertices "
            f"against {len(long_chain)}). Match the counts with Adjust "
            f"Density instead — it edits both sides of the sewn seam "
            f"together, which adding vertices here would break.")
    short_params = arc_params([v.co.copy() for v in short_chain])
    long_params = arc_params([v.co.copy() for v in long_chain])
    plan = plan_insertions(short_params, long_params)
    # The rungs are checked here, while nothing has been touched yet: the
    # shorter line's parameters after the division are its own plus the ones
    # copied from the longer line.
    predicted = sorted(short_params + [t for _i, _f, t in plan])
    if not check_monotonic(predicted, long_params):
        raise ChainError(
            "The two lines cannot be matched up without the rungs crossing. "
            "Nothing was changed.")
    return short_chain, long_chain, plan, swapped
