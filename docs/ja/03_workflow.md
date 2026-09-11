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
全手順が **Guide** に対して実行されるので、先に上の **Setup** パネルで **Guide** を指定してください（**Retopo** は 2 節まで後回しで構いません）。何を選択・アクティブにしているかは関係なく、**Guide** 欄が指しているオブジェクトと、そのオブジェクトのモードだけが効きます。CLO の書き出しが複数オブジェクトに分かれている場合は、**Guide** を 1 つずつ差し替えて整えます。

1. **Create Flat SK**（Object Mode）— UV アイランド境界で分割し、平面レイアウトをシェイプキーに書きます。以降の 2〜4 はこのシェイプキーを必要とします。
2. **Find Folds** — 二面角が **Crease Min Angle** 以上の辺を折れ線としてタグ付けします。**Show** で何がタグされたか選択して確認、**Mark** / **Untag** で手直し、**×** で全消し。
3. **Inset Line**（Edit Mode）— タグした折れ線を両側にインセットして帯にします。**Inset Pieces** より先に実行します（帯が、まだインセットされていない外周まで届く必要があるため）。
4. **Inset Pieces**（Object Mode）— 型紙ごとに、外周から **Width** 以内の頂点を外周へ吸収してから、外周を **Width** だけインセットして平行な頂点列を作ります。

**次へ**: パネル下の結果行に処理件数（吸収した頂点数、除去した針状三角形、帯幅の達成率など）が出ます。帯幅が **Width** に対して極端に細い箇所が報告されていないか見てください。
Solidify は最後まで適用しないこと。

![Guide Prep パネル（1 Flat SK / 2 Folds / 3 Lines / 4 Pieces）と、平面に展開された型紙。](../images/03_prepare_panel.png)

整えた効果は 3D に出ます。左が書き出したまま、右が **Inset Line** / **Inset Pieces** の後 — 折れ線に沿って走っていた鋭い折れが消えています。

![CLO 書き出しの肩まわり。左は折れ線に沿って鋭い筋が入っているが、右はインセット後で滑らかになっている。](../images/04_inset_before_after.jpg)


## 2. Setup — 入力を指定して解析する

1. **Retopo** にこれから作る低ポリメッシュ、**Guide** に CLO 書き出しメッシュを指定します。
2. **Flat SK** は Guide のシェイプキーから検索ドロップダウンで選びます（1 節の **Create Flat SK** で既に入っています）。
3. **Analyze Guide** を押します。Seams（縫い合わせペア）→ Symmetry（Folds と Twins）→ Anchors（スパンの区切り）を順に実行します。すべて Guide の読み取りだけで、Retopo には触りません。

**次へ**: **Analyze Guide** の下に `Seams 〇〇 · Anchors 〇〇 · Folds 〇〇 · Twins 〇〇 / 〇〇` のような 1 行が出ます。
ここが「Seams not analyzed」の赤い警告のままなら、以降のオーバーレイ・ゴースト・スナップは何も出ません。
**この解析はファイルを開いたときと Reload Scripts で消えます。** そのつど押し直してください。

## 3. Boundary — 2D の外周を作る

詳細は [04_panels.md](04_panels.md) の Boundary 節にあります。おおまかな順序は次のとおりです。

1. Object Mode: **Generate** — Guide の外周に沿って境界頂点列を作ります。
2. Edit Mode: 必要な位置にナイフでカットを入れます。
3. Edit Mode: 正としたい側の辺（または頂点）を選んで **Match** — ナイフで入れた頂点の相手側を対岸に作ります。選択は必須です: **Match** は「どちら側が正しいか」の判断なので、何も選択せずに押すと全 Seam をなぞる代わりに拒否されます。足すだけの操作なので、両側に相手の無い頂点がある Seam は触れずに残り、**Status** に紫（数は同じだが位置が合わない）で出ます。そのときは片側の余分な頂点を **Dissolve** してから **Match** をやり直してください。
4. Object Mode: **Verify → Status**。

**Density**（**− 1** / **+ 1** / **Even Out**、**Count → Set**、**Spacing → Apply**）と **Corner** はこの流れから外れました。Experimental になったため（[05_experimental.md](05_experimental.md)）、**Experimental tools** を ON にしたときだけ表示されます。縫い目の両側で頂点数が違う場合の答えは **Match** で、それを指し示すのが **Status** です。

