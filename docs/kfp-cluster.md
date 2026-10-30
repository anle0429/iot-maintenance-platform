# Initial Kubeflow cluster training milestone

This historical milestone ran four connected training tasks on the Kubeflow backend using a fresh native Feast cohort of 3,132,184 rows. Artifacts persisted beyond task lifetimes and linked to the original model/data registry versions.

The recorded evaluation under the fixed protocol yielded PR-AUC 0.6151 and F1 approximately 0.5510. These are synthetic-data holdout measurements, not field-performance guarantees. Earlier modelversion 2 and the original serving model 1 are not the current serving pin.

The later notebook-parity/distributed pipeline has five ML steps and executes fit tasks on two Kubernetes executor Pods. This initial four-step run remains useful orchestration history; it must not be relabeled as proof of the later distributed implementation.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```bash
sudo k3s kubectl get pods,pvc -n kubeflow
python -m ml.kfp_cluster --output /tmp/training-pipeline.yaml
python -m unittest tests.test_cluster_artifacts -v
python scripts/run-kfp-cluster.py --run training-reproduction-new --prepared outputs/kfp-training-prepare-27-v2 --deadline-utc YOUR_ACTUAL_VM_STOP_UTC
```

## Implementation and evidence

- [Source: ml/kfp_cluster.py](../ml/kfp_cluster.py)
- [Source: ml/training_job.py](../ml/training_job.py)
- [Recorded artifact: kfp-cluster/backend/verification.json](evidence/kfp-cluster/backend/verification.json)
- [Recorded artifact: kfp-cluster/controller/verification.json](evidence/kfp-cluster/controller/verification.json)
- [Recorded artifact: kfp-cluster/capture/task-artifacts/evaluation.json](evidence/kfp-cluster/capture/task-artifacts/evaluation.json)
- [Recorded artifact: kfp-cluster/capture/artifact-verification.json](evidence/kfp-cluster/capture/artifact-verification.json)
- [Recorded artifact: kfp-cluster/capture/manifest.json](evidence/kfp-cluster/capture/manifest.json)
- [Recorded artifact: kfp-cluster/capture/evidence-map.json](evidence/kfp-cluster/capture/evidence-map.json)
- [Recorded artifact: kfp-cluster/initial/diagnosis/evaluation-main.txt](evidence/kfp-cluster/initial/diagnosis/evaluation-main.txt)
- [Recorded artifact: kfp-cluster/initial/diagnosis/workflow.json](evidence/kfp-cluster/initial/diagnosis/workflow.json)
- [Source: ml/cluster_training_job.py](../ml/cluster_training_job.py)
- [Source: tests/test_cluster_artifacts.py](../tests/test_cluster_artifacts.py)
- [Recorded artifact: kfp-cluster/initial/diagnosis/source-before-repair/kfp_cluster.py.txt](evidence/kfp-cluster/initial/diagnosis/source-before-repair/kfp_cluster.py.txt)
- [Recorded artifact: kfp-cluster/preparation/verification.json](evidence/kfp-cluster/preparation/verification.json)
- [Recorded artifact: kfp-cluster/registry/registration.json](evidence/kfp-cluster/registry/registration.json)
- [Recorded artifact: kfp-cluster/registry/verification.json](evidence/kfp-cluster/registry/verification.json)
- [kfp-kubernetes.readthedocs.io / kubernetes.html](https://kfp-kubernetes.readthedocs.io/en/kfp-kubernetes-2.17/source/kubernetes.html)
- [www.kubeflow.org / installation](https://www.kubeflow.org/docs/components/pipelines/operator-guides/installation/)
- [Source: infra/kfp/training-volumes.yaml](../infra/kfp/training-volumes.yaml)

## Recorded interface views

These are original captures from the runs described above. Their timestamps, model identities and visible states belong to those runs.

![Recorded four training tasks succeeded](evidence/kfp-cluster/screenshots/four-training-tasks-succeeded.jpg)

![Recorded training run succeeded detail](evidence/kfp-cluster/screenshots/training-run-succeeded-detail.jpg)

![Recorded first evaluation path failed](evidence/kfp-cluster/screenshots/first-evaluation-path-failed.jpg)


## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/a395c4ea47b8f8e235556ab93de3d699ed9df18b/docs/kfp-cluster.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
