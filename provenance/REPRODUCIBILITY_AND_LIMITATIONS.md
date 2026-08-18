# 再現性の限界と方法論的制約

本書は「もう一度最初から実行したら同じ結果になるか」「現在の成果物をどこまで信頼できるか」を評価するための、既知の制約・ギャップの一覧。過大な確信を避けるため、検証できていない点は明示的に UNVERIFIED と記す。

## 1. LLMサンプリングの非決定性

- **DeepSeek**（`scripts/run_deepseek_pilot.py`）: `enable_thinking` 既定（disabled）時は `temperature=0.4` を明示設定。`seed` パラメータは未設定。
- **Qwen**（`scripts/qwen_review_logic_batch.py`, `scripts/qwen_reviewer_logic_direct.py`）: `temperature` も `seed` も**未設定**（プロバイダ既定値に依存）。
- プロジェクト全体で使われる `seed` 値（例: `prepare_episode_lite_1000.py`, `prepare_logic_pattern_pilot.py`, `prepare_evaluation_episode_pilot.py` の `seed=20260816` 等）は、**どの論文/レビューをサンプルに含めるかという入力選定の決定性**のためのものであり、LLM呼び出し自体の出力再現性を保証するものではない。
- **結論**: 同じ入力（メモ・プロンプト・スキーマ）で再実行しても、モデル側の非決定性により、生成される自由文メモや構造化JSONの字句レベルの内容が完全に同一になる保証はない。スキーマ制約付きJSON出力は構造は安定しやすいが、内容（`observation`, `reasoning` などの自由記述フィールド）は再現保証の対象外。

## 2. プロトコル/スキーマのハッシュ検証の適用範囲は限定的

`protocol_sha256` / `schema_sha256` によるハッシュ固定は**プロジェクト全体の慣行ではない**。確認された適用範囲:

- 採用しているスクリプト: `prepare_qwen_direct_retry.py`, `qwen_review_logic_batch.py`, `qwen_reviewer_logic_direct.py`, `run_review_logic_compact_2026.py` の4本のみ。
- 適用されているマニフェスト: `review-logic-qwen-*` 系・`reviewer-logic-direct-qwen-*` 系・`review-logic-compact-2026` の計16件のみ。
- 並行するLunaパイプライン（`episode-lite-2026-full-v3`）は**別名で重複しない**ハッシュ体系（`record_schema_sha256`, `batch_schema_sha256`, `source_manifest_sha256`, 独自の `protocol_sha256`）を使用。
- `episode-lite-1000` / `episode-deep-63` / `episode-reclassification-3135` / `new-card-*` / `unmapped-discovery` / Atlas系 —（分析成果物のかなりの割合を占める）— は**ハッシュ検証を一切行っていない**。この系統の来歴保証は、ファイルパスとドキュメント間の相互参照に依存しており、暗号学的な内容固定はされていない。

**含意**: 「このマニフェストのプロトコル/スキーマは本当にこのバージョンか」を機械的に検証できるのは一部の実行だけであり、Episode/Atlas系の大部分は目視・ドキュメント突き合わせでしか来歴を確認できない。

## 3. 生データの上流での変化・消失リスク

- `docs/iclr-data-inventory.md` は、OpenReviewの公開APIが「完全な内容編集履歴を公開しない」ことを明記している。収集されたレビュー本文は「公開時点のレビューフォーム」であり、査読者による事後編集や匿名性の解除・撤回など、OpenReview側の可視性ウィンドウの変化を本プロジェクトは検知・追跡できない。
- 収集完了後の再取得による独立検証（ライブAPIとの突合）は実装されていない（`docs/provenance/END_TO_END_PROCESS.md` §3参照）。したがって、収集後にOpenReview側でデータが変わった場合、本プロジェクトの `data/raw/` はそれを検知せず、古い状態のまま「不変」であり続ける。

## 4. 手作業・エージェント判断による、スクリプト化されていない意思決定

以下は再実行時に**同じ結論に至る保証がない**、人間/エージェントの判断ポイント:

- プロンプト反復の凍結タイミング（例: `review-logic-compact-pilot-v3` でプロンプトを確定した判断）
- 較正構成の採用決定（`episode-lite-calibration-r8-medium-v3` を採用構成とした判断）
- Atlas系の2段階コンセンサス審査（`atlas-13-consensus-v2` における合議的判定）
- 全件エピソード抽出を「速度面で断念し較正資産として保持する」という停止判断（`docs/iclr-2026-full-episode-lite-rollout.md`）

