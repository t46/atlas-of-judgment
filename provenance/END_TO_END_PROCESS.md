# エンドツーエンド・プロセス — 収集からLLM分析まで

前提: `docs/provenance/README.md` の「3層の証拠区分」を先に読むこと。本書の件数は特記なき限り 2026-08-18 の read-only 検証結果。

## 1. 研究課題とその変遷

タイムスタンプ（ファイル mtime）順に確認できる変遷:

1. **`docs/initial-review-observations.md`**（2026-08-11 18:53）— 素朴なクローズリーディング。「reviewer challenge」（対象claim→検査対象→観察→規範/対抗仮説→推論→要求される弁別材料→評価結果→解決）という単位を暫定的に提示。「これは確定した分類法ではなく、より広いサンプルで検証すべき候補」と明記。
2. **`docs/iclr-2026-pilot.md`**（2026-08-11 22:05）— 手法の分岐点。**Layered**（レビュー→派生やり取り→論文統合、DeepSeek 3回呼び出し）vs **Direct**（フォーラム全体を1回で分析）。同一モデルによるブラインド審判で Layered が 69–31 で優勢だったが、コスト/来歴リスクの観点（「Layered は中間メモの誤りが後段に伝播しうる」）から「まず Direct を2026年全件に適用する」方針を採用。
3. **`docs/prompts.md`**（2026-08-15 23:18）— Direct が事前計画通り 2018–2026 全コーパスへ拡大されたことを記録。`forum_direct` は「2018–2026 ICLR 51,813件の Direct ジョブ全件に一度ずつ使われた本番プロンプト」。
4. **`docs/evaluation-logic-discovery-pilot.md`**（2026-08-16 07:15）— 「レビュー分析」全般から「reviewer logic（査読者の評価論理）」への明示的転換点。「既存の自由記述DeepSeek分析から、人間査読者の評価論理の帰納的マップへの、サブスクリプション課金を活用した高速な経路」。論文レベルの `paper_synthesis` 取得率が71.2%に留まる問題を指摘し、より安定した査読者レベルの段階（`initial_blind`, `trajectory`）への抽出源変更を提案 — 論文レベル自由文からの脱却の第一歩。
5. **`docs/evaluation-episode-method.md`**（2026-08-16 07:45）— 「evaluation episode（評価エピソード）」という原子単位を定式化。研究課題を明文化:「論文が採択されたか、レビューが好意的だったかではない…査読者は何を検査し、どのような基準・比較・仮定・推論を通してその観察が…判断になるのか」。論文統合ではなく「査読者ブランチ」を抽出入力とすることを明示的に採用。`schemas/evaluation-episode-v0.1.json` を導入。
6. **`docs/output-backward-data-contract.md`**（2026-08-16 13:19）— この転換を支える設計契約。「拡張可能な単一スキーマに2段階の充実度（Lite/Deep）」。逆算リーク防止原則:「スコア・確信度・decision・分野などの結果メタデータは論理抽出の**後に**結合する — 最終結果から査読者の論理を逆算的に再構築するリスクを減らすため」。v0.1（時系列探索寄りのリッチなスキーマ）と v0.2（Lite/Deep中間設計、採用版）を分離。
7. **Deep-challenge / atlas-pilot / reclassification-3135 系ドキュメント**（2026-08-16 13:42〜21:09）— エピソード基盤のパターン発見を統合（10カード→13カードAtlas）。概念的な転換はなく、方法論の成熟のみ。
8. **`docs/iclr-2026-full-episode-lite-rollout.md`**（2026-08-16 23:32）— 「エピソード→compact」への転換点。「全件本番方式としては停止。Luna medium の strict Episode Lite は品質が高いが75,859件で約1週間を要するため、1000件の詳細参照と抽出境界の較正資産として残す。現行の全件方式は compact-reviewer-logic-rollout」。
9. **`docs/iclr-2026-compact-reviewer-logic-rollout.md`**（2026-08-17 02:45）— 縮小スキーマ `inspected_object → observation → reasoning → judgment → suggested_improvement`（`schemas/review-logic-compact-v0.1.json`）を全件規模向けに採用。既存Atlasを正解として種付けしないことを明記。同日追記で、エージェント型 Luna（`gpt-5.6-luna`）実行器が876/75,859件で「安全に停止」され、Qwen 3.7 Flash バッチ正規化に置き換えられたことを記録。
10. **`docs/qwen-batch-review-logic-rollout.md`**（2026-08-17 03:54）— 最終的な本番方針。Qwen は生レビューの再分析ではなく、既存 DeepSeek 自由文メモに対する**正規化層**として、ICLR 2026 の75,859件全レビューに適用。2018–2026 Direct 母集団（51,813件）を次のシリーズとして名指し。

