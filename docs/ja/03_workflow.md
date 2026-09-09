# 03. 作業の流れ

上から下へ一本道です。サイドバーのパネル順がそのまま工程順になっています。
各ステップに「次へ進む判断」を書いてあります。

## 0. CLO / MD から書き出す

CLO / Marvelous Designer で衣装を書き出し、Blender に読み込みます。
書き出しは **FBX**、**三角形メッシュ**、**UV は CLO 側で展開済み**にしておきます（詳細な書き出し設定は後日追記）。
アドオン側の前提は次の 2 つだけです。

- 三角形メッシュであること（**Create Flat SK** が既定で三角化するので、そこで満たせます）
- UV が展開済みであること（UV レイヤの無いオブジェクトは **Create Flat SK** でスキップされます）

厚みは Solidify モディファイアで付け、**ライブのまま**にしておいてください。

## 1. Prepare — CLO 書き出しを整える

ベイク元として使えるように、書き出しの外周と折れ線を整えます。パネルの行番号が手順の順番です。
CLO の書き出しは通常複数オブジェクトなので、**Create Flat SK** だけは選択中の全メッシュに、それ以外はアクティブオブジェクトに効きます。

1. **Create Flat SK**（Object Mode）— UV アイランド境界で分割し、平面レイアウトをシェイプキーに書きます。以降の 2〜4 はこのシェイプキーを必要とします。
2. **Find Folds** — 二面角が **Crease Min Angle** 以上の辺を折れ線としてタグ付けします。**Show** で何がタグされたか選択して確認、**Mark** / **Untag** で手直し、**×** で全消し。
3. **Inset Line**（Edit Mode）— タグした折れ線を両側にインセットして帯にします。**Inset Pieces** より先に実行します（帯が、まだインセットされていない外周まで届く必要があるため）。
4. **Inset Pieces**（Object Mode）— 型紙ごとに、外周から **Width** 以内の頂点を外周へ吸収してから、外周を **Width** だけインセットして平行な頂点列を作ります。

**次へ**: パネル下の結果行に処理件数（吸収した頂点数、除去した針状三角形、帯幅の達成率など）が出ます。帯幅が **Width** に対して極端に細い箇所が報告されていないか見てください。
Solidify は最後まで適用しないこと。

<!-- screenshot: Prepare パネル（1 Flat SK 〜 4 Pieces と結果行） -->

## 2. Setup — 入力を指定して解析する

1. **Retopo** にこれから作る低ポリメッシュ、**Guide** に CLO 書き出しメッシュを指定します。
2. **Flat SK** は Guide のシェイプキーから検索ドロップダウンで選びます（**Create Flat SK** で Guide を処理した場合は自動で入ります）。
3. **Analyze Guide** を押します。Seams（縫い合わせペア）→ Symmetry（Folds と Twins）→ Anchors（スパンの区切り）を順に実行します。すべて Guide の読み取りだけで、Retopo には触りません。

**次へ**: **Analyze Guide** の下に `Seams 〇〇 · Anchors 〇〇 · Folds 〇〇 · Twins 〇〇 / 〇〇` のような 1 行が出ます。
ここが「Seams not analyzed」の赤い警告のままなら、以降のオーバーレイ・ゴースト・スナップは何も出ません。
**この解析はファイルを開いたときと Reload Scripts で消えます。** そのつど押し直してください。

## 3. Boundary — 2D の外周を作る

詳細は [04_panels.md](04_panels.md) の Boundary 節にあります。おおまかな順序は次のとおりです。

1. Object Mode: **Generate** — Guide の外周に沿って境界頂点列を作ります。
2. Edit Mode: 必要な位置にナイフでカットを入れます。
3. Edit Mode: そのカット頂点を選んで **Selected → Sync+Pin** — 対岸に相棒の頂点を作り、両側を Pin します。
4. Edit Mode: **Density**（**− 1** / **+ 1** / **Even Out**、**Count → Set**、**Spacing → Apply**）で密度を整えます。Pin と Corner は動きません。
5. Edit Mode: **Corner** を付けます（**Detect** で候補を選択 → 取捨選択 → **+**）。
6. Object Mode: **Sync → Check** で追加内容を確認してから **Sync**。
7. Object Mode: **Verify → Status**。

**次へ**: **Status** の結果が全部緑（両側が対応）になったら Faces へ。
赤（両側にあるが合わない）・オレンジ（片側だけ）が残っているうちは、面を張っても縫い目が 3D で一致しません。
全件の表はテキストブロック `AC9_SeamStatus` に書き出されます。

<!-- screenshot: Boundary パネル Object Mode 側と、Seam Status オーバーレイが緑になったビューポート -->

