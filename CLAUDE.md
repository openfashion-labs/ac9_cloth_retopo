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
| `flat_merge` | 1114 | 2Dリトポマージ（青=ドレープ帯を緑=グリッドへ合体、shapely依存） |
| `bake_maps` | 665 | Guide Maps（残差マップ・たわみマップのベイク） |
| `guide_separate` | 約600 | Guide Separate（自己密着したドレープ層を引き離す。ShapeKey `AC9_Separated`＋診断色 `AC9_Gap`。3D Sourceで投影・Mapsの読み元を切替） |
| `quad_fix` | 469 | 非平面クワッドを正しい対角線で分割 |
| `mesh_edit` | 288 | UV保持Collapse。**Cloth専用ではない**（元コメントの通り、将来は別の共有mesh-toolsアドオンに移す可能性あり） |
| `clo_cleanup` | 約1700 | CLO Cleanup（CLO書き出しのベイク前整備。平面シェイプキー上で外周・折れ線をインセットし元3Dへ戻す `FlatSession`。Find Folds → Inset Line → Inset Pieces。計算はwm.progressで進捗表示） |

依存: `shapely`（flat_mergeのみ、Blender非同梱・利用者が手動pip install）。他は標準bpy/bmesh/mathutils/numpy（numpyはBlender同梱）。

## 命名規則（2026-08-26 NK→AC9リネーム完了、これ以降はこの形で統一）
- クラス: `AC9_OT_*`（オペレータ）、`AC9_PT_*`（パネル）、`AC9*Props`（PropertyGroup、例: `AC9ClothRetopoProps`）
- オペレータ名前空間: `ac9_cloth.xxx`（bl_idname）
- シーンプロパティ: `bpy.types.Scene.ac9_cloth_retopo`（`context.scene.ac9_cloth_retopo`でアクセス）
- .blendに永続化される名前（ShapeKey/頂点グループ/マテリアル/画像/属性/カスタムプロパティ）は全て`AC9_`または`ac9_`接頭辞
- 新しい識別子を追加するときは必ずこの接頭辞に従う。`NK`/`nk_`は歴史的経緯で過去に使われていたが、公開に向けて全廃済み（コード内に残っていたら削り忘れ、指摘してよい）

## 公開に向けて守ること
- **絶対パス・個人名・顧客名や商品名をコードやコメントに書かない。** このリポジトリは一般公開される
- 新しいテスト用アセットを追加する時は、実際の商用データから作らない（合成データにする）
- `maintainer`欄・ログ接頭辞は`AC9`で統一（`blender_manifest.toml`参照）
