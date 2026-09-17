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

**Cause**: the Guide has no flat shape key (the UV layout as geometry). Steps 2-5 of **Prepare** need the result of step 1.

**Fix**: point **Guide** at that object in **Setup** and run **Create Flat SK** with the Guide in Object Mode. Every button in **Prepare** works on the Guide, so a CLO export that came in as several objects is prepared one object at a time, pointing **Guide** at each in turn.
A Guide with no UV layer is refused with a warning — create UVs first if that happens.

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

**Cause**: the wrong fold axis is selected, or it is set to **Auto** (which is no longer the default — **Self axis** now starts at the horizontal fold, because most pattern pieces are near enough to a rectangle that **Auto** finds both folds equally asymmetric and refuses). A single pattern piece can be symmetric in both directions at once (a waistband is symmetric left-right and top-bottom), and only one of those is the copy you actually intended.
**Auto** picks "whichever side is still asymmetric," and refuses if both sides are equally asymmetric.

**Fix**: explicitly pick **Self axis** before pressing.

- **Vertical axis (mirror left-right)** — folds along the piece's vertical centerline, rebuilding the left half from the right (or vice versa)
- **Horizontal axis (mirror top-bottom)** — folds along the horizontal centerline, rebuilding the bottom half from the top (or vice versa)

**Self** is destructive. Picking the wrong axis produces "destructive, yet nothing visibly changed," so confirm the axis before pressing.
Also, the authoritative side is "whichever side has the selected vertex." Select a vertex on the side you trust before running it.

## Subdivide didn't add any wires to some faces

**Symptom**: after **Subdivide**, most faces split into four, but a few regions get no new wireframe at all —
and because everything around them got finer, they read as "the edges vanished right here."

**Cause**: those regions are n-gons (faces with more than four vertices). Blender's Edit-Mode Subdivide is an
*edge* operator: **it splits an n-gon's outline but never puts a single edge inside it**
(measured: a 146-gon becomes a 292-gon, same area). Only quads and tris are divided internally, so the n-gons
are left behind. This differs from the Subdivision Surface modifier (Catmull-Clark), which does split n-gons.

**Fix**: nothing is broken — carry on. **Subdivide passes n-gons through on purpose**, so that one unfinished
pattern piece can't stop you from checking the silhouette and the poly count of everything else.
The number left is reported in Subdivide's result message and on the **Faces** panel's result row
(`20 n-gon(s) left whole`). **Finalize** reports the same count, but never blocks on it.

To actually divide an n-gon, cut the region with the knife (K) and press **Connect**.
They are not divided automatically because the obvious method — one vertex in the middle — would put
146 edges on a single pole for a 146-gon, which is unusable as retopology.

To find them, in Edit Mode use **Select → Select All by Trait → Faces by Sides**, with
Number of Vertices 4 and the type set to **Greater Than**.

## A baked map isn't visible

**Symptom**: **Residual** / **Sag** / **Drape**'s **Bake** succeeded, but nothing shows in the viewport.

**Cause**: Solid shading doesn't display textures by default. Also, the preview plane sits **below** the flat retopo (5 mm below).

**Fix**: check things in this order.

1. In **Guide Maps → Preview**, pick the map you want, then press **Plane**. This creates the `AC9_BakePreview` plane and switches a Solid viewport to **Solid color = Texture**.
2. Still nothing? Check whether the **Solid** field in the **Guide Maps** panel is set to **Texture** (the same property as Viewport Shading > Color).
3. **To see through the retopo, press **Add Transparent Material**.** The plane sits 5 mm below z = 0, so the flat retopo at z = 0 comes in front of it when you look down from above and hides the map completely — the giveaway is seeing the panel shapes filled solid white. That button makes the retopo semi-transparent, and the **Alpha** slider sets how much.
4. **Hide anything else lying flat at z = 0, by hand** (the Guide in its Flat SK pose, a hand-made bake board). The **Plane** button deliberately does not touch other objects' visibility.
5. Check that the **Retopology** overlay (Overlays > Mesh Edit Mode > Retopology) is **off**. While it is on, the edit-mesh faces are repainted in the theme's colour (measured alpha 0.502), which **overrides the ghost's transparency**. Pressing **Plane** switches it back off for you. X-Ray is not used either.
6. **Plane** doesn't touch Rendered shading viewports (they already show it, since it's an Emission material).
7. Right after reopening the file, **AO** and **Curvature** are empty unless **Keep Passes** was enabled; these temporary passes are not packed. The combined **Drape** map is kept when **Keep in file** is enabled (the default). Re-bake only when you need the passes again or changed the settings.

