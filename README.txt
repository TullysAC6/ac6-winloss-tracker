AC6 Win/Loss Tracker
====================

ARMORED CORE VI の対戦結果を画面認識し、WIN / LOSE / 勝率 / 連勝を自動計測するWindows向けツールです。
YouTubeコメント機能は含みません。

【一般ユーザー向け / GitHub Releases版】
1. AC6-WinLoss-Tracker-Windows.zip を展開
2. AC6-WinLoss-Tracker.exe をダブルクリック
3. AC6を起動してプレイ

以上です。Python、pip、setup.bat は不要です。
初回起動時に必要な設定・戦績ファイルは自動生成されます。
データ保存先:
  %LOCALAPPDATA%\AC6WinLossTracker\

OBSブラウザソース:
  http://127.0.0.1:8765/

【カウントの安全設計】
- 手動 WIN +1 / LOSE +1 API は存在しません。
- WIN/LOSEは自動検出経路からのみ加算されます。
- 結果は複数回連続検出してから確定します。
- 一度結果を確定すると detector は LOCK 状態になります。
- cooldown経過だけでは再受付しません。
- 結果画面後に安定した CLEAR が連続成立するまで次試合へ再アームしません。
- DRAWも確定後は同じCLEARロックを通過するまで次結果を受け付けません。
- UNDO/RESETは修正用であり、戦績を手動で増加させる機能ではありません。

注意:
完全なローカルアプリなので、PC所有者自身によるバイナリ改造やデータ直接編集まで暗号学的に防止するものではありません。
本設計の目的は、通常操作による手動加算経路をなくし、誤検出・二重加算を強く抑制することです。

【誤検知・取りこぼしを報告する場合】
Release ZIPにある Create-Diagnostic-Report.bat をダブルクリックしてください。
デスクトップに次のようなZIPが作成されます:
  AC6-Tracker-Diagnostics-YYYYMMDD-HHMMSS.zip

報告時は以下の2点だけ添えてください:
- おおよその発生時刻（例: 21:43頃）
- 実際の結果とツールの結果（例: 実際はLOSEだがWIN +1された）

診断ZIPには以下が含まれます:
- detector.jsonl / detector.previous.jsonl
  判定状態、スコア、CLEAR/LOCK状態などの循環ログ
- roi/*.png
  FINAL候補または怪しい却下時の「結果判定領域だけ」の画像
- config.json
- stats.json
- manifest.json（アプリ/OS情報）

画面全体のスクリーンショットは自動収集しません。
診断データはローカル保存され、ユーザーが自分でZIPを提出しない限り外部送信されません。

【開発者向け】
ソースから実行する場合のみ Python 3.10+ が必要です。
  setup.bat
  start.bat

Windows EXEをローカルビルド:
  build_release.bat

GitHubではタグ v* をpushすると .github/workflows/build-release.yml がWindows runnerでEXEを生成し、
AC6-WinLoss-Tracker-Windows.zip をReleaseへ添付します。

主要データ:
  %LOCALAPPDATA%\AC6WinLossTracker\config.json
  %LOCALAPPDATA%\AC6WinLossTracker\stats.json
  %LOCALAPPDATA%\AC6WinLossTracker\diagnostics\
