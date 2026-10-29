# The agent front door reads only its own login secret. Other lab credentials remain inaccessible.
path "iot/data/agents/gateway" { capabilities = ["read"] }
path "auth/token/revoke-self" { capabilities = ["update"] }
