"""Shared drawing helpers for every AC9 Cloth Retopo panel.

The panels used to grow one labelled box per feature, each with its own
heading, explanation lines, settings, buttons and result string. These helpers
fix the grammar instead, so the same kind of control looks the same in every
panel:

  labeled_row      "Label   [button] [button]" — one action per row
  draw_check_apply "[Check] [Apply]" — dry run beside the real thing
  draw_make_clear  "[Make] [x]" — a derived thing and the button that removes it
  draw_mark        "Noun  [+] [-] [x]" — a user mark: add / remove / remove all
  draw_blocker     one alert row: what is missing + the button that fixes it
  draw_hint        one plain row: a soft prerequisite (works, but degraded)
  draw_status      one dimmed row: the last result of this panel

Rules the helpers encode (see UI_REDESIGN_PROPOSAL.md, section 3):
  - panels hold verbs; tuning values live in a "... Settings" child panel
  - explanations belong in bl_description / property descriptions, not labels
  - the only text a panel may show is an actionable warning or a result line
  - every user-facing string is one complete literal (translation-ready)
"""

import functools
import os
import time

import bpy


LABEL_FACTOR = 0.30  # width share of the left-hand label in labeled_row


def get_top(context):
    """Return the top-level AC9ClothRetopoProps, or None."""
    return getattr(getattr(context, "scene", None), "ac9_cloth_retopo", None)


def experimental_enabled(context) -> bool:
    """True when Add-on Preferences > Experimental tools is on.

    False on any failure to reach it — no Preferences entry yet (the add-on
    was loaded by `register()` directly, e.g. dev/test harnesses, rather than
    enabled through Blender's add-on system), no `context.preferences`, etc.
    Missing prefs must read as "off", never raise.
    """
    try:
        return bool(
            context.preferences.addons[__package__].preferences.experimental
        )
    except Exception:
        return False


_shapely_present = None


def shapely_available() -> bool:
    """True when shapely can be imported.

    Cached for the session on purpose: shapely arrives as a wheel Blender
    installs when the add-on is enabled, and a hand `pip install` into
    Blender's Python needs a restart to be seen anyway - so this answer
    cannot change while Blender runs, and a panel draw() must not pay for
    find_spec on every redraw.
    """
    global _shapely_present
    if _shapely_present is None:
        import importlib.util
        try:
            _shapely_present = importlib.util.find_spec("shapely") is not None
        except Exception:
            _shapely_present = False
    return _shapely_present


def labeled_row(layout, text, factor=LABEL_FACTOR, align=True):
    """Draw "text" in a fixed-width left column; return the row to its right.

    Every action row in the tab uses this, so the buttons of different panels
    line up in the same column and the label column reads as a table of
    contents for the panel.
    """
    split = layout.split(factor=factor, align=align)
    split.label(text=text)
    return split.row(align=True)


def draw_check_apply(row, idname, apply_text, check_text="Check",
                     apply_icon='CHECKMARK', prop="apply", scale_y=1.2):
    """[Check] [Apply]: the same operator twice, dry run first.

    Returns (check_op, apply_op) so the caller can set further properties on
    both (e.g. `pin`). `prop` is the operator's dry-run flag.
    """
    row.scale_y = scale_y
    op_check = row.operator(idname, text=check_text, icon='VIEWZOOM')
    setattr(op_check, prop, False)
    op_apply = row.operator(idname, text=apply_text, icon=apply_icon)
    setattr(op_apply, prop, True)
    return op_check, op_apply


def draw_make_clear(row, make_id, clear_id, text, icon='NONE', scale_y=1.2):
    """[Make] [x]: create some derived data, and the button that removes it.

    Returns (make_op, clear_op) so the caller can set properties on either.
    """
    row.scale_y = scale_y
    op_make = row.operator(make_id, text=text, icon=icon)
    op_clear = row.operator(clear_id, text="", icon='X')
    return op_make, op_clear


def draw_mark(layout, text, mark_id, clear_id, all_prop,
              detect_id=None, detect_text="Detect", factor=LABEL_FACTOR):
    """"Noun  [+] [-] [x] ([Detect])": a user-placed mark on the retopo.

    + marks the selection, - unmarks the selection, x removes every mark
    (`all_prop` is the clear operator's "all" flag). `detect_id` adds an
    optional proposal button.
    """
    row = labeled_row(layout, text, factor=factor)
    row.operator(mark_id, text="", icon='ADD')
    op = row.operator(clear_id, text="", icon='REMOVE')
    setattr(op, all_prop, False)
    op = row.operator(clear_id, text="", icon='X')
    setattr(op, all_prop, True)
    if detect_id is not None:
        row.operator(detect_id, text=detect_text, icon='ZOOM_SELECTED')
    return row


