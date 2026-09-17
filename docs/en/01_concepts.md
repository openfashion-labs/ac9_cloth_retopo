# 01. Concepts and terminology

## What this tool is for

Garments made in CLO / Marvelous Designer become dense triangulated meshes when exported.
They aren't usable in a product as-is, so you have to rebuild a low-poly retopo mesh.

This add-on is a set of tools for doing that rebuild **not on top of the 3D draped shape, but on top of the flattened pattern layout.**
On the pattern, knife cuts and density adjustments behave predictably, and you can decide vertex correspondence with the sewn-together side purely from 2D position.
The 3D shape is obtained by projecting the flat vertices onto the Guide's triangles using barycentric coordinates.

## Terminology

### Guide

The high-poly garment mesh exported from CLO / MD. This is the "original" that the add-on reads.

- The Basis shape key is the **3D draped shape**
- The shape key specified as **Flat SK** is the **UV-unwrapped flat shape**

In other words, a single object carries both 3D and 2D coordinates.
Every analysis reads the Guide, but nothing writes to it from the analysis side (with a few exceptions such as **Bake Island Colors**, which adds display-only colors or materials to the Guide).

The CLO Projector requires a Guide made only of triangles (`validate_guide` rejects it if it contains n-gons or quads).
That's why **Create Flat SK** triangulates by default before unwrapping.

### Retopo

The low-poly mesh you're building. This is the object every tool in the sidebar operates on.

**Retopo is always handled flat (2D).** Adding, removing, and moving vertices all happens on the plane.

### Flat SK

A shape key whose vertex coordinates are simply the UV layout. It's named `<UV name>_Flattened`, e.g. `UV_Map_Flattened`.
**Create Flat SK** splits the mesh at UV island boundaries and writes `(u, v, 0)` to each vertex (it shows at value 1.0 right after being created).

Since UVs range from 0 to 1, this plane sits within a **0-to-1-meter square area**. The unit is meters.
Keep the Guide's Solidify modifier **live** — if you apply it, that mesh can no longer be used as the Guide for retopo.

### Mirror

A **view-only 3D display** of the flat Retopo, projected onto the Guide.

- It's built as a separate object with 1:1 topology with the Retopo (same vertex indices and faces)
- It is rebuilt from scratch every time you press **Refresh**
- Any manual edits to it are silently overwritten on the next **Refresh**. Only the **vertex selection state** is read back (for the Selection Link overlay)

In code, the object name is `<Retopo name>_AC93DMirror`, and it is linked back to the Retopo through a custom property.
Note that the UI wording refers to it as `AC9_3D_Mirror`, but the object actually created is named `<Retopo name>_AC93DMirror` as above. Look for that in the Outliner.

### Final

The deliverable produced by **Finalize**: a plain mesh object named `<Retopo name>_Final`.

- Shape = the 3D coordinates projected onto the Guide
- UV = the Retopo's own flat layout (so shape and UV never disagree)
- No shape keys, no `ac9_*` data

The Retopo itself is left untouched (still flat, still editable). You can run this as many times as you like.

### Dihedral angle

How sharply two faces meet across the edge between them.
**0deg is flat** (the two faces lie in the same plane); the sharper the fold, the larger the angle.

**Crease Min Angle** is a threshold on this angle.

## "2D is authoritative, 3D is a mirror"

![How Guide, Retopo, Mirror and Final relate. Guide is one object holding both the 2D and the 3D coordinates; Retopo is always flat and is the only thing you edit; Mirror is a view-only 3D result rebuilt from Retopo plus Guide on every Refresh; Final is the deliverable.](../images/01_2d_is_truth.png)

The previous approach used a single object that moved between 2D (Basis) and 3D (`AC9_3D_Project`) via a shape key value, and let you edit it in either state.
Editing in 3D meant going through a fragile reverse-projection path (including spike repair for new vertices), and you couldn't see both the 2D layout and the 3D shape at the same time.

The current Mirror approach fixes this in two ways.

1. **Retopo is the single source of truth, and editing only ever happens in 2D.** Editing topology and position is straightforward on a flat plane, and no information is lost.
2. **Mirror is a pure result derived from (the Retopo's 2D state + Guide), rebuilt every time.** It's a viewer — any edits you make there are gone at the next Refresh.

There are two Refresh paths.

- **Object Mode**: forward projection writes the Retopo's `AC9_3D_Project` shape key and attachments, and the Mirror copies those coordinates. Topology comes from `retopo.data.polygons`.
- **Edit Mode (with Retopo active)**: positions and topology are read directly and live from the Retopo's edit-mode bmesh and projected, writing **only the Mirror**. In Edit Mode, the Retopo's mesh and shape keys are never touched (touching them there is what caused the crashes in the old Sync operators).

## Coordinate system

- The working space is the Flat SK space: a 0-to-1 plane where the geometry is the UV. The unit is meters
- Most distance-based settings are specified in mm (Width, Spacing, Scale, Threshold, etc.)
- The Guide's Solidify stays live. **Find Folds** doesn't look at the live Solidify, but its rim comes from the outline already tidied up by the Inset steps, so this isn't an issue
- Mirror and the Bake Preview Plane sit near the world origin. The Preview Plane is placed 5 mm below (z = -0.005) so it doesn't z-fight with the flat Retopo
