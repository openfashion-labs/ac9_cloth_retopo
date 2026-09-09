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


def draw_fold(layout, data, prop, text):
    """A one-line disclosure heading. Returns a column to draw into when open.

    Used to tuck away the tools of the OTHER mode (Object vs Edit): they stay
    reachable, take one line when folded, and their operators grey out via
    their own poll() when opened in the wrong mode.
    """
    expanded = bool(getattr(data, prop))
    row = layout.row(align=True)
    row.prop(data, prop, text=text, emboss=False,
             icon='DISCLOSURE_TRI_DOWN' if expanded else 'DISCLOSURE_TRI_RIGHT')
    if not expanded:
        return None
    return layout.column()


def guide_ready(layout, top):
    """Blocker for every panel that reads the Guide. True when usable."""
    if top is None:
        return False
    if top.guide_obj is None or not top.guide_flat_shapekey:
        draw_blocker(layout, "Set the Guide and Flat SK first")
        return False
    return True


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
