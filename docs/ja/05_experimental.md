# 05. Experimental 機能

**Edit > Preferences > Add-ons > AC9 Cloth Retopo** の **Experimental tools** を ON にしたときだけサイドバーに現れる機能です。既定は OFF。

対象は 7 つ: Grid Regions, Align to Outline, Mesh Edit, Quad Fix, Legacy Flip, UV Mirror（Guide Prep の 5 UV）, Density 一族（Density / Even Out / Count / Spacing / Pins / Corners）。

このアドオンは公開されており、サブパッケージの半分はまだ作り込み中です。ここに挙げたものは「動くが、まだ工程に組み込める完成度ではない」ものです。
どれもオペレータ自身の `poll()` で Experimental を見ているので、スイッチが OFF なら F3 検索から呼んでも動きません。

## Grid Regions

**Faces** の **Regions** 行、**Connect** の隣の **Grid**。**×** は **Clear Regions**。

カットが作った各領域をグリッド化します。角はカットから取ります。カット線が着地した頂点は定義上そこが角なので、ほとんどは印を付ける必要がありません。
構造的なノッチ（段になった裾、袖ぐりの内側）は測定で見つけ、これも角として数えます。四隅に足りない領域は、最も鋭い凸の曲がりで不足分を補い、その 1 つ 1 つを報告します。
カット線は先に一度だけ分割されるので、その両側の領域は同じ頂点を共有します。**外周は移動も追加もされません。** Edit Mode では選択のあるアイランドだけを処理します。

未完成な点: **shapely が必要**です（無いとエラーで中止。[02_install.md](02_install.md) 参照）。そのうえで、そもそも「型紙 = 四隅のパッチ」という単純化が実データで成立しません。実際の Guide で計測したところ、外形を角で切ると最大の 2 枚で 12 辺になり、そのうちどの 4 つを選んでも形を表せませんでした。
袖ぐりと段裾のあるボディ型紙は四辺形ではありません。だから分割はユーザーの手（ナイフ）に委ねる設計になっており、そのうえでの自動グリッドがここです。
角が 4 つより多い / 少ない領域は一覧にして放置されます。**Fill Mismatched Regions**（**Face Settings**）を ON にすると向かい合う辺の頂点数が違う領域も埋めますが、その差は三角形 1 列で吸収され、袖山のように短い曲線辺と長い辺が向かい合って三角形が 1 頂点に多数集まる領域は拒否されて報告されます。

なお、内部を完全自動でグリッド化する（ユーザーのカットを一切使わない）という別の初期機能は打ち切られています。**Grid Regions** はそれとは別物で、**Fill Regions** の後継です。

**Fill Regions**（閉じた領域ごとに n-gon を 1 枚置くだけ）と **Patch Grid**（四隅パッチのグリッド）は登録はされていますが、どのパネルにも描かれていません（F3 検索からのみ到達できます）。**Fill Regions** の役割は **Connect** が兼ねています。

## Align to Outline

**3D View** パネルの **Align → To Outline** と **Threshold**。詳細は [04_panels.md](04_panels.md) を参照。

Guide の型紙外周（UV シーム辺）から **Threshold** 以内の頂点を辺上へスナップし、境界フラグを立て直す旧 **Sync 3D > 2D** 系ワークフローの前処理です。

## Mesh Edit（Collapse Keep UV）

**Faces**（Edit Mode）の **Collapse** 行、**Keep UV**。

選択した辺を中点へ Collapse します（**Mesh > Merge > Collapse** と同じ操作）が、**UV を保ちます**。ネイティブの Collapse は UV アイランドを潰して壊します。
全 UV レイヤの UV が中点へ追従し、Collapse をまたぐ UV シームは分かれたままになります。

未完成な点というより置き場所の問題です。この機能は **Cloth 固有ではありません。** いまは仮にここに置いてあり、将来は共有の mesh-tools アドオンへ移る可能性があります。

## Quad Fix

**Faces**（Edit Mode）の **Select** / **Fix** 行、および **Face Settings** の **Quad Fix** 欄。

非平面クワッドを「正しい」対角線で三角形に割り直します。

| ボタン | すること |
|---|---|
| **Select → Clean** / **Saddle** / **All** | 全選択解除してから、選んだ種類のクワッドだけを選択する。フラグの立った面のうち、単純な折れがいくつで本当のサドルがいくつなのかを、直す前に見るため |
| **Fix → Convex** | 各非平面クワッドを**凸側の対角線**（アーティストが選ぶほう。外へ膨らみ、丸い曲面に合う）で三角化する |
| **Fix → Alternate** | もう一方（非凸側）の対角線で割る。「間違ったほう」のプレビュー用: Fix → 回して見る → Undo → Alternate → 回して見る → 比較 |

未完成な点: どちらが正しいかは幾何だけでは決まらず、視点による知覚が要ります。だから **Alternate** による A/B 比較が機能として用意されています。
**Face Settings** の **Non-planar Angle** / **Flat Angle** / **Only Selected** / **Fix Saddles** で対象と挙動が変わります。

## Legacy Flip

**Advanced** パネルの **Legacy Flip** 欄。

