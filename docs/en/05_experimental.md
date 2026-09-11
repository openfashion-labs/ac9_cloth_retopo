# 05. Experimental features

Features that only appear in the sidebar once **Experimental tools** is turned on in **Edit > Preferences > Add-ons > AC9 Cloth Retopo**. Off by default.

There are 7 of them: Grid Regions, Align to Outline, Mesh Edit, Quad Fix, Legacy Flip, UV Mirror (Guide Prep step 5), and the Density family (Density / Even Out / Count / Spacing / Pins / Corners).

This add-on is public, and roughly half its sub-packages are still being built out. Everything listed here "works, but isn't finished enough to build into the pipeline."
Every one of them gates itself on Experimental in its own `poll()`, so with the switch off they won't run even from an F3 search.

## Grid Regions

The **Regions** row in **Faces**, the **Grid** button next to **Connect**. **×** is **Clear Regions**.

Grids each region a cut creates. Corners are taken from the cuts: wherever a cut line lands is, by definition, a corner, so most don't need manual flagging.
Structural notches (a stepped hem, the inside of an armhole) are found by measurement and counted as corners too. Regions with fewer than four corners have the shortfall filled in at the sharpest convex bend, and each such fill is reported individually.
Cut lines are subdivided once up front, so the regions on either side of a cut share the same vertices. **The outline is never moved or added to.** In Edit Mode, only the island containing the selection is processed.

Unfinished aspects: **requires shapely** (errors out without it — see [02_install.md](02_install.md)). Beyond that, the underlying simplification — "a pattern piece = a four-corner patch" — just doesn't hold on real data. Measured against actual Guides, cutting the outline at its corners produced up to 12 edges on the largest two pieces, and no choice of 4 out of those 12 could represent the shape.
Body pattern pieces with an armhole and a stepped hem aren't quadrilaterals. That's why subdivision is left to the user's knife, with automatic gridding applied on top of that. Regions with more or fewer than 4 corners are simply listed and left alone. Turning on **Fill Mismatched Regions** (in **Face Settings**) also fills regions where opposite sides have different vertex counts, absorbing the difference into a single row of triangles — but a region where a short curved edge faces a long one, piling many triangles onto one vertex like a sleeve cap, is rejected and reported instead.

