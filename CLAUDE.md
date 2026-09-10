# AC9 Cloth Retopo — 開発ルール

このファイルはこのリポジトリ専用の開発ルール（リポジトリに同梱して公開する）。

## これは何か
CLO/Marvelous Designerで作ったドレープメッシュを、Blenderでのリトポロジー作業に使うための
統合アドオン（garment retopology toolkit）。Blender 5.0+、GPL-3.0-or-later。

## モジュール構成
`__init__.py` が全サブモジュールを束ねる。各サブモジュールは `properties.py`（PropertyGroup）/
`operators.py` / `ui.py`（+ 中身の `core.py`）という同じ形。

| サブモジュール | 行数 | 役割 |
|---|---|---|
| `uv_seam_guide` | 3193 | UVシーム解析＋対岸ゴースト表示 |
| `clo_projector` | 5631 | CLO/MDガイドを使った2D↔3D射影（バリセントリック） |
| `bake_maps` | 1752 | Guide Maps（残差・たわみ・Drape のベイク）。画像は Guide ごとに `AC9_<種別>Map_<Guide名>` で分離、`Keep in file` で パックの有無を選択、`Baked Maps` 一覧から削除 |
| `guide_separate` | 約1700 | Guide Separate（自己密着したドレープ層を引き離す。ShapeKey `AC9_Separated`＋診断色 `AC9_Gap`。3D Sourceで投影・Mapsの読み元を切替）。`detect.py`=ベクトル化した接触検出（判定は「3Dで近い ∧ 平坦レイアウトで遠い」）、`seams.py`=縫合ペアの検出・免除・双子拘束、`levels.py`=粗い代理/拡散メッシュと格子間のバリセントリック転写、`core.py`=解法本体。変位場は粗い格子で解いてGuideへ戻す（Guide自身で解くと1時間超、粗格子で80〜110秒） |
| `quad_fix` | 469 | 非平面クワッドを正しい対角線で分割 |
| `mesh_edit` | 288 | UV保持Collapse。**Cloth専用ではない**（元コメントの通り、将来は別の共有mesh-toolsアドオンに移す可能性あり） |
| `clo_cleanup` | 約1700 | CLO Cleanup（CLO書き出しのベイク前整備。平面シェイプキー上で外周・折れ線をインセットし元3Dへ戻す `FlatSession`。Find Folds → Inset Line → Inset Pieces → UV Mirror（`uv_mirror.py`: 参照UV `AC9_UV_Reference` 経由のバリセントリック転写で、片側で編集したUVをミラーペア島/折れ線の反対半分へ鏡像コピー）。計算はwm.progressで進捗表示） |

依存: `shapely`（`uv_seam_guide/preview_fill.py` と `patch_grid.py` のみ＝Preview Fill と Grid Regions）。wheel を `wheels/` に同梱し `blender_manifest.toml` の `wheels` で宣言しているので、利用者のインストール作業は不要（cp311 と cp313 の2セット。Blender 5.0 が Python 3.11、5.1/5.2 が 3.13）。他は標準bpy/bmesh/mathutils/numpy（numpyはBlender同梱）。

## 命名規則（2026-08-26 NK→AC9リネーム完了、これ以降はこの形で統一）
- クラス: `AC9_OT_*`（オペレータ）、`AC9_PT_*`（パネル）、`AC9*Props`（PropertyGroup、例: `AC9ClothRetopoProps`）
- オペレータ名前空間: `ac9_cloth.xxx`（bl_idname）
- シーンプロパティ: `bpy.types.Scene.ac9_cloth_retopo`（`context.scene.ac9_cloth_retopo`でアクセス）
- .blendに永続化される名前（ShapeKey/頂点グループ/マテリアル/画像/属性/カスタムプロパティ）は全て`AC9_`または`ac9_`接頭辞
- 新しい識別子を追加するときは必ずこの接頭辞に従う。`NK`/`nk_`は歴史的経緯で過去に使われていたが、公開に向けて全廃済み（コード内に残っていたら削り忘れ、指摘してよい）

## 進捗表示（待たされる操作）
- **待ち時間の出る処理は `ui_common.ProgressScope` で囲む。** 生の `wm.progress_begin/end` は使わない
  （ネストすると進捗バーが何本も出る）。毎アイテムの更新は必ず `ui_common.ProgressThrottle` を通す
  （`wm.progress_update` はGUIでカーソル再描画に約1.4ms/回かかる）。
- `register()` が全 `AC9_OT_*` の `execute`（modalは`invoke`）を計測ラッパーで包んでいる。
  Preferences の "Report slow operators" を ON にすると、進捗を出さずに1秒以上かかったツールが
  `[AC9] <op> took 12.3 s without progress` としてコンソールに出る。**新しいオペレータを足したら
  一度これを ON にして押してみる**（書き漏れを2度やっている）。仕組みの詳細は
  `ui_common.py` の "Progress audit" のコメントにある。

## マニュアル（docs/）
- マニュアルの正本は `docs/ja/` と `docs/en/`（各6章）。GitHub 上でそのままページとして読まれる前提。外部サービスに正本を置かない（コードと同じコミットで直せることが、この置き方を選んでいる理由）。
- **UI のラベル・ボタン構成・パネルの並び・前提条件・エラーメッセージを変えたら、同じコミットで `docs/ja` と `docs/en` の該当章を直す。** 別コミットに回すと必ず片方だけ進む。
- どの章を見るか: `04_panels.md`（ボタン別リファレンス・パネルの並び順）、`03_workflow.md`（手順そのものが変わる変更）、`06_troubleshooting.md`（エラーメッセージの文言）、`05_experimental.md`（Experimental の増減）、`01_concepts.md`（用語の変更）。
- **2 言語は必ず対で直す。** 片方だけのコミットは差分の非対称としてレビューで弾く。
- ボタン名の翻訳は `translations.py`（ja_JP）にある。ラベルを変えたら翻訳キーも一緒に直す（キーは英語リテラル完全一致）。
- 画像は `docs/images/` に置き、`blender_manifest.toml` の `paths_exclude_pattern` に同ディレクトリを足して配布 ZIP から外す（ZIP は wheels だけで既に 22MB）。UIのスクリーンショットは PNG（JPEG は文字の縁にリンギングが出る）。

## 公開に向けて守ること
- **絶対パス・個人名・顧客名や商品名を、コード・コメント・コミットメッセージ・PR文面に書かない。** このリポジトリは一般公開される
- 新しいテスト用アセットを追加する時は、実際の商用データから作らない（合成データにする）
- `maintainer`欄・ログ接頭辞は`AC9`で統一（`blender_manifest.toml`参照）
