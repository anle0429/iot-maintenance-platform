"""Adapt the shared DataHub publisher to the actual separately deployed CI DAG identities."""
from pipelines import metadata_publish


def main():
    """Use the real CI Airflow IDs in this isolated publication process, retaining the shared validation logic."""
    metadata_publish.DAGS = {'dp1': 'iot_ci_dp1_bronze', 'dp2': 'iot_ci_dp2_warehouse', 'dp3': 'iot_ci_dp3_features'}
    return metadata_publish.main()


if __name__ == '__main__':
    raise SystemExit(main())
