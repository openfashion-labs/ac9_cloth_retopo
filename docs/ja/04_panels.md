# 04. パネル別リファレンス

サイドバー **AC9 Cloth Retopo** タブの並び順に説明します。

## パネル共通の文法

どのパネルでも同じ種類のコントロールは同じ見た目になります。

| 見た目 | 意味 |
|---|---|
| **Check** / 実行名 の 2 つ組 | 同じ処理の「報告だけ」と「実行」。**Check** は何も変えません |
| 実行名 / **×** の 2 つ組 | 派生データを作るボタンと、それを消すボタン |
| **+** **−** **×** の 3 つ組 | 選択に印を付ける / 選択の印を外す / 印を全部外す |
| **Detect** | 候補を機械的に提案して選択状態にするだけ。採用は自分で **+** |
| 赤い警告行 | 硬い前提が満たされていない。下のボタンは動かない（多くは直すボタンが同じ行にある） |
| 情報アイコンの行 | 柔らかい前提。動くが機能は落ちる |
| 薄いグレーの 1 行 | そのパネルで最後に実行した結果 |

説明文はボタンのツールチップ（`bl_description`）に入っています。パネル本文に長い説明は出ません。
数値の設定は「**... Settings**」という子パネルにまとめてあり、既定で畳まれています。

**Boundary** と **Faces** は、Retopo が Object Mode か Edit Mode かで表示が切り替わります。
いまのモードのツールが上に出て、もう一方は 1 行の折りたたみ（**Object Mode tools** / **Edit Mode tools**）に入ります。
折りたたみを開いても、モードが違うオペレータはそれぞれの `poll()` でグレーアウトします。

---

## Prepare

CLO 書き出しをベイク元として整える工程。既定で畳まれています。
**Create Flat SK** は選択中の全メッシュに、それ以外はアクティブオブジェクトに効きます。

| ボタン | すること | 前提 | 結果 |
|---|---|---|---|
| **Create Flat SK** | 選択中の各メッシュを UV アイランド境界で分割し、平面レイアウト `(u, v, 0)` をシェイプキー `<UV名>_Flattened` に書く。既定で三角化してから実行 | Object Mode / メッシュを 1 つ以上選択 | シェイプキーが増え、値 1.0 で表示される。UV レイヤの無いオブジェクトはスキップ。Guide が選択に含まれていれば **Flat SK** 欄が新しい名前に更新される。Basis は触られない |
| **Find Folds** | 二面角が **Crease Min Angle** 以上の辺を crease としてタグ付け。平面シェイプキーがある場合、表示中のキーに関係なく Basis 形状で測る | アクティブメッシュ（どちらのモードでも可） | タグ数が結果行に出る。ライブの Solidify は見ない |
| **Show** | crease タグの付いた辺を選択して見せる | Edit Mode | 選択が置き換わる |
| **Mark** | 選択辺を手で crease にする（**Inset Line** が拾う） | Edit Mode / 辺を選択 | タグが増える |
| **Untag** | 選択辺のタグを外す | Edit Mode / 辺を選択 | タグが減る |
| **×** | crease タグ（辺属性 `ac9_crease_kind`）を全消し | アクティブメッシュ | タグが全部消える |
| **Inset Line** | 折れ線を両側にインセット。**Width** より近い元頂点を線上へ吸収したうえで、線を左右 **Width** の 2 列にベベルし、crease 自体は中央列として残す。2 頂点以上選択されていればそれを使い（**Extend Along Fold** で線に沿って外へ辿る）、無ければ **Find Folds** のタグを使う | Edit Mode / 平面シェイプキー必須 | 帯ができる。結果行に線の頂点数・修復した辺・吸収数・除去した針・帯幅の達成率（最小値つき）が出る |
| **Inset Pieces** | 型紙ごとに、外周から **Width** 以内の頂点を外周へ吸収してから外周を **Width** インセットし、縫い目も自由辺も区別なく平行な頂点列を作る | Object Mode / 平面シェイプキー必須 | 結果行に型紙数・吸収頂点数・除去した針・統合した外周スリバー・広げた角・スリット先端・帯の面数が出る |
| **Width** | インセットの半幅（メッシュ単位、既定 0.001 = 1 mm）。この距離より近い元頂点は先に吸収される。大きいほど陰影のグラデーションが緩くなる | — | 上の 2 つのインセットに効く |

