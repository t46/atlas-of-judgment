# データ由来（Provenance）ドキュメント群 — 入口

このディレクトリは、`/Users/s30825/unktok/dev/ml-top-conf-review-analysis` プロジェクトが OpenReview からの生データ収集を起点として、どのような変換・LLM分析を経て現在の成果物に至ったかを記録する。**データ層構築当時の運用状態（当時進行中だったタスクの監視・再開・マージ手順）は `CLAUDE_CODE_HANDOFF.md`（同ディレクトリ、歴史的記録） を参照** — 本ディレクトリは由来・方法論に特化する。

本プロジェクトは Git 管理下にない。証跡は主にファイルシステムのタイムスタンプ、SQLiteデータベースの内容、およびローカルの Codex セッション履歴（`/Users/s30825/.codex/sessions/2026/08/11/rollout-2026-08-11T18-17-08-019ff01c-83cb-7060-ac21-3658f8b4a748.jsonl`、約34MB・約2万行、本ドキュメント作成時点でも成長中）に依拠する。

最終検証時刻: **2026-08-18T04:27:30Z (13:27:30 JST)**。以降に変化しうる件数（特に `reviewer-logic-direct-qwen-retry-32k-v1` の進行状況）は明示的に「検証時点」を付記している。

> **追記 (2026-08-27):** データ層はこの検証時点で確定し、以後変更されていない（retry タスクも完了済み）。以降の作業はすべて分析・可視化・サイト層で、その経緯は `method.html` の §10（訂正記録）・§11（各図版の由来台帳）と本リポジトリのコミット履歴に記録されている。上記の Codex セッション履歴も成長を終え、サニタイズ版が `codex-history/` に公開済み。

## 3層の証拠区分（最重要の前提）

本プロジェクトの成果物を読む際、常に以下の3層を混同しないこと:

1. **人間のレビュー（生データ）** — OpenReview 上の実際のレビュー・コメント・決定。`data/raw/iclr/openreview.sqlite3`（収集直後・不変）および `data/processed/iclr/analysis.sqlite3`（provenance のみを付与した正規化形）に存在。
2. **DeepSeek の分析メモ（analytic memo）** — スキーマ制約のない自由記述の英語 Markdown。`data/analysis/iclr/production-2026.sqlite3` および `data/analysis/iclr/direct-2018-2026.sqlite3` の `memos` テーブルに存在。人間レビューの「要約」ではなく「メタサイエンス的な分析」である。
3. **Qwen（および一部 Luna）による構造化レコード（structured record）** — DeepSeek メモをスキーマ準拠 JSON（`schemas/review-logic-compact-v0.1.json`, `schemas/reviewer-logic-direct-v0.1.json` など）へ正規化した層。生レビューを再分析しているのではなく、**DeepSeekメモに対する正規化レイヤー**である（`docs/qwen-batch-review-logic-rollout.md` に明記）。

この3層はそれぞれ別のスクリプト・別のDBに存在し、後段のドキュメントでも常にどの層の話かを明示する。

## ドキュメント一覧

| ファイル | 内容 |
|---|---|
| [`END_TO_END_PROCESS.md`](./END_TO_END_PROCESS.md) | 研究課題の変遷、会議/年範囲の選定理由、生収集→正規化→DeepSeek→各分析ファミリー→マージまでの全プロセス、選定ルール・件数・コスト、失敗と緩和策 |
| [`ARTIFACT_REGISTRY.md`](./ARTIFACT_REGISTRY.md) | `data/` 配下の全成果物（DB・ディレクトリ・スキーマ）を PRIMARY / DERIVED / SUPERSEDED / EXPERIMENTAL / BACKUP に分類した台帳 |
| [`DECISION_LOG.md`](./DECISION_LOG.md) | 主要な意思決定を時系列で記録し、セッション履歴の該当箇所を引用 |
| [`SESSION_EVIDENCE_INDEX.md`](./SESSION_EVIDENCE_INDEX.md) | 他ドキュメントが引用しているセッション履歴の該当箇所（タイムスタンプ・検索語）の索引 |
| [`REPRODUCIBILITY_AND_LIMITATIONS.md`](./REPRODUCIBILITY_AND_LIMITATIONS.md) | 再現性の限界、方法論的な制約、未解決の証拠ギャップ |
| [`CLAUDE_CODE_HANDOFF.md`](./CLAUDE_CODE_HANDOFF.md) | 当時の運用状態・進行中タスクの監視/再開/マージ手順（歴史的記録） |
| [`HF_DATASET_CARD.md`](./HF_DATASET_CARD.md) | Hugging Face データセット（`t46/atlas-of-judgment`）のカード原稿 |

## 読み方の指針

- すべての件数・パス・スキーマは実ファイル・実DBに対する read-only クエリで検証されたものを優先し、Codex セッション履歴からの引用は「決定の経緯」を補うためだけに用いる。
- 「完了」は成果物の存在だけでは判断していない。件数・整合性チェックの結果を明記する。
- 不明・未検証の点は各ドキュメント内で明示的に "UNVERIFIED" と記す。憶測で埋めない。
