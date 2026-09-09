[日本語版 README はこちら](README.ja.md)

# AC9 Cloth Retopo

A Blender add-on for retopologizing garment meshes made in CLO / Marvelous Designer, while staying in the flattened pattern shape (2D).
The 3D shape is obtained by projecting the flat retopo onto the high-poly Guide using barycentric coordinates. Editing always happens in 2D; 3D is a view-only mirror.

- **Supported Blender**: 5.0 or later
- **Version**: 1.0.0
- **License**: GPL-3.0-or-later ([LICENSE](LICENSE))

## Installation

Package it as a ZIP and install/enable it via **Edit > Preferences > Add-ons > Install from Disk**.
An **AC9 Cloth Retopo** tab appears in the 3D viewport sidebar (N key).

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

v1.0.0. Regular features work with no external libraries.
Unfinished features (Preview Fill, Grid Regions, Drape Merge, Guide Separate, Mesh Edit, Quad Fix, Legacy Flip) only appear once **Experimental tools** is turned on in Add-on Preferences (off by default). Of these, Preview Fill and Drape Merge additionally require installing shapely.