**総括**: 素朴な「reviewer challenge」観察 → コスト理由で Direct を採用（品質で Layered に劣ると自覚しつつ） → 「reviewer evaluation logic」への転換 → スキーマ制約付き「evaluation episode」（Lite/Deep、v0.1→v0.2）へ、論文統合を抽出源から排除 → Atlas パターン発見・検証 → 全件エピソード抽出は速度面で断念、較正資産として保持 → 規模のための軽量「compact」単位を採用 → 実行エンジン自体もエージェント型 Luna から Qwen バッチ/リアルタイム正規化へ変更。

## 2. 会議・年範囲（ICLR / OpenReview / 2018–2026）の選定理由

ドキュメント自体には明示的な選定理由の記述がなく、根拠はセッション履歴のみに存在する（`docs/provenance/DECISION_LOG.md` に全文引用）。要旨:

- OpenReview に情報源を限定し、出所と取得経路を統一する方針をまず決定。
- ICLR を主コーパスに選んだ決定的理由: **ICML/NeurIPS は不採択論文のレビューが基本的に公開されず、公開されているレビューが採択論文に偏る**のに対し、ICLR は不採択を含む全提出のレビューを公開している。
- 年範囲は 2018–2026 に決定。2017年は OpenReview の公式ベニューとして確認しづらいため意図的に除外・別途調査扱い。

**重要**: この2018–2026という多年範囲の決定は、作業セッション開始からわずか約4分後（セッションJSONLの早い段階）に行われており、`docs/iclr-2026-pilot.md` が「2026年全件の Direct を先に実行する」と書いているのは**分析手法のロールアウト順序**の話であって、年範囲自体を後から2026年単独から2018–2026へ拡張した、という意味ではない。年範囲は最初から固定されており、変化したのは抽出「手法」の方だけである。

## 3. 生データ収集

スクリプト: `scripts/collect_iclr_forums.py`（380行）。ライブラリ: `openreview-py>=2.4.1`（`pyproject.toml`）。

- **API版の切替**: `venue_config()` が常にまず OpenReview API v2 で `ICLR.cc/{year}/Conference` グループを取得し、`content.submission_id` があれば v2、なければ v1（`{venue_id}/-/Blind_Submission`）へフォールバックする実行時判定。年ごとのハードコード表ではない。実データでは 2018–2023 が v1、2024–2026 が v2 という結果になっている（`docs/iclr-data-inventory.md` と整合）。
- **ページネーション**: カーソル方式（`after=<最後のnote id>`、`sort="id"`、`limit=page_size` 既定100）。`details="replies"` で返信グラフを同時取得。件数が進まなければ `RuntimeError` で無限ループを防止。最大7回・指数バックオフ（1秒〜上限30秒）のリトライ関数あり。
- **識別子・PII の扱い**: **匿名化・マスキングは一切行われていない**。OpenReview のノートを `.to_json()` でそのままシリアライズし、署名・内容をそのまま保存する。正規化段階の `classify_role()` も、匿名疑似ID（`Reviewer_abcd`）や実名の公開プロフィールID（`~実名1`）をそのまま読み取る。PII 特別対応のコードは存在しない。
- **再開性**: `collection_status` テーブル（年ごと1行）に `expected_submissions`, `stored_submissions`, `stored_replies`, `after_id`（再開用カーソル）, `completed` を保持。中断からは `after_id` で再開でき、`forums` テーブルは `PRIMARY KEY (year, forum_id)` + `ON CONFLICT DO UPDATE` で再実行しても重複しない。

