"""AC9 Cloth Retopo — Japanese (ja_JP) UI translation.

The English UI in the rest of the add-on is the single source of truth: every
entry here is keyed by the exact English literal already written in the source,
so no bl_label, bl_description, property name/description, enum item or
`text=` argument had to change. Blender does the lookup itself for anything it
draws, so there is no wrapping to do at the call sites either.

Registered from the root `register()` (after the classes) and unregistered
first in `unregister()`.

Terms deliberately LEFT IN ENGLISH -- they are the proper names of the objects,
shape keys, panels and operators as the manual calls them:

  Guide, Retopo, Flat SK, Mirror, Finalize, Prepare, Setup, Boundary, Faces,
  3D View, Guide Maps, Overlays, Advanced, Experimental, Ghost, Anchor, Corner,
  Pin, Twin, Self, Connect, Connect Rows, Grid, Subdivide, Bake, Residual, Sag,
  Drape, Clear All, Reset to Defaults, Solidify, Symmetrize

Those appear here with an identical Japanese value on purpose, so the intent is
recorded next to the string.

Known limitation (measured on Blender 5.0, ja_JP)
-------------------------------------------------
For the "*" context, Blender's OWN ja_JP catalogue wins over an add-on
catalogue whenever it already carries the same msgid. 46 of the entries below
therefore never reach the UI; 22 of them are the kept-in-English terms above,
which Blender renders in its own vocabulary regardless of what we ask for:

  3D View -> 3Dビュー   Advanced -> 詳細設定   Bake -> ベイク
  Boundary -> 境界      Collapse -> 束ねる     Corner(s) -> コーナー
  Crease -> クリース    Faces -> 面            Ghost(s) -> ゴースト
  Grid -> グリッド      Guide -> ガイド        Merge -> マージ
  Mirror -> ミラー      Overlays -> オーバーレイ  Pin -> 固定
  Seam(s) -> シーム     Solid -> ソリッド      Subdivide -> 細分化
  Sync -> 同期

The remaining 22 (Alpha, Count, Levels, Lines, Mapping, Outline, Points,
Replace, Set, Status, Step, Symmetry, Triangulate, Vertices, ...) get Blender's
own wording instead of ours, which is still correct Japanese, just phrased the
way the rest of Blender phrases it.

There is no priority flag in `bpy.app.translations` to change that. The only
fix would be giving those labels a private `text_ctxt=` at the call sites,
which would mean editing the UI code -- deliberately not done here.

Operator names are unaffected: all 76 `bl_label`s resolve to our value, because
Blender has no entry for them in the "Operator" context.

Dynamic strings (status lines, f-string reports carrying counts) are NOT
translated -- there is no literal for Blender to match. See
`ui_common.draw_status` callers and the `%s` in clo_projector's
`Flat SK '...' not found` message.

Adding a string to the UI
-------------------------
Write the English literal as usual, then add one entry below:
  * an operator's `bl_label` needs BOTH ("Operator", en) and ("*", en) --
    Blender looks operator names up in the "Operator" context, while a button
    given an explicit `text=` is looked up in "*".
  * everything else needs only ("*", en).
The two dicts at the bottom of this file do that split for you.
"""

import bpy

# Operator bl_labels: registered under both "Operator" and "*" (see docstring).
_OPERATOR_LABELS = {
    "Add Transparent Material": "半透明マテリアルを追加",
    "Adjust Density": "密度を調整",
    "Align Boundary to Outline": "Boundary を輪郭に整列",
    "Analyze Anchors": "Anchor を解析",
    "Analyze Guide": "Guide を解析",
    "Analyze Seams": "Seams を解析",
    "Analyze Symmetry": "対称性を解析",
    "Apply 3D Edits → 2D": "3D の編集を 2D へ適用",
    "Bake Drape Maps": "Drape Maps を Bake",
    "Bake Island Colors": "アイランド色を Bake",
    "Bake Residual Map": "Residual Map を Bake",
    "Bake Sag Map": "Sag Map を Bake",
    "Bind New 3D Verts": "新規 3D 頂点をバインド",
    "Clear All AC9 Data": "AC9 データを Clear All",
    "Clear Analysis": "解析結果をクリア",
    "Clear Corner": "Corner をクリア",
    "Clear Fill": "Fill をクリア",
    "Clear Folds": "折れ線をクリア",
    "Clear Ghost Points": "Ghost 点をクリア",
    "Clear Guide Cache": "Guide キャッシュをクリア",
    "Clear Island Colors": "アイランド色をクリア",
    "Clear Pin": "Pin をクリア",
    "Clear Regions": "領域をクリア",
    "Clear Seams": "Seams をクリア",
    "Clear Separation": "引き離しをクリア",
    "Clear Stored Attachments": "保存済みアタッチメントをクリア",
    "Clear Symmetry": "対称性をクリア",
    "Clear Tags": "タグをクリア",
    "Clear Twins": "Twin をクリア",
    "Collapse (Keep UV)": "Collapse（UV 保持）",
    "Connect Loose Ends": "開いた端を Connect",
    "Connect Rows": "Connect Rows",
    "Create Flat SK": "Flat SK を作成",
    "Detect Corners": "Corner を検出",
    "Detect Folds": "折れ線を検出",
    "Detect Twins": "Twin を検出",
    "Diagnose Spaces": "座標空間を診断",
    "Diagnose Twin Mapping": "Twin の対応を診断",
    "Even Out Density": "密度を均等化",
    "Fill Regions": "領域を Fill",
    "Finalize": "Finalize",
    "Find Folds": "折れ線を検索",
    "Fix Non-planar Quads": "非平面クワッドを修正",
    "Force Bond Selected": "選択を強制 Bond",
    "Generate Boundary": "Boundary を生成",
    "Ghost Snap Move": "Ghost スナップ移動",
    "Grid Regions": "Grid Regions",
    "Inset Line": "Inset Line",
    "Inset Pieces": "Inset Pieces",
    "Inspect Selected Vertex": "選択頂点を調査",
    "Mark Corner": "Corner をマーク",
    "Mark Pin": "Pin をマーク",
    "Match Seams": "Seams を Match",
    "Outline Snap Move": "輪郭スナップ移動",
    "Overlay Preset": "Overlay プリセット",
    "Patch Grid": "Patch Grid",
    "Preview Fill": "Preview Fill",
    "Preview Plane": "プレビュー平面",
    "Refresh Ghosts": "Ghost を更新",
    "Refresh Mirror": "Mirror を更新",
    "Remove Mirror": "Mirror を削除",
    "Remove Transparent Material": "半透明マテリアルを削除",
    "Replace Twin Island": "Twin アイランドを置換",
    "Reset Settings": "設定をリセット",
    "Seam Status": "Seam ステータス",
    "Select Quads by Type": "種類でクワッドを選択",
    "Select Tagged Edges": "タグ付き辺を選択",
    "Separate Self-Contact": "自己接触を引き離す",
    "Set View State": "View 状態を設定",
    "Subdivide Retopo": "Retopo を Subdivide",
    "Symmetrize Island": "アイランドを Symmetrize",
    "Sync 2D > 3D": "Sync 2D > 3D",
    "Sync 3D > 2D": "Sync 3D > 2D",
    "Sync Selected Vertex": "選択頂点を Sync",
    "Tag Selected Edges": "選択辺をタグ付け",
    "Toggle Wireframe": "ワイヤーフレームを切替",
    "Triangulate Guide": "Guide を三角化",
}

