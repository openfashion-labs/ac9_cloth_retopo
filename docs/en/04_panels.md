# 04. Panel-by-panel reference

Described in the order the panels appear in the **AC9 Cloth Retopo** sidebar tab.

## Conventions shared across panels

The same kind of control looks the same in every panel.

| Look | Meaning |
|---|---|
| **Check** / action-name pair | "report only" and "actually do it" for the same operation. **Check** never changes anything |
| action-name / **×** pair | a button that creates derived data, and one that removes it |
| **+** **−** **×** triple | flag the selection / unflag the selection / clear all flags |
| **Detect** | mechanically proposes candidates and selects them — you still have to accept with **+** |
| Red warning row | a hard prerequisite isn't met. The buttons below won't work (usually the fix is a button on the same row) |
| Row with an info icon | a soft prerequisite. It still works, but with reduced capability |
| Light-gray single line | the result of the last operation run in that panel |

Explanations live in the buttons' tooltips (`bl_description`). No long explanations appear in the panel body itself.
Numeric settings are grouped into "**... Settings**" sub-panels, collapsed by default.

**Boundary** and **Faces** switch what they show depending on whether the Retopo is in Object Mode or Edit Mode.
The tools for the current mode appear at the top; the other mode's tools collapse into a single row (**Object Mode tools** / **Edit Mode tools**).
Even if you expand the collapsed row, operators for the wrong mode are grayed out via their own `poll()`.

---

## Prepare

The step that tidies the CLO export so it can be used as a bake source. Collapsed by default.
**Create Flat SK** applies to all selected meshes; everything else applies to the active object.

| Button | What it does | Prerequisite | Result |
|---|---|---|---|
| **Create Flat SK** | splits each selected mesh at UV island boundaries and writes the flat layout `(u, v, 0)` to a shape key `<UV name>_Flattened`. Triangulates by default before running | Object Mode / one or more meshes selected | more shape keys appear, shown at value 1.0. Objects with no UV layer are skipped. If the Guide is among the selection, the **Flat SK** field is updated with the new name. Basis is untouched |
| **Find Folds** | tags edges as crease where the dihedral angle is at least **Crease Min Angle**. If a flat shape key exists, measures against the Basis shape regardless of which key is currently displayed | active mesh (either mode) | the tag count appears in the result line. Doesn't look at a live Solidify |
| **Show** | selects the edges tagged as crease so you can see them | Edit Mode | replaces the selection |
| **Mark** | manually tags selected edges as crease (picked up by **Inset Line**) | Edit Mode / edges selected | adds tags |
| **Untag** | removes the tag from selected edges | Edit Mode / edges selected | removes tags |
| **×** | clears all crease tags (the edge attribute `ac9_crease_kind`) | active mesh | all tags removed |
| **Inset Line** | insets the fold line on both sides. Absorbs original vertices closer than **Width** into the line, then bevels the line into two rows left and right by **Width**, keeping the crease itself as the middle row. Uses the current selection if 2+ vertices are selected (with **Extend Along Fold** walking outward along the line), otherwise falls back to the **Find Folds** tags | Edit Mode / requires a flat shape key | a band is created. The result line reports the line's vertex count, repaired edges, absorbed count, removed slivers, and the band-width achievement rate (with the minimum) |
| **Inset Pieces** | for each pattern piece, absorbs vertices within **Width** of the outline into the outline, then insets the outline by **Width**, creating a parallel vertex row without distinguishing sewn seams from free edges | Object Mode / requires a flat shape key | the result line reports piece count, absorbed vertices, removed slivers, merged outline slivers, widened corners, slit tips, and band face count |
| **Width** | the half-width of the inset (in mesh units, default 0.001 = 1 mm). Original vertices closer than this distance are absorbed first. Larger values give a more gradual shading gradient | — | affects both insets above |

**Order matters**: run **Inset Line** before **Inset Pieces**. The band needs to reach an outline that hasn't been inset yet.
The panel's row numbers — 1 Flat SK / 2 Folds / 3 Lines / 4 Pieces — are the authoritative order.

Keep Solidify as a live modifier throughout this step. The export doubles as the retopo Guide, and applying Solidify breaks that.

### Prepare Settings (sub-panel)