**生データベース** `data/raw/iclr/openreview.sqlite3`（read-only 検証済み）:
```sql
CREATE TABLE forums(year, forum_id, api_version, submission_invitation, submission_json, replies_json, reply_count, fetched_at, PRIMARY KEY(year, forum_id));
CREATE TABLE collection_status(year PRIMARY KEY, venue_id, api_version, submission_invitation, expected_submissions, stored_submissions, stored_replies, completed, updated_at, after_id);
```
`forums`: **52,460行**（2018=935, 2019=1419, 2020=2213, 2021=2594, 2022=2617, 2023=3792, 2024=7404, 2025=11672, 2026=19814）。`collection_status`: 9行、全年 `completed=1` かつ `stored_submissions == expected_submissions`。

**完全性チェックの限界（重要な未検証事項）**: 収集完了後に**独立してOpenReview本体と突き合わせる仕組みは存在しない**。`scripts/inventory_iclr.py` はライブOpenReviewから件数を取得するが、書き込み先は `data/inventory/iclr.json` のみで、`data/raw/iclr/openreview.sqlite3` とは突き合わせない。「完全性」の唯一の根拠は、同一収集実行の1ページ目で取得した `expected_submissions` と、その実行自身の `stored_submissions` の内部整合性だけであり、独立した再取得による事後検証ではない。

**不変性境界**: `scripts/*.py` 全体を `grep` した結果、`data/raw` へのパス参照は収集スクリプトと正規化スクリプトの2箇所のみ。収集スクリプトは書き込みモードで開き（想定通り）、正規化スクリプトは `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` で厳格に読み取り専用で開く。他のどのスクリプトも生データベースのパスを参照しない。**実質的に生データは不変である**。

## 4. 正規化

スクリプト: `scripts/normalize_iclr.py`（334行）。

- **kind/role 判定**: `classify_kind()` が invitation ラベルからOpenReview v2 の "edit" ラッパーを除去した上で、順序付きパターン（official_review → meta_review → desk_rejection → withdrawal → decision → official_comment → public_comment → comment(キャッチオール) → "unknown"）に最初にマッチしたものを採用。`classify_role()` は署名文字列（`/authors`, `/reviewer_`, `/area_chair_`, `/program_chair`, `~`公開ID）を検査し、kind によるフォールバックも持つ。
- **決定性**: `datetime.now()` や乱数は不使用（タイムスタンプは生データの `cdate`/`mdate` をそのまま使用）。年ごとに `DELETE` してから1トランザクション内で再構築する冪等設計。不変な生データがある限り再実行しても同等の結果になる。
- **手作業・不可逆なステップ**: 確認できず。全年に一様なルールベース処理のみ（v2の "edit" ラベル除去は構造的なAPIアーティファクトへの対処であり、内容依存の例外ではない）。

**正規化済みデータベース** `data/processed/iclr/analysis.sqlite3`（read-only 検証済み）:
```sql
CREATE TABLE papers(year, forum_id, api_version, title, abstract, content_json, reply_count, review_count, comment_count, has_meta_review, decision, withdrawn, desk_rejected, PRIMARY KEY(year, forum_id));
CREATE TABLE messages(year, note_id, forum_id, replyto, kind, role, signature, cdate, mdate, invitations_json, content_json, content_text, PRIMARY KEY(year, note_id), FK(year, forum_id) REFERENCES papers);
-- + messages_forum_idx / messages_replyto_idx / messages_kind_idx / messages_role_idx
```
`papers`: **52,460行**。`messages`: **792,703行**。`kind`内訳: `official_comment=507543, official_review=199031, decision=40821, meta_review=30108, withdrawal=8235, public_comment=5563, desk_rejection=980, comment=422`（"unknown" 該当0件）。`role`内訳: `author=427488, reviewer=277884, program_chair=41957, area_chair=33327, unknown=7906, public=4141`。

テストは `tests/test_pipeline.py::test_review_kind_and_role_are_provenance_only` のみで、happy-pathを1件ずつ確認する程度に限られる（網羅的なタクソノミーテストではない）。

## 5. DeepSeek 分析メモ層（レイヤー2 — 生レビューの直後）

スクリプト: `scripts/run_deepseek_pilot.py`（`MODEL = "deepseek-v4-flash"`）。設定: `enable_thinking` 既定 disabled の場合 `temperature=0.4`（thinking 有効時は `reasoning_effort="high"` で温度未設定）。予算上限付きの永続SQLite台帳（`budget_state`, `api_call_attempts`）と `fcntl` ロックで中断時も課金整合性を保つ設計。

2つの本番実行:

