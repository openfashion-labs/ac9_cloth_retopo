# AC9 Cloth Retopo — UI/UX 全体再設計案

作成: 2026-09-02
対象: `ac9_cloth_retopo/` 全6モジュール + ルート `__init__.py` / `overlays.py`
性質: **設計提案のみ。コードは未変更。**
根拠: 各モジュールの `ui.py` / `properties.py` / `operators.py`（bl_label・poll）、ルートの `__init__.py` / `overlays.py`、
`REDESIGN_uv_seam_guide.md`、`../ミラーペア機能設計.md`「UIパネル整理」節、`../引き継ぎ_2Dバウンダリ生成.md` A節・B-15節、
`../機能ドキュメント/*.md` をソース読みで確認。**Blender実機でのパネル表示は未確認**（本文中の数値はソースからの数え上げ）。

---

## 0. 結論（先に要点だけ）

「ごちゃごちゃ」の実体は、**ボタンの数**ではなく次の3つの構造欠陥である。

1. **分類軸が3つ同居している。** タブの中で「工程順（1.〜5.）」「モジュール別（3D Projection / Guide Maps / Mesh Merge…）」「関心別（Overlays）」「頻度別（ヘッダーに昇格した3D Mirror）」が混在し、しかも番号が実際の作業順序と一致していない。
2. **「機能追加 = ボックス1個追加」というテンプレートに歯止めがない。** 各ボックスが「見出し / 説明文 / 設定値 / ボタン / 結果文字列 / 注意文」を自前で持つため、パネル2（2D Boundary）は8ボックス・約19本の説明文・15個のインライン設定値に膨れた。**説明文が多いのは構造が意味を伝えていない証拠**であり、これ自体が症状。
3. **ユーザーの「今の状態」（Object Mode か Edit Mode か）をUIが構造として使っていない。** オペレータの `poll` は厳密にモードを区別しているのに、パネルは両モードの操作を全部並べ、グレーアウトと散在する「Enter/Exit Edit Mode…」ラベルで補っている。

前回（2026-08-31 A節、2026-09-01 UIパネル整理）の再設計が効かなかった理由は、**同じ枠の中でボックスを並べ替えたから**である。整理直後にAdjust Density / Density Pin / Sync Selected / Topology Corner / Preview Fill / Symmetrize / Replace が同じテンプレートで追加され、元に戻った。**必要なのはレイアウトではなく、「新しい機能はどこに・どの形で置くか」を決める文法（ルール）**である。本提案の中身はその文法と、それを適用した具体構成の2つ。

提案の骨子:

- 分類軸を **「工程」1本**に固定し、モジュール境界をUIから消す（Setup / Boundary / Faces / 3D View / Guide Maps / Overlays / Advanced）
- 各工程パネルは **Object Mode と Edit Mode で描く内容を切り替える**（一括操作 vs 選択操作）
- **パネルは動詞の列。設定は子パネル、説明はツールチップ、結果は1行。**
- 同じ意味の操作は同じ形（Check/Apply、Mark/Clear、Refresh/Clear、Analyze/Clear）を共通ヘルパーで描く
- 1コントロール1箇所（例外はビューポートヘッダーの常設2つのみ）
- 語彙を固定し、`bl_label` とパネル上の表示文字列を一致させる

---

## 1. 現状の棚卸し

### 1.1 現在のタブ構成（ソースから再構成）

```
[3Dビューポートヘッダー]  [👁 Overlays] [Refresh 3D]        ← __init__._draw_view3d_header_extras

AC9 Cloth Retopo タブ
├─ AC9 Cloth Retopo（ヘッダー, order 0, 常時開）
│    Overlays: ON/OFF（大ボタン）
│    Retopo Mesh / Guide / Flat SK
│    Guide: Show in 3D ↗ / Back to 2D ↩   （条件表示）
│    View: Mirror → / ← View: Guide       （条件表示）
│    ┌ 3D Mirror (read-only) ┐ Refresh / Auto-Refresh / Show 3D Mirror / Remove / 説明1行
├─ 1. Seam Analysis（order 1, 常時開）
│    ┌Analyze Seams┐ Analyze / Clear
│    ┌Opposite Ghosts┐ Refresh Ghosts / Clear / Force Bond Selected / 説明
│    ┌Analyze Guide (Symmetry)┐ 説明 / Analyze / Clear / ミラーペア一覧(N行) / 不合格理由 /
│                             Replace Partner Island / Symmetrize Island / 説明
│    Stats（Seam Pairs / Ghost Points / Fold Lines / Mirror Pairs）
│    └─ Analysis Settings（子, 閉）9プロパティ
├─ 2. 2D Boundary（order 2, 常時開）
│    ┌Generate┐ 6プロパティ + Generate
│    ┌Sync Counts┐ 説明 / Check / Sync / tol / stats / 説明 / 説明 / Check Selected / Sync+Pin Selected / stats
│    ┌Topology Corner┐ 説明 / deg / Detect Candidates / Mark / Clear / Clear All / stats
│    ┌Preview Fill┐ 説明 / 2プロパティ / Fill / Clear / stats / 説明
│    ┌Patch Grid┐ 説明 / Fill Regions / Clear / 3プロパティ / Grid Regions / stats / 説明
│    ┌Adjust Density┐ 説明 / −1 +1 / 説明 / Count+Set / Spacing+Apply / 説明 / stats / Even Out / 説明
│                    Density Pins: 説明 / Mark / Clear / Clear All / stats / 警告
│    ┌Status┐ Seam Status / stats / 説明 / 説明
│    ┌Snap┐ Ghost Snap Mode / UV Seam Snap Mode / distance / (snap_to_fold) / Move / 説明
├─ 3. 2D Mesh（order 3, 閉, 親のみ。子=モジュール）
│    ├─ Mesh Merge（子, 閉）flat_merge 全部: 2ピッカー + 8プロパティ + Merge + Select Holes
│    ├─ Collapse (Keep UV)（子, 閉）mesh_edit
│    └─ Quad Fix (Non-planar)（子, 閉）quad_fix
├─ 4. 3D Projection（order 4, 常時開）clo_projector の残り
│    ┌Experimental: 3D edit → 2D┐(折り畳み) Apply 3D Edits→2D / Legacy: Sync 2D>3D / Sync 3D>2D / Auto Bind / Bind New
│    ┌Subdivision┐ Subdivide Retopo / 説明 / Preview Subdiv / levels / smooth / 説明
│    ┌Boundary Alignment┐ 説明 / threshold / Align
│    (island jump 警告)
│    ┌Island Colors┐ alpha / Bake / Clear / 説明
│    ┌Options┐ 3 bool
│    ┌Maintenance┐ Clear Guide Cache / Clear Stored Attachments
├─ 5. Guide Maps（order 5, 閉）bake_maps
└─ Overlays（order 9, 閉）
     Overlays: ON/OFF（大ボタン、3箇所目）/ 警告ボックス / Analyze Seams + Anchors ボタン /
     プリセット4 / ┌Seam Lines┐5 ┌Anchors┐3 ┌Ghosts┐5 ┌Boundary/Status┐3 ┌3D Mirror┐1
     └─ Appearance（子, 閉）
```

### 1.2 数え上げ（ソース読み・概算）