| Setting | Affects |
|---|---|
| **Crease Min Angle** | the threshold for **Find Folds** (degrees, default 60). At 60, it picks up the Solidify rim (90°) and deliberate pressed folds, but not drape wrinkles |
| **Extend Along Fold** | whether **Inset Line** walks outward along the fold line from both ends of the selection (default on). Lets you process an entire line by selecting just two vertices |
| **Fold Min Angle** | while walking, only edges with a dihedral angle at or above this value are treated as fold line (degrees, default 6). Lower it for soft folds |
| **Line Profile** | the cross-section of **Inset Line**'s band (default 1.0). 1.0 keeps the crease sharp and flattens the sides; 0.5 rounds the fold over the band width |

---

## Setup

The inputs shared by every tool, and the analysis every other panel depends on.

| Item | What it does | Prerequisite | Result |
|---|---|---|---|
| **Retopo** | specifies the low-poly mesh you're building | a mesh object | sets the target for every panel. If left empty, **Faces** and everything after it shows a red warning |
| **Guide** | specifies the triangulated mesh that carries both the 3D shape (Basis) and the flat layout (Flat SK) | a mesh object | the source every analysis reads |
| **Flat SK** | the name of the flat-layout shape key. Becomes a search dropdown once the Guide has shape keys | Guide is set | e.g. `UV_Map_Flattened` |
| **Analyze Guide** | runs Seams → Symmetry (Folds + Twins) → Anchors together. Only reads the Guide | Guide and Flat SK | a line appears below reading `Seams … · Anchors … · Ghosts … · Folds … · Twins …/…` |
| **×** | clears all analysis (Seams and the ghosts derived from it, Folds, Twins, Anchors) | — | previously drawn overlays go blank |

**The seam analysis is cleared when you open the file and on Reload Scripts.** When that happens, a red "Seams not analyzed" appears — press **Analyze Guide** again.

<!-- screenshot: the Setup panel (Retopo / Guide / Flat SK, Analyze Guide, and the results line) -->

### Analysis Settings (sub-panel)

The rows to run each of the three analyses individually, and their tuning values. You typically set these once per Guide and rarely touch them again.

| Button | What it does |
|---|---|
| **Seams → Analyze** / **×** | finds sewn vertex pairs on the Guide in Flat SK space, and builds the seam overlay. Ghosts, snapping, and the seam overlay all read this result |
| **Symmetry → Analyze** / **×** | detects both Folds (symmetry axes within a single island) and Twins (separate islands that mirror each other left/right). **Generate** uses Folds to place vertices along the axis; **Self** / **Twin** rebuild one side from the other |
| **Anchors → Analyze** | shows the Guide's anchor points (where 3+ pattern pieces meet, or a sewn seam turns into a free edge) and the spans they divide the outline into. Spans are the unit density editing works on. Read-only |

The tuning values are:

- **Analyze Seams**: **Merge Precision** / **3D Match Distance** / **Marked Seams (Sharp)** (enables **Marked Seam Distance** when on)
- **Ghosts**: **Max Seam Distance** / **Bond Distance**
- **Analyze Symmetry**: **Symmetry Tolerance** / **Twin Tolerance**

Folds and Twins are detected by a single button, but they keep separate tolerances. Even with the same normalization, merging them without re-validating on pattern pieces that have darts can make Twin detection too aggressive or too lax.

---

## Boundary

Builds the Retopo's 2D outline and aligns vertices on both sides of each seam.
Object Mode handles the whole outline; Edit Mode handles just the part you've selected.

### Object Mode tools (whole outline)

**Generate** / **Sync** / **Status** all recompute the Guide's spans from scratch every time they run (**Generate** also recomputes Folds), so they don't depend on cached analysis.