**If Add Transparent Material stops with "already carries ..."**: the retopo has another material on it. Which slot is which is your business, so this tool stops rather than rearranging them. Remove that material and press again, or work without the ghost.

<!-- screenshot: Viewport Shading with Color = Texture, and the Drape map read through a semi-transparent retopo -->

Note that bake progress is only shown as staged text. The bake itself doesn't report incremental percentages, so that stretch appears stalled (**Drape** bakes twice internally).

## Ridges (convex folds) don't show in the Drape map

**Symptom**: a fold that is clearly a ridge reads the same grey as its surroundings in the Curvature / Drape map. Only concave folds show.

**There were two causes, both fixed in 1.0.0.**

1. **The Guide carried a Solidify modifier.** A Guide out of CLO often has one for thickness, and when it is **off in the viewport but on in the render**, only the bake sees it — and it sees the **inner** face of that thickness, whose UVs sit exactly on top of the outer one. Measured: 91.2% of covered texels came off the inner shell, the Curvature pass was a bit-exact mirror about 0.5 (**ridges black, folds white**) and the AO pass was anti-correlated with the real one. Shell modifiers are now suspended for the duration of the bake.
2. **Curvature was Geometry Pointiness.** Pointiness only looks at a vertex's immediate neighbours, so a broad ridge sinks below the triangulation's own scatter (measured: 0.0127 mm of signal along the normal against 0.245 mm of scatter across it). Curvature now measures relief at the scale set by **Curvature Radius**, and a ridge comes out as a white band.

**Still not showing**: match **Curvature Radius** (Guide Maps) to the width of the ridge. Anything much wider than the radius is smoothed away together with the reference. For a ridge too broad for the 8 mm default, try 12–20 mm; for fine creases, 3–5 mm.

## Baking normals / AO externally picks up the pattern of a different surface

![A normal map of a ruffle. Before Separate, the layer behind it is baked in as yellow streaks; after Separate they are gone.](../images/04_separate_before_after.gif)

**Symptom**: after running Separate, a bake onto the retopo still shows the neighbouring layer's detail in the normal map, and the AO is black where it should be open. Re-running Separate or raising Gap barely changes it.

There are three causes and **none of them are on the Guide's side** -- they are all bake settings. Raising Gap does not fix them.

### 1. AO counts every other object as an occluder

Selected to Active for normals only hits the high-poly you selected, but **AO rays hit everything in the scene that is visible to renders**: the avatar body underneath, the trousers under the skirt, the other garments -- and any leftover duplicate of the bake target.

Measured on a production file: within 4 mm of the skirt's surface there were 1,223 sample points of body, 3,154 of trousers and around 800 of other garments. On top of that a duplicate of the bake target (named like `..._Final.001`) had been left visible to renders, sitting **0.0-0.3 mm** off the surface and blocking nearly every ray.

**Fix**: **when baking AO, hide everything from renders except the source Guide and the target retopo** (the camera icon in the Outliner). Hiding in the viewport is not enough -- rays follow render visibility. **Check for leftover duplicates such as `_Final.001`.**

### 2. The cage extrusion is too large, or too small

Rays start from the low-poly pushed outward along its normal, so **an extrusion larger than the spacing between layers sails past the near layer and lands on one behind it**. At 0 the opposite happens: wherever the low-poly sits inside the high-poly the ray starts inside it and misses the surface it should have hit.

Measured on fabric with layers a few millimetres apart (2,856 retopo faces):

| extrusion | hits on a different surface |
|---|---|
| 0 mm | 80 |
| 0.5 mm | 31 |
| 1 mm | 17 |
| **2 mm** | **5** |
| 5 mm | 167 |
| 10 mm | 559 |
| 100 mm | 1,135 |

**The valley is narrow, and its floor is around half the gap.** Some bake add-ons default to 0.1 m -- 100 mm -- which is far too much for cloth.

### 3. The ray has no maximum length

