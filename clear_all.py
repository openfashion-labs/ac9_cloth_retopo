"""Clear All — remove every trace this add-on leaves in a .blend.

The add-on has no single "finish" step: the 3D result is either the Mirror
object or the retopo flipped to 3D by the legacy shape key, and the user joins
that into their final model by hand. What stays behind afterwards is spread
over the whole file:

  * the scene settings (which is also what keeps the header buttons showing)
  * the Mirror object(s), found by their ``ac9_mirror_of`` property
  * the Bake Preview Plane object (``AC9_BakePreview``, mesh + material),
    matched by name like the bake images below
  * on every mesh: the ``ac9_*`` / ``AC9_*`` attribute layers (attachments,
    status, pins, corners, grid/fill/scaffold markers, bake colour layers),
    the ``AC9_3D_Project`` and ``AC9_Separated`` shape keys, the
    ``AC9_Project_Failed`` vertex group, the ``AC9_*`` modifiers (older files
    still carry ``AC9_Preview_Subdiv`` from the Preview Subdiv step that was
    removed), the ``AC9_Island_Colors`` slot
  * ``ac9_*`` custom properties on the scene and on objects/meshes
  * the bake images, the temporary bake material, the report Text

Everything here is data the add-on wrote; the user's own geometry, UVs, the
Guide's Flat shape key and their materials are never touched.

One deliberate exception to "just delete": a retopo currently shown in 3D by
the legacy shape key (value > 0.5) would snap back to its 2D layout when the
key is removed. That is the one case where deleting loses work the user can
see, so the 3D coordinates are written into the mesh first — what is on
screen is what stays.

The prefix rule (`ac9_` / `AC9_` for everything persisted) is what makes a
generic sweep possible for attributes, modifiers and custom properties.
Datablocks (materials, texts) are matched by their exact names instead,
because ``AC9`` is also a product name the user may use in their own naming;
bake images additionally allow the per-Guide ``_<Guide name>`` suffix they
are keyed on (several garments in one file each get their own set).
"""

import bpy

from .clo_projector import core as _pcore
from .clo_projector import guide as _pguide
from .clo_projector import mirror as _mirror
from .clo_projector import operators as _pops
from .bake_maps import core as _bcore
from .bake_maps import preview as _bpreview

SCENE_GROUP = "ac9_cloth_retopo"
ATTR_PREFIXES = ("ac9_", "AC9_")
IDPROP_PREFIXES = ("ac9_", "_ac9_")

SHAPEKEY_NAME = _pcore.SHAPEKEY_NAME            # AC9_3D_Project
SEPARATED_SK = _pguide.SEPARATED_SK_NAME        # AC9_Separated (on the Guide)
VGROUP_NAME = _pcore.FAILED_GROUP_NAME          # AC9_Project_Failed
MIRROR_PROP = _mirror.MIRROR_PROP               # ac9_mirror_of
PREVIEW_PLANE_NAME = _bpreview.PLANE_OBJ_NAME   # AC9_BakePreview (object/mesh)
MATERIAL_NAMES = (_pops._ISLAND_MAT_NAME, "AC9_BakeMap_Temp",
                  _bpreview.PREVIEW_MAT_NAME)
# Bake images are matched by _bcore.is_map_image: a base name on its own
# (older files), the per-Guide "<base>_<Guide name>" that every bake writes
# now, or a numbered copy. The base is specific enough that the product name
# AC9 in a user's own image cannot collide.
TEXT_NAMES = ("AC9_MirrorPairRetopoMap", "AC9_SeamStatus")


def _named(name, wanted):
    """True when `name` is one of `wanted` or a numbered copy of one
    (``AC9_SagMap.001``)."""
    for w in wanted:
        if name == w or name.startswith(w + "."):
            return True
    return False


def _idprop_keys(idblock):
    try:
        return [k for k in idblock.keys() if k.startswith(IDPROP_PREFIXES)]
    except Exception:
        return []


