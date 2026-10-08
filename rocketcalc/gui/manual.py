"""アプリ内マニュアル（目次・本文・検索）。

本文の書き方（簡易マークアップ）:
  "## 見出し"   小見出し
  "- 項目"      箇条書き
  "1. 項目"     番号付き
  "> 文"        注意書き（色付き）
  "**語**"      太字
  空行          段落の区切り
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk

from .widgets import fit_geometry, set_window_icon

SECTIONS: list[tuple[str, str]] = [
    ("はじめに", """
このツールは、モデルロケットの形を部品から組み立てて、安定性・空気抵抗・飛び方（軌道と姿勢）・パラシュートでの回収までを計算するアプリです。

## 画面の見方
- **左側**: 入力欄。「機体構成」タブで機体の形を、「モーター・飛行条件」タブでモーターや天候を設定します。
- **右側**: 計算結果。タブを切り替えて、機体図・空力・飛行解析・3D 姿勢ビュー・回収・モーターを見ます。
- **一番下**: ステータスバー。安定比・到達高度などの要点と、入力エラーが表示されます。

入力を変えると、約 0.4 秒後に自動で再計算されます。飛行計算は裏で行うので、計算中も操作できます（ステータスバーに「飛行を計算中…」と出ます）。

## まず試すこと
1. メニューの「テンプレート」から好きな機体を選ぶ。
2. 右の「機体図・安定性」タブで、安定比が緑（適正）になっているか確認する。
3. 「3D 姿勢ビュー」タブで ▶ 再生 を押して、飛び方を見る。
4. 「モーター・飛行条件」タブの「遅延を最適値にする」を押して、パラシュートが頂点付近で開くようにする。

> 計算結果はあくまで目安です。実際に打ち上げるときは、所属する団体や法令の安全規則に従ってください。
"""),
    ("機体を組み立てる", """
機体は「部品」を組み合わせて作ります。左の「機体構成」タブの一覧で部品を選ぶと、その下に設定欄（プロパティ）が出ます。

## 部品の操作
- **＋ 部品を追加**: 種類を選んで追加します。胴体系の部品は、選んでいる部品のすぐ後ろに入り、直径は前の部品に合わせられます。
- **複製 / 削除**: 選んでいる部品をコピー・削除します。
- **▲ ▼**: 順番を入れ替えます。胴体系の部品は、一覧の上から順に先端から積み重なります。

## 胴体系の部品（先端から順に積み重なる）
- **ノーズコーン**: 形状はオジャイブ・コーン・放物線・楕円・ハーク・べき乗。ハークは C=0 でフォン・カルマン、C=0.333 で LV-Haack。
- **ボディチューブ**: 円筒。太さの違うチューブを重ねれば段付きの機体になります。
- **トランジション**: 円錐台。太くする（肩）にも、細くする（ボートテール）にも使えます。
- **球・楕円体**: 長さ＝直径にすると球。球体型ロケット用で、揚力は出さず、空気抵抗が大きい物体として計算します。
- **自由形状ボディ**: 「x, 直径」を mm で 1 行ずつ書いて、好きな外形を作ります。直径 0 から始めるとノーズになり、同じ x を 2 行書くと段差になります。

## フィン
- **平面形**: 台形・楕円・自由形状。自由形状は「x, y」を mm で書きます（根元前縁 (0, 0) から外周を回って、根元後縁 (根元翼弦長, 0) で終わる。y は胴体表面からの高さ）。
- **枚数 / 1 枚目の向き**: 0° が真上、90° が右。3 枚・4 枚フィンは等間隔に並びます。
- **位置**: 「機体後端から（後縁まで）」か「先端から（前縁まで）」で指定します。
- **断面**: 四角・前縁を丸める・翼型。空気抵抗が変わります。
- **取付角（キャント）**: 0 以外にすると機体が回転（スピン）します。
- **取付迎角**: 機体の「上」（0° 方向）に揚力を出す角度。飛行機型の主翼用です。
- フィンセットはいくつでも付けられます（カナード、主翼＋尾翼など）。