これらは `docs/reviews/*.md`（`docs/reviews/episode-deep-challenge-review-2026-08-16.md` など）やCodexセッション履歴には記録されているが、スクリプトの実行パラメータとしては再現不可能な形で存在する。ゼロから再実行する場合、同じ結論に到達するには同じ人間/エージェントの判断を再現する必要がある。

## 5. 一度限りの手動修復（再現不可能な補正）

- `scripts/migrate_episode_ids_20260816.py` — 28件のエピソードIDをハードコードされた対応表で修正。`episode-lite-1000` / `episode-deep-63` のみが対象、`archive`/`backup`/`logs`/`prompt-tuning` パスは意図的に除外。
- `scripts/repair_regional_02_chain_templates_20260816.py` — 7件の `chain_template` プレースホルダを手動復元。

いずれも**元の生成プロセスから導出可能ではない**、その時点でのバグ・データ品質問題に対する個別対応であり、該当ステージを最初からやり直す場合はこれらの補正を意識的に再適用しなければならない。

## 6. 残存失敗の4分類とカバレッジの2定義（確定値）

`reviewer-logic-direct-qwen-retry-32k-v1`（959件対象）は2026-08-18T04:31頃に完了し（complete=812, failed=147）、`reviewer-logic-direct-qwen-full-v1` へマージ済み。マージ後の最終状態（2026-08-18T04:37:05Z確認）は `complete=50,861/51,813 (98.16%)`, `failed=952 (1.84%)`。

**カバレッジには2つの定義があり、混同しないこと**:
- **厳密完了（strict-complete）**: `complete=50,861/51,813 = 98.16%`。分析の既定値。
- **JSON取得済み（JSON-available）**: `51,674/51,813 = 99.73%`（= complete 50,861 + 出力JSONありの失敗813）。警告付きレコードであることを明示的にフラグ立てたうえで集約分析に使う場合にのみ根拠にしてよい。

残存952件は性質の異なる4分類に分かれ、**単一の「セマンティック検証失敗」や「恒久的に解決不能」というラベルで一括りにしてはならない**（直接集計、確定値）:

| 分類 | 件数 | 出力JSON | `reprocess`対象 | 性質 |
|---|---:|---|---|---|
| 無効なエビデンス参照 | 709 | あり | 候補（修復保証なし） | HTTP 200・JSON成立、自前の参照検証のみ失敗 |
| JSON Schema検証エラー | 65 | あり | 候補（修復保証なし） | HTTP 200・JSON成立、自前のスキーマ検証のみ失敗 |
| その他の厳密/ローカル検証エラー | 39 | あり | 候補（修復保証なし） | 同上 |
| パース/切り詰め/不正なプロバイダJSON | 107 | **なし** | **対象外** | プロバイダ応答自体が解析可能なJSONとして完結せず。32k化後も未解決 |
| プロバイダのコンテンツポリシー拒否（`DataInspectionFailed`, HTTP 400） | 32 | **なし** | **対象外** | 入力テキスト自体がプロバイダに拒否される。16k→24k→32kと3段階で引き上げても同一forum集合が失敗し続けた |

- 出力JSONあり計813件は `prepare_qwen_direct_retry.py` の選定ロジックが意図的にAPIリトライ対象から除外しており（`provider_status=200` の行は再試行されない設計）、新規API課金なしの `reprocess` サブコマンドでのみ修復されうる**候補**だが、正規化/検証コード自体の改善が前提であり、修復は保証されない。
- パース/切り詰め107件とコンテンツポリシー拒否32件（計139件、出力JSONなし）は`reprocess`の対象にならない。前者は出力トークン予算の拡大で大部分は解消したが全件は解消しなかった残存分、後者は「トークン予算調整では解決しないことが確認されている」失敗であり、それ以上の緩和策（入力内容の扱いの変更等）が試されたかどうかは今回の調査で確認できておらず、「絶対に恒久的に解決不能」と断定する根拠はない。

**結論**: Direct-Qwenトラックの厳密完了カバレッジは `50,861/51,813 = 98.16%` で確定している。100%到達への明確な道筋は本調査では見つかっていないが、内訳の性質ごとに改善余地の有無が異なる点（813件はコード改善で改善しうる、139件は別の戦略が必要）を区別して扱うべきである。