**実行順の注意**: **Inset Line** を **Inset Pieces** より先に実行してください。帯が、まだインセットされていない外周まで届く必要があります。
パネルの行番号 1 Flat SK / 2 Folds / 3 Lines / 4 Pieces が正です。

Solidify はこの工程を通じてライブのモディファイアのままにしてください。書き出しはリトポの Guide でもあり、適用するとそれが壊れます。

### Prepare Settings（子パネル）

| 設定 | 効く先 |
|---|---|
| **Crease Min Angle** | **Find Folds** の閾値（度、既定 60）。60 なら Solidify の rim（90°）と押した折り目を拾い、ドレープのしわは拾わない |
| **Extend Along Fold** | **Inset Line** で、選択の両端から折れ線に沿って外へ辿るか（既定 ON）。2 頂点選ぶだけで線全体を処理できる |
| **Fold Min Angle** | 辿るとき、二面角がこの角度以上の辺だけを折れ線とみなす（度、既定 6）。柔らかい折り目では下げる |
| **Line Profile** | **Inset Line** の帯の断面（既定 1.0）。1.0 は crease をそのまま残し左右を平坦にする、0.5 は帯幅で折り目を丸める |

---

## Setup

全ツールが共有する入力と、他パネルが依存する解析。

| 項目 | すること | 前提 | 結果 |
|---|---|---|---|
| **Retopo** | これから作る低ポリメッシュを指定 | メッシュオブジェクト | 全パネルの対象が決まる。ここが空だと **Faces** 以降は赤い警告になる |
| **Guide** | 3D 形状（Basis）と平面レイアウト（Flat SK）の両方を持つ三角形メッシュを指定 | メッシュオブジェクト | 全解析の読み元になる |
| **Flat SK** | 平面レイアウトのシェイプキー名。Guide にシェイプキーがあれば検索ドロップダウンになる | Guide 指定済み | 例: `UV_Map_Flattened` |
| **Analyze Guide** | Seams → Symmetry（Folds + Twins）→ Anchors をまとめて実行。Guide の読み取りのみ | Guide と Flat SK | 下に `Seams … · Anchors … · Ghosts … · Folds … · Twins …/…` の 1 行が出る |
| **×** | 全解析（Seams とそこから作ったゴースト、Folds、Twins、Anchors）を消す | — | 描いていたオーバーレイが空になる |

**Seams の解析はファイルを開いたときと Reload Scripts で消えます。** そのときは赤い「Seams not analyzed」が出るので、もう一度 **Analyze Guide** を押してください。

<!-- screenshot: Setup パネル（Retopo / Guide / Flat SK と Analyze Guide、解析結果の行） -->

### Analysis Settings（子パネル）

3 つの解析を個別に実行する行と、その調整値。Guide ごとに 1 度決めればほぼ触りません。

| ボタン | すること |
|---|---|
| **Seams → Analyze** / **×** | Guide 上の縫い合わせペアを Flat SK 空間で見つけ、縫い目オーバーレイを作る。ゴースト・スナップ・縫い目オーバーレイの全てがこの結果を読む |
| **Symmetry → Analyze** / **×** | Folds（1 アイランド内の対称軸）と Twins（左右で鏡になる別アイランド同士）の両方を検出。**Generate** は Folds を使って軸上に頂点を置き、**Self** / **Twin** は片側からもう片側を作り直す |
| **Anchors → Analyze** | Guide のアンカー点（3 枚以上の型紙が集まる点、縫い目が自由辺に変わる点）と、それが外周を切り分けるスパンを表示。スパンは密度編集の単位。読み取り専用 |

調整値は次のとおりです。

- **Analyze Seams**: **Merge Precision** / **3D Match Distance** / **Marked Seams (Sharp)**（ON のとき **Marked Seam Distance** が有効）
- **Ghosts**: **Max Seam Distance** / **Bond Distance**
- **Analyze Symmetry**: **Symmetry Tolerance** / **Twin Tolerance**

Folds と Twins は 1 つのボタンで検出されますが、許容値は別々に持っています。同じ正規化をしていても、ダーツのある型紙で再検証せずに統合すると Twin の判定が過剰または不足になりうるためです。