def run(scene, dry_run=True):
    """Sweep the file. Returns a dict of counts (and, for the dialog, names).

    With dry_run the file is untouched and the counts say what WOULD go.
    """
    rep = {
        "mirrors": [], "preview_plane": [], "objects": 0, "attributes": 0,
        "shape_keys": 0, "baked_3d": [], "vertex_groups": 0, "modifiers": 0,
        "material_slots": 0, "idprops": 0, "images": [], "materials": [],
        "texts": [], "scene_reset": False,
    }

    # ---- Mirror objects: whole objects, found by their back-pointer
    for ob in list(bpy.data.objects):
        if ob.type == "MESH" and ob.get(MIRROR_PROP) is not None:
            rep["mirrors"].append(ob.name)
            if not dry_run:
                me = ob.data
                bpy.data.objects.remove(ob)
                if me is not None and me.users == 0:
                    bpy.data.meshes.remove(me)

    # ---- Bake Preview Plane: a whole standalone object, matched by name
    # (like the bake images below — AC9 is also a product name the user may
    # use for their own objects, so an exact/numbered-copy match only)
    for ob in list(bpy.data.objects):
        if ob.type == "MESH" and _named(ob.name, (PREVIEW_PLANE_NAME,)):
            rep["preview_plane"].append(ob.name)
            if not dry_run:
                me = ob.data
                bpy.data.objects.remove(ob)
                if me is not None and me.users == 0:
                    bpy.data.meshes.remove(me)

    # ---- every other object: strip what we wrote onto it
    seen_meshes = set()
    for ob in list(bpy.data.objects):
        if ob.type == "MESH" and ob.get(MIRROR_PROP) is not None:
            continue  # counted above (dry run leaves them in place)
        if ob.type == "MESH" and _named(ob.name, (PREVIEW_PLANE_NAME,)):
            continue  # counted above (dry run leaves it in place)
        touched = False
        keys = _idprop_keys(ob)
        rep["idprops"] += len(keys)
        if keys:
            touched = True
            if not dry_run:
                for k in keys:
                    del ob[k]
        if ob.type != "MESH":
            rep["objects"] += int(touched)
            continue

        vgs = [vg for vg in ob.vertex_groups if _named(vg.name, (VGROUP_NAME,))]
        rep["vertex_groups"] += len(vgs)
        if vgs:
            touched = True
            if not dry_run:
                for vg in vgs:
                    ob.vertex_groups.remove(vg)

        mods = [m for m in ob.modifiers if m.name.startswith(ATTR_PREFIXES)]
        rep["modifiers"] += len(mods)
        if mods:
            touched = True
            if not dry_run:
                for m in mods:
                    ob.modifiers.remove(m)

        me = ob.data
        sk = me.shape_keys
        kb = sk.key_blocks.get(SHAPEKEY_NAME) if sk is not None else None
        if kb is not None:
            rep["shape_keys"] += 1
            touched = True
            in_3d = kb.value > 0.5
            if in_3d:
                rep["baked_3d"].append(ob.name)
            if not dry_run:
                if in_3d:
                    # keep what is on screen: the 3D layout becomes the mesh
                    n = len(me.vertices)
                    buf = [0.0] * (n * 3)
                    kb.data.foreach_get("co", buf)
                    me.vertices.foreach_set("co", buf)
                    sk.reference_key.data.foreach_set("co", buf)
                ob.shape_key_remove(kb)
                # the key we added brought a Basis with it; a lone Basis is
                # just a copy of the mesh, so drop it too
                if sk is not None and len(sk.key_blocks) == 1:
                    ob.shape_key_remove(sk.key_blocks[0])
        # The Guide's separated shape: plain removal — the Basis it was
        # derived from is the original drape and stays.
        sk = me.shape_keys
        kb = sk.key_blocks.get(SEPARATED_SK) if sk is not None else None
        if kb is not None:
            rep["shape_keys"] += 1
            touched = True
            if not dry_run:
                ob.shape_key_remove(kb)

        if me.name not in seen_meshes:
            seen_meshes.add(me.name)
            names = [a.name for a in me.attributes if a.name.startswith(ATTR_PREFIXES)]
            rep["attributes"] += len(names)
            if names:
                touched = True
                if not dry_run:
                    for name in names:
                        a = me.attributes.get(name)
                        if a is not None:
                            me.attributes.remove(a)
            mkeys = _idprop_keys(me)
            rep["idprops"] += len(mkeys)
            if mkeys:
                touched = True
                if not dry_run:
                    for k in mkeys:
                        del me[k]
            slots = [i for i, m in enumerate(me.materials)
                     if m is not None and _named(m.name, MATERIAL_NAMES)]
            rep["material_slots"] += len(slots)
            if slots:
                touched = True
                if not dry_run:
                    for i in reversed(slots):
                        me.materials.pop(index=i)
        rep["objects"] += int(touched)

    # ---- bake images: one set per Guide, so matched on the base name
    for img in list(bpy.data.images):
        if _bcore.is_map_image(img.name):
            rep["images"].append(img.name)
            if not dry_run:
                bpy.data.images.remove(img)

    # ---- the remaining datablocks, by exact name
    for coll, wanted, key in ((bpy.data.materials, MATERIAL_NAMES, "materials"),
                              (bpy.data.texts, TEXT_NAMES, "texts")):
        for db in list(coll):
            if _named(db.name, wanted):
                rep[key].append(db.name)
                if not dry_run:
                    coll.remove(db)

    # ---- scene: our custom properties, then the settings group itself
    keys = _idprop_keys(scene)
    rep["idprops"] += len(keys)
    top = getattr(scene, SCENE_GROUP, None)
    if top is not None and (top.retopo_obj is not None or top.guide_obj is not None
                            or top.guide_flat_shapekey or SCENE_GROUP in scene.keys()):
        rep["scene_reset"] = True
    if not dry_run:
        for k in keys:
            del scene[k]
        _reset_group(top)
        if SCENE_GROUP in scene.keys():
            try:
                del scene[SCENE_GROUP]
            except Exception:
                pass
        _drop_caches()
    return rep


