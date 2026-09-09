# 03. Workflow

It's a straight line from top to bottom. The order of panels in the sidebar is the order of the process.
Each step below lists what to check before moving to the next.

## 0. Export from CLO / MD

Export the garment from CLO / Marvelous Designer and import it into Blender.
Export as **FBX**, as a **triangulated mesh**, with the **UVs already unwrapped in CLO** (detailed export settings will be added later).
The add-on itself only assumes two things:

- The mesh is triangulated (**Create Flat SK** triangulates by default, so this is satisfied there)
- UVs are already unwrapped (objects with no UV layer are skipped by **Create Flat SK**)

Add thickness with a Solidify modifier, and keep it **live**.

## 1. Prepare — tidy up the CLO export

Tidies the outline and fold lines of the export so it can be used as a bake source. The panel's row numbers are the step order.
CLO exports are usually multiple objects, so **Create Flat SK** alone applies to all selected meshes; everything else applies to the active object.

1. **Create Flat SK** (Object Mode) — splits at UV island boundaries and writes the flat layout to a shape key. Steps 2–4 below need this shape key.
2. **Find Folds** — tags edges as fold lines where the dihedral angle is at least **Crease Min Angle**. Use **Show** to select and check what got tagged, **Mark** / **Untag** to adjust by hand, and **×** to clear everything.
3. **Inset Line** (Edit Mode) — insets the tagged fold lines on both sides into a band. Run this before **Inset Pieces** (the band needs to reach an outline that hasn't been inset yet).
4. **Inset Pieces** (Object Mode) — for each pattern piece, absorbs vertices within **Width** of the outline into the outline, then insets the outline by **Width** to create a parallel row of vertices.

**Next**: the result line below the panel shows the counts of what happened (vertices absorbed, sliver triangles removed, band-width achievement rate, etc.). Check that no spot is reported where the band width came out extremely thin relative to **Width**.
Don't apply Solidify until the very end.

<!-- screenshot: the Prepare panel (1 Flat SK through 4 Pieces, and the result line) -->

## 2. Setup — specify inputs and analyze

1. Set **Retopo** to the low-poly mesh you're about to build, and **Guide** to the CLO export mesh.
2. Pick **Flat SK** from the Guide's shape keys via the search dropdown (it's filled in automatically if **Create Flat SK** processed the Guide).
3. Press **Analyze Guide**. This runs Seams (sewn pairs) → Symmetry (Folds and Twins) → Anchors (span breakpoints) in order. All of it only reads the Guide — Retopo isn't touched.

**Next**: below **Analyze Guide**, a line like `Seams NN · Anchors NN · Folds NN · Twins NN / NN` appears.
While it still shows the red "Seams not analyzed" warning, none of the overlays, ghosts, or snapping downstream will show anything.
**This analysis is cleared when you open the file and on Reload Scripts.** Press it again whenever that happens.

## 3. Boundary — build the 2D outline

Details are in the Boundary section of [04_panels.md](04_panels.md). The rough order is:

1. Object Mode: **Generate** — creates boundary vertex chains along the Guide's outline.
2. Edit Mode: knife-cut wherever you need.
3. Edit Mode: select the cut vertices and run **Selected → Sync+Pin** — creates a matching vertex on the opposite side and pins both sides.
4. Edit Mode: use **Density** (**− 1** / **+ 1** / **Even Out**, **Count → Set**, **Spacing → Apply**) to adjust density. Pinned and Corner vertices don't move.
5. Edit Mode: add **Corner** flags (**Detect** selects candidates → cull them → **+**).
6. Object Mode: **Sync → Check** to review what would be added, then **Sync**.
7. Object Mode: **Verify → Status**.

**Next**: once **Status** shows all green (both sides matching), move on to Faces.
While red (both sides present but not matching) or orange (only one side present) remain, the sewn seams won't line up in 3D even after faces are filled.
The full table is written out to the text block `AC9_SeamStatus`.

<!-- screenshot: the Boundary panel's Object Mode side, and a viewport with the Seam Status overlay all green -->

## 4. Faces — fill in the 2D interior