| 実行 | 呼び出し元 | データベース | ステージ | 件数 | 実コスト |
|---|---|---|---|---|---|
| **Full Layered** | `scripts/run_iclr_2026_full_layered.py` | `data/analysis/iclr/production-2026.sqlite3` | `initial_blind`/`trajectory`/`paper_synthesis`（3段階、トークン上限は段階ごとに4k→8k / 6k→12k / 8k→12k） | 151,193メモ | $314.416569 |
| **Direct** | `scripts/supervise_iclr_direct.py`（ジョブは `scripts/prepare_iclr_direct_production.py` が `analysis.sqlite3` から準備） | `data/analysis/iclr/direct-2018-2026.sqlite3` | `forum_direct`（フォーラム丸ごと1回） | 51,813メモ | $194.753528 |

プロンプトの正典は `docs/prompts.md`（元ソース: `scripts/prepare_pilot.py` の `INITIAL_SYSTEM_PROMPT`、`scripts/prepare_pilot_followups.py` の `TRAJECTORY_SYSTEM_PROMPT`/`SYNTHESIS_SYSTEM_PROMPT`。レンダリング済み全文は各分析DBの `jobs.system_prompt`/`jobs.user_prompt` に記録）。共通方針: 質的メタサイエンスとしての枠組み、独自の分類・再査読の禁止、行ID引用の必須化（`R-<note_id>:L###`）、初期段階では結果/他レビューの意図的隠蔽。

このステージの出力（`memos` テーブルの自由記述テキスト）が、後述するすべてのQwen/Luna構造化ステージの**入力**になる。Qwen側の `source_memo()` 関数（`scripts/qwen_reviewer_logic_direct.py`）は明示的に `memos.memo` を読み込んでおり、生レビューには一切触れない。

## 6. 分析ファミリー全体像

以下は主要な系列の概要。各成果物の PRIMARY/SUPERSEDED 等の詳細分類は `docs/provenance/ARTIFACT_REGISTRY.md` を参照。

### 6-a. Episode / Atlas / パターン発見クラスタ（Full Layered メモが入力）
```
evaluation-episode-pilot（24論文、v0.1目標、スキーマ未接続）
 → logic-pattern-pilot（100論文、因果帰属プロトタイプ、スキーマフリー）
 → episode-lite-1000-shard10/-shard25-attempt（同日中の先行検討、即日 episode-lite-1000 に置換）
 → episode-lite-1000（1,000レビュー、v0.2 Lite）+ episode-deep-63（v0.2 Deep, 63件）
 → build_episode_pattern_atlas.py 系 → 10カードAtlas → atlas-adjudication.json（構造化判定の正典入力）
 → episode-reclassification-3135（3,135エピソードを結果非公開で再分類）
 → unmapped-discovery（未マップ272エピソード → local→regional→global 統合）
 → new-card-screening（v1ブラインド選別、656メンバーシップ）— 完了・非正準
 → new-card-challenge-audit（懐疑的監査）→ new-card-refinement-v2 → new-card-confirmation-v2
 → build_atlas_13_consensus_v2.py → atlas-13-consensus-v2-membership.jsonl（15件新規確認: N-P01=5, N-P02=2暫定, N-P03=8）— 完了・正準
```
並行して打ち切られた全件エピソード試行: `episode-lite-2026-full`（低品質）→ `-full-v2`（品質不足で却下）→ 較正三点（`r5-low`/`r15-medium`/`r8-medium-v3`、`r8-medium-v3` が採用構成）→ `-full-v3`（9,774シャード予定、途中で明示的に停止）→ `-full-single`（1レビュー/シャード再パック、Qwen対Lunaコントロール用）。実行エンジンは `gpt-5.6-luna`（Luna）。

### 6-b. Compact / Qwen 構造化トラック（Full Layered メモ・Episode Lite 由来）
```
review-logic-compact-pilot → -v1 → -v2 → -v3（プロンプト凍結）→ holdout / holdout-v1
 → review-logic-compact-2026（gpt-5.6-luna low、source=episode-lite-2026-full-v3、
     目標75,859件、876完了・186失敗で「安全に停止」— 全件方式としてはSUPERSEDED、Luna基準として保持）
 → review-logic-qwen-pilot-200/-v2（Batch API、model_not_found で0/27）
 → -v3（realtime API、22/27）→ single-control-v4/v5 → pair-control-v1（却下）→ single-loadtest-206（最終ゲート、203/206）
 → review-logic-qwen-2026-full（qwen3.7-flash、source=episode-lite-2026-full-single、
     74,380/75,859完了(98.1%)、失敗1,479、$28.79/上限$40）— PRIMARY
```

