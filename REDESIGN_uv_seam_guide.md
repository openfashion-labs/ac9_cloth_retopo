# UV Seam Guide — Redesign Spec (2026-06-09)

## Why redesign

The UV Seam Guide was originally a standalone addon that assumed:
`ghost_position = source_mesh.UV × guide_scale`

When merged into `nk_cloth_retopo`, this assumption was never reconciled with
the CLO Retopo Projector's world, where **the retopo lives in the Guide's
Flat-ShapeKey space**, not the source mesh's raw UV space.

A single scalar `guide_scale` cannot reconcile two different normalizations
(FlattenUV's 0–1m layout vs raw UV 0–1, plus any per-axis scale/offset). The
result: only retopo verts that happen to land within `max_distance_uv` of a
seam segment get a ghost. Others silently get none. Raising the threshold only
masks it. This is the root cause of "一部のゴーストが欠ける."

Also: green boundary crosses (Projector) and magenta ghosts (Seam Guide) are
two overlapping vertex-marker overlays in different coordinate spaces →
user confusion. (Note: green = retopo vert; magenta = opposite-island point.
They are *meant* to differ, but the spaces must still match.)

## The design

**One guide, one coordinate space, no scalar mapping.**

### Single source of truth
- Use the **same Guide object the CLO Projector uses**:
  - Basis ShapeKey = 3D garment shape
  - Flat SK = 2D flattened layout (the space the retopo is built in)
- Drop the Seam Guide's own `source_mesh`, raw-UV analysis, and `guide_scale`.

### Seam pair detection (already implemented, keep)
- CLO UV-flatten physically splits the mesh at seams → seam edges become
  open boundary edges.
- Pair boundary edges that are **coincident in 3D** (equal Basis positions).
  This is `analysis._find_seam_pairs_split_mesh`, which already works (3845
  pairs found on the test jacket).
- Read each paired edge's endpoints in **Flat-SK space** (NOT raw UV).
  Flat-SK space == retopo space, so no conversion is needed downstream.

### Ghost computation
- retopo boundary vert (Flat-SK space) → nearest seam segment → opposite
  island's matching point (Flat-SK space) → draw ghost there directly.
- No `÷ guide_scale`, no `× guide_scale`. Distances are already in the
  correct space, so `max_distance_uv` becomes a real, meaningful threshold.

### Performance (must port from standalone)
- The standalone version had a **KDTree fast path** for ghost lookup
  (`build_pair_kd` + `find_n` candidates). The multi-file version still does
  O(N×M) brute force (3719 verts × 3845 pairs ≈ 14M ops). Port the KDTree.
- Target scale: 1.2M-poly CLO source.

## Migration notes
- `properties.py`: remove `source_mesh`, `guide_scale`; the Guide pointer +
  Flat-SK name should come from the shared Projector props (`top.proj`).
- `analysis.py`: keep `_find_seam_pairs_split_mesh`; have it read Flat-SK
  coords for endpoints (currently reads UV map — change to Flat SK, or accept
  that flat layout == UV and confirm they're identical for this pipeline).
- `ghost.py`: positions already returned in local (Flat-SK) space — good.
  Remove scale division in `find_opposite_points_for_retopo_vertices`.
- `gpu_overlay.py`: `_uv_to_3d` and all `× scale` usages collapse to identity.
- UI: drop `source_mesh` / `guide_scale` fields; reference the Projector Guide.

## CONFIRMED (2026-06-09)
The retopo's flat layout is in **FlattenUV normalized 0–1m space** — the SAME
space the CLO Projector's Flat SK uses. This means:

- `guide_scale` is unnecessary. The whole "× scale / ÷ scale" mapping should
  be removed; retopo positions are already in the canonical flat space.
- Seam-pair endpoints MUST be read in this same FlattenUV 0–1m space, NOT raw
  UV (0–1). If `_find_seam_pairs_split_mesh` currently reads the raw UV map,
  that is the residual mismatch causing missing ghosts. Switch it to read the
  guide's **Flat SK coordinates** (the FlattenUV layout), which is the only
  space guaranteed to match the retopo.
- Confirm: the guide whose Flat SK defines this space is the CLO Projector
  Guide (currently UNSET in the user's scene — must be set up first, or the
  source high mesh must carry an equivalent FlattenUV ShapeKey).

## Status
- Seam-pair detection via 3D-coincident boundary edges: DONE, verified.
- Flat-SK redesign: IMPLEMENTED (2026-06-09).
  - `analysis.find_seam_pairs_flat(guide_obj, flat_sk_name)` — boundary edges
    grouped by Basis world position, endpoints read from the Flat SK in world
    space. Replaces the raw-UV / source_mesh path entirely.
  - `ghost.py` — world-space retopo positions, `build_pair_kd` + KDTree fast
    path (find_n over segment midpoints), no scale division.
  - `gpu_overlay.py` — `_flat_to_3d` (identity XY, Z=overlay height); pairs/
    ghosts/snap-rings all drawn in world space; KDTree cached in `_cache`.
  - `operators.py` — Guide resolved from `top.proj` (guide_obj + Flat SK);
    ghost-snap modal converts between world (ghost) and local (bmesh) via
    matrix_world.
  - `properties.py` / `ui.py` — `source_mesh` and `guide_scale` removed; UI
    shows the shared Projector Guide + Flat SK (or a warning if unset).
- PENDING / verify in Blender: requires the Projector Guide to be set with a
  valid Flat SK in the scene. Confirm ghost coverage on the test jacket
  (3845 pairs) now that endpoints are in FlattenUV 0–1 m space.
