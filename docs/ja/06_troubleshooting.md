# 06. うまく動かないとき

## 赤い「Seams not analyzed」が出てボタンが動かない

**症状**: **Setup** や **Overlays** に赤い警告が出て、オーバーレイが何も描かれない。**Snap (G) → Ghost** / **Outline** を ON にしても効かない。

**原因**: 縫い目の解析結果がメモリ上に無い。この解析はゴースト・スナップ・縫い目オーバーレイの全ての土台ですが、**ファイルを開いたときと Reload Scripts で空になります。**

**対処**: **Setup → Analyze Guide** を押す（警告行の横にある **Analyze** でも同じ）。
**Guide** と **Flat SK** が未設定なら、その前に「Set the Guide and Flat SK first」の警告が出ているのでそちらを先に埋めます。
なお **Generate** / **Sync** / **Status** は実行のたびに Guide のスパンを計算し直すので、この警告が出ていても動きます。

## Inset が「needs a planar shape key」で止まる

**症状**: **Inset Pieces** または **Inset Line** を押すと
`Inset Pieces needs a planar shape key (the UV layout as geometry, e.g. UV_Map_Flattened)`
というエラーが出て中止される。

**原因**: そのオブジェクトに平面シェイプキー（UV レイアウトを幾何にしたもの）が無い。**Prepare** の 2〜4 は手順 1 の結果を必要とします。

**対処**: そのオブジェクトを選んで Object Mode で **Create Flat SK** を実行する。
**Create Flat SK** は選択中の全メッシュに効きます。CLO の書き出しは通常複数オブジェクトなので、まとめて選んで 1 回押せば済みます。
UV レイヤの無いオブジェクトはスキップされる（結果行に `(N skipped: no UV)` と出る）ので、その場合は UV を先に作ってください。

## ナイフ（K）が切れない

**症状**: 外周を作ったのにナイフで切れない。

**原因**: ナイフは面を切る道具で、境界線だけのメッシュには面がない。

**対処**: **Faces → Regions → Connect** を押す。閉じた領域すべてに面が張られ、開いた線も近くの辺まで延長して繋がれます。
少数の面だけ手で張りたい場合は、頂点を選んで Blender 標準の **F**（New Edge/Face from Vertices）でも構いません。
切ったあと **Connect** をもう一度押せば、いまのカットが作る領域に張り直されます。

## Connect のあとに小さな突起が残る

**症状**: **Connect** の実行後、ナイフの切りすぎでできた短い出っ張りが残っている（ように見える）。

**原因**: ナイフはしばしば目的の辺を少し越えて切るため、数 mm の行き止まりの枝（stub）ができます。

**対処**: 対処は不要です。**5 mm 未満の stub は Connect が自動で除去し、除去した位置（mm）を結果メッセージに列挙します。**
5 mm 以上の枝は「意図した線」として扱われ残ります。長い枝を消したいときは手で削除してください。
結果メッセージに「N end(s) left alone」（繋げなかった端）や「N region(s) could not be closed — lines crossing without a vertex?」が出ている場合は、そちらが本当の残り作業です。

## Symmetry の Self を押しても何も変わらない

**症状**: **Faces → Symmetry → Self** を実行しても形が変わらない。

**原因**: 折り軸が **Auto** のまま、あるいは意図と違う軸が選ばれている。1 枚の型紙は両方向に対称なことがあり（ウエストバンドは左右対称でも上下対称でもある）、そのうち片方だけがユーザーの意図したコピーです。
**Auto** は「まだ非対称なほう」を選び、両方が同程度に非対称なら拒否します。

**対処**: **Self axis** を明示的に選んでから押します。

- **Vertical axis (mirror left-right)** — 型紙の縦中心線で折り、左半分を右半分（またはその逆）から作り直す
- **Horizontal axis (mirror top-bottom)** — 横中心線で折り、下半分を上半分（またはその逆）から作り直す

**Self** は破壊的です。軸を間違えると「破壊的なのに見た目が変わらない」結果になるので、押す前に軸を確認してください。
また、正とする側は「選択している頂点のある側」です。信頼できる側の頂点を選んでから実行します。

## ベイクしたマップが見えない

**症状**: **Residual** / **Sag** / **Drape** の **Bake** は成功したのに、ビューポートに何も見えない。

**原因**: Solid シェーディングは既定でテクスチャを表示しません。またプレビュー用のプレーンは平面リトポの**下**（5 mm 下）にあります。

**対処**: 次の順で確認します。

1. **Guide Maps → Preview** で見たいマップを選び、**Plane** を押す。これで `AC9_BakePreview` プレーンが作られ、Solid のビューポートは自動で **Solid の色 = Texture** に切り替わり、**X-Ray** が ON になります。
2. それでも見えないときは、**Guide Maps** パネルの **Solid** の欄が **Texture** になっているか確認します（Viewport Shading > Color と同じプロパティです）。
3. X-Ray が OFF に戻っていないか確認します。プレーンはリトポの下にあるので、リトポが透けないとマップは隠れます。
4. Material Preview / Rendered シェーディングのビューポートは **Plane** が触りません（そのままでも Emission マテリアルなので見えます）。

<!-- screenshot: Viewport Shading の Color = Texture と X-Ray が ON の状態 -->

なお、ベイク中の進捗は段階表示だけです。Blender のベイク本体は途中の割合を返さないため、その区間は止まって見えます（**Drape** は内部で 2 回ベイクします）。

