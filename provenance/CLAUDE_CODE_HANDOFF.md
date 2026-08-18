# Claude Code 引き継ぎドキュメント

最終検証時刻: **2026-08-18T04:37:05Z (2026-08-18 13:37:05 JST)**。本書は前回版（32kリトライ進行中に作成）を、リトライ完了・マージ完了後に全面更新したもの。以降のセッションもこの節を read-only コマンドで再確認してから作業を始めること。

このドキュメントは、別セッションの Claude Code が **Codex の会話履歴を一切読まずに** このプロジェクトの続きを安全に着手できることを目的とする。プロジェクトルート: `/Users/s30825/unktok/dev/ml-top-conf-review-analysis`（Git 管理下ではない）。

データの由来・分析手法の全体像は `docs/provenance/` 配下（特に `docs/provenance/README.md`）を参照。本書は**運用状態と直近の完了作業**に特化する。

---

## 1. 結論（TL;DR）

`reviewer-logic-direct-qwen-retry-32k-v1`（959件、`max_output_tokens=32000`のリトライ）は**完了し、`reviewer-logic-direct-qwen-full-v1` へマージ済み**。現在このプロジェクトに**進行中のQwen/DeepSeekリトライやAPI課金プロセスは存在しない**（プロセス・tmuxセッションともに確認済みで残っていない）。次にやるべきことは新規実行ではなく、**残存失敗952件（出力JSON付き813件＝ほぼ`invalid evidence ref`、出力JSONなし139件＝パース/切り詰め107件＋プロバイダのコンテンツポリシー拒否32件、内訳は§2参照）をどう扱うかについての方針確認**である（§5参照）。

```bash
# 現在プロセスが無いことの確認（念のため自分でも実行）
ps aux | grep -i "qwen_reviewer_logic_direct" | grep -v grep    # 何も出なければOK
tmux list-sessions 2>&1                                          # "no server running" が正常
```

## 2. 完了した操作: 32kリトライとそのマージ

### 背景（簡潔に）
`reviewer-logic-direct-qwen-full-v1`（Direct手法・Qwen構造化、51,813 forum対象）に対し、2026-08-18 13:01 JST に一次リトライ `retry-24k-v1`（`max_output_tokens=24000`、7,270件）がマージ済み。そのマージ後もなお「出力が全く得られなかった」959件が残り、これを `max_output_tokens=32000` で再試行する二次リトライ `retry-32k-v1` を単離実行していた。

### 32kリトライの最終結果（確定値、2026-08-18T04:37:05Z時点で再検証済み）

```
complete | 812
failed   | 147
（合計 959、prepared/running は残っていない = 完全終了）
```

失敗147件の内訳（`state.sqlite3` を直接集計して検証）:

| カテゴリ | 件数 | 説明 |
|---|---:|---|
| `parse/validation exception:%`（パース/切り詰め失敗） | 107 | 32k化でもなお解決しなかった残存分 |
| コンテンツモデレーション拒否（`DataInspectionFailed`, HTTP 400） | 32 | プロバイダのコンテンツポリシー拒否、トークン予算調整では未解決（§5参照） |
| 無効なエビデンス参照（`invalid evidence ref: ...`） | 6 | HTTP 200・スキーマは通ったが自前の参照検証で失敗 |
| スキーマ検証エラー（`schema: ...`） | 2 | HTTP 200・自前のJSON Schema検証で失敗 |

実コスト: `actual_cost_usd = 1.355473`（マニフェストの上限 `declared_cost_cap_usd=6.0` に対し十分な余裕を残して完了）。

出力ファイル数: `outputs/`=820、`validations/`=959、`provider/`=959。`outputs`(820) と `complete`(812) の差8は「HTTP 200・JSON構文は成立したが自前検証で `failed` になった行にも `outputs/<id>.json` が書き出される」設計挙動によるもの（マージレコードの `failed_with_output: 8` と一致）。

