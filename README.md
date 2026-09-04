# AC6 Win/Loss Tracker

ARMORED CORE VIの対戦結果を画面から自動認識し、勝敗・勝率・連勝・累計履歴を記録するWindows 11向けツールです。ゲーム中の手動入力は必要ありません。

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
$u='https://raw.githubusercontent.com/TullysAC6/ac6-winloss-tracker/refs/tags/v1.0.1/bootstrap.ps1';$p=Join-Path ([IO.Path]::GetTempPath()) ('ac6-bootstrap-'+[guid]::NewGuid().ToString('N')+'.ps1');try{Invoke-WebRequest $u -OutFile $p -UseBasicParsing;if((Get-FileHash $p -Algorithm SHA256).Hash -ne '39E7E8C54239F1FA61666FF4C9199AFF6BF86B5937C7F69C6B14EBBC59D1C9E8'){throw 'bootstrap SHA-256 mismatch'};& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p;$ec=$LASTEXITCODE;if($ec -ne 0){throw "Installer failed with exit code $ec"}}finally{Remove-Item $p -Force -ErrorAction SilentlyContinue}
```

- 管理者権限は不要です。
- GitやGitHub CLIは不要です。
- 必要な対応Pythonは自動で準備されます。
- 更新も同じ1行を実行します。

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

## OBS（任意）

OBSで戦績を配信画面に表示したい場合は、ブラウザソースに次のURLを指定できます。通常の利用では設定不要です。

OBSブラウザソース:

```text
http://127.0.0.1:8765/
```

## アンインストール

Windows PowerShellへ次の1行を貼り付けて実行します。

```powershell
$u='https://raw.githubusercontent.com/TullysAC6/ac6-winloss-tracker/refs/tags/v1.0.1/bootstrap.ps1';$p=Join-Path ([IO.Path]::GetTempPath()) ('ac6-bootstrap-'+[guid]::NewGuid().ToString('N')+'.ps1');try{Invoke-WebRequest $u -OutFile $p -UseBasicParsing;if((Get-FileHash $p -Algorithm SHA256).Hash -ne '39E7E8C54239F1FA61666FF4C9199AFF6BF86B5937C7F69C6B14EBBC59D1C9E8'){throw 'bootstrap SHA-256 mismatch'};& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p -Mode Uninstall;$ec=$LASTEXITCODE;if($ec -ne 0){throw "Installer failed with exit code $ec"}}finally{Remove-Item $p -Force -ErrorAction SilentlyContinue}
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

## Security

配布ファイルは実行前に改ざんがないか検証されます。YouTubeコメント機能や外部へのテレメトリ送信はありません。詳細は[SECURITY.md](SECURITY.md)を参照してください。
