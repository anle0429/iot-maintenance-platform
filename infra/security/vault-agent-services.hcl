# The deployment consumer reads existing MCP/model credentials, without write/list or gateway/CI access.
path "iot/data/agents/services" { capabilities = ["read"] }
path "auth/token/revoke-self" { capabilities = ["update"] }