| 指標 | 現状 |
|---|---|
| トップレベルパネル | 7（+子パネル5） |
| ボックス（`layout.box()`）総数 | 約30。うちパネル2に8、パネル4に6 |
| 説明文ラベル（INFO/CHECKMARK/ERROR/EVENT_G/EDITMODE_HLT アイコン付き、stats除く） | 約40。うちパネル2に約19 |
| パネル2にインライン表示される設定値 | 15（gen_* 6, retopo_sync_tol, topo_corner_detect_deg, fill_* 2, grid_* 3, dens_* 2） |
| 「最後の結果」用 StringProperty | 9（seam）+1（mesh_edit）+1（quad_fix）+2（maps）+ シーンカスタムプロパティ5（Stats）+ objカスタムプロパティ1（flat_merge） |
| Overlays マスタースイッチの出現箇所 | 3（ヘッダーパネル / ビューポートヘッダー / Overlaysパネル） |
| Refresh 3D Mirror の出現箇所 | 2 |
| Analyze Seams の出現箇所 | 2（パネル1 / Overlays） |
| Analyze Anchors の出現箇所 | 1 — **Overlaysパネルのみ**（解析操作が表示パネルにしか無い） |
| 同じ縫い目を二重描画するオーバーレイ | 2（シアン=Seam Guides / 白=UV Seam Lines、B節で既知） |

### 1.3 各モジュールがUIに置いているもの（役割の実態）

| モジュール | パネル上の居場所 | 実態 |
|---|---|---|
| `uv_seam_guide` | パネル1・2の全部 + Overlaysの14トグル中13 + Appearance大半 | 中心。解析（Guide読み）と境界編集（Retopo書き）が混在 |
| `clo_projector` | ヘッダーの3D Mirrorブロック・Guide 2D/3D切替 + パネル4 + Overlays 3トグル | 主機能（Refresh Mirror）がヘッダーへ移動済みで、パネル4は残り物置き場になっている |
| `flat_merge` | 3. 2D Mesh › Mesh Merge | **別パイプライン**（ドレープ帯+グリッド合体）。ヘッダーのRetopo/Guideを使わず独自ピッカー2つ |
| `mesh_edit` | 3. 2D Mesh › Collapse | 汎用。Cloth専用でない（CLAUDE.md明記） |
| `quad_fix` | 3. 2D Mesh › Quad Fix | Edit Mode専用の修正補助 |
| `bake_maps` | 5. Guide Maps | 診断用ベイク。工程のどこでも使う |
| `overlays.py` | Overlaysパネル + Appearance | 関心別。プリセットがSelection Linkを落とす（B-15、未対策） |

---

## 2. 問題点の言語化

### 2.1 分類軸の混在（最上位の欠陥）

| パネル | 実際の分類軸 |
|---|---|
| ヘッダー | 「共有入力」＋「使用頻度が高いので昇格したもの（3D Mirror）」 |
| 1. Seam Analysis / 2. 2D Boundary | 工程 |
| 3. 2D Mesh | モジュール容器に工程番号を貼ったもの |
| 4. 3D Projection | モジュール（clo_projectorの残り） |
| 5. Guide Maps | モジュール |
| Overlays | 関心（何を描くか） |

その結果、**番号「1〜5」が示す順序は実作業と一致しない**。実際のループは

```
Analyze → Generate → (Preview Fill → 見る → Adjust Density → Preview Fill)* → ナイフカット
→ Sync Selected(+Pin) → Refresh Mirror → 3Dで確認 → Fill Regions → Subdivide → …
```

であり、3D Mirror（パネル4相当）は工程の最後ではなく**全工程で常に**使う。Guide Maps も任意時点の診断。「3. 2D Mesh」のMesh Mergeは Generate系ワークフローとは別の入口である。番号は「順番に押せ」と読めるので、**誤った導線を与えている**。

### 2.2 ボックス・テンプレートの無制限増殖

パネル2の8ボックスはいずれも「見出し / 説明 / 設定 / ボタン / stats / 注意」の同型で、**それぞれが小さなパネルとして自己完結している**。結果:

- 説明文がパネルの半分を占める（約19本）。「Step is a local edit — never deletes a face.」「Even Out only moves vertices — never deletes a face.」「Insert-only: nothing moves or is deleted.」「Boundary is never moved.」は**同じ保証を4回別の言葉で**書いている
- 設定値がボタンの間に散らばり、「押す」列と「合わせる」列が視覚的に分離しない
- stats文字列が9個あり、それぞれ別のボックスに出る。**どれが最新の結果か分からない**
- Density Pins が Adjust Density ボックスの中に `separator` で入っており、階層が2段目に潜っている

### 2.3 モードを構造に使っていない

| 必要モード | オペレータ |
|---|---|
| Object | Generate Seam Chain / Sync Retopo Seams / Seam Status / Preview Fill / Fill Regions / Grid Regions / Subdivide Retopo / Flat Merge / Bake Residual・Sag |
| Edit | Resample Span Density(−1/+1/Set/Apply) / Even Out / Mark・Clear Corner / Mark・Clear Pin / Force Bond / Ghost Snap / UV Seam Snap / Collapse (Keep UV) / Fix Non-planar Quads |
| どちらでも | Refresh 3D Mirror / Replace Partner Island / Symmetrize Island / Sync Selected Vertex / Analyze系 |

パネル2はこの3種を1つの縦列に混ぜている。Edit Mode中は Generate / Sync / Status / Preview Fill / Fill Regions がグレーアウトし、Object Mode中は Adjust Density 一式がグレーアウトする。**常にパネルの半分近くが押せない状態で表示されている**。それを補うために「Select boundary edges in Edit Mode.」「Enter Edit Mode to mark by hand.」「Enter Edit Mode to pin by hand.」「Exit Edit Mode to merge.」「Enter Edit Mode to use.」と**言い回しの違う案内が6箇所**にある。

### 2.4 同じ意味の操作が違う形をしている

| 意味 | 現在の表現（バラバラ） |
|---|---|
| 実行前に結果だけ見る（dry run） | 別ボタン「Check Retopo Seams」／「Check Selected」／ Status は常にレポートのみ／ Preview Subdiv はトグル／ Quad Fix は「Fix→回して→Undo→Alternate」というA/B手順 |
| ユーザーの印を付ける・消す | Corner: Detect Candidates / Mark Corner / Clear / Clear All ／ Pin: Mark Pin / Clear / Clear All（Detect無し）／ Anchor: Analyze（印ではなく解析、しかしOverlaysで同列） |
| 派生データを作り直す・消す | Analyze / Clear ／ Refresh Ghosts / Clear ／ Preview Fill / Clear ／ Fill Regions / Clear ／ Bake / Clear ／ Preview Subdiv トグル（Removeに文言が変わる）／ Refresh 3D Mirror / Remove 3D Mirror |
| 設定値の置き場 | パネル1: 子パネル「Analysis Settings」／ パネル2: 全部インライン／ パネル4: 「Options」ボックス／ Overlays: 子パネル「Appearance」／ Guide Maps・Mesh Merge: インライン |

### 2.5 命名の不一致（F3検索・ツールチップ・パネルが別の名前）

| bl_label（F3で出る名前） | パネル上の表示 | ボックス見出し |
|---|---|---|
| Analyze UV Seams | Analyze Seams | Analyze Seams |
| Resample Span Density | − 1 / + 1 / Set / Apply | Adjust Density |
| Sync Retopo Seams | Check Retopo Seams / Sync Retopo Seams | Sync Counts |
| Seam Status | Seam Status | Status |
| Merge Blue into Green | Merge Drape into Grid | Mesh Merge（モジュール名は 2D Retopo Merge） |
| Clear（island colors） | Clear | Island Colors（F3では何のClearか分からない） |
| Preview Subdiv (non-destructive) | Preview Subdiv (non-destructive) / Remove Preview Subdiv | Subdivision |
| Guide: Show in 3D | Guide: Show in 3D ↗ / Guide: Back to 2D ↩ | （ヘッダー直置き） |