---

## Boundary

リトポの 2D 外周（境界線）を作り、縫い目の両側で頂点を揃える工程です。
Object Mode では外周全体、Edit Mode では選択した箇所を扱います。

### Object Mode tools（外周全体）

**Generate** / **Sync** / **Status** は実行のたびに Guide のスパン（**Generate** は Folds も）を計算し直すので、キャッシュされた解析に依存しません。

| ボタン | すること | 前提 | 結果 |
|---|---|---|---|
| **Boundary → Generate** | Guide の外周（縫い目で区切られた各スパン）に沿って、リトポの境界頂点列を生成する。両側を同時に、スパンに沿って同じ位置に置くので 3D で同じ点に来る | Object Mode / Guide + Retopo | 既に境界がある部分は触らない。間隔・個数・対象・角の閾値は **Boundary Settings** の **Generate** 欄 |
| **Sync → Check** | 何が追加されるかだけ報告する | 同上 | メッシュは変わらない |
| **Sync → Sync** | 縫い合わされる 2 辺の両側で頂点数を揃える。足りない側に足りない分だけ挿入し、既存頂点は動かさない | 同上 | 挿入位置は「チェーンの端の外側」優先。既存頂点の間に割り込むと隣のクワッドが n-gon になるので、その件数を報告する。頂点を減らしたり均したりはしない（それは Edit Mode の **Density**） |
| **Verify → Status** | 全部の縫い目をリトポと照合する。緑 = 両側が対応、赤 = 両側にあるが数や位置が合わない、オレンジ = 片側だけ | Object Mode / Guide + Retopo | Seam Status オーバーレイで色が付き、問題の縫い目の頂点が選択され、全件の表がテキストブロック `AC9_SeamStatus` に書き出される。報告される 2 つの数値は align（対応頂点が縫い目に沿ってどれだけ離れているか）と offset（リトポが Guide の外周からどれだけ外れているか） |

**Faces に進む前の合格判定は Status です。**

### Edit Mode tools（選択した箇所）

| ボタン | すること | 前提 | 結果 |
|---|---|---|---|
| **Density → − 1** / **+ 1** | 選択スパンの頂点を 1 つ減らす / 増やす。**縫い目の両側同時** | Edit Mode / 境界の辺（または内部の境界頂点 1 つ）を選択 | 面を壊さない局所編集を先に試し、無理なスパンだけ作り直す（作り直すと Preview Fill は消える）。Pin と Corner は動かない |
| **Density → Even Out** | 個数を変えず間隔だけ均す。頂点を足しも消しもせず、面にも触らない | 同上 | 既存頂点が移動するだけ |
| **Count → Set** | 指定個数にする | 同上 | 上と同じ非破壊優先 |
| **Spacing → Apply** | 指定間隔（mm）にする | 同上 | 同上 |
| **Selected → Check** | 作られる位置と Pin される頂点を報告するだけ | Guide + Retopo（Edit / Object どちらの選択でも読む） | メッシュは変わらない |
| **Selected → Sync+Pin** | 選択頂点の対岸（縫い合わせ相手側）に対応する頂点を 1 つ作り（既にあれば見つけ）、**両側とも Pin** する | 同上 | ナイフカットで外周に置いた頂点の相棒を作るピンポイント版。実作業は Object Mode で行われる |
| **Pin → +** / **−** / **×** | 選択頂点を Pin / Pin を外す / 全 Pin を外す | Edit Mode / Retopo を選択 | Pin = 「この頂点は意図して置いた。**Density** で消したり滑らせたりしないでほしい」という印。面が付いた後のカット頂点は形状からは普通の頂点と区別できないので、自動判定ではなく手動の印。Pin が安全なのは同じ 3D 位置が縫い目の反対側でも Pin されているときだけ（**Sync+Pin** が両側同時にやる） |
| **Corner → +** / **−** / **×** | 選択頂点を Corner にする / 外す / 全部外す | Edit Mode / Retopo を選択 | Corner = 「ここで型紙を区切り、エッジフローを曲げてよい」という印。リトポのメッシュ属性に保存されるのでファイルを閉じても残る |
| **Corner → Detect** | 型紙自身の幾何から Corner の候補を提案する（**Boundary Settings** の **Candidate Angle**） | Object Mode / Guide + Retopo | 提案するだけで印は付かない。境界生成が区切りを打つのと同じ測り方なので、提案には必ず頂点が載っている。載る頂点が無かった件数は報告される |
| **Snap (G) → Ghost** | G キーでの移動時、**Snap Distance** 以内のゴースト点（対岸の対応点）だけにスナップする | Edit Mode / ゴーストがある | **Outline** と排他 |
| **Snap (G) → Outline** | G キーでの移動時、Guide の型紙外周線（縫い目 + 自由辺）の最近点にスナップする | Edit Mode / 縫い目の解析済み | 境界頂点を外周線に落とすのに使う。Folds があれば **Fold** トグルも出る |
| **Bond → Force Bond** | 選択頂点を、**Bond Distance** 以内の最も近いゴーストへ正確にスナップする | Edit Mode / ゴーストがある | 非破壊。範囲内にゴーストが無い選択頂点と、非選択の頂点は触られない |

