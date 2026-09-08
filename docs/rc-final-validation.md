# 最終RC・非プレイ検証（2026-09-09）

基準: `0afbe71e5689e079b10e25be6a50a40e3266be2a`、ブランチ `codex/wgc-rc-validation-20260908`。
旧mainへの再実装・main merge・tag作成・Release公開は行わない。

## 判定

非プレイ検証を通過した、実AC6確認待ちの候補。
当該commitの **Windows tests 必須ジョブ成功**と、末尾の実プレイチェックをRelease判断の条件とする。
実AC6で報告されたPNG未保存は解決済みと断定しない。
第三者の0/0報告は非公式導入が判明しており、製品バグ確定や分類閾値変更の根拠にしない。

## レビューと限定修正

新規High以上の勝敗・キャプチャ・所有管理・配布処理の不具合は今回の検証では見つからなかった。
診断には次の不足があり、分類／ResultGate／CLEAR／WGC安全条件を変えず補った。

- 診断ZIPが新しいScreenshotログ、startup/installログ、installed-version情報を収録していなかった。
- 依存一覧がmss/ttkbootstrapだけで、WGC依存の不整合やRCファイル混在を照合しにくかった。
- capture待機/エラーのhealth情報と、正常終了時の直前フレーム情報が永続化されていなかった。
- Diagnostic ReportのDesktopはKnown Folderに追従していなかった。

ZIPに現行/ローテーション済みScreenshot・startupログ、導入/削除ログと導入commit情報を追加。
全6依存の情報と主要ソース/lockのSHA-256をmanifestへ追加。
health遷移と終了時だけ直前情報を保存し、通常フレームは従来どおりメモリ内に保持する。
診断失敗がhealth通知やworker cleanupを止めないことをテストした。

## Windows検証

| 項目 | 結果・範囲 |
|---|---|
| Python 3.13.15 / 3.14.7 | 隔離LOCALAPPDATAで全回帰成功。最終差分はCIでも再検証 |
| 本番Python選択 | 実インストーラの選択関数でPSF署名Valid・AMD64・通常GILの3.14.7を確認 |
| 全ソース導入行程 | PS7/3.14、PS5.1/3.13で新規導入、稼働中更新、削除、再導入成功 |
| 手動終了なしの更新 | 実Launcher/Server/Overlay/Dashboardを起動したまま更新し、旧PID終了を確認 |
| 更新失敗 | 新版起動後のreadiness失敗を注入。旧ソース・ショートカット復元と再起動成功 |
| 異常終了 | 実Serverを強制終了して残った状態から更新成功 |
| 二重起動 | 正式Launcher再起動で既存Serverを維持。追加Overlayはmutexで終了 |
| データ | 全行程を通して履歴4件・累計3勝1敗、config内容を保持。起動時のsession初期化は既存仕様 |
| uninstall | source/previous source/shortcut/runtimeファイルを削除。history/config/stats/diagnostics/metadata/Pythonを保持 |
| reinstall | 保持した履歴・設定を再利用。終了後の関連PID、listenポート、一時領域残留を確認 |
| worker | 既存の実Job Object親異常終了、12回spawn/reap・handle非増加、timeout/kill/重複防止テスト成功 |
| Diagnostic Report | 稼働中と停止/削除後に別プロセスでZIP作成・内容・CRC検証。runtime認証ファイル除外 |
| Screenshot | 既存のrotation/I/O失敗隔離テスト成功。前RCの実Tk/MSS/pythonw結果を維持。実AC6未保存は未解決扱い |
| Dashboard | 実Tkで50件・スクロール・同一poll再描画なし。61件DBの新しい50件だけを取得、集計61件を維持 |
| PowerShell・依存 | 既存PS7/5.1回帰、lock/hashes依存導入、pip check、import、pip-audit成功 |

`test_source_install_flow.ps1`は本番installer/uninstallerのトランザクション全体を実行する。
外部release輸送をローカル固定ZIP、Python探索を検証用runtime、Desktopを一時フォルダー、
Overlay mutex/ゲーム名を専用名に置換する。PSF署名ポリシーは別途実機確認。
pipは本番の`--user --require-hashes --only-binary`を使用し、PYTHONUSERBASEを一時領域に限定する。
uv検証runtimeの管理制約解除はこのfixture内だけ。製品インストーラには追加しない。
実ユーザーデータ・稼働中Tracker・公開Releaseを変更しない。これは未公開tagからの実配布テストではない。

## 次の0/0報告の切り分け

1. `installed-version.json`、manifestのPython/依存/ソースハッシュ、`source-install.log`で正式導入・混在を確認。
2. `detector.jsonl`の`detector_health`でcapture待機・WGCエラー・disabledを確認。
3. `frame_context`の分類結果/スコアとstate、`startup.log`の拒否理由で`not_armed`／`no_recent_gameplay`等を区別。
4. 正常終了後のZIPなら直前リング情報も保存される。強制終了直前の未保存メモリは復元できない。

DashboardのRUNNINGはHTTP接続を表し、勝敗検出成功の証拠ではない。この表示改善は今回の非必須事項として保留。

## Release資材

- 公開中はv1.0.1。VERSION・installer既定版・README tagは現在も1.0.1で一致し、正式版番号は変更しない。
- bootstrap SHA-256: `39e7e8c54239f1fa61666ff4c9199aff6bf86b5937c7f69c6b14ebbc59d1c9e8`。READMEの固定値と一致。
- `scripts/prepare-release-assets.ps1`でinstall/uninstallと各SHA-256 sidecarをローカル生成・照合済み。
- 現RC install.ps1のSHA-256は`5a82ea4845eba7abb1c5d35a1358475789feb7b9cfbc50405c8cd57c0ce8be89`。
  公開v1.0.1 assetとは異なるため、既存Releaseを置換してはならない。
- 公開操作には新しい版番号の確定、VERSION/installer/README・版固定テストの同期、
  最終tagとソースcommitの一致、資材checksum再生成が必要。これは未許可の公開操作であり実施しない。
  非プレイの生成・検証手順自体は実行済み。AC6合格だけでv1.0.1資材を上書きしてよいわけではない。

## 実AC6で残る最小チェックリスト

- [ ] ボーダーレスSDRでWIN/LOSS/DRAWを確認。WIN/LOSSは各1回のみ加算、DRAWでWIN/LOSE増加なし。次試合の再アーム、UNDO/RESET後の重複なし。
- [ ] 結果直前～演出中のAlt+Tab、最小化復帰、ゲーム再起動。取りこぼし/二重加算なし。別アプリの結果画像で加算なし。
- [ ] ScreenshotをGUIでONにし、実5連勝演出でPNG1枚と`saved`ログ。OFFでは保存なし。未保存ならevent IDごとの理由を確認し、遮蔽なら該当Overlay無効時と比較。
- [ ] 高連勝/50連勝の実ゲーム合成と一枚制限、実Desktop転送を確認。可能な場合に実施し、未実施ならその範囲を明示。
- [ ] HDR・排他的fullscreen・複数/混在DPIは対応を約束する構成だけ確認。安全側の保存見送りは記録し、非対応構成を成功扱いしない。

合格後に新規リリースを判断できるが、上記のversion/tag/asset公開操作は別途明示的な許可が必要。
