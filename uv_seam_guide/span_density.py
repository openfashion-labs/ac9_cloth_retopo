"""Change how many vertices a seam span carries, on both sides at once.

`retopo_seam_sync` deliberately only ever INSERTS: it mirrors what one side of
a seam already has onto the other and never moves or deletes anything. That is
the right default, but it leaves no way to say "this span is too coarse" once
a span has been generated — Generate skips any span that already has retopo on
it, so re-running it with a different Spacing changes nothing.

This module is the other half: pick a span, give it a new vertex count, and
both of its sides are laid down again from scratch at that count. Existing
interior vertices are deleted (and with them any faces that used them — a
boundary vertex cannot be removed while keeping the faces it corners).

Which span, and how it is picked
--------------------------------
From the retopo selection in Edit Mode. `map_retopo_to_spans` already reports,
for every retopo boundary vertex, which Guide span it sits on and where along
it; an edge whose two ends share a span IS that span.

Edges are the primary handle, and vertex mode needs nothing special: Blender
flushes a vertex selection up to the edges between adjacent selected vertices,
so a run of selected vertices arrives here as selected edges. Only a LONE
vertex flushes to no edge, and that falls back to the vertex's own span — well
defined everywhere except on an anchor, which belongs to every span meeting
there and is reported rather than guessed at.

The partner side comes along automatically through the span pairing, so
selecting one side of a seam is enough.

Why orientation does not matter
-------------------------------
`build_spans` has no preferred direction, so a paired span's two sides may run
opposite ways: side A's u and side B's u can be each other's 1-u. That would
matter for an arbitrary set of parameters — but a uniform resample places
vertices at {0, 1/(n-1), ... 1}, which is symmetric under u -> 1-u. The two
sides therefore land on the same set of 3D points whichever way round they
run, and this module can work in each span's own canonical orientation
without tracking the pairing direction at all.
"""

from collections import defaultdict, deque

import bmesh

from . import anchor_segments as anch
from . import density_pin
from . import retopo_seam_sync as rss

#: A parameter this close to an end counts as sitting ON the anchor. Anchors
#: are shared with the neighbouring spans, so they are kept, never rebuilt.
U_ANCHOR_EPS = 1e-3

#: Weld radius for the rebuilt chain, in flat-layout world units (0.2mm). Same
#: value and same reasoning as apply_seam_sync: in the flat layout the two
#: sides of a seam sit on different panels, so nothing can be merged by
#: accident.
WELD_DIST = 0.0002


class SpanContext:
    """Everything the resample needs about the Guide, computed once."""

    def __init__(self, spans, pairs, assign, span_index, basis_co, flat_co):
        self.spans = spans
        self.pairs = pairs
        self.assign = assign
        self.span_index = span_index
        self.basis_co = basis_co
        self.flat_co = flat_co

        # span index -> the span it is sewn to, if any.
        self.partner = {}
        for pr in pairs:
            ia = span_index.get(tuple(pr["a"]))
            ib = span_index.get(tuple(pr["b"]))
            if ia is None or ib is None:
                continue
            self.partner[ia] = ib
            self.partner[ib] = ia

        # retopo vertex index -> the spans it is filed under. An anchor
        # vertex legitimately appears under every span meeting there.
        self.vert_spans = defaultdict(set)
        for si, entries in assign.items():
            for _u, vi, _d in entries:
                self.vert_spans[vi].add(si)

    def length_mm(self, si):
        return anch._arc_lengths(self.spans[si], self.basis_co)[-1] * 1000.0

    def count(self, si):
        """How many retopo vertices currently sit on this span, anchors included."""
        return len(self.assign.get(si, ()))


def build_context(guide, retopo, flat_sk, match_distance=0.0, tol=0.005):
    """Run the read-only Guide analysis and map the retopo onto it.

    Same preamble as classify_seam_pairs; kept as its own function because the
    resample needs the raw spans and the assignment, not the verdict rows.
    """
    bvs, vns, parts = anch.find_vertex_partners(guide, match_distance=match_distance)
    _a, _b, basis_co = anch._collect_boundary(guide)
    flat_co = anch.get_flat_co(guide, flat_sk)
    anchors = anch.find_anchors(bvs, vns, parts)
    spans = anch.build_spans(vns, anchors)
    pairs, _unpaired = anch.pair_spans(spans, parts, basis_co)
    assign = rss.map_retopo_to_spans(guide, retopo, spans, basis_co, flat_co,
                                     tol=tol)
    span_index = rss.build_span_index(spans)
    return SpanContext(spans, pairs, assign, span_index, basis_co, flat_co)


def spans_from_selection(bm, ctx, matrix):
    """Which spans the current retopo selection lies on.

    Returns (span indices, edges that matched no span, verts that were on an
    anchor and so could not be resolved).

    Edges first. Blender flushes a vertex selection up to the edges between
    adjacent selected vertices, so selecting a run of vertices in vertex mode
    already lands here — nothing special is needed for that case (measured:
    verts [1,2,3] -> edges [(1,2),(2,3)]).

    A LONE vertex flushes to no edge at all, and that is the one case worth
    handling separately: a vertex in the middle of a span belongs to exactly
    one span, so it names its span unambiguously. A vertex ON an anchor
    genuinely belongs to every span meeting there, and there is no honest way
    to guess which — those are counted and reported rather than picked.
    """
    picked = set()
    unmatched = 0
    for e in bm.edges:
        if not e.select:
            continue
        a, b = e.verts[0].index, e.verts[1].index
        cand = ctx.vert_spans.get(a, set()) & ctx.vert_spans.get(b, set())
        if not cand:
            unmatched += 1
            continue
        if len(cand) == 1:
            picked.add(next(iter(cand)))
            continue
        # Two spans can both claim an edge when it runs between two anchors
        # they share (a span with no interior vertices, alongside the panel
        # outline going the other way). Decide on the midpoint: the edge lies
        # along exactly one of them.
        mid = matrix @ ((e.verts[0].co + e.verts[1].co) * 0.5)
        best = None
        for si in cand:
            d, _u = rss._project_to_span(mid, ctx.spans[si], ctx.basis_co,
                                         ctx.flat_co)
            if best is None or d < best[0]:
                best = (d, si)
        picked.add(best[1])

    if picked:
        return picked, unmatched, 0

    # ── Nothing came from edges: fall back to lone vertices ────────────────
    on_anchor = 0
    for v in bm.verts:
        if not v.select:
            continue
        cand = ctx.vert_spans.get(v.index, set())
        if len(cand) == 1:
            picked.add(next(iter(cand)))
        elif len(cand) > 1:
            on_anchor += 1
        else:
            unmatched += 1
    return picked, unmatched, on_anchor


def group_with_partners(ctx, span_indices):
    """Expand each picked span into the group that has to move together.

    A sewn span and its partner are one job: resampling one side without the
    other is exactly the mismatch this whole feature exists to remove. A free
    edge is a group of one.
    """
    groups = []
    seen = set()
    for si in sorted(span_indices):
        if si in seen:
            continue
        group = [si]
        seen.add(si)
        pj = ctx.partner.get(si)
        if pj is not None and pj not in seen:
            group.append(pj)
            seen.add(pj)
        groups.append(group)
    return groups