def _reset_group(group):
    """Put every property of a PropertyGroup (and its nested groups) back to
    its default, so nothing of the session's settings is stored in the file."""
    if group is None:
        return
    for prop in group.bl_rna.properties:
        ident = prop.identifier
        if ident == "rna_type":
            continue
        if prop.type == "POINTER" and isinstance(getattr(group, ident, None),
                                                 bpy.types.PropertyGroup):
            _reset_group(getattr(group, ident))
            continue
        try:
            group.property_unset(ident)
        except Exception:
            pass


def _drop_caches():
    """In-memory caches keyed by objects that may no longer exist. Best effort:
    each one is independent and none is needed for the file to be clean."""
    from .uv_seam_guide import analysis as _analysis
    from .clo_projector import gpu_overlay as _proj_overlay
    from .uv_seam_guide import gpu_overlay as _seam_overlay
    for fn in (
        _pcore.invalidate_guide_cache,
        _analysis.invalidate_mirror_pair_cache,
        _analysis.invalidate_symmetry_cache,
        _analysis._island_cache.clear,
        _proj_overlay.reset_guide_derived,
        _proj_overlay.reset_retopo_derived,
        # These two are the full list (seams, folds, twins, anchors, status,
        # ghosts / corners, pins); the four clear_* calls that used to be
        # listed here covered only part of it.
        _seam_overlay.reset_guide_derived,
        _seam_overlay.reset_retopo_derived,
    ):
        try:
            fn()
        except Exception:
            pass


def summary_lines(rep):
    """Human-readable lines for the confirm dialog and the report."""
    lines = []
    if rep["mirrors"]:
        lines.append("Mirror object(s): %d  (%s)" % (len(rep["mirrors"]),
                                                     ", ".join(rep["mirrors"][:3])))
    if rep["preview_plane"]:
        lines.append("Bake Preview Plane: %d  (%s)" % (
            len(rep["preview_plane"]), ", ".join(rep["preview_plane"][:3])))
    if rep["attributes"]:
        lines.append("Attribute layers on meshes: %d" % rep["attributes"])
    if rep["shape_keys"]:
        s = "Shape keys %s / %s: %d" % (SHAPEKEY_NAME, SEPARATED_SK, rep["shape_keys"])
        if rep["baked_3d"]:
            s += "  — %s shown in 3D: the 3D layout is kept as the mesh" % (
                ", ".join(rep["baked_3d"][:3]))
        lines.append(s)
    if rep["vertex_groups"]:
        lines.append("Vertex group %s: %d" % (VGROUP_NAME, rep["vertex_groups"]))
    if rep["modifiers"]:
        lines.append("Modifiers on meshes (AC9_*, e.g. the old Preview Subdiv): %d"
                     % rep["modifiers"])
    if rep["material_slots"]:
        lines.append("Material slots (island colours / bake temp): %d" % rep["material_slots"])
    if rep["idprops"]:
        lines.append("Custom properties (scene / objects / meshes): %d" % rep["idprops"])
    for key, label in (("images", "Images"), ("materials", "Materials"), ("texts", "Texts")):
        if rep[key]:
            lines.append("%s: %s" % (label, ", ".join(rep[key])))
    if rep["scene_reset"]:
        lines.append("Scene settings (Retopo / Guide / Flat SK and all options) reset")
    return lines


class AC9_OT_ClearAll(bpy.types.Operator):
    bl_idname = "ac9_cloth.clear_all"
    bl_label = "Clear All AC9 Data"
    bl_description = (
        "Remove every trace of AC9 Cloth Retopo from this file: the Mirror "
        "object, the add-on's attribute layers, shape key, vertex group, "
        "modifier, material slots and custom properties on every mesh, the "
        "bake images and report text, and the scene settings (which also "
        "removes the header buttons). Your geometry, UVs, materials and the "
        "Guide's Flat shape key are left alone. A retopo currently shown in 3D "
        "keeps its 3D layout as the mesh"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if context.mode != "OBJECT":
            cls.poll_message_set("Clear All runs in Object Mode.")
            return False
        return True

    def invoke(self, context, event):
        self._lines = summary_lines(run(context.scene, dry_run=True))
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        col = self.layout.column(align=True)
        lines = getattr(self, "_lines", None) or []
        if not lines:
            col.label(text="Nothing from AC9 Cloth Retopo found in this file.",
                      icon='CHECKMARK')
            return
        col.label(text="This will remove from the file:", icon='TRASH')
        for line in lines:
            col.label(text="  " + line)
        col.separator()
        col.label(text="Geometry, UVs, your materials and the Guide's Flat SK stay.")

    def execute(self, context):
        rep = run(context.scene, dry_run=False)
        lines = summary_lines(rep)
        for area in context.screen.areas if context.screen else ():
            area.tag_redraw()
        if not lines:
            self.report({"INFO"}, "Nothing from AC9 Cloth Retopo found in this file.")
        else:
            self.report({"INFO"}, "Cleared AC9 data: " + "; ".join(lines))
        return {"FINISHED"}


def get_classes():
    return (AC9_OT_ClearAll,)