Without a limit, a ray that grazes past its own surface carries on across the garment and strikes something far away (318 mm, measured). **With a limit it simply finds nothing, and the bake margin fills that pixel from its neighbours -- blank is safer than confidently wrong.** In the measurements above, capping the ray anywhere between 4 and 20 mm took the remaining 5 wrong hits to 0.

**In short**: extrusion around half the gap (2 mm for a 4 mm gap), ray length two or three times that. The fields are in metres, so type `0.002` and `0.006`.

## Selection Link markers don't appear

**Symptom**: with both the Retopo and the Mirror in Edit Mode, selecting vertices shows no orange markers on the other object — or only sometimes.

**Cause**: two things. (1) Markers follow the **active object's** selection only (the one selected last): Mirror active gives Mirror → 2D only, Retopo active gives 2D → Mirror only. (2) The 2D → Mirror direction uses the Guide projection cache, which is **empty right after opening a file**. One **Refresh** (or any projection) fills it; editing or swapping the Guide empties it again. The Mirror → 2D direction only reads the 2D positions recorded at Refresh time, so it always shows.

**Fix**: make the object you want to select on the active one (Ctrl+click it last). If 2D → Mirror shows nothing, press **3D View → Mirror → Refresh** once.

## Hidden geometry (H) doesn't hide on the Mirror

**Symptom**: pressing **H** in the Retopo's Edit Mode leaves the Mirror fully drawn.

**Cause**: Blender only draws geometry as hidden in Edit Mode; a Mirror in Object Mode carries the flags but looks unchanged. Also, **while Subdiv Preview is on the sync itself is skipped** (the subdivided Mirror has no vertex correspondence with the Retopo).

**Fix**: select both the Retopo and the Mirror and press **Tab** (both in Edit Mode). Turn Subdiv Preview off, or apply **Subdivide** to the Retopo first. The sync is one way, Retopo → Mirror; hiding on the Mirror does not travel back.

## The Mirror looks stale

**Symptom**: after editing in 2D, the Mirror's 3D shape still looks like the old one.

**Cause**: the Mirror is a derived object rebuilt from (the Retopo's 2D + Guide) every time — it never follows along automatically.

**Fix**: press **3D View → Mirror → Refresh** (the **Refresh Mirror** button in the viewport header does the same thing). Works in either Object Mode or the Retopo's Edit Mode.

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
- Every `ac9_*` / `AC9_*` attribute layer on every mesh, the `AC9_3D_Project` and `AC9_Separated` shape keys, the `AC9_Project_Failed` vertex group, the `AC9_Island_Colors` and `AC9_RetopoTransparent` (formerly `AC9_RetopoGhost`) material slots
- `ac9_*` custom properties on the scene, objects, and meshes
- Bake images, temporary bake materials, and report text data blocks

**What's left behind: your own geometry, UVs, your own materials, and the Guide's flat shape key.**

There's one exception. A Retopo currently displayed in 3D via the legacy shape key (value > 0.5) would snap back to the 2D layout once the key is removed, so its 3D coordinates are written into the mesh before deletion. Whatever is on screen stays exactly as it was.

If you just want to reset the tuning values, use **Reset to Defaults** instead of **Clear All** — it leaves the Retopo / Guide / Flat SK assignments and meshes untouched.

## Can't find the Experimental buttons

**Symptom**: **Grid** / **Quad Fix** / **Density** and the like, described in the manual, aren't there.

**Cause**: **Experimental tools** is off (the default).

**Fix**: turn on **Experimental tools** in **Edit > Preferences > Add-ons > AC9 Cloth Retopo**.
The same hint also appears at the bottom of the **Advanced** panel.
Even calling them from F3 search won't work while it's off, since the operators themselves check Experimental.

## Preview Fill / Grid Regions stops with "shapely is required"

**Symptom**: the error message reads `shapely is required for ... but could not be imported.` (Preview Fill shows this as a warning row instead of a button; Grid Regions errors out when pressed.)

**Cause**: shapely ships bundled with the add-on as a wheel, but the wheel for this particular Blender didn't install. The known case is **Windows on ARM** — no shapely wheel exists on PyPI for that platform, so the bundled install can't succeed there.

**Fix**: there's no user-side install step to fall back to (shapely is bundled, not something you `pip install` yourself). On an unsupported platform like Windows on ARM, Preview Fill and Grid Regions simply aren't available; every other feature works without shapely. See [02_install.md](02_install.md) for the full list of bundled wheels.

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