**Detect** で拾えるのは裾の直角や襟の先端のような幾何的な角です。「脇縫いの途中でフローを変えたい」といった角は幾何では分からないので手で **+** してください。

### Boundary Settings（子パネル）

| 設定 | 意味 |
|---|---|
| **Generate**（scope） | **All Empty Seams** = リトポが無い縫い目を全部 / **Nearest to 3D Cursor** = 3Dカーソルに一番近い 1 本だけ |
| **What**（targets） | **Seams + Free Edges**（全部の型紙境界を閉じる。面を張る前提） / **Sewn Seams Only** / **Free Edges Only**（裾・開き・ネックラインなど） |
| **Divide By** | **Spacing** = 長さの違う縫い目でも密度が揃うよう個数を決める / **Count** = どの縫い目も同じ個数 |
| **Spacing (mm)** / **Vertices** | 上の選択に応じて片方が出る |
| **Ignore Under (mm)** | これより短い縫い目は無視する |
| **Corner Angle** | 角とみなす角度 |
| **Sync** → 許容値 | **Sync** の一致判定 |
| **Corners** → **Candidate Angle** | **Detect** の閾値 |

---

## Faces

外周ができたあと、2D の内側を埋めて整える工程。

### Object Mode

| ボタン | すること | 前提 | 結果 |
|---|---|---|---|
| **Regions → Connect** | 開いた線を最後のセグメント方向へ延長して最初にぶつかった辺まで繋ぎ（短いものから順に）、そのうえで閉じた領域すべてに面を張る | Object Mode（Edit Mode 版もある） | **外周は分割しない**。外周に届いた線は最も近い既存の境界頂点に繋ぐので、縫い目の再 Sync は不要。ナイフで切れる状態になる。5 mm 未満の突起（ナイフの切りすぎ）は自動で除去され、位置が報告される |
| **Symmetry → Twin** | 選択頂点があるアイランドを正として、対になるもう片方のアイランドを作り直す | Guide + Retopo / 頂点を 1 つ選択 | 破壊的（対象側の頂点と面を全部消して作り直す）。実行時に Guide の対称性を再検出するので事前準備は不要。Ctrl+Z で戻せる |
| **Symmetry → Self** | 1 枚のアイランドの、選択頂点がある側を正として、折り軸の反対側を鏡像で作り直す | 同上 / **Self axis** を先に決める | 破壊的。軸を間違えると「何も変わらない」破壊的no-opになる |
| **Self axis** | **Self** が使う折り軸。**Auto**（左右非対称なほう＝まだ作業が残っているほうを選ぶ。両方とも同程度に非対称なら拒否）/ **Vertical axis (mirror left-right)** / **Horizontal axis (mirror top-bottom)** | — | 押す前に見えている必要がある設定なので、独立した行になっている |
| **Subdivide** | リトポを 2D で単純分割し、新しい境界頂点を Guide の縫い目線へスナップして 3D へ再投影する | Object Mode / 2D 状態のみ（`AC9_3D_Project` が表示状態だと動かない） | 破壊的で解像度が恒久的に上がる。新頂点も Guide 表面に投影されるのでスムーズ処理は不要。Mirror があれば自動で更新される。縫い目の解析が無い場合は動くがスナップを飛ばす（情報行で警告） |