A separate, now-discontinued earlier feature that fully auto-gridded the interior (using none of the user's cuts) exists but is different from **Grid Regions**, which is the successor to **Fill Regions**.

**Fill Regions** (drops a single n-gon into each closed region) and **Patch Grid** (a four-corner-patch grid) are registered but don't appear on any panel (reachable only via F3 search). **Connect** now covers what **Fill Regions** used to do.

## Align to Outline

**Align → To Outline** and **Threshold** in the **3D View** panel. See [04_panels.md](04_panels.md) for details.

Pre-processing for the old **Sync 3D > 2D** workflow: snaps vertices within **Threshold** of the Guide's pattern outline (UV seam edges) onto the edge and re-flags them as boundary.

## Mesh Edit (Collapse Keep UV)

The **Collapse** row in **Faces** (Edit Mode), **Keep UV**.

Collapses the selected edges to their midpoint (the same operation as **Mesh > Merge > Collapse**), but **preserves UVs**. The native Collapse crushes and breaks UV islands.
UVs on every UV layer follow along to the midpoint, and UV seams that cross the collapse stay split.

Unfinished mostly in the sense of not having a proper home. This feature **isn't specific to Cloth.** It's parked here for now, and may move to a shared mesh-tools add-on in the future.

## Quad Fix

The **Select** / **Fix** rows in **Faces** (Edit Mode), and the **Quad Fix** section of **Face Settings**.

Re-splits non-planar quads into triangles along the "correct" diagonal.

| Button | What it does |
|---|---|
| **Select → Clean** / **Saddle** / **All** | deselects everything, then selects only the chosen kind of quad. Lets you see, before fixing, how many of the flagged faces are simple bends versus genuine saddles |
| **Fix → Convex** | triangulates each non-planar quad along the **convex-side diagonal** (the one an artist would choose — bulging outward, matching the rounded surface) |
| **Fix → Alternate** | splits along the other (non-convex) diagonal instead — a preview of "the wrong one": Fix → rotate to look → Undo → Alternate → rotate to look → compare |

Unfinished aspects: which diagonal is actually correct can't be determined by geometry alone — it takes viewpoint-dependent perception, which is why **Alternate** exists as an A/B comparison tool.
**Non-planar Angle** / **Flat Angle** / **Only Selected** / **Fix Saddles** in **Face Settings** change the target set and behavior.

## Legacy Flip

The **Legacy Flip** section of the **Advanced** panel.

The old workflow, where the Retopo itself moved between 2D (Basis) and 3D (the `AC9_3D_Project` shape key), editable in either state.
**2D editing + Refresh Mirror is what's recommended now** (see "2D is authoritative, 3D is a mirror" in [01_concepts.md](01_concepts.md)).

| Button | What it does |
|---|---|
| **Apply → 3D → 2D** | snaps moves made to existing vertices on the Mirror onto the Guide's surface, and rewrites the 2D layout (boundary vertices stay on the CLO outline). **Moves only**: don't add or cut vertices on the Mirror — build new geometry in 2D |
| **Sync → 2D > 3D** | re-projects every 2D vertex onto the Guide's 3D (Basis), writing to the `AC9_3D_Project` shape key |
| **Sync → 3D > 2D** | snaps the Retopo's current 3D shape-key state onto the nearest point of the Guide's 3D, and rewrites the 2D Basis. Updates both the Basis and the shape key |
| **Bind → New Verts** | binds vertices created in 3D Edit Mode (missing attachments, or inconsistent with their 2D position) onto the Guide's surface, and repairs their 2D Basis position. Uses neighboring vertices' attachments to land on the correct fold side of the fabric |
| **Bind → Auto** | runs the above automatically whenever you leave Edit Mode |

Not so much unfinished as deliberately not recommended. Editing in 3D goes through a fragile reverse-projection path (including spike repair for new vertices).
The **Sync** family is also Object Mode only — calling it from Edit Mode forces an Edit→Object→Edit mode switch that carries a heavy mesh rewrite, which can crash Blender on a large Guide.
A red warning line reports the count of any vertices that straddle a UV island boundary.

Note that **Legacy Flip Options** (**Overwrite ShapeKey** / **Clear Failed Group** / **Select Failed**) and **Maintenance** are always shown, even with Experimental off.

## Density (Density / Even Out / Count / Spacing / Pins / Corners)

**Boundary**'s Edit Mode half: the **Density** row (**− 1** / **+ 1** / **Even Out**), the **Count** row (value + **Set**), the **Spacing** row (value + **Apply**), the **Pin** row (**+** / **−** / **×**), and the **Corner** row (**Detect** / **+** / **−** / **×**). Also gated with this family: the **Corners** → **Candidate Angle** tuning value in **Boundary Settings**, and the **Pins** / **Corners** toggles in **Overlays**. See [04_panels.md](04_panels.md) for the full per-button reference.

This whole family went unused in production, and nothing outside it reads what it writes — that's why it moved here.
Don't confuse **Spacing → Apply** (this family, Experimental — sets a span's spacing non-destructively) with **Generate**'s own **Vertices** (`gen_count`) and **Spacing** (`gen_spacing_mm`) settings in **Boundary Settings**: those are a different pair of settings with similar names, used to build the boundary in the first place, and they are **not** Experimental.

## UV Mirror (Guide Prep step 5)

**Guide Prep**'s **5 UV** row: **Reference** / **Pairs** / **Self**.

Copies one side's edited UV layout onto its mirror partner. Left and right pattern pieces are mirror images as 2D patterns but neither in 3D (the simulation drapes each side differently) nor in topology (CLO meshes each piece on its own, so vertex counts differ — measured on 3 of 7 pairs of a production jacket). Blender's own tools fail on exactly those two points: `mesh.faces_mirror_uv` looks for 3D-mirrored vertices and `uv.paste` needs identical island topology. The outlines do match, so the transfer here is geometric: **Reference** keeps a copy of the untouched CLO UV, and a target vertex's reference position is reflected into the source island's reference space and interpolated there with barycentric weights. Topology never has to agree.

**Pairs** does left/right partner islands; **Self** does one cut-on-fold island, keeping the side that holds the selection.

What is unfinished is not the transfer but the step. Steps 1-4 of **Guide Prep** are each one press. This one only makes sense in the middle of a sequence carried out elsewhere: weld the dart edges (**Merge by Distance**), stitch the dart closed in the UV editor on **one** side, relax it (**Unwrap** / **Minimize Stretch**), *then* run this, and re-create the **Flat SK** from the finished UV afterwards. Sitting in the numbered list, it invited being pressed in order, which is not how it works. It has also had little testing compared with the rest of the panel.

Order matters and is not enforced: **Reference** must be made **before** any UV editing, or the reference layer already carries the edits and the match has nothing to measure against.