「3D」という語が6つの別概念に使われている: **Guide: Show in 3D**（GuideのShapeKey状態）／**3D Mirror**（ビューアオブジェクト）／**4. 3D Projection**（パネル名）／**Sync 2D > 3D**（Legacyのretopo側ShapeKey）／**3D Check**（オーバーレイプリセット）／**3D Match Distance**（設定）。

印（マーカー）も3種: **Anchor**（解析結果・×印）／**Topology Corner**（ユーザーの印・◇）／**Density Pin**（ユーザーの印・□）。CornerとPinは**UIが同型で目的も近い**（どちらもAdjust Densityから頂点を守る。`density_pin.py` は `ac9_topo_corner` も尊重する）。Cornerの本来の消費者（内部グリッド生成）は B-28 で打ち切りになっており、いま2種類ある理由がユーザーからは見えない。

### 2.6 見えない依存関係

- オーバーレイの大半は `_cache["pairs"]` を描くが、それを埋めるのは Analyze Seams だけで、**ファイルを開くたび・Reload Scriptsのたびに空になる**。Overlaysパネルの赤警告で補っているが、パネル1のボタンとOverlaysのボタンが**同じオペレータの二重配置**になっている
- Subdivide Retopo の境界スナップは Analyze Seams のキャッシュ前提（無ければ警告してスキップ）。パネル4からは分からない
- Generate はGuideの対称軸（Analyze Guide の結果）を使うが、パネル2にはその依存が書かれていない（P0で新たに生まれた依存）
- Analyze Anchors は「表示用の手動キャッシュ」で Generate では更新されない（B-14）。パネルからは解析の一種に見える

### 2.7 死んだ・保留の機能が一等地を占めている

- パネル4「Experimental / Legacy」（OSS掃除の候補A）・「Options」（Legacy Syncしか読まない3 bool）・「Maintenance」
- パネル2「Patch Grid」ボックスの **Grid Regions と grid_* 3プロパティ**は B-28 で打ち切られた自動グリッド。ボックス見出しの「1. Fill 2. cut with the Knife 3. Grid.」は打ち切った手順そのもの。**残すべきは Fill Regions だけ**
- `live_ghost_update`（Analysis Settings内）、`show_experimental`

### 2.8 ヘッダーパネルの肥大とOverlaysの位置

- ヘッダー（常時開・order 0）に 3ピッカー + 2条件ボタン + 3D Mirrorボックス（4コントロール+説明）。パネル1が最初から画面下に押し出される
- Overlaysは**最頻操作**なのに order 9（最下部・閉）。それを補うためにビューポートヘッダーへ複製し、さらにヘッダーパネルにもマスタースイッチを複製した。**3箇所に同じスイッチがあるのは「置き場所が間違っている」というシグナル**
- プリセット Boundary / Seams が Selection Link を OFF にする（B-15）。Selection Link は作業モード別ではなく常時欲しい導線

### 2.9 その他

- パネル1「Analyze Guide」内に**ミラーペアの一覧（N行）と不合格理由**が直接描画される。パネルにデータダンプが出るのは Seam Status のText block出力と方式が揃っていない
- flat_merge の10プロパティが1ボックスにインラインで並ぶ（seam_mode によって出る行が変わる）。パネル3の他2子パネルとも密度が違いすぎる
- 「Snap」ボックスの Ghost Snap Mode / UV Seam Snap Mode は**Gキーの挙動を変えるモード切替**であり、「境界を作る」操作ではない。編集操作群の隣に置くべきもの

---

## 3. 設計思想（6モジュールを貫くルール）

これが本提案の中心。レイアウトはこのルールの適用結果にすぎない。

### R1. 分類軸は「工程」だけ。モジュール名をUIに出さない
- タブの各パネルは**ユーザーがRetopoに対して何をしている段階か**で切る: **Setup → Boundary → Faces → 3D View**、横断的な **Guide Maps / Overlays / Advanced**
- どのモジュールのオペレータかは配置に影響しない。例: `clo_projector.subdivide_retopo` は Faces、`uv_seam_guide.ghost_force_bond` は Boundary(Edit)
- パネル名に番号を付けない（ループする作業に順序番号は嘘になる）

### R2. モードが第一のフィルタ
- 工程パネルは `context.mode` を見て **Object Mode = メッシュ全体への一括操作、Edit Mode = 選択に対する操作** を描き分ける
- 非該当モードの操作は**描かない**のではなく、1行の折り畳み見出し（例: `▸ Object Mode tools`）に畳む。存在は示す、場所は取らない
- 両モードで動くもの（Refresh Mirror / Replace / Symmetrize / Sync Selected）は両方に出してよいが、意味的に近い側に置く
- これにより `poll` の「Exit/Enter Edit Mode first」ラベルは**全廃**できる（畳んだ見出し自体が案内になる）

### R3. パネルは動詞の列。設定は子パネル、説明はツールチップ、結果は1行
- パネル本体に置けるのは **ボタン行** と、**そのボタンの引数になる値**（Count の数、Spacing の mm、Snap の距離）だけ
- 閾値・許容差・角度・解像度などの「一度合わせたら触らない値」は **子パネル `… Settings`（DEFAULT_CLOSED）** へ。パネルの設定が3個以下なら子パネル不要（Guide Maps程度）
- 説明文（INFO ラベル）は**原則ゼロ**。説明は `bl_description` / プロパティの `description` に書く（Blender標準のツールチップ機構）。既にほぼ全プロパティに詳細な description があるので、パネルの説明文は二重
- パネルに残してよい文字は **(a) 行動可能な警告**（`alert=True`、解消するボタンを同じ行に置く。例: 「No seam analysis loaded [Analyze]」）と **(b) 結果1行**（下記）のみ
- **結果はパネルにつき1行**。9個の `*_stats` を1パネル1つの `status_*` に集約し、最後に実行したオペレータが上書きする。詳細は `self.report()`（ステータスバー）と、必要なら Text block（Seam Status の `AC9_SeamStatus` と同じ方式）

### R4. 同じ意味は同じ形（共通ヘルパーで描く）
ルートに `ui_common.py` を作り、全モジュールの `ui.py` はこれだけを使って描く。

| パターン | ヘルパー | 描画 |
|---|---|---|
| dry run → 実行 | `draw_check_apply(row, idname, check_props, apply_props)` | `[Check] [Apply-label]` 同一行・同幅。Check は `VIEWZOOM` アイコン固定 |
| 派生データの作成/削除 | `draw_make_clear(row, make_id, clear_id, label)` | `[Label] [×]` 同一行、Clear は `X` アイコン・文字なし |
| ユーザーの印 | `draw_mark(row, noun, mark_id, clear_id)` | `Noun [+] [−]`、Clear All は `−` の Alt/Shift か子パネル。3種の印（Pin / Corner）を同一行形式に統一 |
| 表示対象の再計算 | `draw_refresh_icon(row, idname)` | 見出し行右端に `FILE_REFRESH` アイコンボタン（Overlaysのグループ見出し用） |
| 結果1行 | `draw_status(layout, text)` | `─ text` を薄く1行。空なら描かない |
| 前提未達 | `draw_blocker(layout, text, fix_idname)` | `alert` ボックス1行 + 解消ボタン。文言は「何が無いか」だけ |
| 破壊的操作 | `draw_destructive(col, ...)` | 見出し `Rebuild` の下にまとめ、`⚠` 1行のみ。個別に注意文を書かない |