def chain_counts(bm, ctx, groups):
    """Vertex count of every span in `groups`, measured on the ACTUAL
    boundary chain (_span_chain), not on the raw assignment.

    The two differ on real files: knife-cut edges meeting the outline and
    leftover wire stubs near an anchor put extra vertices within the sync
    tolerance of the span curve, and those get filed into ctx.assign along
    with the true chain (measured 2026-09-02: 8 of 20 sewn pairs on a
    production file carried 1-3 such spurs). A DELTA baseline taken from the
    padded assignment overshoots, and a mismatched PAIR baseline even makes
    "-1" a silent no-op. Spans whose chain can't be derived at all are
    simply absent from the result; callers fall back to ctx.count for those
    (the resample itself will then report them skipped).
    """
    counts = {}
    for group in groups:
        for si in group:
            entries = sorted(ctx.assign.get(si, ()))
            if len(entries) < 2:
                continue
            chain = _span_chain(bm, entries)
            if chain is not None:
                counts[si] = len(chain)
    return counts


def target_count(ctx, group, mode, delta=0, count=8, spacing_mm=20.0,
                 counts=None):
    """The vertex count this group should end up with, anchors included.

    `counts` (optional, from chain_counts) overrides ctx.count per span —
    the DELTA baseline should follow the real boundary chain, not the
    assignment padded with knife-cut spurs (see chain_counts).
    """
    if mode == 'COUNT':
        return max(2, int(count))
    if mode == 'SPACING':
        length = max(ctx.length_mm(si) for si in group)
        return max(2, int(round(length / max(spacing_mm, 1e-6))) + 1)
    # DELTA — relative to what the group carries now. Both sides are about to
    # be given the SAME count, so which side a mismatched pair is measured
    # from decides whether the other one gains or loses.
    #
    # Measuring from the busier side always ("+1 never silently drops
    # vertices") made minus ADD: on a pair carrying 2 and 4, "-1" asks for 3,
    # and the sparse side gains one. On a panel sewn to itself both sides are
    # in the same view, so it reads as "I pressed minus and it got denser" —
    # measured on a production file, 11 of 64 sewn pairs disagreed and 4 of
    # them grew under "-1", leaving a visible cluster.
    #
    # So pick the end that keeps the button's promise: minus never adds,
    # plus never removes. A mismatched pair converges on the first press.
    per_span = [(counts[si] if counts and si in counts else ctx.count(si))
                for si in group]
    current = min(per_span) if delta < 0 else max(per_span)
    return max(2, current + int(delta))


def _place(vert, u, span, ctx, inv, basis_layer, proj_layer):
    """Move a vertex onto the span at parameter u, in every space it lives in."""
    flat_w, basis_w = rss.span_point_at_u(span, u, ctx.basis_co, ctx.flat_co)
    local_flat = inv @ flat_w
    vert.co = local_flat
    if basis_layer is not None:
        vert[basis_layer] = local_flat
    if proj_layer is not None:
        vert[proj_layer] = inv @ basis_w


def _uniform_us_with_pins(target, pin_by_u):
    """`target` evenly-spaced 0..1 parameters (the plain "no pins at all"
    layout this module always used), then each pin claims the grid slot it
    lands closest to instead of a fresh evenly-spaced point there.

    The two end slots (0.0 and 1.0 — the anchors) are never claimable: a
    pin is always an INTERIOR vertex by construction (see resample_
    groups' caller, which only ever puts chain[1:-1] entries in pin_by_u),
    so a pin sitting a hair away from an anchor should displace its
    nearest INTERIOR neighbour, not the anchor itself.

    `target` is floored to at least `len(pin_by_u) + 2` — a pin can never
    be dropped to hit a target that has no room left for it once the two
    anchors are accounted for.

    Slots are claimed in ascending pin-parameter order and each pin picks
    its own single nearest still-free slot; with pins spread out this
    barely perturbs neighbouring grid points, and with pins clustered
    together the closest free slots are shared out in position order
    rather than colliding on the same one.

    KNOWN LIMITATION (measured 2026-09-02, test_density_pin.py /
    probe_pin_asymmetry.py): a pin only protects the SIDE it sits on. Each
    side of a sewn pair is planned independently (same as this module has
    always done), so a pin on side A with no corresponding pin at the
    matching 3D position on side B lets the two sides' grids perturb
    differently and can break the "both sides land on the same 3D points"
    guarantee this module otherwise keeps to 0.00000mm. A pin at a
    parameter that happens to coincide with a plain evenly-spaced grid
    point causes no perturbation at all and hides this — confirmed with
    both sides pinned at the SAME u, which reproduces the old zero-gap
    guarantee exactly. Placing a pin is therefore only safe, in general,
    when the corresponding vertex is pinned on both sides — the intended
    workflow pairs this with syncing a knife-cut vertex to its partner
    side first (see the retopo_seam_sync Sync-selected design discussion),
    not with pinning one side alone and hoping the other matches by luck.
    """
    target = max(2, int(target), len(pin_by_u) + 2)
    us = [k / (target - 1) for k in range(target)]
    claimed = {0, target - 1}
    for pu in sorted(pin_by_u):
        best_k, best_d = None, None
        for k in range(1, target - 1):
            if k in claimed:
                continue
            d = abs(us[k] - pu)
            if best_d is None or d < best_d:
                best_k, best_d = k, d
        if best_k is not None:
            us[best_k] = pu
            claimed.add(best_k)
    us.sort()
    return us