# Everything else: panel labels, `text=` arguments, property names and
# descriptions, enum item names and descriptions, poll messages, hints.
_UI_STRINGS = {
    # 3D View panel: the View row
    "Mirror": "Mirror",
    "Guide": "Guide",
    "Both": "両方",
    "The Mirror alone: the Guide is hidden and laid out flat (Flat SK 1), which is the state the 2D retopo is edited in":
        "Mirror だけを見せます。Guide は非表示にし、平面（Flat SK 1）に寝かせます。2D リトポを編集する状態です",
    "The Guide's 3D garment shape alone (Flat SK 0); the Mirror, which occupies the same space, is hidden":
        "Guide の 3D 衣装形状だけを見せます（Flat SK 0）。同じ位置に重なる Mirror は非表示にします",
    "The Mirror on top of the Guide's 3D garment shape":
        "Guide の 3D 衣装形状の上に Mirror を重ねて見せます",
    "What is visible. Mirror: the retopo's 3D form alone, with the Guide hidden and laid out flat — the 2D working state. Guide: the garment alone. Both: the retopo on top of the garment. Plain visibility, so the Outliner undoes any of it; Refresh never changes this":
        "何を表示するか。Mirror: リトポの 3D 形状だけ。Guide は非表示にして平面に寝かせます＝2D 作業状態。Guide: 衣装だけ。Both: 衣装の上にリトポを重ねる。ふつうの表示 / 非表示なので、アウトライナーからいつでも上書きできます。Refresh はこの状態を変えません",
    "Retopo must be in Object or Edit Mode.": "Retopo は Object Mode か Edit Mode にしてください。",

    # ---- panel bl_label ----
    "3D View": "3D View",
    "Advanced": "Advanced",
    "Analysis Settings": "解析設定",
    "Appearance": "見た目",
    "Boundary Settings": "Boundary 設定",
    "Face Settings": "Faces 設定",
    "Faces": "Faces",
    "Guide Maps": "Guide Maps",
    "Baked Maps": "ベイク済みマップ",
    "Map Settings": "Map 設定",
    "No maps baked yet.": "まだマップをベイクしていません。",
    "(no Guide in the name)": "(名前に Guide が入っていない)",
    "Keep in file": "ファイルに残す",
    "Bake on GPU when available": "GPU があれば GPU でベイクする",
    "CPU bake: Blender stays frozen until it finishes":
        "CPU ベイク: 終わるまで Blender は固まったままです",
    "Enable a GPU in Preferences > System, or use 1024.":
        "プリファレンス > システム で GPU を有効にするか、1024 を使ってください。",
    "Keep Passes": "パスを残す",
    "Delete Map": "マップを削除",
    "Delete This Guide's Maps": "この Guide のマップを削除",
    "Delete All Maps": "全マップを削除",
    "Guide Prep": "Guide Prep",
    "Guide Prep Settings": "Guide Prep 設定",
    "Separation Settings": "引き離し設定",
    "Setup": "Setup",

    # ---- sidebar tab (bl_category) ----
    "AC9 Cloth Retopo": "AC9 Cloth Retopo",

    # ---- text= arguments ----
    "    same-handed: a duplicate, not a reflection?": "    同じ向き: 反転ではなく複製では?",
    "+ 1": "+ 1",
    "2D > 3D": "2D > 3D",
    "3D > 2D": "3D > 2D",
    "3D → 2D": "3D → 2D",
    "All": "すべて",
    "Alternate": "逆対角",
    "Analyze": "解析",
    "Analyze Guide": "Guide を解析",
    "Analyze Seams": "Seams を解析",
    "Analyze Symmetry": "対称性を解析",
    "Anchor": "Anchor",
    "Anchors": "Anchor",
    "Anchors not analyzed yet": "Anchor が未解析",
    "Auto": "自動",
    "Auto Fill": "自動 Fill",
    "Bake": "Bake",
    "Boundary Cross Size": "Boundary 十字サイズ",
    "Boundary Flags": "Boundary フラグ",
    "Clean": "クリーン",
    "Clear": "クリア",
    "Clear All AC9 Data": "AC9 データを Clear All",
    "Connect": "Connect",
    "Connect Rows": "Connect Rows",
    "Convex": "凸対角",
    "Corners": "Corner",
    "Create Flat SK": "Flat SK を作成",
    "Diagnostics": "診断",
    "Distance": "距離",
    "Done with this file": "このファイルでの作業終了",
    "Even Out": "均等化",
    "Find Folds": "折れ線を検索",
    "Fold": "折れ線",
    "Fold Lines": "折れ線",
    "Creases (Find Folds)": "折り目（Find Folds）",
    "Show Creases": "折り目を表示",
    "Snap to Creases": "折り目へスナップ",
    "Crease Color": "折り目の色",
    "Crease": "折り目",
    "Blue": "青",
    "Draw the fold lines tagged on the Guide by CLO Cleanup's Find Folds / Tag Selected Edges — the crease the Inset Line band was built on — in the flat layout, so a cut line can be laid on it. Different from Fold Lines, which are an island's symmetry axis. Filled in by Analyze Seams":
        "CLO Cleanup の Find Folds / Tag Selected Edges で Guide にタグ付けした折り目（Inset Line の帯の芯になった線）を平面レイアウトに描く。カット線をその上に置くための目印。島の対称軸である「折れ線」とは別物。Analyze Seams で更新",
    "Include the Guide's tagged fold lines (Find Folds) as Outline Snap targets, so the retopo vertices of a cut land exactly on the crease":
        "Guide のタグ付き折り目（Find Folds）も Outline Snap の対象にし、カットの頂点を折り目の真上に置けるようにする",
    "Fold Width": "折れ線の太さ",
    "Force Bond": "強制 Bond",
    "Geometry, UVs, your materials and the Guide's Flat SK stay.":
        "ジオメトリ・UV・自作マテリアル・Guide の Flat SK は残ります。",
    "Ghost": "Ghost",
    "Ghost (Placed)": "Ghost（配置済み）",
    "Ghost (Unplaced)": "Ghost（未配置）",
    "Ghost Line": "Ghost 線",
    "Ghosts": "Ghost",
    "Grid": "Grid",
    "Grid Regions": "Grid Regions",
    "Inset Line": "Inset Line",
    "Inset Pieces": "Inset Pieces",
    "Islands": "アイランド",
    "Keep UV": "UV 保持",
    "Legacy Flip (retopo morphs 2D/3D itself)": "Legacy Flip（retopo 自体が 2D/3D を切替）",
    "Legacy Flip Options": "Legacy Flip オプション",
    "Lines": "線",
    "Link Point Size": "リンク点サイズ",
    "Maintenance": "メンテナンス",
    "Mapping": "対応付け",
    "Mark": "マーク",
    "Marks": "マーク",
    "Match": "Match",
    "New Verts": "新規頂点",
    "Nothing from AC9 Cloth Retopo found in this file.":
        "このファイルに AC9 Cloth Retopo のデータはありません。",
    "Only Unplaced": "未配置のみ",
    "Orphan Rings": "孤立リング",
    "Close Seam Gaps": "縫い目の隙間を閉じる",
    "Weld Seam Vertices": "縫い目の頂点を溶接",
    "Lines: Unplaced Only": "線: 未配置のみ",
    "Unplaced Lines Only": "未配置の線のみ",
    "Outline": "輪郭",
    "Outline (white)": "輪郭（白）",
    "Overlays": "Overlays",
    "Overlays: OFF": "Overlays: OFF",
    "Overlays: ON": "Overlays: ON",
    "Pair Lines": "ペア線",
    "Pins": "Pin",
    "Plane": "平面",
    "Points": "点",
    "Presets": "プリセット",
    "Preview Fill": "Preview Fill",
    "Preview Fill, Grid Regions, Drape Merge, Guide Separate, Mesh Edit, Quad Fix, Legacy Flip":
        "Preview Fill, Grid Regions, Drape Merge, Guide Separate, Mesh Edit, Quad Fix, Legacy Flip",
    "Quad Fix": "Quad Fix",
    "Refresh": "更新",
    "Refresh Mirror": "Mirror を更新",
    "Reset to Defaults": "Reset to Defaults",
    "Residual Map": "Residual Map",
    "Saddle": "サドル",
    "Sag Map": "Sag Map",
    "Seam": "Seam",
    "Seam Lines": "Seam 線",
    "Seam Status": "Seam ステータス",
    "Seam Width": "Seam の太さ",
    "Seams (cyan)": "Seams（シアン）",
    "Seams not analyzed": "Seams が未解析",
    "Selected Only": "選択のみ",
    "Selection Link": "選択リンク",
    "Self": "Self",
    "Separate": "引き離す",
    "Set": "設定",
    "Show": "表示",
    "Snap Radius": "スナップ半径",
    "Spaces": "座標空間",
    "Status": "ステータス",
    "This will remove from the file:": "このファイルから次を削除します:",
    "Threshold": "しきい値",
    "To Outline": "輪郭へ",
    "Twin": "Twin",
    "Twins (magenta)": "Twin（マゼンタ）",
    "Unpaired islands:": "ペア未成立のアイランド:",
    "Vertex": "頂点",
    "Vertex Counts": "頂点数",
    "View": "表示",
    "Wire": "ワイヤー",
    "Wireframe": "ワイヤーフレーム",
    "− 1": "− 1",

    # ---- ui_common row labels, hints and blockers ----
    "1 Flat SK": "1 Flat SK",
    "2 Folds": "2 折れ線",
    "3 Pieces": "3 Pieces",
    "4 Lines": "4 Line",
    "Align": "整列",
    "Apply": "適用",
    "Attachments": "アタッチメント",
    "Bind": "バインド",
    "Bond": "Bond",
    "Check": "確認",
    "Collapse": "Collapse",
    "Contact": "接触",
    "Corner": "Corner",
    "Density": "密度",
    "Edit Mode tools": "Edit Mode のツール",
    "Experimental tools: Edit > Preferences > Add-ons > AC9 Cloth Retopo":
        "Experimental ツール: 編集 > プリファレンス > アドオン > AC9 Cloth Retopo",
    "Finalize": "Finalize",
    "Fix": "修正",
    "Guide Cache": "Guide キャッシュ",
    "Leave Solidify a live modifier (do not apply it)":
        "Solidify はライブモディファイアのまま残す（適用しない）",
    "New <Retopo>_Final: 3D shape, UV = 2D layout, no ShapeKeys":
        "新しい <Retopo>_Final: 3D 形状・UV は 2D レイアウト・ShapeKey なし",
    "No ghosts": "Ghost なし",
    "No seams: Subdivide skips the snap": "Seams なし: Subdivide はスナップを省略します",
    "Object Mode tools": "Object Mode のツール",
    "Preview": "プレビュー",
    "Preview (stale)": "プレビュー（要更新）",
    "Commit": "確定",
    "Regions": "領域",
    "Rows": "Rows",
    "Select": "選択",
    "Set the Guide first (Setup)": "先に Guide を設定してください（Setup）",
    "Select two lines (edges); rungs cut through the faces between them":
        "2 本の線（辺）を選択してください。その間の面が横木で分割されます",
    "Auto Fill: selected islands only": "Auto Fill は選択アイランドのみ",
    "Self axis": "Self 軸",
    "Set the Guide and Flat SK first": "先に Guide と Flat SK を設定してください",
    "Set the Retopo first": "先に Retopo を設定してください",
    "Settings": "設定",
    "Snap (G)": "スナップ (G)",
    "Solid": "Solid",
    "Subdivide": "Subdivide",
    "Symmetry": "対称性",
    "Sync": "Sync",
    "Twins": "Twin",
    "Verify": "検証",

    # ---- property name= ----
    "3D Match Distance": "3D 一致距離",
    "3D Source": "3D Source",
    "Align Threshold": "整列しきい値",
    "Alpha": "不透明度",
    "Also Split Saddles": "サドルも分割",
    "Alternate Diagonal": "逆の対角線",
    "Anchor Color": "Anchor の色",
    "Anchor Cross Size": "Anchor 十字サイズ",
    "Auto Bind New Verts": "新規頂点を自動バインド",
    "Bond Distance": "Bond 距離",
    "Boundary Result": "Boundary 結果",
    "Boundary Snap Distance": "Boundary スナップ距離",
    "Candidate Angle": "候補の角度",
    "Clear Previous Failed Group": "前回の失敗グループを消す",
    "CLO Cleanup Result": "CLO Cleanup 結果",
    "Corner Angle": "Corner 角度",
    "Corner Color": "Corner の色",
    "Corner Marker Size": "Corner マーカーサイズ",
    "AO Distance": "AO 距離",
    "Curvature Radius": "Curvature 半径",
    "AO Mix": "AO 合成比",
    "Coverage Margin": "被覆マージン",
    "Crease Min Angle": "クリース最小角度",
    "Cross Size": "十字サイズ",
    "Density Pins": "密度 Pin",
    "Divide By": "分割基準",
    "Edge Clearance": "外周からの余白",
    "Experimental tools": "Experimental ツール",
    "Experimental: in-place 3D edit": "Experimental: 3D 直接編集",
    "Extend Selected": "選択から延長",
    "Faces Result": "Faces 結果",
    "Fill Mismatched Regions": "不一致の領域も Fill",
    "Fill Spacing (mm)": "Fill 間隔 (mm)",
    "Flat SK": "Flat SK",
    "Flat-After-Fix": "修正後の平坦さ",
    "Fold Axis": "折り軸",
    "Fold Line Color": "折れ線の色",
    "Fold Line Width (px)": "折れ線の太さ (px)",
    "Gap": "隙間",
    "Generate": "生成",
    "Ghost Cross Size": "Ghost 十字サイズ",
    "Ghost Line Color": "Ghost 線の色",
    "Ghost Line Width (px)": "Ghost 線の太さ (px)",
    "Ghost Snap Mode": "Ghost スナップモード",
    "Grid Spacing (mm)": "Grid 間隔 (mm)",
    "Guide": "Guide",
    "Guide Color": "Guide の色",
    "Guide Line Width (px)": "Guide 線の太さ (px)",
    "Guide Maps Result": "Guide Maps 結果",
    "Guide Result": "Guide 結果",
    "Ignore Under (mm)": "無視する長さ (mm)",
    "Include Solidify": "Solidify を含める",
    "Kind": "種類",
    "Levels": "分割レベル",
    "Live Ghost Update": "Ghost をライブ更新",
    "Marked Seam Distance": "マーク Seam 距離",
    "Marked Seams (Sharp)": "マーク Seam (Sharp)",
    "Marker Size": "マーカーサイズ",
    "Max Iterations": "最大反復回数",
    "Max Seam Distance": "最大 Seam 距離",
    "Merge Precision": "Merge 精度",
    "Mode": "モード",
    "Only Selected Faces": "選択面のみ",
    "Outline Snap Mode": "輪郭スナップモード",
    "Overwrite Existing ShapeKey": "既存 ShapeKey を上書き",
    "Pin": "Pin",
    "Pin Color": "Pin の色",
    "Pin Marker Size": "Pin マーカーサイズ",
    "Placed Ghost Color": "配置済み Ghost の色",
    "Preset": "プリセット",
    "Replace": "置き換える",
    "Report slow operators": "遅いオペレータを報告",
    "Residual Scale": "Residual スケール",
    "Resolution": "解像度",
    "Retopo": "Retopo",
    "Sag Scale": "Sag スケール",
    "Seam Distance": "Seam 距離",
    "Select Failed Vertices": "失敗した頂点を選択",
    "Select Problem Seams": "問題のある Seam を選択",
    "Select Unmatched Retopo Verts": "未対応の Retopo 頂点を選択",
    "Self Axis": "Self 軸",
    "Show Anchors": "Anchor を表示",
    "Show Boundary Verts": "Boundary 頂点を表示",
    "Show Fold Lines": "折れ線を表示",
    "Show Free Edges": "フリー辺を表示",
    "Show Ghost Lines": "Ghost 線を表示",
    "Show Ghost Points": "Ghost 点を表示",
    "Show Outline": "輪郭を表示",
    "Show Overlays": "Overlays を表示",
    "Show Pair for Selected Only": "選択のペアのみ表示",
    "Show Pair Lines": "ペア線を表示",
    "Show Seam Status": "Seam ステータスを表示",
    "Show Seam Vertex Count": "Seam の頂点数を表示",
    "Show Seams": "Seams を表示",
    "Show Snap Radius": "スナップ半径を表示",
    "Show Twin Lines": "Twin 線を表示",
    "Side Smoothing": "辺の平滑化",
    "Smooth Radius": "平滑化半径",
    "Snap Distance": "スナップ距離",
    "Snap New Boundary to Seam": "新規 Boundary を Seam へスナップ",
    "Snap to Fold Lines": "折れ線へスナップ",
    "Spacing (mm)": "間隔 (mm)",
    "Straight Tolerance (mm)": "直線許容 (mm)",
    "Symmetry Tolerance": "対称性の許容値",
    "Topology Corners": "トポロジー Corner",
    "Triangulate": "三角化",
    "Guide is not all triangles (3D Mirror will fail)":
        "Guide に三角形でない面があります（3D Mirror が失敗します）",
    "Triangulate the faces of the Guide that are not triangles already. The projector reads the Guide as triangles (a retopo vertex is stored as a triangle index plus barycentric weights), so an all-triangle Guide is a hard requirement — validate_guide rejects anything else and 3D Mirror stops. Create Flat SK triangulates as it runs, but a Guide can pick up n-gons afterwards, so this is the repair: no vertex is added or moved, the Flat SK and the 2D layout the retopo was built against are untouched, only a diagonal is added inside each offending face":
        "Guide の三角形でない面だけを三角化します。プロジェクタは Guide を三角形として読む（retopo の頂点は三角形の番号＋重心座標で保存される）ため、全面が三角形であることは必須要件で、そうでなければ validate_guide が弾いて 3D Mirror が止まります。Create Flat SK は実行時に三角化しますが、その後に Guide が n-gon を持つことがあるので、これはその修復用です。頂点は増えも動きもせず、Flat SK も retopo が拠って立つ 2D レイアウトもそのまま、該当する面の内側に対角線が 1 本増えるだけです",
    "Twin Line Color": "Twin 線の色",
    "Twin Line Width (px)": "Twin 線の太さ (px)",
    "Twin Tolerance": "Twin の許容値",
    "Unplaced Ghost Color": "未配置 Ghost の色",
    "UV Layer": "UV レイヤー",
    "Vertices": "頂点数",
    "Warp Threshold": "ねじれしきい値",
    "What": "対象",
    "Width": "幅",
    "Z Offset": "Z オフセット",

    # ---- EnumProperty item names and descriptions ----
    "1024": "1024",
    "1K — fast preview": "1K — 高速プレビュー",
    "2048": "2048",
    "2D seam boundary work: seam guides, free edges, creases, folds, twins, ghosts, anchors, parity counts":
        "2D の Seam Boundary 作業向け: Seam ガイド・フリー辺・Crease・折れ線・Twin・Ghost・Anchor・頂点数の一致",
    "2K — recommended": "2K — 推奨",
    "4096": "4096",
    "4K — slow, for final inspection": "4K — 低速、最終確認用",
    "A fold line: Inset Line will pick it up": "折れ線として扱う（Inset Line が拾います）",
    "Add or remove vertices relative to the count now": "現在の頂点数を基準に増減する",
    "All Empty Seams": "空の Seam すべて",
    "All non-planar": "非平面すべて",
    "All Off": "すべて OFF",
    "Both clean and saddle": "クリーンもサドルも両方",
    "Boundary": "Boundary",
    "Clean (simple fold)": "クリーン（単純な折れ）",
    "Count": "頂点数",
    "Crease": "Crease",
    "Cyan": "シアン",
    "Drape": "Drape",
    "Drape Maps": "Drape Maps",
    "Every seam that has no retopo yet, in one go": "Retopo が未着手の Seam をすべて一度に",
    "Fold along the panel's horizontal centre line, so the bottom half is rebuilt from the top half (or the other way round)":
        "型紙の水平中心線で折り返し、上半分から下半分を作り直す（逆方向も可）",
    "Fold along the panel's vertical centre line, so the left half is rebuilt from the right half (or the other way round)":
        "型紙の垂直中心線で折り返し、右半分から左半分を作り直す（逆方向も可）",
    "Fold remains after any split": "どちらに分割しても折れが残る",
    "Free Edge": "フリー辺",
    "Free Edge Color": "フリー辺の色",
    "Free Edges (yellow)": "フリー辺（黄）",
    "Free Edges Only": "フリー辺のみ",
    "Give every seam the same number of vertices": "すべての Seam に同じ頂点数を与える",
    "Give the span exactly this many vertices": "このスパンにちょうどこの頂点数を与える",
    "Green": "緑",
    "Horizontal axis (mirror top-bottom)": "水平軸（上下を反転）",
    "Look at the Mirror in 3D: white outline":
        "3D で Mirror を見る用: 白い輪郭",
    "Magenta": "マゼンタ",
    "Mirror": "Mirror",
    "Nearest to 3D Cursor": "3D カーソルに最も近いもの",
    "Only edges sewn to another panel": "他の型紙と縫い合わされた辺のみ",
    "Only hems, openings, necklines and the like — edges with no sewn partner":
        "裾・開き・襟ぐりなど、縫い相手のない辺のみ",
    "Only the empty seam closest to the 3D cursor — for starting one seam at a time while working a panel":
        "3D カーソルに最も近い空の Seam だけ。型紙を 1 本ずつ進めるとき用",
    "Orange": "オレンジ",
    "Original": "オリジナル",
    "Pick the count per seam so vertices land roughly this far apart, keeping density even across seams of different lengths":
        "頂点間隔がおよそこの値になるよう Seam ごとに頂点数を決め、長さの違う Seam でも密度をそろえる",
    "Pick the count so vertices land this far apart": "頂点がこの間隔で並ぶように頂点数を決める",
    "Pick the fold that still has work: the side-to-side asymmetric one; refuses when both are equally asymmetric":
        "まだ作業が残っている折り軸（左右非対称な側）を選ぶ。両方が同程度に非対称なら中止する",
    "Read the guide's seam structure: seams, free edges, folds, twins, white outline, ghosts":
        "Guide の Seam 構造を読む用: Seams・フリー辺・折れ線・Twin・白い輪郭・Ghost",
    "Red": "赤",
    "Remove the tag from the selected edges": "選択辺からタグを外す",
    "Residual": "Residual",
    "Saddle (true twist)": "サドル（真のねじれ）",
    "Sag": "Sag",
    "Seams": "Seams",
    "Seams + Free Edges": "Seams + フリー辺",
    "Separated": "引き離し済み",
    "Sewn seams and free edges alike — what it takes to close every panel boundary, which is the prerequisite for filling faces":
        "縫い Seam もフリー辺も対象。型紙の外周を閉じるのに必要な範囲で、面を貼るための前提になる",
    "Sewn Seams Only": "縫い Seam のみ",
    "Show the combined Drape map — Curvature x AO, mixed at bake time by 'AO Mix'":
        "合成済みの Drape Map を表示する。Curvature x AO で、比率はベイク時に 'AO Mix' で決まる",
    "Show the drape AO pass": "Drape の AO パスを表示する",
    "Show the drape Curvature pass": "Drape の Curvature パスを表示する",
    "Show the Residual Map": "Residual Map を表示する",
    "Show the Sag Map": "Sag Map を表示する",
    "Spacing": "間隔",
    "Splittable to flat": "分割すれば平坦にできる",
    "Step": "増減",
    "The AC9_Separated ShapeKey: layers pushed apart by Separate Self-Contact. Use while baking":
        "AC9_Separated ShapeKey。Separate Self-Contact で層を引き離した形状。Bake 中に使う",
    "The Basis ShapeKey: the drape as exported. Use for the final retopo":
        "Basis ShapeKey。書き出したままのドレープ形状。最終 Retopo にはこちらを使う",
    "Turn every individual overlay off (the master switch is left alone)":
        "個別の Overlay をすべて OFF にする（マスタースイッチは触らない）",
    "Untag": "Untag",
    "Vertical axis (mirror left-right)": "垂直軸（左右を反転）",
    "White": "白",
    "Yellow": "黄",

    # ---- poll_message_set() ----
    "Clear All runs in Object Mode.": "Clear All は Object Mode で実行してください。",
    "Leave the Guide's Edit Mode first.": "先に Guide の Edit Mode を抜けてください。",
    "Edit the Retopo object itself, or leave Edit Mode.":
        "Retopo オブジェクト自体を編集するか、Edit Mode を抜けてください。",
    "Edit the Retopo object itself.": "Retopo オブジェクト自体を編集してください。",
    "Exit Edit Mode first (Tab) — runs in Object Mode.":
        "先に Edit Mode を抜けてください (Tab) — Object Mode で実行します。",
    "Exit Edit Mode first (Tab) — Sync runs in Object Mode.":
        "先に Edit Mode を抜けてください (Tab) — Sync は Object Mode で実行します。",
    "Exit Edit Mode first (Tab).": "先に Edit Mode を抜けてください (Tab)。",
    "Leave Edit Mode first (Tab).": "先に Edit Mode を抜けてください (Tab)。",
    "Leave the current mode first (Tab).": "先に現在のモードを抜けてください (Tab)。",
    "No Mirror — press 'Refresh Mirror' first.":
        "Mirror がありません — 先に 'Refresh Mirror' を押してください。",
    "Object Mode only.": "Object Mode 専用です。",
    "Refresh runs in Object or Edit Mode.":
        "Refresh は Object Mode か Edit Mode で実行してください。",
    "Put the Guide in Edit Mode.": "Guide を Edit Mode にしてください。",
    "Select the side you trust on the Retopo in Edit Mode.":
        "Retopo の Edit Mode で、正としたい側を選択してください。",
    "Select boundary edges on the Retopo in Edit Mode.":
        "Edit Mode で Retopo の外周辺を選択してください。",
    "Select two lines on the Retopo in Edit Mode.":
        "Edit Mode で Retopo の線を 2 本選択してください。",
    "Select vertices on the Retopo in Edit Mode.": "Edit Mode で Retopo の頂点を選択してください。",
    "Set the Guide and Flat SK first.": "先に Guide と Flat SK を設定してください。",
    "Set the Guide and Retopo first.": "先に Guide と Retopo を設定してください。",
    "3D Check": "3D 確認",
    "Repair": "修復",
    "Repair Guide": "Guide を修復",
    "Preview Fill needs shapely (ships with the add-on; not available for this Blender)":
        "Preview Fill には shapely が必要です（アドオンに同梱していますが、この Blender では利用できません）",
    "Preview Fill needs shapely, which ships with the add-on.":
        "Preview Fill には shapely が必要です（アドオンに同梱しています）。",
    "Every step works on the Guide (Setup). The later steps need step 1; Basis is left untouched":
        "すべてのステップは Guide（Setup）に対して動く。後のステップは 1 が必要。Basis は触らない",
    "Show / Untag / Inset Line: put the Guide in Edit Mode":
        "Show / Untag / Inset Line：Guide を Edit Mode にする",
    "Inset Line: run Inset Pieces first (step 3, Object Mode)":
        "Inset Line：先に Inset Pieces を実行（手順 3、Object Mode）",
    "Run Inset Pieces first (step 3, Object Mode).":
        "先に Inset Pieces を実行してください（手順 3、Object Mode）。",
    "UV Mirror is Experimental (Add-on Preferences > Experimental tools).":
        "UV Mirror は Experimental です（アドオン設定 > Experimental tools）。",
    "Regions / × : selected islands only":
        "Regions / × ：選択アイランドのみ",
    "Density, Pins and Corners are Experimental (Add-on Preferences > Experimental tools).":
        "Density・Pin・Corner は Experimental です（アドオン設定 > Experimental tools）。",
    "Set the Guide first.": "先に Guide を設定してください。",
    "The Guide has no active UV layer.": "Guide にアクティブな UV レイヤーがありません。",
    "The Guide must be in Edit Mode.": "Guide が Edit Mode である必要があります。",
    "The Guide must be in Object Mode.": "Guide が Object Mode である必要があります。",
    "Set the Guide's Flat SK first.": "先に Guide の Flat SK を設定してください。",
    "Set the Retopo first.": "先に Retopo を設定してください。",
    "Subdivide runs in the 2D state only — press 'Sync 3D > 2D' first.":
        "Subdivide は 2D 状態専用です — 先に 'Sync 3D > 2D' を押してください。",

    # ---- operator bl_description ----
    "Add the vertices a seam's sparser side is missing, copied from its sewn partner at the matching position along the seam. Select the side you trust on the Retopo in Edit Mode: only the seam(s) that selection touches run, and the selected side is the source. Add-only — never moves or deletes existing vertices. A new vertex that lands on an existing edge splits it. A seam where both sides hold vertices the other lacks is left alone and reported":
        "Seam の手薄な側に不足している頂点を、縫い相手から Seam 沿いの対応位置にコピーして足します。Retopo の Edit Mode で、正としたい側を選択してください: その選択が触れる Seam だけが対象で、選択側が元になります。足すだけです — 既存の頂点は動かしも消しもしません。新しい頂点が既存の辺の上に来る場合はその辺を割ります。両側に相手の無い頂点がある Seam は触れずに報告します",
    "Bind vertices that were created in 3D Edit Mode (their stored attachment is missing or inconsistent with their 2D position) to the Guide surface, and repair their 2D Basis position. Uses the neighbouring verts' attachments to stay on the correct fold side of the fabric. Runs automatically on Edit Mode exit when 'Auto Bind New Verts' is on":
        "3D の Edit Mode で作られた頂点（アタッチメントが未保存、または 2D 位置と食い違っている頂点）を Guide 表面にバインドし、2D Basis 位置を修復します。隣接頂点のアタッチメントを参照して、生地の正しい折り側に留まります。'Auto Bind New Verts' が ON なら Edit Mode を抜けたときに自動実行されます",
    "Change how many vertices the selected span(s) carry — both sides of the sewn seam together, so they keep matching in 3D. Select boundary edges (or one interior boundary vertex) on the Retopo in Edit Mode. Tries a local edit that keeps every face first, and rebuilds a span only where no safe edit exists (which also clears the Preview Fill). Pins and Corners stay exactly where they are":
        "選択したスパンの頂点数を変更します — 縫い Seam の両側をまとめて変えるので、3D でも一致したままになります。Edit Mode で Retopo の外周辺（または内部の外周頂点 1 つ）を選択してください。まず面を壊さないローカルな編集を試し、安全な編集ができない場合だけスパンを作り直します（このとき Preview Fill もクリアされます）。Pin と Corner はその位置から動きません",
    "Check the whole Guide outline against the retopo.\n"
    "\n"
    "SEWN SEAMS\n"
    "• green = both sides pair up\n"
    "• red = the two sides hold a different NUMBER of vertices\n"
    "    (Match Seams or Generate fills that in)\n"
    "• purple = same number, but out of line along the seam\n"
    "    (those vertices have to be moved by hand)\n"
    "• orange = one side only\n"
    "\n"
    "FREE EDGES — hems, openings, no partner side\n"
    "• teal = the retopo sits on the outline\n"
    "• purple = it has drifted more than 1 mm off it\n"
    "\n"
    "Colours them in the viewport (Seam Status overlay), selects the "
    "vertices on the problem seams, and writes the full table to the "
    "Text 'AC9_SeamStatus'. Also counts T-junctions (a vertex lying on "
    "an edge it is not joined to)":
        "Guide の型紙輪郭全体を retopo と照合します。\n"
        "\n"
        "縫い SEAM\n"
        "• 緑 = 両側がペアになっている\n"
        "• 赤 = 両側の頂点数が違う\n"
        "    （Match Seams / Generate で埋まる）\n"
        "• 紫 = 数は同じだが Seam に沿って位置がそろっていない\n"
        "    （頂点を手で動かす必要がある）\n"
        "• オレンジ = 片側だけ\n"
        "\n"
        "フリー辺 — 裾・開口部など、相手側がない辺\n"
        "• 青緑 = retopo が輪郭に載っている\n"
        "• 紫 = 輪郭から 1mm 以上ずれている\n"
        "\n"
        "ビューポートで色を付け（Seam ステータス Overlay）、問題のある "
        "Seam とランの頂点を選択し、全一覧をテキスト 'AC9_SeamStatus' に "
        "書き出します。T字接合（辺の上に乗っているのに繋がっていない頂点）も数えます",
    "Clear every Guide analysis: seam pairs (and the ghosts built from them), folds, twins and anchors. Overlays that draw them go blank until the next Analyze":
        "Guide の解析結果をすべてクリアします: Seam のペア（およびそこから作った Ghost）、折れ線、Twin、Anchor。これらを描く Overlay は次の解析まで空になります",
    "Clear the detected fold lines": "検出した折れ線をクリアします",
    "Clear the detected twin pairs": "検出した Twin のペアをクリアします",
    "Clear the ghost point overlay": "Ghost 点の Overlay をクリアします",
    "Clear the seam analysis: cached seam pairs, seam overlay and ghosts":
        "Seam の解析結果をクリアします: キャッシュした Seam ペア・Seam Overlay・Ghost",
    "Clear the symmetry analysis: fold lines and twin pairs":
        "対称性の解析結果をクリアします: 折れ線と Twin のペア",
    "Collapse the selected edges to their midpoints like Mesh > Merge > Collapse, but keep the UVs intact (native Collapse pinches/destroys the UV island). UVs on every layer follow to the midpoint; UV seams crossing the collapse stay separate":
        "選択した辺を Mesh > Merge > Collapse と同様に中点へ collapse しますが、UV はそのまま保ちます（ネイティブの Collapse は UV アイランドを潰して壊します）。全レイヤーの UV が中点へ追従し、collapse をまたぐ UV シームは分かれたままです",
    "Delete the Mirror (the read-only AC9_3D_Mirror object) for the current retopo. Rebuild it any time with 'Refresh Mirror'":
        "現在の retopo の Mirror（読み取り専用の AC9_3D_Mirror オブジェクト）を削除します。'Refresh Mirror' でいつでも作り直せます",
    "Deselect all, then select only the quads of the chosen kind. Use it to SEE how many of the flagged faces are simple folds vs real saddles before fixing":
        "全選択を解除し、指定した種類のクワッドだけを選択します。修正する前に、フラグの立った面のうち単純な折れがいくつ、本当のサドルがいくつあるかを目で確かめるのに使います",
    "Detect both kinds of symmetry on the Guide's flat pattern: folds (a mirror axis within a single island) and twins (separate left/right islands that reflect each other). Runs Detect Folds and Detect Twins. Generate Boundary uses the folds to place vertices on the axis; Symmetrize and Replace Twin Island rebuild one side from the other":
        "Guide の平面型紙上で 2 種類の対称性を検出します: 折れ（1 つのアイランド内のミラー軸）と Twin（互いに反転した左右別アイランド）。Detect Folds と Detect Twins を実行します。Generate Boundary は折れを使って軸上に頂点を配置し、Symmetrize と Replace Twin Island は片側からもう片側を作り直します",
    "Discard the cached triangle lists and 2D BVH for the current Guide mesh.  The cache is rebuilt automatically on the next Sync operation.  Run this after editing Guide mesh geometry (vertex positions) without replacing the data-block — e.g. after sculpting or vertex-level edits on the CLO export":
        "現在の Guide メッシュについて、キャッシュした三角形リストと 2D BVH を破棄します。キャッシュは次の Sync 操作で自動的に再構築されます。データブロックを差し替えずに Guide メッシュのジオメトリ（頂点位置）を編集した後に実行してください — CLO 書き出しをスカルプトしたり頂点単位で編集した場合などです",
    "Find self-symmetric (cut-on-fold) UV islands on the Guide and draw each one's fold (centre) line. Tests every island's outline shape for bilateral symmetry — independent of 3D drape and UV placement. Self-symmetric panels (back body, waistband, plackets, cuffs) get a fold line; left/right twins (sleeves, front panels) do not — those are found by Detect Twins":
        "Guide 上の自己対称（わ裁ち）な UV アイランドを見つけ、それぞれの折れ（中心）線を描きます。各アイランドの輪郭形状を左右対称性でテストします — 3D のドレープや UV 配置には依存しません。自己対称な型紙（後身・ベルト・前立て・カフス）には折れ線が付き、左右の Twin（袖・前身）には付きません — そちらは Detect Twins が見つけます",
    "Find the sewn seam pairs on the Guide (in Flat SK space) and build the seam overlay. Ghosts, snapping and the seam overlays all read this result. It is cleared on file open and on Reload Scripts":
        "Guide 上の縫い Seam のペアを（Flat SK 空間で）見つけ、Seam Overlay を作ります。Ghost・スナップ・Seam Overlay はすべてこの結果を読みます。ファイルを開いたときと Reload Scripts でクリアされます",
    "Find twins: separate UV islands that are left/right reflections of each other (e.g. a left sleeve and a right sleeve), by matching outline shape in the flat pattern layout. Different from Detect Folds, which finds a fold line WITHIN one island. Pattern pieces are reflected as FLAT PATTERNS — the 3D drape of the two sides differs even when the pattern is identical, so the test is 2D only":
        "Twin を見つけます: 互いに左右反転した別々の UV アイランド（左袖と右袖など）を、平面型紙レイアウト上の輪郭形状で照合します。1 つのアイランドの「中」に折れ線を見つける Detect Folds とは別物です。型紙は「平面型紙として」反転されます — 型紙が同一でも左右の 3D ドレープは違うので、テストは 2D のみで行います",
    "For each retopo vertex within 'Align Threshold' of the Guide's pattern outline (a UV seam edge) in 2D, snap its Basis (2D) position onto that edge and rebuild its attachment so one barycentric coordinate is exactly 0. Flags the vertex as boundary so the legacy 'Sync 3D > 2D' pins it perfectly to the outline. Run once after 'Sync 2D > 3D'":
        "2D で Guide の型紙輪郭（UV シーム辺）から 'Align Threshold' 以内にある retopo 頂点を、その辺の上へ Basis（2D）位置をスナップし、バリセントリック座標の 1 つがちょうど 0 になるようアタッチメントを作り直します。頂点は boundary としてフラグが立つので、旧来の 'Sync 3D > 2D' が輪郭にぴったり固定します。'Sync 2D > 3D' の後に 1 度実行してください",
    "Make a self-symmetric panel (one island with a fold — see Detect Folds) match itself: keep the side the SELECTED vertex is on, rebuild the other side as its reflection across the fold. Destructive — the other side is deleted and recreated. Undo (Ctrl+Z) to revert":
        "自己対称な型紙（折れを持つ 1 アイランド — Detect Folds 参照）を自分自身に一致させます: 選択した頂点がある側を残し、反対側を折れを軸にした反転として作り直します。破壊的です — 反対側は削除して再生成されます。元に戻すには Undo (Ctrl+Z)",
    "Mark the selected edges by hand as a crease (Inset Line will pick them up), or untag them. Edit Mode":
        "選択した辺を手動でクリースとしてマーク（Inset Line が拾います）、または解除します。Edit Mode",
    "Move selected retopo vertices; each snaps to the nearest point on the full Guide pattern outline (sewn seams + free edges) when within Snap Distance. Activated by G when Outline Snap Mode is ON. Use it to drop boundary verts onto the pattern outline":
        "選択した retopo 頂点を移動します。各頂点は Snap Distance 以内なら Guide の型紙輪郭全体（縫い Seam + フリー辺）の最近点にスナップします。Outline Snap Mode が ON のとき G で起動します。外周頂点を型紙の輪郭に載せるのに使います",
    "Move selected retopo vertices; snaps only to ghost points when within Snap Distance. Activated by G when Ghost Snap Mode is ON. A free edge's ghost is a fixed point on the outline from the last Refresh, not a line that follows the drag — use Outline Snap to slide a vertex along a hem":
        "選択した retopo 頂点を移動します。Snap Distance 以内の Ghost 点にだけスナップします。Ghost Snap Mode が ON のとき G で起動します。フリー辺の Ghost は前回 Refresh 時点の輪郭上の固定点で、ドラッグに追従する線ではありません — 裾に沿って頂点をずらすには Outline Snap を使ってください",
    "Print the world-space bounding boxes of the detected seam pairs, the retopo boundary verts, and the guide's flat layout to the system console. If the seam box and retopo box do not overlap, they are in different coordinate spaces (a matrix_world / normalisation mismatch)":
        "検出した Seam ペア・retopo の外周頂点・Guide の平面レイアウトのワールド空間バウンディングボックスをシステムコンソールに出力します。Seam のボックスと retopo のボックスが重なっていなければ、両者は別の座標空間にあります（matrix_world / 正規化の不一致）",
    "Re-project every 2D retopo vertex onto Guide 3D (Basis) and write the result to the AC9_3D_Project ShapeKey. Press whenever you've edited the 2D layout. Counterpart of 'Sync 3D > 2D'":
        "すべての 2D retopo 頂点を Guide 3D（Basis）へ再投影し、結果を AC9_3D_Project ShapeKey に書き込みます。2D レイアウトを編集したら押してください。'Sync 3D > 2D' の対になる操作です",
    "Re-space the selected span's vertices evenly along the seam, both sides together. Only moves existing vertices — never adds or removes one, never touches a face. Select boundary edges on the Retopo in Edit Mode":
        "選択したスパンの頂点を Seam に沿って両側そろえて等間隔に配置し直します。既存の頂点を動かすだけで、追加も削除もせず、面にも触りません。Edit Mode で Retopo の外周辺を選択してください",
    "Read-only: match every Retopo island to its Guide island and report the result (full table in a Text datablock). Runs Detect Twins first if needed. Nothing is written to the mesh":
        "読み取り専用: すべての Retopo アイランドを Guide のアイランドに対応付け、結果を報告します（全一覧はテキストデータブロックへ）。必要なら先に Detect Twins を実行します。メッシュには何も書き込みません",
    "Rebuild the Mirror (the read-only AC9_3D_Mirror object) from the current 2D retopo layout, projected onto the Guide surface. The retopo stays flat (2D) and editable; the Mirror shows the 3D result on the garment. Press after editing the 2D layout — works in Object and Edit Mode. The Mirror is a viewer: any edits made to it are overwritten on the next Refresh":
        "現在の 2D retopo レイアウトを Guide 表面に投影して、Mirror（読み取り専用の AC9_3D_Mirror オブジェクト）を作り直します。retopo は平面（2D）のまま編集でき、Mirror が衣装上での 3D 結果を見せます。2D レイアウトを編集したら押してください — Object Mode でも Edit Mode でも動きます。Mirror はビューアです: そこへ加えた編集は次の Refresh で上書きされます",
    "Turn the Wireframe overlay on or off in EVERY 3D Viewport at once. Retopo work is usually split across a 2D view and a 3D view, and wanting the wires in one of them but not the other is not a thing that happens — so both follow the viewport the button was pressed in":
        "すべての 3D ビューポートのワイヤーフレーム Overlay を一括で ON / OFF します。リトポ作業は 2D ビューと 3D ビューに分かれているのが普通で、片方だけワイヤーが欲しい場面はまず無いため、押したビューポートの状態に両方を揃えます",
    "Recompute the opposite-side ghost points from the retopo's current boundary. Runs Analyze Seams first if no seam analysis is loaded":
        "retopo の現在の外周から、対岸の Ghost 点を再計算します。Seam の解析結果が読み込まれていなければ、先に Analyze Seams を実行します",
    "Remove every crease tag (the ac9_crease_kind edge attribute)":
        "すべてのクリースタグ（ac9_crease_kind 辺属性）を削除します",
    "Remove every trace of AC9 Cloth Retopo from this file: the Mirror object, the add-on's attribute layers, shape key, vertex group, modifier, material slots and custom properties on every mesh, the bake images and report text, and the scene settings (which also removes the header buttons). Your geometry, UVs, materials and the Guide's Flat shape key are left alone. A retopo currently shown in 3D keeps its 3D layout as the mesh":
        "AC9 Cloth Retopo がこのファイルに書いたものをすべて削除します: Mirror オブジェクト、各メッシュ上のアドオンの属性レイヤー・シェイプキー・頂点グループ・モディファイア・マテリアルスロット・カスタムプロパティ、ベイク画像とレポートテキスト、シーン設定（これによりヘッダーのボタンも消えます）。ジオメトリ・UV・マテリアル・Guide の Flat シェイプキーはそのまま残ります。3D 表示中の retopo は、その 3D レイアウトをメッシュとして保持します",
    "Remove the per-vertex attachment data (ac9_tri_idx / ac9_bary_u / ac9_bary_v / ac9_status) from the retopo. Useful before rebinding to a different Guide":
        "retopo から頂点ごとのアタッチメントデータ（ac9_tri_idx / ac9_bary_u / ac9_bary_v / ac9_status）を削除します。別の Guide に再バインドする前に便利です",
    "Replace the twin of the SELECTED vertex's island with a reflected copy of it, so left/right topology finally matches. Select a vertex on the side you trust. Destructive — deletes and recreates the OTHER island. Undo (Ctrl+Z) to revert":
        "選択した頂点のアイランドの Twin を、そのアイランドの反転コピーで置き換え、左右のトポロジーをようやく一致させます。信頼できる側の頂点を選択してください。破壊的です — 「反対側」のアイランドを削除して作り直します。元に戻すには Undo (Ctrl+Z)",
    "Run every Guide analysis in one go: Analyze Seams (sewn seam pairs — what the overlays, ghosts and snapping read), Analyze Symmetry (folds and twins) and Analyze Anchors (span divisions). Read-only on the Guide; nothing touches the retopo. The seam analysis is cleared on file open and on Reload Scripts, so run this again then":
        "Guide の解析を一度にすべて実行します: Analyze Seams（縫い Seam のペア — Overlay・Ghost・スナップが読む情報）、Analyze Symmetry（折れと Twin）、Analyze Anchors（スパンの区切り）。Guide に対しては読み取り専用で、retopo には触りません。Seam の解析結果はファイルを開いたときと Reload Scripts でクリアされるので、そのときは再実行してください",
    "Select one vertex on the RETOPO mesh (the object set as 'Retopo', not the guide) and run this to print its matched seam pair to the system console. Use it to confirm a known pair lands where you expect":
        "RETOPO メッシュ（'Retopo' に設定したオブジェクト。Guide ではありません）の頂点を 1 つ選んでこれを実行すると、対応付けられた Seam ペアがシステムコンソールに出力されます。既知のペアが期待どおりの場所に来るか確かめるのに使います",
    "Select the crease-tagged edges, to see what Inset Line will pick up. Edit Mode":
        "クリースタグの付いた辺を選択して、Inset Line が何を拾うかを確認します。Edit Mode",
    "Snap each SELECTED retopo vertex exactly onto its nearest ghost when within Snap Distance (the same radius as Ghost Snap on G). Non-destructive: selected verts with no ghost in range — and all unselected verts — are left untouched. Use to finish seams you placed roughly but forgot to snap exactly. A free edge's ghost is the point on the outline the vertex belongs on, fixed at the last Refresh: it does not follow a vertex as you drag it, so to walk a vertex along a hem use Outline Snap instead":
        "選択した各 retopo 頂点を、Snap Distance 以内にある最も近い Ghost にぴったりスナップします（G の Ghost Snap と同じ半径）。非破壊的です: 範囲内に Ghost がない選択頂点と、選択されていない頂点はすべて手つかずです。だいたいの位置に置いたまま正確なスナップを忘れた Seam を仕上げるのに使います。フリー辺の Ghost は、その頂点が載るべき輪郭上の点で、前回 Refresh 時点に固定されています: ドラッグに追従しないので、裾に沿って頂点をずらすには Outline Snap を使ってください",
    "Step 1. Tag every edge whose two faces meet at Crease Min Angle or more as a crease (fold lines and, on a mesh that already has thickness, its rim edges at 90 degrees). With a planar shape key, measured on the Basis shape regardless of which key is displayed. A live Solidify modifier is not seen here, and that is fine: its rims are built from the outline the Inset step has already regularised":
        "手順 1。2 つの面が Crease Min Angle 以上で交わる辺をすべてクリースとしてタグ付けします（折れ線と、すでに厚みのあるメッシュならその 90 度のリム辺）。平面シェイプキーがある場合、どのキーが表示されているかに関わらず Basis 形状で測ります。ライブの Solidify モディファイアはここでは見えませんが、それで問題ありません: そのリムは Inset 手順ですでに整えた輪郭から作られるからです",
    "Step 3. Run after Inset Pieces. Inset a fold line to both sides: a band Width wide on each side of the line is rebuilt as triangles, with the fold's own vertices and edges left exactly where they are. Uses the SELECTED EDGES (each connected run is one line); with nothing selected, the crease edges tagged by Find Folds. The band stops at the row Inset Pieces left along the outline. Edit Mode":
        "手順 3。Inset Pieces の後に実行します。折れ線を両側にインセットします: 線の左右それぞれ Width の幅の帯を三角形として張り直し、折れ線自身の頂点と辺はその場に残します。選択された「辺」を使い（つながった 1 本が 1 つの線）、何も選択されていなければ Find Folds がタグ付けしたクリース辺を使います。帯は Inset Pieces が輪郭沿いに残した行で止まります。Edit Mode",
    "Step 2. Run before Inset Line. For every pattern piece: offset the outline inward by Width on the flat shape key and rebuild the ring between the two as triangles, so a vertex row runs parallel to the outline, seams and free edges alike. Nothing is welded, so every outline vertex survives and the sewn pairs stay matched. The parallel-internal-line trick, done in Blender on the raw CLO export. Object Mode":
        "手順 2。Inset Line より先に実行します。各型紙について: 平面シェイプキー上で輪郭を Width だけ内側へオフセットし、その間のリングを三角形として張り直します。縫い Seam でもフリー辺でも区別なく、輪郭に平行な頂点列が走ります。頂点を溶接しないので、輪郭の頂点は 1 つも消えず、縫い合わせのペアも対応したまま保たれます。いわゆる「輪郭に平行な内部線」の手法を、CLO の生書き出しに対して Blender 上で行います。Object Mode",
    "Subdivide the retopo in 2D (simple/linear), snap new boundary verts to the Guide seam lines, then re-project to 3D. Because the new verts are projected onto the Guide surface, the result follows the garment shape — no smoothing needed. Destructive: bumps resolution permanently. Runs in the 2D state only — press 'Sync 3D > 2D' first if you are in 3D":
        "retopo を 2D で（単純／線形に）Subdivide し、新しい外周頂点を Guide の Seam 線にスナップしてから 3D へ再投影します。新しい頂点は Guide 表面に投影されるため、結果は衣装の形状に沿います — スムーズ処理は不要です。破壊的です: 解像度が恒久的に上がります。2D 状態でのみ動きます — 3D にいる場合は先に 'Sync 3D > 2D' を押してください",
    "Show or hide a reversible, Guide-projected subdivision on the Mirror. While it is on, every Refresh rebuilds it from the current live 2D edit mesh; the low-poly Retopo is not changed":
        "Guide に再投影した可逆な Subdivision を Mirror に表示／非表示します。ON の間は Refresh のたびに現在の 2D 編集メッシュから再構築し、低解像度の Retopo は変更しません",
    "Subdivision Mirror": "Subdivision Mirror",
    "Detail": "分割レベル",
    "Show Subdivided": "分割表示を ON",
    "Update Subdivided": "分割表示を更新",
    "Subdivided": "分割表示 ON",
    "Stays on when Refresh rebuilds the Mirror; Retopo stays low-poly":
        "Refresh 後も分割表示を維持します。Retopo は低解像度のままです",
    "Apply Subdivision": "Subdivision を適用",
    "Take the moves you made to existing vertices on the Mirror, snap them onto the Guide surface, and rewrite the 2D retopo layout to match (boundary verts stay pinned to the CLO outline). Move-only: do NOT add verts or cut on the mirror — new geometry must be made in 2D. The mirror is rebuilt to the clean snapped result afterwards":
        "Mirror 上で既存の頂点に加えた移動を取り込み、Guide 表面にスナップして、2D retopo レイアウトを合わせて書き換えます（外周頂点は CLO の輪郭に固定されたままです）。移動のみです: Mirror 上で頂点を追加したりカットしたりしないでください — 新しいジオメトリは 2D で作ります。その後 Mirror はスナップ済みのきれいな結果に作り直されます",
    "Take the retopo's current 3D ShapeKey state, snap each vertex to the nearest point on Guide 3D (Basis), and rewrite the 2D Basis layout to match. Both Basis and ShapeKey are updated so they stay consistent":
        "retopo の現在の 3D ShapeKey 状態を取り込み、各頂点を Guide 3D（Basis）の最近点にスナップして、2D Basis レイアウトを合わせて書き換えます。Basis と ShapeKey の両方が更新され、整合が保たれます",
    "Triangulate each non-planar quad along the CONVEX diagonal (the one the artist would pick — outward bulge, matches the rounded surface). Use 'Alternate' + Undo to A/B the other diagonal and see what goes wrong":
        "各非平面クワッドを凸側の対角線（作業者が選ぶ方 — 外側に膨らみ、丸い面に沿う方）で三角化します。'Alternate' と Undo でもう一方の対角線を A/B 比較して、何がおかしくなるか確かめられます",

    # ---- property description= ----
    "A quad counts as non-planar when its warp (the LARGER of its two diagonal fold angles) exceeds this. Matches the 'Non-planar Angle' of a typical mesh checker. Quads below this are left alone":
        "クワッドのねじれ（2 つの対角線の折れ角のうち「大きい方」）がこれを超えると非平面と判定します。一般的なメッシュチェッカーの 'Non-planar Angle' に相当します。これ未満のクワッドは手つかずです",
    "Actually create the vertex and mark the pins. Leave OFF to only report what would happen":
        "実際に頂点を作成して Pin を付けます。OFF なら何が起きるかの報告だけ行います",
    "Actually create the vertices. Leave OFF to only report what would be added":
        "実際に頂点を作成します。OFF なら何が追加されるかの報告だけ行います",
    "Also Density-Pin both sides of every pair this touches, so Adjust Density leaves them exactly where they are":
        "この操作が触る各ペアの両側に密度 Pin も付け、Adjust Density がその位置を動かさないようにします",
    "Also fill regions whose opposite sides carry different numbers of vertices. The core grid follows the boundary counts and the difference is absorbed by one row of triangles, which are left selected so they can be found. Regions where that row would fan many triangles onto one vertex (a short curved side against a long one, e.g. a sleeve cap) are refused and reported instead. Regions whose sides already match are unaffected — they come out as pure quads either way":
        "対辺の頂点数が違う領域も Fill します。中心のグリッドは外周の頂点数に従い、差分は 1 列の三角形で吸収します。その三角形は見つけやすいよう選択状態で残ります。その列が多数の三角形を1 頂点に扇状に集めてしまう領域（短い曲線辺と長い辺の組み合わせ、袖山など）は拒否して報告します。対辺がすでに一致している領域には影響しません — どちらにせよ純粋なクワッドになります",
    "Arm length of ghost cross markers (flat-layout world units)":
        "Ghost の十字マーカーの腕の長さ（平面レイアウトのワールド単位）",
    "Arm length of the anchor markers (flat-layout world units)":
        "Anchor マーカーの腕の長さ（平面レイアウトのワールド単位）",
    "Arm length of the boundary-vert cross marker (world units)":
        "Boundary 頂点の十字マーカーの腕の長さ（ワールド単位）",
    "Cell size for the structured grid, in mm of fabric (converted into the flat layout the retopo lives in). 0 follows the boundary Spacing above":
        "構造グリッドのセルサイズ。単位は布の実寸 mm（retopo が住む平面レイアウトへ変換されます）。0 にすると上の境界 Spacing に従います",
    "Clear every corner on the mesh, not just the selection":
        "選択範囲だけでなく、メッシュ上のすべての Corner をクリアします",
    "Clear every pin on the mesh, not just the selection":
        "選択範囲だけでなく、メッシュ上のすべての Pin をクリアします",
    "Colour the Guide outline by the last Seam Status run.\n"
    "\n"
    "SEWN SEAMS\n"
    "• green = both sides authored and aligned\n"
    "• red = the two sides hold different numbers of vertices\n"
    "• purple = the same number, but out of line along the seam\n"
    "• orange = only one side done\n"
    "\n"
    "FREE EDGES — no partner side, so what is checked is whether\n"
    "the retopo sits on the outline\n"
    "• teal = on it\n"
    "• purple = more than 1 mm off\n"
    "\n"
    "Drawn OVER the Seams and Free Edges overlays, so those two go "
    "dim while this is on. Seams and runs with no retopo yet are "
    "left uncoloured. Run 'Seam Status' to fill it in":
        "直前の Seam Status の結果で Guide の輪郭を色分けします。\n"
        "\n"
        "縫い SEAM\n"
        "• 緑 = 両側が作られていてそろっている\n"
        "• 赤 = 両側の頂点数が違う\n"
        "• 紫 = 数は同じだが Seam に沿って位置がそろっていない\n"
        "• オレンジ = 片側だけ完了\n"
        "\n"
        "フリー辺 — 相手側がないので、retopo が輪郭に\n"
        "載っているかを見ます\n"
        "• 青緑 = 載っている\n"
        "• 紫 = 1mm 以上ずれている\n"
        "\n"
        "Seams / Free Edges の Overlay の上に描かれるので、ON の間は "
        "その 2 つはグレー表示になります。retopo が未着手の Seam・ランは "
        "色が付きません。'Seam Status' を実行すると埋まります",
    "Decimal places for flat-coordinate duplicate detection": "平面座標の重複判定に使う小数桁数",
    "Detect Candidates proposes a corner wherever the pattern outline turns at least this much. It only ever proposes — the set is yours to edit afterwards":
        "Detect Candidates は、型紙の輪郭がこれ以上曲がる場所に Corner を提案します。あくまで提案するだけで、その後の取捨選択は作業者に任されます",
    "The scale of detail (mm) the drape Curvature reports. The Guide is smoothed with a kernel this wide and the map shows how far the surface sits above that smoothed copy along its normal: white is convex (a ridge), black concave (a fold), mid grey flat. Features much wider than the radius are smoothed away with the reference and disappear; set it near the width of the ridges you cut along. Cost rises with the square of the radius":
        "Curvature が拾う凹凸のスケール（mm）。この幅で Guide を平滑化し、平滑化した形から法線方向にどれだけ浮いているかを出す。白＝凸（稜線）、黒＝凹（折り目）、中間のグレー＝平ら。半径よりずっと広い起伏は基準ごと平滑化されて消えるので、切りたい稜線の幅くらいに合わせる。コストは半径の2乗で増える",
    "How far (mm) the drape AO looks for occluders — the scale of detail the map reports. Around a fold's own width it draws folds; far above that it only reports how enclosed a region is, and any panel sewn flat onto another (pocket, placket, tab) goes solid black because its neighbour is well inside the distance. The Guide occludes itself only — the body never darkens it":
        "Drape の AO が遮蔽物を探す距離 (mm)。マップが拾うディテールのスケールそのもので、皺の幅くらいなら皺が描かれます。それよりずっと大きいとその領域が囲まれているかどうかしか出ず、他の型紙に密着して縫われた型紙（ポケット・当て布・タブ）は相手が距離の内側に入りきるので真っ黒になります。遮蔽するのは Guide 自身だけで、身体がマップを暗くすることはありません",
    "How much of the AO goes into the combined Drape map, which is Curvature x (1 - mix + mix x AO) — the same arithmetic as a Mix node set to MULTIPLY with this as its Factor. At 1.0 the AO's dark folds bury the Curvature creases you are actually cutting along, so it is eased off by default":
        "合成 Drape Map に AO をどれだけ効かせるか。式は Curvature x (1 - mix + mix x AO) で、Mix ノードを MULTIPLY にして Factor にこの値を入れたのと同じです。1.0 だと AO の暗い皺が、実際に切る対象である Curvature の折り目を潰してしまうので、既定では弱めてあります",
    "Distance (mm) at which the residual map saturates to full red/blue. Keep it FIXED across iterations so successive bakes are directly comparable — 'whiter than last time' then really means the retopo got closer":
        "Residual Map が完全な赤／青に飽和する距離 (mm)。繰り返しの間はこの値を「固定」しておいてください。そうすれば連続したベイクを直接比べられ、「前回より白い」が本当に retopo が近づいたという意味になります",
    "Draw a circle of the Snap Distance radius around every ghost point. Helps judge whether a retopo vertex is within snapping range":
        "各 Ghost 点のまわりに Snap Distance 半径の円を描きます。retopo 頂点がスナップ範囲内にあるかを判断する助けになります",
    "Draw a connector between the Flat-SK centroids of each detected twin pair (e.g. left sleeve ↔ right sleeve). Run Detect Twins (or Analyze Symmetry) first":
        "検出した各 Twin ペアの Flat SK 重心どうしをつなぐ線を描きます（左袖 ↔ 右袖など）。先に Detect Twins（または Analyze Symmetry）を実行してください",
    "Draw a green + cross at every retopo vertex flagged as boundary (ac9_is_boundary = True). The flag is written by Sync 2D>3D / Sync 3D>2D / Align Boundary to Outline, so this only shows after you have run one of those at least once":
        "boundary フラグが立っている（ac9_is_boundary = True）retopo 頂点すべてに緑の + 十字を描きます。このフラグは Sync 2D>3D / Sync 3D>2D / Align Boundary to Outline が書くので、いずれかを 1 度実行した後にだけ表示されます",
    "Draw connector lines between corresponding seam endpoints":
        "対応する Seam 端点どうしをつなぐ線を描きます",
    "Draw lines from every retopo vertex to its opposite-side ghost. Off by default — the full set is cluttered; use 'Show Pair for Selected Only' to see just the connections you care about":
        "各 retopo 頂点から対岸の Ghost へ線を描きます。既定は OFF です — 全部出すとごちゃつくので、見たい接続だけを見るには 'Show Pair for Selected Only' を使ってください",
    "Draw orange markers at corresponding positions. A 2D Retopo selection uses its live Guide projection; a Mirror selection uses the 2D source stored by the last Refresh. Works with vertex / edge / face / loop / shortest-path selections":
        "対応する位置にオレンジのマーカーを描きます。2D Retopo 側の選択には現在の Guide 投影位置を使い、Mirror 側の選択には最後の Refresh で記録した生成元 2D 位置を使います。頂点／辺／面／ループ／最短パスのどの選択でも動きます",
    "Draw the detected fold (centre) line of each self-symmetric UV island. Run Detect Folds (or Analyze Symmetry) first":
        "自己対称な各 UV アイランドについて、検出した折れ（中心）線を描きます。先に Detect Folds（または Analyze Symmetry）を実行してください",
    "Draw the Guide's full pattern outline as white lines over the Guide mesh: every open-boundary edge (the pattern outline — sewn seams and free edges alike), plus any UV-seam-marked edge. Always on top, so it stays visible in Solid mode, and follows the Guide's current ShapeKey blend (2D ↔ 3D). The cyan Seams overlay shows only the sewn pairs":
        "Guide の型紙輪郭全体を Guide メッシュの上に白い線で描きます: 開いた境界辺すべて（型紙の輪郭 — 縫い Seam もフリー辺も）に加え、UV シームが付いた辺も描きます。常に最前面なので Solid モードでも見え、Guide の現在の ShapeKey ブレンド（2D ↔ 3D）に追従します。シアンの Seams Overlay は縫いペアだけを表示します",
    "Draw the pattern's FREE edges (yellow) — every boundary edge with no sewn partner: hems, necklines, openings, a collar's outer edge. Together with the cyan Seams these make up the whole pattern outline in the flat layout. Filled in by Analyze Seams":
        "型紙のフリー辺（黄）を描きます — 縫い相手のない境界辺すべて: 裾・襟ぐり・開口部・襟の外周など。シアンの Seams と合わせて、平面レイアウト上の型紙輪郭全体になります。Analyze Seams で作られます",
    "Draw the sewn seam pairs found by Analyze Seams (cyan). The white Outline overlay shows the whole pattern outline including free edges":
        "Analyze Seams が見つけた縫い Seam のペアを（シアンで）描きます。白い Outline Overlay はフリー辺を含む型紙輪郭全体を表示します",
    "Extend from the selected vertices instead of from every loose end":
        "すべての開いた端からではなく、選択した頂点から延長します",
    "Find Folds marks every edge whose two faces meet at this angle or more (degrees). 60 catches Solidify rims (90) and pressed folds while leaving drape wrinkles alone":
        "Find Folds は、2 つの面がこの角度（度）以上で交わる辺をすべてマークします。60 なら Solidify のリム（90 度）と押さえた折れを拾い、ドレープのしわは拾いません",
    "Half-width of the density-pin squares (flat-layout world units)":
        "密度 Pin の四角マーカーの半幅（平面レイアウトのワールド単位）",
    "Half-width of the inset, in real fabric distance: the new vertex row is placed this far in from the outline (Inset Pieces) or on each side of a fold line (Inset Line), and the original geometry inside that band is replaced by new triangles. Larger = softer shading gradient. The inset runs on the flat layout, whose scale depends on how the UV is packed, so the value is converted before use — 1 mm is 1 mm of cloth either way. The report shows the band as a multiple of the outline's own vertex spacing; about 0.5x is the normal working point":
        "インセットの半幅（布の実寸）: 新しい頂点列は輪郭からこの距離だけ内側（Inset Pieces）または折れ線の両側（Inset Line）に置かれ、その帯の中にあった元のジオメトリは新しい三角形に置き換えられます。大きくするとシェーディングのグラデーションが緩やかになります。インセットは平面レイアウト上で走り、その倍率は UV の詰め方で変わるので、値は使用前に変換されます——どちらの詰め方でも 1mm は布の 1mm です。レポートには帯の幅が輪郭の頂点間隔の何倍かが出ます。0.5 倍前後が通常の動作点です",
    "Half-width of the topology-corner diamonds (flat-layout world units)":
        "トポロジー Corner のひし形マーカーの半幅（平面レイアウトのワールド単位）",
    "How see-through the Retopo Mesh's working material makes it, so the Preview Plane 5 mm underneath reads through the faces you are cutting. 0 = invisible, 1 = solid. The value is kept on the AC9_RetopoTransparent material itself, so it is saved in the .blend and nothing in Preferences is touched — unlike the Retopology overlay, whose transparency is a theme colour shared by every file. Solid and Material Preview read different properties for this (measured), and this slider writes both":
        "Retopo Mesh に付けた作業用マテリアルの透け具合。5mm 下の Preview Plane が、いま切っている面越しに読めるようになります。0 = 完全に透明、1 = 不透明。値は AC9_RetopoTransparent マテリアル自身が持つので .blend に保存され、プリファレンスには一切触れません（Retopology オーバーレイの透明度はテーマの色で、全ファイル共通になってしまいます）。Solid と Material Preview はこれに別々のプロパティを見る実測結果なので、このスライダーは両方に書き込みます",
    "How close a retopo boundary vertex must be to a Guide seam to count as sitting on it. Vertices further away are cuts through the middle of a panel and are left alone. In real fabric distance: the test runs in the flat layout, whose scale depends on the UV packing, so the value is converted before use":
        "retopo の境界頂点が Guide の縫い目上にあるとみなす距離。これより離れた頂点はパネル中央を貫くカットとして扱われ、手を付けません。単位は布の実寸で、判定は平面レイアウト上で走り、その倍率は UV の詰め方で変わるので、値は使用前に変換されます",
    "How close a retopo vertex must be to a ghost to count as BONDED (placed → green, and hidden by Only Unplaced). In the cloth's real dimensions, converted into the flat layout. This is a CLASSIFICATION threshold only — keep it small (a few mm) so 'placed' still means placed. The radius that actually pulls vertices, both for Ghost Snap on G and for Force Bond, is Snap Distance":
        "retopo 頂点が Ghost に BONDED（配置済み → 緑。Only Unplaced で隠れる）とみなされるための近さ。布の実寸で、平面レイアウトへ変換されます。これは**判定**のしきい値だけです — 「配置済み」が配置済みを意味するよう、小さめ（数 mm）に保ってください。実際に頂点を引き寄せる半径は、G の Ghost Snap も Force Bond も Snap Distance のほうです",
    "Pull the two sides of every sewn seam onto one shared 3D point, so the deliverable has no crack along its seams. Finalize projects each vertex onto the Guide on its own, and the Guide itself does not hold the two sides of a sewn seam at the same place — CLO leaves them apart — so without this the gap comes straight through into the Final. Positions only: no vertex is created, removed or merged, the UV islands stay split, and each vertex moves at most half the gap it was closing. Only pairs whose ghost is PLACED are touched; an unplaced (red) ghost has no counterpart to meet, so those vertices are left alone and counted in the report. Sewn seams only — a layered seam (a pocket marked onto a body panel) is a separate layer resting on the surface, not the same spot, so those are never pulled together":
        "縫い合わされた縫い目の両側を、同じ 3D 座標に引き寄せます。納品メッシュの縫い目に割れが出なくなります。Finalize は各頂点を個別に Guide へ射影しますが、Guide 自身が縫い目の両側を同じ位置に持っていない（CLO は離したまま縫う）ため、そのままだと隙間が Final にそのまま出ます。動かすのは位置だけで、頂点の作成・削除・マージは一切しません。UV アイランドも分かれたままで、各頂点が動く距離は閉じる隙間の半分までです。対象は Ghost が**配置済み**のペアだけです。未配置（赤）の Ghost には合わせる相手が居ないので、その頂点はそのまま残し、件数をレポートに出します。縫い合わせ縫い目のみが対象です——重ね縫い（ボディにマークしたポケット等）は表面に乗った別レイヤーであって同じ位置ではないので、引き寄せません",
    "Also merge each group of now-coincident seam vertices into a single vertex. OFF by default because the Final goes to baking next, and baking wants the two sides of a seam as separate vertices — splitting a welded mesh back apart per UV island is real work, while welding an unwelded one is a single Merge by Distance. So the Final ships unwelded and you weld when you actually want it. Needs Close Seam Gaps, which makes the members of a group exactly equal so the merge cannot pick up anything else":
        "同じ位置になった縫い目の頂点どうしを、1 個の頂点にマージします。既定は OFF です。Final はこの後ベイクへ回り、ベイクでは縫い目の両側が別々の頂点であってほしいからです。溶接済みのメッシュを UV アイランドごとに分け直すのは手間ですが、未溶接のものを溶接するのは Merge by Distance 一発で済みます。なので Final は未溶接で渡し、必要になったときに溶接します。Close Seam Gaps が前提です（グループの頂点が厳密に一致していないと、マージが無関係なものを巻き込みます）",
    "Ring the retopo vertex that OWNS each unplaced ghost — the vertex whose counterpart is missing. The ghost cross itself marks the empty spot on the PARTNER panel, which in a split flat layout is a whole panel away and off screen at working zoom, so without this nothing warns you at the vertex you are actually looking at. Sewn seams only — a free edge's foot sits on the vertex's own outline, close enough that a ring would just blur into the cross. The ring says 'the slot opposite this vertex is empty' — it does not say this vertex is the wrong one; which side ends up unpaired is decided by nearest-neighbour matching within Bond Distance":
        "未配置 Ghost を**持っている**側の retopo 頂点、つまり相方が居ない頂点にリングを描きます。Ghost のクロス自体は**対岸**パネルの空き位置を指していて、分割された平面レイアウトではパネル1枚ぶん離れており、作業中の拡大率では画面の外です。これが無いと、いま見ている頂点には何の警告も出ません。縫い目のみが対象です——自由端の足は頂点自身の輪郭上にあり、リングを描くとクロスと重なって潰れます。リングの意味は「この頂点の対岸の枠が空いている」であって、「この頂点が間違っている」ではありません。どちら側が余るかは Bond Distance 内の最近傍マッチングで決まります",
    "Draw the connector line only for unplaced ghosts — the ones that still need work. A placed ghost sits on a vertex that already exists, so its connector is pure confirmation, and on a finished panel those outnumber the unplaced ones by two orders of magnitude and bury them. Turn OFF to get a line for every ghost":
        "連結線を未配置 Ghost だけに描きます——まだ作業が要るものだけです。配置済み Ghost は既にある頂点の上に乗っているので、その連結線は確認にしかならず、仕上がったパネルでは未配置のものより2桁多くて埋もれさせます。OFF にすると全ての Ghost に線が引かれます",
    "How far (mm) the opening is spread across the surface around each contact. Wider keeps the drape smoother but moves more of it; narrower is more local but starts to show as bumps. About ten times the gap is a good default":
        "各接触点のまわりで、開きを表面上にどれだけ広げて分散させるか (mm)。広いほどドレープは滑らかなままですがより多くが動き、狭いほど局所的ですが凸凹として見え始めます。隙間の 10 倍くらいが良い既定値です",
    "How far (mm of fabric) a Guide vertex may sit from the retopo's 2D footprint and still count as covered. Outside this the residual map shows neutral dark gray. The test itself runs in the flat layout, whose scale depends on how the UV is packed, so this is converted before use — the same number means the same fabric distance whatever the packing":
        "Guide の頂点が retopo の 2D 占有範囲からどれだけ離れていても被覆済みとみなすか（布の実寸 mm）。これを超えると Residual Map は中立の濃いグレーになります。判定自体は平面レイアウト上で走り、その倍率は UV の詰め方で変わるので、値は使用前に変換されます——どの詰め方でも同じ数値が同じ布の距離を意味します",
    "How far the interior lattice keeps away from the outline, as a fraction of the fill spacing. Too small and the border quads come out as slivers":
        "内部の格子が輪郭からどれだけ離れるか、Fill 間隔に対する比率で指定します。小さすぎると境界のクワッドが細片になります",
    "How many times to subdivide (each level doubles edge resolution)":
        "Subdivide の回数（1 レベルごとに辺の解像度が 2 倍になります）",
    "Subdivision levels shared by the reversible Mirror preview and the destructive Subdivide operation":
        "可逆な Mirror プレビューと破壊的な Subdivide 操作で共有する分割レベル",
    "How many vertices per side, counting both anchors": "片側あたりの頂点数（両端の Anchor を含む）",
    "How many vertices to add (negative removes)": "追加する頂点数（負の値で削除）",
    "How much the four side curves are smoothed before the grid is mapped onto them. The boundary vertices never move — this only stops a zigzag hem from printing itself onto every interior row. 0 follows the outline exactly":
        "グリッドを写し込む前に、4 辺の曲線をどれだけ平滑化するか。外周頂点は動きません — ジグザグな裾がすべての内部列に写ってしまうのを防ぐだけです。0 なら輪郭にそのまま従います",
    "How strict the bilateral-symmetry test is, as a normalised RMS (point-to-outline reflection error at the best-fit axis, ÷ island size — the actual pass/fail cutoff is 0.4x this value, tightened from the raw setting so the number keeps its old meaning for anyone with a value already dialled in). Lower = stricter. ~0.015 cleanly separates cut-on-fold panels (back body, waistband, plackets, cuffs) from genuinely asymmetric pieces (sleeves, front-opening panels), which are left/right TWINS rather than self-symmetric. Raise it to catch panels with darts; lower it to reject borderline shapes":
        "左右対称性テストの厳しさ。正規化 RMS（最適軸での点-輪郭反転誤差 ÷ アイランドサイズ。実際の合否しきい値はこの値の 0.4 倍で、既に値を調整済みの人にとって数値の意味が変わらないよう生の設定より締めてあります）で指定します。小さいほど厳しくなります。~0.015 でわ裁ちの型紙（後身・ベルト・前立て・カフス）と、本当に非対称な型紙（袖・前開きの身頃、これらは自己対称ではなく左右の TWIN）をきれいに分離できます。ダーツのある型紙も拾いたいなら上げ、際どい形状を弾きたいなら下げてください",
    "How strict the twin (cross-island reflection) test is, as a normalised RMS (outline mismatch ÷ larger island's bbox diagonal) in the flat pattern layout. Lower = stricter. Not a delicate setting: measured on a production jacket, true pairs came in at 0.000-0.007 and the closest false candidate at 0.047, so anything in between gives the same answer. Companion to Symmetry Tolerance above, which tests one island against ITSELF for a fold line; this tests two DIFFERENT islands against each other":
        "Twin（アイランド間の反転）テストの厳しさ。平面型紙レイアウト上の正規化 RMS（輪郭の不一致 ÷ 大きい方のアイランドのバウンディングボックス対角）で指定します。小さいほど厳しくなります。繊細な設定ではありません: 実際のジャケットで測ると、真のペアは 0.000〜0.007、最も近い偽候補は 0.047 だったので、その間ならどの値でも同じ答えになります。上の Symmetry Tolerance と対になる設定で、あちらは 1 つのアイランドを「自分自身」と比べて折れ線を探し、こちらは「別々の」2 アイランドを互いに比べます",
    "If the BEST diagonal's fold drops below this, the quad is a simple bend — splitting along that diagonal makes it visually flat ('clean'). Above this it is a true saddle/twist where some fold remains no matter how you cut":
        "「最良の」対角線の折れがこれを下回るなら、そのクワッドは単純な曲がりで、その対角線で分割すれば見た目上平坦になります（クリーン）。これを超えると本当のサドル／ねじれで、どう切っても折れが残ります",
    "Include the detected fold (centre) lines as snap targets in Outline Snap, so the centre column of retopo verts can land cleanly on a cut-on-fold line. Fold lines sit at island centres, far from the outline, so this won't pull boundary verts off the pattern edge":
        "検出した折れ（中心）線も Outline Snap のスナップ対象に含め、retopo 頂点の中央列がわ裁ち線にきれいに載るようにします。折れ線はアイランドの中心にあって輪郭から遠いので、外周頂点が型紙の縁から引き剥がされることはありません",
    "Interior quad size for the preview fill, in mm of fabric (converted into the flat layout the retopo lives in). 0 follows the boundary Spacing above, which is what makes the preview honest: the interior then shows the density the boundary is asking for":
        "プレビュー充填の内部クワッドのサイズ。単位は布の実寸 mm（retopo が住む平面レイアウトへ変換されます）。0 にすると上の境界 Spacing に従い、それがプレビューを正直にします——内部が境界の要求している密度をそのまま見せることになります",
    "Live preview: draw the opposite-side ghost + connector ONLY for SELECTED BOUNDARY verts in Edit Mode. Interior verts have no seam partner so they're ignored; boundary verts farther than Max Seam Distance from any seam (free edges / hems) are skipped too. Shows ALL partners, so an N-way junction (folded hem / pocket / 3+ panels meeting) draws every counterpart, not just the nearest. Updates as you select/move":
        "ライブプレビュー: Edit Mode で「選択中の外周頂点」についてだけ、対岸の Ghost と接続線を描きます。内部頂点には Seam の相手がないので無視され、どの Seam からも Max Seam Distance より遠い外周頂点（フリー辺／裾）もスキップされます。「すべての」相手を表示するので、N 方向の合流点（折り返した裾／ポケット／3 枚以上の型紙が集まる箇所）では最近傍だけでなく全ての対応点を描きます。選択や移動に合わせて更新されます",
    "Mark the Guide's anchor points — where three or more panels meet, and where a sewn seam turns into a free edge. Anchors are what cut the boundary into spans, so this shows where one span ends and the next begins, which is the unit every density edit works in. Run 'Analyze Anchors' to fill it in":
        "Guide の Anchor 点をマークします — 3 枚以上の型紙が集まる箇所と、縫い Seam がフリー辺に変わる箇所です。Anchor は外周をスパンに切り分けるものなので、これはスパンの終わりと次の始まりを示します。スパンはあらゆる密度編集の作業単位です。'Analyze Anchors' を実行すると埋まります",
    "Master switch for ALL AC9 Cloth Retopo viewport overlays (seams, ghosts, parity text, boundary markers — both tools). Turn OFF to hide everything at once without touching the individual toggles. Also stops all per-frame overlay computation, so it lightens the viewport when things feel heavy":
        "AC9 Cloth Retopo の「すべての」ビューポート Overlay のマスタースイッチです（Seam・Ghost・頂点数のテキスト・Boundary マーカー、両ツール分）。OFF にすると個別のトグルを触らずに一度にすべて隠せます。フレームごとの Overlay 計算もすべて止まるので、重く感じるときにビューポートを軽くできます",
    "Max distance a new boundary vert may move to reach a seam line, in the cloth's real dimensions (converted into the flat layout)":
        "新しい外周頂点が Seam 線に届くために移動できる最大距離。布の実寸で、平面レイアウトへ変換されます",
    "Max projection distance for Mark Sharp seam edges. Edges marked Sharp on the GUIDE mesh (e.g. a pocket outline) are projected onto the nearest other panel within this distance and shown as seam pairs — the explicit way to register layered seams that 3D Match Distance cannot infer. Keep small (~1 cm): larger values project marked outlines onto unrelated panels that merely hang nearby in 3D (a sleeve cuff next to a pocket)":
        "Mark Sharp した Seam 辺の最大投影距離。GUIDE メッシュ上で Sharp にマークした辺（ポケットの輪郭など）は、この距離以内の最も近い別の型紙に投影され、Seam ペアとして表示されます — 3D Match Distance では推定できない重ね縫いを明示的に登録する方法です。小さく（~1 cm）保ってください: 大きいと、3D で近くに垂れているだけの無関係な型紙（ポケットの隣にある袖のカフスなど）にマーク済み輪郭を投影してしまいます",
    "Maximum 2D world-space distance from the Guide's pattern outline for a retopo vertex to be snapped during 'Align Boundary to Outline'. FlattenUV-to-ShapeKey normalises UVs into a 0..1 m grid, so a few millimetres is typical. Too large risks classifying interior verts as boundary":
        "'Align Boundary to Outline' で retopo 頂点がスナップされる、Guide の型紙輪郭からの2D ワールド空間距離の上限。FlattenUV-to-ShapeKey は UV を 0..1 m のグリッドに正規化するので、数ミリが典型的です。大きすぎると内部頂点を boundary と誤判定する危険があります",
    "Maximum 3D (Basis) distance for two boundary edges to count as a sewn pair. Keep this small (1-3 mm) — it is for near-coincident split seams only. For layered seams (a pocket sewn onto a body panel, gap varies with drape) use Mark Sharp on the guide instead. 0 = exact position matching — the right setting when CLO exported split seams perfectly coincident (typical); raising it can pick up folded ribs/hems whose two layers nearly touch":
        "2 つの外周辺が縫いペアとみなされる 3D（Basis）距離の上限。小さく（1〜3 mm）保ってください — ほぼ重なった分割 Seam 専用です。重ね縫い（身頃に縫い付けたポケットなど、隙間がドレープで変わるもの）には代わりに Guide 上の Mark Sharp を使ってください。0 = 位置の完全一致 — CLO が分割 Seam を完全に重なった状態で書き出した場合（典型的）に正しい設定です。上げると、2 層がほぼ触れている折り返しのリブや裾を拾うことがあります",
    "Maximum distance, in the cloth's real dimensions, for a retopo vertex to be treated as sitting on a seam and given a ghost (converted into the flat layout the ghosts are drawn in)":
        "retopo 頂点が Seam 上にあるとみなされ Ghost が与えられる距離の上限。布の実寸で、Ghost を描く平面レイアウトへ変換されます",
    "Maximum distance, in the cloth's real dimensions, for a moving vertex to snap to a ghost — this MOVES vertices, both for Ghost Snap on G and for Force Bond. Converted into the flat layout. Enable 'Show Snap Radius' to visualize this as circles around each ghost":
        "移動中の頂点が Ghost にスナップする距離の上限。布の実寸で、平面レイアウトへ変換されます。**これは頂点を実際に動かします**（G の Ghost Snap と Force Bond の両方）。'Show Snap Radius' を有効にすると、各 Ghost のまわりの円として可視化できます",
    "Minimum distance (mm) to open between layers of the Guide that touch each other. Set it above the cage / ray distance you bake with; a few millimetres suits garment-scale folds. The Solidify thickness is added automatically when 'Include Solidify' is on":
        "互いに触れている Guide の層の間に開ける最小距離 (mm)。ベイクに使うケージ／レイ距離より大きく設定してください。衣装スケールの折れなら数ミリが適当です。'Include Solidify' が ON なら Solidify の厚みが自動的に加算されます",
    "Move new boundary verts onto the nearest Guide seam line so the CLO outline stays crisp on curves. Only midpoints born on an edge whose both ends already sit on the seam are snapped — boundaries without a seam of their own (e.g. an island's symmetry centre-line) are never touched. Requires 'Analyze Seams' to have run":
        "新しい外周頂点を最も近い Guide の Seam 線に載せ、CLO の輪郭が曲線でも鮮明に保たれるようにします。スナップされるのは、両端がすでに Seam 上にある辺で生まれた中点だけです — 自分の Seam を持たない外周（アイランドの対称中心線など）には触りません。'Analyze Seams' の実行済みが前提です",
    "Name of the ShapeKey that represents the UV-flattened (2D) layout. Example: 'UV_Map_Flattened'":
        "UV 平面化（2D）レイアウトを表す ShapeKey の名前。例: 'UV_Map_Flattened'",
    "Off: measure and colour only. On: build the AC9_Separated ShapeKey":
        "OFF: 測定と色付けのみ。ON: AC9_Separated ShapeKey を作成します",
    "Operate only on currently selected quads (the workflow: select non-planar faces in your checker, then fix). When off, scan every quad in the mesh":
        "現在選択中のクワッドだけを対象にします（想定の流れ: チェッカーで非平面の面を選択 → 修正）。OFF ならメッシュ内のすべてのクワッドを走査します",
    "Pin a vertex wherever the pattern outline turns at least this much, so a right-angled hem stays square instead of being cut across. Measured in the flat layout: 0 is straight, 90 a right angle. Set to 0 to divide purely by spacing":
        "型紙の輪郭がこれ以上曲がる場所に頂点を Pin し、直角の裾が横切って切られる代わりに直角のまま保たれるようにします。平面レイアウト上で測ります: 0 が直線、90 が直角です。0 にすると間隔だけで分割します",
    "Pixel size of the selection-correspondence markers": "選択対応マーカーのピクセルサイズ",
    "Plane-fit deviation (mm) mapped to pure white/black in the sag map. Mid gray = on the panel's best-fit plane":
        "Sag Map で純白／純黒にマップされる平面フィットからのずれ (mm)。中間グレー = 型紙の最適フィット平面上",
    "Project edges marked Sharp on the GUIDE mesh onto the nearest other panel and show them as seam pairs. Mark a pocket outline with Mark Sharp to register layered seams explicitly. Turn OFF if the guide came in with stray Sharp edges (some importers mark hard edges) and unexpected guides appear. The Sharp rows CLO Cleanup's insets build are ignored":
        "GUIDE メッシュ上で Sharp にマークした辺を最も近い別の型紙に投影し、Seam ペアとして表示します。ポケットの輪郭を Mark Sharp すれば重ね縫いを明示的に登録できます。Guide に余計な Sharp 辺が付いた状態で読み込まれ（ハードエッジをマークするインポーターもあります）意図しないガイドが現れる場合は OFF にしてください。CLO Cleanup のインセットが作る Sharp 行は無視します",
    "Read the thickness of the Guide's Solidify modifier and keep the shell it adds clear of the neighbouring layers too. Turn off when you bake without Solidify":
        "Guide の Solidify モディファイアの厚みを読み、それが加えるシェルも隣の層から離れた状態に保ちます。Solidify なしでベイクする場合は OFF にしてください",
    "Rebuild the selected span so its vertices land roughly this far apart. The count follows from the span's 3D length":
        "選択したスパンの頂点がおよそこの間隔で並ぶよう作り直します。頂点数はスパンの 3D 長さから決まります",
    "Recompute the full ghost field automatically after a retopo edit is confirmed (e.g. you finish a move). Saves running Refresh Ghosts by hand. Updates on confirm, not continuously mid-drag":
        "retopo の編集が確定した後（移動を終えたときなど）、Ghost の場を自動で全再計算します。Refresh Ghosts を手動で実行する手間が省けます。更新は確定時で、ドラッグ中に連続更新はしません",
    "Replace 'AC9_3D_Project' if it already exists on the retopo object":
        "retopo オブジェクトに 'AC9_3D_Project' が既にある場合は置き換えます",
    "Reset the AC9_Project_Failed vertex group before populating it":
        "頂点グループ AC9_Project_Failed を埋める前にリセットします",
    "Roughly how far apart the generated vertices should sit, in mm of fabric measured along the garment's 3D surface. The interior fills convert it into the flat layout so both end up at the same fabric spacing whatever the UV packing":
        "生成する頂点のおおよその間隔。単位は布の実寸 mm で、服の3D表面に沿って測ります。内部の充填側はこれを平面レイアウトへ変換するので、UV の詰め方に関わらず境界と内部が同じ布の間隔になります",
    "0 (the default) divides straight runs by Spacing like everything else. Raised, a stretch of the outline that stays within this distance of a straight line (both sides of a seam) is left undivided, so straight runs carry only their corners while curves still get the Spacing. In mm of fabric: the straightness test runs on the flat layout, whose scale depends on the UV packing, so the value is converted before use":
        "既定の 0 では直線区間も他と同じように間隔で分割します。上げると、輪郭がこの距離以内で直線とみなせる区間（縫い目なら両側とも）は分割されず、直線区間は角だけ、カーブは従来どおり間隔で分割されます。単位は布の実寸 mm で、直線判定は平面レイアウト上で走るため、値は使用前に UV の詰め方に応じて変換されます",
    "Saddles can't be made flat by a single cut. When ON they are still split along their least-bad diagonal (least visible). When OFF only the cleanly-fixable simple folds are split, and saddles are left for you to inspect/handle by hand":
        "サドルは 1 回のカットでは平坦にできません。ON なら、それでも最もましな（最も目立たない）対角線で分割します。OFF ならきれいに直せる単純な折れだけを分割し、サドルは手で確認／対処できるよう残します",
    "Select the retopo vertices on any island that failed to match a Guide island, so they can be found in the viewport":
        "Guide のアイランドと対応が付かなかったアイランドの retopo 頂点を選択し、ビューポートで見つけられるようにします",
    "Select the retopo vertices on every seam that is mismatched or authored on one side only, and on every free-edge run that has drifted off the outline, so they can be found in the viewport without relying on the overlay":
        "食い違っている Seam・片側だけ作られている Seam、および輪郭からずれたフリー辺のランすべての retopo 頂点を選択し、Overlay に頼らずビューポートで見つけられるようにします",
    "Select unprojected vertices in Object Mode. Switch to Edit Mode afterwards to see them highlighted":
        "投影されなかった頂点を Object Mode で選択します。その後 Edit Mode に切り替えるとハイライトされて見えます",
    "Show only ghosts whose partner vertex isn't placed yet (the gaps you still need to fill). Hides 'placed' ghosts that already sit on an existing retopo vert. A vert counts as placed when it's within Bond Distance of the ghost. Turn OFF to also see placed ghosts (drawn in the Placed colour) for a full picture":
        "相手の頂点がまだ配置されていない Ghost（まだ埋める必要がある隙間）だけを表示します。既存の retopo 頂点の上にすでに載っている「配置済み」の Ghost は隠します。頂点は Ghost から Bond Distance 以内なら配置済みと数えられます。全体像を見るために配置済みの Ghost（配置済みの色で描かれます）も表示したい場合は OFF にしてください",
    "Show the corners you pinned the edge flow to, as diamonds. These are yours — unlike anchors, nothing recomputes them":
        "エッジフローを固定した Corner をひし形で表示します。これらは作業者のもので、Anchor と違い自動再計算されることはありません",
    "Show the legacy in-place 3D editing tools (Sync 2D>3D / Sync 3D>2D / Bind New Verts). The recommended workflow is to edit the 2D retopo and press 'Refresh Mirror' instead — these tools are kept for advanced 3D move-only tweaks and may shift vertices":
        "旧来のその場 3D 編集ツール（Sync 2D>3D / Sync 3D>2D / Bind New Verts）を表示します。推奨の流れは 2D retopo を編集して 'Refresh Mirror' を押すことです — これらのツールは上級者向けの 3D 移動のみの微調整用に残されており、頂点をずらすことがあります",
    "Show the vertices pinned against Adjust Density, as squares. A pin (e.g. a knife-cut vertex added to follow a fabric wrinkle) survives a Count/Spacing change or a Step that would otherwise dissolve or slide it":
        "Adjust Density に対して Pin された頂点を四角で表示します。Pin（生地のしわに沿わせるためナイフカットで足した頂点など）は、本来なら溶けたり滑ったりする Count/Spacing の変更や Step を生き延びます",
    "Print a line to the console whenever a tool of this add-on takes a second or more without showing progress. For developing the add-on: it names the tools that need a progress bar":
        "このアドオンのツールが、進捗を出さずに1秒以上かかったときにコンソールへ1行出力します。アドオン開発用で、進捗表示が必要なツールを教えてくれます",
    "Show unfinished tools: Grid Regions, Align to Outline, Mesh Edit, Quad Fix, Legacy Flip, and the Density family (Density, Even Out, Count, Spacing, Pins, Corners)":
        "未完成のツールを表示します: Grid Regions, Align to Outline, Mesh Edit, Quad Fix, Legacy Flip, および Density 一族（Density, Even Out, Count, Spacing, Pin, Corner）",
    "Size of the baked map images (square)": "ベイクするマップ画像のサイズ（正方形）",
    "Skip seams shorter than this. Pattern corners produce a lot of millimetre-long seams that are not worth dividing":
        "これより短い Seam はスキップします。型紙の角はミリ単位の Seam を大量に作りますが、分割する価値はありません",
    "Split along the OTHER (non-convex) diagonal instead. For previewing the 'wrong' cut: run Fix, orbit, Undo, run this, orbit, compare":
        "代わりに「もう一方の」（凸でない）対角線で分割します。「間違った」カットのプレビュー用です: Fix を実行して視点を回し、Undo し、これを実行して視点を回し、比べます",
    "Start from scratch instead of adding to what is marked":
        "マーク済みのものに追加するのではなく、ゼロから始めます",
    "The low-poly retopo mesh you are building. Every tool in this tab works on it":
        "作成中のローポリ retopo メッシュ。このタブのすべてのツールがこれを対象に動きます",
    "Toggle ghost cross marker visibility": "Ghost の十字マーカーの表示を切り替えます",
    "Transparency of the baked island colours in Material Preview mode. 1.0 = fully opaque · lower values let you see through the Guide to the Retopo. Drag the slider — the material updates live without re-baking.":
        "Material Preview モードでのベイク済みアイランド色の透明度。1.0 = 完全に不透明 · 値を下げると Guide の向こうの Retopo が透けて見えます。スライダーをドラッグしてください — ベイクし直さなくてもマテリアルが即座に更新されます。",
    "Triangulate the Guide before flattening. CLO Projector requires an all-triangle Guide (validate_guide rejects any n-gon or quad), so this is on by default — turn it off only if the Guide is already triangulated and you want to keep its exact topology":
        "平面化の前に Guide を三角化します。CLO Projector は全三角形の Guide を要求するため（validate_guide は n-gon やクワッドを拒否します）既定で ON です — Guide が既に三角化済みで、そのトポロジーを正確に保ちたい場合だけ OFF にしてください",
    "Triangulated garment mesh carrying both the 3D shape (Basis ShapeKey) and the UV-flat layout (the ShapeKey named in 'Flat SK'). Every analysis reads it; nothing writes to it":
        "3D 形状（Basis ShapeKey）と UV 平面レイアウト（'Flat SK' に指定した ShapeKey）の両方を持つ三角形化された衣装メッシュ。すべての解析がこれを読み、何も書き込みません",
    "Upper bound on push-and-smooth rounds. The pass stops early when every contact has the gap, or when the unresolved count stops improving (a Solidify thicker than the layer spacing)":
        "押し出しと平滑化を繰り返す回数の上限。すべての接触が所定の隙間を得た時点、または未解決数の改善が止まった時点（層の間隔より厚い Solidify）で早期終了します",
    "UV layer to flatten. Empty uses the Guide's active UV layer":
        "平面化する UV レイヤー。空なら Guide のアクティブな UV レイヤーを使います",
    "Vertex count to give the selected span, both anchors included. Both sides of a sewn seam are set to the same number":
        "選択したスパンに与える頂点数（両端の Anchor を含む）。縫い Seam の両側が同じ数に設定されます",
    "When leaving Edit Mode in the 3D state, automatically bind any vertices that were just created (no stored attachment) to the Guide surface and repair their 2D Basis position. Prevents new verts from flying away or fusing with the back face. Turn off to bind manually with 'Bind New 3D Verts'":
        "3D 状態で Edit Mode を抜けるとき、直前に作られた頂点（アタッチメント未保存のもの）を自動的に Guide 表面へバインドし、2D Basis 位置を修復します。新しい頂点が飛んでいったり裏面に融合するのを防ぎます。'Bind New 3D Verts' で手動バインドしたい場合は OFF にしてください",
    "When ON, pressing G in Edit Mode activates Ghost Snap Move — vertices snap only to ghost points":
        "ON にすると、Edit Mode で G を押したとき Ghost Snap Move が起動します — 頂点は Ghost 点にだけスナップします",
    "When ON, pressing G in Edit Mode activates Outline Snap Move — vertices snap to the nearest point on the FULL Guide pattern outline: sewn seams AND free edges (hems, necklines, openings), not just sewn seams. Use it to drop boundary verts cleanly onto the pattern outline during initial placement":
        "ON にすると、Edit Mode で G を押したとき Outline Snap Move が起動します — 頂点は Guide の型紙輪郭「全体」の最近点にスナップします: 縫い Seam「と」フリー辺（裾・襟ぐり・開き）で、縫い Seam だけではありません。最初の配置で外周頂点を型紙の輪郭にきれいに落とすのに使います",
    "Run the Guide Maps bakes on the GPU set up in Preferences > System, instead of whatever device the .blend happens to carry. A scene that has never rendered with Cycles carries CPU, and a 2048 Drape bake then freezes the interface for a minute or more (measured: 67.6 s against 12.4 s on the GPU). Turn this off if a bake fails for lack of VRAM — it will then use the scene's own device, which is what earlier versions always did":
        "Guide Maps のベイクを、プリファレンス > システム で設定した GPU で実行する（.blend が持っているデバイスではなく）。Cycles で一度もレンダリングしていないシーンは CPU を持っているため、2048 の Drape ベイクが UI を 1 分以上固める（実測: 67.6 秒 に対し GPU では 12.4 秒）。VRAM 不足でベイクが失敗する場合はオフにする（シーン自身のデバイスを使う＝以前のバージョンと同じ挙動になる）",
    "Pack this map's pixels into the .blend so it is still there after reopening the file. The pack is a 16-bit PNG, not the raw float buffer, so it costs about 3 MB per 2K map instead of 50 (measured), for a quantisation of 7.7e-06 on a map that is looked at rather than measured. Off = session-only: the image is dropped on load and the map has to be re-baked (the whole drape set re-bakes in under 5 s at 2K on a 283k-vert Guide)":
        "このマップの画素を .blend にパックして、ファイルを開き直しても残るようにする。パックは生の float バッファではなく 16bit PNG なので、2K 1 枚あたり約 3 MB（生 float なら 50 MB、実測）。量子化誤差は 7.7e-06 で、測るのではなく見るためのマップなので問題にならない。オフ = セッション限り: 読み込み時に画像は捨てられ、マップは再ベイクが必要（283k 頂点の Guide なら 2K の Drape 一式が 5 秒未満で焼き直せる）",
    "Keep the AO and Curvature passes as images of their own after the Drape map has been built from them, so each can be looked at on its own in Solid. They are deleted by default: nothing re-reads them (pressing Bake always re-bakes both passes), and the pair costs 128 MiB of RAM per garment at 2K (measured). They are session-only either way":
        "Drape Map を作った後も AO と Curvature のパスを個別の画像として残し、Solid でそれぞれ単体で確認できるようにする。既定では削除する: 後から読むものが無く（Bake を押すと常に両パスを焼き直す）、2K なら 1 衣装あたり 128 MiB の RAM を占めるため（実測）。どちらにしてもセッション限り",
    # The other two delete operators have multi-line docstrings, and Blender
    # keys a tooltip on the raw docstring — so, as everywhere else in this
    # catalogue, only the single-line one is translatable.
    "Delete every baked map belonging to this Guide":
        "この Guide に属するベイク済みマップをすべて削除する",
    "Which baked map of the current Guide the Preview Plane displays":
        "現在の Guide のどのベイク済みマップをプレビュー平面に表示するか",
    "Which fold axis Self mirrors across. A panel can be symmetric both ways (a waistband: left-right AND top-bottom), and only one of them is the copy you meant":
        "Self がどの折り軸で反転するか。型紙は両方向に対称なことがあり（ベルトなら左右「かつ」上下）、そのうち一方だけが意図したコピーです",
    "Which fold axis to mirror across. A panel can be symmetric both ways (a waistband: left-right AND top-bottom), and only one of them is the copy you meant":
        "どの折り軸で反転するか。型紙は両方向に対称なことがあり（ベルトなら左右「かつ」上下）、そのうち一方だけが意図したコピーです",
    "Which parts of the pattern outline to lay down": "型紙の輪郭のどの部分を敷くか",
    "Which seams without any retopo to lay down": "retopo のない Seam のどれを敷くか",
    "Which shape the projector and the maps read as the Guide's 3D shape":
        "プロジェクタとマップが Guide の 3D 形状としてどの形状を読むか",
    "While SELECTED seam (boundary) verts are highlighted in Edit Mode, show the vertex count on this side (A) and on the partner side (B) as text at each seam location. Green when they match, orange + '≠' when they don't — so you instantly know how many verts to add or remove for a clean weld. B counts the retopo verts already placed near the partner seam (not the guide ghosts)":
        "Edit Mode で Seam（外周）頂点が選択・ハイライトされている間、こちら側 (A) と相手側 (B) の頂点数を各 Seam の位置にテキストで表示します。一致すれば緑、しなければオレンジと '≠' が付くので、きれいに溶接するには何頂点足す／減らすべきかがすぐ分かります。B は相手の Seam 付近にすでに配置された retopo 頂点を数えます（Guide の Ghost ではありません）",
    "Z height of the seam/ghost overlay above the flat layout plane":
        "平面レイアウト面から上への Seam/Ghost Overlay の Z 高さ",
}


def _build() -> dict:
    """Flatten the two tables into Blender's {(msgctxt, msgid): msgstr} form."""
    out = {}
    for en, ja in _OPERATOR_LABELS.items():
        out[("Operator", en)] = ja
        out[("*", en)] = ja
    for en, ja in _UI_STRINGS.items():
        # An operator label reused as a button text is already covered above.
        out.setdefault(("*", en), ja)
    return out


translations_dict = {"ja_JP": _build()}


def register():
    """Register the catalogue, tolerating a stale registration.

    Reload Scripts re-imports this module without calling unregister(), so the
    previous registration can still be live on the C side under the same module
    name; drop it first, then register.
    """
    try:
        bpy.app.translations.unregister(__name__)
    except Exception:
        pass
    try:
        bpy.app.translations.register(__name__, translations_dict)
    except Exception as exc:      # never let a translation break the add-on
        print("[AC9] translations not registered: %r" % (exc,))


def unregister():
    try:
        bpy.app.translations.unregister(__name__)
    except Exception:
        pass