## 7. 完全性チェックの構造的な欠落

- 生データ収集の「完全性」は同一収集実行内での自己整合性（1ページ目で取得した期待件数と、その実行自身の最終件数の一致）のみで判定されており、収集完了後にライブOpenReviewへ再度問い合わせて突き合わせる独立検証は実装されていない（`inventory_iclr.py` はライブ件数を取得するが `data/raw/` とは突き合わせない）。
- したがって「52,460件のforumを完全に収集した」という主張は、**その収集実行自身が報告する完全性**に基づくものであり、独立した第三の情報源による裏付けはない。

## 8. テストカバレッジの限界

- `pytest` は未インストール。全テストは `unittest` ベースで、`uv run python -m unittest ...` で実行する。
- `normalize_iclr.py` の `classify_kind`/`classify_role` を検証するテストは `tests/test_pipeline.py::test_review_kind_and_role_are_provenance_only` の1件のみで、happy-pathを1ケースずつ確認する程度であり、21パターンの `KIND_PATTERNS` や役割判定の全分岐を網羅していない。
- `collect_iclr_forums.py`（生データ収集）と `normalize_iclr.py`（正規化）に専用のテストファイルは存在しない（`test_collect_*.py` / `test_normalize_*.py` 相当のファイルは無い）。
- 全テストディレクトリを `uv run python -m unittest discover -s tests` で一括実行し、**69テスト全通過**を確認済み（2026-08-18T04:37:05Z時点、32kリトライのマージ完了後に再実行）。全て合成フィクスチャ（`tempfile.TemporaryDirectory()` 上の一時SQLite等）に対する単体テストで、実データには触れない。`scripts/prepare_episode_lite_1000.py:194` 付近で無害な `ResourceWarning: unclosed database` が出るが、テスト結果自体には影響しない（未クローズのSQLite接続によるもの、Direct-Qwenリトライ/マージとは無関係）。

## 9. 未解決のまま残っている論点（本調査で確定できなかったこと）

- 残存952件（出力JSONなし139件＝パース/切り詰め107件＋プロバイダのコンテンツポリシー拒否32件、出力JSONあり813件＝無効なエビデンス参照709件＋Schema検証65件＋その他39件、§6）を「今後も一切追わない」ことがプロジェクトの最終方針として正式に確定しているか、それとも一時的な保留かは、セッション履歴上でも明示的な最終宣言としては見つかっていない（UNVERIFIED）。特に出力JSONあり813件は `reprocess` サブコマンドで新規課金なしに再挑戦できる可能性があり、コード改善の要否も含めて未着手のまま残っている。出力JSONなし139件は`reprocess`の対象外であり、別の対処戦略が必要。
- DeepSeek用APIキー（`DEEPSEEK_API_KEY`）が1Passwordから自動解決される仕組み（Qwen側の `run_qwen_direct_retry_32k_supervised.zsh` に相当するラッパー）の存在は確認できなかった（UNVERIFIED）。
- Episode系・Compact/Luna系・Direct/Qwen系という3系統を横断して統合する最終成果物（`docs/output-backward-data-contract.md` が言及する Evaluation Logic Atlas / Pattern Dossier / Evidence Explorer）は、本調査時点ではまだ実装・生成されていない目標として記述されているのみで、実体は存在しない。
- `data/analysis/iclr/episode-lite-1000/manifest.json` の `selection_count=1000` に対し、実測エピソード数が3,176件（`content-quality-report.json` 系の数値）と報告されている一方、`docs/data-asset.md` は後段の再分類後の母集団として3,135という数字を用いている。両者の関係（1,000レビュー→3,176生エピソード→3,135再分類後、という解釈でよいか）は本調査では完全には裏付けきれていない（PLAUSIBLE、要追加確認）。

## 使い方の指針

新しいセッションがこの成果物を利用・拡張する際は:

1. 「PRIMARY」と分類された成果物であっても、上記1〜7の制約を踏まえたうえで**カバレッジ・再現性の限界付きの正典**として扱うこと。
2. 数値を引用する際は、本ドキュメント群に記載の検証時刻（2026-08-18T04:27:30Z前後）を明記し、変化しうる値（特にDirect-Qwenトラックの完了件数）は自分で再クエリすること。
3. 新たな知見・修復・意思決定を追加した場合は、`docs/provenance/DECISION_LOG.md` と本ドキュメントを合わせて更新すること。
