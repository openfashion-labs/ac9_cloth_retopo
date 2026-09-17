# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version number is the one in `blender_manifest.toml`; each release is tagged
`vX.Y.Z` and published on the GitHub Releases page.

## [Unreleased]

### Fixed

- Baking a Guide Map no longer disturbs collections the Guide is not in.
  Getting the Guide into the view layer meant clearing `exclude` up its chain,
  and Blender applies that to the whole subtree — so a collection the user had
  unchecked in the Outliner came back, with its objects still selected, and the
  bake stopped with `Object "..." is not enabled for rendering`. Every
  collection now keeps its own value, restored in the same order, and the bake
  deselects what is in the view layer at that moment rather than what was in it
  before the collections were touched.

## [1.1.0] - 2026-09-17

### Added

- **Subdiv Preview** — a reversible subdivision shown on the 3D Mirror, so a
  denser result can be judged without subdividing the retopo itself. The
  destructive **Subdivide** button stays: the two do different jobs.
- **Selection Link** — vertices selected in the 2D retopo are marked on the
  Mirror, and the other way round. The 2D → Mirror direction needs the Guide
  projection cache, which is cold right after a file is opened until one
  Refresh; the manual says so.
- **Close Seam Gaps** and **Weld Seam Vertices** at Finalize, so the sewn seams
  of the delivered mesh meet in 3D (measured: largest gap 0.857 mm → 0.000 mm).
- **Add Transparent Material** and **Alpha** in Guide Maps, so the preview plane
  5 mm below the retopo reads through the faces being cut, without touching a
  theme colour shared by every file.
- **Curvature Radius** in Guide Maps.
- **Keep Shading** on Create Flat SK, on by default.
- **Orphan Rings** and **Unplaced Lines Only** overlays, which mark the vertex
  whose ghost could not be placed, not only the empty slot it points at.

### Changed

- **Inset Pieces / Inset Line** are a true 2D offset with a constrained
  Delaunay triangulation, instead of the old ring rebuild. Inset Line is greyed
  out until Inset Pieces has run, and the Prepare order is Pieces before Line.
- **Curvature** in Guide Maps is now sampled at a chosen scale (the Guide is
  smoothed with a kernel of **Curvature Radius** and the map reports the offset
  along the normal) rather than Geometry Pointiness, which sank a broad ridge
  below the triangulation's own scatter. The bakes also ignore a Solidify shell
  that is off in the viewport but on in the render.
- **Create Flat SK** splits only where the UV really breaks. A seam flag the UV
  no longer backs up is left whole and reported instead (measured on a welded
  Guide: 1,552 → 3,760 boundary edges becomes 1,552 → 3,292; on another,
  5,494 pointless splits become none).
- **Finalize** drops everything the add-on leaves behind from the delivered mesh:
  its working materials, the mesh-level custom property recording which Guide the
  retopo was bound to, and the weightless marker vertex groups.

### Fixed

- Create Flat SK no longer changes the Guide's shading. Splitting cut the smooth
  fans the custom split normals are stored against, so the same data decoded to
  a different normal and a crease appeared along every seam (measured: up to
  43.672° → 0.052°).
- Apply Subdivide no longer raises `IndexError` when the retopo carries
  attachments recorded against an older Guide.
- Preview Fill no longer skips a panel whose outline vertices were matched to a
  stray millimetre-wide island left by the CLO export.
- Inset Line no longer loses a whole band to a self-intersecting ring
  (measured: 56 edges over 6 mm → 0, longest band edge 9.58 → 4.50 mm).
- The inset regions are cut into cells before triangulation, so the ear-clipping
  fan no longer leaves long edges across a ring (measured: 128 edges over 3 mm
  → 0, worst dihedral 127° → 73°).
- Subdiv Preview refreshes correctly in Edit Mode.
- Documentation: the install instructions never said where to get the ZIP, and the manual
  still told readers to package the add-on themselves — a leftover from before the first
  release. Both READMEs and `docs/{en,ja}/02_install.md` now link to the Releases page and
  spell out that `Source code (zip)` and the green **Code > Download ZIP** button do not
  work, because they omit the bundled shapely wheels.

## [1.0.0] - 2026-09-14

First public release.

### Added

- **UV Seam Guide** — seam analysis of the Guide mesh and opposite-side ghost display.
- **CLO Retopo Projector** — 2D ↔ 3D barycentric projection between the flat retopo and the CLO / Marvelous Designer Guide, with a view-only 3D Mirror.
- **Guide Maps** — bake residual / sag / drape maps from the Guide.
- **Guide Separate** — pull apart self-touching drape layers before projection.
- **CLO Cleanup** — pre-bake preparation of CLO exports (Find Folds, Inset Line, Inset Pieces, UV Mirror).
- **Finalize** — bake the retopo's 2D layout and Guide projection into one plain, self-contained mesh.
- **Clear All** and **Reset Settings** — remove every trace the add-on leaves in a .blend, or reset tuning values.
- Japanese (ja_JP) UI translation.
- User manual in English and Japanese (`docs/en`, `docs/ja`).
- Bundled `shapely` wheels for Blender 5.0 (Python 3.11) and 5.1+ (Python 3.13) on Windows x64, macOS arm64 / x64 and Linux x64 / arm64.

### Experimental (off by default, enabled in Add-on Preferences)

- Grid Regions, Align to Outline, Mesh Edit, Quad Fix, Legacy Flip and the Density family
  (Density / Even Out / Count / Spacing / Pins / Corners). See `docs/en/05_experimental.md`.

[Unreleased]: https://github.com/openfashion-labs/ac9_cloth_retopo/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/openfashion-labs/ac9_cloth_retopo/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/openfashion-labs/ac9_cloth_retopo/releases/tag/v1.0.0
