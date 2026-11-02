# Agent contracts and acceptance

The deployed system has retrieval and drift specialists plus a coordinator. Kubernetes/WorkerPool declarations are under [infra/agents](../infra/agents/); this package holds shared policy, release contracts and independent checks of the original answers.

| File | Responsibility |
|---|---|
| [response-policy.json](response-policy.json) | Versioned prompt/response policy for the three roles |
| [ci_contract.py](ci_contract.py) | Sealed source/release identity and capability boundaries |
| [release_ci.py](release_ci.py) | Native release checks, feature lease and actual agent readiness |
| [answer_acceptance.py](answer_acceptance.py) | Match specialist answers to their own MCP results and authored references |
| [delegation_acceptance.py](delegation_acceptance.py) | Match the coordinator to two actual child sessions and preserve their returned values |
| [ab_configuration.py](ab_configuration.py) | Narrow paired configuration planning and outcome summaries |
| [probe.py](probe.py) | Fixed role questions and native request/probe support |

[Runtime proof](../docs/edai-2/gpu_agent_routing.md) · [Post-restart validation](../docs/edai-2/agent_runtime_validation.md) · [Scaling](../docs/edai-2/agent_scaling.md) · [CI/CD](../docs/edai-2/ci_agents.md)