## 質量
- 各部品の質量は、**材質と肉厚から自動で推定**します。実際に量った値があれば「質量」欄に入れてください（空欄なら推定値）。
- **質量（おもり・搭載物）**: パラシュート・ペイロード・機首おもりなどを、位置（先端から mm）と一緒に追加します。
- 一覧の一番上の「機体全体」を選ぶと、**実測した機体質量・重心（モーター除く）**で上書きできます。完成した機体を指の上でバランスさせて、先端から重心までを測ってください。

## 機体全体の設定
- **Cd を手動指定**: 空欄なら形状から自動推定。風洞や実測の値がある時だけ入れます。
- **表面粗さ / 追加 Cd**: 塗装の仕上がりや、ランチラグ等の抵抗を加味します。
"""),
    ("飛行機型・球体型などの特殊な形", """
## 飛行機型
- 主翼: フィン 2 枚、1 枚目の向き 90°（左右に水平）。
- 水平尾翼: フィン 2 枚、向き 90°、機体後端。
- 垂直尾翼: フィン 1 枚、向き 0°（真上）、機体後端。
- 主翼に「取付迎角」を付けると揚力が出て、経路が曲がります。

水平な主翼は上下の安定（ピッチ）にだけ、垂直尾翼は左右の安定（ヨー）にだけ効きます。そのため、非対称な機体では安定比を**ピッチとヨーで別々に**表示します。機体図の下段（上から見た図）で主翼の形を確認できます。

## 球体型
- 先頭に「球・楕円体」、その後ろにモーターチューブ（細いボディチューブ）、そこにフィンを付けるのが基本です。
- 球は揚力を出さないので、安定はフィンと重心の位置で決まります。機首おもりで重心を前にすると安定します。
- 全長が直径の 5 倍未満のずんぐりした機体は、cal では判定が厳しく出すぎるため、**安定比 %（全長比）で判定**します。

> モーターが機体の外形からはみ出すと、安定性の表に注意が出ます。モーターチューブを追加してください。
"""),
    ("モーター・飛行条件", """
左の「モーター・飛行条件」タブで設定します。

## モーター
- **モーター**: 内蔵の Estes A8 / B6 / C6 / D12 から選ぶか、.eng ファイルを読み込みます（「モーター」の章を参照）。
- **放出遅延**: 燃焼が終わってからパラシュートを放出するまでの秒数。下の候補はそのモーターで選べる値です。
- **遅延を最適値にする**: 頂点で放出される遅延に、いちばん近い市販の値を設定します。
- **後端からの突出量**: モーターが機体後端からはみ出す長さ。

## 回収（パラシュート）
- **直径 / Cd**: 0 にするとパラシュートなし（弾道落下）。Cd は平面円形で 0.75〜0.8、ドーム型で 1.2〜1.5 くらいです。
- **ショックコード長**: ノーズと機体をつなぐひも。これが伸びきるまでが「ノーズ分離」です。
- **開傘時間**: 0 なら直径と速度から自動で推定します。

## 打ち上げ条件
- **ランチロッド長 / 発射角**: 発射角は鉛直から風上側へ傾ける角度です。
- **平均風速 / 突風の強さ**: 突風は平均風速に対するばらつき（%）。0 で一定の風になります。
- **突風パターン番号**: 変えると別の突風パターンで計算します（結果のばらつきを確認できます）。
- **推力のずれ角**: モーターの取付誤差。姿勢の乱れ方を見たい時に使います。
- **気温**: 空気の密度と音速に影響します。
"""),
    ("モーターを複数にする（クラスター）", """
「モーター・飛行条件」タブの「モーターの本数（クラスター）」で設定します。

## 設定
- **本数**: 1〜9 本。1 本なら機体の中心に付きます。
- **配置**: 「円周上に並べる」か「中心に 1 本＋周り」。
- **周りのモーター**: 「中心に 1 本＋周り」の時、周りを別の種類にできます（例: 中心 C6 ＋ 周り A8）。
- **配置半径**: 機軸からモーター中心までの距離。0 なら、重ならない最小の半径で隙間なく並べます。
- **1 本目の向き**: 0° が真上。フィンの間に並べたい時などに使います。
- **点火しない本数**: 点火不良の想定です。周りのモーターから順に止まり、推力が偏って機体が曲がる様子を計算できます。点火しないモーターも重りとして積んだまま飛びます。