`_guide_ready()`（seam ui）とプロジェクターの「Set Guide + Flat SK in the header above.」は `draw_blocker` に統一。

### R5. 1コントロール1箇所
- 例外は**ビューポートヘッダーの常設2つ**（Overlays ポップオーバー / Refresh 3D）のみ。「パネルが長くて届かないから複製」は禁止——長いのはR2/R3違反なのでそちらを直す
- Analyze Seams / Analyze Anchors のOverlays側コピーは削除。代わりに R3 の blocker（キャッシュが空のとき1つだけ出るボタン）に置き換える

### R6. 語彙の固定と bl_label の一致
名詞（UIとdescriptionで統一。日本語訳は付けない）:

| 語 | 意味 | 使わない言い方 |
|---|---|---|
| **Retopo** | 作業対象のローポリ | Retopo Mesh / retopo object |
| **Guide** | CLOメッシュ（Basis=3D, Flat SK=2D） | source mesh / CLO guide |
| **Mirror** | Retopoの3D読み取り専用ビュー（`AC9_3D_Mirror`） | 3D Mirror / 3D Projection / Sync 2D>3D |
| **Guide 2D / Guide 3D** | GuideのShapeKey状態 | Show in 3D / Back to 2D（ボタン文言は `Guide: 2D ⇄ 3D`） |
| **Seam** | 縫い合わせ（両側にペアがある） | UV seam（自由端を含む輪郭は **Outline**） |
| **Outline** | Guideの型紙輪郭（Seam + Free Edge） | boundary segments / UV seam lines |
| **Span** | アンカーで切られた縫い目の区間 | segment / chain |
| **Anchor** | 解析で決まる区間の端点 | — |
| **Pin** | ユーザーが打つ保護マーク | Density Pin / Topology Corner（→ §5.4 で統合を提案） |
| **Ghost** | 対岸の対応点（表示） | opposite point |
| **Fold** | アイランド内対称軸 | symmetry axis / centre line |
| **Mirror Pair** | 左右アイランドのペア | — |

動詞:

| 動詞 | 意味 | 対になる語 |
|---|---|---|
| **Analyze** | Guideを読んでキャッシュを作る（Retopo不変） | Clear |
| **Generate** | 無いものを作る（挿入のみ） | — |
| **Sync** | 両側を一致させる（挿入のみ） | Check |
| **Adjust** | 既存を作り直す（Density） | — |
| **Mark / Unmark** | ユーザーの印 | — |
| **Check** | 変更せず報告する | Apply/Sync |
| **Refresh** | 派生ビューを作り直す（Mirror, Ghosts） | Remove（オブジェクト）/ Clear（キャッシュ） |
| **Fill / Subdivide / Collapse / Fix** | 面操作 | Clear |
| **Bake** | 画像/属性に焼く | Clear |

- **`bl_label` = パネル上の文字列** にする（F3検索とツールチップの一致）。文言が状態で変わるボタン（Show/Hide系）は `bl_label` を中立形（`Toggle Guide 2D/3D`）に
- 「Clear」だけの `bl_label` は禁止（`Clear Island Colors` のように目的語を付ける）

### R7. Overlaysは「見るもの」の一枚板。表示用データの更新ボタンは見出し行に
- Overlaysパネルは関心別のままでよい（唯一の横断パネルとして正当）。ただし **(a) ビューポートヘッダーからポップオーバーで開ける**、**(b) 解析ボタンを置かない**（blockerの1つだけ）、**(c) 手動更新式の表示データ（Ghosts / Anchors / Island Colors）はそのグループ見出し右端の ⟳ で更新**
- プリセットは **Selection Link を管理対象から外す**（B-15）
- 色・線幅・サイズは Appearance 子パネルのまま

### R8. 死んだ・保留・上級者向けは Advanced に隔離
- Legacy Flip（Sync 2D>3D / 3D>2D / Bind / Apply 3D Edits）、Options 3 bool、Maintenance、Diagnostics（Inspect Vert / Diagnose Spaces / Diagnose Retopo Mapping）、Grid Regions（B-28打ち切り）、Live Ghost Update
- パネル `Advanced`（DEFAULT_CLOSED、最下部）。OSS掃除で削除が決まったものはそこから消える。**UI設計としては「削除か隔離か」を先に決めなくても進められる**ようにするのがこのパネルの目的

### R6 補足（2026-09-02 実機確認後の修正）: 行ラベル + ボタン文言 = bl_label の意味
`labeled_row` の左列が名詞（Seams / Sync / Preview …）を担うので、ボタン側は動詞または対比語だけにする（`Analyze` / `Sync` / `Fill` / `Twin` / `Self`）。**bl_label（F3で出る名前）は完全な名前のまま**（`Analyze Seams` / `Replace Twin Island` / `Symmetrize Island`）。R6 の「bl_label = 表示文字列」は「**行ラベルとボタンを合わせて読めば bl_label と同じ意味になる**」と読み替える。F3 検索性を落とさないための意図的な例外。

### R10. サイドバーは狭いのが前提 — 切れない語を選ぶ
実機で `Replace ...` のように省略表示になったボタンが目立った。**「切れてもツールチップで分かる」は設計ではない。** 目安は標準幅1カラムのサイドバーで、`labeled_row` の右側（幅70%）に2ボタンが並んだとき各ボタンが8文字前後まで。手本は `Ghost` / `Outline`、`Check` / `Apply`、`+` / `−` / `×`。長い説明的な名前は bl_label（F3）と description に置き、パネルには短い動詞・名詞・対比語だけを出す。Overlays のトグル文言も同様（`Twins (magenta)` / `Points` / `Vertex Counts`）。

### R9. 新機能を足すときのチェックリスト（ルールの運用）
1. 工程はどれか（Setup / Boundary / Faces / 3D View / Overlays / Advanced）。決まらないなら Advanced
2. Object か Edit か。`poll` と描画側を一致させる
3. 動詞は R6 の表から選ぶ。`bl_label` = 表示文字列
4. 設定は `… Settings` 子パネルへ。インラインに置けるのはボタンの引数のみ
5. 結果はそのパネルの `status_*` に書く。新しい `*_stats` を作らない
6. パネルに説明文を足さない。`bl_description` に書く
7. オーバーレイを増やすなら、Overlaysのグループ + `_SEAM_TOGGLES`/`_PROJ_TOGGLES` 登録 + プリセット判断の3点セット
8. 同じ意味のボタンが既に無いか（R5）

---

## 4. 提案する構成

### 4.1 全体

```
[3Dビューポートヘッダー]   [👁 Overlays ▾]（ポップオーバー）   [⟳ Refresh Mirror]

AC9 Cloth Retopo タブ
├─ Setup            開。ピッカー3 + Analyze + 状態1行。子: Analysis Settings
├─ Boundary         開。Object/Editで描き分け。子: Boundary Settings
├─ Faces            開。Object/Editで描き分け。子: Face Settings, Drape Merge
├─ 3D View          開。Mirror / Guide 2D⇄3D / Align
├─ Guide Maps       閉。現状維持（形式だけ統一）
├─ Overlays         閉。プリセット + グループ。子: Appearance
└─ Advanced         閉。Legacy / Options / Maintenance / Diagnostics / 打ち切り機能
```

トップレベル7は現状と同数だが、**ヘッダーパネルが消え、番号が消え、各パネルが「今のモードで押せるもの」だけになる**。

