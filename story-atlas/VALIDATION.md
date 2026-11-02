# Published snapshot validation

Validated on the remote computation host on2026-11-02, before publishing:

- All1,478 snapshot files matched their manifest size and SHA-256.
- All1,397 location records and747,993 literal spans were checked.
- Complete19,168 static nodes and141,109 edge indices matched the all-edge list; kept/excluded cyclic edge sets are disjoint and cover the original list.
- All248,199 catalog units were retained:235,431 eligible and12,768 review.
- Every exported table row count matched the snapshot summary; translated source IDs were valid and unique per run.
- Current-round approved slots matched **397/1000** in this snapshot. This is a dated count, not a live progress promise.
- The exported workbench code independently passed **181 mock/unit tests** and Node's `translate.js` syntax check; tests sent no paid model requests.
- Publication security scanning compared file contents (including decompressed data) against locally held live secret values without copying/outputting them. **0 secret matches**; no credential, raw database, log, backup, game binary/media or player-save files were included.

The first standalone verifier expected an edge `id` property; the source format actually uses array indices. The verifier was corrected against the existing parser and rerun successfully. This was a validation-tool error, not a change to the source graph or a reason to renumber it.

These checks prove file/data consistency for the stated inventory, not complete runtime reachability, universal translation quality, or completion of all game text. The public JSONL snapshot intentionally lacks raw model audits/credentials and is not a complete directly resumable production backup.