### 6-c. Direct / Qwen 構造化トラック（Direct メモが入力）
```
reviewer-logic-direct-qwen/（プロトコルのみ、実行記録なし）
 → pilot-v1（0/90、行き詰まり）→ v2（大半 prepared のまま）→ v3（1件完了）→ v4（79/90、手法確定）
 → reviewer-logic-direct-qwen-full-v1（51,813件対象）— PRIMARY
 → reviewer-logic-direct-qwen-retry-24k-v1（7,270件を max_output_tokens=24000 で再試行、
     complete=6,223 failed=1,047、full-v1へマージ済み: merge-reviewer-logic-direct-qwen-retry-24k-v1.json）
 → reviewer-logic-direct-qwen-retry-32k-v1（959件を max_output_tokens=32000 で再試行、
     complete=812 failed=147、full-v1へマージ済み: merge-reviewer-logic-direct-qwen-retry-32k-v1.json）
```

**2026-08-18T04:37:05Z 時点の最終状態（両リトライとも完了・マージ済み、read-only検証済み）**: `reviewer-logic-direct-qwen-full-v1` は `complete=50,861 (98.16%)`, `failed=952 (1.84%)`。カバレッジには2つの定義があり用途に応じて使い分けること: **厳密完了（strict-complete）50,861/51,813 (98.16%)** を分析の既定とし、**JSON取得済み（JSON-available）51,674/51,813 (99.73%)** は、警告付きレコードであることを明示的にフラグ立てたうえで集約分析に用いる場合にのみ使ってよい。

残存952件の内訳（`state.sqlite3` 直接集計、確定値）:

| 区分 | 件数 | 小分類 | 件数 |
|---|---:|---|---:|
| 出力JSONあり（プロバイダ応答は取得済み、自前検証で失敗） | 813 | 無効なエビデンス参照 | 709 |
| | | JSON Schema検証エラー | 65 |
| | | その他の厳密/ローカル検証エラー | 39 |
| 出力JSONなし（プロバイダから解析可能なJSONが得られず） | 139 | パース/切り詰め/不正なプロバイダJSON | 107 |
| | | プロバイダのコンテンツポリシー拒否（HTTP 400） | 32 |

出力JSONあり813件のみが新規API課金なしの `reprocess` サブコマンドによるローカル修復の候補（正規化/検証ロジックの改善が前提、修復は保証されない）。出力JSONなし139件（特にパース/切り詰め107件）は`reprocess`の対象外で、別のパース/修復戦略か新規モデルリクエストが必要（§8参照）。これ以上のリトライラウンドは行われていない。運用の詳細・次のアクション判断は `docs/handoff/CLAUDE_CODE_HANDOFF.md` を参照。

モデル・設定の確認: `qwen3.7-flash`、`base_url=https://dashscope-intl.aliyuncs.com/compatible-mode/v1`、`enable_thinking=false`。**Qwen側は temperature も seed も未設定**（プロバイダ既定値依存）— DeepSeekの明示的 `temperature=0.4` とは異なる。

### マージ・再パックスクリプト
- `scripts/merge_qwen_direct_retry.py` — リトライ実行を元実行へ検証・マージ。一次試行の `provider/` 証拠とusage列は**一切上書きしない**設計。適用前に必ず元DBのバックアップ（`state-pre-retry-merge-<timestamp>.sqlite3`）を自動生成する。詳細な操作手順は `docs/handoff/CLAUDE_CODE_HANDOFF.md` §5。
- `scripts/repack_qwen_control_pairs.py` — 単一レビュー1件/シャードのコーパスを、監査可能な2レビュー/パケットへ再パック。`review-logic-qwen-pair-control-v1` の入力に使用。

## 7. 選定ルール・件数・コストの出所

- **「3135」は文字通りの件数** — `episode-reclassification-3135/manifest.json` の `episode_count: 3135`（200シャード分の集計）。
- **「1000」は文字通りの件数** — `episode-lite-1000/manifest.json` の `selection_count: 1000`（seed 20260816、層化サンプル、実論文数900）。
- **「24k」「32k」は件数ではなく `max_output_tokens`（トークン予算）** — 実際の対象件数は `retry-24k-v1: request_count=7270`、`retry-32k-v1: request_count=959` であり、マニフェストの数字名と件数を混同しないこと。

