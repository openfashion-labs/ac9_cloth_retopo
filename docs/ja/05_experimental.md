# 05. Experimental 機能

**Edit > Preferences > Add-ons > AC9 Cloth Retopo** の **Experimental tools** を ON にしたときだけサイドバーに現れる機能です。既定は OFF。

対象は 7 つ: Preview Fill, Grid Regions, Drape Merge, Guide Separate, Mesh Edit, Quad Fix, Legacy Flip。

このアドオンは公開されており、サブパッケージの半分はまだ作り込み中です。ここに挙げたものは「動くが、まだ工程に組み込める完成度ではない」ものです。
どれもオペレータ自身の `poll()` で Experimental を見ているので、スイッチが OFF なら F3 検索から呼んでも動きません。

## Preview Fill

**Faces**（Object Mode）の **Preview** 行。**Auto Fill** と **×**。

外周が正しい頂点数を持っているかは、面が張られて初めて見えます。この機能は各型紙の内側に使い捨てのクワッドグリッドを敷き、境界頂点は 1 つも動かさず、作ったものにフラグを立てて **×** で全部戻せるようにします。
最終トポロジーではありません。見て、密度を直して、もう一度実行する、という使い方です。**Adjust Density** がスパンを作り直す必要が出たときには自分で消します。

未完成な点: 外形が閉じていない型紙はスキップされ、その件数だけが報告されます。**shapely が必要**です（無いとエラーで中止）。
そして役割としては **Grid Regions** と重複しており、「型紙を四隅に還元させる」という前提が実データでは成り立たないことが分かっています（次項）。

## Grid Regions

**Faces** の **Regions** 行、**Connect** の隣の **Grid**。**×** は **Clear Regions**。

カットが作った各領域をグリッド化します。角はカットから取ります。カット線が着地した頂点は定義上そこが角なので、ほとんどは印を付ける必要がありません。
構造的なノッチ（段になった裾、袖ぐりの内側）は測定で見つけ、これも角として数えます。四隅に足りない領域は、最も鋭い凸の曲がりで不足分を補い、その 1 つ 1 つを報告します。
カット線は先に一度だけ分割されるので、その両側の領域は同じ頂点を共有します。**外周は移動も追加もされません。** Edit Mode では選択のあるアイランドだけを処理します。

未完成な点: そもそも「型紙 = 四隅のパッチ」という単純化が実データで成立しません。実際の Guide で計測したところ、外形を角で切ると最大の 2 枚で 12 辺になり、そのうちどの 4 つを選んでも形を表せませんでした。
袖ぐりと段裾のあるボディ型紙は四辺形ではありません。だから分割はユーザーの手（ナイフ）に委ねる設計になっており、そのうえでの自動グリッドがここです。
角が 4 つより多い / 少ない領域は一覧にして放置されます。**Fill Mismatched Regions**（**Face Settings**）を ON にすると向かい合う辺の頂点数が違う領域も埋めますが、その差は三角形 1 列で吸収され、袖山のように短い曲線辺と長い辺が向かい合って三角形が 1 頂点に多数集まる領域は拒否されて報告されます。

なお、内部を完全自動でグリッド化する（ユーザーのカットを一切使わない）という別の初期機能は打ち切られています。**Grid Regions** はそれとは別物で、**Fill Regions** の後継です。

**Fill Regions**（閉じた領域ごとに n-gon を 1 枚置くだけ）と **Patch Grid**（四隅パッチのグリッド）は登録はされていますが、どのパネルにも描かれていません（F3 検索からのみ到達できます）。**Fill Regions** の役割は **Connect** が兼ねています。

## Drape Merge

**Faces** の子パネル **Drape Merge**（+ 子パネル **Merge Settings**）。

平面の Drape ストリップメッシュを平面の Grid メッシュに埋め込みます（衝突部では Drape が勝つ）。結果に対する穴検出付き。
Guide ベースの境界ワークフローとは別の入口なので、**共有の Retopo / Guide を読まず、自分専用の 2 つのオブジェクトピッカーを持ちます**。

| ボタン | すること |
|---|---|
| **Drape → Grid** | Drape が横切るグリッドセルをクリップし（Drape が勝つ）、縫い目を埋めて 1 つのメッシュに縫合する |
| **Holes** | Edit Mode に入って、アクティブメッシュの内側の境界ループ（穴）を選択する。外周は無視。何も選択されなければ穴なし |

直前のマージが穴を残した場合は赤い警告行に件数が出ます。穴が無ければ「Last merge: no holes」。

未完成な点: **shapely が必要**です。**Merge Settings** の **Seam Fill** モードは **QUAD** / **TRI** / **NGON** と枝分かれし、それぞれ別の調整値を持ちます（**Carve Cells** / **Quad Angle** / **Split Drape** / **Carve Margin** / **Sliver Factor**）。
入力の作り方は工事中です。おおまかには、ドレープ（たわみ）に沿ったメッシュを別に用意し、それを外周の内側に入れ込んで合体させる、という使い方です。手順は機能が固まってから書きます。

## Guide Separate

**3D View** パネル内の **Contact** 行（**Check** / **Separate** / **×**）と **3D Source**、および子パネル **Separation Settings**。

Guide の互いに接触している層（プリーツ、巻きの重なり）の間に最小の隙間を開け、レイキャストのベイクが隣の層を拾わないようにします。
結果は Guide 上のシェイプキー `AC9_Separated` で、Basis は元のドレープを保ちます。**3D Source** を **Separated** にしている間、投影と Guide Maps はその分離後の形を読みます。

- **Check** は測定と色分けだけ（頂点色 `AC9_Gap`: 赤 = 接触、黄 = 隙間未満、緑 = 十分）
- **Separate** が実際に `AC9_Separated` を作り、**3D Source** を自動的に **Separated** に切り替えます
- **×** は `AC9_Separated` と `AC9_Gap` を消して Basis に戻します

Object Mode 専用。**Separation Settings** は **Gap** / **Smooth Radius** / **Max Iterations** / **Include Solidify**。

未完成な点: 収束しないことがあります。その場合は「まだ隙間未満の接触が N 個」という警告になり、Solidify の厚みがある場合は「厚みが層の間隔を超えている可能性が高い」と付記されます。
結果行には移動量の最大値と法線の p99（度）も出るので、形をどれだけ壊したかは数値で見る必要があります。

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

未完成というより、意図的に非推奨です。3D で編集すると壊れやすい逆投影の経路（`run_reverse_projection` と新規頂点のスパイク修復）を通ります。
また **Sync** 系は Object Mode 専用です。Edit Mode から呼ぶと Edit→Object→Edit のモード切り替えが重いメッシュ書き換えを挟むことになり、大きな Guide で Blender が落ちます。
UV アイランドを跨いだ頂点があると赤い警告行に件数が出ます。

なお **Legacy Flip Options**（**Overwrite ShapeKey** / **Clear Failed Group** / **Select Failed**）と **Maintenance** の 2 つは Experimental が OFF でも常に表示されます。
