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
Every button works on the **Guide** (Setup's picker) and on the mode the **Guide** is in — not on the active object, and not on the selection. Set **Guide** before you open this panel; several export objects are prepared by pointing **Guide** at each in turn. Inside the Guide, a vertex or edge selection still means what it says (**Inset Line**, **Mark** / **Untag**, **Pairs** / **Self**).

| Button | What it does | Prerequisite | Result |
|---|---|---|---|
| **Create Flat SK** | splits the Guide at UV island boundaries and writes the flat layout `(u, v, 0)` to a shape key `<UV name>_Flattened`. Triangulates by default before running | Guide set / Guide in Object Mode | the shape key appears, shown at value 1.0, and the **Flat SK** field is updated with its name. A Guide with no UV layer is refused with a warning. Basis is untouched |
| **Find Folds** | tags edges as crease where the dihedral angle is at least **Crease Min Angle**. If a flat shape key exists, measures against the Basis shape regardless of which key is currently displayed | Guide set (either mode) | the tag count appears in the result line. Doesn't look at a live Solidify |
| **Show** | selects the edges tagged as crease so you can see them | Guide in Edit Mode | replaces the selection |
| **Mark** | manually tags selected edges as crease (picked up by **Inset Line**) | Guide in Edit Mode / edges selected | adds tags |
| **Untag** | removes the tag from selected edges | Guide in Edit Mode / edges selected | removes tags |
| **×** | clears all crease tags (the edge attribute `ac9_crease_kind`) | Guide set | all tags removed |
| **Inset Pieces** | for each pattern piece, offsets the outline inward by **Width** on the flat shape key and rebuilds the ring between the two as triangles, creating a parallel vertex row without distinguishing sewn seams from free edges. Nothing is welded, so no outline vertex is lost, and tagged fold lines are kept as constraints | Guide in Object Mode / requires a flat shape key | the result line reports piece count, faces replaced, new row vertices, strip and gap face counts, the fold edges kept as constraints, and the seam desync count |
| **Inset Line** | rebuilds a band **Width** wide on each side of a fold line as triangles, leaving the fold's own vertices and edges exactly where they are. Uses the **selected edges** (each connected run is one line); with nothing selected, falls back to the **Find Folds** tags. The band stops at the row **Inset Pieces** left behind | Guide in Edit Mode / requires a flat shape key | a band is created. The result line reports how many lines were inset (with branching and too-short counts), the band face count, the row edge count, and the seam desync count |
| **Width** | the half-width of the inset (in mesh units, default 0.001 = 1 mm). The original geometry inside that band is replaced by new triangles. Larger values give a more gradual shading gradient | — | affects both insets above |

**Order matters**: run **Inset Pieces** before **Inset Line**. Inset Pieces keeps the tagged fold lines as constraints, so the later Inset Line only has to reach the inner row and never touches the outline.
The panel's row numbers — 1 Flat SK / 2 Folds / 3 Pieces / 4 Lines — are the authoritative order.
**Inset Line stays greyed out until Inset Pieces has run** (the reason is shown under the button and in its tooltip). Find Folds works in Edit Mode while Inset Pieces is Object Mode only, so the lit button next to it used to be Inset Line — this guard stops that slip. Go back to Object Mode, run Inset Pieces, then press Inset Line in Edit Mode.

Keep Solidify as a live modifier throughout this step. The export doubles as the retopo Guide, and applying Solidify breaks that.

### Prepare Settings (sub-panel)

| Setting | Affects |
|---|---|
| **Crease Min Angle** | the threshold for **Find Folds** (degrees, default 60). At 60, it picks up the Solidify rim (90°) and deliberate pressed folds, but not drape wrinkles |

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

**Generate** / **Match** / **Status** all recompute the Guide's spans from scratch every time they run (**Generate** also recomputes Folds), so they don't depend on cached analysis.

| Button | What it does | Prerequisite | Result |
|---|---|---|---|
| **Boundary → Generate** | generates the Retopo's boundary vertex chains along the Guide's outline (each span cut off by a seam), placing both sides at matching positions along the span so they land at the same point in 3D | Object Mode / Guide + Retopo | leaves any existing boundary untouched. Spacing, count, target, and corner threshold are in **Boundary Settings**' **Generate** section |
| **Verify → Status** | checks the whole pattern outline against the Retopo. **Sewn seams**: green = both sides match, red = **the two sides hold different numbers of vertices** (Match or Generate fills that in), purple = **the same number but out of line along the seam** (those vertices have to be moved), orange = only one side present. **Free edges** (hems, openings, necklines) have no partner side, so "matching" does not exist for them — the only question is whether they sit on the outline: teal = on it, purple = more than 1 mm off (a free edge has no count to get wrong, so drifting off is a position problem, same colour) | Object Mode / Guide + Retopo | the Seam Status overlay colors accordingly, vertices on problem seams and runs — and on T-junctions (a vertex lying on an edge it is not joined to) — get selected, and the full table is written to the text block `AC9_SeamStatus`. Two reported numbers for a seam: align (how far matched vertices drift along the seam) and offset (how far the Retopo strays from the Guide's outline); a free edge has only offset. Free-edge rows are per **run** — one continuous hem is one row, the same unit **Generate** divides — not per span |

**Status is the pass/fail gate before moving on to Faces.**

### Edit Mode tools (the selected part)

**Density**, **Count**, **Spacing**, **Pin**, and **Corner** (including **Corner → Detect**) are Experimental — see [05_experimental.md](05_experimental.md). **Match**, **Snap (G)**, and **Bond → Force Bond** are not.

| Button | What it does | Prerequisite | Result |
|---|---|---|---|
| **Density → − 1** / **+ 1** | removes / adds one vertex on the selected span. **Both sides of the seam simultaneously** | Edit Mode / boundary edge(s) selected (or one internal boundary vertex) / Experimental on | tries a local edit that doesn't break faces first, and only rebuilds spans where that isn't possible (rebuilding clears Preview Fill). Pinned and Corner vertices don't move |
| **Density → Even Out** | evens out spacing without changing the count. Doesn't add or remove vertices, and doesn't touch faces | same | only existing vertices move |
| **Count → Set** | sets a specific vertex count | same | same non-destructive preference as above |
| **Spacing → Apply** | sets a specific spacing (mm) | same | same |
| **Match** | takes the selected edge(s) (or vertex) as the source, and on those seam(s) only, creates whatever vertex the opposite side is missing (does nothing if it's already there). The point-and-shoot way to create a partner for a vertex you placed on the outline with a knife cut | Guide + Retopo / selected in Edit Mode | new vertices are preferred at "outside the ends of a chain". Inserting between existing vertices turns the neighboring quad into an n-gon, and that count is reported. A new vertex that would land on an existing boundary edge splits that edge instead. Add-only — never reduces or evens out vertices (that's **Density**), and doesn't pin: to protect a deliberately placed vertex from **Density**, follow up with **Pin → +**. A seam where both sides hold vertices the other lacks is left alone; the reasons (needs repositioning / selected side too sparse to match without removing from the partner / selected side has nothing to copy) are each reported with a count. **Nothing selected is refused**: with no selection this used to match every sewn seam of the garment, taking whichever side held more vertices as the source, which can add vertices across panels you have not built yet — it now takes an explicit `every_seam=True` from a script, and no button passes it. There is no separate **Check** button either (it was a second checker beside **Verify → Status** and only told you the count before committing, of an add-only run that Ctrl+Z undoes); `apply` is still on the operator for F3 and scripts |
| **Pin → +** / **−** / **×** | pins the selected vertices / unpins them / unpins all | Edit Mode / Retopo selected / Experimental on | Pin means "this vertex was placed deliberately — don't let **Density** delete or slide it." A cut vertex looks like an ordinary vertex once faces are attached, so this is a manual flag rather than something detected automatically. Pinning is only safe when the matching 3D position is also pinned on the opposite side of the seam |
| **Corner → +** / **−** / **×** | marks the selected vertices as Corner / unmarks them / unmarks all | Edit Mode / Retopo selected / Experimental on | Corner means "the pattern piece breaks here, and the edge flow is allowed to bend." Stored as a mesh attribute on the Retopo, so it persists across file closes |
| **Corner → Detect** | proposes Corner candidates from the pattern piece's own geometry (**Candidate Angle** in **Boundary Settings**) | Object Mode / Guide + Retopo / Experimental on | proposes only — nothing gets flagged. Uses the same measurement boundary generation uses to place breakpoints, so a proposal always lands on an existing vertex. Cases where no vertex lands are reported as a count |
| **Snap (G) → Ghost** | while moving with G, snaps only to a ghost point (the matching point on the opposite side) within **Snap Distance** | Edit Mode / ghosts exist | mutually exclusive with **Outline** |
| **Snap (G) → Outline** | while moving with G, snaps to the nearest point on the Guide's pattern outline (seams + free edges) | Edit Mode / seams analyzed | used to drop boundary vertices onto the outline. A **Fold** toggle also appears if Folds exist |
| **Bond → Force Bond** | snaps the selected vertices exactly onto the nearest ghost within **Bond Distance** | Edit Mode / ghosts exist | non-destructive. Selected vertices with no ghost in range, and unselected vertices, are left alone |

**Detect** only catches geometric corners like a square hem or a collar tip. Corners like "I want the flow to change partway along the side seam" can't be inferred geometrically, so add them with **+** by hand.

**Free-edge ghosts.** A sewn seam's ghost is the matching point on the opposite side; a free edge has no opposite side. Its ghost is instead **the foot of the perpendicular from the vertex onto the nearest free edge**. A vertex properly on the outline has its foot at its own position, so it reads as placed (green); only vertices that have drifted off the outline keep a red cross and a connector. Combined with **Only Unplaced** this shows exactly the free-edge vertices that are off the pattern.
The foot is a static point from the last **Refresh Ghosts** and does not follow a drag. To walk a vertex continuously along a hem, use **Snap (G) → Outline** instead.

**The red cross lands on the opposite panel (Orphan Rings).** An unplaced ghost's red cross marks the empty spot on the **partner** panel — the side that is missing a vertex. In a split flat layout that is a whole panel away from the vertex you are looking at, and at working zoom it is off screen. So selecting a vertex that has no partner shows you nothing at the vertex itself. **Orphan Rings** (enabled when **Points** is on) rings the vertex that **owns** that unplaced ghost. Same colour as the cross so the two read as one warning, different shape so it stays obvious which end is the vertex and which is the empty slot. Sewn seams only — a free edge's foot sits right beside its own vertex, close enough that a ring there would blur into the cross.

The ring means "the slot opposite this vertex is empty", not "this vertex is the wrong one". Where a seam has three vertices on one side and two on the other, nearest-neighbour matching within **Bond Distance** pairs two of them and the leftover gets the ring; which one is left over depends on the spacing.

Turn **Lines** on to draw a connector between the vertex and its cross. **Unplaced Lines Only** is on by default, so only unplaced ghosts get a line. A placed ghost's connector only confirms that a vertex is already there, and on a finished panel those outnumber the unplaced ones by two orders of magnitude — drawing them all buries the few that matter.

### Boundary Settings (sub-panel)

| Setting | Meaning |
|---|---|
| **Generate** (scope) | **All Empty Seams** = every seam without a Retopo yet / **Nearest to 3D Cursor** = only the one closest to the 3D cursor |
| **What** (targets) | **Seams + Free Edges** (closes every pattern boundary, on the assumption faces will follow) / **Sewn Seams Only** / **Free Edges Only** (hems, openings, necklines, etc.) |
| **Divide By** | **Spacing** = determines vertex count so density matches even across seams of different lengths / **Count** = the same count for every seam |
| **Spacing (mm)** / **Vertices** | one of these appears depending on the choice above |
| **Straight Tolerance (mm)** | Spacing only. Default 0: straight runs are divided by Spacing like everything else. Raised, a stretch that stays within this distance of a straight line (both sides of a seam) is left undivided — corners only — while curves still get the Spacing |
| **Ignore Under (mm)** | seams shorter than this are ignored |
| **Corner Angle** | the angle treated as a corner |
| **Match** → tolerance | **Match**'s matching tolerance |
| **Corners** → **Candidate Angle** (Experimental) | **Detect**'s threshold |

---

## Faces

Once the outline exists, this is the step that fills and tidies the 2D interior.

### Object Mode

| Button | What it does | Prerequisite | Result |
|---|---|---|---|
| **Regions → Connect** | extends open lines in the direction of their last segment until they hit the first edge in the way (shortest lines first), then fills every closed region with faces | Object Mode (an Edit Mode version also exists) | **never subdivides the outline**. A line that reaches the outline connects to the nearest existing boundary vertex, so seams never need re-matching. Leaves things ready for knife cutting. Stubs shorter than 5 mm (knife overshoot) are removed automatically, and their positions are reported |
| **Preview → Auto Fill** | lays a disposable quad grid inside each pattern piece so you can judge whether the outline's vertex count is right before committing to real topology. Never moves a boundary vertex | Object Mode or Edit Mode (Edit Mode works in place) / Guide + Retopo | flags everything it created so **Clear Fill (×)** can undo it cleanly. Not final topology — the intended loop is look, fix density, run again; **Adjust Density** clears the fill itself whenever it needs to rebuild a span. Pattern pieces whose outline isn't closed are skipped (reported as a count). If shapely isn't available for this Blender, the row shows a warning instead of the button |
| **Preview → Clear Fill (×)** | removes everything the last **Auto Fill** created | same | doesn't need shapely — deleting a previous fill needs no geometry library |
| **Symmetry → Twin** | treats the island containing the selected vertex as authoritative, and rebuilds its mirror-pair island | Guide + Retopo / one vertex selected | destructive (deletes and rebuilds every vertex and face on the target side). Re-detects the Guide's symmetry at run time, so no prep is needed. Undo-able with Ctrl+Z |
| **Symmetry → Self** | treats the side of a single island containing the selected vertex as authoritative, and rebuilds the other side of the fold axis as its mirror image | same / **Self axis** must be set first | destructive. Picking the wrong axis produces a destructive no-op — "nothing changed, but it still overwrote something" |
| **Self axis** | the fold axis **Self** uses. **Auto** (picks whichever side is still asymmetric — i.e. still has work left; refuses if both sides are equally asymmetric) / **Vertical axis (mirror left-right)** / **Horizontal axis (mirror top-bottom)** | — | on its own row since you need to see this setting before pressing the button |
| **Subdivision Mirror → Detail / Show Subdivided** | runs the real Subdivide, outline snap, and Guide reprojection for the chosen detail level on a disposable copy, then shows that result on the Mirror | Object Mode or Edit Mode / 2D state | it sits outside the mode-specific tool groups and remains usable while editing. While on, **Refresh** after a cut or other edit keeps it on and rebuilds the subdivided Mirror from the live 2D mesh. The Retopo stays low-resolution. Press the **×** at the right to return to the ordinary Mirror. No preview cache is saved |
| **Apply → Apply Subdivision** | uses the Detail shared with the Mirror display to subdivide the Retopo in 2D, snap new boundary vertices onto the Guide's sewn-seam lines, and re-project into 3D | Object Mode / only while in 2D state (doesn't work while `AC9_3D_Project` is being displayed) | destructive, permanently raises the resolution, and this is when the subdivided display turns off once. New vertices are projected onto the Guide surface, so no smoothing pass is needed. The ordinary Mirror updates automatically if it exists. Without seam analysis it still runs, but skips snapping (with an info-row warning). N-gons (faces with more than four vertices) pass through undivided — only their count is reported on the result row (this is how Blender's Subdivide works; see 06. Troubleshooting) |

### Edit Mode

| Button | What it does | Prerequisite | Result |
|---|---|---|---|
| **Regions → Connect** | same as above, but only for the island containing the selection | Edit Mode / part of an island selected | aborts with a warning if nothing is selected |
| **Preview → Auto Fill** / **Clear Fill (×)** | same as the Object Mode side, but works in place on the current island while editing | Edit Mode / Guide + Retopo | same shapely / warning-row behavior as above |
| **Rows → Connect Rows** | select the edges of two lines, and it runs a rung between each pair of corresponding vertices, cutting the faces between them into rows (the same result as repeatedly pressing J by hand) | Edit Mode / editing the Retopo itself / edges of two lines selected | the two lines are matched end-to-end automatically, so the direction you drew them doesn't matter. If the vertex counts differ, extra vertices (spaced from the denser side's interval) are added into the wider gaps on the sparser side — existing vertices don't move. **The outline is never subdivided by this** (outline vertices already correspond to the other side in 3D; that's **Density**'s job) |
| **Symmetry → Twin** / **Self** / **Self axis** | same as the Object Mode side. **Twin** / **Self** read the selection from either mode, so they appear on both sides | — | Edit Mode is where you pick the source island |

### Face Settings (sub-panel)

**Preview Fill**'s tuning values (**Fill Spacing (mm)** / **Edge Clearance**) are always visible here. The **Quad Fix** section (**Non-planar Angle** / **Flat Angle** / **Only Selected** / **Fix Saddles**) and the **Grid Regions** section (**Grid Spacing (mm)** / **Side Smoothing** / **Fill Mismatched Regions**) inside this same sub-panel are still Experimental-only.

---

## 3D View

The Mirror (a view-only 3D display of the 2D retopo), what is visible (**View**), and **Finalize**.

| Button | What it does | Prerequisite | Result |
|---|---|---|---|
| **Mirror → Refresh** | rebuilds the Mirror by projecting the current 2D layout onto the Guide's surface | Guide + Flat SK + Retopo / Object Mode or Edit Mode | the Retopo stays flat and editable. Geometry only for a Mirror that already exists: it never changes what is visible — not the View state, not the Guide's Flat SK or visibility. When it has to CREATE the Mirror it shows it, by moving **View** to **Mirror** (there is no earlier choice about a Mirror to respect, and **Mirror** is the state that leaves the Guide's Flat SK at 1). The same button also appears in the viewport header |
| **Mirror → ×** | deletes the Mirror | Mirror exists | rebuild any time with Refresh |
| **View → Mirror** / **Guide** / **Both** | the one place visibility is decided, named after what you end up looking at; exclusive, and the current state's button is depressed | Guide + Flat SK | **Mirror**: the retopo's 3D form alone — the Guide is hidden and laid out flat (Flat SK = 1), which is the state the 2D retopo is edited in. **Guide**: the garment alone (Flat SK = 0); the Mirror, which occupies the same space, is hidden. **Both**: the retopo on top of the garment. These are plain eye-icon (per-View-Layer) visibility, so they mean the same thing in every viewport and the Outliner undoes any of them — hide either object there and the row simply reads back what is actually on screen |
| **Wire → Wireframe** | toggles the Wireframe overlay in **every** 3D Viewport at once — independent of the **View** state | at least one 3D Viewport | every viewport is set to the opposite of the one the button was pressed in. The depress state reads the viewport you are in |
| **Contact → Check** | measures and colors where layers of the Guide are touching each other (pleats, wrap-around overlaps), without changing anything | Object Mode | vertex color `AC9_Gap` on the Guide: red = touching, yellow = under the gap threshold, green = sufficient |
| **Contact → Separate** | opens a minimal gap between touching layers so raycasting bakes don't pick up the neighboring layer. Writes the result to a shape key `AC9_Separated` on the Guide (Basis keeps the original drape) and automatically switches **3D Source** to **Separated** | Object Mode | it can fail to converge — the result line then reports how many contacts are still under the gap threshold (plus a note about Solidify thickness if that's likely the cause), along with the maximum displacement and the p99 normal deviation (degrees), so you can check numerically how much it distorted the shape. **Separation Settings** covers **Gap** / **Smooth Radius** / **Max Iterations** / **Include Solidify** |
| **Contact → ×** | removes `AC9_Separated` and `AC9_Gap`, reverting to Basis | `AC9_Separated` exists | — |
| **3D Source** | the shape key that projection and the maps read as the Guide's 3D shape. **Original** (Basis, the draped shape as exported — use for the final retopo) / **Separated** (`AC9_Separated`, used while baking) | separation done | switching discards the Guide's triangle cache. The Mirror and projections catch up at the next **Refresh** |
| **Align → To Outline** | for each vertex within **Threshold** of the Guide's pattern outline (UV seam edges), snaps its 2D position onto the edge and rebuilds its attachment so one of the barycentric coordinates is exactly 0. Also flags it as boundary | Guide + Retopo / Experimental on | the pre-processing step that the old **Sync 3D > 2D** needed to pin things exactly to the outline. Run once after **Sync 2D > 3D** |
| **Threshold** | the snap tolerance above (default 0.003 = 3 mm) | Experimental on | too large, and interior vertices get misclassified as boundary |
| **Finalize → Finalize** | bakes the Retopo + the Guide's projection into a plain mesh `<Retopo name>_Final`. Shape = projection onto the Guide, UV = the 2D layout, no shape keys, no `ac9_*` data | Object Mode / Retopo, Guide, and Flat SK all set | the Retopo is untouched (stays flat and editable). Can be run any number of times. The resulting `_Final` becomes the selected/active object |
| **Finalize → Close Seam Gaps** | pulls the two sides of every sewn seam onto one 3D point (on by default) | same | positions only — nothing is created, removed or merged, and the UV islands stay split |
| **Finalize → Weld Seam Vertices** | merges each group of now-coincident seam vertices into one (off by default) | the above is on | welding stops the seams being boundary edges |

**Why there is a gap at all.** Finalize projects each vertex onto the Guide independently. Two vertices on opposite sides of a seam are the same spot on the garment, but they travel separately and land on separate points. The retopo is not at fault — the Guide is. CLO sews panel edges without making them coincident: measured over 400 seam pairs of a trouser Guide, the two sides sat up to 2.061 mm apart, 0.941 mm on average, and the retopo reproduces that faithfully. The result was a crack in the Finalize output measuring up to 0.857 mm, 0.066 mm on average, with 33 of 170 pairs over 0.1 mm.

**Close Seam Gaps** (on by default) moves each matched pair to its midpoint. Positions only — the vertex and face counts are unchanged — and a vertex moves at most half the gap it closes (0.428 mm at most in that measurement). Only pairs whose ghost is placed (green) are touched; an unplaced (red) ghost has no counterpart to meet, so those are left alone and counted in the result line. Layered seams (a pocket you marked with Mark Sharp) are excluded: that is a separate layer resting on the surface, genuinely apart in 3D.

**Weld Seam Vertices** (off by default) then merges each group of coincident vertices into one. It is off because the Final goes to baking next, where the two sides of a seam are wanted as separate vertices. Splitting a welded mesh back apart per UV island is real work; welding an unwelded one is a single Merge by Distance. The default is the direction that is easy to undo.

**Hidden geometry follows the Retopo.** While the Retopo is in Edit Mode, whatever you hide or
reveal on it with **H** / **Alt+H** lands in the same place on the Mirror with no button pressed
(**Refresh** ends with the same sync, so a state hidden back in Object Mode is picked up too). It
travels one way, Retopo → Mirror: hiding on the Mirror alone leaves the Retopo untouched and is
undone by the next sync. Blender only draws geometry as hidden in Edit Mode, so this shows when
the Mirror is in Edit Mode as well (see "Put the Retopo AND the Mirror in Edit Mode together" in
[03_workflow.md](03_workflow.md)); a Mirror in Object Mode keeps drawing the whole mesh.
Selection is not touched. **Nothing is synced while Subdiv Preview is on** — the subdivided Mirror has no vertex
correspondence with the Retopo — so turn the preview off, or apply **Subdivide** to the Retopo
first (a known gap, to be closed later).

### Separation Settings (sub-panel)

**Gap** / **Smooth Radius** / **Max Iterations** / **Include Solidify**.

---

## Guide Maps

Diagnostic bakes. Collapsed by default. Each result image is named after **the Guide it came from** (`AC9_SagMap_<Guide name>`), so a jacket, a pair of trousers and a skirt worked on side by side in one file keep their own sets instead of overwriting each other. Keep one open in the Image Editor and it updates every time that Guide is re-baked.

| Button | What it does | Prerequisite | Result |
|---|---|---|---|
| **Resolution** | the size (square) of the bake images. **1024** (fast preview) / **2048** (recommended, default) / **4096** (slow, for final checks) | — | affects the three bakes below |
| **Residual → Bake** | the signed distance from the Guide's surface to the current retopo. Red = Guide is closer, blue = farther, white = matching, dark gray = not yet covered by the retopo | Object Mode / Retopo + Guide | image `AC9_ResidualMap_<Guide name>`. The result line reports RMS / p90 / max (mm) and the number of covered vertices. Which retopo it was measured against is recorded on the image and shown in the Baked Maps list as "vs `<retopo>`" |
| **Sag → Bake** | the signed distance from each pattern piece's (a seam-bounded island's) best-fit plane. White = bulging toward the viewer, black = sinking away, mid-gray = flat | Object Mode / Guide | image `AC9_SagMap_<Guide name>`. Contour lines follow the edge-loop flow of low-frequency sagging. The result line reports piece count and max deviation |
| **Drape → Bake** | bakes Ambient Occlusion and Curvature (Geometry Pointiness) off the Guide's **3D shape** onto its flat layout, then multiplies the two. The Flat SK is blended out for the bake and restored afterwards | Object Mode / Guide | image `AC9_DrapeMap_<Guide name>`. Useful as a guide for where to knife-cut in 2D. The AO and Curvature passes are deleted once the product exists — keep them with **Keep Passes** |
| **Preview** | which map the Preview Plane displays (**Residual** / **Sag** / **Drape**, plus **AO** / **Curvature** while **Keep Passes** is on) | — | switching swaps the reference image on the preview material. Every mode is a single baked image, so Solid > Texture can draw all of them. It resolves against **the current Guide**, and follows a Guide swap (going blank when that Guide has no such bake yet) |
| **Plane** | creates (or reuses) a 1×1 m plane `AC9_BakePreview` in Flat SK space. Uses an Emission material so it's visible without the Image Editor | Object Mode | switches a Solid-shading viewport to **Solid color = Texture** and turns on the **Retopology overlay** — X-Ray is not used, and is switched back off if an earlier version left it on. Material Preview / Rendered viewports are untouched. **The plane sits 5 mm below z = 0**, so anything else lying flat at z = 0 (the Guide in its Flat SK pose, the flat retopo, a hand-made bake board) covers it from above. Hide those **by hand** — this button does not touch other objects' visibility |
| **Solid** | the viewport's own Solid color source (the same property as Viewport Shading > Color) | inside a 3D viewport | the map won't be visible unless this is set to **Texture**. It's surfaced here because many people don't know about this setting |

