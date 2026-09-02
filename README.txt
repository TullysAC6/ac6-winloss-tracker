AC6 Win/Loss Tracker Stable v1.0.0
==================================

ARMORED CORE VI の対戦結果を画面認識し、WIN / LOSE / 勝率 / 連勝を自動計測する
Windows 11向けローカルツールです。テレメトリやYouTubeコメント機能はありません。

【推奨インストール / 更新】
Windows PowerShellへ次の1行をそのまま貼り付けて実行してください。管理者権限、Git、GitHub CLI、
独自バイナリの実行は不要です。

  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Invoke-Expression (Invoke-RestMethod 'https://raw.githubusercontent.com/TullysAC6/ac6-winloss-tracker/refs/heads/main/install.ps1')"

【何がインストールされますか？】
- AC6 Win/Loss Tracker本体
- デスクトップショートカット
- 必要な対応Pythonがない場合は、公式Python 3.14

インストーラーはGitHub APIでmainのHEADを1回だけ解決し、40桁SHAを検証して、その変更不能な
リビジョンのZIPを取得します。Python Software Foundationの有効なAuthenticode署名を確認し、
Python 3.14を優先します。3.14がなく、検証可能なPython 3.13がある場合はそれを使用できます。
同じ系列に複数候補がある場合は新しいversionを選択します。3.12以下しかない場合は既存Pythonを
消さず、wingetから公式Python 3.14をユーザー領域へ追加します。python.exeとpythonw.exeの署名が
一致しない候補、prerelease、free-threaded buildは使用しません。Windows x64が正式検証済みです。
venvや独自バイナリは使用しません。
AppLocker、WDAC、Group PolicyでPowerShellやPythonが制限されている場合、そのポリシーを迂回せず
管理者へ確認してください。インストーラーはExecutionPolicyを永続変更しません。

インストール先:
  %LOCALAPPDATA%\Programs\AC6WinLossTrackerSource\

ユーザーデータ:
  %LOCALAPPDATA%\AC6WinLossTracker\

設定、現在戦績、累計履歴、診断データはソース本体とは分離されています。再インストールや更新時も
config.json、stats.json、history.db、diagnostics\ は削除しません。更新は新ソースの取得・検証と
依存確認が完了してから行い、起動確認に失敗した場合は直前のソースとショートカットへ戻します。
更新後のショートカットは、選択された検証済みRuntimeのpythonw.exeを直接使用します。

【既にPythonがあります】
Trackerは既存Pythonを削除しません。インストーラーがPython 3.14を追加した場合も、他のアプリが
利用する可能性があるため、Trackerのアンインストール時にPythonを自動削除しません。

【アップデート方法】
初回と同じ「推奨インストール / 更新」のPowerShell 1行をもう一度実行してください。

【アンインストール】
uninstall.ps1はrelease候補に含まれています。main昇格前のため、正式なPowerShell 1行コマンドは
まだ公開していません。main昇格後に正式導線として案内します。

通常アンインストールではアプリ本体とデスクトップショートカットだけを削除し、戦績・設定・診断は
残します。完全削除はuninstall.ps1をローカル保存して -RemoveUserData を明示した場合だけ選べ、
実行前にPowerShellの確認が表示されます。

【戦績は消えますか？】
通常アンインストールではhistory.db、config.json、stats.json、diagnostics\を保持します。
再インストール後も過去戦績を利用できます。-RemoveUserDataを明示して確認した場合だけ削除します。

【Pythonは消えますか？】
消えません。Trackerは既存Pythonも、セットアップ時に追加したPython 3.14も自動削除しません。

【使い方】
セットアップ完了後はデスクトップの「AC6 WinLoss Tracker」を開きます。起動中にもう一度開くと
ダッシュボードを表示できます。OBSブラウザソースは次です。

  http://127.0.0.1:8765/

【カウントの安全設計】
- 手動WIN / LOSE加算経路はありません。
- 結果は複数回連続検出後に確定します。
- 確定後はLOCKし、安定したCLEARが連続成立するまで次試合へ再アームしません。
- DRAWも同じCLEARロックを通過します。
- UNDO / RESETは誤検出修正用で、戦績を増加させる操作ではありません。

【診断ZIP】
誤検知や起動失敗を報告する場合は Create-Diagnostic-Report.bat を実行してください。デスクトップに
AC6-Tracker-Diagnostics-YYYYMMDD-HHMMSS.zip が作られます。検出ログ、結果判定領域だけの画像、
設定・戦績、アプリ/OS/Python/SQLite/依存ライブラリ/ディスプレイ概要が含まれます。ユーザー名を
含む完全パスや画面全体は収集せず、ZIPをユーザー自身が提出しない限り外部送信しません。

【リリース方針】
Stable v1.0.0は、検証済みbaseline
6b8dcdd818ec9c5b6e81450fb955d1451a5dc540を基礎にしたPythonソース配布です。
正式な導入・更新経路は、このREADME冒頭のPowerShell 1行へ一本化しています。

開発者向けテスト:
  python tests/run_all_tests.py