新しい密度で作り直したい Seam は、**両側を消してから** **Generate** を押してください（片側だけ消すと、相手側の分割をそのまま継承してしまいます）。
頂点を **Delete** して開けた穴は **Generate** が埋め直しますが、**Dissolve** した箇所（辺は残ります）には **Generate** は触れません。
**Status** は T字接合（辺の上に乗っているのに繋がっていない頂点）も数えて選択します。

**次へ**: **Status** の結果が全部緑（両側が対応）になったら Faces へ。
赤（両側の数が違う）・紫（数は同じだが位置が合わない）・オレンジ（片側だけ）が残っているうちは、面を張っても縫い目が 3D で一致しません。赤とオレンジは **Match** / **Generate** で埋まりますが、紫は頂点を動かす作業なのでボタンでは直りません。
全件の表はテキストブロック `AC9_SeamStatus` に書き出されます。

![Boundary パネル。Object Mode tools（Generate / Status）と Edit Mode tools（Match / Ghosts / Snap / Bond）、および Generate が作った境界頂点列。](../images/03_boundary_panel.png)

## 4. Faces — 2D の内側を埋める

1. **Regions → Connect** — 開いた線を近くの辺まで延長して繋ぎ、閉じた領域すべてに面を張ります。これでナイフが使えるようになります（ナイフは面を切る道具なので、外周だけでは切れません）。**外周に新しい頂点は作りません**（外周に届いた線は既存の境界頂点に繋がります）。
2. ナイフ（K）で型紙を自由に分割します。切ったあと **Connect** をもう一度押せば、いまのカットが作る領域に張り直します。
3. Edit Mode: **Connect Rows** — 2 本の線（辺）を選ぶと、頂点対ごとに桟（rung）を通し、その間の面を列に割ります。頂点数が違う場合は少ない側に足されます（外周は分割されません）。
4. **Symmetry → Twin** / **Self** — 左右対の型紙の片側を相手側から作り直す（**Twin**）、1 枚の型紙を自身の折り軸で作り直す（**Self**）。**Self** は **Self axis** で軸を選んでから押します。どちらも破壊的です。
5. Object Mode: **Subdivide** — 2D で分割し、新しい境界頂点を Guide の外周線へスナップして 3D に再投影します。Mirror があれば自動で更新されます。まだ切っていない N-gon はそのまま通過し、枚数だけが結果行に出ます（未完成の型紙があっても全体のシルエットとポリゴン数を確認できるようにするため）。

**次へ**: パネル下の結果行に、繋いだ端の数・張った領域数・作った桟の数が出ます。
Mirror を見て、面の流れが破綻していないことを確認してください。

![Faces パネル（Edit Mode 側）と、Auto Fill で型紙の内側にクワッドが敷かれた状態。](../images/03_faces_connect.png)

**Subdivide** は 2D で割ってから 3D へ投影し直すので、解像度を上げてもシルエットが崩れません。

![Subdivide の前後。粗いクワッドのジャケットが、シルエットを保ったまま分割されて密度が上がる。](../images/03_subdivide.gif)

## 5. 3D View — 3D で確認して確定する

1. **Mirror → Refresh** — いまの 2D レイアウトから Mirror を作り直します。Object Mode でも Retopo の Edit Mode でも動きます。ジオメトリだけを扱い、表示状態は変えません — ただし Mirror を新規に作る初回だけは、**View** を **Mirror** に切り替えて見せます。
2. **View → Mirror** / **Guide** / **Both** — 何を表示するか。**Mirror** が作業状態で、リトポの 3D 形状だけを見せ、Guide は非表示にして平面に寝かせます。**Guide** は衣装だけ、**Both** は両方を重ねます。リトポと衣装の見比べは Mirror ↔ Both の往復で行います。
3. どれもふつうの表示 / 非表示なので、逃げ道はアウトライナーです。2D 作業中に Mirror が邪魔ならそこで隠し、平面レイアウトを下敷きに使いたければ Guide を表示に戻します。
4. **Wire → Wireframe** で、すべての 3D Viewport の Wireframe オーバーレイを View の状態とは無関係に一括で切り替えられます。
5. 必要なら **Align → To Outline** で、外周線の近く（**Threshold** 以内）にある頂点を外周線上へスナップし、境界としてフラグを立てます。
6. **Finalize** — `<Retopo名>_Final` を作ります。Retopo 自体は触られません。