1. **Regions → Connect** — extends open lines to the nearest edge and connects them, then fills every closed region with faces. This is what makes the knife usable (the knife cuts faces, so an outline alone can't be cut). **No new vertices are created on the outline** (a line reaching the outline connects to an existing boundary vertex).
2. Freely subdivide the pattern piece with the knife (K). After cutting, pressing **Connect** again fills whatever regions the new cuts created.
3. Edit Mode: **Connect Rows** — select two lines (edges), and it runs a rung between each pair of corresponding vertices, splitting the faces between them into rows. If the vertex counts differ, vertices are added to the shorter side (the outline itself isn't subdivided).
4. **Symmetry → Twin** / **Self** — rebuild one side of a left/right pattern pair from the other side (**Twin**), or rebuild one piece from its own fold axis (**Self**). **Self** requires picking the axis with **Self axis** before pressing it. Both are destructive.
5. Object Mode: **Subdivide** — subdivides in 2D, snaps new boundary vertices onto the Guide's outline, and re-projects into 3D. The Mirror updates automatically if it exists.

**Next**: the result line below the panel shows how many open ends were connected, how many regions were filled, and how many rungs were created.
Check the Mirror to confirm the face flow isn't broken anywhere.

<!-- screenshot: the Faces panel (Edit Mode side) and the results of Connect / Connect Rows -->

## 5. 3D View — check in 3D and finalize

1. **Mirror → Refresh** — rebuilds the Mirror from the current 2D layout. Works in either Object Mode or the Retopo's Edit Mode. With **Auto-refresh** on, it runs automatically whenever you leave Edit Mode.
2. **Show** toggles the Mirror's visibility (since it tends to get in the way of the top-down view during 2D work, it's hidden rather than deleted).
3. **Guide → To 3D** / **To 2D** switches the Guide between its 3D shape and its flat layout. When the Guide is in 3D and a Mirror exists, the adjacent button swaps which one (Guide or Mirror) is displayed.
4. If needed, **Align → To Outline** snaps vertices within **Threshold** of the outline onto the outline itself and flags them as boundary.
5. **Finalize** — creates `<Retopo name>_Final`. The Retopo itself is untouched.

**Next**: `<Retopo name>_Final` becomes the selected/active object, and the result line shows its vertex count.
This is the deliverable you take out of the add-on.

<!-- screenshot: the 3D View panel, and the Mirror sitting on top of the Guide -->

## 6. Guide Maps — diagnostic bakes (optional)

Useful mid-work, when you want to see where things need fixing.

1. Pick a **Resolution** (1024 / 2048 / 4096, default 2048).
2. **Residual → Bake** — the offset between the Guide's surface and the current Retopo (red = Guide is closer, blue = farther, white = matching, dark gray = not yet covered by the Retopo). Image `AC9_ResidualMap`.
3. **Sag → Bake** — the offset from each pattern piece's best-fit plane (white = bulging toward the viewer, black = sinking away, mid-gray = flat). Image `AC9_SagMap`. The contour lines follow the edge-loop flow of low-frequency sagging.
4. **Drape → Bake** — a shaded reference combining AO × curvature. Image `AC9_DrapeMap`. Useful as a guide for where to knife-cut in 2D.
5. Pick which map to view with **Preview**, then press **Plane** to create a 1×1 m plane called `AC9_BakePreview`, switch the Solid-shading viewport to **Solid color = Texture**, and turn on **X-Ray**.

**Next**: confirm the map is actually visible. If it isn't, check whether **Solid** is set to **Texture** and whether X-Ray is on (the Preview Plane sits 5 mm below the Retopo).
Progress is shown only as staged text, since the bake itself (`bpy.ops.object.bake`) doesn't report incremental percentages — that stretch of the process will look stalled.

<!-- screenshot: the Guide Maps panel, and the Residual Map showing on the Preview Plane -->

## 7. Cleanup

- **Advanced → Reset to Defaults** — resets only the setting values to their defaults. Leaves the Retopo / Guide / Flat SK assignments and meshes untouched.
- **Advanced → Clear All AC9 Data** — strips every trace of the add-on from this file. A list of what will be removed appears in a dialog before it runs.