### Edit Mode

| ボタン | すること | 前提 | 結果 |
|---|---|---|---|
| **Regions → Connect** | 上と同じ。ただし選択があるアイランドだけを対象にする | Edit Mode / アイランドのどこかを選択 | 何も選択していないと警告で中止 |
| **Rows → Connect Rows** | 2 本の線の辺を選ぶと、頂点対ごとに桟を通し、その間の面を切り抜いて列に割る（手で J を繰り返すのと同じこと） | Edit Mode / Retopo 自身を編集中 / 2 本の線の**辺**を選択 | 2 本は自動で端から端に対応付けられるので、描いた向きは問わない。頂点数が違う場合、少ない側の広い隙間に、多い側の間隔から取った頂点が足される（既存頂点は動かない）。**外周はこの方法では分割されない**（外周の頂点は 3D で相手側と対応しているため。そちらは **Density** の仕事） |
| **Symmetry → Twin** / **Self** / **Self axis** | Object Mode 側と同じ。**Twin** / **Self** はどちらのモードの選択も読むので両側に置かれている | — | Edit Mode は元になるアイランドを選ぶ場所 |

### Face Settings（子パネル）

Experimental が ON のときだけ出ます。**Quad Fix**（**Non-planar Angle** / **Flat Angle** / **Only Selected** / **Fix Saddles**）、**Preview Fill**（**Fill Spacing (mm)** / **Edge Clearance**）、**Grid Regions**（**Grid Spacing (mm)** / **Side Smoothing** / **Fill Mismatched Regions**）の調整値です。

### Drape Merge（子パネル）

Experimental が ON のときだけ出ます。[05_experimental.md](05_experimental.md) を参照。

---

## 3D View

Mirror（2D リトポの閲覧専用 3D 表示）と、Guide の 2D / 3D 状態、そして **Finalize**。

| ボタン | すること | 前提 | 結果 |
|---|---|---|---|
| **Mirror → Refresh** | いまの 2D レイアウトを Guide 表面へ投影して Mirror を作り直す | Guide + Flat SK + Retopo / Object Mode か Edit Mode | Retopo は平面のまま編集可能。Mirror はビューアなので、そこへの編集は次の Refresh で上書きされる。ビューポートヘッダーにも同じボタンがある |
| **Auto-refresh** | 2D リトポの Edit Mode を抜けたときに自動で **Refresh Mirror** を走らせる（既定 OFF） | — | **Apply 3D Edits → 2D** の未適用の移動が Mirror に載っているときは、黙って捨てないよう警告付きでスキップされる。その場合は手で **Refresh** |
| **Show** | Mirror の表示 / 非表示。削除ではなく非表示 | Mirror が存在する | Mirror はワールド原点にあり上面図で平面レイアウトに重なるので、2D 作業中は邪魔になりがち。作り直すには Refresh のコストがかかるため、消さずに隠す |
| **ゴミ箱アイコン** | Mirror を削除する | Mirror が存在する | **Refresh** でいつでも作り直せる |
| **Guide → To 3D** / **To 2D** | Guide を平面レイアウト（作業モード）と 3D 衣装形状（参照モード）で切り替える | Guide + Flat SK / Flat SK が Guide に存在する | **To 3D** は Flat SK を 0 にし、Guide（親コレクション含む）を表示し、**透視投影のビューポートに限って** Wireframe オーバーレイを切る（平行投影の 2D 作業ビューは触らない）。**To 2D** は Flat SK を 1 に戻し、表示状態と Wireframe も元に戻す |
| **Mirror** / **Guide**（入れ替え） | Guide（高ポリの 3D 衣装）と Mirror（投影したリトポ）のどちらを表示するか切り替える。ボタンに出ているのは「切り替え先」の名前 | Guide が 3D（Flat SK = 0）かつ Mirror が存在 | 表示が入れ替わる |
| **Contact → Check** / **Separate** / **×** | Guide Separate（Experimental）。[05_experimental.md](05_experimental.md) 参照 | Experimental ON | — |
| **3D Source** | 投影とマップが Guide の 3D 形状として読むシェイプキー。**Original**（Basis、書き出したままのドレープ。最終リトポ用）/ **Separated**（`AC9_Separated`、ベイク中用） | Experimental ON / 分離済み | 切り替えると Guide の三角形キャッシュが破棄される。Mirror と投影は次の **Refresh** で追いつく |
| **Align → To Outline** | Guide の型紙外周（UV シーム辺）から **Threshold** 以内の各頂点について、2D 位置を辺上へスナップし、バリセントリック座標の 1 つがちょうど 0 になるようアタッチメントを作り直す。境界フラグも立てる | Guide + Retopo | 旧 **Sync 3D > 2D** が外周にぴったり留めるための前処理。**Sync 2D > 3D** のあと 1 度実行する |
| **Threshold** | 上のスナップ許容距離（既定 0.003 = 3 mm） | — | 大きすぎると内部頂点まで境界と誤判定される |
| **Finalize → Finalize** | Retopo + Guide の投影を素のメッシュ `<Retopo名>_Final` に焼く。形状 = Guide への投影、UV = 2D レイアウト、シェイプキーなし、`ac9_*` データなし | Object Mode / Retopo・Guide・Flat SK が揃っている | Retopo は触られない（平面のまま、編集可能なまま）。何度でも実行できる。作られた `_Final` が選択・アクティブになる |

