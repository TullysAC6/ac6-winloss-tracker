# AC6 Win/Loss Tracker

ARMORED CORE VIの対戦結果を画面から自動認識し、勝敗・勝率・連勝・累計履歴を記録するWindows 11向けツールです。ゲーム中の手動入力は必要ありません。

> [!IMPORTANT]
> **対応モードは RANK MATCH: SINGLE（ランクマッチ・シングル）のみです。**
>
> CUSTOM MATCH（カスタムマッチ）およびRANK MATCH: TEAM（ランクマッチ・チーム）は現在サポート対象外です。これらのモードでは、WIN / LOSE検出、戦績カウント、Overlay更新、History記録の動作を検証しておらず、動作保証していません。

<img src="docs/images/dashboard-overview.png" alt="AC6 Win/Loss Tracker Dashboard Overview" width="900">

## 主な機能

- WIN / LOSEの自動検出
- 勝率と連勝の記録
- ゲーム内Overlay
- Dashboard
- Lifetime history（これまでの累計履歴）

## インストール / 更新

1. Windows PowerShellを開きます。
2. 次の1行をすべてコピーして貼り付けます。
3. Enterキーを押します。

```powershell
$u='https://raw.githubusercontent.com/TullysAC6/ac6-winloss-tracker/refs/tags/v1.1.0/bootstrap.ps1';$p=Join-Path ([IO.Path]::GetTempPath()) ('ac6-bootstrap-'+[guid]::NewGuid().ToString('N')+'.ps1');try{Invoke-WebRequest $u -OutFile $p -UseBasicParsing;if((Get-FileHash $p -Algorithm SHA256).Hash -ne '2FDE252FA841430C845681BB23860E2D365F8A575CB2ED39515AC5F9F2CB41B7'){throw 'bootstrap SHA-256 mismatch'};& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p;$ec=$LASTEXITCODE;if($ec -ne 0){throw "Installer failed with exit code $ec"}}finally{Remove-Item $p -Force -ErrorAction SilentlyContinue}
```

- 管理者権限は不要です。
- GitやGitHub CLIは不要です。
- 必要な対応Pythonは自動で準備されます。
- 更新も同じ1行を実行します。

> [!NOTE]
> 現在のv1.1.0では専用のPython仮想環境（venv）を使用していません。必要なPythonパッケージはユーザーのPython環境へインストールされるため、他のPythonアプリやスクリプトと依存関係が競合する可能性があります。既存のPython本体はインストール時もアンインストール時も削除しません。専用venvによる完全な環境分離は今後のバージョンで対応予定です。

## 使い方

### 1. Trackerを起動

セットアップ後、デスクトップに作成される「AC6 WinLoss Tracker」ショートカットを開きます。

<img src="docs/images/desktop-shortcut.png" alt="AC6 WinLoss Tracker desktop shortcut" width="99">

Trackerを起動したら、基本的にはそのままARMORED CORE VIをプレイするだけです。WIN / LOSEを手動入力する必要はありません。

### 2. Dashboardを開く

Tracker起動中に「AC6 WinLoss Tracker」ショートカットをもう一度開くと、Launcherが表示されます。

<img src="docs/images/launcher.png" alt="AC6 Win/Loss Tracker Launcher" width="424">

- **ダッシュボードを開く**: Dashboardを表示します。
- **Trackerを終了**: Tracker、Overlay、Dashboardを安全に終了します。
- **閉じる**: Launcher画面だけを閉じます。Trackerは動作を続けます。

## Dashboardの見方

### Overview

<img src="docs/images/dashboard-overview.png" alt="Dashboard Overview showing current session and lifetime records" width="900">

**CURRENT SESSION**は、Trackerを現在起動してからの戦績です。Trackerを終了して再起動すると、新しいセッションが始まります。

- **WIN**: 現在のセッションの勝利数
- **LOSE**: 現在のセッションの敗北数
- **WIN RATE**: 現在のセッションの勝率
- **STREAK**: 現在の連勝数
- **BEST STREAK**: 現在のセッションでの最高連勝数

**LIFETIME**は、過去のセッションを含む、これまで保存された累計戦績です。アプリの更新や通常のアンインストールでは消えません。

- **WIN**: 累計勝利数
- **LOSE**: 累計敗北数
- **WIN RATE**: 累計勝率
- **MATCHES**: 記録された総試合数
- **BEST STREAK**: これまでの最高連勝数

### 履歴

<img src="docs/images/dashboard-history.png" alt="Dashboard match history" width="900">

履歴は新しい試合が上に表示されます。

- **Time**: 試合が記録された日時
- **Result**: WIN / LOSSの試合結果
- **Streak**: その試合終了時点の連勝数

### ゲーム内Overlay

<img src="docs/images/in-game-overlay.png" alt="In-game win loss overlay" width="900">

ARMORED CORE VIが前面にあるとき、現在の戦績をゲーム内Overlayに表示します。試合結果は画面から自動認識されるため、ゲームプレイ中の手動操作は不要です。

### Alt+Tabと勝敗認識

勝敗判定はAC6のウィンドウをWindows Graphics Captureで取得します。他アプリへ切り替えても、AC6が描画を続けていて取得できる間は認識を継続します。別アプリの画面をバックグラウンド判定には使いません。

最小化や排他的フルスクリーンの切替でゲームの描画が停止した場合は、消えてしまった結果を後から復元できません。短い取得中断では直前のプレイ証拠を既存の5秒制限内で保持します。ウィンドウ取得が使えない場合は、AC6が前面で、判定領域を他のウィンドウが覆っていない場合に限り画面キャプチャへ切り替えます。ボーダーレスを推奨します。

### Tracker演出込みスクリーンショット（任意）

