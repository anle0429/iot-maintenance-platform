# Read only gateway Basic credentials; no CI credentials or secret modification.
path "iot/data/gateway/basic" { capabilities = ["read"] }
path "auth/token/revoke-self" { capabilities = ["update"] }
