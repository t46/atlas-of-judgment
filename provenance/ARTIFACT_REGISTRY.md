# 成果物台帳（Artifact Registry）

全パスは `/Users/s30825/unktok/dev/ml-top-conf-review-analysis/` からの相対パス。分類凡例:

- **PRIMARY** — 現時点での正典（canonical）成果物
- **DERIVED** — PRIMARY から構築・マージされた派生物
- **SUPERSEDED** — 後続版に置き換えられた版（置き換え先を明記）
- **EXPERIMENTAL/PILOT** — 最終成果として意図されていない実験・試作
- **BACKUP** — スナップショット・安全用コピー
- **UNCLEAR** — 証拠から確信を持てないもの（根拠付きの推定を付記）

最終検証時刻: 2026-08-18T04:27:30Z。件数は read-only クエリまたはマニフェストからの直接引用。

## 1. コアデータベース

| パス | 分類 | 内容 |
|---|---|---|
| `data/raw/iclr/openreview.sqlite3` | **PRIMARY**（不変の生データ） | `forums`=52,460, `collection_status`=9年分全 `completed=1` |
| `data/processed/iclr/analysis.sqlite3` | **PRIMARY**（決定的に再生成可能） | `papers`=52,460, `messages`=792,703 |
| `data/analysis/iclr/production-2026.sqlite3` | **PRIMARY** | Full Layered DeepSeek メモ, 151,193件, $314.42 |
| `data/analysis/iclr/direct-2018-2026.sqlite3` | **PRIMARY** | Direct DeepSeek メモ, 51,813件, $194.75 |
| `data/analysis/iclr/pilot.sqlite3` | EXPERIMENTAL/PILOT | 2026-08-11 手法比較パイロットDB。本番DBの起源だが本番自体ではない |
| `data/backups/iclr-2026/*.sqlite3`（3件） | BACKUP | `production-2026.sqlite3` のローテーションスナップショット、整合性検証済み |
| `data/backups/iclr-direct-2018-2026/*.sqlite3`（3件） | BACKUP | `direct-2018-2026.sqlite3` の同様のスナップショット |

## 2. スキーマ定義（`schemas/`）

| ファイル | 用途 | 状態 |
|---|---|---|
| `schemas/evaluation-episode-v0.1.json` | 初期の時系列探索寄りエピソードスキーマ | SUPERSEDED by v0.2（本番では未使用と確認） |
| `schemas/evaluation-episode-v0.2.json` | Lite/Deep 二段階エピソードスキーマ | **PRIMARY**（Episode系全体で使用） |
| `schemas/review-logic-compact-v0.1.json` | Compact reviewer-logic 出力スキーマ | **PRIMARY**（Luna/Qwen compact系で使用） |
| `schemas/review-logic-compact-batch-v0.1.json` | Compact バッチAPI用スキーマ変種 | **PRIMARY**（該当バッチ実行専用） |
| `schemas/reviewer-logic-direct-v0.1.json` | Direct reviewer-logic 出力スキーマ | **PRIMARY**（Direct-Qwen系全体で使用） |

## 3. Episode / Atlas / パターン発見クラスタ（`data/analysis/iclr/` 配下）