**`scripts/run_qwen_direct_retry_32k_supervised.zsh` の既知の不具合と修正**: 全959件の処理が完了した**後**、スーパーバイザのzshスクリプトが `status` という zsh の読み取り専用変数名をローカル変数として使おうとしてエラーになっていた（`read-only variable: status`）。これはランナー終了後のスーパーバイザ側のログ出力コードの不具合であり、**959件全件のリクエスト永続化には一切影響していない**（全件がすでに `state.sqlite3` にコミット済みの時点で発生したエラーのため）。スクリプトは `exit_status` という変数名に変更して修正済み（`scripts/run_qwen_direct_retry_32k_supervised.zsh:33-37`）。今後同種のスーパーバイザラッパーを書く/複製する際は、zshの予約変数名（`status`, `pipestatus` 等）をローカル変数名に使わないよう注意すること。

### マージ結果（`reviewer-logic-direct-qwen-full-v1` へ適用済み）

- マージレコード: `data/analysis/iclr/reviewer-logic-direct-qwen-full-v1/merge-reviewer-logic-direct-qwen-retry-32k-v1.json`（`merged_at: 2026-08-18T04:31:44.724990+00:00`、内容を直接読んで検証済み）
- マージ前バックアップ（SQLiteオンラインバックアップAPIで自動生成、原本破壊なし）: `data/analysis/iclr/reviewer-logic-direct-qwen-full-v1/state-pre-retry-merge-20260818T043143Z.sqlite3`（13,307,904バイト、存在確認済み）。24kリトライ時の同種バックアップ `state-pre-retry-merge-20260818T040104Z.sqlite3`（11,415,552バイト）も引き続き存在し、上書きされていない。
- `retry_ancestry` は `retry-32k-v1 → retry-24k-v1 → full-v1` の3階層のネスト系譜として正しく検証・記録された（`merge_qwen_direct_retry.py` の `retry_ancestry()` 拡張が意図通り機能したことを確認）。
- `retry_attempts` テーブルは now `reviewer-logic-direct-qwen-retry-24k-v1|7270` と `reviewer-logic-direct-qwen-retry-32k-v1|959` の2グループを保持（直接クエリで確認済み）。
- **元の一次試行 `provider/<id>.json`（プロバイダ生証拠）は一切上書きされていない** — `retry-24k-v1/provider/` と `retry-32k-v1/provider/` はそれぞれのディレクトリにそのまま残存し、`full-v1/provider/` には元の一次試行分のみが存在する設計が維持されている。

### `reviewer-logic-direct-qwen-full-v1` の最終状態（マージ後、全件対象51,813）

```
complete | 50,861   (98.16%)
failed   |    952   (1.84%)
```
- `outputs/` ファイル数: **51,674 / 51,813（99.73%）** — これは `complete`(50,861) + `failed`だが出力JSONは書き出された行(813) の合計と一致する。
- `validations/` ファイル数: **51,813 / 51,813**（対象全件に検証記録あり）。
- **カバレッジの2つの定義を明確に区別すること**:
  - **厳密完了（strict-complete）**: `complete=50,861/51,813 = 98.16%`。分析の既定はこちらを使うこと。
  - **JSON取得済み（JSON-available）**: `51,674/51,813 = 99.73%`。`failed` だが出力JSONが存在する813件を「警告付きレコード」として明示的にフラグを立てたうえで集約分析に含める場合にのみ、この数字を根拠に使ってよい。フラグ付けせずに厳密完了と同列に扱わないこと。

`failed` 952件の内訳（`state.sqlite3` を直接集計、確定値）:

| 大分類 | 件数 | 小分類 | 件数 |
|---|---:|---|---:|
| **出力JSONあり（813件）** — 自前の正規化/検証ロジックで`failed`扱いになった行。プロバイダからのレスポンス自体は取得できている | 813 | 無効なエビデンス参照（`invalid evidence ref: ...`） | 709 |
| | | JSON Schema検証エラー（`schema: ...`） | 65 |
| | | その他の厳密/ローカル検証エラー | 39 |
| **出力JSONなし（139件）** — プロバイダから解析可能なJSONが得られなかった行。上記813件とは根本的に異なる失敗系統 | 139 | パース/切り詰め/不正なプロバイダJSON（`parse/validation exception:%`） | 107 |
| | | プロバイダのコンテンツポリシー拒否（`DataInspectionFailed`, HTTP 400） | 32 |