## 4. Faces — 2D の内側を埋める

1. **Regions → Connect** — 開いた線を近くの辺まで延長して繋ぎ、閉じた領域すべてに面を張ります。これでナイフが使えるようになります（ナイフは面を切る道具なので、外周だけでは切れません）。**外周に新しい頂点は作りません**（外周に届いた線は既存の境界頂点に繋がります）。
2. ナイフ（K）で型紙を自由に分割します。切ったあと **Connect** をもう一度押せば、いまのカットが作る領域に張り直します。
3. Edit Mode: **Connect Rows** — 2 本の線（辺）を選ぶと、頂点対ごとに桟（rung）を通し、その間の面を列に割ります。頂点数が違う場合は少ない側に足されます（外周は分割されません）。
4. **Symmetry → Twin** / **Self** — 左右対の型紙の片側を相手側から作り直す（**Twin**）、1 枚の型紙を自身の折り軸で作り直す（**Self**）。**Self** は **Self axis** で軸を選んでから押します。どちらも破壊的です。
5. Object Mode: **Subdivide** — 2D で分割し、新しい境界頂点を Guide の外周線へスナップして 3D に再投影します。Mirror があれば自動で更新されます。

**次へ**: パネル下の結果行に、繋いだ端の数・張った領域数・作った桟の数が出ます。
Mirror を見て、面の流れが破綻していないことを確認してください。

<!-- screenshot: Faces パネル（Edit Mode 側）と Connect / Connect Rows の結果 -->

## 5. 3D View — 3D で確認して確定する

1. **Mirror → Refresh** — いまの 2D レイアウトから Mirror を作り直します。Object Mode でも Retopo の Edit Mode でも動きます。**Auto-refresh** を ON にすると Edit Mode を抜けたときに自動で走ります。
2. **Show** で Mirror の表示を切り替えます（2D 作業中は上面図で邪魔になりがちなので、削除ではなく非表示で逃がします）。
3. **Guide → To 3D** / **To 2D** で Guide を 3D 形状と平面の間で切り替えます。3D かつ Mirror がある状態では、隣のボタンで Guide と Mirror の表示を入れ替えられます。
4. 必要なら **Align → To Outline** で、外周線の近く（**Threshold** 以内）にある頂点を外周線上へスナップし、境界としてフラグを立てます。
5. **Finalize** — `<Retopo名>_Final` を作ります。Retopo 自体は触られません。

**次へ**: `<Retopo名>_Final` が選択・アクティブになり、結果行に頂点数が出ます。
これがアドオンの外へ持ち出す成果物です。

<!-- screenshot: 3D View パネルと Mirror が Guide の上に乗った状態 -->

## 6. Guide Maps — 診断ベイク（任意）

作業の途中、どこを直すべきか見たいときに使います。

1. **Resolution** を選びます（1024 / 2048 / 4096、既定 2048）。
2. **Residual → Bake** — Guide 表面と現在の Retopo のずれ（赤 = Guide が手前、青 = 奥、白 = 一致、暗い灰 = Retopo にまだ覆われていない）。画像 `AC9_ResidualMap`。
3. **Sag → Bake** — 型紙ごとの平面フィットからのずれ（白 = 手前に膨らむ、黒 = 奥に沈む、中間の灰 = 平面上）。画像 `AC9_SagMap`。等高線が低周波のたわみに沿うエッジループの流れになります。
4. **Drape → Bake** — AO × Curvature の陰影付きリファレンス。画像 `AC9_DrapeMap`。2D でナイフを入れるときの当たりに使います。
5. **Preview** で見たいマップを選び、**Plane** を押すと `AC9_BakePreview` という 1×1 m のプレーンが作られ、Solid シェーディングのビューポートを **Solid の色 = Texture** に切り替え、**X-Ray** を ON にします。

**次へ**: マップが見えていること。見えないときは **Solid** の欄が **Texture** になっているか、X-Ray が入っているかを確認してください（Preview Plane は Retopo の 5 mm 下にあります）。
進捗はベイク中の段階表示のみで、Blender のベイク本体（`bpy.ops.object.bake`）は途中の割合を返さないため、その区間は止まって見えます。

<!-- screenshot: Guide Maps パネルと Preview Plane に表示された Residual Map -->

## 7. 片付け

- **Advanced → Reset to Defaults** — 設定値だけを既定に戻します。Retopo / Guide / Flat SK の指定とメッシュは触りません。
- **Advanced → Clear All AC9 Data** — このファイルからアドオンの痕跡を全部取り除きます。押すと、何が消えるかの一覧がダイアログに出ます。
