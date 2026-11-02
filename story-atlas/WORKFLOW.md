# Story Atlas translation workflow

## 1. Source inventory, not runtime brute force

The QSP extractor retains each location's source once, with stable references to literal spans and candidate transitions. Static location/event/ACT/exec connections become shared nodes and edges. A complete edge inventory is separate from the DFS-order-dependent acyclic projection used for scheduling. Unresolved dynamic destinations/conditions remain flagged; static extraction does not prove runtime reachability.

所有運算、模型呼叫與正式儲存在遠端；瀏覽器只呈現工作台。公開的完整抽取資料不是只截取目前翻譯完成的節點。

## 2. Branch-aware batches and context

- Newly prepared batches use at most approximately **10,000 non-whitespace source characters**; atomic oversized units are flagged, not silently cut. Existing batches/results are preserved.
- Shared approved ancestor context is at most10,000 characters. No common ancestor means following the current path without borrowing sibling context.
- Common prefixes finish before independent branches run. At convergence, one owner translates the node; other route contexts are retained for review, not declared semantically resolved.
- Persistent branch jobs/nodes/batches/merges prevent duplicate source approvals. Requested batch quotas are not total-game completion counts.

## 3. Dictionary and model tasks

Search the latest approved dictionary for each batch, including polysemous senses, usage and literal name spellings. Lookup snapshots are audited privately. Known names keep the source's exact spelling/case; this is not a claim to detect every unknown person name.

All **new** requests use `deepseek/deepseek-v4.1-flash`:

1. Translate source into strict `translations` and `glossary_proposals` JSON.
2. Validate IDs, complete source coverage, protected fragments, known names, term IDs and every proposal citation.
3. Repair only invalid rows/proposals when eligible; keep already valid translations unchanged.
4. Review proposals in a separate glossary-review task with related dictionary senses and the exact same system snapshot, but a separate strict schema.
5. Validate all review decisions and atomically approve/reject the proposals; preserve reasons.
6. Machine-submit a fully valid batch with an explicit automatic-approval marker, not forged human review.

翻譯／局部修正`max_tokens=384000`，含推理，不是每次實際使用量；拆包目前停用。審詞原本未明訂輸出cap，沒有擅自降低或新增上限。歷史Pro結果保持當時真實模型，不改標為Flash。

## 4. Residual-only repair

- Up to three residual translation/proposal corrections; malformed local-correction JSON uses the same shared budget. Review format correction has its separate maximum of three extra calls.
- A proposal may split an incorrectly combined term or use an exact surface phrase from **its own already cited current source units**. Every new citation must contain the complete source term; unrelated valid rows/sibling sources remain forbidden.
- Never guess a source ID, silently strip an unwanted response field, assume omitted proposals mean an empty array, or retransmit an already valid full batch just because one proposal fails.
- When newly approved names make a saved machine draft fail final validation, seed a residual-only repair from persisted targets/decisions with the original system and unchanged old terms. Do not change the dictionary simply to bypass the check.
- Valid saved text can survive dictionary additions. Changes to the original system/source or referenced old glossary entries require stopping, not an automatic full retranslation.
- Failed accumulated results remain marked in the private DB and the public progress export. An explicit bounded resume is not an unlimited retry loop.

Verified repairs: batch821 had an upstream whitespace runaway ending with `length` at65,536 completion tokens; this is not evidence of a hard model maximum. Only glossary review was repeated after explicit authorization. Batch825 split source-evidenced terms;954 used source-evidenced surface spelling;923 changed one name-case row while preserving13 other rows. Batch981's proposal-only schema failure was corrected without changing its27 translation rows. Old attempts/reviews remained unchanged.

## 5. Spending, concurrency and failures

- The current user-authorized branch limit is **5**, distinct from the model-request gate.
- Below US$5, the per-session gate permits only one model POST at a time; others queue. Adequate balance restores up to the configured limit, subject to actual key RPM.
- RPM spacing includes headroom. Explicit output caps use a conservative UTF-8-byte input proxy plus the full cap and public model pricing; this is **not** an exact tokenizer or the provider's private reservation formula.
- A positive balance is not proof that a particular request's reservation fits. A preflight block is not a claim that the account balance is zero.
- Authentication/budget/rate-limit/server/network failures latch the gate and stop new sends. No automatic recharge/key change/model downgrade, no blind retry of402, refusal or truncated output.
- Only explicit continuation creates a fresh gate. The authorized1000-batch session resumes remaining slots; it does not add another1000-batch round.

## 6. Persistence and reporting

The live workbench uses one configured active workspace. Historical directory names do not make the production DB a disposable test. Durable journals and independent worker process lifetimes avoid relying on an SSH stdout pipe.

Refreshing the page sends read-only status requests, not model POSTs. Stop finishes already sent requests safely; it does not pretend to cancel provider work already in flight. Orphaned/pending work is not silently retried.

The GitHub snapshot is exported from a consistent SQLite online-backup copy while the live worker can continue. It includes saved draft/approved translations, glossary/proposals, source and branch references, retained issues, original quotas and current status. It omits raw SQLite, credentials and raw model request/response payloads; it is an interchange/reporting snapshot, **not** a full resumable production backup.

The earlier cost forecast used mixed Flash/Pro history; it is not a new all-Flash quotation. Reasoning is already part of completion tokens and must not be added twice. Flash glossary-review token behaviour has limited new calibration, so future costs and remaining total-game batches are estimates, not guarantees.
