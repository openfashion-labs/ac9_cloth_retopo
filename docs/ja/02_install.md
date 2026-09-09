# 02. インストールと設定

## 必要環境

| 項目 | 値 |
|---|---|
| Blender | 5.0.0 以上（`blender_manifest.toml` の `blender_version_min`） |
| バージョン | 1.0.0 |
| ライセンス | GPL-3.0-or-later |
| 追加ライブラリ | 不要（Experimental の一部機能のみ shapely が必要） |

## インストール

1. アドオンを ZIP にまとめます。
2. Blender で **Edit > Preferences > Add-ons**。
3. 右上のメニューから **Install from Disk** を選び、ZIP を指定します。
4. 一覧の **AC9 Cloth Retopo** を有効化します。

有効化すると、3Dビューポートのサイドバー（N キー）に **AC9 Cloth Retopo** タブが追加されます。
パネルは作業フェーズの順に並びます: **Prepare** / **Setup** / **Boundary** / **Faces** / **3D View** / **Guide Maps** / **Overlays** / **Advanced**。

さらに、**Retopo** が設定されているときだけ、3Dビューポートのヘッダーに次の 3 つが足されます。
サイドバーをスクロールせずに押せる場所として、意図的に重複させてある唯一の場所です。

- オーバーレイのマスタースイッチ（目のアイコン）
- **Overlays** ポップオーバー
- **Refresh Mirror**

<!-- screenshot: サイドバーの AC9 Cloth Retopo タブ（全パネルが畳まれた状態）とビューポートヘッダーの Overlays / Refresh Mirror -->

## Experimental tools スイッチ

**Edit > Preferences > Add-ons > AC9 Cloth Retopo** を展開すると、スイッチが 1 つだけあります。

- **Experimental tools** — 既定 OFF

これを ON にしたときだけサイドバーに現れる機能は次の 7 つです。

Preview Fill, Grid Regions, Drape Merge, Guide Separate, Mesh Edit, Quad Fix, Legacy Flip

未完成の機能なので既定では隠れています。中身は [05_experimental.md](05_experimental.md) を参照してください。
OFF のままだと、**Advanced** パネルの末尾に「Experimental tools: Edit > Preferences > Add-ons > AC9 Cloth Retopo」というヒントが出ます。

なお、Experimental が OFF のときは対応する設定用の子パネル（**Face Settings**、**Separation Settings**、**Merge Settings**）も出ません。

## shapely について

外部ライブラリ shapely が要るのは **Experimental の 2 機能だけ**です。

- **Preview Fill**（Faces パネル）
- **Drape Merge**（Faces の子パネル）

公開されている通常の機能（Prepare / Setup / Boundary / Faces の Connect・Connect Rows・Symmetry・Subdivide / 3D View / Guide Maps / Overlays）は shapely 無しで動きます。

shapely は Blender に同梱されていないため、無い状態で上の 2 機能を実行するとエラーとして次のメッセージが出ます。

```
shapely is required for Preview Fill but could not be imported. Install it into Blender's Python, e.g.:
  <blender>/python/bin/python -m pip install shapely
```

```
shapely is required for 2D Retopo Merge but could not be imported. Install it into Blender's Python, e.g.:
  <blender>/python/bin/python -m pip install shapely
```

`<blender>` は Blender のインストール先に読み替えてください。Blender 5.0 に同梱される Python は 3.11 系です（Blender 5.0.1 で実測: 3.11.13）。
numpy は Blender に同梱されているので、別途入れる必要はありません。