813 + 139 = 952、709 + 65 + 39 = 813、107 + 32 = 139 で全て整合。**出力JSONあり813件のみが `reprocess` サブコマンドによるローカル修復の対象候補**であり、それも正規化/検証ロジックの改善が前提で、修復を保証するものではない。出力JSONなし139件（特にパース/切り詰め107件）は`reprocess`の対象にならない — プロバイダの生応答自体が解析可能なJSONとして存在しないため、別のパース/修復戦略か、モデルへの新規リクエストが必要になる。詳細は §5。

### 累計コスト（Direct Qwen系）
- `reviewer-logic-direct-qwen-full-v1` + `retry-24k-v1` + `retry-32k-v1` の Direct-Qwen 系累計: **$74.795158**
- Compact-Qwen系（`review-logic-qwen-2026-full`）の実測コスト $28.793265 を加えると、Qwen構造化2系統の合算は **$103.588423**。この数字は Qwen構造化ステージのみの部分合計であり、DeepSeek メモ生成（Full Layered $314.42、Direct $194.75）やその他パイロットのコストは含まない点に注意（`docs/provenance/END_TO_END_PROCESS.md` §7 に全体のコスト内訳がある）。

## 3. 現在のプロセス状態（確認済み・進行中の処理なし）

- `ps aux` に `qwen_reviewer_logic_direct.py` を含むプロセスは存在しない。
- `tmux list-sessions` は "no server running" を返す（`iclr-qwen-retry-32k` セッションは自然終了済み）。
- `data/analysis/iclr/reviewer-logic-direct-qwen-retry-24k-v1/` と `.../retry-32k-v1/` のディレクトリ自体はそのまま残っている（削除されていない） — これらは今後もリトライ系譜の一次証拠として保持すること。

## 4. テストスイート（69テスト、全通過を確認済み）

```bash
uv run python -m unittest discover -s tests
```
実行結果: **`Ran 69 tests in 0.387s / OK`**（本ドキュメント更新時点で再実行し確認）。`pytest` は未インストールのため `unittest` を使う（`pyproject.toml` に開発依存として含まれていない）。

**既知の無害な警告**: `scripts/prepare_episode_lite_1000.py:194` 付近で `ResourceWarning: unclosed database in <sqlite3.Connection object ...>` が出る（テスト内でこのモジュールをインポート/実行する際に、明示的な `close()` がされていないSQLite接続がガベージコレクトされることによるもの）。これはテスト結果（69件 OK）には影響しない副次的な警告であり、**Direct-Qwenリトライ/マージとは無関係**。修正の緊急性は低いが、`prepare_episode_lite_1000.py` を将来編集する際は該当のSQLite接続に `with` 文または明示的な `close()` を追加するとよい。

全テストは合成フィクスチャ（`tempfile.TemporaryDirectory()` 上の一時SQLite等）に対する単体テストであり、実データ（`data/analysis/iclr/*.sqlite3` の本番ファイル）には一切触れない設計。したがって今後どのタイミングで実行しても安全。

## 5. 残存する未解決事項（次セッションが最初に判断すべきこと）

残存952件は性質の異なる2グループに分かれる。**「セマンティック検証失敗」や「恒久的に解決不能」という単一のラベルで一括りにしないこと** — 修復可能性も対処法もグループごとに異なる。

### 5-a. 出力JSONなし・139件 — パース/切り詰め(107件) + プロバイダのコンテンツポリシー拒否(32件)

**パース/切り詰め・不正なプロバイダJSON（107件）**: プロバイダの生応答自体が解析可能なJSONとして完結しなかった行。`outputs/<id>.json` は存在しない。32kへのトークン予算拡大でも解決しなかった残存分であり、**通常のセマンティック検証失敗として扱ってはならない**（`reprocess`の対象外 — ローカルに再解析できる完全なJSONペイロードがそもそも無い）。解決には別のパース/修復戦略（例えばプロバイダ側の応答を切り詰めずに再取得する等）か、モデルへの新規リクエストが必要。

