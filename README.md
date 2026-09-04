# AC6 Win/Loss Tracker

ARMORED CORE VIの対戦結果を画面から自動検出し、勝敗・勝率・連勝・累計履歴を記録するWindows 11向けローカルツールです。YouTubeコメント機能や外部へのテレメトリ送信はありません。

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

セットアップ後、デスクトップの「AC6 WinLoss Tracker」ショートカットを開きます。起動中にもう一度開くとダッシュボードを表示できます。

OBSブラウザソース:

```text
http://127.0.0.1:8765/
```

## アンインストール

Windows PowerShellへ次の1行を貼り付けて実行します。

```powershell
$u='https://raw.githubusercontent.com/TullysAC6/ac6-winloss-tracker/refs/tags/v1.0.1/bootstrap.ps1';$p=Join-Path ([IO.Path]::GetTempPath()) ('ac6-bootstrap-'+[guid]::NewGuid().ToString('N')+'.ps1');try{Invoke-WebRequest $u -OutFile $p -UseBasicParsing;if((Get-FileHash $p -Algorithm SHA256).Hash -ne '39E7E8C54239F1FA61666FF4C9199AFF6BF86B5937C7F69C6B14EBBC59D1C9E8'){throw 'bootstrap SHA-256 mismatch'};& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p -Mode Uninstall;$ec=$LASTEXITCODE;if($ec -ne 0){throw "Installer failed with exit code $ec"}}finally{Remove-Item $p -Force -ErrorAction SilentlyContinue}
```

通常アンインストールではアプリとショートカットだけを削除し、戦績・設定・診断データを保持します。ユーザーデータは`%LOCALAPPDATA%\AC6WinLossTracker\`に保存されており、再インストール後も利用できます。Pythonは自動削除しません。

## 主な機能

- WIN / LOSEの自動検出
- 勝率と連勝の記録
- ゲーム内Overlay
- Dashboard
- Lifetime match history
- 安全な更新・再インストール・アンインストール
- CLEAR必須の厳格な1試合1カウント

## トラブル時

インストール先にある`Create-Diagnostic-Report.bat`を実行すると、デスクトップに診断ZIPが作成されます。診断情報は自動送信されません。問題を報告するときだけ、ご自身でZIPを共有してください。

## Security

正式な1行コマンドは、固定されたbootstrapのSHA-256とStable Releaseの配布ファイルを検証してから実行します。詳しい配布・Python Runtime・依存関係の検証方針は[SECURITY.md](SECURITY.md)を参照してください。