### 4.2 Setup

```
▾ Setup
   Retopo   [ Retopology.001            ]
   Guide    [ ML_fab.002                ]
   Flat SK  [ UV_Map_Flattened        ▾ ]
   [ Analyze Guide ]                 [×]
   ─ Seams 3845 · Anchors 197 · Folds 20 · Mirror pairs 7 / 9 tested
   ▸ Analysis Settings
```

- **Analyze Guide 1ボタン** = Analyze Seams + Analyze Guide(Symmetry/Mirror Pairs) + Analyze Anchors を順に実行（全部Guide読み取り専用）。現状 `ac9_cloth.analyze_guide` が既に2つを束ねているので、Seams と Anchors を足す。個別実行は Analysis Settings 内に小ボタンで残す（判断点 D4）
- 未解析時は blocker: `⚠ Not analyzed (cleared on file open / reload)  [Analyze Guide]`。この1つが**アドオン内で唯一の Analyze の入口**になり、Overlays側の複製は消える
- ミラーペア一覧・不合格理由はパネルから外し、`Analysis Report` としてText blockへ（Seam Status と同方式）。パネルは件数だけ
- Analysis Settings（子・閉）: precision / 3D Match Distance / Marked Seams / Marked Seam Distance / Symmetry Tolerance / Mirror Pair Tolerance / Max Seam Distance / Bond Distance / （個別 Analyze ボタン3つ）

### 4.3 Boundary

**Object Mode:**
```
▾ Boundary                                        Object Mode
   Generate      [ Generate Boundary ]
   Sync          [ Check ] [ Sync Seams ]
   Verify        [ Seam Status ]
   Preview       [ Preview Fill ] [×]
   Rebuild  ⚠    [ Replace Partner ] [ Symmetrize ]
   ─ Would add 12 vertices over 3 seam sides
   ▸ Edit Mode tools (Density, Pins, Snap)
   ▸ Boundary Settings
```

**Edit Mode:**
```
▾ Boundary                                          Edit Mode
   Density       [ − 1 ]  [ + 1 ]  [ Even Out ]
                 Count   [  8  ] [ Set ]
                 Spacing [ 20mm] [ Apply ]
   Selected      [ Check ] [ Sync + Pin ]
   Pins          Pin [+] [−]     Corner [+] [−] [Detect]
   Snap (G)      [ Ghost ] [ Outline ]   [ 10mm ]  ☑ Fold
                 [ Force Bond ]
   ─ 9 → 10 vertices, 0 faces removed
   ▸ Object Mode tools (Generate, Sync, Status)
   ▸ Boundary Settings
```

- Boundary Settings（子・閉）: Generate: targets / scope / divide by / spacing・count / ignore under / corner angle ／ Sync: seam distance(`retopo_sync_tol`) ／ Corner: candidate angle ／ Preview Fill: spacing / clearance
- Preview Fill は Boundary に置く。理由: 用途が「境界密度の判定」で、`Generate → Preview Fill → Adjust → Preview Fill` のループが1パネルで閉じる（判断点 D6）
- 現在の説明文19本は全て `bl_description` へ。「never deletes a face」系の保証4本は、Adjust Density 行のツールチップ1本に集約
- Snap の2モード排他トグルは `[Ghost] [Outline]` の2ボタン（`toggle=True`、両方OFFも可）。Gキーの案内はツールチップ

### 4.4 Faces

**Object Mode:**
```
▾ Faces                                           Object Mode
   Fill          [ Fill Regions ] [×]
   Subdivide     [ Subdivide Retopo ]
   Preview       [ Preview Subdiv ☐ ]  Levels [2]  ☐ Smooth
   ▸ Edit Mode tools (Collapse, Quad Fix)
   ▸ Drape Merge
   ▸ Face Settings
```

**Edit Mode:**
```
▾ Faces                                             Edit Mode
   Collapse      [ Collapse (Keep UV) ]
   Quad Fix      Select [ Clean ] [ Saddle ] [ All ]
                 [ Fix ] [ Fix (Alternate) ]   ☑ Also saddles
   ─ 12 quads split (9 clean, 3 saddle)
   ▸ Object Mode tools (Fill, Subdivide)
   ▸ Face Settings
```

- Face Settings（子・閉）: Quad Fix の Warp Threshold / Flat-After-Fix / Only Selected
- **Drape Merge**（子・閉）: flat_merge 一式。別パイプラインなので独自ピッカー（Drape / Grid）はそのまま。ただし内部は R3 に従い `[Merge Drape into Grid]` + `[Select Holes]` + 結果1行を上に、10プロパティは下に `Settings` 見出しでまとめる（判断点 D5）
- Grid Regions と grid_* は Advanced へ（B-28で打ち切り）。Fill Regions のみ残す

### 4.5 3D View

```
▾ 3D View
   [ ⟳ Refresh Mirror ]           ☐ Auto-refresh on Tab
   ☑ Show Mirror                  [ Remove Mirror ]
   Guide         [ Guide: 3D ↗ ]   [ View: Mirror → ]      ← 条件表示は現状通り
   Align         [ Align Boundary to Outline ]             ← Object Mode のみ
```

- 現在ヘッダーにある 3D Mirror ブロック・Guide 2D/3D・Swap をここへ。「届かない」問題はビューポートヘッダーの `Refresh Mirror` と、Setup が3行に痩せることで解消する（判断点 D7）
- Align Boundary（閾値は Settings へ）は Boundary との重複が疑われる（UV Seam Snap / Force Bond / Generate と役割が近い）。まず 3D View に置き、OSS掃除で存廃判断（判断点 D9）

### 4.6 Guide Maps

現状の内容を維持し、形式だけ R3/R4 に合わせる。

```
▾ Guide Maps
   Resolution  [ 2048 ▾ ]
   Residual    [ Bake Residual Map ]   → AC9_ResidualMap
   Sag         [ Bake Sag Map ]        → AC9_SagMap
   ─ residual: mean 3.1mm, max 12.4mm
   ▸ Map Settings   (Residual Scale / Coverage Margin / Sag Scale)
```

### 4.7 Overlays

```
▾ Overlays                        （ビューポートヘッダーのポップオーバーと同じ描画関数）
   [ Overlays: ON ]
   ⚠ No seam analysis loaded   [ Analyze Guide ]        ← キャッシュ空のときだけ
   Presets   [ Boundary ] [ Seams ] [ 3D Check ] [ All Off ]
   ┌ Seam Lines ┐  ☑ Seams (cyan)  ☑ Pair Lines  ☑ Fold  ☑ Mirror Pair  ☐ Outline (white)
   ┌ Marks    ⟳ ┐  ☑ Anchors  ☑ Corners  ☑ Pins
   ┌ Ghosts   ⟳ ┐  ☑ Selected Only  ☑ Points  ☐ Only Unplaced  ☐ Lines  ☐ Snap Radius
   ┌ Status     ┐  ☑ Vertex Counts  ☑ Seam Status  ☐ Boundary Flags
   ┌ Mirror     ┐  ☑ Selection Link        （プリセットの対象外）
   ┌ Guide      ┐  Islands α[0.7] [Bake] [×]
   ▸ Appearance
```

