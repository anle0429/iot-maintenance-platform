# Orchestrate isolated Spark containers and publish external validation to DataHub.
FROM apache/airflow:3.1.7-python3.12
RUN pip install --no-cache-dir apache-airflow==3.1.7 docker==7.1.0 \
    --constraint https://raw.githubusercontent.com/apache/airflow/constraints-3.1.7/constraints-3.12.txt