| Button | What it does | Prerequisite | Result |
|---|---|---|---|
| **Boundary → Generate** | generates the Retopo's boundary vertex chains along the Guide's outline (each span cut off by a seam), placing both sides at matching positions along the span so they land at the same point in 3D | Object Mode / Guide + Retopo | leaves any existing boundary untouched. Spacing, count, target, and corner threshold are in **Boundary Settings**' **Generate** section |
| **Sync → Check** | only reports what would be added | same | doesn't change the mesh |
| **Sync → Sync** | equalizes the vertex count on both sides of each sewn edge pair. Inserts exactly the missing count into the shorter side, without moving existing vertices | same | new vertices are preferred at "outside the ends of a chain". Inserting between existing vertices turns the neighboring quad into an n-gon, and that count is reported. Never reduces or evens out vertices (that's Edit Mode's **Density**) |
| **Verify → Status** | checks every seam against the Retopo. Green = both sides match, red = both sides present but count or position disagrees, orange = only one side present | Object Mode / Guide + Retopo | the Seam Status overlay colors accordingly, vertices on problem seams get selected, and the full table is written to the text block `AC9_SeamStatus`. Two reported numbers: align (how far matched vertices drift along the seam) and offset (how far the Retopo strays from the Guide's outline) |

**Status is the pass/fail gate before moving on to Faces.**

### Edit Mode tools (the selected part)

| Button | What it does | Prerequisite | Result |
|---|---|---|---|
| **Density → − 1** / **+ 1** | removes / adds one vertex on the selected span. **Both sides of the seam simultaneously** | Edit Mode / boundary edge(s) selected (or one internal boundary vertex) | tries a local edit that doesn't break faces first, and only rebuilds spans where that isn't possible (rebuilding clears Preview Fill). Pinned and Corner vertices don't move |
| **Density → Even Out** | evens out spacing without changing the count. Doesn't add or remove vertices, and doesn't touch faces | same | only existing vertices move |
| **Count → Set** | sets a specific vertex count | same | same non-destructive preference as above |
| **Spacing → Apply** | sets a specific spacing (mm) | same | same |
| **Selected → Check** | only reports the positions that would be created and which vertices would be pinned | Guide + Retopo (reads either Edit or Object Mode selection) | doesn't change the mesh |
| **Selected → Sync+Pin** | creates (or finds, if it already exists) one matching vertex on the opposite side (the sewn partner) of the selected vertices, and **pins both sides** | same | the point-and-shoot version of creating a partner for a vertex you placed on the outline with a knife cut. The real work happens in Object Mode |
| **Pin → +** / **−** / **×** | pins the selected vertices / unpins them / unpins all | Edit Mode / Retopo selected | Pin means "this vertex was placed deliberately — don't let **Density** delete or slide it." A cut vertex looks like an ordinary vertex once faces are attached, so this is a manual flag rather than something detected automatically. Pinning is only safe when the matching 3D position is also pinned on the opposite side of the seam (which **Sync+Pin** does for both sides at once) |
| **Corner → +** / **−** / **×** | marks the selected vertices as Corner / unmarks them / unmarks all | Edit Mode / Retopo selected | Corner means "the pattern piece breaks here, and the edge flow is allowed to bend." Stored as a mesh attribute on the Retopo, so it persists across file closes |
| **Corner → Detect** | proposes Corner candidates from the pattern piece's own geometry (**Candidate Angle** in **Boundary Settings**) | Object Mode / Guide + Retopo | proposes only — nothing gets flagged. Uses the same measurement boundary generation uses to place breakpoints, so a proposal always lands on an existing vertex. Cases where no vertex lands are reported as a count |
| **Snap (G) → Ghost** | while moving with G, snaps only to a ghost point (the matching point on the opposite side) within **Snap Distance** | Edit Mode / ghosts exist | mutually exclusive with **Outline** |
| **Snap (G) → Outline** | while moving with G, snaps to the nearest point on the Guide's pattern outline (seams + free edges) | Edit Mode / seams analyzed | used to drop boundary vertices onto the outline. A **Fold** toggle also appears if Folds exist |
| **Bond → Force Bond** | snaps the selected vertices exactly onto the nearest ghost within **Bond Distance** | Edit Mode / ghosts exist | non-destructive. Selected vertices with no ghost in range, and unselected vertices, are left alone |

**Detect** only catches geometric corners like a square hem or a collar tip. Corners like "I want the flow to change partway along the side seam" can't be inferred geometrically, so add them with **+** by hand.

### Boundary Settings (sub-panel)

| Setting | Meaning |
|---|---|
| **Generate** (scope) | **All Empty Seams** = every seam without a Retopo yet / **Nearest to 3D Cursor** = only the one closest to the 3D cursor |
| **What** (targets) | **Seams + Free Edges** (closes every pattern boundary, on the assumption faces will follow) / **Sewn Seams Only** / **Free Edges Only** (hems, openings, necklines, etc.) |
| **Divide By** | **Spacing** = determines vertex count so density matches even across seams of different lengths / **Count** = the same count for every seam |
| **Spacing (mm)** / **Vertices** | one of these appears depending on the choice above |
| **Ignore Under (mm)** | seams shorter than this are ignored |
| **Corner Angle** | the angle treated as a corner |
| **Sync** → tolerance | **Sync**'s matching tolerance |
| **Corners** → **Candidate Angle** | **Detect**'s threshold |

---

## Faces

Once the outline exists, this is the step that fills and tidies the 2D interior.

### Object Mode

| Button | What it does | Prerequisite | Result |
|---|---|---|---|
| **Regions → Connect** | extends open lines in the direction of their last segment until they hit the first edge in the way (shortest lines first), then fills every closed region with faces | Object Mode (an Edit Mode version also exists) | **never subdivides the outline**. A line that reaches the outline connects to the nearest existing boundary vertex, so seams never need re-syncing. Leaves things ready for knife cutting. Stubs shorter than 5 mm (knife overshoot) are removed automatically, and their positions are reported |
| **Symmetry → Twin** | treats the island containing the selected vertex as authoritative, and rebuilds its mirror-pair island | Guide + Retopo / one vertex selected | destructive (deletes and rebuilds every vertex and face on the target side). Re-detects the Guide's symmetry at run time, so no prep is needed. Undo-able with Ctrl+Z |
| **Symmetry → Self** | treats the side of a single island containing the selected vertex as authoritative, and rebuilds the other side of the fold axis as its mirror image | same / **Self axis** must be set first | destructive. Picking the wrong axis produces a destructive no-op — "nothing changed, but it still overwrote something" |
| **Self axis** | the fold axis **Self** uses. **Auto** (picks whichever side is still asymmetric — i.e. still has work left; refuses if both sides are equally asymmetric) / **Vertical axis (mirror left-right)** / **Horizontal axis (mirror top-bottom)** | — | on its own row since you need to see this setting before pressing the button |
| **Subdivide** | simply subdivides the Retopo in 2D, snaps the new boundary vertices onto the Guide's sewn-seam lines, and re-projects into 3D | Object Mode / only while in 2D state (doesn't work while `AC9_3D_Project` is being displayed) | destructive, and permanently raises the resolution. New vertices are also projected onto the Guide surface, so no smoothing pass is needed. The Mirror updates automatically if it exists. Without seam analysis it still runs, but skips snapping (with an info-row warning) |

