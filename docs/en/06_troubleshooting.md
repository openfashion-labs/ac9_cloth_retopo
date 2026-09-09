# 06. When things don't work

## Red "Seams not analyzed" appears and nothing responds

**Symptom**: **Setup** or **Overlays** shows a red warning, and no overlay draws. Turning on **Snap (G) → Ghost** / **Outline** has no effect.

**Cause**: the seam analysis result isn't in memory. This analysis is the foundation for ghosts, snapping, and the seam overlay — all of it — but **it's cleared when you open the file and on Reload Scripts.**

**Fix**: press **Setup → Analyze Guide** (or the **Analyze** button next to the warning line — same thing).
If **Guide** and **Flat SK** aren't set, you'll see "Set the Guide and Flat SK first" first — fill those in before analyzing.
Note that **Generate** / **Sync** / **Status** recompute the Guide's spans every time they run, so they still work even with this warning showing.

## Inset stops with "needs a planar shape key"

**Symptom**: pressing **Inset Pieces** or **Inset Line** aborts with
`Inset Pieces needs a planar shape key (the UV layout as geometry, e.g. UV_Map_Flattened)`.

**Cause**: that object has no flat shape key (the UV layout as geometry). Steps 2-4 of **Prepare** need the result of step 1.

**Fix**: select that object and run **Create Flat SK** in Object Mode.
**Create Flat SK** applies to every selected mesh. Since a CLO export is usually multiple objects, selecting them all and pressing it once is enough.
Objects with no UV layer are skipped (the result line reads `(N skipped: no UV)`) — create UVs first if that happens.

## The knife (K) can't cut

**Symptom**: after building the outline, the knife won't cut.

**Cause**: the knife cuts faces, and a boundary-only mesh has none.

**Fix**: press **Faces → Regions → Connect**. Every closed region gets filled with faces, and open lines get extended and connected to nearby edges too.
If you only want to fill a few faces by hand, Blender's own **F** (New Edge/Face from Vertices) on the selected vertices also works.
After cutting, pressing **Connect** again refills whatever regions the new cuts created.

## A small stub is left behind after Connect

**Symptom**: after **Connect** runs, there's what looks like a short leftover stub from knife overshoot.

**Cause**: the knife often cuts slightly past the intended edge, leaving a few-millimeter dead-end branch (a stub).

**Fix**: no action needed. **Connect automatically removes stubs under 5 mm, and lists the positions (mm) it removed in the result message.**
Branches 5 mm or longer are treated as intentional lines and left in place. Delete a long branch by hand if you don't want it.
If the result message shows "N end(s) left alone" (ends it couldn't connect) or "N region(s) could not be closed — lines crossing without a vertex?", that's genuine remaining work.

## Pressing Symmetry's Self does nothing

**Symptom**: running **Faces → Symmetry → Self** doesn't change the shape.

**Cause**: the fold axis is still **Auto**, or the wrong axis is selected. A single pattern piece can be symmetric in both directions at once (a waistband is symmetric left-right and top-bottom), and only one of those is the copy you actually intended.
**Auto** picks "whichever side is still asymmetric," and refuses if both sides are equally asymmetric.

**Fix**: explicitly pick **Self axis** before pressing.

- **Vertical axis (mirror left-right)** — folds along the piece's vertical centerline, rebuilding the left half from the right (or vice versa)
- **Horizontal axis (mirror top-bottom)** — folds along the horizontal centerline, rebuilding the bottom half from the top (or vice versa)

**Self** is destructive. Picking the wrong axis produces "destructive, yet nothing visibly changed," so confirm the axis before pressing.
Also, the authoritative side is "whichever side has the selected vertex." Select a vertex on the side you trust before running it.

## A baked map isn't visible

**Symptom**: **Residual** / **Sag** / **Drape**'s **Bake** succeeded, but nothing shows in the viewport.

**Cause**: Solid shading doesn't display textures by default. Also, the preview plane sits **below** the flat retopo (5 mm below).

**Fix**: check things in this order.

1. In **Guide Maps → Preview**, pick the map you want, then press **Plane**. This creates the `AC9_BakePreview` plane, automatically switches the Solid viewport to **Solid color = Texture**, and turns on **X-Ray**.
2. Still nothing? Check whether the **Solid** field in the **Guide Maps** panel is set to **Texture** (the same property as Viewport Shading > Color).
3. Check that X-Ray hasn't been switched back off. Since the plane is below the retopo, the map stays hidden unless the retopo is see-through.
4. **Plane** doesn't touch Material Preview / Rendered shading viewports (they already show it, since it's an Emission material).

<!-- screenshot: Viewport Shading with Color = Texture and X-Ray on -->

Note that bake progress is only shown as staged text. The bake itself doesn't report incremental percentages, so that stretch appears stalled (**Drape** bakes twice internally).