## Mirror が古いままに見える

**症状**: 2D を編集したのに Mirror の 3D 形状が前のまま。

**原因**: Mirror は (Retopo の 2D + Guide) から毎回作り直される派生物で、自動では追従しません。

**対処**: **3D View → Mirror → Refresh**（ビューポートヘッダーの **Refresh Mirror** でも同じ）を押します。Object Mode でも Retopo の Edit Mode でも動きます。
**Auto-refresh** を ON にすると、2D リトポの Edit Mode を抜けたときに自動で走ります。
ただし **Apply 3D Edits → 2D**（Experimental）の未適用の移動が Mirror に載っている場合は、それを黙って捨てないよう警告付きでスキップされます。その場合は手で **Refresh** してください。

**Subdivide** は Mirror が存在すれば自動で更新します（更新に失敗した場合はその旨がメッセージに付きます）。

Mirror を手で編集した場合、その編集は次の **Refresh** で消えます。Mirror はビューアです。読み戻されるのは頂点の選択状態だけです。

## Finalize が押せない

**症状**: **Finalize** がグレーアウトしている。

**原因と対処**: ツールチップに理由が出ます。次の 4 つのいずれかです。

| 表示 | 対処 |
|---|---|
| Exit Edit Mode first (Tab). | Object Mode に戻る |
| Set the Retopo first. | **Setup** の **Retopo** を指定する（メッシュオブジェクトであること） |
| Set the Guide first. | **Setup** の **Guide** を指定する |
| Set the Guide's Flat SK first. | **Setup** の **Flat SK** を指定する |

なお **Finalize** は Retopo を変更しません。作られるのは `<Retopo名>_Final` という別オブジェクトで、何度でも実行できます。

## Clear All を押すと何が消えるのか

**症状 / 疑問**: **Advanced → Clear All AC9 Data** を押すのが怖い。

**対処**: 押すと、**まず消えるものの一覧がダイアログに出ます**（この時点ではファイルは変更されません）。内容を見てから確定できます。Object Mode 専用です。

消えるもの:

- シーン設定（Retopo / Guide / Flat SK と全オプション。これが消えるとビューポートヘッダーのボタンも出なくなります）
- Mirror オブジェクト
- Bake Preview Plane（`AC9_BakePreview` のオブジェクト・メッシュ・マテリアル）
- 全メッシュ上の `ac9_*` / `AC9_*` 属性レイヤ、`AC9_3D_Project` と `AC9_Separated` シェイプキー、頂点グループ `AC9_Project_Failed`、`AC9_Island_Colors` マテリアルスロット
- シーン・オブジェクト・メッシュ上の `ac9_*` カスタムプロパティ
- ベイク画像、ベイク用の一時マテリアル、レポート用テキストデータブロック

**残るもの: 自分のジオメトリ、UV、自分のマテリアル、Guide の Flat シェイプキー。**

例外が 1 つあります。旧シェイプキーで 3D 表示中（値 > 0.5）のリトポは、キーを消すと 2D レイアウトに戻ってしまうため、消す前に 3D 座標をメッシュに書き込みます。画面に見えているものがそのまま残ります。

設定値だけを初期化したいときは **Clear All** ではなく **Reset to Defaults** を使ってください。Retopo / Guide / Flat SK の指定とメッシュには触りません。

## Experimental のボタンが見つからない

**症状**: マニュアルにある **Grid** / **Drape Merge** / **Quad Fix** などが見当たらない。

**原因**: **Experimental tools** が OFF（既定）。

**対処**: **Edit > Preferences > Add-ons > AC9 Cloth Retopo** で **Experimental tools** を ON にします。
**Advanced** パネルの末尾にも同じ案内のヒント行が出ています。
F3 検索から呼んでも、オペレータ自身が Experimental を見ているので OFF のままでは動きません。

## Preview Fill / Drape Merge が「shapely is required」で止まる

**症状**: エラーメッセージに `shapely is required for ... but could not be imported.` と出る。

**原因**: shapely は Blender に同梱されておらず、この 2 機能だけが必要とします。

**対処**: Blender の Python に入れます。

```
<blender>/python/bin/python -m pip install shapely
```

他の機能は shapely 無しで動きます。詳細は [02_install.md](02_install.md)。

## Guide を編集したらオーバーレイと投影がずれた

**症状**: Guide の頂点をスカルプト / 編集したあと、縫い目線や投影が古い形のまま。

**原因**: Guide の三角形リストと 2D BVH がキャッシュされている。データブロックを差し替えずにジオメトリだけ変えた場合、自動では気づきません。

**対処**: **Advanced → Maintenance → Guide Cache → Clear**。次の Sync 系操作で自動的に再構築され、縫い目線と境界のオーバーレイのキャッシュも同時に無効化されます。

## 縫い目とリトポの位置がまるごと合わない

**症状**: オーバーレイの縫い目線がリトポと全然違う場所に描かれる。

**原因**: 座標空間の不一致（`matrix_world` や正規化のずれ）の可能性があります。

**対処**: **Advanced → Diagnostics → Seam → Spaces** を実行し、システムコンソールに出力される 3 つのバウンディングボックス（縫い目ペア、リトポ境界頂点、Guide の平面レイアウト）を見ます。
縫い目の箱とリトポの箱が重なっていなければ、別の座標空間にいます。
特定の 1 ペアだけを確認したいときは、リトポの頂点 1 つを選んで **Diagnostics → Seam → Vertex**。