def draw_blocker(layout, text, op_idname=None, op_text="", op_icon='NONE',
                 icon='ERROR'):
    """One alert row: what is missing, and the button that fixes it.

    Use only for a hard prerequisite — the operators below it cannot run (or
    the overlays draw nothing) until it is met. Returns the fix operator so the
    caller can set properties on it, or None.
    """
    box = layout.box()
    box.alert = True
    row = box.row(align=True)
    row.label(text=text, icon=icon)
    if op_idname is None:
        return None
    return row.operator(op_idname, text=op_text, icon=op_icon)


def draw_hint(layout, text, op_idname=None, op_text="", op_icon='NONE'):
    """One plain row for a SOFT prerequisite: things run, but degraded.

    Example: Generate works without a symmetry analysis, but then it cannot
    put vertices on the fold axes. Not alert — nothing is broken. Returns the
    fix operator, or None.
    """
    row = layout.row(align=True)
    row.label(text=text, icon='INFO')
    if op_idname is None:
        return None
    return row.operator(op_idname, text=op_text, icon=op_icon)


def draw_status(layout, text):
    """The panel's single result line, dimmed. Draws nothing when empty."""
    if not text:
        return
    row = layout.row()
    row.active = False
    row.label(text=text, icon='INFO')


def draw_heading(layout, text, icon='NONE'):
    """A section heading inside a column (a label, not a box)."""
    layout.label(text=text, icon=icon)


def guide_ready(layout, top):
    """Blocker for every panel that reads the Guide. True when usable.

    Also blocks on an unapplied object scale and on a Flat SK whose scale is
    implausible. Both are put here rather than in each panel because this is
    the one gate every Guide-reading panel already passes through, and both
    failures are silent otherwise: Create Flat SK writes the UV layout into
    local coordinates, so an unapplied scale shrinks the flat layout in world
    space (measured: a 413 mm layout becomes 0.41 mm at scale 0.001, what an
    FBX import leaves behind) and every flat-space distance in the addon is
    then read at the wrong size, with no error anywhere.

    The ROTATION is blocked here for the same reason and at the same cost:
    Create Flat SK writes the layout into local XY, so an unapplied rotation
    (X 90 degrees is what an FBX import leaves behind) puts it in world XZ,
    and every reader that uses .x / .y sees a line — measured 2026-09-08:
    18 of 18 islands reported "symmetric" and Generate laid a vertex on every
    Guide vertex instead of at the requested spacing.

    Only the free MATRIX checks (scale, rotation) are made here, not the Flat
    SK's own ratio or plane. This runs in a panel draw(), which Blender calls
    on every redraw — reading matrix_world is free, but the ratio needs a pass
    over every edge (500k on a production jacket) and would make the sidebar
    crawl. Both are checked where they cost nothing: validate_guide, i.e. when
    an operator actually runs. That case (a Flat SK built while the scale was unapplied and the
    scale applied afterwards — Apply Scale transforms ShapeKeys too, so the key
    stays exactly as wrong while the object starts passing the scale check)
    therefore surfaces on the first button press rather than in the panel, and
    still cannot be missed.
    """
    if top is None:
        return False
    if top.guide_obj is None or not top.guide_flat_shapekey:
        draw_blocker(layout, "Set the Guide and Flat SK first")
        return False
    return guide_set(layout, top)


def guide_set(layout, top):
    """Blocker for Guide Prep: a Guide object is picked and its transform is
    applied. True when usable.

    Everything guide_ready checks EXCEPT the Flat SK, because Guide Prep is
    what creates the Flat SK — gating it on the key existing would lock the
    user out of step 1. The scale and rotation are still checked (same reason
    as in guide_ready: Create Flat SK writes the layout into local
    coordinates, so an unapplied transform silently produces a Flat SK at the
    wrong size or standing up in Z).
    """
    if top is None:
        return False
    if top.guide_obj is None or top.guide_obj.type != 'MESH':
        draw_blocker(layout, "Set the Guide first (Setup)")
        return False
    from .clo_projector import guide as _pguide
    if _pguide.object_scale_error(top.guide_obj) is not None:
        s = top.guide_obj.matrix_world.to_scale()
        draw_blocker(
            layout,
            f"Guide scale is not 1 ({s.x:.4g}, {s.y:.4g}, {s.z:.4g}) — "
            f"Object > Apply > Scale, then re-create the Flat SK")
        return False
    if _pguide.object_rotation_error(top.guide_obj) is not None:
        draw_blocker(
            layout,
            "Guide rotation is not applied — the flat layout stands up in Z "
            "and every 2D reader sees a line. Object > Apply > Rotation, then "
            "re-create the Flat SK")
        return False
    return True