## The Mirror looks stale

**Symptom**: after editing in 2D, the Mirror's 3D shape still looks like the old one.

**Cause**: the Mirror is a derived object rebuilt from (the Retopo's 2D + Guide) every time — it never follows along automatically.

**Fix**: press **3D View → Mirror → Refresh** (the **Refresh Mirror** button in the viewport header does the same thing). Works in either Object Mode or the Retopo's Edit Mode.
Turn on **Auto-refresh** and it runs automatically whenever you leave the 2D retopo's Edit Mode.
That said, if an unapplied move from **Apply 3D Edits → 2D** (Experimental) is sitting on the Mirror, it's skipped with a warning rather than silently discarded — run **Refresh** by hand in that case.

**Subdivide** updates the Mirror automatically if one exists (if that update fails, the message says so).

Any manual edits to the Mirror are gone at the next **Refresh** — it's a viewer. Only the vertex selection state is read back.

## Finalize is grayed out

**Symptom**: **Finalize** is grayed out.

**Cause and fix**: the reason is in the tooltip. It's one of these four:

| Message | Fix |
|---|---|
| Exit Edit Mode first (Tab). | return to Object Mode |
| Set the Retopo first. | set **Retopo** in **Setup** (it must be a mesh object) |
| Set the Guide first. | set **Guide** in **Setup** |
| Set the Guide's Flat SK first. | set **Flat SK** in **Setup** |

Note that **Finalize** never changes the Retopo. It creates a separate object named `<Retopo name>_Final`, and can be run any number of times.

## What does Clear All actually remove?

**Symptom / question**: pressing **Advanced → Clear All AC9 Data** is scary.

**Fix**: pressing it **first shows a dialog listing what will be removed** (nothing changes in the file at this point). You can review it before confirming. Object Mode only.

What gets removed:

- Scene settings (Retopo / Guide / Flat SK and every option — once this is gone, the viewport header buttons disappear too)
- The Mirror object
- The Bake Preview Plane (the `AC9_BakePreview` object, mesh, and material)
- Every `ac9_*` / `AC9_*` attribute layer on every mesh, the `AC9_3D_Project` and `AC9_Separated` shape keys, the `AC9_Project_Failed` vertex group, the `AC9_Island_Colors` material slot
- `ac9_*` custom properties on the scene, objects, and meshes
- Bake images, temporary bake materials, and report text data blocks

**What's left behind: your own geometry, UVs, your own materials, and the Guide's flat shape key.**

There's one exception. A Retopo currently displayed in 3D via the legacy shape key (value > 0.5) would snap back to the 2D layout once the key is removed, so its 3D coordinates are written into the mesh before deletion. Whatever is on screen stays exactly as it was.

If you just want to reset the tuning values, use **Reset to Defaults** instead of **Clear All** — it leaves the Retopo / Guide / Flat SK assignments and meshes untouched.

## Can't find the Experimental buttons

**Symptom**: **Grid** / **Drape Merge** / **Quad Fix** and the like, described in the manual, aren't there.

**Cause**: **Experimental tools** is off (the default).

**Fix**: turn on **Experimental tools** in **Edit > Preferences > Add-ons > AC9 Cloth Retopo**.
The same hint also appears at the bottom of the **Advanced** panel.
Even calling them from F3 search won't work while it's off, since the operators themselves check Experimental.

## Preview Fill / Drape Merge stops with "shapely is required"

**Symptom**: the error message reads `shapely is required for ... but could not be imported.`

**Cause**: shapely isn't bundled with Blender, and only these two features need it.

**Fix**: install it into Blender's Python.

```
<blender>/python/bin/python -m pip install shapely
```

Every other feature works without shapely. See [02_install.md](02_install.md) for details.

## Overlays and projection drifted after editing the Guide

**Symptom**: after sculpting/editing the Guide's vertices, the seam lines and projection still show the old shape.

**Cause**: the Guide's triangle list and 2D BVH are cached. If you change the geometry without swapping out the data block, nothing notices automatically.

**Fix**: **Advanced → Maintenance → Guide Cache → Clear**. It's rebuilt automatically at the next Sync-family operation, and the seam-line and boundary overlay caches are invalidated at the same time.

## Seams and the retopo are completely misaligned

**Symptom**: the seam overlay lines are drawn somewhere entirely different from the retopo.

**Cause**: possibly a coordinate-space mismatch (a `matrix_world` or normalization discrepancy).

**Fix**: run **Advanced → Diagnostics → Seam → Spaces** and look at the three bounding boxes it prints to the system console (seam pairs, retopo boundary vertices, the Guide's flat layout).
If the seam box and the retopo box don't overlap, you're in different coordinate spaces.
To check one specific pair, select a single Retopo vertex and use **Diagnostics → Seam → Vertex**.