| ディレクトリ | エンジン/モデル | 件数 | 分類 |
|---|---|---|---|
| `evaluation-episode-pilot/` | エージェント, スキーマ未接続 | 24論文 | EXPERIMENTAL/PILOT |
| `logic-pattern-pilot/` | エージェント, スキーマフリー | 100論文の草案Dossier | EXPERIMENTAL/PILOT（設計探索） |
| `episode-lite-1000-shard10-attempt/` | エージェント | 1シャード | SUPERSEDED by `episode-lite-1000` |
| `episode-lite-1000-shard25-attempt/` | エージェント | 1シャード | SUPERSEDED by `episode-lite-1000` |
| `episode-lite-1000/`（+ `logs/`, `synthesis/`, `content-quality-rerun-backup-*`） | エージェント, v0.2 Lite | 1,000レビュー | **PRIMARY**（較正・参照資産として明示的に保持） |
| `episode-deep-63/`（+ `logs/`, `pattern-challenges/`, `prompt-tuning-iteration-0/1`） | エージェント, v0.2 Deep | 63件 | **PRIMARY** |
| `episode-lite-2026-full/`（+ `logs/`） | gpt-5.6-luna low | 部分実行 | SUPERSEDED（抽出不足で却下） |
| `episode-lite-2026-full-v2/`（+ `logs/`） | gpt-5.6-luna medium | 部分実行 | SUPERSEDED（テンプレート化フィールドで却下） |
| `episode-lite-2026-full-v3/`（+ `logs/`, `batch-logs/`） | gpt-5.6-luna medium, 8件/シャード | 9,774シャード計画、途中停止 | 停止/EXPERIMENTAL（`review-logic-compact-2026` のソースDBとして再利用） |
| `episode-lite-2026-full-single/` | 再パック（1件/シャード） | 75,859シャード | EXPERIMENTAL（`review-logic-qwen-2026-full` の入力） |
| `episode-lite-2026-qwen-pair-control/` | Qwen対Luna A/B | 54件 | EXPERIMENTAL/PILOT |
| `episode-lite-2026-qwen-single-control/` | Qwen対Luna A/B | 54件 | EXPERIMENTAL/PILOT |
| `episode-lite-2026-qwen-single-pilot-206/` | Qwen対Luna A/B | 206件 | EXPERIMENTAL/PILOT |
| `episode-lite-calibration-r5-low/`（+ `logs/`） | gpt-5.6-luna low | 3件 | SUPERSEDED（却下構成） |
| `episode-lite-calibration-r15-medium/`（+ `logs/`） | gpt-5.6-luna medium | 44件 | SUPERSEDED（却下構成） |
| `episode-lite-calibration-r8-medium-v3/`（+ `logs/`） | gpt-5.6-luna medium, 8件/シャード | 49件, エラー0 | **PRIMARY**（採用構成の較正結果） |
| `episode-reclassification-3135/`（+ `logs/`, `prompt-tuning/`） | エージェント | 3,135エピソード | **PRIMARY** |
| `episode-reclassification-3135/unmapped-discovery/` | エージェント | 272エピソード | **PRIMARY** |
| `episode-reclassification-3135/new-card-screening/` | エージェント | 656メンバーシップ | SUPERSEDED（完了・非正準、`new-card-refinement-v2`/`consensus-v2` に置換） |
| `episode-reclassification-3135/new-card-challenge-audit/` | エージェント | 3回の懐疑監査 | **PRIMARY**（v2の入力） |
| `episode-reclassification-3135/new-card-refinement-v2/` | エージェント | 900候補ペア | **PRIMARY** |
| `episode-reclassification-3135/new-card-confirmation-v2/` | エージェント | 900候補ペアの確認 | **PRIMARY** |
| `episode-reclassification-3135/atlas-13-membership.jsonl`（v1） | 決定的結合 | 3,135行 | SUPERSEDED by `atlas-13-consensus-v2-membership.jsonl` |
| `episode-reclassification-3135/atlas-13-consensus-v2-membership.jsonl` | 決定的結合 | 3,135行, 新規15確認 | **PRIMARY / 完了・正典**（Atlas系の終点） |

## 4. Compact / Qwen 構造化トラック（`data/analysis/iclr/` 配下）