## 計算のしかた
- 推力・質量は全モーターの合計です。種類が違うモーターは、それぞれの推力曲線で燃えます。
- 中心からずれたモーターの推力は、機体を回すモーメントとして 6 自由度計算に入ります（全部点火して対称に並んでいれば打ち消し合います）。
- 放出遅延の候補は「主」のモーターのものを使います。
- 太い機体が必要です。テンプレートの「太胴（クラスター用 φ50）」で 3〜4 本を試せます。

> モーターが機体より太くはみ出す、またはモーター同士が重なる時は入力エラーになります。機体図では 1 本ずつ点線で表示され、点火しないモーターは赤い点線になります。
"""),
    ("安定性（安定比 cal と %）", """
「機体図・安定性」タブで確認します。図の右下に、判定の色付きで安定比が表示されます（緑＝適正、橙＝注意、赤＝不安定）。

## 用語
- **CG（重心）**: 青い丸。打上げ時（モーター満載）と燃焼後（推進剤を使い切った時）の 2 つを示します。
- **CP（圧力中心）**: 赤い丸。空気の力がかかる中心です。
- **抗力中心**: 紫の三角。空気抵抗がかかる中心で、CG より後ろなら安定側に働きます。

## 安定比
- **安定比 cal** = (CP − CG) ÷ 機体の直径。1〜2 cal が目安です。
- **安定比 %** = (CP − CG) ÷ 全長 × 100。10〜20 % が目安です。
- どちらも CP が CG より後ろ（プラス）でないと、機体は回転して安定して飛びません。
- 細長い機体は cal、ずんぐりした機体（球体型など）は % の方が実態に合います。
- 大きすぎると風に向かって曲がりやすく（風見効果）、到達高度が下がります。

## 安定比が足りない時
- 機首におもりを足して CG を前に出す。
- フィンを大きくする、または後ろに付ける。
- 重いモーターに変えると CG が後ろに下がり、安定比が小さくなることに注意（モーター比較表で確認できます）。

> 飛行中も推進剤が減って CG が動くので、安定比は変化します。飛行解析の表と 3D 姿勢ビューで、飛行中の値も確認できます。
"""),
    ("結果のタブ", """
## 機体図・安定性
上が横から見た図、下が上から見た図です（幅の広い機体は左右に並びます）。表には、安定比、CP・CG の位置、部品ごとの揚力（CNα）の寄与が出ます。

## 空力・質量
- 左のグラフ: 速度ごとの空気抵抗係数 Cd の内訳（表面摩擦・底面・フィンなど）。
- 右のグラフ: 迎角（機体が斜めに風を受ける角度）が大きくなると CP がどう動くか。
- 下の表: 50 m/s での Cd の内訳と、部品ごとの質量・重心。

## 飛行解析（6自由度）
高度・速度・迎角・加速度・機軸の傾き・回転の速さ・経路（横から／上から）のグラフと、主な結果の表です。
- **初期加速度**: ランチロッド上の平均加速度。4 G 以上が目安です。加速度グラフの橙の帯がロッド上の区間です。
- **推力重量比**: 平均推力 ÷ 重量。5 以上が目安です。
- **ランチロッド離脱速度**: 15 m/s 以上が目安。足りなければロッドを長くします。
- **最大迎角**: 上昇中に機体がどれだけ斜めに風を受けたか。大きいと姿勢が乱れています。
- **開傘衝撃**: パラシュートが開く時の衝撃。放出が早すぎると大きくなります。

## 回収
パラシュートの直径と降下速度の関係のグラフです。降下速度は 3〜6 m/s が目安。「目標降下速度」を入れると必要な直径が出て、「この直径を使う」で設定に反映できます。
"""),
    ("3D 姿勢ビュー", """
