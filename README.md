[日本語版 README はこちら](README.ja.md)

# AC9 Cloth Retopo

![AC9 Cloth Retopo - a garment shown as a low-poly retopology wireframe next to the finished shaded result](docs/images/readme_hero.png)

A Blender add-on for retopologizing garment meshes made in CLO / Marvelous Designer, while staying in the flattened pattern shape (2D).
The 3D shape is obtained by projecting the flat retopo onto the high-poly Guide using barycentric coordinates. Editing always happens in 2D; 3D is a view-only mirror.

- **Supported Blender**: 5.0 or later (tested on 5.0.1, 5.1.1 and 5.2.1 LTS)
- **Version**: 1.0.0
- **License**: GPL-3.0-or-later ([LICENSE](LICENSE))
  Copyright (C) 2026 OpenFashion. This program is free software: you can
  redistribute it and/or modify it under the terms of the GNU General Public
  License as published by the Free Software Foundation, either version 3 of
  the License, or (at your option) any later version.
- **Tested on**: Windows. The add-on itself is pure Python with no platform-specific code, and its one external dependency ships as official PyPI wheels for macOS and Linux as well — but those two have not been run by the author.

## Installation

1. Download `ac9_cloth_retopo-<version>.zip` from **Assets** on the
   [latest release page](https://github.com/openfashion-labs/ac9_cloth_retopo/releases/latest).
2. In Blender, install it via **Edit > Preferences > Add-ons > Install from Disk** and enable it.

> **Source code (zip)**, and **Download ZIP** under the green **Code** button, will not work.
> Both are the source tree alone, without the bundled shapely wheels. Pick the ZIP whose
> name starts with `ac9_cloth_retopo-`.

An **AC9 Cloth Retopo** tab appears in the 3D viewport sidebar (N key).

Nothing else to install: shapely (needed by Preview Fill and Grid Regions) is bundled
as a wheel and Blender installs it for you when the add-on is enabled. Because the
Python version moves with Blender — 5.0 is Python 3.11, 5.1 and 5.2 are Python 3.13 — both sets
of wheels ship, for Windows x64, macOS arm64/x64 and Linux x64/arm64. The one gap is
Windows on ARM, for which no shapely wheel exists on PyPI; there, those two features
say so instead of running.

For developers — to build the ZIP from source yourself:

```
blender --command extension build --source-dir ac9_cloth_retopo --output-dir .
```

## Documentation

English manual: [docs/en/](docs/en/README.md)

| Doc | Summary |
|---|---|
| [01_concepts.md](docs/en/01_concepts.md) | What this tool is for, and the Guide / Retopo / Flat SK / Mirror / Final terminology |
| [02_install.md](docs/en/02_install.md) | Requirements, installation, the Experimental tools switch, and shapely |
| [03_workflow.md](docs/en/03_workflow.md) | The full workflow from CLO export to Finalize |
| [04_panels.md](docs/en/04_panels.md) | Panel-by-panel button reference |
| [05_experimental.md](docs/en/05_experimental.md) | The unfinished Experimental features and why |
| [06_troubleshooting.md](docs/en/06_troubleshooting.md) | Fixes for common stuck points |

## Status

v1.0.0.
Unfinished features only appear once **Experimental tools** is turned on in Add-on
Preferences (off by default): Grid Regions, Align to Outline, Mesh Edit, Quad Fix,
Legacy Flip, and the Density family (Density / Even Out / Count / Spacing / Pins /
Corners). See [05_experimental.md](docs/en/05_experimental.md) for what is unfinished
about each.