def guide_mode(top):
    """'EDIT', 'OBJECT' or '' — the Guide object's mode, not the context's.

    Guide Prep draws by what the user is doing TO THE GUIDE, exactly as the
    other panels do with retopo_mode: another object being in Edit Mode does
    not change what its buttons will touch.
    """
    guide = top.guide_obj if top is not None else None
    if guide is None or guide.type != 'MESH':
        return ''
    return 'EDIT' if guide.mode == 'EDIT' else 'OBJECT'


def retopo_ready(layout, top):
    """Blocker for every panel that writes the Retopo. True when usable."""
    if top is None:
        return False
    if top.retopo_obj is None or top.retopo_obj.type != 'MESH':
        draw_blocker(layout, "Set the Retopo first")
        return False
    return True


def retopo_mode(top):
    """'EDIT', 'OBJECT' or '' — the Retopo object's mode, not the context's.

    Panels draw by what the user is doing TO THE RETOPO; another object being
    in Edit Mode (say, the Guide) does not switch them.
    """
    retopo = top.retopo_obj if top is not None else None
    if retopo is None:
        return ''
    return 'EDIT' if retopo.mode == 'EDIT' else 'OBJECT'


class ProgressThrottle:
    """Calls wm.progress_update only when the integer percent (lo..hi) changes.

    wm.progress_update is free headless but redraws the cursor in the GUI —
    measured at ~1.4 ms per call — so calling it once per vertex/item turns a
    fast loop into a slow one purely from cursor redraws (a 4153-vertex
    Refresh Mirror measured at 6 s of pure cursor updates before this). Every
    per-item progress loop should map its 0..1 fraction through an instance of
    this instead of calling wm.progress_update directly.
    """

    def __init__(self, wm, lo=0, hi=100):
        self._wm = wm
        self._lo = lo
        self._hi = hi
        self._last_pct = -1

    def __call__(self, fraction_0_to_1):
        fraction_0_to_1 = max(0.0, min(1.0, fraction_0_to_1))
        pct = int(self._lo + fraction_0_to_1 * (self._hi - self._lo))
        if pct != self._last_pct:
            self._last_pct = pct
            self._wm.progress_update(pct)

    def set(self, pct):
        """Set an absolute percent directly (already in wm's 0..100 space),
        skipping the wm call when it repeats the last one sent."""
        pct = int(pct)
        if pct != self._last_pct:
            self._last_pct = pct
            self._wm.progress_update(pct)


