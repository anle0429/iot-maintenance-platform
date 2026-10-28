# Read only the Airflow identity used by Jenkins; no gateway or metadata listing.
path "iot/data/ci/airflow" { capabilities = ["read"] }
path "auth/token/revoke-self" { capabilities = ["update"] }
