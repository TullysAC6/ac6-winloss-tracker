# Alt+Tab認識と演出スクリーンショット

調査・実装の基準: `origin/main` の `a6b7994cc73e8d1f61b001008b117efe2a092275`。

2026-09-08 Windows追検証: ZIP内の作業ツリーを引き継いだ最新結果・追加修正・未完了条件は[RC検証結果](rc-validation.md)を参照。以下のmacOS検証記録は前回実装時点の記録。

## 原因

旧`ResultDetector.run()`は前面プロセスだけを調べ、AC6以外ならMSSキャプチャを停止していた。同時に`note_foreground(False)`が`last_clear_at`・`last_activity_at`・結果候補・CLEAR連続回数を消去していた。Alt+Tab中の結果表示を読めず、復帰後も`FINAL_AFTER_GAMEPLAY_MAX_SECONDS`判定の証拠が失われるため、残っている結果表示が`no_recent_gameplay`で棄却されていた。

単純な前面チェックの削除では、MSSが別アプリのデスクトップ画素を判定してしまう。そのためウィンドウ専用キャプチャが必要になる。

Overlayは独立したTk/Win32のレイヤードウィンドウであり、Steam側のゲーム撮影経路に合成する処理は存在しない。今回もSteam・DirectXのフック、DLL注入、ゲームメモリ参照は追加していない。

## 勝敗認識の設計

- `WinApi.game_target()`で実行ファイル名、HWND、PID、クライアント矩形、DWM可視枠を確認する。ウィンドウタイトルの部分一致は使わず、複数候補・最小化・取得不能は判定しない。
- `game_capture.py`はWindows Graphics CaptureをHWND指定で起動する。別ウィンドウの取り込みも無効にする。クライアント／可視枠と一致したフレームだけから、既存と同じ20%/43%/60%/7%のROIを切り出す。サイズ不一致は推測補正しない。Overlayと同じper-monitor-v2のDPI設定を使い、物理座標をそろえる。
- ネイティブコールバックは最新ROI一件のみ保持。要求も一件に限定し、画像キューを蓄積しない。同じ／逆行したネイティブ時刻と1.6秒を超えたサンプルを拒否する。WGCのQPC時刻（100ns単位）を判定へ渡し、コールバック遅延・IPC待ちをプレイ証拠に加算しない。DWMの提示予定時刻が到着より最大50ms先の場合は到着時刻へ切り詰め、それ以上の未来時刻は拒否する。
- WGC失敗時は10秒の再試行間隔を設ける。前面がAC6で、上位ウィンドウがROIを覆わず、キャプチャ前後の対象・矩形が一致する場合だけMSSへフォールバックする。バックグラウンドでデスクトップ画素を使うことはない。
- 映像中断・キャプチャ方式変更は、結果候補、初期CLEAR連続回数、再アーム用CLEAR時間、モーション差分を破棄する。直前のプレイ時刻だけを元の5秒上限内で残す。別HWND/PIDへ変われば、その証拠も消す。
- 既存分類器・テンプレート・確認ヒット数・ResultGate・5秒クールダウン・その後の連続5秒CLEAR再アーム条件は維持する。カウント済み／手動操作／UNDO後のロックは映像中断や例外では解除しない。
- DRAWは現行仕様どおり「確認後に終端ロックするがWIN/LOSS/連勝には加算しない」。統計仕様の変更は含まない。

## スクリーンショットの設計

- `effect_screenshot_enabled`は既定OFFのboolean。既存version 17設定に項目がなくても読み込める。Overlayの既存Tk tickから起動し、独自の監視スレッドは増やさない。
- 対象はサーバーが発行する5～50連勝のmilestoneイベント。初回表示後に少なくとも1 tickを空け、通常は開始0.5秒以降、50連勝は2秒以降を対象にする。新規ワーカーは同時に一件だけ。
- ワーカーでMSSのデスクトップ合成画面を撮影する。保存領域はゲームのクライアント全体。撮影前後にAC6が前面であること、同じ対象・サイズ、全領域がデスクトップ内、HUD・演出HWNDの可視状態、他ウィンドウの被覆がないことを確認する。
- `IsWindowVisible`だけでは排他的フルスクリーン上でOverlayが撮れていると断定できないため、PNG化前に実画素にバナーの塗り／枠線が存在することも確認する。映らない演出を後付け合成しない。色変換がある場合は安全側に見送る。
- Known Folder APIでデスクトップを取得する。名前は日時・連勝数・イベントIDのハッシュから作る。イベントIDをパスに直接使用しない。
- 起動前の試行済みフラグ、既存SSEのID重複除外、イベント固有ファイル名、排他的pendingファイル作成で二重保存を防ぐ。完成後にatomic renameするため、強制停止したPNGエンコーダが不完全な最終PNGを公開しない。通常の失敗時にはpendingを削除する。
- 撮影・エンコード・ファイルI/Oの例外はワーカー内、ワーカー起動失敗はOverlayの任意機能境界で捕捉する。stats、history、ResultGateへの書き込み経路は持たない。

## ライフサイクル