### Edit Mode

| Button | What it does | Prerequisite | Result |
|---|---|---|---|
| **Regions → Connect** | same as above, but only for the island containing the selection | Edit Mode / part of an island selected | aborts with a warning if nothing is selected |
| **Rows → Connect Rows** | select the edges of two lines, and it runs a rung between each pair of corresponding vertices, cutting the faces between them into rows (the same result as repeatedly pressing J by hand) | Edit Mode / editing the Retopo itself / edges of two lines selected | the two lines are matched end-to-end automatically, so the direction you drew them doesn't matter. If the vertex counts differ, extra vertices (spaced from the denser side's interval) are added into the wider gaps on the sparser side — existing vertices don't move. **The outline is never subdivided by this** (outline vertices already correspond to the other side in 3D; that's **Density**'s job) |
| **Symmetry → Twin** / **Self** / **Self axis** | same as the Object Mode side. **Twin** / **Self** read the selection from either mode, so they appear on both sides | — | Edit Mode is where you pick the source island |

### Face Settings (sub-panel)

Only appears with Experimental on. Tuning values for **Quad Fix** (**Non-planar Angle** / **Flat Angle** / **Only Selected** / **Fix Saddles**), **Preview Fill** (**Fill Spacing (mm)** / **Edge Clearance**), and **Grid Regions** (**Grid Spacing (mm)** / **Side Smoothing** / **Fill Mismatched Regions**).

### Drape Merge (sub-panel)

Only appears with Experimental on. See [05_experimental.md](05_experimental.md).

---

## 3D View

The Mirror (a view-only 3D display of the 2D retopo), the Guide's 2D/3D state, and **Finalize**.