def resample_groups(bm, ctx, groups, targets, matrix, mode='COUNT',
                    spacing_mm=0.0, min_seam_mm=5.0, fold_hard_us=None):
    """Rebuild every span in `groups` at its group's target count.

    Deletes first across the whole batch, then builds, then welds once — so no
    BMVert reference is used after the operation that could have freed it.

    Pin protection reads `bm`'s OWN custom-data layers (density_pin.
    pinned_indices_bm: this module's own ac9_density_pin attribute UNION
    topology_corner's ac9_topo_corner, see density_pin's module docstring
    for why both are honoured but only the former is ever written here),
    never mesh.attributes -- this function always runs against an Edit
    Mode bmesh (bmesh.from_edit_mesh), where the mesh datablock's attribute
    arrays read back EMPTY (same Edit Mode trap topology_corner.py's module
    comment describes). Measured 2026-09-02: a mesh.attributes-based reader
    here saw zero pins every time, silently discarding one the user had
    just marked in the same session on its very next Count change. A
    pinned interior vertex is kept exactly where it is -- never in the
    delete list, forced into the rebuilt chain at its own existing u
    (matched by value, not moved) -- instead of being erased and replaced
    like every other interior vertex always has been. This is what makes a
    knife-cut vertex on the outline (indistinguishable from a plain chain
    vertex once it has a face) survive a Count/Spacing change instead of
    being silently dissolved back into a uniform spacing.

    `mode` ('COUNT' or 'SPACING') and `spacing_mm` pick how anchor_segments.
    division_parameters lays out each stretch BETWEEN pins (and fold-axis
    crossings, see `fold_hard_us`): COUNT gives every stretch `target`
    vertices -- matching Generate's own per-stretch semantics, confirmed
    2026-09-02 as the wanted behaviour over a strict whole-span budget;
    SPACING places them `spacing_mm` apart, same as Generate. With no pins
    and no fold crossings this reduces to exactly the old "n equally-spaced
    points" layout (division_parameters on a single 0..1 stretch with an
    empty hard_us list is that layout by construction), so an unpinned span
    behaves exactly as it always has.

    `fold_hard_us`, when given, is {span index: [u, ...]} -- self-symmetry
    fold-axis crossings (retopo_seam_sync.resolve_fold_axis_hard_us) forced
    the same way a pin is, except there is no existing vertex to snap onto:
    a brand new one is created there, at the same 0..1 parameter Generate
    itself would have used.
    """
    inv = matrix.inverted()
    from ..clo_projector.core import SHAPEKEY_NAME
    shape_layers = bm.verts.layers.shape
    basis_layer = shape_layers.get("Basis")
    proj_layer = shape_layers.get(SHAPEKEY_NAME)

    bm.verts.ensure_lookup_table()

    stats = {"spans": 0, "removed": 0, "created": 0, "welded": 0,
             "faces_lost": 0, "skipped_ambiguous": 0,
             "skipped_broken_chain": 0, "pinned_kept": 0}

    pin_set = density_pin.pinned_indices_bm(bm)

    # ── Plan: resolve every BMVert up front, while indices are still valid ──
    plans = []
    for group, target in zip(groups, targets):
        for si in group:
            span = ctx.spans[si]
            if not anch_span_ok(span, ctx):
                stats["skipped_ambiguous"] += 1
                continue
            entries = sorted(ctx.assign.get(si, ()))
            if len(entries) < 2:
                continue
            # The ACTUAL boundary chain, not the raw assignment — see
            # _span_chain's docstring (knife-cut spurs / wire stubs near an
            # anchor land in ctx.assign alongside the true chain and used to
            # get bulk-deleted as ordinary "interior").
            chain = _span_chain(bm, entries)
            if chain is None:
                stats["skipped_broken_chain"] += 1
                continue

            umap = {}
            for u, vi, _d in entries:
                umap.setdefault(vi, u)
            interior_idx = chain[1:-1]
            pinned_idx = [i for i in interior_idx if i in pin_set]
            pin_by_u = {umap[i]: bm.verts[i] for i in pinned_idx}
            fold_us_here = list((fold_hard_us or {}).get(si, ()))

            if fold_us_here:
                # A fold-axis crossing changes what the whole run MEANS —
                # the two sides of the axis are two independent mirror
                # halves, so Generate's own per-stretch semantics apply
                # (confirmed 2026-09-02: `target` vertices EACH side of
                # the axis, not `target` total) — same anch.
                # division_parameters call Generate itself uses, with pins
                # added to the same hard_us list (pins never get dropped
                # by division_parameters' merge_eps proximity rule the way
                # a fold crossing can, because a pin coincides with an
                # EXISTING break by construction whenever it's close enough
                # to matter).
                hard_us = sorted(pin_by_u) + fold_us_here
                if mode == 'SPACING':
                    us = anch.division_parameters(
                        span, ctx.basis_co, hard_us=hard_us,
                        spacing_mm=spacing_mm, min_seam_mm=min_seam_mm)
                else:
                    us = anch.division_parameters(
                        span, ctx.basis_co, hard_us=hard_us,
                        count=target, min_seam_mm=min_seam_mm)
            else:
                # Pins alone do NOT change what the run means — a knife-cut
                # vertex is still one point on an otherwise ordinary
                # stretch, and the user asked for `target` vertices on
                # THIS SPAN, not `target` per pin-bounded stretch (measured
                # 2026-09-02: division_parameters' per-stretch count on a
                # single pin turned Count=11 into 21 vertices — Set Count
                # ballooning is not what "give this span 11 vertices"
                # means). So `target`'s uniform grid is computed as if
                # there were no pins at all, and each pin then simply
                # claims whichever grid slot it lands closest to (never an
                # anchor's own two end slots) — the rest of the grid stays
                # evenly spaced, i.e. a pin near the middle of an otherwise
                # even spacing barely perturbs its neighbours, while pins
                # clustered together fall back to sharing the closest
                # available slots in position order.
                us = _uniform_us_with_pins(target, pin_by_u)

            doomed = [bm.verts[i] for i in interior_idx if i not in pin_set]
            plans.append({
                "span": span,
                "us": us,
                "start": bm.verts[chain[0]],
                "end": bm.verts[chain[-1]],
                "pin_by_u": pin_by_u,
                "doomed": doomed,
            })
            stats["pinned_kept"] += len(pinned_idx)

    # ── Delete ─────────────────────────────────────────────────────────────
    doomed = []
    doomed_edges = []
    for p in plans:
        doomed.extend(p["doomed"])
        # A span whose rebuild ends up with only its two anchors (or an
        # anchor and a pin sitting right at the very end) may currently
        # have its two ends joined directly; that edge would collide with
        # the one the build phase creates in its place.
        for e in p["start"].link_edges:
            if e.other_vert(p["start"]) is p["end"]:
                doomed_edges.append(e)
    if doomed:
        faces = set()
        for v in doomed:
            faces.update(v.link_faces)
        stats["faces_lost"] += len(faces)
        stats["removed"] += len(doomed)
        bmesh.ops.delete(bm, geom=doomed, context='VERTS')
    if doomed_edges:
        alive = [e for e in doomed_edges if e.is_valid]
        if alive:
            stats["faces_lost"] += len({f for e in alive for f in e.link_faces})
            bmesh.ops.delete(bm, geom=alive, context='EDGES')
    bm.verts.ensure_lookup_table()

    # ── Build ──────────────────────────────────────────────────────────────
    created = []
    for p in plans:
        span, us = p["span"], p["us"]
        n = len(us)
        chain = []
        for k, u in enumerate(us):
            if k == 0 and p["start"].is_valid:
                chain.append(p["start"])
                continue
            if k == n - 1 and p["end"].is_valid:
                chain.append(p["end"])
                continue
            matched = None
            for pu, pv in p["pin_by_u"].items():
                if pv.is_valid and abs(pu - u) < 1e-9:
                    matched = pv
                    break
            if matched is not None:
                chain.append(matched)
                continue
            v = bm.verts.new((0.0, 0.0, 0.0))
            _place(v, u, span, ctx, inv, basis_layer, proj_layer)
            created.append(v)
            chain.append(v)
            stats["created"] += 1
        for a, b in zip(chain, chain[1:]):
            try:
                bm.edges.new((a, b))
            except ValueError:
                pass  # already joined
        for v in chain:
            v.select_set(True)
        stats["spans"] += 1
        bm.verts.ensure_lookup_table()

    # ── Weld ───────────────────────────────────────────────────────────────
    # Two rebuilt spans meeting at a shared anchor each recreate that anchor
    # when neither had one, which would leave the chains unjoined. Same fix
    # and same radius as apply_seam_sync.
    if created:
        targets_w = {v for v in created if v.is_valid}
        for e in bm.edges:
            if len(e.link_faces) <= 1:
                targets_w.update(e.verts)
        before = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=list(targets_w), dist=WELD_DIST)
        stats["welded"] = before - len(bm.verts)
        bm.verts.ensure_lookup_table()

    return stats