### Separation Settings（子パネル）

Experimental が ON のときだけ出ます。**Gap** / **Smooth Radius** / **Max Iterations** / **Include Solidify**。

---

## Guide Maps

診断用のベイク。既定で畳まれています。結果は固定名の画像に入るので、Image Editor で開いておくと再ベイクごとに更新されます。

| ボタン | すること | 前提 | 結果 |
|---|---|---|---|
| **Resolution** | ベイク画像のサイズ（正方形）。**1024**（速いプレビュー）/ **2048**（推奨、既定）/ **4096**（遅い、最終確認用） | — | 下 3 つのベイクに効く |
| **Residual → Bake** | Guide 表面から現在のリトポまでの符号付き距離。赤 = Guide が手前、青 = 奥、白 = 一致、暗い灰 = リトポにまだ覆われていない | Object Mode / Retopo + Guide | 画像 `AC9_ResidualMap`。結果行に RMS / p90 / max（mm）と覆われた頂点数が出る |
| **Sag → Bake** | 型紙（縫い目で区切られたアイランド）ごとの平面フィットからの符号付き距離。白 = 手前に膨らむ、黒 = 奥に沈む、中間の灰 = 平面上 | Object Mode / Guide | 画像 `AC9_SagMap`。等高線が低周波のたわみに沿うエッジループの流れになる。結果行に型紙数と最大偏差 |
| **Drape → Bake** | Guide の平面レイアウトに AO × Curvature（Dirty Vertex Colors）を陰影として焼く。両方を別々に焼いてシェーダで乗算するのと同じことを 1 クリックで行う | Object Mode / Guide | 画像 `AC9_DrapeMap`。2D でナイフを入れるときの当たりに使う |
| **Preview** | Preview Plane が表示するマップ（**Residual** / **Sag** / **Drape**） | — | 切り替えるとプレビュー用マテリアルの参照画像が差し替わる |
| **Plane** | Flat SK 空間に 1×1 m のプレーン `AC9_BakePreview` を作る（あれば再利用）。Emission マテリアルなので Image Editor 無しで見られる | Object Mode | Solid シェーディングのビューポートを **Solid の色 = Texture** に切り替え、**X-Ray** を ON にする（プレーンは平面リトポの 5 mm 下にあるため）。Material / Rendered のビューポートは触らない |
| **Solid** | ビューポート自身の Solid カラーソース（Viewport Shading > Color と同じプロパティ） | 3Dビューポート内 | **Texture** になっていないとマップは見えない。ここに出しているのはその設定を知らない人が多いため |

進捗表示は段階的です。ベイク本体（`bpy.ops.object.bake`）は途中の割合を返さないので、その区間は前後を括った表示になります。**Drape** は内部で 2 回ベイクします。

### Map Settings（子パネル）

| 設定 | 意味 |
|---|---|
| **Residual Scale** | 残差マップが完全な赤 / 青に飽和する距離（mm、既定 10）。**反復の間は固定しておくこと。**そうすれば「前より白い」が本当に近づいたことを意味する |
| **Coverage Margin** | Guide 頂点が 2D 平面上でリトポのフットプリントからどれだけ離れていても「覆われている」とみなすか（mm、既定 2）。これを超えると残差マップは暗い灰になる |
| **Sag Scale** | 平面フィット偏差が純白 / 純黒になる距離（mm、既定 20）。中間の灰が平面上 |