飛行を 3D で再生します。左が軌道全体、右が機体の拡大です。

## 操作
- **▶ 再生 / ⏸ 停止**、**⏮** で最初に戻る。スライダーで好きな時刻に移動できます。
- **速度**: ×0.1〜×5。開傘の様子は ×0.1 がおすすめです。
- **機体の拡大**: 軌道全体ビューで機体を何倍に描くか。「自動」は見やすい大きさにします。
- **カメラ追従**: 機体を中心に表示し続けます。
- **ジャンプ**: 動き出し・ロッド離脱・燃焼終了・頂点・放出・ノーズ分離・パラシュート全開・着地へ移動します。
- 図をマウスでドラッグすると視点を回転できます。

## 拡大ビューの見方
- **青い線**: 機体に当たる気流（● が下流側）。機体と気流がずれている角度が迎角です。
- **緑の破線**: 進行方向。
- **橙の炎**: 噴射中。炎の長さは推力の大きさです。
- 回収時は「放出の煙 → ノーズがショックコードで飛び出す → パラシュートが引き出されて開く → 機体が吊り下がる」までを表示します。

右下の「現在の状態」に、その時刻の高度・速度・加速度・迎角・傾き・ロール・推力・安定比が出ます。
"""),
    ("モーター", """
## .eng ファイルの読み込み
「ファイル → モーターデータ (.eng) を読み込み」か、「モーター」タブのボタンから読み込みます。thrustcurve.org などで配布されている RASP 形式（.eng）に対応しています。1 つのファイルに複数のモーターが入っていても読み込めます。

> 内蔵の Estes のデータは近似値です。正確に計算したい時は .eng ファイルを使ってください。

## モーター比較
「全モーターで比較」を押すと、今の機体で各モーターを飛ばした結果（安定比・初期加速度・到達高度・最高速度・ロッド離脱速度・最適遅延）が一覧になります。機体に入らないモーターや、安定比が小さくなるモーターには ✖ が付きます。
"""),
    ("保存・テンプレート", """
## 保存と読み込み
- **ファイル → 名前を付けて保存 / 上書き保存（Ctrl+S）**: 機体・飛行条件・読み込んだ .eng モーターを、まとめて .json ファイルに保存します。
- **ファイル → 開く（Ctrl+O）**: 保存したファイルを開きます。
- **ファイル → 新規**: 標準の機体に戻します。

## テンプレート
メニューの「テンプレート」から、いろいろな形の例を読み込めます: 標準 3 枚フィン、ボートテール＋楕円フィン、カナード付き、大径ペイロード（段付き）、スピン（フィン取付角 2°）、自由形状、太胴（クラスター用 φ50）、球体型、飛行機型、不安定な例。

> テンプレートを読み込んでも「モーター・飛行条件」は変わりません。必要に応じて「遅延を最適値にする」を押してください。
"""),
    ("計算方法", """
## 圧力中心（CP）
- 胴体: 細長物体理論（任意の外形を数値積分。Barrowman 法の一般化）。
- フィン: Diederich の式（任意の平面形を短冊に分割）＋ 胴体との干渉。1 枚ずつ取付方向を考え、ピッチ面とヨー面で別々に求めます。
- 迎角が大きい時は胴体の揚力（Galejs）も考慮します。球・楕円体は揚力なしとします。

## 空気抵抗
表面摩擦（レイノルズ数・表面粗さ）、底面、フィンの前縁・後縁、段差・傾斜面、球・楕円体の形状抵抗を積み上げます。燃焼中はモーターの噴射で底面の抵抗が減ります。

## 質量
部品の材質と肉厚から、質量・重心・慣性モーメントを推定します（実測値で上書き可）。推進剤は力積に比例して減るとします。

## 飛行
6 自由度（位置 3 + 回転 3）の剛体モデルを RK4 法で計算します。
- 迎角に応じた力による復元モーメント、空気とジェットによる減衰。
- フィンは 1 枚ずつ、局所の気流（機体の回転分を含む）から力を求めます。
- 突風（ばらつきのある風）、推力のずれ、ランチロッドでの拘束。
- 大気は国際標準大気（地上気温のみ変更可）。