| Button | What it does | Prerequisite | Result |
|---|---|---|---|
| **Mirror → Refresh** | rebuilds the Mirror by projecting the current 2D layout onto the Guide's surface | Guide + Flat SK + Retopo / Object Mode or Edit Mode | the Retopo stays flat and editable. The Mirror is a viewer — edits to it are overwritten at the next Refresh. The same button also appears in the viewport header |
| **Auto-refresh** | automatically runs **Refresh Mirror** when you leave the 2D retopo's Edit Mode (off by default) | — | if an unapplied move from **Apply 3D Edits → 2D** is sitting on the Mirror, it's silently skipped with a warning rather than discarded. Run **Refresh** by hand in that case |
| **Show** | shows / hides the Mirror. Hides rather than deletes | Mirror exists | the Mirror sits at the world origin and overlaps the flat layout in top view, so it tends to get in the way during 2D work. Since rebuilding costs a Refresh, it's hidden instead of removed |
| trash-can icon | deletes the Mirror | Mirror exists | can always be rebuilt with **Refresh** |
| **Guide → To 3D** / **To 2D** | switches the Guide between its flat layout (working mode) and 3D garment shape (reference mode) | Guide + Flat SK / the Flat SK exists on the Guide | **To 3D** sets the Flat SK to 0, shows the Guide (including its parent collection), and turns off the Wireframe overlay **only in perspective viewports** (it leaves the orthographic 2D working view untouched). **To 2D** sets the Flat SK back to 1 and restores visibility and the Wireframe overlay |
| **Mirror** / **Guide** (swap) | switches which of the Guide (high-poly 3D garment) or the Mirror (projected retopo) is shown. The button's label is the name of what you'd switch to | Guide is in 3D (Flat SK = 0) and a Mirror exists | swaps visibility |
| **Contact → Check** / **Separate** / **×** | Guide Separate (Experimental). See [05_experimental.md](05_experimental.md) | Experimental on | — |
| **3D Source** | the shape key that projection and the maps read as the Guide's 3D shape. **Original** (Basis, the draped shape as exported — use for the final retopo) / **Separated** (`AC9_Separated`, used while baking) | Experimental on / separation done | switching discards the Guide's triangle cache. The Mirror and projections catch up at the next **Refresh** |
| **Align → To Outline** | for each vertex within **Threshold** of the Guide's pattern outline (UV seam edges), snaps its 2D position onto the edge and rebuilds its attachment so one of the barycentric coordinates is exactly 0. Also flags it as boundary | Guide + Retopo | the pre-processing step that the old **Sync 3D > 2D** needed to pin things exactly to the outline. Run once after **Sync 2D > 3D** |
| **Threshold** | the snap tolerance above (default 0.003 = 3 mm) | — | too large, and interior vertices get misclassified as boundary |
| **Finalize → Finalize** | bakes the Retopo + the Guide's projection into a plain mesh `<Retopo name>_Final`. Shape = projection onto the Guide, UV = the 2D layout, no shape keys, no `ac9_*` data | Object Mode / Retopo, Guide, and Flat SK all set | the Retopo is untouched (stays flat and editable). Can be run any number of times. The resulting `_Final` becomes the selected/active object |

### Separation Settings (sub-panel)

Only appears with Experimental on. **Gap** / **Smooth Radius** / **Max Iterations** / **Include Solidify**.

---

## Guide Maps

Diagnostic bakes. Collapsed by default. Results go into fixed-name images, so keeping them open in the Image Editor means they update on every re-bake.

| Button | What it does | Prerequisite | Result |
|---|---|---|---|
| **Resolution** | the size (square) of the bake images. **1024** (fast preview) / **2048** (recommended, default) / **4096** (slow, for final checks) | — | affects the three bakes below |
| **Residual → Bake** | the signed distance from the Guide's surface to the current retopo. Red = Guide is closer, blue = farther, white = matching, dark gray = not yet covered by the retopo | Object Mode / Retopo + Guide | image `AC9_ResidualMap`. The result line reports RMS / p90 / max (mm) and the number of covered vertices |
| **Sag → Bake** | the signed distance from each pattern piece's (a seam-bounded island's) best-fit plane. White = bulging toward the viewer, black = sinking away, mid-gray = flat | Object Mode / Guide | image `AC9_SagMap`. Contour lines follow the edge-loop flow of low-frequency sagging. The result line reports piece count and max deviation |
| **Drape → Bake** | bakes AO × curvature (Dirty Vertex Colors) as shading onto the Guide's flat layout — the equivalent of baking both separately and multiplying them in a shader, in one click | Object Mode / Guide | image `AC9_DrapeMap`. Useful as a guide for where to knife-cut in 2D |
| **Preview** | which map (**Residual** / **Sag** / **Drape**) the Preview Plane displays | — | switching swaps the reference image on the preview material |
| **Plane** | creates (or reuses) a 1×1 m plane `AC9_BakePreview` in Flat SK space. Uses an Emission material so it's visible without the Image Editor | Object Mode | switches the Solid-shading viewport to **Solid color = Texture** and turns on **X-Ray** (the plane sits below the flat retopo). Material Preview / Rendered viewports are untouched |
| **Solid** | the viewport's own Solid color source (the same property as Viewport Shading > Color) | inside a 3D viewport | the map won't be visible unless this is set to **Texture**. It's surfaced here because many people don't know about this setting |