---

## Overlays

ビューポートに描かれるものを全部ここで切り替えます。同じ内容がビューポートヘッダーのポップオーバーからも開けます。

以前は 13 個のトグルが各作業パネルに散っていました。初回は読みやすくても、「境界作業」から「3D確認」へ切り替えるたびに 4 箱を探し回ることになるので、ここに集めてあります。

### マスタースイッチ

**Overlays: ON** / **Overlays: OFF** — このアドオンの**全**オーバーレイの親スイッチ。
OFF にすると毎フレームの描画コールバックが冒頭で打ち切られるので、「全部隠す」ボタンであると同時に軽量化のスイッチでもあります（選択ゴーストや縫い目パリティの重い毎フレーム計算が丸ごと省かれます）。

### Presets

押すと、そのプリセットで ON にすべきトグルだけが ON になり、それ以外は OFF になります（前の状態を引き継がず、必ず既知の状態に着地します）。
**All Off** 以外はマスタースイッチも ON にします（見せてほしいという要求なのに何も起きないように見えるのを避けるため）。

| プリセット | 意味 |
|---|---|
| **Boundary** | 2D 境界の作業: 縫い目ガイド、選択ペアのゴースト、頂点数（パリティ）、ステータス |
| **Seams** | Guide の縫い目構造を読む: 縫い目、ペアライン、Folds、Twins、白い外周線、ゴースト |
| **Mirror** | Mirror を 3D で見る: 白い外周線と境界フラグ |
| **All Off** | 個別のオーバーレイを全部 OFF にする（マスタースイッチは触らない） |

**Selection Link** は意図的にプリセットの管理外です。どの作業モードでも欲しい（2D の選択が Mirror のどこにあるか、その逆を見る手段）ため、プリセットは触りません。

### トグル

| 箱 | トグル |
|---|---|
| **Seam Lines** | **Seams (cyan)** / **Pair Lines** / **Fold Lines** / **Twins (magenta)** / **Outline (white)** |
| **Marks** | 見出しに **Analyze Anchors** の更新ボタン。**Anchors** / **Corners** / **Pins**。**Anchors** を ON にしたのに解析が無いと赤い警告が出る。解析済みならアンカー数とスパン数が出る |
| **Ghosts** | 見出しに手動更新（**Refresh Ghosts**）と **×**（**Clear Ghost Points**）。**Selected Only** / **Points**（ON のとき **Only Unplaced** が有効）/ **Lines** / **Snap Radius** |
| **Status** | **Vertex Counts** / **Seam Status** / **Boundary Flags** |
| **Mirror** | **Selection Link** — 相手オブジェクト側の対応頂点にオレンジのマーカーを描く。頂点 / 辺 / 面 / ループ / 最短経路の選択に対応 |
| **Guide** | **Islands** スライダ（**Alpha**）と **Bake** / **×** — Guide の UV アイランドを検出して頂点色属性に書き、それを表示する簡単なマテリアルを作る。GPU オーバーレイではないが「Guide に何が見えているか」という同じ問いなので、ここに置かれている |

ここのほとんどは **Analyze Seams** が埋めるキャッシュから描いています。そのキャッシュはファイル読み込みと Reload Scripts で空になるので、空のときはこのパネルにも「Seams not analyzed」と **Analyze** ボタンが出ます。

<!-- screenshot: Overlays パネル（プリセットと各トグルの箱） -->

### Appearance（子パネル）

色・線幅・マーカーサイズだけの飾りの設定です。ここを触っても解析は走りません。
**Seam** / **Seam Width** / **Ghost (Unplaced)** / **Ghost (Placed)** / **Ghost Line** / **Ghost Cross Size** / **Ghost Line Width** / **Fold** / **Fold Width** / **Anchor** / **Anchor Cross Size** / **Link Point Size** / **Boundary Cross Size** / **Z Offset**。

---

## Advanced

旧ワークフロー、保守、診断、打ち切った実験の置き場。既定で畳まれています。
作業フェーズのパネルに今の手順で使うものだけを残すため、ここに分けてあります。