Tracker起動中にショートカットをもう一度開き、「正常に起動中です」の画面で「設定」を押します。
「連勝演出時のスクリーンショットを保存する」を切り替えて「保存」すると、再起動せず反映されます。
設定画面の「新しいバージョンを調べる」はGitHubの最新公開Releaseと現在のバージョンを比較して結果を表示します。自動ダウンロード・更新・インストールは行いません。

`%LOCALAPPDATA%\AC6WinLossTracker\config.json`のJSONオブジェクトへ、次の設定を追加すると自動保存が有効になります。既存項目との間にカンマが必要です。省略時・`false`の場合は保存しません。設定の変更は起動中にも反映されます。

```json
"effect_screenshot_enabled": true
```

- 対象は5・10・15…50連勝時のTracker演出です。演出1件につき最大1枚保存します。
- AC6が前面で演出バナーが表示された後、ゲームのクライアント領域に見えているゲーム・HUD・演出をPNGで保存します。50連勝は白／黒フラッシュの後に撮影します。
- 保存先はWindowsのデスクトップです。OneDriveなどへ移動したデスクトップにも対応します。
- ファイル名例: `AC6_2026-09-08_21-30-45_05-WIN-STREAK_<イベント識別子>.png`
- AC6上でユーザーが実際に見ている合成画面を保存します。SteamP2PScanner、NVIDIA、Steam、Discord等のOverlay/windowが見えていても、重なりだけを理由に保存を拒否しません。ただしAC6が前面であること、対象ウィンドウとクライアント領域が撮影前後で一致すること、Tracker演出が表示され実ピクセルで確認できること等の安全条件を満たさない場合は保存を見送ります。プレビュー表示は保存対象外です。
- 排他的フルスクリーンでは外部Overlayが表示／取得されない場合があります。その場合も演出を後付け合成せず保存を見送ります。ボーダーレスを使用してください。HDRなどで演出の色が変換される環境でも保存を見送る場合があります。
- 保存失敗で勝敗カウントやOverlayは停止しません。保存中の強制終了ではデスクトップに`.AC6_...png.pending`が残る場合があります。完成したPNGではなく、削除して構いません。

Steamのスクリーンショット機能は変更しません。Steamで撮影した画像にTracker演出が追加される機能ではありません。

設計と検証内容は[キャプチャ・スクリーンショット変更報告](docs/capture-and-screenshots.md)に記載しています。
Windowsでの追加修正、RC検証結果、未検証項目は[RC検証結果](docs/rc-validation.md)を参照してください。

## OBSで配信画面に表示する（任意）

OBSは任意です。OBSを使用しなくてもTrackerは動作し、自動WIN / LOSE検出とゲーム内OverlayにOBSは必要ありません。

1. AC6 Win/Loss Trackerを起動します。
2. OBSを開きます。
3. 「Sources / ソース」の「+」を押します。
4. 「Browser / ブラウザ」を追加します。
5. URLに次を入力します。

```text
http://127.0.0.1:8765/
```

6. 幅と高さをOBSのキャンバスや配信レイアウトに合わせます。
7. OBS上で必要な位置とサイズに配置します。

ゲーム内OverlayはAC6の画面上に戦績を表示する機能です。OBS Browser SourceはOBSの配信画面に戦績を表示する別の機能で、互いに独立しています。

## アンインストール

Windows PowerShellへ次の1行を貼り付けて実行します。

```powershell
$u='https://raw.githubusercontent.com/TullysAC6/ac6-winloss-tracker/refs/tags/v1.1.0/bootstrap.ps1';$p=Join-Path ([IO.Path]::GetTempPath()) ('ac6-bootstrap-'+[guid]::NewGuid().ToString('N')+'.ps1');try{Invoke-WebRequest $u -OutFile $p -UseBasicParsing;if((Get-FileHash $p -Algorithm SHA256).Hash -ne '2FDE252FA841430C845681BB23860E2D365F8A575CB2ED39515AC5F9F2CB41B7'){throw 'bootstrap SHA-256 mismatch'};& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p -Mode Uninstall;$ec=$LASTEXITCODE;if($ec -ne 0){throw "Installer failed with exit code $ec"}}finally{Remove-Item $p -Force -ErrorAction SilentlyContinue}
```

通常のアンインストールでは次のように処理します。

- アプリを削除
- デスクトップショートカットを削除
- 戦績を保持
- 設定を保持
- Pythonを保持（Pythonは自動削除しません）

保存されたユーザーデータは`%LOCALAPPDATA%\AC6WinLossTracker\`にあり、再インストール後も利用できます。

## トラブル時

インストール先にある`Create-Diagnostic-Report.bat`を実行すると、デスクトップに診断ZIPが作成されます。診断情報は自動送信されません。問題を報告するときだけ、ご自身でZIPを共有してください。

デスクトップの移動先（OneDriveなど）も追従します。取得できない場合の保存先は`%LOCALAPPDATA%\AC6WinLossTracker\`です。
未カウントやPNG未保存を報告する場合は、可能なら再現後にLauncherの「Trackerを終了」で終了してから診断ZIPを作成してください。終了時に直前の検出情報を保存します。強制終了直前のメモリ内情報は復元できません。
ZIPには起動・導入ログ、導入commit情報、依存バージョン、主要ソースのハッシュ、`effect-screenshot.jsonl`とローテーション済みログを含めます。capture失敗、検出候補の拒否、導入ファイルの混在を切り分けるための情報です。全画面画像や認証用runtimeファイルは収録しません。

## Security

配布ファイルは実行前に改ざんがないか検証されます。YouTubeコメント機能や外部へのテレメトリ送信はありません。詳細は[SECURITY.md](SECURITY.md)を参照してください。