| ディレクトリ | エンジン/モデル | 結果 | 分類 |
|---|---|---|---|
| `review-logic-compact-pilot/`, `-v1/`, `-v2/`, `-v3/` | gpt-5.6-luna low（`codex exec`） | プロンプト反復、v3で凍結 | EXPERIMENTAL/PILOT |
| `review-logic-compact-holdout/`, `-holdout-v1/` | gpt-5.6-luna low | 未見データでの確認 | EXPERIMENTAL/PILOT |
| `review-logic-compact-2026/`（+ `logs/`, `outputs/`, `raw/`, `reports/`, `validations/`） | gpt-5.6-luna low | 876完了/186失敗/74,797未処理で停止 | SUPERSEDED（全件方式として）/ 基準データとして保持（Qwen比較の参照元） |
| `review-logic-qwen-pilot-200/`, `-v2/` | qwen3.7-flash, Batch API | 0/27（`model_not_found` で行き詰まり） | EXPERIMENTAL/PILOT（行き詰まり） |
| `review-logic-qwen-pilot-200-v3/` | qwen3.7-flash, realtime API | 22/27 | EXPERIMENTAL/PILOT |
| `review-logic-qwen-single-control-v4/` | qwen3.7-flash | 46/54 | EXPERIMENTAL/PILOT |
| `review-logic-qwen-single-control-v5/` | qwen3.7-flash | 53/54 | EXPERIMENTAL/PILOT |
| `review-logic-qwen-pair-control-v1/` | qwen3.7-flash, 2件/リクエスト | 54/54 | EXPERIMENTAL/PILOT（カバレッジ理由で却下） |
| `review-logic-qwen-single-loadtest-206/` | qwen3.7-flash | 203/206 | EXPERIMENTAL/PILOT（本番前最終ゲート） |
| `review-logic-qwen-2026-full/`（+ `inputs/`, `outputs/`, `provider/`, `raw/`, `reports/`, `validations/`） | qwen3.7-flash | 74,380/75,859完了(98.1%), 失敗1,479, $28.79/$40 | **PRIMARY** |
| `unit-taxonomy-2026-v1/`（`units.sqlite3`, `taxonomy-v1.json`, `clusters-*.json`, `centroids-*.npz`, `embeddings-*-sample.npy`, `viz-data.json`, `anatomy.html`, `manifest.json`） | ローカル埋め込み bge-small-en-v1.5 + UMAP/HDBSCAN（API課金なし、2026-08-18作成） | `review-logic-qwen-2026-full` の complete 74,380 レビューから 410,586 logic unit を抽出。12k サンプルのクラスタリングから検査対象12カテゴリ・推論型12型の taxonomy を帰納し、全 unit へ最近傍セントロイド割り当て（cos≥0.75: object 94.5% / reasoning 87.9%）。`anatomy.html` は可視化アーティファクト（Claude Artifact として公開）。`galaxy.json` は 40k unit サンプルの UMAP 2D 星図（seed 11、`build_galaxy_regions.py` が k-means+c-TF-IDF で領域名を追記）。`structure-data.json` はレビュー内論理構造（開き手/閉じ手のlift、位置別valence、遷移、arcs）。`anatomy.html` は英語版 "Atlas of Judgment"（`scripts/atlas_template.html`。旧テンプレート `anatomy_template.html`(日本語版)・`observatory_template.html`(observatory版) は SUPERSEDED として保持）。生成スクリプト: `scripts/extract_logic_units_2026.py` → `induce_unit_taxonomy.py` → `assign_unit_taxonomy.py` → `build_unit_viz_data.py` + `build_galaxy_data.py` + `build_galaxy_regions.py` + `build_structure_data.py` → `build_anatomy_html.py`。同ディレクトリの `about.html`（プロジェクト説明）・`method.html`（データ生成の再現手順記録、可視化はスコープ外）は静的コンパニオンページで、それぞれ独立の Claude Artifact として公開・相互リンク済み | DERIVED（分析レイヤー、ソース run は一切変更していない） |
| `unit-taxonomy-direct-v1/`（`units.sqlite3`, `manifest.json`） | ローカル埋め込み（2026-08-18作成、進行中） | Direct トラック（2018–2026, `reviewer-logic-direct-qwen-full-v1` complete 50,861 forum）から 1,009,592 logic unit を抽出（year / temporal_position / judgment_change / update_trigger 付き）。`assign_unit_taxonomy_direct.py` が 2026 側セントロイド（taxonomy-v1 共有）で全 1,009,592 unit へ割り当て完了。`drift-data.json`（年次集計、`build_drift_data.py`）、`panel-data.json`（rebuttal動力学 / decision連関 / 批判の広さ×採択(gauntlet) / メタレビュアー対比 / レビュアー間対立 / memo_inferred信頼性グリッド、`build_panel_data.py`）を保持。`minds-data.json`（レビュアー・アーキタイプ k-means k=5 seed 7・98,513人 / reasoning論法マーカー分類 / 年次vitals、`build_minds_data.py`）を保持 | DERIVED（時系列・パネル分析レイヤー） |

