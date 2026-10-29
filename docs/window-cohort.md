# Full retained-window cohort across offline and online stores

The native bounded workload processes 256, 1,024 and 5,760 finalized windows from retained Kafka/Flink output. The full cohort covers 57,600 readings and 20 machines. Each size runs isolated offline write/replay and online write/replay containers with the actual SDK.

The full run records 355.499 seconds offline,9.403 seconds offline replay,147.789 seconds online and 46.298 seconds online replay:558.989 seconds total. All 12 containers exit 0 without OOM, using two CPU/fourGiBlimits, non-root execution, read-only inputs and dropped capabilities.

Readback checks complete offline history and the latest online state;80 values and 80 timestamps are independently inspected. Both replays produce 5,760 no-ops and preserve the expected bytes/state. The scale/time/resource admission uses measured pilots and remaining authorized session time.

This is a Docker/SDK run over the retained 22 OctFlink source, not a new full Kafka run or an Airflow job at the same scale. CI/Airflow uses its own three-window fixture and original UI captures.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
python -S scripts/verify-window-cohort.py
python -S scripts/verify-final-handover.py
python -m unittest discover -s tests -p test_window_cohort.py -v
python scripts/replay-window-cohort.py --help
```

```bash
test -n "${IOT_FEAST_REDIS:-}" || exit 1
cohort_id="practice-$(date -u +%Y%m%dT%H%M%SZ)"
cohort_dir="$PWD/data/$cohort_id"
test ! -e "$cohort_dir" || exit 1
sudo install -d -o 10001 -g 10001 "$cohort_dir"/{offline,offline-reports,online,online-reports}
parent_sha=48fca351b74c88a746611b2e103ac9023cd30bbcad321bf933c597eea33db5bf
common=(--rm --network host --user 10001:10001 --cpus 2 --memory 4g --memory-swap 4g --pids-limit 256 --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,size=128m --env IOT_FEAST_REDIS -v "$PWD:/src:ro")
source_args=(--parent /src/docs/evidence/day3/stream-report.json --parent-sha256 "$parent_sha" --rows 256 --source-id "$cohort_id" --state /work/offline)
for op in write replay; do
  docker run "${common[@]}" -v "$cohort_dir/offline:/work/offline:rw" -v "$cohort_dir/offline-reports:/work/offline-reports:rw" iot-coursework-feast-versioned:0.67.0-3.17.2 /src/scripts/replay-window-cohort.py "${source_args[@]}" --destination offline --operation "$op" --output "/work/offline-reports/$op.json" || exit 1
done
for op in write replay; do
  docker run "${common[@]}" -v "$cohort_dir/offline:/work/offline:ro" -v "$cohort_dir/offline-reports:/work/offline-reports:ro" -v "$cohort_dir/online:/work/online:rw" -v "$cohort_dir/online-reports:/work/online-reports:rw" iot-coursework-feast-versioned:0.67.0-3.17.2 /src/scripts/replay-window-cohort.py "${source_args[@]}" --destination online --operation "$op" --online-state /work/online --offline-report /work/offline-reports/replay.json --output "/work/online-reports/$op.json" || exit 1
done
```

## Implementation and evidence

- [Recorded artifact: window-cohort-linux/run/preflight.json](evidence/window-cohort-linux/run/preflight.json)
- [Recorded artifact: window-cohort-linux/run/budget.json](evidence/window-cohort-linux/run/budget.json)
- [Recorded artifact: window-cohort-linux/run/full/verification.json](evidence/window-cohort-linux/run/full/verification.json)
- [Recorded artifact: window-cohort-linux/history/state-readback.json](evidence/window-cohort-linux/history/state-readback.json)

## Recorded interface views

These are original captures from the runs described above. Their timestamps, model identities and visible states belong to those runs.

![Recorded offline running](evidence/window-cohort-linux/screenshots/offline-running.jpg)

![Recorded online running](evidence/window-cohort-linux/screenshots/online-running.jpg)

![Recorded full success](evidence/window-cohort-linux/screenshots/full-success.jpg)


## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](demo-walkthrough.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/window-cohort.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
