# Windows RC検証結果 — 2026-09-08

> この文書は初期WGC導入時の記録です。最新の判定・配布前検証・実プレイチェックリストは[最終RC検証](rc-final-validation.md)を参照してください。

対象はユーザー提供 `ac6-winloss-tracker-wgc-worktree.zip` の未コミット変更・未追跡ファイルを含む作業ツリー。基点はa6b7994、旧mainへの再実装はしていない。

## 判定

ローカル非プレイ検証を通過したRC検証候補。正式リリース／mainへのmergeはまだ推奨しない。GitHub CIの当該差分での実行とAC6実プレイが残る。「残りは実プレイだけ」とはまだ判定しない。

## 引き継ぎ実装と追加修正

- Alt+Tab対策のAC6専用HWND WGC、安全な前面MSSフォールバック、演出PNG（既定OFF）を引き継ぎ。
- High相当のリスク: ネイティブ側で遅れた画像をコールバック到着時刻で新しい証拠として扱えていた。QPC由来の取得時刻で古さを検証し、プレイ証拠の有効時間を延長しないよう修正。実DWMでは提示予定時刻が到着より約11～13ms先になることを実測。最大50msの先行を到着時刻に切り詰め、それ以上の未来時刻を拒否する。副作用は取得遅延が大きい画像の安全側の取りこぼし。
- High相当のリスク: 子がNone応答だけを返し続ける映像停止でWGCが再接続されず、バックグラウンド認識が継続して失われる。5秒の有効フレーム監視と、回収後10秒の再試行を追加。静止画面でも更新がない場合は再接続する。
- Medium: WGC再試行待ちのMSS中に矩形が変わった場合、候補／CLEAR途中状態を継承する可能性を除去。ソース切替と同様に中断する。
- テストの修正: bootstrapテストが実行前から存在した他の一時フォルダーを自分の回収漏れと誤認していた。今回作成した残留だけを検査し、既存フォルダーを削除しない。
- 分類器、ResultGate、確認ヒット数、UNDO/RESET、終端ロック、CLEAR再アーム条件は変更していない。

## Windowsで実施した検証

| 検証 | 結果と限界 |
|---|---|
| CPython x64 3.13.15 / 3.14.7 | 専用検証環境。現行配布方式と同じ直接Python起動で全26スクリプト成功。opt-in WGCテスト1件は通常スイートでskipし、別途両Pythonで成功 |
| キャプチャ／保存の専用単体テスト | 18+7ケース成功。WIN/LOSS/DRAW、取得ギャップ、古い証拠、逆行、候補／CLEAR中断、Job失敗時の開始拒否、回収不能時の新規spawn拒否、8秒保存worker上限、旧設定、encoder/rename失敗、二重保存拒否 |
| 実Windows Job Object | 親のos._exit後の子回収、通常のspawn/reap、12回反復の子・ハンドル非増加 |
| 実WinAPI/WGC/DWM | ダミーTkウィンドウの実HWND/PID/矩形特定、WGC画素とQPC時刻、実MSS合成PNG、被覆拒否、最小化時対象除外（3.14）、回収。AC6のGPU描画や本物のOverlayの動作証明ではない |
| 保存先 | 実Known Folder APIで絶対パス取得。日本語・OneDrive/デスクトップ相当の一時パスでPNG保存。ユーザーのDesktopには保存していない |
| 起動・終了・二重起動 | 既存Launcher/Overlay/shutdown/ownership/先行初期化テスト成功。fake serverと実OSプロセスを使用 |
| 設定・データ | 既存config/history/stats/移行・破損復旧・UNDO/RESET回帰成功。実ユーザー戦績は変更していない |
| 依存 | 両Pythonで全6パッケージのWindows wheelをrequire-hashes付きでインストール、pip check、tkinter/mss/PIL/ttkbootstrap/windows_capture/numpy/cv2 import成功 |
| 脆弱性 | pip-audit 2.10.1でrequirements.lockの全6パッケージに既知脆弱性なし（検査時点） |
| PowerShell | 7と5.1でruntime policy/bootstrap/README/uninstallerの既存4テストすべて成功。構文確認とrelease script/checksum assets生成成功 |
| 耐久・診断 | 既存60.5秒SSE接続/スレッド安定性、最新ROI一件、12回子回収。WGCタイムアウト／再試行理由をログ追加。長時間AC6/GPU負荷は未検証 |

Python仮想環境のredirector経由ではLauncherのPID一致テストが失敗した。現行インストーラはvenvを使用しないため、本検証は直接Python起動へ合わせた。venv配布への変更や既存別ブランチのownership修正統合は今回行っていない。

## CI・配布で残る条件

- GitHub Actionsは当該差分では未実行。ローカル結果をCI成功とは扱わない。
- workflowにWindows依存import、追加回帰テスト、RCブランチpushトリガーを追加。実WGCはGPU/対話デスクトップ依存のためworkflow_dispatchの `native_wgc` を明示ONした場合のみ実行し、失敗を成功へ丸めない。
- GitHub CI実行にはRCブランチへのcommit/pushが必要。main merge、tag、Release公開はしていない。
- install/updateの検証は既存の隔離fixtureと実依存解決であり、この候補版の公開Releaseを使うネットワーク経由の全行程インストールではない。現行v1.0.1を参照するversion/tagは変更していない。公開時に次の版番号・tag・配布assetの対応と、新規インストール／更新を確認する必要がある。

## 次のAC6実プレイ確認

1. ボーダーレスSDRでWIN/LOSS/DRAW、次試合での再アーム、1試合1カウント、UNDO/RESET後のロック。
2. 結果直前／途中のAlt+Tab。別アプリにWIN/LOSS画像を表示しても加算しない。短い最小化からの復帰と、5秒を超える停止を区別する。
3. 演出設定OFF/ON、通常milestoneと50連勝の描画待ち、実HUD/演出を含む1枚だけのPNG、実OneDrive Desktop。
4. 排他的フルスクリーン、HDR、混在DPI／複数モニター。安全側の保存見送りやWGC未取得を記録する。
5. ゲーム再起動、Detector無効/有効、Tracker/Overlay二重起動、通常終了／強制終了後に子が残らない。

時刻の根拠: [Microsoft SystemRelativeTime](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture.direct3d11captureframe.systemrelativetime)、[CPython monotonic clock](https://docs.python.org/3/library/time.html#time.monotonic)、[windows-captureのPython時刻引き渡し](https://github.com/NiiightmareXD/windows-capture/blob/main/windows-capture-python/src/lib.rs)。固定2.0.1 wheelでのWindows実行も上記のとおり検証した。