def even_out_groups(bm, ctx, groups, matrix):
    """Re-place every span's EXISTING interior vertices at evenly-spaced u,
    without changing vertex count or touching a single face/edge.

    "Phase 0" of the Adjust Density redesign (see 引き継ぎメモ /
    session notes on the Opus+Fable design review, 2026-09-01): the
    resample_groups delete-and-rebuild approach takes every face hanging off
    a span's interior vertices with it, which is fine before any fill exists
    but destructive once the user has started Grid Regions / Patch Grid /
    hand-cutting with the Knife — exactly the workflow this tool exists to
    support. This function is the non-destructive half: it only works when
    the vertex COUNT is already right and the positions just need
    straightening (e.g. after a few manual edits nudged them off-u), using
    the same per-vertex move ghost_snap_move already does (vert.co /
    Basis / projection layers only — no bmesh.ops.delete or .new at all).

    Anchors are never moved (shared with the neighbouring span, same as
    resample_groups). A density pin (or a user-placed topology_corner) is
    never moved either — it becomes an extra break the same way an anchor
    already is (see _uniform_replace's pin_set handling), so a knife-cut
    vertex the user pinned on purpose survives an Even Out the same way it
    survives a Count/Spacing change. A span whose interior has 0 or 1
    vertex has nothing to straighten and is skipped, not an error — call
    target_count/resample_groups first if the count itself needs to change.
    """
    inv = matrix.inverted()
    from ..clo_projector.core import SHAPEKEY_NAME
    shape_layers = bm.verts.layers.shape
    basis_layer = shape_layers.get("Basis")
    proj_layer = shape_layers.get(SHAPEKEY_NAME)

    bm.verts.ensure_lookup_table()
    pin_set = density_pin.pinned_indices_bm(bm)
    stats = {"spans": 0, "moved": 0, "skipped_ambiguous": 0,
             "skipped_too_short": 0, "skipped_broken_chain": 0}

    for group in groups:
        for si in group:
            span = ctx.spans[si]
            if not anch_span_ok(span, ctx):
                stats["skipped_ambiguous"] += 1
                continue
            entries = sorted(ctx.assign.get(si, ()))
            if len(entries) < 3:
                # Just the two anchors (or fewer) -- no interior to straighten.
                stats["skipped_too_short"] += 1
                continue
            # The CHAIN, not the raw entries: the assignment also catches
            # knife-cut spurs near the outline, and index-spacing those
            # along with the real chain places everything unevenly AND
            # drags the spur vertex onto the seam (see _span_chain).
            chain = _span_chain(bm, entries)
            if chain is None:
                stats["skipped_broken_chain"] += 1
                continue
            n = len(chain)
            if n < 3:
                stats["skipped_too_short"] += 1
                continue
            _uniform_replace(bm, chain, span, ctx, inv, basis_layer,
                             proj_layer, pin_set=pin_set)
            bm.verts.ensure_lookup_table()
            for k, vi in enumerate(chain):
                if k == 0 or k == n - 1:
                    continue
                v = bm.verts[vi]
                v.select_set(True)
                if vi not in pin_set:
                    stats["moved"] += 1
            stats["spans"] += 1
    return stats


def _span_chain(bm, entries):
    """Ordered vertex INDICES of the span's actual boundary chain, derived
    as a PATH from one anchor to the other through the assigned vertices.

    Indices, not BMVert references: bmesh.ops.subdivide_edges and
    bmesh.ops.pointmerge invalidate EVERY existing BMVert Python wrapper in
    the whole bmesh as a side effect, not just the ones the op touches --
    measured directly (probe_shapekey_subdivide.py, 2026-09-01), reproduced
    both with and without shape-key layers on the mesh. Holding onto a
    BMVert across either op raises ReferenceError the next time it's read.
    Indices survive both ops (also measured, probe_index_stability.py):
    subdivide_edges never renumbers an existing vertex (new ones are
    appended); pointmerge removes exactly one and compacts everything
    after it down by 1 -- see _reindex_after_removal for the compaction.

    Why a path search and not just "consecutive entries must share an
    edge": ctx.assign holds every vertex within the sync tolerance of the
    span CURVE, and on real files that includes vertices that are NOT part
    of the boundary chain -- the first vertex of a knife cut leaving the
    outline, or a leftover wire stub hanging off an anchor. Measured
    (2026-09-02, production file): 8 of 20 sewn pairs carried 1-3 such
    spurs, and the u-order adjacency check tripped over every one of them,
    turning the whole span into a silent no-op. The spurs are dead ends in
    the subgraph induced by the assigned vertices, so a breadth-first path
    between the anchor candidates walks straight past them; the u values
    along the found path must still be (weakly) monotone, which rejects a
    path that runs the wrong way round a panel outline.

    Returns None when no such path exists -- the boundary there is
    genuinely fragmented (or ctx.assign has drifted from `bm` after a
    topology edit). Callers must treat None as "skip this span", never
    guess a chain from stale u values.
    """
    bm.verts.ensure_lookup_table()
    entries = sorted(entries)
    umap = {}
    for u, vi, _d in entries:
        umap.setdefault(vi, u)
    vids = set(umap)

    adj = {vi: [] for vi in vids}
    for vi in vids:
        v = bm.verts[vi]
        for e in v.link_edges:
            o = e.other_vert(v).index
            if o in vids:
                adj[vi].append(o)

    starts = [vi for u, vi, _d in entries if u <= U_ANCHOR_EPS]
    ends = [vi for u, vi, _d in entries if u >= 1.0 - U_ANCHOR_EPS]
    if not starts:
        starts = [entries[0][1]]
    if not ends:
        ends = [entries[-1][1]]

    def _bridge(cands):
        """A stub tip filed as the span's anchor can be a DEAD END in the
        subgraph: its only mesh edge leads to the true junction vertex,
        which the assignment missed (it sits just past the tolerance).
        Measured (2026-09-02): three spans on a production file had exactly
        this — anchor entry with a single edge to an unassigned vertex that
        itself connects straight into the chain. Stand that neighbour in as
        an anchor candidate, inheriting the stub tip's u; the geometry
        validation downstream still gates anything done with it.
        """
        out = list(cands)
        for c in cands:
            if adj[c]:
                continue
            v = bm.verts[c]
            for e in v.link_edges:
                w = e.other_vert(v).index
                if w in vids or w in adj:
                    continue
                wv = bm.verts[w]
                links = [e2.other_vert(wv).index for e2 in wv.link_edges]
                if any(o in vids and o != c for o in links):
                    adj[w] = [o for o in links if o in vids]
                    for o in adj[w]:
                        adj[o].append(w)
                    umap[w] = umap[c]
                    out.append(w)
        return out

    starts = _bridge(starts)
    ends = _bridge(ends)
    start_set, end_set = set(starts), set(ends)

    for s in starts:
        # BFS to the FIRST end candidate reached: two candidates can share
        # an end of the span (the junction vertex plus a stub hanging off
        # it), and running past the nearer one would put the stub INSIDE
        # the chain and let the uniform re-place drag the real junction
        # off its anchor.
        targets = end_set - {s}
        if not targets:
            continue
        prev = {s: None}
        queue = deque([s])
        hit = None
        while queue and hit is None:
            cur = queue.popleft()
            if cur in targets:
                hit = cur
                break
            for o in adj[cur]:
                if o not in prev:
                    prev[o] = cur
                    queue.append(o)
        if hit is None:
            continue
        path = []
        cur = hit
        while cur is not None:
            path.append(cur)
            cur = prev[cur]
        path.reverse()
        # Trim anchor-candidate runs off both ends for the same reason:
        # the chain should START at the last start-candidate in a row and
        # END at the first end-candidate reached.
        while len(path) > 2 and path[1] in start_set:
            path.pop(0)
        while len(path) > 2 and path[-2] in end_set:
            path.pop()
        if len(path) < 2:
            continue
        us = [umap[vi] for vi in path]
        if all(b >= a - 0.02 for a, b in zip(us, us[1:])):
            return path
    return None


