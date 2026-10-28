ui = true
disable_mlock = true
api_addr = "https://10.52.0.1:18200"
cluster_addr = "https://10.52.0.1:18201"
storage "raft" {
  path = "/srv/iot-data/workspace/iot-maintenance-platform/data/runtime/vault-state-28/raft"
  node_id = "iot-vault-single"
}
listener "tcp" {
  address = "10.52.0.1:18200"
  cluster_address = "10.52.0.1:18201"
  tls_cert_file = "/srv/iot-data/workspace/iot-maintenance-platform/data/runtime/vault-server-28/server.crt"
  tls_key_file = "/srv/iot-data/workspace/iot-maintenance-platform/data/runtime/vault-server-28/server.key"
  tls_min_version = "tls12"
}
