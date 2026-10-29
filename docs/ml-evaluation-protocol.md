# ML evaluation protocol: medium-60d-v1

The protocol was fixed before the corresponding training runs. It uses the verified 200-machine, 60-day source of 5,587,984,353 offline bytes. The separate 102.45 GB ingest result is not its training dataset.

| Partition | Simulated event-time interval, UTC | Additional condition |
|---|---|---|
| Train | 2026-10-01 to before 2026-11-12 | Label availability strictly before the validation boundary |
| Validation | 2026-11-12 to before 2026-11-21 | Label availability strictly before the test boundary |
| Test | 2026-11-21 to before 2026-11-30 | Eligible label and fully observed horizon |

Snapshots occur every five minutes. Eight allowlisted inputs use arrived readings in `(t−10 minutes, t]` and recorded failure history from the past 24 hours. Simulator truth determines the cohort/label, but it is not a model feature or a serving lookup. A positive label means failure onset in `(t, t+1h]`; a maintenance intervention or insufficient observation censors the label. Label availability is horizon end plus 30 seconds.

The baseline is Spark MLlib logistic regression with `maxIter=40` and `regParam=0.1`. StandardScaler and class weights are fitted only on training data. Validation selects a threshold from 0.1 to 0.9 by F1, then precision, then the higher threshold on a tie. Training prevalence supplies a constant comparator. Test data does not select features, cut times, seeds or hyperparameters.

Validation and test each require at least 20 positives, 20 negatives and five independent failure episodes. Multiple snapshots preceding one failure are not independent episodes. The 60-day cohort passed these gates; the earlier 48-hour holdout with zero positives remains an explicitly limited historical result. Any changed experimental policy needs a new protocol version and an appropriate independent evaluation.

## Implementation and evidence

- [Source: ml/protocols/medium-60d-v1.json](../ml/protocols/medium-60d-v1.json)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](demo-walkthrough.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/ml-evaluation-protocol.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