#: Face-flag layers every tool that fills interior faces uses to mark its
#: OWN disposable output (see each module's *_FACE_LAYER). A face carrying
#: none of these is presumed hand-made (Knife-cut, manually authored) --
#: exactly what the user said must never be destroyed. Imported lazily
#: inside _tool_face_layers to avoid a needless import at module load for
#: callers that never touch faces (Even Out).
def _tool_face_layers(bm):
    from . import preview_fill as _pf
    from . import panel_regions as _pr
    from . import patch_grid as _pg
    names = (_pf.FILL_FACE_LAYER, _pr.SCAFFOLD_LAYER, _pg.GRID_FACE_LAYER)
    return [lay for lay in (bm.faces.layers.int.get(n) for n in names) if lay is not None]


def _is_hand_made(face, flay_list):
    return not any(face[lay] for lay in flay_list)


def _piecewise_uniform_us(L, pinned_positions=None):
    """The u (0..1) each of L sequence positions gets under a plain
    evenly-spaced layout (k/(L-1)), except that every position in
    `pinned_positions` is treated as an extra break alongside the two ends
    (0 and L-1): the sequence between two consecutive breaks is re-spaced
    evenly using each break's OWN k/(L-1) value as its endpoint u, rather
    than re-deriving a break's position from anything else. A pin's u is
    therefore always its plain grid position — this function never MOVES
    a break, it only uses the break positions to re-space what falls
    between them.

    Shared by _uniform_replace (the real per-vertex move) and _steps_safe
    (the safety simulation) so a pin is treated identically by both — the
    two disagreeing would mean a plan passes validation but the actual
    execution ends up somewhere else (see _steps_safe's docstring on why
    that would defeat the whole plan/validate/execute split).

    Returns a list of L floats. With no pins this is exactly the old
    `[k / (L - 1) for k in range(L)]` layout.
    """
    if L < 2:
        return [0.0] * L
    if not pinned_positions:
        return [k / (L - 1) for k in range(L)]
    breaks_k = sorted({0, L - 1} | set(pinned_positions))
    us = [0.0] * L
    for k in breaks_k:
        us[k] = k / (L - 1)
    for i in range(len(breaks_k) - 1):
        k0, k1 = breaks_k[i], breaks_k[i + 1]
        u0, u1 = us[k0], us[k1]
        span_k = k1 - k0
        for k in range(k0 + 1, k1):
            t = (k - k0) / span_k
            us[k] = u0 + (u1 - u0) * t
    return us


def _uniform_replace(bm, chain, span, ctx, inv, basis_layer, proj_layer,
                     pin_set=None):
    """Re-place every vertex in `chain` (a list of vertex INDICES, anchors
    included) at evenly-spaced u. Same per-vertex move as even_out_groups --
    no bmesh.ops involved, so the indices are read fresh and safely.

    `pin_set` (density_pin.pinned_indices_bm's output), when given, marks
    which of the chain's INTERIOR indices must not move at all -- a
    density pin, or a user-placed topology_corner. See
    _piecewise_uniform_us for how a pin becomes a break instead of an
    excluded vertex.
    """
    n = len(chain)
    if n < 2:
        return
    bm.verts.ensure_lookup_table()
    pinned_positions = ({k for k in range(1, n - 1) if chain[k] in pin_set}
                        if pin_set else None)
    us = _piecewise_uniform_us(n, pinned_positions)
    for k, vi in enumerate(chain):
        if pinned_positions and k in pinned_positions:
            continue  # never moved -- see _piecewise_uniform_us's docstring
        _place(bm.verts[vi], us[k], span, ctx, inv, basis_layer, proj_layer)


def _insert_entry(bm, chain, gap_idx, flay_list):
    """Subdivide the chain edge at gap_idx, inserting exactly 1 new vertex.

    `chain` is a list of vertex INDICES (see _span_chain's docstring for
    why) -- mutated in place, the new index inserted at gap_idx+1.

    Never deletes a face: subdivide_edges on a single boundary edge only
    ever widens its one adjacent face by a vertex (a quad becomes a
    pentagon, etc) -- measured, see probe_bmesh_ops.py. The touched faces'
    hand-made-ness is read and returned as plain booleans BEFORE the op
    runs, since the BMFace wrappers themselves go stale immediately after
    (same invalidation as the verts).
    """
    bm.verts.ensure_lookup_table()
    va, vb = bm.verts[chain[gap_idx]], bm.verts[chain[gap_idx + 1]]
    e = bm.edges.get((va, vb))
    if e is None:
        raise RuntimeError(
            "chain edge missing between two entries that were connected a "
            "moment ago -- topology changed mid-operation unexpectedly")
    touched_hand_made = [_is_hand_made(f, flay_list) for f in e.link_faces]
    res = bmesh.ops.subdivide_edges(bm, edges=[e], cuts=1)
    new_verts = [el for el in res['geom_inner']
                if isinstance(el, bmesh.types.BMVert)]
    if len(new_verts) != 1:
        raise RuntimeError(
            f"subdivide_edges on one edge should yield exactly 1 new "
            f"vertex, got {len(new_verts)}")
    # subdivide_edges interpolates neighbour custom-data onto the new
    # vertex, including the density-pin/topology-corner INT flags — see
    # density_pin.clear_new_vertex's docstring for the measured incident.
    density_pin.clear_new_vertex(bm, new_verts[0])
    bm.verts.ensure_lookup_table()
    chain.insert(gap_idx + 1, new_verts[0].index)
    return touched_hand_made


def _reindex_after_removal(chain, removed_idx):
    """Adjust every OTHER index in `chain` for bmesh's compaction after one
    vertex (at `removed_idx`, already gone from `chain` itself) is deleted:
    every surviving index greater than removed_idx shifts down by 1,
    smaller ones are untouched -- measured, probe_index_stability.py.
    """
    return [(i - 1 if i > removed_idx else i) for i in chain]


# --------------------------------------------------------------------------
# Plan-and-validate machinery (2026-09-02 design review, Fable):
#
# The first incremental implementation picked WHERE to merge purely by
# 1D crowding and mutated the mesh straight away. Measured on a production
# file with a Preview Fill in place, that destroyed faces: the CDT fill lays
# a TRIANGLE on many boundary edges (5 of 9 edges on the first span checked),
# and pointmerge across an edge a triangle spans deletes that triangle
# outright — the user saw the holes as "black gaps" along the seam. A face
# count guard after the fact can only stop the SECOND deletion.
#
# There is no way to remove a boundary vertex while keeping a triangle whose
# entire base is the collapsed segment — that triangle degenerates by
# construction. (This is also why the "detach the collar, then re-weld to
# the nearest vertex" idea ends at the same place: the re-weld must merge
# two old positions into one new vertex somewhere, and whatever face spans
# those two dies the same death.) What CAN be done is to choose a segment no
# triangle spans — a quad there just turns into a triangle — and to prove,
# before touching anything, that every face hanging off the span survives
# with sane geometry. So each span edit is now three phases:
#
#   plan      — pick candidate steps by the same 1D crowding/gap heuristics
#   validate  — simulate every affected face's final vertex ring (merges
#               applied, inserts applied, chain at its final uniform-u
#               positions) and require: ring still has >= 3 corners, area
#               keeps its sign (no flips), keeps >= 5% of its magnitude
#               (no slivers), and the ring stays self-intersection-free
#   execute   — only then run the real bmesh ops (unchanged helpers below)
#
# A span with NO safe candidate is refused honestly (skipped_no_safe_edit),
# and the whole pair group is skipped with it so the two sides of a seam
# never end up at different counts. Unlike the sealed stitch_band, nothing
# here ever CREATES a face — existing faces are reshaped only, and only
# after the reshape is proven non-degenerate.
# --------------------------------------------------------------------------

