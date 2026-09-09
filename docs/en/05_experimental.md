# 05. Experimental features

Features that only appear in the sidebar once **Experimental tools** is turned on in **Edit > Preferences > Add-ons > AC9 Cloth Retopo**. Off by default.

There are 7 of them: Preview Fill, Grid Regions, Drape Merge, Guide Separate, Mesh Edit, Quad Fix, Legacy Flip.

This add-on is public, and roughly half its sub-packages are still being built out. Everything listed here "works, but isn't finished enough to build into the pipeline."
Every one of them gates itself on Experimental in its own `poll()`, so with the switch off they won't run even from an F3 search.

## Preview Fill

The **Preview** row in **Faces** (Object Mode). **Auto Fill** and **×**.

Whether the outline has the right vertex count is only really visible once faces are filled in. This feature lays a disposable quad grid inside each pattern piece, never moves a boundary vertex, flags what it created, and lets **×** undo all of it.
It isn't final topology. The intended workflow is: look, fix the density, run it again. **Adjust Density** clears it itself whenever it needs to rebuild a span.

Unfinished aspects: pattern pieces whose outline isn't closed are skipped, and only the count is reported. **Requires shapely** (errors out without it).
It also overlaps in role with **Grid Regions**, and it's become clear the underlying assumption — that a pattern piece reduces to four corners — doesn't hold on real data (see below).

## Grid Regions

The **Regions** row in **Faces**, the **Grid** button next to **Connect**. **×** is **Clear Regions**.

Grids each region a cut creates. Corners are taken from the cuts: wherever a cut line lands is, by definition, a corner, so most don't need manual flagging.
Structural notches (a stepped hem, the inside of an armhole) are found by measurement and counted as corners too. Regions with fewer than four corners have the shortfall filled in at the sharpest convex bend, and each such fill is reported individually.
Cut lines are subdivided once up front, so the regions on either side of a cut share the same vertices. **The outline is never moved or added to.** In Edit Mode, only the island containing the selection is processed.

Unfinished aspects: the underlying simplification — "a pattern piece = a four-corner patch" — just doesn't hold on real data. Measured against actual Guides, cutting the outline at its corners produced up to 12 edges on the largest two pieces, and no choice of 4 out of those 12 could represent the shape.
Body pattern pieces with an armhole and a stepped hem aren't quadrilaterals. That's why subdivision is left to the user's knife, with automatic gridding applied on top of that. Regions with more or fewer than 4 corners are simply listed and left alone. Turning on **Fill Mismatched Regions** (in **Face Settings**) also fills regions where opposite sides have different vertex counts, absorbing the difference into a single row of triangles — but a region where a short curved edge faces a long one, piling many triangles onto one vertex like a sleeve cap, is rejected and reported instead.

A separate, now-discontinued earlier feature that fully auto-gridded the interior (using none of the user's cuts) exists but is different from **Grid Regions**, which is the successor to **Fill Regions**.

**Fill Regions** (drops a single n-gon into each closed region) and **Patch Grid** (a four-corner-patch grid) are registered but don't appear on any panel (reachable only via F3 search). **Connect** now covers what **Fill Regions** used to do.

## Drape Merge

The **Drape Merge** sub-panel of **Faces** (plus the **Merge Settings** sub-panel).

Embeds a flat drape-strip mesh into a flat grid mesh (the drape wins wherever they overlap). Includes hole detection on the result.
This is a separate entry point from the Guide-based boundary workflow, so it **doesn't read the shared Retopo / Guide — it has its own pair of object pickers.**

| Button | What it does |
|---|---|
| **Drape → Grid** | clips the grid cells the drape crosses (the drape wins), fills the seams, and stitches everything into a single mesh |
| **Holes** | enters Edit Mode and selects the interior boundary loops (holes) on the active mesh. Ignores the outer boundary. No selection means no holes |

If the last merge left holes, a red warning line reports the count. With none, it reads "Last merge: no holes."

Unfinished aspects: **requires shapely**. **Merge Settings**' **Seam Fill** mode branches into **QUAD** / **TRI** / **NGON**, each with its own tuning values (**Carve Cells** / **Quad Angle** / **Split Drape** / **Carve Margin** / **Sliver Factor**).
How to prepare the inputs is still under construction. Roughly: build a separate mesh that follows the drape (the sag), then merge it into the inside of the outline. The procedure will be written once the feature settles.

## Guide Separate

The **Contact** row (**Check** / **Separate** / **×**) and **3D Source** in the **3D View** panel, plus the **Separation Settings** sub-panel.

Opens a minimal gap between layers of the Guide that are touching each other (pleats, wrap-around overlaps), so raycasting bakes don't pick up the neighboring layer.
The result lives in a shape key `AC9_Separated` on the Guide; Basis keeps the original drape. While **3D Source** is set to **Separated**, projection and Guide Maps read that separated shape.

- **Check** only measures and colors (vertex color `AC9_Gap`: red = touching, yellow = under the gap threshold, green = sufficient)
- **Separate** actually creates `AC9_Separated` and automatically switches **3D Source** to **Separated**
- **×** removes `AC9_Separated` and `AC9_Gap`, reverting to Basis

Object Mode only. **Separation Settings** covers **Gap** / **Smooth Radius** / **Max Iterations** / **Include Solidify**.

Unfinished aspects: it can fail to converge. When that happens, you get a warning like "still N contacts under the gap threshold," and if Solidify thickness is in play, an added note that "the thickness likely exceeds the spacing between layers."
The result line also reports the maximum displacement and the p99 normal deviation (degrees), so you need to check numerically how much it distorted the shape.

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

Not so much unfinished as deliberately not recommended. Editing in 3D goes through a fragile reverse-projection path (`run_reverse_projection` plus spike repair for new vertices).
The **Sync** family is also Object Mode only — calling it from Edit Mode forces an Edit→Object→Edit mode switch that carries a heavy mesh rewrite, which can crash Blender on a large Guide.
A red warning line reports the count of any vertices that straddle a UV island boundary.

Note that **Legacy Flip Options** (**Overwrite ShapeKey** / **Clear Failed Group** / **Select Failed**) and **Maintenance** are always shown, even with Experimental off.
