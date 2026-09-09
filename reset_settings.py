"""Reset Settings — put every tuning value in the AC9 property tree back to
its default, without touching the Retopo / Guide pickers or any mesh data.

Walks ``scene.ac9_cloth_retopo`` and every nested PropertyGroup it holds
(``.seam``, ``.proj``, ``.flat_merge``, ``.maps``, ``.separate``,
``.quad_fix``, ``.mesh_edit``, ``.clo``), calling ``property_unset`` on each
plain property it finds. Two kinds of property are deliberately left alone:

  - PointerProperty to an ID (Object pickers: ``retopo_obj``, ``guide_obj``,
    ``flat_merge.drape_obj``, ``flat_merge.grid_obj``, ...) — these are the
    user's scene wiring, not a tuning value.
  - The Flat SK name (``guide_flat_shapekey``) and every ``status*`` result
    line — also not tuning values.

A PointerProperty to another PropertyGroup is recursed into rather than
unset (unsetting it would do nothing useful — it has no scalar value of its
own, only nested properties do).
"""

import bpy


def _is_property_group_pointer(fixed_type) -> bool:
    """True when a POINTER property's fixed_type is a nested PropertyGroup
    (safe to recurse into) rather than an ID (Object, Image, ...) picker.
    Distinguished by RNA base chain: every custom PropertyGroup here
    subclasses PropertyGroup directly; an ID pointer's chain ends in ID —
    NOT in PropertyGroup, so it must never be recursed into (Object alone
    carries hundreds of RNA properties, several of them themselves POINTERs
    into further large structs — recursing into one is both wrong and, in
    practice, a runaway recursion / MemoryError, confirmed empirically).
    """
    base = fixed_type.base
    return base is not None and base.identifier == 'PropertyGroup'


def _reset_group(group) -> int:
    """Recursively property_unset every eligible property on `group`.
    Returns how many properties actually changed (were explicitly set)."""
    count = 0
    for prop in group.bl_rna.properties:
        name = prop.identifier
        if name == "rna_type":
            continue
        if prop.type == 'POINTER':
            if not _is_property_group_pointer(prop.fixed_type):
                continue  # Object/Image/... picker — leave the user's wiring alone
            sub = getattr(group, name)
            if sub is not None:
                count += _reset_group(sub)
            continue
        if name == "guide_flat_shapekey" or name.startswith("status"):
            continue
        if group.is_property_set(name):
            group.property_unset(name)
            count += 1
    return count


class AC9_OT_ResetSettings(bpy.types.Operator):
    """Reset every AC9 Cloth Retopo tuning value to its default. Leaves the
    Retopo / Guide / Flat SK pickers and all mesh data untouched"""

    bl_idname = "ac9_cloth.reset_settings"
    bl_label = "Reset Settings"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        top = getattr(context.scene, "ac9_cloth_retopo", None)
        if top is None:
            self.report({'ERROR'}, "AC9 Cloth Retopo properties not found.")
            return {'CANCELLED'}
        count = _reset_group(top)
        self.report({'INFO'}, f"Reset {count} setting(s) to default.")
        return {'FINISHED'}


def get_classes():
    return (AC9_OT_ResetSettings,)