def _signed_area_xy(pts):
    """Twice-signed-area-halved of a 2D polygon given as (x, y) tuples."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return 0.5 * a


def _segs_cross(p, q, r, s):
    """Proper crossing of segments pq and rs (touching endpoints don't count
    — a new vertex sitting exactly on a neighbour edge is legal)."""
    def cr(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    d1, d2 = cr(r, s, p), cr(r, s, q)
    d3, d4 = cr(p, q, r), cr(p, q, s)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _ring_is_simple(pts):
    """No two non-adjacent edges of the polygon properly cross."""
    n = len(pts)
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # adjacent edges share a vertex
            if _segs_cross(pts[i], pts[(i + 1) % n],
                           pts[j], pts[(j + 1) % n]):
                return False
    return True


def _dedup_ring(ring):
    """Collapse consecutive duplicate tokens (cyclically)."""
    out = []
    for t in ring:
        if out and t == out[-1]:
            continue
        out.append(t)
    while len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def _steps_safe(bm, matrix, ctx, span, chain, steps, pin_set=None):
    """Simulate `steps` against every face currently touching `chain` and
    report whether all of them come out geometrically sound.

    Runs BEFORE any bmesh op, on plain Python data: face rings are token
    lists (existing vertex indices, or ("new", i) for planned inserts), a
    "del" step replaces the doomed token with its survivor in every ring
    (exactly what pointmerge does to a face loop) and an "ins" step splices
    the new token between its two neighbours (exactly what subdivide_edges
    does). Final chain positions are whatever _uniform_replace will
    actually apply — _piecewise_uniform_us with any surviving pin token as
    an extra break, EXACTLY the same call _uniform_replace itself makes,
    so a plan this function approves cannot end up placed differently once
    executed (a pin the planner is refusing to touch, per _plan_span_edit,
    should never even appear as a "del" target here, but simulating its
    real final position rather than pretending the chain is unpinned keeps
    the OTHER chain vertices' simulated positions honest too).

    Sound means, for every affected ring: >= 3 corners left (a triangle
    whose base edge collapses FAILS here — that is the face-deletion case),
    the flat-layout signed area keeps its sign (no flipped faces), keeps at
    least 5% of its magnitude (no slivers — a legitimate quad-to-triangle
    at the merge point keeps roughly half), and the ring has no proper
    self-intersection (no bowties).
    """
    bm.verts.ensure_lookup_table()

    def cur_xy(vi):
        w = matrix @ bm.verts[vi].co
        return (w.x, w.y)

    rings = {}
    for vi in chain:
        for f in bm.verts[vi].link_faces:
            if f.index not in rings:
                rings[f.index] = [v.index for v in f.verts]
    before_pts = {fi: [cur_xy(t) for t in ring] for fi, ring in rings.items()}

    sim = list(chain)
    new_id = 0
    for step in steps:
        if step[0] == "ins":
            g = step[1]
            a, b = sim[g], sim[g + 1]
            tok = ("new", new_id)
            new_id += 1
            for ring in rings.values():
                n = len(ring)
                for i in range(n):
                    p, q = ring[i], ring[(i + 1) % n]
                    if (p == a and q == b) or (p == b and q == a):
                        ring.insert(i + 1, tok)
                        break
            sim.insert(g + 1, tok)
        else:
            _kind, k, go_left = step
            doomed = sim[k]
            surv = sim[k - 1] if go_left else sim[k + 1]
            for fi in rings:
                if doomed in rings[fi]:
                    rings[fi] = _dedup_ring(
                        [surv if t == doomed else t for t in rings[fi]])
            del sim[k]

    L = len(sim)
    if L < 2:
        return False
    pinned_positions = ({i for i, t in enumerate(sim)
                        if isinstance(t, int) and t in pin_set}
                        if pin_set else None)
    us = _piecewise_uniform_us(L, pinned_positions)
    pos = {}
    for i, t in enumerate(sim):
        flat_w, _basis_w = rss.span_point_at_u(span, us[i], ctx.basis_co,
                                               ctx.flat_co)
        pos[t] = (flat_w.x, flat_w.y)

    for fi, ring in rings.items():
        if len(ring) < 3:
            return False  # this face would be deleted by the merge
        pts = [pos[t] if t in pos else cur_xy(t) for t in ring]
        a0 = _signed_area_xy(before_pts[fi])
        a1 = _signed_area_xy(pts)
        if a0 != 0.0 and a1 != 0.0 and (a0 > 0) != (a1 > 0):
            return False  # flipped
        if abs(a1) < max(1e-12, 0.05 * abs(a0)):
            return False  # sliver / degenerate
        if not _ring_is_simple(pts):
            return False  # bowtie
    return True


def _plan_span_edit(bm, matrix, ctx, span, chain, ulist, delta, pin_set=None):
    """Pick the steps that bring `chain` to len(chain)+delta vertices, and
    prove them safe (_steps_safe) before anything mutates.

    Returns (steps, floored, err): `steps` is a list of ("ins", gap_idx) /
    ("del", k, go_left) positional against the evolving chain (exactly how
    _insert_entry/_collapse_entry mutate it), or None with err set when no
    safe plan exists. `floored` reports that a removal was clipped at the
    2-vertex floor. `ulist` is the current u of each chain vertex (same
    order as `chain`), used for the crowding/gap heuristics.

    `pin_set` (density_pin.pinned_indices_bm's output) excludes a pinned
    vertex from ever being a removal target — a density pin (or a
    user-placed topology_corner) must survive a Step the same way it
    survives a Count/Spacing change. The 2-vertex removal floor becomes
    `2 + (pin count)`: a pin can never be traded away to reach a target
    that leaves it no room. Positions are tracked as a plain shifting set
    alongside `us` (an insert before a pin's position pushes it right, a
    deletion before it pulls it left) — `chain` itself never changes here,
    only this local bookkeeping does, so pin membership after several
    steps is not re-derived by looking `chain` back up.

    Each step tries EVERY candidate position in heuristic order until the
    cumulative plan validates: the sparsest gap first for an insert; for a
    removal, the most crowded interior vertex first, each with its closer
    survivor side before the other. (The first multi-step version validated
    only one greedy plan and got refused whenever that single pick landed on
    a triangle-covered segment — measured: every "-2" on a CDT-filled seam
    was refused while "-1" twice worked fine. Per-step candidate iteration
    is what the single-step buttons were already doing; this just applies
    it uniformly.) A span where some step has NO valid candidate is refused
    as a whole.
    """
    pin_set = pin_set or set()
    pinned_positions = {k for k, vi in enumerate(chain) if vi in pin_set}
    n_pins = len(pinned_positions)

    floored = False
    if delta < 0:
        avail = len(chain) - 2 - n_pins
        if avail <= 0:
            return [], True, None
        if -delta > avail:
            delta = -avail
            floored = True

    if delta == 0:
        steps = []
        if _steps_safe(bm, matrix, ctx, span, chain, steps, pin_set=pin_set):
            return steps, floored, None
        return None, floored, "skipped_no_safe_edit"

    steps = []
    us = list(ulist)
    if delta > 0:
        for _ in range(delta):
            order = sorted(range(len(us) - 1),
                           key=lambda i: us[i + 1] - us[i], reverse=True)
            chosen = None
            for g in order:
                if _steps_safe(bm, matrix, ctx, span, chain,
                               steps + [("ins", g)], pin_set=pin_set):
                    chosen = g
                    break
            if chosen is None:
                return None, floored, "skipped_no_safe_edit"
            steps.append(("ins", chosen))
            us.insert(chosen + 1, 0.5 * (us[chosen] + us[chosen + 1]))
            pinned_positions = {(p + 1 if p > chosen else p)
                                for p in pinned_positions}
    else:
        for _ in range(-delta):
            ks = sorted((k for k in range(1, len(us) - 1)
                        if k not in pinned_positions),
                        key=lambda k: us[k + 1] - us[k - 1])
            chosen = None
            for k in ks:
                closer_left = (us[k] - us[k - 1]) <= (us[k + 1] - us[k])
                for go_left in (closer_left, not closer_left):
                    if _steps_safe(bm, matrix, ctx, span, chain,
                                   steps + [("del", k, go_left)],
                                   pin_set=pin_set):
                        chosen = (k, go_left)
                        break
                if chosen is not None:
                    break
            if chosen is None:
                return None, floored, "skipped_no_safe_edit"
            k, go_left = chosen
            steps.append(("del", k, go_left))
            del us[k]
            pinned_positions = {(p - 1 if p > k else p)
                                for p in pinned_positions}
    return steps, floored, None


def _collapse_entry(bm, chain, k, flay_list, go_left=None):
    """Merge chain[k] (an INTERIOR vertex, k is never 0 or len(chain)-1) into
    a neighbour via bmesh.ops.pointmerge. `go_left` picks the surviving side
    (True = chain[k-1]); None falls back to whichever neighbour is closer in
    world space. The planner (_plan_span_edit) always passes it explicitly,
    because WHICH side survives decides which faces get reshaped — and it has
    already validated one specific choice.

    `chain` is a list of vertex INDICES -- mutated in place: index k is
    removed and every remaining index is compacted via
    _reindex_after_removal.

    Face COUNT never drops: pointmerge on a chain vertex only ever turns
    one quad into a triangle (measured, see probe_bmesh_ops.py) -- it never
    removes a face outright the way deleting the vertex would. Hand-made-
    ness of the touched faces is read as plain booleans BEFORE the merge
    (see _insert_entry's docstring on why).

    bmesh.ops.pointmerge(verts=[a, b], merge_co=...) keeps `a` (the FIRST
    listed vertex) and removes `b` -- measured directly, 12/12 trials
    across two mesh sizes (probe_pointmerge_survivor.py, 2026-09-01), the
    OPPOSITE of what "pass the vertex you want to keep as merge_co's owner"
    suggests. Passing [doomed, keep] here (matching an earlier version of
    this function) merged into the wrong side every time on the real
    Preview-Filled mesh: chain[k]'s neighbour survived, chain[k] itself
    should have been removed, but the API removed the NEIGHBOUR instead --
    which silently corrupted every later index this call computed from the
    (wrong) assumption, and one whole seam span "teleported" onto a vertex
    hundreds of mm away (see the session's incident: max edge 37.8mm ->
    272.2mm after a single -1). This function still verifies which vertex
    actually survived after the op rather than trusting the argument order
    (in case a future Blender version changes the rule) -- but the PASSED
    order below is deliberately [keep, doomed] to match measured behaviour.

    Returns (touched_hand_made, removed_vi) -- `removed_vi` is the index
    that vanished, IN THE INDEX NUMBERING THAT WAS CURRENT AT THIS MOMENT
    (i.e. before _reindex_after_removal's shift). The caller must record
    these in chronological order across the whole call and replay the same
    shift against every OTHER span's still-original ctx.assign indices
    before reading them, or an earlier span's removal silently makes a
    later span resolve the wrong vertex (or none, since the count only
    shifts by 1 either way, it rarely raises -- it just moves quietly).
    """
    bm.verts.ensure_lookup_table()
    doomed_i = chain[k]
    doomed = bm.verts[doomed_i]
    left_i, right_i = chain[k - 1], chain[k + 1]
    left, right = bm.verts[left_i], bm.verts[right_i]
    if go_left is None:
        go_left = (doomed.co - left.co).length <= (doomed.co - right.co).length
    keep_i = left_i if go_left else right_i
    keep = bm.verts[keep_i]
    merge_co = keep.co.copy()
    touched_hand_made = [_is_hand_made(f, flay_list) for f in doomed.link_faces]
    bmesh.ops.pointmerge(bm, verts=[keep, doomed], merge_co=merge_co)
    if not (keep.is_valid and not doomed.is_valid):
        # Either the measured survival rule reversed (a future Blender
        # version?) or something else went wrong. `del chain[k]` below
        # assumes doomed_i is what's gone -- if that's false, silently
        # continuing would corrupt the chain the same way the original bug
        # did, so refuse instead of guessing.
        raise RuntimeError(
            f"pointmerge(keep={keep_i}, doomed={doomed_i}) left "
            f"keep.is_valid={keep.is_valid} doomed.is_valid={doomed.is_valid} "
            f"-- expected keep to survive and doomed to vanish (measured "
            f"behaviour, see this function's docstring); refusing to guess")
    removed_i = doomed_i
    del chain[k]
    chain[:] = _reindex_after_removal(chain, removed_i)
    return touched_hand_made, removed_i


def resample_groups_incremental(bm, ctx, groups, targets, matrix):
    """Bring each group's spans to `target` vertices (same target_count
    contract as resample_groups -- one target per GROUP, both sides of a
    partnered pair converge to it even if they started mismatched),
    WITHOUT DELETING, FLIPPING, OR DEGENERATING A FACE.

    "Phase 2" of the Adjust Density redesign (2026-09-01 design review,
    case B: local subdivide/pointmerge edits instead of resample_groups'
    delete-and-rebuild), rebuilt 2026-09-02 as plan -> validate -> execute
    after the first version was measured deleting boundary triangles (see
    the block comment above _signed_area_xy for the incident and the
    reasoning). Both sides of a sewn pair move together, one group = one
    partnered pair, same contract as resample_groups/even_out_groups.

    Per span: candidate steps are picked by 1D crowding/gap heuristics
    (+1 subdivides the sparsest stretch, -1 merges out of the most crowded
    one), but a candidate only survives if _steps_safe proves every
    affected face keeps >= 3 corners, its orientation, >= 5% of its area,
    and no self-intersection -- otherwise the next candidate position is
    tried, and a span with no safe candidate at all is REFUSED
    (skipped_no_safe_edit) rather than damaged. Groups are atomic: if any
    side of a pair has no safe plan, the whole group is skipped, so the two
    sides of a seam never end up at different counts. After a span's steps
    run, its whole chain is re-placed at uniform u (same as
    even_out_groups) so repeated presses don't let spacing drift.

    Faces are never deleted, but existing faces DO get reshaped (a quad
    next to an inserted vertex becomes a pentagon; a vertex removal
    triangulates one corner) -- this is not "nothing changes", it's "the
    covering never has a hole in it". A touched face that carries none of
    the tool fill-layer flags (preview_fill / panel_regions / patch_grid --
    see _tool_face_layers) is presumed hand-cut by the user and is counted
    in stats["hand_made_faces_touched"], NOT blocked -- warn, don't refuse
    (the user asked for "never deletes", not "never touches").

    Targets should be computed against chain_counts, not ctx.count -- the
    raw assignment is padded by knife-cut spurs near the outline, which
    inflates the DELTA baseline (measured; see chain_counts' docstring).

    Returns a stats dict: spans, inserted, removed, skipped_ambiguous,
    skipped_broken_chain, skipped_at_floor, skipped_no_safe_edit,
    hand_made_faces_touched, skipped_ungenerated_partner,
    stopped_face_loss, fallback_groups. The last is [(group, target), ...]
    for every group refused as skipped_no_safe_edit specifically (not the
    other skip reasons, which a destructive rebuild wouldn't resolve
    either) -- Count/Spacing's caller uses this to retry just those groups
    through resample_groups (see AC9_OT_ResampleSpanDensity.execute's
    two-phase call, 2026-09-02: try the non-destructive path everywhere
    first, since it is now what Step already proved safe, and fall back to
    the destructive rebuild only where no safe incremental plan exists —
    e.g. a fan of interior faces all sharing one hub vertex, where ANY
    vertex count change degenerates some triangle no matter which vertex
    moves where).
    """
    inv = matrix.inverted()
    from ..clo_projector.core import SHAPEKEY_NAME
    shape_layers = bm.verts.layers.shape
    basis_layer = shape_layers.get("Basis")
    proj_layer = shape_layers.get(SHAPEKEY_NAME)
    flay_list = _tool_face_layers(bm)

    stats = {"spans": 0, "inserted": 0, "removed": 0,
             "skipped_ambiguous": 0, "skipped_broken_chain": 0,
             "skipped_at_floor": 0, "skipped_no_safe_edit": 0,
             "hand_made_faces_touched": 0,
             "skipped_ungenerated_partner": 0, "stopped_face_loss": 0,
             "fallback_groups": []}

    # A removal in an EARLIER span (within this same call) compacts every
    # vertex index above it by 1 -- including indices ctx.assign recorded
    # for a LATER span that hasn't been touched yet. Recorded in
    # chronological order (each entry is the index that vanished, in the
    # numbering current at that moment) and replayed against every span's
    # raw ctx.assign indices before use -- see _collapse_entry's docstring.
    removed_so_far = []

    def _adjust(vi):
        for r in removed_so_far:
            if vi == r:
                return None  # this exact vertex is the one that got merged away
            if vi > r:
                vi -= 1
        return vi

    for group, target in zip(groups, targets):
        # A group whose partner span has 0 assigned verts (its side of the
        # seam hasn't been generated yet) drives target_count's DELTA
        # baseline to an extreme, unintended value -- measured: a healthy
        # 7-vertex span paired with an empty partner got target=2 from a
        # single "-2" press (min(7,0)=0, then max(2, 0-2)=2), i.e. "strip
        # every interior vertex". Skip the whole group up front: there's no
        # meaningful density-matching to do against a span that doesn't
        # exist in the retopo yet.
        if any(len(ctx.assign.get(si, ())) == 0 for si in group):
            stats["skipped_ungenerated_partner"] += len(group)
            continue

        # ── Phase 1: plan and validate EVERY span in the group ────────────
        # Nothing mutates until the whole group is proven safe, so a pair
        # whose one side can't be edited safely is skipped as a unit and
        # its two sides keep matching counts.
        plans = []
        fail_key = None
        for si in group:
            span = ctx.spans[si]
            if not anch_span_ok(span, ctx):
                fail_key = "skipped_ambiguous"
                break
            raw_entries = sorted(ctx.assign.get(si, ()))
            if len(raw_entries) < 2:
                fail_key = "skipped_broken_chain"
                break
            adjusted = [(u, _adjust(vi), d) for u, vi, d in raw_entries]
            if any(vi is None for _u, vi, _d in adjusted):
                fail_key = "skipped_broken_chain"
                break
            # A PRIOR group in this same call may have inserted/removed
            # verts, which leaves bm.verts[i] index lookups stale until
            # re-synced -- measured: without this, the next span's chain
            # silently resolved to a DIFFERENT (already-freed) BMVert,
            # raising ReferenceError deep inside _uniform_replace.
            bm.verts.ensure_lookup_table()
            chain = _span_chain(bm, adjusted)
            if chain is None:
                fail_key = "skipped_broken_chain"
                break
            # Per-SPAN delta, not per-group: a mismatched pair (partner
            # sides started at different counts) converges on the shared
            # target -- each side just needs its own number of steps.
            delta = target - len(chain)
            # u per CHAIN vertex (the path may exclude spur entries and may
            # include a bridged junction vertex the assignment missed, so
            # this is looked up per vertex with a positional fallback, not
            # taken from entries order).
            umap = {}
            for u, vi, _d in adjusted:
                umap.setdefault(vi, u)
            ulist = [umap.get(vi, i / (len(chain) - 1))
                     for i, vi in enumerate(chain)]
            # Read fresh every span, not once for the whole call: an
            # earlier span's removals in THIS SAME call renumber every
            # vertex above them (see _adjust), and re-deriving the pin set
            # from the bmesh's own current layer state sidesteps having to
            # replay that same shift against a stale set by hand.
            pin_set = density_pin.pinned_indices_bm(bm)
            steps, floored, err = _plan_span_edit(bm, matrix, ctx, span,
                                                  chain, ulist, delta,
                                                  pin_set=pin_set)
            if steps is None:
                fail_key = err
                break
            plans.append({"si": si, "span": span, "chain": chain,
                          "steps": steps, "floored": floored,
                          "marker": len(removed_so_far)})
        if fail_key is not None:
            stats[fail_key] += len(group)
            if fail_key == "skipped_no_safe_edit":
                stats["fallback_groups"].append((group, target))
            continue

        # ── Phase 2: execute the proven plans ─────────────────────────────
        for p in plans:
            chain = p["chain"]
            # Removals executed for EARLIER plans in this group happened
            # after this plan's chain indices were resolved -- replay the
            # same compaction against them (partner sides sit on different
            # panels, so another span's removal is never a vertex of this
            # chain and no index here can vanish).
            for r in removed_so_far[p["marker"]:]:
                chain[:] = _reindex_after_removal(chain, r)
            if p["floored"]:
                stats["skipped_at_floor"] += 1
            for step in p["steps"]:
                if step[0] == "ins":
                    touched = _insert_entry(bm, chain, step[1], flay_list)
                    stats["hand_made_faces_touched"] += sum(touched)
                    stats["inserted"] += 1
                else:
                    _kind, k, go_left = step
                    faces_before = len(bm.faces)
                    touched, removed_vi = _collapse_entry(
                        bm, chain, k, flay_list, go_left=go_left)
                    removed_so_far.append(removed_vi)
                    stats["hand_made_faces_touched"] += sum(touched)
                    stats["removed"] += 1
                    if len(bm.faces) < faces_before:
                        # Backstop only: _steps_safe already refused every
                        # face-deleting candidate, so this firing means the
                        # simulation and bmesh disagreed -- stop this span
                        # rather than compound whatever that is, and report
                        # it loudly instead of pretending nothing happened.
                        stats["stopped_face_loss"] += 1
                        break

            bm.verts.ensure_lookup_table()
            _uniform_replace(bm, chain, p["span"], ctx, inv, basis_layer,
                             proj_layer,
                             pin_set=density_pin.pinned_indices_bm(bm))
            bm.verts.ensure_lookup_table()
            for vi in chain:
                bm.verts[vi].select_set(True)
            stats["spans"] += 1
    return stats


def anch_span_ok(span, ctx):
    """Reject a span that folds back on itself in the flat layout.

    Such a span (a zero-width slit) has no unique parameter for a position, so
    the retopo-to-span assignment underneath this whole feature is unreliable
    there. Same guard the generator uses.
    """
    return rss.span_is_unambiguous(span, ctx.basis_co, ctx.flat_co)