Progress is shown in stages. The bake itself (`bpy.ops.object.bake`) doesn't report incremental percentages, so that stretch is shown bracketed before/after. **Drape** bakes twice internally.

### Map Settings (sub-panel)

| Setting | Meaning |
|---|---|
| **Residual Scale** | the distance (mm, default 10) at which the residual map fully saturates to red/blue. **Keep this fixed across iterations** — that way "whiter than before" really does mean it got closer |
| **Coverage Margin** | how far, in the 2D plane, a Guide vertex can be from the retopo's footprint and still count as "covered" (mm, default 2). Beyond this, the residual map goes dark gray |
| **Sag Scale** | the distance (mm, default 20) at which plane-fit deviation becomes pure white/black. Mid-gray is on-plane |

---

## Overlays

Everything drawn in the viewport is toggled from here. The same set is also reachable from the popover in the viewport header.

Previously, 13 toggles were scattered across the various work panels. That reads fine the first time, but switching from "boundary work" to "checking in 3D" meant hunting through four boxes each time — so they're all gathered here now.

### Master switch

**Overlays: ON** / **Overlays: OFF** — the parent switch for **every** overlay this add-on draws.
Turning it off short-circuits every per-frame draw callback right at the start, so it's both a "hide everything" button and a performance switch (it skips the heavier per-frame computations like selection ghosts and seam parity entirely).

### Presets

