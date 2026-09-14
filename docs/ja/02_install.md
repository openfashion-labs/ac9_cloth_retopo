# 02. インストールと設定

## 必要環境

| 項目 | 値 |
|---|---|
| Blender | 5.0.0 以上（`blender_manifest.toml` の `blender_version_min`） |
| バージョン | 1.0.0 |
| ライセンス | GPL-3.0-or-later |
| 追加ライブラリ | 手動インストール不要 — shapely はアドオンに同梱されています（下の「shapely について」参照） |

## インストール

1. [最新リリースのページ](https://github.com/openfashion-labs/ac9_cloth_retopo/releases/latest) を開き、
   **Assets** の中の `ac9_cloth_retopo-<バージョン>.zip` をダウンロードします。
2. Blender で **Edit > Preferences > Add-ons**。
3. 右上のメニューから **Install from Disk** を選び、ダウンロードした ZIP を指定します。
4. 一覧の **AC9 Cloth Retopo** を有効化します。

> **どの ZIP を選ぶか**
> Assets には `Source code (zip)` も並んでいますが、そちらではインストールできません。
> リポジトリ上部の緑の **Code** ボタンにある **Download ZIP** も同じものです。
> どちらもソースツリーだけのアーカイブで、同梱している shapely の wheel が入っていないためです。
> **`ac9_cloth_retopo-` で始まる ZIP**（20MB 以上あります）を選んでください。

有効化すると、3Dビューポートのサイドバー（N キー）に **AC9 Cloth Retopo** タブが追加されます。
パネルは作業フェーズの順に並びます: **Prepare** / **Setup** / **Boundary** / **Faces** / **3D View** / **Guide Maps** / **Overlays** / **Advanced**。

さらに、**Retopo** が設定されているときだけ、3Dビューポートのヘッダーに次の 3 つが足されます。
サイドバーをスクロールせずに押せる場所として、意図的に重複させてある唯一の場所です。

- オーバーレイのマスタースイッチ（目のアイコン）
- **Overlays** ポップオーバー
- **Refresh Mirror**

![サイドバーの AC9 Cloth Retopo タブ。全パネルが畳まれ、Setup だけが開いて「Set the Guide and Flat SK first」の警告が出ている状態。](../images/02_sidebar_tab.png)

## Experimental tools スイッチ

**Edit > Preferences > Add-ons > AC9 Cloth Retopo** を展開すると、スイッチが 1 つだけあります。

- **Experimental tools** — 既定 OFF

これを ON にしたときだけサイドバーに現れる機能は次の 7 つです。

Grid Regions, Align to Outline, Mesh Edit, Quad Fix, Legacy Flip, UV Mirror（Guide Prep の 5 UV）, Density 一族（Density / Even Out / Count / Spacing / Pin / Corner）

未完成の機能なので既定では隠れています。中身は [05_experimental.md](05_experimental.md) を参照してください。
OFF のままだと、**Advanced** パネルの末尾に「Experimental tools: Edit > Preferences > Add-ons > AC9 Cloth Retopo」というヒントが出ます。

## shapely について

shapely は **アドオンに同梱**されています（Python wheel として）。手動でインストールする必要はありません。`blender_manifest.toml` の `wheels` に宣言されていて、アドオンを有効化すると Blender が対応する wheel を自動でインストールします。
同梱される wheel は 10 個です。プラットフォーム 5 種（Windows x64・macOS arm64・macOS x64・Linux x64・Linux arm64）× Python バージョン 2 種（Blender 5.0 は Python 3.11、Blender 5.1 は Python 3.13）の組み合わせです。

**Preview Fill** と **Grid Regions**（Experimental）以外の全機能は shapely 無しで動きます。

その Blender 用の同梱 wheel が入らなかった場合（既知の例は Windows on ARM。shapely の wheel が PyPI に存在しません）、Preview Fill は実行の代わりにパネルに警告行を出し、Grid Regions も同様にエラーで止まります。該当する場合は [06_troubleshooting.md](06_troubleshooting.md) を参照してください。

numpy は Blender に同梱されているので、別途入れる必要はありません。
