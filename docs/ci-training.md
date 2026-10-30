# Training CI and native Kubeflow execution

The recorded Jenkins training build 2 succeeded after 22 tests and image/release checks. It built two images from the bound source inputs, deployed the ML release and submitted a fresh five-task Kubeflow run with real work on two Spark executor Pods.

The run freshly retrieved 3,132,184 eligible Feast rows under the fixed protocol. Data versioning reused 180 unchanged files, approximately 740 MB, instead of uploading the full dataset again. The resulting model 6 was registered as a candidate in this training step; its later serving selection is a separate release decision.

Original build 1 was stopped by a guard and remains in the history. The successful run retains stage-level evidence for test, build, deploy, submission, data/model versions and backend task completion. Two executors share the same physical VM; their existence is not a multi-machine benchmark.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```bash
python scripts/package-training-release.py \
  --commit <source-commit> --output outputs/training-release.tar
```

```bash
python -S scripts/verify-ci-training.py
```

## Implementation and evidence

- [Source: infra/ci/Training.Jenkinsfile](../infra/ci/Training.Jenkinsfile)
- [Source: scripts/ci-training.py](../scripts/ci-training.py)
- [Source: infra/ci/TrainingController.Dockerfile](../infra/ci/TrainingController.Dockerfile)
- [Source: infra/ci/requirements-training-controller.lock](../infra/ci/requirements-training-controller.lock)
- [Source: scripts/package-training-release.py](../scripts/package-training-release.py)
- [Recorded artifact: ci-training/source-manifest.json](evidence/ci-training/source-manifest.json)
- [Recorded artifact: ci-training/ci/jenkins-build.json](evidence/ci-training/ci/jenkins-build.json)
- [Recorded artifact: ci-training/ci/jenkins-build2-console.txt](evidence/ci-training/ci/jenkins-build2-console.txt)
- [Recorded artifact: ci-training/ci/build.json](evidence/ci-training/ci/build.json)
- [Recorded artifact: ci-training/runtime-source-readback.json](evidence/ci-training/runtime-source-readback.json)
- [Recorded artifact: ci-training/ci/deployment.json](evidence/ci-training/ci/deployment.json)
- [Recorded artifact: ci-training/ci/kfp-run.json](evidence/ci-training/ci/kfp-run.json)
- [Recorded artifact: ci-training/pipeline.yaml](evidence/ci-training/pipeline.yaml)
- [Recorded artifact: ci-training/ci/verified-main-containers.json](evidence/ci-training/ci/verified-main-containers.json)
- [Recorded artifact: ci-training/two-executors-running.json](evidence/ci-training/two-executors-running.json)
- [Recorded artifact: ci-training/ci/distributed-verification.json](evidence/ci-training/ci/distributed-verification.json)
- [Recorded artifact: ci-training/event-summary.json](evidence/ci-training/event-summary.json)
- [Recorded artifact: ci-training/ci/lineage/canonical.json](evidence/ci-training/ci/lineage/canonical.json)
- [Recorded artifact: ci-training/ci/lineage/automatic-data.json](evidence/ci-training/ci/lineage/automatic-data.json)
- [Recorded artifact: ci-training/ci/lineage/remote-version.json](evidence/ci-training/ci/lineage/remote-version.json)
- [Recorded artifact: ci-training/registry/data-run.json](evidence/ci-training/registry/data-run.json)
- [Recorded artifact: ci-training/ci/lineage/registration.json](evidence/ci-training/ci/lineage/registration.json)
- [Recorded artifact: ci-training/registry/model-version.json](evidence/ci-training/registry/model-version.json)
- [Recorded artifact: ci-training/registry/model-run.json](evidence/ci-training/registry/model-run.json)
- [Recorded artifact: ci-training/ci/lineage/evaluation.json](evidence/ci-training/ci/lineage/evaluation.json)
- [Recorded artifact: ci-training/vm-session.json](evidence/ci-training/vm-session.json)
- [Recorded artifact: ci-training/resources-after.json](evidence/ci-training/resources-after.json)
- [Recorded artifact: ci-training/ci/jenkins-build1.json](evidence/ci-training/ci/jenkins-build1.json)
- [Recorded artifact: ci-training/failed-image-source-diff.json](evidence/ci-training/failed-image-source-diff.json)
- [Recorded artifact: ci-training/ci/jenkins-build1-console.txt](evidence/ci-training/ci/jenkins-build1-console.txt)
- [Recorded artifact: serving-ci-preparation/current/contract-tests-v4.txt](evidence/serving-ci-preparation/current/contract-tests-v4.txt)
- [Recorded artifact: serving-ci-preparation/current/training-source-v4.manifest.json](evidence/serving-ci-preparation/current/training-source-v4.manifest.json)
- [Recorded artifact: ci-training-preparation/final-source/source-manifest.json](evidence/ci-training-preparation/final-source/source-manifest.json)
- [Recorded artifact: ci-training-preparation/final-source/unit-tests.txt](evidence/ci-training-preparation/final-source/unit-tests.txt)
- [Recorded artifact: ci-training-preparation/final-source/verification.json](evidence/ci-training-preparation/final-source/verification.json)
- [Recorded artifact: ci-training-preparation/sdk-preparation/pip-check.txt](evidence/ci-training-preparation/sdk-preparation/pip-check.txt)
- [Recorded artifact: ci-training-preparation/final-source/pipeline.yaml](evidence/ci-training-preparation/final-source/pipeline.yaml)
- [Recorded artifact: ci-training-preparation/private-control/verification.json](evidence/ci-training-preparation/private-control/verification.json)
- [Recorded artifact: ci-training-preparation/private-control/expected-missing-config.txt](evidence/ci-training-preparation/private-control/expected-missing-config.txt)
- [github.com / crictl.md](https://github.com/kubernetes-sigs/cri-tools/blob/master/docs/crictl.md)
- [Recorded artifact: ci-training-preparation/final-source/export-function-binding.json](evidence/ci-training-preparation/final-source/export-function-binding.json)
- [Recorded artifact: ci-training-preparation/final-source/job-source-binding.json](evidence/ci-training-preparation/final-source/job-source-binding.json)
- [Recorded artifact: ci-training-preparation/final-source/current-session-rejected.json](evidence/ci-training-preparation/final-source/current-session-rejected.json)
- [Recorded artifact: ci-training-preparation/capture/manifest.json](evidence/ci-training-preparation/capture/manifest.json)
- [Recorded artifact: ci-training-preparation/capture/evidence-map.json](evidence/ci-training-preparation/capture/evidence-map.json)
- [www.kubeflow.org / container-components](https://www.kubeflow.org/docs/components/pipelines/user-guides/components/container-components/)

## Recorded interface views

These are original captures from the runs described above. Their timestamps, model identities and visible states belong to those runs.

![Recorded jenkins training2 success](evidence/ci-training/screenshots/jenkins-training2-success.jpg)

![Recorded jenkins training2 steps](evidence/ci-training/screenshots/jenkins-training2-steps.jpg)

![Recorded jenkins training2 deploy verify](evidence/ci-training/screenshots/jenkins-training2-deploy-verify.jpg)

![Recorded kfp training2 success](evidence/ci-training/screenshots/kfp-training2-success.jpg)

![Recorded kfp training2 detail](evidence/ci-training/screenshots/kfp-training2-detail.jpg)


## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](operations.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/ci-training.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
