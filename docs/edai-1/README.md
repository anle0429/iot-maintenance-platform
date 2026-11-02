# Data and ML platform

[Documentation](../README.md) · [Other part](../edai-2/README.md)

Follow simulated telemetry from its source through batch/stream processing, feature history, model training and prediction. The large ingest, ML cohort and retained stream are separate measured workloads.

[System, flow and deployment diagrams](../diagrams/README.md#three-layers) · [How to read and zoom the figures](../diagrams/README.md#reading-size).

## Reading guide

| Step | Page |
|---|---|
| Source and faults | [Data generator](data_generator.md) |
| Baseline and corrections | [Data processing: inputs, corrections and evidence](processing_jobs.md) |
| Paired measurements | [Schema evolution and deduplication benchmark](batch_corrections_benchmark.md) |
| DAGs and metadata | [Airflow DP1–DP3, validation and DataHub lineage](orchestration.md) |
| Table grains and SCD2 | [Table design and historical relationships](schema_design.md) |
| Offline and online features | [Feast offline and online feature stores](feast_feature_store.md) |
| Training and data loading | [Training notebook and pipeline parity](training_notebook.md) |
| Serving and provenance | [Feature API: eligibility, inference and provenance](feature_api.md) |
| Container optimization | [Container image optimization](docker.md) |

## Reference pages

Open a group for its full component and experiment index. Commands run from the repository root; linked receipts describe their recorded workloads.

<details>
<summary>Monitoring, drift and security · 17 pages</summary>

- [A/B monitoring with Prometheus and Grafana](ab_monitoring.md)
- [API metrics and distributed traces](api_observability.md)
- [CI for API metrics and tracing](ci_api_observability.md)
- [OPA deployment-policy gate](deployment_policy.md)
- [Sensor drift generation and real-time drift API](drift_detection.md)
- [Knative Eventing to KServe drift delivery](drift_eventing.md)
- [Airflow-to-Kubeflow retraining admission](drift_retraining.md)
- [Host metrics on Windows and Linux](host_monitoring.md)
- [Native MMD audit on a bound feature cohort](joint_cohort.md)
- [MMD for joint sensor-distribution changes](joint_drift.md)
- [Dynamic Pod log discovery and collector restart](log_discovery.md)
- [Native Loki/Alloy log collection](log_monitoring.md)
- [Load test of the observable API wrapper](observed_api_load.md)
- [Scheduled offline drift monitoring](offline_drift_monitoring.md)
- [Private gateway authentication, rate limiting and HTTPS](private_gateway.md)
- [Service identity and authorization with a private mesh](private_mesh.md)
- [Vault secrets, scoped consumers and restart recovery](vault.md)

</details>

<details>
<summary>Serving, releases and API checks · 12 pages</summary>

- [A/B assignment and replay semantics](ab_testing.md)
- [Fresh feature materialization for the model 6 demo](active_feature_refresh.md)
- [Native autoscaling of feature and drift APIs](api_autoscaling.md)
- [Helm rolling updates and automatic fallback](api_deployment.md)
- [Boundary, partition and property-based API validation](api_validation.md)
- [Native A/B serving deployment](ci_ab_deployment.md)
- [Serving CI: tests, images, A/B and rollback](ci_serving.md)
- [Feature API: eligibility, inference and provenance](feature_api.md)
- [KServe deployment and native inference checks](kubernetes_serving.md)
- [Portable model serving and lineage](model_serving.md)
- [Serving release bundle and model pin](serving_release.md)
- [Version consistency across feature and prediction APIs](versioned_serving.md)

</details>

<details>
<summary>Architecture, infrastructure and operations · 7 pages</summary>

- [Architecture](architecture.md)
- [DataHub restoration after a VM restart](datahub_restore.md)
- [Container image optimization](docker.md)
- [Measured Google Cloud runtime](gcp_runtime.md)
- [Google Cloud VM design and cost controls](gcp_vm.md)
- [Terraform and Ansible verification](iac_verification.md)
- [Run and operate the platform](operations.md)

</details>

<details>
<summary>Training, evaluation and versioning · 9 pages</summary>

- [Automatic incremental data and model versioning](automatic_ml_versioning.md)
- [Training CI and native Kubeflow execution](ci_training.md)
- [Distributed training on Kubeflow Pipelines](distributed_training.md)
- [Incremental dataset versions and full restore](incremental_data_versioning.md)
- [Initial Kubeflow cluster training milestone](kfp_cluster.md)
- [Five key classes in the ML lifecycle](ml_class_design.md)
- [ML evaluation protocol: medium-60d-v1](ml_evaluation_protocol.md)
- [Initial training pipeline and fixed-protocol evaluation](ml_training.md)
- [Training notebook and pipeline parity](training_notebook.md)

</details>

<details>
<summary>Processing and orchestration · 10 pages</summary>

- [Schema evolution and deduplication benchmark](batch_corrections_benchmark.md)
- [Catalog-bound Spark ingestion and exact Parquet readback](chunk_ingest.md)
- [CI/CD for DP1–DP3](ci_data_pipelines.md)
- [Verified full-scale Bronze/Silver ingestion](full_scale_ingest.md)
- [Guarded full-source Spark runner](large_ingest_runner.md)
- [Airflow DP1–DP3, validation and DataHub lineage](orchestration.md)
- [Data processing: inputs, corrections and evidence](processing_jobs.md)
- [Table design and historical relationships](schema_design.md)
- [Spark join-skew benchmark](skew_benchmark.md)
- [Kafka/Flink event-time processing and replay](streaming.md)

</details>

<details>
<summary>Feature engineering and stores · 9 pages</summary>

- [Bounded temporal joins for feature engineering](bounded_features.md)
- [CI/CD for incremental Feast materialization](ci_materialization.md)
- [CI/CD for offline and online stream-feature jobs](ci_stream_push.md)
- [Feast offline and online feature stores](feast_feature_store.md)
- [Airflow incremental materialization and DataHub publication](feast_materialization.md)
- [Feature write paths and TTL policy](feature_jobs_and_ttl.md)
- [The 60-day feature and label dataset](medium_feature_dataset.md)
- [Pushing finalized Flink windows through Feast](stream_feast_push.md)
- [Full retained-window cohort across offline and online stores](window_cohort.md)

</details>

<details>
<summary>Source generation and capacity · 5 pages</summary>

- [Chunked generation with continuous physical state](chunked_generator.md)
- [Source data contract](data_contract.md)
- [Data generator](data_generator.md)
- [Generator throughput and memory measurements](generator_throughput.md)
- [Large-source preparation and resource budgeting](scale_100gb.md)

</details>

<details>
<summary>Review and evidence references · 11 pages</summary>

- [Data-platform review checklist](data_review_audit.md)
- [Recorded evidence](evidence_guide.md)
- [Final rubric audit](final_audit.md)
- [Recorded ML handover and demonstration evidence](final_handover.md)
- [Source history migration](history_rewrite.md)
- [Rubric implementation and evidence map](implementation_map.md)
- [Novel ideas and coursework scope](novel_ideas.md)
- [Repository quality and documentation checks](repository_quality.md)
- [Coursework rubric index](rubric_overview.md)
- [Rubric sources and English presentation](rubric_source.md)
- [Final ML rubric tracker](rubric_tracker.md)

</details>

## Evidence boundaries

[The requirement tracker](rubric_tracker.md) links the original requirements to scoped native results. Screenshots, failed attempts and measurements keep their recorded identities. Offline artifact checks do not establish current service readiness, mentor acceptance or a grade.