**プロバイダのコンテンツポリシー拒否（32件、`DataInspectionFailed`, HTTP 400）**: DashScope（Qwen）が入力テキスト自体を「不適切な内容を含む可能性」として拒否するエラー。**16k→24k→32kとトークン予算を3段階で引き上げても、同一の32forum相当が一貫して失敗し続けている** — これは出力トークン長とは無関係な失敗モードであり、トークン予算の調整では解決しないことが確認されている。ただし「絶対に恒久的に解決不能」と断定はしない — 未解決の**プロバイダのコンテンツポリシー由来の失敗**であり、入力内容の扱い（例: 該当箇所の要約・言い換えでの再提出）を変更すれば解決する可能性は残る。現在の運用方針（コードでは強制されていない、Codexセッション上の言明）は「これらをリトライループへ戻さず、成功分のみを統合する」というもの。この方針は`docs/provenance/DECISION_LOG.md` に引用済みだが、**プロジェクトの正式な最終方針として文書化された宣言は確認できていない（UNVERIFIED）**。次セッションがさらなるリトライラウンド（例:「40kリトライ」）を検討する場合、この32件相当をトークン予算だけで解決しようとしても無駄な課金になる点に注意。

### 5-b. 出力JSONあり・813件 — ローカル`reprocess`の候補（保証なし）

HTTP 200・プロバイダのJSON応答自体は取得できているが、自前の正規化/検証ロジックで`failed`扱いになった行。内訳: 無効なエビデンス参照709件、JSON Schema検証エラー65件、その他の厳密/ローカル検証エラー39件。**`prepare_qwen_direct_retry.py` の `RETRIABLE` 選定ロジックは意図的にこれらをAPIリトライ対象から除外している**（`provider_status=200` かつエラーが `parse/validation exception:` で始まらないため、次のリトライラウンドには絶対に選ばれない — `tests/test_qwen_reviewer_logic_direct.py::test_retry_selection_excludes_semantic_validation_only_failures` で担保）。この813件を直せる可能性がある唯一の経路は **新規API課金なしの `reprocess` サブコマンド**:
```bash
uv run python scripts/qwen_reviewer_logic_direct.py reprocess \
  --output data/analysis/iclr/reviewer-logic-direct-qwen-full-v1 [--max-requests N]
```
これは既に保存済みの `provider/<id>.json`（一次試行のプロバイダ生応答）に対してローカルの正規化/検証ロジックだけを再実行する。**ただし正規化/検証コードそのものにバグ修正・改善を加えない限り、単に再実行しても同じ結果になる可能性が高く、修復は保証されない**。まず `scripts/qwen_reviewer_logic_direct.py` の正規化/検証関数（`normalize_payload()` 等）に本当に直せる不具合があるか調査してから実行を検討すること。

### 5-c. 次のアクション候補（優先順位順、次セッションの判断に委ねる）
1. Director（プロジェクト所有者）に、厳密完了カバレッジ50,861/51,813 (98.16%) を「Direct-Qwenトラックの現時点での確定値」として扱ってよいか、特にプロバイダのコンテンツポリシー拒否32件への追加対応方針を確認する。
2. 出力JSONあり813件（`invalid evidence ref`が大半）が `reprocess` で救えるものかどうか、`normalize_payload()` のロジックを読み、必要なら小規模サンプルで調査する（新規API課金なし）。
3. 出力JSONなし107件のパース/切り詰め失敗について、どのような再解析・再取得の戦略が現実的か検討する（`reprocess`では直せない点に注意）。
4. `docs/provenance/ARTIFACT_REGISTRY.md` および `docs/provenance/END_TO_END_PROCESS.md` の該当箇所は本更新で最新化済みだが、今後さらに手を加えた場合は両ドキュメントも追随して更新すること。

## 6. マージ手順の参考情報（今後another retryを行う場合のために保持）

マージスクリプト: `scripts/merge_qwen_direct_retry.py`（257行、`retry_ancestry()`/`validate()`/`apply_merge()` の3関数構成）。設計:

- `validate()` はretry側 `state.sqlite3` に `prepared`/`running` が残っていると `RuntimeError` を投げる（未完了リトライの誤マージを防止）。
- `retry_ancestry(source_run, retry_run)` は `manifest.json["source_run"]` を最大32階層まで遡り、指定した `--source-run` に到達するか検証する。ネストしたリトライ（今回のように24k→32kと連鎖する場合）でも、真の正規sourceへ直接マージできる。
- `--apply` 時、適用前に必ず `state.sqlite3` を SQLite オンラインバックアップAPIで `state-pre-retry-merge-<UTCタイムスタンプ>.sqlite3` として保存してから変更を加える。**一次試行の `provider/<id>.json` とusage列は一切上書きしない**。
- 実行コマンド（ドライラン→適用の順、今回はこの手順で完了済み）:
```bash
# ドライラン（副作用なし）
uv run python -m scripts.merge_qwen_direct_retry \
  --source-run data/analysis/iclr/reviewer-logic-direct-qwen-full-v1 \
  --retry-run  data/analysis/iclr/reviewer-logic-direct-qwen-retry-32k-v1
# 適用
uv run python -m scripts.merge_qwen_direct_retry \
  --source-run data/analysis/iclr/reviewer-logic-direct-qwen-full-v1 \
  --retry-run  data/analysis/iclr/reviewer-logic-direct-qwen-retry-32k-v1 \
  --apply
```

## 7. 資格情報（credential）の契約 — 値は絶対に出力しない

| 変数名 | 用途 | 取得方法 |
|---|---|---|
| `DASHSCOPE_API_KEY` | Qwen (DashScope) | `scripts/run_qwen_direct_retry_32k_supervised.zsh` が起動時に一度だけ 1Password CLI（`op item get <item_id> --vault unktok --format json`）で解決し、子プロセスの環境変数へexport。以降はメモリ上の値を使い回し、リクエストごとに1Passwordを呼ばない。 |
| `DEEPSEEK_API_KEY` | DeepSeek | `run_iclr_2026_full_layered.py` / `run_deepseek_pilot.py` / `supervise_iclr_direct.py` が `os.environ` から読む。1Password連携の自動化コードは確認できず（UNVERIFIED）、起動元シェルでの事前exportが前提と見られる。 |
| `OPENREVIEW_USERNAME` / `OPENREVIEW_PASSWORD` | OpenReview収集 | `collect_iclr_forums.py` / `inventory_iclr.py` が `os.environ` から読む。 |

いずれの値も本ドキュメント作成過程で一度も出力・コピーしていない。

## 8. 安全上の注意（再掲）

- ドキュメント以外のファイル（コード・データ・DB・マニフェスト・プロンプト・スキーマ・状態・実行中プロセス・認証情報）を無断で変更しない。
- API キー・トークン・Cookie・1Passwordの値を絶対に出力・コピーしない。
- 有償API呼び出しを新規に発行する前に、必ずDirectorに意図を確認する（特に §5 の残存失敗への対応方針）。
- `state.sqlite3` 系のファイルを読む際は、可能なら `sqlite3 -readonly` を使う。ただし環境によっては `-readonly` フラグ自体がサンドボックス制約で `unable to open database file` エラーになることがある（本ドキュメント作成中にも発生）。その場合は `sqlite3 <path> "PRAGMA query_only=ON; <query>;"` を代替手段として使うこと（書き込みは行われない）。
- ファイルが存在するだけで完了と主張しない。件数・タイムスタンプを実測して記述する。

## 関連ドキュメント
- `docs/provenance/README.md` — データ由来ドキュメント群の入口
- `docs/provenance/END_TO_END_PROCESS.md` — 収集からLLM分析までの全体プロセス
- `docs/provenance/ARTIFACT_REGISTRY.md` — 全成果物の分類台帳
- `docs/provenance/DECISION_LOG.md` — 意思決定の時系列記録
- `docs/provenance/SESSION_EVIDENCE_INDEX.md` — セッション履歴の引用索引
- `docs/provenance/REPRODUCIBILITY_AND_LIMITATIONS.md` — 再現性の限界