キャプチャはDetectorが所有する子プロセス一つ、保存はOverlayが所有する一時子プロセス一つまで。どちらもWindowsのkill-on-close Job Objectへ登録してから処理を開始する。登録不能なら処理開始せず子を回収する。所有プロセスが異常終了してもJobのハンドル閉鎖で子が終了する。

キャプチャの停止・無効化・対象変更では接続を閉じ、短い猶予の後にJob閉鎖／terminate／kill／joinで回収する。回収不能時は新しい子を重複生成しない。要求応答が5秒停止した場合も回収してから再試行する。保存ワーカーは8秒の上限を持ち、演出置換やOverlay終了でも回収する。

応答が続いていても新しい有効WGCフレームが5秒届かない場合は子を回収して再試行する。MSSフォールバック中の矩形変更も連続判定状態を中断する。

サーバーは既存のHTTP bindによる所有権確立後にDetectorを開始する順序を維持し、終了時にDetectorスレッドを最大4秒待ってキャプチャ解放の猶予を与える。既存のOverlay mutex、runtime所有者確認、launcher起動境界、Overlay停止フォールバックは維持する。

## 依存関係

Windows専用依存として`windows-capture==2.0.1`、その依存`numpy==2.5.3`と`opencv-python==5.0.0.93`を固定した。`requirements.lock`にWindows x64 Python 3.13/3.14のwheel SHA-256を追加。インストーラのimport確認にも追加した。アプリ実行時はWGC障害がTracker起動失敗にならないよう子プロセスに隔離している。

## 検証

- 変更前の既存Python回帰スイート: 全成功。
- 変更後: `tests/run_all_tests.py`で既存20スクリプトと新規2スクリプトが全成功。新規17ケース中16成功・Windows専用1ケースskip。新規テストは下記を検証する。
- 取得ギャップ前後のWIN/LOSS/DRAW、Detectorループへの接続、期限切れプレイ証拠、候補ヒットのリセット、安定CLEAR時間の中断、UNDO・終端ロック。
- WGCの専用HWND指定、最新ROI一件の消費、同一／逆行時刻・古い画像の拒否、最小化、対象変更、前面フォールバックの再検査、矩形変更、負のモニター座標。
- 保存ON/OFF、描画待ち、50連勝フラッシュ待ち、イベント重複、起動失敗、書き込み失敗、PNG画素一致、バナー欠落、Alt+Tab競合。
- 実際のspawn子プロセスのタイムアウト回収。Windows専用の親強制終了→Jobによる子回収テストも追加し、既存Windows CIへ含めた（macOSではskip）。
- 既存のルート直下のstrict CLEAR・Overlay静的／ライフサイクルテストも実行。
- PowerShell AST検査と既存4スクリプトのテストをPowerShell 7で実行。
- Windows x64 Python 3.13/3.14の全依存について、`pip download --only-binary=:all: --require-hashes`で解決・ハッシュ検証成功。クロスプラットフォーム検証用コピーではWindows限定マーカーを除去し、Windows wheelを明示的に選択した。

実行環境はmacOS/Python 3.14。Windows GPU・AC6・Tk/DWMの実描画とSteamを使用した実機確認、Windows側pip check/pip-audit、Windows専用親異常終了テストはこの環境では未実行。mockによる画素・API境界検証をWindows実機での成功とは扱わない。

## Windows実機で確認する項目

| 条件 | 確認内容 |
| --- | --- |
| ボーダーレスで通常プレイ | 従来同様に1試合1カウント、次の試合で再アーム |
| 結果直前／途中に別アプリへ切替 | AC6が描画継続中なら認識継続。別アプリにWIN/LOSS/DRAW画像を表示しても加算なし |
| 1ヒット後に短い最小化・復帰 | 復帰後の新規2ヒットと5秒以内のプレイ証拠がある場合のみ受理 |
| 長い最小化・描画停止 | 未取得の結果を推測加算しない。復帰後に新しいCLEAR証拠を待つ |
| 排他的フルスクリーンのAlt+Tab | WGCまたは前面フォールバックの状態と取りこぼし範囲を記録 |
| 通常／50連勝演出、設定ON/OFF | 1イベント1枚、実際のゲームとHUD・演出、50連勝のフラッシュ後を確認 |
| 排他的フルスクリーン／HDR | バナー未取得や色変換では保存見送り。ボーダーレスSDRでも比較 |
| OneDriveデスクトップ・日本語ユーザー名 | 正しいデスクトップへの保存、ファイル名とPNG表示 |
| ディスク不足・書き込み拒否・Alt+Tab中 | 保存失敗してもHUD、カウント、終了処理が継続 |
| Detector無効化、ゲーム再起動、Tracker二重起動・終了・強制終了 | 子プロセス重複／残留なし、既存runtime所有権とmutexを維持 |

## 参考にした一次資料

- [Microsoft: Windows Graphics Capture](https://learn.microsoft.com/en-us/windows/apps/develop/media-authoring-processing/screen-capture)
- [Microsoft: Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
- [windows-capture Python API](https://github.com/NiiightmareXD/windows-capture/blob/main/windows-capture-python/windows_capture/__init__.py)（実装時には取得した2.0.1 wheel内のAPIも確認）