コストロールアップ（`scripts/report_pilot_costs.py` を read-only 実行して検証。ネットワーク呼び出しなし、`sqlite3` の `mode=ro` のみ使用する安全なスクリプトであることをコード読解で確認済み）:

| ステージ | ジョブ数 | USD | 平均/件 |
|---|---:|---:|---:|
| initial_blind | 395 | $0.325734 | $0.000825 |
| trajectory | 327 | $0.506839 | $0.001550 |
| paper_synthesis | 100 | $0.520947 | $0.005209 |
| forum_direct | 100 | $0.318475 | $0.003185 |
| method_comparison | 100 | $0.402448 | $0.004024 |

（上表はパイロット期間の実測。全件本番の実コストは Full Layered $314.416569 / Direct $194.753528、いずれも `docs/data-asset.md` に記載、DB実測と整合確認済み。）

Qwen構造化2系統の実測コスト（2026-08-18T04:37:05Z時点、全リトライ完了後の確定値）:

| 系統 | 実行 | 実コスト |
|---|---|---:|
| Direct-Qwen | `reviewer-logic-direct-qwen-full-v1` + `retry-24k-v1` + `retry-32k-v1` 累計 | **$74.795158** |
| Compact-Qwen | `review-logic-qwen-2026-full` | $28.793265 |
| **Qwen構造化2系統 合算** | | **$103.588423** |

この合算はQwen構造化ステージのみの部分合計であり、DeepSeekメモ生成（Full Layered/Direct）やその他パイロットのコストを含まない。プロジェクト全体のコストを知りたい場合は、上記DeepSeek実コストとこのQwen合算、およびパイロット期間のロールアップ表を別々に足し合わせること（単一の「総コスト」フィールドは存在しない）。

## 8. 失敗・緩和策

### プロバイダのコンテンツポリシー拒否（`DataInspectionFailed`）— 未解決、トークン予算調整では直らない
DashScope（Qwen）が入力テキスト自体を拒否するHTTP 400エラー。`reviewer-logic-direct-qwen-full-v1`（マージ後の最終状態）に**32件**、`retry-32k-v1` 単独でも**32件**（同一forum集合）が最終的に確認されており、16k→24k→32kとトークン予算を3段階で上げても解消しなかった。**これは出力トークン長とは無関係な失敗モードであり、トークン予算の調整では解決しないことが確認されている**（ただし、入力内容自体の扱いを変える等の他の緩和策まで含めて「絶対に解決不能」と断定する根拠は本調査では確認できていない — 単に「トークン長調整では直らない」という限定的な事実のみが確定している）。コード上は非モデレーション系の失敗と区別されず（`qwen_reviewer_logic_direct.py` の `except Exception` で一律捕捉）、`prepare_qwen_direct_retry.py` の選定ロジックでも `provider_status != 200` は一律 `provider_non200` に分類されるため、次のリトライラウンドを組めば再選定されうる。実際の運用では、この32件はこれ以上リトライされず、`retry-32k-v1` 完了後に成功分（812件）のみが `full-v1` へマージされた（`docs/provenance/DECISION_LOG.md` 参照）。**Direct-Qwenトラックの厳密完了カバレッジは complete=50,861/51,813 (98.16%) で確定している**。この32件は残存952件のうち出力JSONが全く無い139件の一部（残りの107件はパース/切り詰め失敗で、これも別系統の未解決問題 — 下記参照）。出力JSONあり813件（大半は`invalid evidence ref`）についてはコード修正次第でローカル修復の余地がある。この4分類の内訳と対処方針は `docs/handoff/CLAUDE_CODE_HANDOFF.md` §2, §5 に詳述。