**次へ**: `<Retopo名>_Final` が選択・アクティブになり、結果行に頂点数が出ます。
これがアドオンの外へ持ち出す成果物です。

### Retopo と Mirror を両方 Edit Mode にする

**3D で場所を指して、2D で直す。**Blender の標準機能（マルチオブジェクト編集）でできますが、
思いつきにくいので手順として書いておきます。

1. Retopo と Mirror の**両方を選択**して **Tab**。2 つとも Edit Mode に入ります。
2. 3D のビューポートで Mirror の頂点をクリックして、直したい箇所を 3D で指します。
   **Selection Link** オーバーレイが、その頂点が 2D レイアウトのどこにあたるかを橙のマーカーで示します（逆向きも同じ）。
3. 2D のビューポートで **Retopo 側**を編集します。
4. **Refresh** を押すと、Edit Mode を抜けないまま Mirror が追いつきます。

編集するのは常に Retopo です。**Mirror は閲覧専用**で、Mirror に加えた編集は次の Refresh で上書きされます（読み取られるのは選択だけ）。

この運用のときに効くのが**隠しの追従**です。Retopo で **H** で隠した頂点 / 辺 / 面は、
ボタンを押さなくても Mirror の同じ場所が隠れます（**Alt+H** で戻せば Mirror も戻ります）。
込み入ったところを数枚だけ出して作業するときに使ってください。向きは Retopo → Mirror の一方向で、
Mirror 側だけ隠しても Retopo には返りませんし、次の同期で Retopo の状態に戻されます。
Blender は Edit Mode でしか隠しを描画しないので、**Mirror が Object Mode のときは見た目は変わりません**（フラグは入っているので、Tab で入れば隠れています）。

![3D View パネルと、平面の Retopo から作られた 3D の Mirror。アウトライナーの `Retopology_AC93DMirror` がその実体。](../images/03_3dview_mirror.png)

## 6. Guide Maps — 診断ベイク（任意）

作業の途中、どこを直すべきか見たいときに使います。

1. **Resolution** を選びます（1024 / 2048 / 4096、既定 2048）。
2. **Residual → Bake** — Guide 表面と現在の Retopo のずれ（赤 = Guide が手前、青 = 奥、白 = 一致、暗い灰 = Retopo にまだ覆われていない）。画像 `AC9_ResidualMap_<Guide名>`。
3. **Sag → Bake** — 型紙ごとの平面フィットからのずれ（白 = 手前に膨らむ、黒 = 奥に沈む、中間の灰 = 平面上）。画像 `AC9_SagMap_<Guide名>`。等高線が低周波のたわみに沿うエッジループの流れになります。
4. **Drape → Bake** — Guide の 3D 形状から焼いた AO と Curvature を掛け合わせた 1 枚（`AC9_DrapeMap_<Guide名>`。AO / Curvature 単体は合成後に削除されます）。2D でナイフを入れるときの当たりに使います。AO の効くスケールは **Map Settings → AO Distance**（既定 30 mm）。真っ黒な型紙が出たらこの値が大きすぎる（密着した型紙同士が全遮蔽になる）。合成の比率は **AO Mix**（既定 0.7）。
5. **Preview** で見たいマップを選び、**Plane** を押すと `AC9_BakePreview` という 1×1 m のプレーンが作られ、Solid シェーディングのビューポートが **Solid の色 = Texture** に切り替わります。あわせて **Retopology オーバーレイ**が ON になります。

マップの画像は Guide ごとに分かれるので、複数の衣装を並行して進めても互いに上書きしません。ファイルが抱えているマップと容量は **Baked Maps** の一覧で確認・削除できます。

**次へ**: マップが見えていること。見えないときは **Solid** の欄が **Texture** になっているか、**Retopology** オーバーレイが入っているかを確認してください（Preview Plane は Retopo の 5 mm 下にあります）。
進捗はベイク中の段階表示のみで、Blender のベイク本体（`bpy.ops.object.bake`）は途中の割合を返さないため、その区間は止まって見えます。

![Guide Maps パネル（Residual / Sag / Drape の Bake と Preview の切り替え）。](../images/03_guide_maps_panel.png)

## 7. 片付け

- **Advanced → Reset to Defaults** — 設定値だけを既定に戻します。Retopo / Guide / Flat SK の指定とメッシュは触りません。
- **Advanced → Clear All AC9 Data** — このファイルからアドオンの痕跡を全部取り除きます。押すと、何が消えるかの一覧がダイアログに出ます。