**Blender's interface is fully blocked while a bake runs.** The bake itself (`bpy.ops.object.bake`) is called directly, so there is no progress and no cancel (**Drape** bakes twice internally: AO, then Curvature). On a GPU a 2048 map takes seconds; **on the CPU it takes a minute or more** (measured on a 202k-vert Guide at 2048: **67.6 s on the CPU against 12.4 s on the GPU**).

Two things keep that down. The **AO ray budget is 1,024 rays per pixel** (16 Cycles samples x 64 AO node samples — Cycles caps the node's own samples at 128, so writing more than that does nothing). And **every object other than the Guide is hidden from the render for the duration of the bake**: a bake renders the scene and writes the result into the target's UVs, so everything with `hide_render` off is synced and gets a BVH. The AO only counts the Guide itself as an occluder, so the map does not change (measured difference: 0.0000), but the time does — **32% on the CPU, 13% on the GPU**.

That is why the add-on preferences (Edit > Preferences > Add-ons > AC9 Cloth Retopo) carry **Bake on GPU when available** (on by default). With a GPU enabled in Preferences > System, the scene's Cycles device is switched to GPU **for the duration of the bake only** and put back afterwards. **A scene that has never rendered with Cycles carries CPU**, so without this a machine with a perfectly good GPU still bakes on the CPU. Turn it off if a bake fails for lack of VRAM — the scene's own device is then used. On a machine with no GPU, picking 2048 shows a warning in the panel.

The Guide may be hidden while you bake: **the eye icon, the monitor icon, selectability, an excluded collection, a hidden collection and local view (`/`) are all lifted for the bake and restored afterwards.**

**Whether a map stays in the file is per map.** **Map Settings → Keep in file** (on by default) packs its pixels into the .blend so it survives a reload. The pack is a **16-bit PNG**, not the raw float buffer, so it costs about **3 MB per 2K map** instead of 50 (measured), at a quantisation of 7.7e-06 on a map that is looked at rather than measured. Off makes it session-only: the image is dropped when the file is reopened — an unpacked map comes back black, and dropping it beats keeping a black one — so press **Bake** again (measured: the whole drape set at 2048 re-bakes in under 5 s on a 283k-vert Guide, GPU).

JPEG would get that down to 0.18 MB and is deliberately **not** used: its error (max 0.060 of the 0..1 range, measured) lands on the pixels either side of the crease lines, which is exactly what the Drape map is there to be cut along.

### Baked Maps (sub-panel)

What this file is carrying. Maps are **grouped per Guide**, with the current Guide's box first. One row per map: kind, resolution, and what it costs in the file (`session` when nothing is packed); Residual rows also say which retopo they were measured against.

| Button | What it does |
|---|---|
| **×** on a group header | delete every map of that Guide |
| **×** on a row | delete that one map |
| **trash** in the header | delete every map in the file (asks first) |

The header shows the count and the total MB in the .blend. The maps are diagnostics — re-bake whatever you still need. This is not **Clear All**: Clear All removes every trace of the add-on, these buttons only reclaim space while you work.

**Why the composite is baked, not made of nodes**: Solid > Texture shading draws a material's **active Image Texture node** straight to the screen and never evaluates the node tree — so a node-side multiply shows nothing in the very mode this map is looked at in. The multiply therefore happens at bake time, and the ratio is **Map Settings → AO Mix** (change it and re-bake; measured 4.7 s).

**The three drape maps are not packed into the .blend.** They are 32-bit float, 50 MB per image at 2K and 150 MB for the set, and embedding that is not worth it: measured on a production Guide (283k verts, GPU), all three re-bake at 2048 in 4.7 s. Press **Bake** again after reopening the file. Residual and Sag are still packed.

### Map Settings (sub-panel)

| Setting | Meaning |
|---|---|
| **Residual Scale** | the distance (mm, default 10) at which the residual map fully saturates to red/blue. **Keep this fixed across iterations** — that way "whiter than before" really does mean it got closer |
| **Coverage Margin** | how far, in the 2D plane, a Guide vertex can be from the retopo's footprint and still count as "covered" (mm, default 2). Beyond this, the residual map goes dark gray |
| **Sag Scale** | the distance (mm, default 20) at which plane-fit deviation becomes pure white/black. Mid-gray is on-plane |
| **AO Mix** | how much of the AO goes into the combined `AC9_DrapeMap` (default 0.7). The formula is `Curvature × (1 - mix + mix × AO)` — the same arithmetic as a Mix node set to MULTIPLY with this as its Factor. At 1.0 the AO's dark folds bury the Curvature creases you are actually cutting along |
| **Keep in file** | one per map (Residual / Sag / Drape, all on by default). On = pack the pixels into the .blend as a 16-bit PNG so the map survives a reload (about 3 MB per 2K map, quantisation 7.7e-06, measured). Off = session-only |
| **Keep Passes** | keep the Drape AO and Curvature passes as images of their own after combining (off by default), for looking at each in Solid. Nothing re-reads them (**Bake** always re-bakes both) and the pair costs 128 MiB of RAM per garment at 2K, so they are deleted by default. Session-only either way |
| **AO Distance** | how far (mm, default 30) the drape AO looks for occluders — the scale of detail the map reports. Around a fold's own width it draws folds. Far above that it only reports how enclosed a region is, and **any panel sewn flat onto another (pocket, placket, tab) goes solid black**, because its neighbour is well inside the distance. Solid black panels? Lower this first. The Guide occludes itself only, so the body never darkens it |

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
| **Boundary** | for 2D boundary work: seam guides, free edges, creases, Folds, Twins, ghosts, anchors, vertex-count parity. Folds and Twins are here because **Generate** places vertices on the fold axis and **Self** / **Twin** rebuild one side from the other, so both are wanted while the boundary is being built |
| **Seams** | for reading the Guide's seam structure: seams, free edges, Folds, Twins, the white outline, ghosts. **Pair Lines** is left off: with all of those drawn on the same edges it reads as noise (still a toggle) |
| **Status** | for reading the **Verify -> Status** verdict: the status colors, the white outline, ghosts, anchors, vertex-count parity. The structural line colors (seams, free edges) are turned OFF here on purpose — the status colors are painted over those same edges and win, so leaving both on means one edge is being colored by two systems at once |
| **3D Check** | for viewing the Mirror in 3D: the white outline, nothing else |
| **All Off** | turns every individual overlay off (leaves the master switch alone) |

The **Seam Status** toggle only ever comes on with the **Status** preset, and while it is on the **Seams** and **Free Edges** rows are grayed out with a note: they are still ON, they are just being painted over.

**Selection Link** is deliberately outside the presets' control. Since it's useful in every working mode (seeing where a 2D selection lands on the Mirror, and vice versa), the presets never touch it.

### Toggles

| Box | Toggles |
|---|---|
| **Seam Lines** | **Seams (cyan)** / **Free Edges (yellow)** / **Pair Lines** / **Fold Lines** / **Twins (magenta)** / **Creases (Find Folds)** / **Outline (white)** — Fold Lines and Twins sit together: they are the two halves of one answer (a panel is either symmetric within itself or has a left/right partner) |
| **Marks** | a refresh button for **Analyze Anchors** in the header. **Anchors** / **Corners** (Experimental) / **Pins** (Experimental). Turning on **Anchors** without an analysis shows a red warning. Once analyzed, the anchor count and span count are shown |
| **Ghosts** | a manual refresh (**Refresh Ghosts**) and **×** (**Clear Ghost Points**) in the header. **Selected Only** / **Points** (enables **Only Unplaced** and **Orphan Rings** when on) / **Lines** (enables **Unplaced Lines Only** when on) / **Snap Radius** |
| **Status** | **Vertex Counts** / **Seam Status** / **Boundary Flags** |
| **Mirror** | **Selection Link** — draws orange markers at the live Guide projection for a 2D selection, or at the Refresh-time 2D source position for a Mirror selection. Works for vertex, edge, face, loop, and shortest-path selections. A vertex created directly on the disposable Mirror has no source marker until Refresh rebuilds it. Markers follow the **active object's** selection only (the one selected last), never both at once. A 2D selection's markers use the Guide projection cache, so **right after opening a file they do not appear until Refresh has been pressed once** (likewise after editing or swapping the Guide). A Mirror selection's markers need no cache and always show |
| **Guide** | an **Islands** slider (**Alpha**) and **Bake** / **×** — detects the Guide's UV islands, writes them to a color attribute, and builds a simple material to display it. Not a GPU overlay, but it's kept here since it answers the same question of "what does the Guide look like" |

**Seams (cyan)** and **Free Edges (yellow)** split the pattern outline in two in the flat layout: cyan for edges sewn to another panel, yellow for the free ones (hems, necklines, openings). A CLO export need not carry a single `use_seam` flag — measured on a production Guide, 5,133 boundary edges and 0 `use_seam` — in which case the yellow lines are the only place free edges show up in the flat layout. The 3D-side **Outline (white)** draws every open-boundary edge of the Guide (plus any UV-seam-marked edge), so it appears whether or not `use_seam` is set.

Most of this is drawn from the cache that **Analyze Seams** fills. That cache is emptied on file load and Reload Scripts, so when it's empty this panel also shows "Seams not analyzed" with an **Analyze** button.

![The Overlays panel: master switch, presets (Boundary / Seams / Status / 3D Check / All Off), the individual toggles, and a viewport with the overlays drawn.](../images/04_overlays_panel.png)

### Appearance (sub-panel)

Purely cosmetic settings — color, line width, marker size. Touching these never triggers analysis.
**Seam** / **Free Edge** / **Seam Width** / **Ghost (Unplaced)** / **Ghost (Placed)** / **Ghost Line** / **Ghost Cross Size** / **Ghost Line Width** / **Fold** / **Fold Width** / **Anchor** / **Anchor Cross Size** / **Link Point Size** / **Boundary Cross Size** / **Z Offset**.

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
