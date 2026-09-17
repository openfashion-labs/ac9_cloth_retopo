# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version number is the one in `blender_manifest.toml`; each release is tagged
`vX.Y.Z` and published on the GitHub Releases page.

## [Unreleased]

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

[Unreleased]: https://github.com/openfashion-labs/ac9_cloth_retopo/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/openfashion-labs/ac9_cloth_retopo/releases/tag/v1.0.0
