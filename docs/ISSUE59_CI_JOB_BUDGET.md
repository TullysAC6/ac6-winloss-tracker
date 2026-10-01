# Issue #59 cumulative CI-job budget repair

Owner resumed this maintenance slice on 2026-10-01 ([authorization/evaluation checkpoint](https://github.com/TullysAC6/ac6-winloss-tracker/issues/59#issuecomment-5928158022)). It supersedes the previous CI-duration deferral only; the separate unexplained 15-second readiness/Node causes remain unresolved.

## Verified evidence before implementation

Base main: `848674115f842f7fcb22ce8c3fccc598502e7c45`, PR #63 / #15-4. [Main run36834524203 attempt1](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/36834524203) was cancelled by the explicit `maximum execution time of 20m0s` annotation, not by a test assertion. Python3.12/3.13 succeeded. Python3.14 T0/T1/T2 passed before the source-install interruption; that interrupted scenario's completion/cleanup is not claimed. The failed run is retained and is not rerun.

| Runtime | T0 | T1 | T2 | Source-install |
|---|---:|---:|---:|---:|
| 3.13 | 65s | 20s | 431s | 473s, PASS |
| 3.14 | 127s | 20s | 484s | 468s before outer cancellation |

The existing setup/distribution/audit work also consumes the same job budget. Splitting source-install removes this cumulative coupling without changing the inner deadlines.

## Minimal job boundary

- Existing `windows-tests` retains its 3.12/3.13/3.14 matrix, full ordered T0/T1/T2 on3.13/3.14, distribution/compatibility checks, dependency audit and opt-in native tests. Its outer20min budget is unchanged.
- `windows-source-install` runs the unchanged `tests/test_source_install_flow.ps1 -PythonPath (Get-Command python.exe).Source` on3.13/3.14, on independent Windows runners with20min each and `fail-fast: false`. Every scenario, assertion, failure-injection, readiness/watchdog timeout and cleanup remains in the original unmodified script.
- Pinned checkout/setup actions and the same hash-verified, binary-only lock install prepare its base interpreter; the legacy shared-interpreter scenario requires those packages. Reusing a core-job venv would introduce artifact/path coupling and would not be the smallest safe change. Pip cache is retained. The core audits the lock; overall success still requires that audit. No duplicate T0/T1/T2, audit or distribution suite is added to source-install.
- Fixture roots, ports, namespaced windows/mutexes and ownership are created by the existing script itself; no core-job output is consumed. Source-install copies the pristine checked-out source and builds its own fixture. Jobs run independently; no cross-runner process state exists.
- The existing required check name `Windows tests` remains. Its `always()` aggregate waits for both matrices and accepts only success/success. Failure, cancellation and skip remain failing gate results, visible separately in the run. The small aggregate has a5min execution bound instead of the default360min; no test watchdog is altered.

Maximum configured execution budget is100 runner-min for the five matrix entries plus5min for aggregation. With available runners the dependency path is at most25 execution minutes (max20 for independent matrices + max5 for the gate), excluding queue time. Observed runtimes are evidence, not correctness thresholds or a promised performance result.

[GitHub workflow semantics](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idneeds) document arrays of required jobs and `always()` after dependency failures. Local static contracts pin matrices/budgets/sole invocation/no error masking; the actual extracted PowerShell gate is executed against all16 pairs of success/failure/cancelled/skipped.

## Verification and final state

Relevant workflow/static checks and fresh independent review precede exact-head CI, pinned merge and exact-main CI. Resolve Issue #59 / its PR for live hashes, gates, review and closure; this document does not predict CI outcomes. T3 N/A for CI-only scheduling.

#15-4 product code, fixtures and recognizer/persistence behavior are unchanged. Once the new exact main is green, its unchanged product bytes and full verification are recorded for #15-4, keeping owner Acceptance PENDING and Released NO. Old cancellation and Node failure evidence stay historical; no old run is described as green. Public stable remains v1.2.0; STOP before the next #15 slice. No installer, readiness, inner test deadline, workflow trigger, permission, skip or retry policy changes are authorized here.