- ⟳ = そのグループの表示データを再計算（Marks: Analyze Anchors、Ghosts: Refresh Ghosts）。Analyze Seams はここに置かない（R5）
- Island Colors（マテリアル焼き込み）は「Guideの見え方」なので Guide グループへ。GPUオーバーレイではないが、ユーザーの分類は「何が見えるか」（判断点 D10）
- 白線（Outline）とシアン線（Seams）の二重描画は既知の保留（B節）。統合するなら Outline を残し Seams を「Outlineのうち縫い目だけ色分け」にするのが自然。本提案の範囲外だが、同じグループに並べば比較しやすい
- ポップオーバー: `VIEW3D_HT_header` 側に `layout.popover(panel="AC9_PT_OverlaysPopover")`。UI領域のパネルクラスをそのまま popover に使えるかは**未検証**なので、`bl_region_type='HEADER'` の小さなパネルクラスを別に作り、`overlays.draw_overlay_body(layout, context)` を共有する形にしておくのが安全

### 4.8 Advanced

```
▸ Advanced（閉）
   Legacy Flip     [ Sync 2D > 3D ] [ Sync 3D > 2D ] [ Bind New Verts ] ☑ Auto Bind
                   [ Apply 3D Edits → 2D ]
   Options         ☑ Overwrite ShapeKey  ☑ Clear Failed Group  ☐ Select Failed
   Maintenance     [ Clear Guide Cache ] [ Clear Attachments ]
   Diagnostics     [ Inspect Vertex ] [ Diagnose Spaces ] [ Diagnose Retopo Mapping ] [ Analysis Report ]
   Abandoned       [ Grid Regions ] + grid_* 3項目   （B-28）
                   ☐ Live Ghost Update
```

---

## 5. 現在の全コントロール → 新配置 対応表

実装時に「消えたものが無い」ことを機械的に確認するための表。★ = 配置以外の変更あり。

### ヘッダーパネル（`__init__.AC9_PT_AC9ClothRetopoHeader`）
| 現在 | 新配置 |
|---|---|
| Overlays: ON/OFF | Overlays パネル + ヘッダーポップオーバー（ここからは削除） |
| Retopo Mesh / Guide / Flat SK | Setup |
| Guide: Show in 3D / Back to 2D | 3D View › Guide ★文言 `Guide: 3D ↗ / Guide: 2D ↩` |
| View: Mirror → / ← View: Guide | 3D View › Guide |
| Refresh 3D Mirror / Auto-Refresh / Show 3D Mirror / Remove | 3D View ★ `Refresh Mirror / Remove Mirror` |
| 説明「Edit the 2D retopo, then Refresh…」 | 削除（`refresh_mirror` の description へ） |

### 1. Seam Analysis
| 現在 | 新配置 |
|---|---|
| Analyze Seams / Clear | Setup › Analyze Guide（統合）/ 個別は Analysis Settings ★ |
| Refresh Ghosts / Clear | Overlays › Ghosts ⟳ ★（Clear は Ghosts トグルOFFで代替、または ⟳ の Alt） |
| Force Bond Selected | Boundary (Edit) › Snap 行の下 |
| Analyze Guide / Clear | Setup › Analyze Guide（統合） |
| ミラーペア一覧・不合格理由 | Advanced › Analysis Report（Text block）★。Setup は件数1行 |
| Replace Partner Island / Symmetrize Island | Boundary (Object) › Rebuild |
| Stats 4行 | Setup 状態1行 ★（シーンカスタムプロパティ5個は `_cache` 参照に置換可） |
| Analysis Settings 9項目 | Setup › Analysis Settings（`live_ghost_update` は Advanced へ） |

### 2. 2D Boundary
| 現在 | 新配置 |
|---|---|
| Generate 6プロパティ | Boundary Settings › Generate |
| Generate Seam Chain | Boundary (Object) ★ `Generate Boundary` |
| Check / Sync Retopo Seams | Boundary (Object) › Sync `[Check] [Sync Seams]` |
| Seam Distance (`retopo_sync_tol`) | Boundary Settings |
| Check Selected / Sync + Pin Selected | Boundary (Edit) › Selected |
| Topology Corner: Candidate Angle | Boundary Settings |
| Detect Candidates / Mark Corner / Clear / Clear All | Boundary (Edit) › Pins 行 `Corner [+][−][Detect]` ★（Clear All は `−` の Alt または Settings） |
| Preview Fill 2プロパティ | Boundary Settings |
| Preview Fill / Clear | Boundary (Object) › Preview |
| Fill Regions / Clear | Faces (Object) › Fill |
| grid_* 3項目 / Grid Regions | Advanced › Abandoned |
| −1 / +1 / Count+Set / Spacing+Apply / Even Out | Boundary (Edit) › Density |
| Mark Pin / Clear / Clear All | Boundary (Edit) › Pins 行 `Pin [+][−]` |
| Seam Status | Boundary (Object) › Verify |
| Ghost Snap Mode / UV Seam Snap Mode / Snap Distance / Snap to Fold | Boundary (Edit) › Snap ★ `[Ghost] [Outline]` |
| Ghost Snap Move / UV Seam Snap Move ボタン | 削除（Gキーで起動。F3から到達可）★ |
| 説明文 約19本 | 全て `bl_description` へ ★ |
| stats 6個（sync, sync_selected, corner, fill, grid, dens, pin, status） | `status_boundary` 1つに集約 ★ |

### 3. 2D Mesh
| 現在 | 新配置 |
|---|---|
| Mesh Merge 全部 | Faces › Drape Merge（子）★ 内部を「ボタン→結果→設定」順に |
| Collapse (Keep UV) | Faces (Edit) |
| Quad Fix: 2閾値 + Only Selected | Face Settings |
| Quad Fix: Clean/Saddle/All, Fix, Fix (Alternate), Also Split Saddles | Faces (Edit) › Quad Fix |
| 説明「A/B: Fix → orbit → Undo → Alternate → orbit」 | `fix_nonplanar_quads` の description へ |

### 4. 3D Projection
| 現在 | 新配置 |
|---|---|
| Experimental / Legacy 一式（5オペレータ + auto_bind + show_experimental） | Advanced › Legacy Flip |
| Subdivide Retopo | Faces (Object) › Subdivide |
| Preview Subdiv トグル / Levels / Smooth | Faces (Object) › Preview |
| Boundary Alignment: threshold / Align | 3D View › Align（閾値は Settings）判断点 D9 |
| island jump 警告 | Advanced（Legacy専用） |
| Island Colors: alpha / Bake / Clear | Overlays › Guide ★ `bl_label` を `Clear Island Colors` に |
| Options 3 bool | Advanced › Options |
| Maintenance 2 | Advanced › Maintenance |

### 5. Guide Maps
| 現在 | 新配置 |
|---|---|
| 全部 | Guide Maps（形式のみ統一） |

### Overlays
| 現在 | 新配置 |
|---|---|
| Overlays: ON/OFF | 維持（唯一の場所 + ポップオーバー） |
| 警告ボックス3行 | blocker 1行 + `[Analyze Guide]` ★ |
| Analyze Seams / Anchors ボタン | 削除（Anchors は Marks ⟳ へ）★ |
| プリセット4 | 維持 ★ Selection Link を対象外に（B-15） |
| Seam Lines 5 / Anchors 3 / Ghosts 5 / Boundary-Status 3 / 3D Mirror 1 | 維持。グループ名を `Seam Lines / Marks / Ghosts / Status / Mirror / Guide` に |
| Appearance | 維持 |

---

## 6. Before / After 指標（目標値）

「ごちゃごちゃ」を数字で追えるようにする。実装後は `_dev_tests/test_ui_integrity.py` を拡張して自動カウントする（パネルクラス列挙、`draw` をモックlayoutで実行して `box()` / `label(icon=INFO…)` / `prop()` 呼び出しを数える。**モックlayoutでの draw 実行は未検証**、不可ならソースの静的カウントで代替）。

