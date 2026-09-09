# 02. Installation and setup

## Requirements

| Item | Value |
|---|---|
| Blender | 5.0.0 or later (`blender_version_min` in `blender_manifest.toml`) |
| Version | 1.0.0 |
| License | GPL-3.0-or-later |
| Extra libraries | None required (shapely is needed only for some Experimental features) |

## Installation

1. Package the add-on as a ZIP.
2. In Blender, go to **Edit > Preferences > Add-ons**.
3. Choose **Install from Disk** from the menu in the top right, and point it at the ZIP.
4. Enable **AC9 Cloth Retopo** in the list.

Once enabled, an **AC9 Cloth Retopo** tab appears in the 3D viewport sidebar (N key).
Panels are ordered by workflow phase: **Prepare** / **Setup** / **Boundary** / **Faces** / **3D View** / **Guide Maps** / **Overlays** / **Advanced**.

In addition, whenever **Retopo** is set, three items are added to the 3D viewport header.
This is the one deliberately duplicated spot — a place you can hit without scrolling through the sidebar.

- The overlay master switch (eye icon)
- The **Overlays** popover
- **Refresh Mirror**

<!-- screenshot: the AC9 Cloth Retopo sidebar tab (all panels collapsed) and the Overlays / Refresh Mirror controls in the viewport header -->

## Experimental tools switch

Expand **Edit > Preferences > Add-ons > AC9 Cloth Retopo** and there's exactly one switch.

- **Experimental tools** — off by default

Turning it on reveals 7 features in the sidebar that are otherwise hidden:

Preview Fill, Grid Regions, Drape Merge, Guide Separate, Mesh Edit, Quad Fix, Legacy Flip

These are unfinished features, hidden by default. See [05_experimental.md](05_experimental.md) for details.
While it's off, a hint reading "Experimental tools: Edit > Preferences > Add-ons > AC9 Cloth Retopo" appears at the bottom of the **Advanced** panel.

Note that while Experimental is off, the corresponding settings sub-panels (**Face Settings**, **Separation Settings**, **Merge Settings**) don't appear either.

## About shapely

The external library shapely is required by only **two Experimental features**.

- **Preview Fill** (Faces panel)
- **Drape Merge** (a sub-panel of Faces)

The regular, published features (Prepare / Setup / Boundary / Faces' Connect, Connect Rows, Symmetry, Subdivide / 3D View / Guide Maps / Overlays) all work without shapely.

Since shapely isn't bundled with Blender, running either of the two features above without it produces this error message:

```
shapely is required for Preview Fill but could not be imported. Install it into Blender's Python, e.g.:
  <blender>/python/bin/python -m pip install shapely
```

```
shapely is required for 2D Retopo Merge but could not be imported. Install it into Blender's Python, e.g.:
  <blender>/python/bin/python -m pip install shapely
```

Replace `<blender>` with your Blender install location. The Python bundled with Blender 5.0 is in the 3.11 series (measured on Blender 5.0.1: 3.11.13).
numpy is bundled with Blender, so there's no need to install it separately.