## 5. Direct / Qwen 構造化トラック（`data/analysis/iclr/` 配下）

| ディレクトリ | エンジン/モデル | 結果 | 分類 |
|---|---|---|---|
| `reviewer-logic-direct-qwen/` | — | プロトコルファイルのみ、実行記録なし | BACKUP/PROTOCOL-SOURCE |
| `reviewer-logic-direct-qwen-pilot-v1/` | qwen3.7-flash | 0/90 | EXPERIMENTAL/PILOT（行き詰まり） |
| `reviewer-logic-direct-qwen-pilot-v2/` | qwen3.7-flash | 大半 `prepared` のまま停滞 | EXPERIMENTAL/PILOT（行き詰まり） |
| `reviewer-logic-direct-qwen-pilot-v3/` | qwen3.7-flash | 1件完了 | EXPERIMENTAL/PILOT |
| `reviewer-logic-direct-qwen-pilot-v4/` | qwen3.7-flash | 79/90 | EXPERIMENTAL/PILOT（手法確定、full-v1へ） |
| `reviewer-logic-direct-qwen-full-v1/`（+ `outputs/`, `provider/`, `validations/`, `state-pre*.sqlite3` 各種バックアップ） | qwen3.7-flash | complete=50,861 (98.16%) failed=952（2026-08-18T04:37:05Z検証、retry-24k・retry-32k両方マージ済みの最終値） | **PRIMARY**（確定） |
| `reviewer-logic-direct-qwen-retry-24k-v1/` | qwen3.7-flash, max_output_tokens=24000 | complete=6,223 failed=1,047 / 7,270件対象 | DERIVED（full-v1へマージ済み: `merge-reviewer-logic-direct-qwen-retry-24k-v1.json`、ディレクトリ自体は一次証拠として保持） |
| `reviewer-logic-direct-qwen-retry-32k-v1/`（+ `logs/`） | qwen3.7-flash, max_output_tokens=32000 | complete=812 failed=147 / 959件対象、実コスト$1.355473 | DERIVED（完了・full-v1へマージ済み: `merge-reviewer-logic-direct-qwen-retry-32k-v1.json`、ディレクトリ自体は一次証拠として保持） |

## 6. その他

| パス | 分類 | 内容 |
|---|---|---|
| `data/inventory/` | 補助 | `inventory_iclr.py` によるライブOpenReview件数のスナップショット（生DBとの突合は行われない） |
| `data/logs/` | 補助 | 実行ログ |
| `logs/claude-code-documentation.log` | 補助 | 本ドキュメント作成タスク自身のログ |

## 分類にあたっての注記

- 「完了」は状態カウントの整合性（`complete + failed [+ running/prepared]` が対象件数に一致するか）を確認したうえで判定している。ファイルの存在のみでは判定していない。
- Episode系・Compact/Luna系・Direct/Qwen系は互いに独立したトラックであり、いずれか一つを「唯一の正典」として扱わないこと。3系統それぞれに現時点でのPRIMARY成果物が存在する（Atlas-13-consensus-v2、review-logic-qwen-2026-full、reviewer-logic-direct-qwen-full-v1）。
- `reviewer-logic-direct-qwen-full-v1` と `retry-24k-v1`/`retry-32k-v1` は 2026-08-18T04:37:05Z 時点で**確定値**（両リトライとも完了・マージ済み、進行中のプロセスなし）。今後さらなるリトライラウンド（例: 残存952件への三次リトライ）が実施された場合は、本表・`docs/handoff/CLAUDE_CODE_HANDOFF.md`・`docs/provenance/END_TO_END_PROCESS.md` を合わせて更新すること。