class ProgressScope:
    """A wm.progress_begin/update/end context manager that nests.

    Several operators call each other (Analyze Guide calls Analyze Seams,
    then Analyze Symmetry, then Analyze Anchors), and each one used to open
    its own progress_begin/end — so running the parent showed three separate
    progress bars back to back instead of one. Nest ProgressScope instances
    instead: only the OUTERMOST one calls wm.progress_begin(0, 100) /
    progress_end(); every inner one just maps its own 0..1 fraction into the
    slice of the outer scope it was given (`lo`/`hi`, in the outer scope's own
    0..100 space) and calls wm.progress_update through it — deduplicated
    exactly like ProgressThrottle, shared across the whole nested chain so
    two scopes never send the same percent twice in a row.

    Called standalone (nothing else on the stack), a scope behaves exactly
    like the old progress_begin(0, 100)/progress_end() pair — so an operator
    that always wraps its body in `with ProgressScope(wm) as prog:` shows one
    bar whether it is pressed directly or driven by a caller that nests it
    into a wider one.

    Usage — the operator that composes others reserves each child's slice:

        with ProgressScope(wm) as prog:
            with ProgressScope(wm, 0, 35):
                bpy.ops.ac9_cloth.analyze_uv_seam_pairs('EXEC_DEFAULT')
            with ProgressScope(wm, 35, 85):
                bpy.ops.ac9_cloth.analyze_guide('EXEC_DEFAULT')
            with ProgressScope(wm, 85, 100):
                bpy.ops.ac9_cloth.analyze_anchors('EXEC_DEFAULT')

    and each child operator just wraps its own body the same way it would if
    it were the only thing running:

        def execute(self, context):
            wm = context.window_manager
            with ProgressScope(wm) as prog:
                ...
                prog.update(0.5)
                ...
            return {'FINISHED'}

    `_stack` is a class attribute — the current chain of active scopes,
    outermost first. Safe for Blender's single-threaded operator execution;
    not reentrant across threads.
    """

    _stack = []

    # Bumped by every __enter__ — the progress audit compares it before and
    # after an operator to see whether a bar was shown at all, which a
    # look at _stack afterwards cannot tell (by then it has been popped).
    _enter_count = 0

    def __init__(self, wm, lo=0, hi=100):
        self._wm = wm
        self._lo = lo
        self._hi = hi
        self._root = None
        self._outer_lo = 0
        self._outer_hi = 100

    def __enter__(self):
        ProgressScope._enter_count += 1
        stack = ProgressScope._stack
        if stack:
            parent = stack[-1]
            root = parent._root
            span = parent._outer_hi - parent._outer_lo
            self._outer_lo = parent._outer_lo + span * (self._lo / 100.0)
            self._outer_hi = parent._outer_lo + span * (self._hi / 100.0)
        else:
            root = self
            root._last_pct = -1
            self._outer_lo, self._outer_hi = self._lo, self._hi
            self._wm.progress_begin(0, 100)
        self._root = root
        stack.append(self)
        return self

    def update(self, fraction_0_to_1):
        fraction_0_to_1 = max(0.0, min(1.0, fraction_0_to_1))
        pct = int(round(
            self._outer_lo + fraction_0_to_1 * (self._outer_hi - self._outer_lo)
        ))
        root = self._root
        if pct != root._last_pct:
            root._last_pct = pct
            root._wm.progress_update(pct)

    def __exit__(self, exc_type, exc, tb):
        ProgressScope._stack.pop()
        if self._root is self:
            self._wm.progress_end()
        return False


# ── Progress audit: measure the symptom, not the code shape ──────────────────
#
# The rule in this add-on is that anything the user waits on shows progress.
# Two hand audits have now both under-counted the misses (the second one found
# only 22 of 80 operators wrapped), and a static check on the operator source
# cannot fix that: the slow part is almost always inside a core.* call, so a
# body with no visible loop passes, while every operator that only touches the
# retopo (about 4 k vertices, instant) fails and needs a whitelist bigger than
# the add-on.
#
# So measure the symptom instead. register() runs every AC9_OT_* class through
# audit_wrap_operators(), which replaces `execute` with a wrapper that times
# the call and remembers whether a ProgressScope was active at any point while
# it ran. An operator that made the user wait a second or more with no progress
# is exactly the bug we keep failing to spot by reading, and the wrapper sees
# it in the GUI and headless alike, with no list of exceptions to maintain.
#
# Reporting is off unless asked for: Preferences > Add-ons > AC9 Cloth Retopo >
# "Report slow operators", or the AC9_PROGRESS_AUDIT environment variable
# (headless tests set that). The measuring itself always runs — a perf_counter
# pair and a dict update per operator — so progress_audit_report() works in a
# test without touching Preferences, and turning the switch on mid-session
# needs no reload.

PROGRESS_AUDIT_ENV = "AC9_PROGRESS_AUDIT"

# An operator slower than this with no progress shown is reported. One second
# is roughly where a press stops feeling like it landed and starts feeling
# broken; below it, a progress bar would only flicker.
PROGRESS_AUDIT_SLOW_SEC = 1.0

# bl_idname -> {"calls", "total", "worst", "worst_bare", "bare_slow"}
_audit_records = {}

# (cls, method_name, original_method_or_None) for audit_unwrap_operators()
_audit_wrapped = []


def progress_audit_enabled() -> bool:
    """True when a missing-progress report should be printed.

    Environment variable first (a headless test cannot set Preferences), then
    the add-on preference. Any failure to reach either reads as off.
    """
    env = os.environ.get(PROGRESS_AUDIT_ENV, "")
    if env and env not in {"0", "false", "False", ""}:
        return True
    try:
        return bool(
            bpy.context.preferences.addons[
                __package__
            ].preferences.progress_audit
        )
    except Exception:
        return False