| 指標 | 現状 | 目標 |
|---|---|---|
| トップレベルパネル | 7 | 7（番号なし、ヘッダーパネル廃止） |
| 常時開のパネル | 4（ヘッダー, 1, 2, 4） | 4（Setup, Boundary, Faces, 3D View） |
| `layout.box()` 総数 | 約30 | ≤ 8（Overlaysのグループのみ） |
| 説明文ラベル（stats・blocker除く） | 約40 | 0 |
| 1パネルに同時表示される押せないボタン（モード不一致） | 常に数個〜十数個 | 0（畳まれる） |
| 工程パネルのインライン設定値 | 15+10+… | ボタン引数のみ（Count, Spacing, Snap距離, Levels, Alpha, Resolution） |
| `*_stats` StringProperty | 13 + カスタムプロパティ6 | 4（status_setup / status_boundary / status_faces / status_maps） |
| 同一コントロールの重複配置 | Overlays×3, Refresh×2, Analyze×2 | ヘッダー常設2つのみ |
| `bl_label` ≠ 表示文字列 | 8以上 | 0（状態で変わるトグル文言を除く） |
| 「3D」を含む別概念の名前 | 6 | 2（Mirror / Guide 3D） |

---

## 7. 判断点（実装前にユーザーと決めること）

「実装より先に対話」（2026-09-02の教訓）に従い、設計上の分岐を明示する。**推奨を太字**にしてあるが、いずれも代替で成立する。

| # | 論点 | 選択肢 | 推奨と理由 |
|---|---|---|---|
| D1 | 工程の見せ方 | (A) パネル積み（現状方式）／(B) 1パネル内で `EnumProperty(expand=True)` のタブ行で工程を切替 | **(A)**。Blender標準の折り畳み挙動をそのまま使え、複数工程の状態行を同時に見られる。(B) はスクロールがゼロになる反面、他工程の状態が見えない。(A)で足りなければ(B)へ移行可能（描画関数を工程ごとに分けておけば差分は小さい） |
| D2 | 非該当モードの操作 | 描かない／グレーアウト／**1行に畳む** | **畳む**。「機能が消えた」と誤解させず、場所も取らない |
| D3 | Legacy等の扱い | 削除／**Advanced に隔離** | **隔離**。UI設計とOSS掃除の判断を分離できる。掃除で削除が決まれば Advanced から消えるだけ |
| D4 | Analyze の統合 | 3ボタン維持／**Analyze Guide 1ボタン + 個別は Settings** | **統合**。3つとも Guide 読み取り専用で、依存が「全部やってあること」前提の機能（Generate の fold軸、Subdivide のスナップ、オーバーレイ）が増えた。コストは Symmetry 約4秒（P0後）+ Anchors 0.25秒 + Seams。明示クリック1回なら許容範囲（未実測: 合計時間） |
| D5 | flat_merge の置き場 | Faces の子「Drape Merge」／独立パネル「Drape Merge」 | **Faces の子**。使う頻度が低く別入口なので、工程パネルの子として畳んでおく。独立パネルにすると「工程」の列に別パイプラインが並ぶ |
| D6 | Preview Fill の置き場 | **Boundary**／Faces | **Boundary**。目的が境界密度の判定で、Adjust Density とのループが1パネルで閉じる |
| D7 | 3D Mirror ブロックをヘッダーから外す | 外す（3D View へ）／ヘッダーに残す | **外す**。ビューポートヘッダーの Refresh で常設性は担保。Setup が3行になるので 3D View も画面内に入る見込み（**実機未確認**、パネル高さは要確認） |
| D8 | Corner と Pin の統合 | 別のまま／**UI上は「Pin」1種に統合**（内部属性は両方残す） | **統合を検討**。Corner の主消費者（自動グリッド）が打ち切られ、現在の実効的な意味は Pin と同じ「Adjust Density から守る」。残す理由が「Detect Candidates（角の自動提案）」だけなら、それは Pin の Detect として吸収できる。ただし将来 Corner を「流れの向きを変える点」として再利用する計画があるなら別のまま |
| D9 | Align Boundary to UV Seams の存廃 | 3D View に置く／Boundary に置く／Advanced | まず **3D View**。Legacy Sync の前処理として生まれた機能で、現在は UV Seam Snap / Force Bond / Generate と役割が重なる。使っていなければ Advanced → 削除 |
| D10 | Island Colors の置き場 | **Overlays › Guide**／3D View | **Overlays**。ユーザー分類は「何が見えるか」。GPU overlay でない点は実装詳細 |
| D11 | Snap Move ボタンの削除 | 削除（Gのみ）／残す | **削除**。モードトグルONなら G で起動する設計なので、ボタンは「G を押す代わり」でしかない。F3 から到達可 |
| D12 | Symmetry / Mirror / Fold の語の整理（ユーザー指摘。Stage 1 着手前に決定） | (a) Fold を Symmetry に統合し Fold 廃止／(b) Symmetry=性質、Fold=軸で両方残す／**(c) Symmetry=解析の総称、Fold と Twin をその2種の結果名にし、Mirror は3Dビューア専用に限定** | **(c)**。決定理由: ①最優先は Mirror との混同回避。現状「Mirror Pair」（左右アイランド）と「3D Mirror」（ビューア）が同じ語で、Overlays の同一パネルに「Mirror Pair Lines」と「3D Mirror」が並んでいた。ビューアは常時使う中心機能なので Mirror をそちらに残し、左右ペアの方を改名する。②Fold は廃止しない。「アイランド内の対称軸」は Snap 対象・Generate の強制分割点・Symmetrize の軸として**線として操作される実体**であり、性質名（Symmetry）では指せない。③左右ペアの新名は **Twin**（双子）。「Symmetric Pair」「L/R Pair」より短く、「別々の2枚が同形」を1語で表し、Blender 標準の Mirror モディファイア／Symmetrize とも衝突しない。④結果として語彙は次の通り: **Symmetry** = Guide の2D対称性解析の総称（`Analyze Symmetry` / `Symmetry Tolerance` / `Symmetrize Island`）、**Fold** = 1アイランド内の対称軸（`Detect Folds` / `Fold Lines`）、**Twin** = 互いに鏡像な別アイランドのペア（`Detect Twins` / `Twin Lines` / `Twin Tolerance` / `Replace Twin Island`）、**Mirror** = `AC9_3D_Mirror` ビューアのみ（`Refresh Mirror` / `Remove Mirror` / `Show Mirror`）。⑤内部識別子（`mirror_pair_*` プロパティ、`_cache` キー、`bl_idname`）は変更しない——.blend に保存済みのプロパティ値を壊さないため。変わるのはユーザーに見える文字列だけ |

### D12 補足: 「3D」の残置
「3D」を含む名前は `Guide: 3D ↗`（Guide の状態）と `3D Match Distance`（Basis 空間での距離、設定値）の2つだけ残す。どちらも「3D空間の」という本来の意味で使っており、ビューアを指していない。