### パース/切り詰め失敗
`max_output_tokens` 不足によりプロバイダの応答自体が途中で切れ、解析可能なJSONとして完結しない失敗（`outputs/<id>.json` が生成されない）。この種の失敗は出力トークン予算を上げることで大部分が改善されており（full-v1の7,166件→24kリトライで大幅減、さらに32kリトライで921件→最終107件まで減少）、プロバイダのコンテンツポリシー拒否（32件）とは根本的に異なる、トークン予算調整で対処可能な失敗モードであることが実証されている。ただし全件が解消したわけではなく、32k化後も**107件が未解決のまま残っている**。この107件は出力JSONが存在しないため`reprocess`サブコマンドの対象にならず、`invalid evidence ref`等のセマンティック検証失敗（出力JSONあり813件）とは異なる、別の残存カテゴリとして扱う必要がある（詳細は `docs/handoff/CLAUDE_CODE_HANDOFF.md` §5-a）。

### 修復スクリプト（一度限りの手動修正）
- `scripts/repair_regional_02_chain_templates_20260816.py`（45行）— `episode-reclassification-3135/unmapped-discovery/regional-patterns-02.json` 内、汎用プレースホルダになっていた7件の `chain_template` を、マップ済みのローカルパターンから実際の文言へ復元。
- `scripts/migrate_episode_ids_20260816.py`（96行）— `episode-lite-1000` と `episode-deep-63` 内の、不正な接尾辞や `paper_id` と不整合なプレフィックスを持つエピソードID計28件をハードコードされた対応表で修正。`archive`/`backup`/`logs`/`prompt-tuning` を含むパスは明示的に除外（過去のスナップショットは意図的に非改変）。`docs/data-asset.md` と `docs/reviews/episode-deep-challenge-review-2026-08-16.md` が独立に「28 ID・43ファイル・489参照を移行」と記録し、相互に整合。

いずれの修復も**再生成不可能な手作業の一回限りの修正**であり、該当ステージを最初からやり直す場合は同じ修正を再適用する必要がある（`REPRODUCIBILITY_AND_LIMITATIONS.md` 参照）。

## 9. リネージュ表（生データ→最終成果物）

```
data/raw/iclr/openreview.sqlite3（collect_iclr_forums.py; 不変, forums=52,460, 9年分）
  │
  ▼ normalize_iclr.py（決定的, 生データは読み取り専用）
data/processed/iclr/analysis.sqlite3（papers=52,460, messages=792,703; provenanceのみの正規化）
  │
  ├─▶ prepare_iclr_direct_production.py + run_deepseek_pilot.py（supervise_iclr_direct.py 経由）
  │      → direct-2018-2026.sqlite3（forum_direct メモ, 51,813）
  │            └─▶ qwen_reviewer_logic_direct.py
  │                   → reviewer-logic-direct-qwen-{pilot-v1..v4, full-v1}
  │                   → retry-24k-v1 → merge_qwen_direct_retry.py → full-v1へマージ済み
  │                   → retry-32k-v1 → merge_qwen_direct_retry.py → full-v1へマージ済み
  │                        （最終: full-v1 complete=50,861/51,813=98.16%, failed=952）
  │
  └─▶ prepare_pilot.py/prepare_iclr_2026_production.py + run_deepseek_pilot.py（run_iclr_2026_full_layered.py 経由）
         → production-2026.sqlite3（initial_blind/trajectory/paper_synthesis メモ, 151,193）
               ├─▶ evaluation-episode-pilot / logic-pattern-pilot（設計探索）
               ├─▶ episode-lite-1000 / episode-deep-63（v0.2）
               │      → Atlas（10カード）→ episode-reclassification-3135
               │        → unmapped-discovery → new-card-screening（非正準）
               │        → new-card-challenge-audit → refinement-v2/confirmation-v2
               │        → atlas-13-consensus-v2-membership.jsonl【Atlas系の正典終点】
               ├─▶ episode-lite-2026-full/-v2/-v3/-single + 較正3点
               │      （全件方式は停止、-v3はcompactの入力DBとして再利用）
               │      └─▶ review-logic-compact-2026（Luna, 876/75,859で停止、基準として保持）
               └─▶ episode-lite-2026-full-single → qwen_review_logic_batch.py
                      → review-logic-qwen-{各pilot/control} → review-logic-qwen-2026-full
                      【Compact系Qwen本番、74,380/75,859】
```

Direct-Qwen系・Compact-Qwen/Luna系・Atlas-13系を横断する単一の最終統合成果物は**まだ存在しない**。`docs/output-backward-data-contract.md` は将来の下流成果物として Evaluation Logic Atlas / Pattern Dossier / Evidence Explorer の3つを名指ししているが、これらは目標であり、未だ提供済みの成果物ではない。