def _audit_record(idname, seconds, had_progress):
    rec = _audit_records.get(idname)
    if rec is None:
        rec = _audit_records[idname] = {
            "calls": 0, "total": 0.0, "worst": 0.0,
            "worst_bare": 0.0, "bare_slow": 0,
        }
    rec["calls"] += 1
    rec["total"] += seconds
    rec["worst"] = max(rec["worst"], seconds)
    if not had_progress:
        rec["worst_bare"] = max(rec["worst_bare"], seconds)
        if seconds >= PROGRESS_AUDIT_SLOW_SEC:
            rec["bare_slow"] += 1
            if progress_audit_enabled():
                print(
                    f"[AC9] {idname} took {seconds:.1f} s without progress"
                )


def _audited(idname, method, arity):
    """`method` wrapped so it is timed. `arity` keeps the signature Blender
    checks: register_class insists that execute takes (self, context) and
    invoke takes (self, context, event), and it counts the arguments of the
    function it finds on the class — so the wrapper cannot use *args."""
    def wrapper(self, context):
        # Progress counts as shown when a scope was already open (this
        # operator was driven by one that reserved a slice for it) or when
        # one was opened while it ran.
        outer = bool(ProgressScope._stack)
        entered = ProgressScope._enter_count
        t0 = time.perf_counter()
        try:
            return method(self, context)
        finally:
            _audit_record(
                idname,
                time.perf_counter() - t0,
                outer or ProgressScope._enter_count != entered,
            )

    def wrapper3(self, context, event):
        outer = bool(ProgressScope._stack)
        entered = ProgressScope._enter_count
        t0 = time.perf_counter()
        try:
            return method(self, context, event)
        finally:
            _audit_record(
                idname,
                time.perf_counter() - t0,
                outer or ProgressScope._enter_count != entered,
            )

    chosen = wrapper if arity == 2 else wrapper3
    functools.update_wrapper(chosen, method)
    chosen._ac9_audited = True
    return chosen


def audit_wrap_operators(classes):
    """Time every operator in `classes`, remembering the ones without progress.

    Call from register(), before register_class — Blender looks the method up
    on the class at every invocation, so either order works, but wrapping
    first keeps the registered class and the timed class the same object.

    `execute` for a normal operator; `invoke` for the modal ones (the Snap
    Moves), where the wait is the setup before the drag starts and there is no
    execute to time.
    """
    for cls in classes:
        idname = getattr(cls, "bl_idname", None)
        if idname is None:
            continue  # a panel or a PropertyGroup
        name = "execute" if getattr(cls, "execute", None) else "invoke"
        method = getattr(cls, name, None)
        if method is None:
            continue
        if getattr(method, "_ac9_audited", False):
            method = method.__wrapped__  # a reload left ours behind
        label = idname if name == "execute" else f"{idname} ({name})"
        _audit_wrapped.append((cls, name, cls.__dict__.get(name)))
        setattr(cls, name, _audited(label, method,
                                    2 if name == "execute" else 3))


def audit_unwrap_operators():
    """Undo audit_wrap_operators(). Call from unregister()."""
    while _audit_wrapped:
        cls, name, original = _audit_wrapped.pop()
        try:
            if original is None:
                delattr(cls, name)  # it was inherited; ours shadowed it
            else:
                setattr(cls, name, original)
        except Exception:
            pass


def progress_audit_report(slow_only=True):
    """The measurements so far as text, worst first. '' when nothing ran."""
    rows = [
        (idname, rec) for idname, rec in _audit_records.items()
        if not slow_only or rec["worst"] >= PROGRESS_AUDIT_SLOW_SEC
    ]
    if not rows:
        return ""
    rows.sort(key=lambda r: r[1]["worst"], reverse=True)
    width = max(len(idname) for idname, _ in rows)
    lines = [f"{'operator':<{width}}  {'worst':>8}  {'calls':>5}  progress"]
    for idname, rec in rows:
        shown = "MISSING" if rec["bare_slow"] else "ok"
        lines.append(
            f"{idname:<{width}}  {rec['worst']:>7.2f}s  {rec['calls']:>5}  {shown}"
        )
    return "\n".join(lines)


def progress_audit_reset():
    """Forget every measurement (a test measuring one operator at a time)."""
    _audit_records.clear()