Pressing one turns on exactly the toggles that preset needs, and turns everything else off (it never carries over the previous state — it always lands on a known configuration).
Every preset except **All Off** also turns the master switch on (so a request to "show me this" doesn't appear to do nothing).

| Preset | Meaning |
|---|---|
| **Boundary** | for 2D boundary work: seam guides, ghosts for the selected pair, vertex-count parity, status |
| **Seams** | for reading the Guide's seam structure: seams, pair lines, Folds, Twins, the white outline, ghosts |
| **Mirror** | for viewing the Mirror in 3D: the white outline and boundary flags |
| **All Off** | turns every individual overlay off (leaves the master switch alone) |

**Selection Link** is deliberately outside the presets' control. Since it's useful in every working mode (seeing where a 2D selection lands on the Mirror, and vice versa), the presets never touch it.

### Toggles

| Box | Toggles |
|---|---|
| **Seam Lines** | **Seams (cyan)** / **Pair Lines** / **Fold Lines** / **Twins (magenta)** / **Outline (white)** |
| **Marks** | a refresh button for **Analyze Anchors** in the header. **Anchors** / **Corners** / **Pins**. Turning on **Anchors** without an analysis shows a red warning. Once analyzed, the anchor count and span count are shown |
| **Ghosts** | a manual refresh (**Refresh Ghosts**) and **×** (**Clear Ghost Points**) in the header. **Selected Only** / **Points** (enables **Only Unplaced** when on) / **Lines** / **Snap Radius** |
| **Status** | **Vertex Counts** / **Seam Status** / **Boundary Flags** |
| **Mirror** | **Selection Link** — draws an orange marker on the corresponding vertex on the other object. Works for vertex, edge, face, loop, and shortest-path selections |
| **Guide** | an **Islands** slider (**Alpha**) and **Bake** / **×** — detects the Guide's UV islands, writes them to a color attribute, and builds a simple material to display it. Not a GPU overlay, but it's kept here since it answers the same question of "what does the Guide look like" |

Most of this is drawn from the cache that **Analyze Seams** fills. That cache is emptied on file load and Reload Scripts, so when it's empty this panel also shows "Seams not analyzed" with an **Analyze** button.

<!-- screenshot: the Overlays panel (presets and each toggle box) -->

### Appearance (sub-panel)

Purely cosmetic settings — color, line width, marker size. Touching these never triggers analysis.
**Seam** / **Seam Width** / **Ghost (Unplaced)** / **Ghost (Placed)** / **Ghost Line** / **Ghost Cross Size** / **Ghost Line Width** / **Fold** / **Fold Width** / **Anchor** / **Anchor Cross Size** / **Link Point Size** / **Boundary Cross Size** / **Z Offset**.

---

## Advanced

Where the legacy workflow, maintenance, diagnostics, and discontinued experiments live. Collapsed by default.
Kept separate so the work-phase panels only carry what the current process actually uses.

| Item | What it does | Prerequisite | Result |
|---|---|---|---|
| **Legacy Flip** (under the header) | the old approach, where the Retopo itself moves between 2D and 3D via a shape key. **Apply 3D → 2D**, **2D > 3D** / **3D > 2D**, **Bind → New Verts** / **Auto** | Experimental on | 2D editing + **Refresh Mirror** is what's recommended instead. See [05_experimental.md](05_experimental.md) |
| **Legacy Flip Options** | **Overwrite ShapeKey** / **Clear Failed Group** / **Select Failed** | always shown | controls the behavior of the legacy workflow above |
| **Maintenance → Guide Cache → Clear** | discards the current Guide's triangle list and 2D BVH cache. Rebuilt automatically on the next Sync-family operation | — | run this after editing the Guide's geometry (vertex positions) without swapping out its data block — e.g. after sculpting or vertex editing. The seam-line and boundary overlay caches are invalidated at the same time |
| **Maintenance → Attachments → Clear** | clears the per-vertex attachment data on the Retopo (`ac9_tri_idx` / `ac9_bary_u` / `ac9_bary_v` / `ac9_status`) | — | use before re-attaching to a different Guide |
| **Diagnostics → Seam → Vertex** | with one Retopo vertex selected, prints the corresponding seam pair to the system console | one vertex selected | useful for checking that a known pair lands where expected |
| **Diagnostics → Seam → Spaces** | prints the world-space bounding boxes of the detected seam pairs, the Retopo's boundary vertices, and the Guide's flat layout to the console | — | if the seam box and the retopo box don't overlap, they're in different coordinate spaces (a `matrix_world` / normalization mismatch) |
| **Diagnostics → Twins → Mapping** | maps each Retopo island to a Guide island and reports the result (the full table goes to a text data block). Runs **Detect Twins** first if needed | — | read-only, writes nothing to the mesh. Lists the Twin pairs below, along with the reason any unpaired island didn't match ("too small to test" / "no matching partner found") |
| **Ghosts → Live Ghost Update** | whether ghosts are recomputed every time an edit is committed | — | — |
| **Settings → Reset to Defaults** | resets every tuning value in the AC9 property tree to its default | confirmation dialog | leaves the Retopo / Guide / Flat SK assignments, result lines, and mesh data untouched. Reports how many values changed |
| **Done with this file → Clear All AC9 Data** | strips everything the add-on has written from this file | Object Mode | shows a dialog listing what will be removed before it runs (see below) |

### Scope of Clear All

What gets removed:

- Scene settings (Retopo / Guide / Flat SK and every option — once this is gone, the header buttons disappear too)
- The Mirror object (found via the `ac9_mirror_of` property)
- The Bake Preview Plane (the `AC9_BakePreview` object, mesh, and material)
- Every `ac9_*` / `AC9_*` attribute layer on every mesh (attachments, status, Pin, Corner, grid/fill/scaffold markers, bake color layers), the `AC9_3D_Project` and `AC9_Separated` shape keys, the `AC9_Project_Failed` vertex group, the `AC9_Island_Colors` material slot
- `ac9_*` custom properties on the scene, objects, and meshes
- Bake images, temporary bake materials, and report text data blocks

What's left behind: **your own geometry, UVs, your own materials, and the Guide's flat shape key.**

There's one exception. A Retopo currently displayed in 3D via the legacy shape key (value > 0.5) would snap back to the 2D layout once the key is removed.
Since that's the one case where what's on screen would be lost, its 3D coordinates are written into the mesh before deletion, so what you see stays exactly as it was.