| # | 論点 | 選択肢 | 決定と理由 |
|---|---|---|---|
| D13 | 隠れた前提条件（§2.6）の見せ方（ユーザーから裁量委任） | (a) 全前提を常時パネルに列挙／(b) 前提未達のときだけ警告行／**(c) 前提を「硬・軟」に分け、硬=blocker（赤・修正ボタン付き）、軟=hint（非alert・修正ボタン付き）、いずれも条件成立中だけ表示。加えて Setup に解析状態の1行を常設** | **(c)**。決定に先立ってソースで依存関係を実測した結果、**前提の大半は実在しなかった**: Generate Boundary は `run_generate_seam_chains` 内で `detect_island_symmetry` を自前実行する（Analyze Symmetry 不要）、Sync / Seam Status は Guide のスパンを毎回再計算する、Replace Twin / Symmetrize は実行時に再検出する、Refresh Ghosts は未解析なら Analyze Seams を自動実行する。**本当に残る依存は「Analyze Seams のキャッシュ（ファイルを開く／Reload Scripts で消える）」1つ**で、それに依存するのは (i) オーバーレイ全般、(ii) Ghost/Outline スナップ、(iii) Subdivide Retopo の境界スナップ（無ければ警告してスキップ＝軟）の3箇所。よって: ①硬=blocker: Overlays パネル冒頭（既存）、Boundary(Edit) の Snap トグルON時、Setup の状態行（「Seams not analyzed (cleared on file open / reload)」——修正ボタンは2行上にあるため付けない）。②軟=hint: Faces(Object) の Subdivide 行の下に「Seams not analyzed: Subdivide will not snap the boundary [Analyze Seams]」。③常設: Setup の readout 行「Seams N · Anchors N · Ghosts N · Folds N · Twins N / M」で「何が解析済みか」を常に見せる。(a) は R3（説明文ゼロ）に反し、(b) だけでは「今どこまで解析済みか」が分からない。**ルール**: hint は「実行はできるが結果が劣化する」場合に限り、1パネル1モードにつき最大1行。将来 Adjust Density が fold 軸（`symmetry_cache_has`、P1 未着手）に依存し始めたら、同じ hint パターンで Boundary(Edit) に1行足す |

---

## 10. 実装記録（Stage 1〜3、2026-09-02）

提案書 §8 の段階に沿って実装した。各段階の詳細はコミットメッセージにある。

- **Stage 1**（語彙統一・重複削除、挙動変更なし）: D12 の語彙を全 `bl_label` / `name` / `description` / poll・report 文言に適用。ヘッダーパネルの Overlays スイッチ、Overlays パネルの Analyze ボタン行を削除。B-15 修正。
- **Stage 2**（`ui_common.py` + Settings 子パネル + 説明文→ツールチップ）: §3 R4 のヘルパーを実装。Boundary Settings / Merge Settings / Map Settings を新設。パネル内の説明文 約40本→6本、`box()` 約30→14（ソース静的カウント）。
- **Stage 3**（パネル再編）: `Setup / Boundary / Faces / 3D View / Guide Maps / Overlays / Advanced` の7パネル。Boundary と Faces は Retopo のモードで描き分け、他モードの道具は1行に畳む（`ui_*_show_other`）。結果文字列 13個 + シーンカスタムプロパティを `status_boundary / status_faces / status_maps` の3つに集約。Island Colors は Overlays › Guide、Ghosts の更新は Overlays › Ghosts 見出しの ⟳ へ。Grid Regions・Legacy Flip・Options・Maintenance・Diagnostics・Twin 一覧・Live Ghost Update は Advanced へ。

- **実機確認後の修正**（2026-09-02、コミット「実機確認の修正2点」）: Rebuild 行 → `Symmetry` 行に `Twin` / `Self`（ユーザー確定）。R10「切れない語を選ぶ」を追加し、全パネルのボタン・行ラベル・Overlays トグル文言を短縮。
- **Stage 4**（D4 + R7a）: Setup に `Analyze Guide` 1ボタン（`ac9_cloth.analyze_all`: Analyze Seams → Analyze Symmetry → Analyze Anchors を既存オペレータ経由で順に実行。独自のキャッシュ操作はせず、`detect_island_symmetry_cached` のメモ化はそのまま活きる）と `×`（`ac9_cloth.clear_analysis`: 3解析＋ゴーストを一括クリア）。個別の Seams / Symmetry / Anchors 行は Analysis Settings 子パネルへ。Overlays を `draw_overlay_body()` に切り出し、サイドバーの `AC9_PT_Overlays` と、`bl_region_type='HEADER'` の `AC9_PT_OverlaysPopover`（`bl_ui_units_x=14`）が共有。ビューポートヘッダーは `[👁] [Overlays ▾] [Refresh Mirror]`。

**§4 の設計からの差分**（意図的）:
- Twin 一覧は Text block 化せず Advanced › Diagnostics に描いている（新規オペレータを増やさないため）。
- `Analyze Anchors` は Analysis Settings の行と Overlays › Marks の ⟳ の2箇所にある（前者は解析の入口、後者は表示データの更新。R7c の「表示データの更新は見出し行」に従い意図的）。
- Overlays › Appearance（色・線幅）はサイドバー側のみ。ポップオーバーには出さない（コスメティックで頻度が低い）。

**ヘッドレス検証**: Blender 5.0 で `register()` → クラス列挙 → UI 参照オペレータ・プロパティの実在確認 → `unregister()` → 再 register/unregister が各 Stage で PASS。**パネルの実表示・高さ・モード切替の見え方は実機未確認。**

---

## 8. 実装への移し方（提案。本文書の範囲外だが順序だけ）

1コミット1段階、各段階で Blender 実機の register/unregister とパネル表示を確認（GPU描画はヘッドレス不可）。

1. **段階0: 指標の baseline** — `test_ui_integrity.py` 拡張で §6 の現状値を記録
2. **段階1: 語彙と重複の整理（挙動変更なし）** — `bl_label` 一致、`Clear` に目的語、Overlays 複製ボタン削除、ヘッダーパネルのマスタースイッチ削除、B-15 修正。ここまでで F3・ツールチップが揃う
3. **段階2: `ui_common.py` と Settings 子パネル** — ヘルパー導入、パネル2/4/Merge の設定を子パネルへ、説明文を description へ移動。ボックス数と説明文が激減する段階
4. **段階3: パネル再編** — Setup / Boundary / Faces / 3D View / Advanced への再配置（§5 の表通り）、モード別描画、`status_*` 集約
5. **段階4: Analyze 統合とポップオーバー** — D4 / R7(a)。ポップオーバーの実現方式はここで実機検証

段階1〜2は既存パネル構造のまま進められるので、リスクが低く効果が見えやすい。段階3で構造が変わるので、**段階2の結果をユーザーが実機で見てから段階3に入る**のが安全。

---

## 9. 未検証・前提の明示

- 本文書は全てソース読みに基づく。**Blender GUI での実表示・パネル高さ・ポップオーバーの動作は未確認**
- `layout.popover()` に UI 領域のパネルクラスを渡せるかは未検証（§4.7 で安全策を併記）
- モックlayoutで `draw()` を走らせる指標計測は未検証（不可ならソース静的カウント）
- Analyze 統合後の合計実行時間は未実測（個別値: Anchors 0.255s は B-3 実測、Symmetry 約3982ms は P0 実測、Seams は未記録）
- 前回の再設計文書と思われた `REDESIGN_uv_seam_guide.md` は **2026-06-09 の座標系統一（guide_scale 廃止）の設計メモであり、UI再設計の文書ではない**。実際のUI整理は `引き継ぎ_2Dバウンダリ生成.md` A節（2026-08-31）と `ミラーペア機能設計.md`「UIパネル整理」節（2026-09-01）に記録されている。本提案はその2回を「同じ枠内の並べ替え」と評価したうえで、枠自体（分類軸・追加テンプレート・モード非依存）を変える案として書いた
