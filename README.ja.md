[English](README.md) | 日本語

# AC9 Cloth Retopo

CLO / Marvelous Designer で作った衣装メッシュを、平面に展開した型紙のかたち（2D）のままリトポロジーするための Blender アドオンです。
3D の形は、平面のリトポを高ポリの Guide へ重心座標で投影して得ます。編集は常に 2D、3D は閲覧専用の鏡（Mirror）という設計です。

- **対応 Blender**: 5.0 以上
- **バージョン**: 1.0.0
- **ライセンス**: GPL-3.0-or-later（[LICENSE](LICENSE)）

## インストール

ZIP にまとめ、**Edit > Preferences > Add-ons > Install from Disk** から指定して有効化します。
3Dビューポートのサイドバー（N キー）に **AC9 Cloth Retopo** タブが出ます。

## ドキュメント

日本語マニュアル: [docs/ja/](docs/ja/README.md)（概念 / インストール / 作業の流れ / パネル別リファレンス / Experimental / トラブルシューティング）

## 状態

v1.0.0。通常機能は外部ライブラリなしで動作します。
未完成の機能（Preview Fill, Grid Regions, Drape Merge, Guide Separate, Mesh Edit, Quad Fix, Legacy Flip）は Add-on Preferences の **Experimental tools** を ON にしたときだけ表示されます（既定 OFF）。うち Preview Fill と Drape Merge は shapely の別途インストールが必要です。