## 回収
放出 → ノーズ分離（ショックコード長 ÷ 放出速度）→ 開傘（抵抗の面積が時間の 2 乗で増える）→ 降下。開傘衝撃は、この間の最大加速度です。

> 音速より十分遅い飛行が前提です（マッハ 0.6 を超えると警告します）。
"""),
    ("困ったとき", """
## 「入力エラー」と出る
ステータスバーに、どの欄が問題かが出ます。数値の欄に文字が入っていないか、フィンが機体からはみ出していないか（位置と根元翼弦長）を確認してください。入力が不正な欄は文字が赤くなります。

## 「推力不足で離陸できません」
機体が重すぎるか、モーターが小さすぎます。軽くするか、大きいモーターにしてください。

## 機体がくるくる回る（迎角が 90° を超える）
安定比がマイナスか小さすぎます。「安定性（安定比 cal と %）」の章の対策をしてください。テンプレートの「不安定な例」で、どうなるかを見られます。

## 3D 表示がカクカクする
ウィンドウを小さくするか、再生速度を上げてください。拡大ビューは機体の向きだけを描き直しているので、通常は滑らかに動きます。

## パラシュートが上昇中に開く / 落下中に開く
放出遅延が合っていません。「遅延を最適値にする」を押してください。飛行解析の表に、頂点の何秒前／後に放出したかが出ます。
"""),
]


def _plain(text: str) -> str:
    return text.replace("**", "")


class ManualWindow(tk.Toplevel):
    """目次 + 本文 + 検索のマニュアル画面。"""

    def __init__(self, master, section: str | None = None):
        super().__init__(master)
        self.title("マニュアル - モデルロケット設計ツール")
        set_window_icon(self)
        self.geometry(fit_geometry(self, 980, 700))
        self.minsize(700, 450)
        self.index = 0

        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text="検索").pack(side="left")
        self.query = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.query, width=28)
        entry.pack(side="left", padx=4)
        entry.bind("<Return>", lambda e: self.search(next_hit=True))
        self.query.trace_add("write", lambda *_: self.search())
        ttk.Button(top, text="次を検索", command=lambda: self.search(next_hit=True)).pack(side="left")
        self.hit_label = ttk.Label(top, text="", style="Hint.TLabel")
        self.hit_label.pack(side="left", padx=8)
        ttk.Button(top, text="次の章 ▶", command=lambda: self.show(self.index + 1)).pack(side="right")
        ttk.Button(top, text="◀ 前の章", command=lambda: self.show(self.index - 1)).pack(side="right", padx=4)

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)
        toc_frame = ttk.Frame(paned, padding=(6, 0, 0, 6))
        paned.add(toc_frame, weight=0)
        ttk.Label(toc_frame, text="目次").pack(anchor="w")
        self.toc = tk.Listbox(toc_frame, width=26, activestyle="none", exportselection=False,
                              font=("Yu Gothic UI", 10), relief="flat", highlightthickness=0)
        self.toc.pack(fill="both", expand=True)
        for title, _ in SECTIONS:
            self.toc.insert("end", "  " + title)
        self.toc.bind("<<ListboxSelect>>", self._on_toc)

        body = ttk.Frame(paned, padding=(0, 0, 6, 6))
        paned.add(body, weight=1)
        self.text = tk.Text(body, wrap="char", padx=18, pady=12, relief="flat", font=("Yu Gothic UI", 10),
                            spacing1=2, spacing3=2, cursor="arrow")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        t = self.text
        t.tag_configure("h1", font=("Yu Gothic UI", 17, "bold"), spacing1=4, spacing3=10, foreground="#0d47a1")
        t.tag_configure("h2", font=("Yu Gothic UI", 12, "bold"), spacing1=14, spacing3=4, foreground="#1565c0")
        t.tag_configure("bold", font=("Yu Gothic UI", 10, "bold"))
        t.tag_configure("item", lmargin1=12, lmargin2=30)
        t.tag_configure("note", lmargin1=12, lmargin2=12, rmargin=12, background="#fff3e0", foreground="#6d4c00",
                        spacing1=6, spacing3=6)
        t.tag_configure("hit", background="#ffeb3b")
        t.tag_configure("current", background="#ff9800")
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Prior>", lambda e: self.show(self.index - 1))
        self.bind("<Next>", lambda e: self.show(self.index + 1))

        titles = [s[0] for s in SECTIONS]
        self.show(titles.index(section) if section in titles else 0)
        entry.focus_set()

    # ---------------------------------------------------------------- 表示
    def show(self, index: int):
        if not 0 <= index < len(SECTIONS):
            return
        self.index = index
        title, body = SECTIONS[index]
        self.toc.selection_clear(0, "end")
        self.toc.selection_set(index)
        self.toc.see(index)
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("end", title + "\n", "h1")
        for raw in body.strip("\n").splitlines():
            line = raw.rstrip()
            if line.startswith("## "):
                t.insert("end", line[3:] + "\n", "h2")
            elif line.startswith("- "):
                self._insert_inline("•  " + line[2:], "item")
            elif re.match(r"\d+\. ", line):
                self._insert_inline(line, "item")
            elif line.startswith("> "):
                self._insert_inline("⚠ " + line[2:], "note")
            elif not line:
                t.insert("end", "\n")
            else:
                self._insert_inline(line, None)
        t.configure(state="disabled")
        t.yview_moveto(0)
        self._hits, self._hit_pos = [], -1
        self._highlight()

    def _insert_inline(self, line: str, tag: str | None):
        """**太字** を処理して 1 行入れる。"""
        parts = line.split("**")
        for k, part in enumerate(parts):
            tags = tuple(x for x in (tag, "bold" if k % 2 else None) if x)
            self.text.insert("end", part, tags)
        self.text.insert("end", "\n", (tag,) if tag else ())

    def _on_toc(self, _event):
        sel = self.toc.curselection()
        if sel and sel[0] != self.index:
            self.show(sel[0])

    # ---------------------------------------------------------------- 検索
    def _highlight(self):
        t = self.text
        t.tag_remove("hit", "1.0", "end")
        t.tag_remove("current", "1.0", "end")
        q = self.query.get().strip()
        self._hits = []
        if not q:
            return
        start = "1.0"
        while True:
            pos = t.search(q, start, stopindex="end", nocase=True)
            if not pos:
                break
            end = f"{pos}+{len(q)}c"
            t.tag_add("hit", pos, end)
            self._hits.append(pos)
            start = end

    def search(self, next_hit: bool = False):
        q = self.query.get().strip()
        # 目次に該当件数を表示
        for i, (title, body) in enumerate(SECTIONS):
            n = _plain(title + body).lower().count(q.lower()) if q else 0
            self.toc.delete(i)
            self.toc.insert(i, f"  {title}" + (f"  ({n})" if n else ""))
            self.toc.itemconfigure(i, foreground="#1565c0" if n else "#9e9e9e" if q else "#000000")
        self.toc.selection_set(self.index)
        self._highlight()
        if not q:
            self.hit_label.configure(text="")
            return
        total = sum(_plain(t + b).lower().count(q.lower()) for t, b in SECTIONS)
        self.hit_label.configure(text=f"全体で {total} 件" if total else "見つかりません")
        if not next_hit:
            return
        if self._hits and self._hit_pos < len(self._hits) - 1:
            self._hit_pos += 1
        else:
            # 次に該当する章へ
            for k in range(1, len(SECTIONS) + 1):
                i = (self.index + k) % len(SECTIONS)
                if q.lower() in _plain(SECTIONS[i][0] + SECTIONS[i][1]).lower():
                    if i != self.index or not self._hits:
                        self.show(i)
                    self._hit_pos = 0
                    break
        if self._hits:
            pos = self._hits[self._hit_pos]
            self.text.tag_remove("current", "1.0", "end")
            self.text.tag_add("current", pos, f"{pos}+{len(q)}c")
            self.text.see(pos)