| 項目 | すること | 前提 | 結果 |
|---|---|---|---|
| **Legacy Flip**（見出し以下） | 旧方式（リトポ自身が 2D / 3D をシェイプキーで行き来する）。**Apply 3D → 2D**、**2D > 3D** / **3D > 2D**、**Bind → New Verts** / **Auto** | Experimental ON | 推奨は 2D 編集 + **Refresh Mirror**。[05_experimental.md](05_experimental.md) 参照 |
| **Legacy Flip Options** | **Overwrite ShapeKey** / **Clear Failed Group** / **Select Failed** | 常に表示 | 上の旧ワークフローの挙動を決める |
| **Maintenance → Guide Cache → Clear** | 現在の Guide の三角形リストと 2D BVH のキャッシュを破棄する。次の Sync 系操作で自動的に再構築される | — | データブロックを差し替えずに Guide のジオメトリ（頂点位置）を編集したあと（スカルプトや頂点編集のあと）に実行する。縫い目線と境界のオーバーレイのキャッシュも一緒に無効化される |
| **Maintenance → Attachments → Clear** | リトポから頂点ごとのアタッチメントデータ（`ac9_tri_idx` / `ac9_bary_u` / `ac9_bary_v` / `ac9_status`）を消す | — | 別の Guide に貼り替える前に使う |
| **Diagnostics → Seam → Vertex** | リトポの頂点 1 つを選んで実行すると、対応する縫い目ペアをシステムコンソールに出力する | 頂点 1 つを選択 | 既知のペアが想定の場所に来ているかの確認用 |
| **Diagnostics → Seam → Spaces** | 検出した縫い目ペア、リトポ境界頂点、Guide の平面レイアウトのワールド空間バウンディングボックスをコンソールに出力する | — | 縫い目の箱とリトポの箱が重なっていなければ座標空間が違う（`matrix_world` / 正規化の不一致） |
| **Diagnostics → Twins → Mapping** | リトポの各アイランドを Guide のアイランドに対応付けて報告する（全件の表はテキストデータブロック）。必要なら **Detect Twins** を先に走らせる | — | 読み取り専用。メッシュには何も書かない。下に Twin ペアの一覧と、ペアにならなかったアイランドの理由（too small to test / no matching partner found）が並ぶ |
| **Ghosts → Live Ghost Update** | 編集を確定するたびにゴーストを再計算するかどうか | — | — |
| **Settings → Reset to Defaults** | AC9 プロパティツリーの調整値を全部既定に戻す | 確認ダイアログあり | Retopo / Guide / Flat SK の指定、結果行、メッシュデータは触らない。変更した件数が報告される |
| **Done with this file → Clear All AC9 Data** | このファイルからアドオンが書いたものを全部取り除く | Object Mode | 押すと、消えるものの一覧がダイアログに出る（下記） |

### Clear All の範囲

消えるもの:

- シーン設定（Retopo / Guide / Flat SK と全オプション。これが消えるとヘッダーのボタンも出なくなります）
- Mirror オブジェクト（`ac9_mirror_of` プロパティで探します）
- Bake Preview Plane（`AC9_BakePreview` のオブジェクトとメッシュ、マテリアル）
- 全メッシュ上の `ac9_*` / `AC9_*` 属性レイヤ（アタッチメント、ステータス、Pin、Corner、grid / fill / scaffold のマーカー、ベイク用の色レイヤ）、`AC9_3D_Project` と `AC9_Separated` シェイプキー、頂点グループ `AC9_Project_Failed`、`AC9_Island_Colors` のマテリアルスロット
- シーン・オブジェクト・メッシュ上の `ac9_*` カスタムプロパティ
- ベイク画像、ベイク用の一時マテリアル、レポート用のテキストデータブロック

残るもの: **自分のジオメトリ、UV、自分のマテリアル、Guide の Flat シェイプキー。**

例外が 1 つあります。旧シェイプキーで 3D 表示中（値 > 0.5）のリトポは、キーを消すと 2D レイアウトに戻ってしまいます。
そこだけは見えているものが失われるので、消す前に 3D 座標をメッシュへ書き込みます。画面に見えているものがそのまま残ります。