旧ワークフローです。リトポ自身が 2D（Basis）と 3D（`AC9_3D_Project` シェイプキー）を行き来し、どちらの状態でも編集できました。
**推奨されるのは 2D 編集 + Refresh Mirror** です（[01_concepts.md](01_concepts.md) の「2Dが正、3Dは鏡」）。

| ボタン | すること |
|---|---|
| **Apply → 3D → 2D** | Mirror 上で既存頂点に加えた移動を Guide 表面へスナップし、2D レイアウトを書き換える（境界頂点は CLO 外周に留まる）。**移動のみ**: Mirror 上で頂点を追加したりカットしてはいけない。新しいジオメトリは 2D で作る |
| **Sync → 2D > 3D** | 2D の全頂点を Guide 3D（Basis）へ再投影し、`AC9_3D_Project` シェイプキーに書く |
| **Sync → 3D > 2D** | リトポの現在の 3D シェイプキー状態を Guide 3D の最近点へスナップし、2D Basis を書き換える。Basis とシェイプキーの両方が更新される |
| **Bind → New Verts** | 3D の Edit Mode で作られた頂点（アタッチメントが欠けている / 2D 位置と矛盾している）を Guide 表面にバインドし、2D Basis 位置を修復する。隣接頂点のアタッチメントを使って布の正しい折り側に留まる |
| **Bind → Auto** | 上記を Edit Mode を抜けたときに自動実行する |

未完成というより、意図的に非推奨です。3D で編集すると壊れやすい逆投影の経路（新規頂点のスパイク修復を含む）を通ります。
また **Sync** 系は Object Mode 専用です。Edit Mode から呼ぶと Edit→Object→Edit のモード切り替えが重いメッシュ書き換えを挟むことになり、大きな Guide で Blender が落ちます。
UV アイランドを跨いだ頂点があると赤い警告行に件数が出ます。

なお **Legacy Flip Options**（**Overwrite ShapeKey** / **Clear Failed Group** / **Select Failed**）と **Maintenance** の 2 つは Experimental が OFF でも常に表示されます。

## Density（Density / Even Out / Count / Spacing / Pins / Corners）

**Boundary** の Edit Mode 側: **Density** 行（**− 1** / **+ 1** / **Even Out**）、**Count** 行（値 + **Set**）、**Spacing** 行（値 + **Apply**）、**Pin** 行（**+** / **−** / **×**）、**Corner** 行（**Detect** / **+** / **−** / **×**）。同じ扱いになるのは **Boundary Settings** の **Corners** → **Candidate Angle** 調整値と、**Overlays** の **Pins** / **Corners** トグルです。ボタン単位の詳細は [04_panels.md](04_panels.md) を参照してください。

この一族は制作で使われないまま終わり、これが書くものを一族の外から読むところも無かったため、ここへ移しました。
**Spacing → Apply**（この一族、Experimental — スパンの間隔を非破壊で設定する）と、**Boundary Settings** にある **Generate** 自身の **Vertices**（`gen_count`）/ **Spacing**（`gen_spacing_mm`）を混同しないでください。後者は名前が似ているだけの別の設定で、外周を最初に作るためのものであり、Experimental ではありません。

## UV Mirror（Guide Prep の 5 UV）

**Guide Prep** の **5 UV** 行: **Reference** / **Pairs** / **Self**。

片側で編集した UV レイアウトを、ミラー相手へ鏡像コピーします。左右の型紙は 2D の型紙としては鏡像ですが、3D では鏡像になりません（シミュレーションが左右を別々にドレープするため）し、トポロジーも一致しません（CLO が型紙ごとに独立にメッシュを切るので頂点数が違う。実測で製品ジャケットの 7 ペア中 3 ペア）。Blender 標準のツールはまさにこの 2 点で失敗します——`mesh.faces_mirror_uv` は 3D で鏡像の頂点を探し、`uv.paste` は同一トポロジーを要求します。外形だけは一致するので、ここでの転写は幾何で行います: **Reference** が編集前の CLO UV を控えとして残し、転写先の頂点の参照位置を転写元アイランドの参照空間へ反射させ、そこで重心座標で補間します。トポロジーは一致しなくて構いません。

**Pairs** が左右のペアアイランド、**Self** が cut-on-fold の 1 枚（選択のある側を残す）です。

未完成なのは転写ではなく**工程としての置き場所**です。**Guide Prep** の 1〜4 はどれも「押すだけ」ですが、これはパネルの外で行う手順の途中にしか意味がありません: ダートの辺を溶接（**Merge by Distance**）→ UV エディタで**片側だけ**ダートを縫い閉じる → **Unwrap** / **Minimize Stretch** で緩める →**ここで**これを押す → 仕上がった UV から **Flat SK** を作り直す。番号付きの並びに置いてあると順番に押したくなりますが、そういう作りではありません。パネルの他の機能に比べてテスト実績も薄いままです。

順番は効きますが強制はされません。**Reference** は **UV を編集する前**に作る必要があります。編集後に作ると参照レイヤが既に編集済みで、突き合わせる基準が無くなります。
