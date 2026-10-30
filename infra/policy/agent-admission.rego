# Admit the complete owned production agent release before any MCP image import or Helm upgrade.
package iot.agent_admission
import rego.v1

resource_list := input.resources if {
    is_object(input)
    is_array(input.resources)
} else := []

resource_shape(r) if {
    is_object(r)
    object.keys(r) == {"apiVersion", "kind", "metadata", "spec"}
    is_object(r.metadata)
    object.keys(r.metadata) == {"name", "namespace"}
    is_string(r.kind)
    is_string(r.metadata.name)
    r.metadata.namespace == data.agent_admission.namespace
    is_object(r.spec)
    identity := sprintf("%s/%s", [r.kind, r.metadata.name])
    r.apiVersion == data.agent_admission.expected_resources[identity]
}

valid_envelope if {
    is_object(input)
    object.keys(input) == {"format", "namespace", "resources"}
    input.format == "iot-agent-admission-v1"
    input.namespace == data.agent_admission.namespace
    is_array(input.resources)
    count(input.resources) == count(data.agent_admission.expected_resources)
    identities := {sprintf("%s/%s", [r.kind, r.metadata.name]) | some r in resource_list; resource_shape(r)}
    identities == object.keys(data.agent_admission.expected_resources)
}

valid_model(spec) if {
    object.keys(spec) == {"apiKeySecret", "apiKeySecretKey", "provider", "model", "openAI"}
    spec.provider == "OpenAI"
    spec.model == "iot-qwen2.5-7b"
    spec.apiKeySecret == "iot-local-model-client"
    spec.apiKeySecretKey == "API_KEY"
    is_object(spec.openAI)
    object.keys(spec.openAI) == {"apiFormat", "baseUrl", "maxTokens", "seed", "temperature", "timeout"}
    spec.openAI.apiFormat == "chatCompletions"
    spec.openAI.baseUrl == data.agent_admission.model_base_url
    spec.openAI.temperature == "0"
    spec.openAI.seed == 7
    is_number(spec.openAI.maxTokens)
    floor(spec.openAI.maxTokens) == spec.openAI.maxTokens
    spec.openAI.maxTokens >= 1
    spec.openAI.maxTokens <= data.agent_admission.max_output_tokens
    is_number(spec.openAI.timeout)
    floor(spec.openAI.timeout) == spec.openAI.timeout
    spec.openAI.timeout >= 1
    spec.openAI.timeout <= data.agent_admission.max_model_timeout_seconds
}

valid_agent(r) if {
    spec := r.spec
    role := trim_prefix(r.metadata.name, "iot-")
    approved := data.agent_admission.roles[r.metadata.name]
    object.keys(spec) == {"type", "version", "description", "allowedNamespaces", "sandbox", "substrate", "declarative"}
    spec.type == "Declarative"
    spec.version == data.version
    is_string(spec.description)
    count(spec.description) >= 1
    count(spec.description) <= 500
    spec.allowedNamespaces == {"from": "Same"}
    spec.substrate == {"workerPoolRef": {"name": approved.pool}}
    is_object(spec.sandbox)
    object.keys(spec.sandbox) == {"network"}
    is_object(spec.sandbox.network)
    object.keys(spec.sandbox.network) == {"allowedDomains"}
    is_array(spec.sandbox.network.allowedDomains)
    count(spec.sandbox.network.allowedDomains) == count(approved.domains)
    {d | some d in spec.sandbox.network.allowedDomains} == {d | some d in approved.domains}
    is_object(spec.declarative)
    object.keys(spec.declarative) == {"runtime", "modelConfig", "executeCodeBlocks", "stream", "systemMessage", "tools"}
    spec.declarative.runtime == "go"
    spec.declarative.modelConfig == "default-model-config"
    spec.declarative.executeCodeBlocks == false
    spec.declarative.stream == true
    spec.declarative.systemMessage == data.prompts[role]
    spec.declarative.tools == approved.tools
}

valid_pool(r) if {
    spec := r.spec
    object.keys(object.remove(spec, {"selector"})) == {"ateomImage", "replicas", "sandboxClass", "sandboxConfigName", "template"}
    object.get(spec, "selector", sprintf("ate.dev/worker-pool=%s", [r.metadata.name])) == sprintf("ate.dev/worker-pool=%s", [r.metadata.name])
    spec.ateomImage == data.agent_admission.worker_image
    spec.replicas == 2
    spec.sandboxClass == "gvisor"
    spec.sandboxConfigName == "gvisor-default"
    spec.template == {"resources": data.agent_admission.pool_resources}
}

valid_resource(r) if {
    resource_shape(r)
    r.kind == "ModelConfig"
    valid_model(r.spec)
}

valid_resource(r) if {
    resource_shape(r)
    r.kind == "SandboxAgent"
    valid_agent(r)
}

valid_resource(r) if {
    resource_shape(r)
    r.kind == "RemoteMCPServer"
    r.spec == data.agent_admission.server_specs[r.metadata.name]
}

valid_resource(r) if {
    resource_shape(r)
    r.kind == "WorkerPool"
    valid_pool(r)
}

valid_resource(r) if {
    resource_shape(r)
    r.kind in {"NetworkPolicy", "ScaledObject"}
    identity := sprintf("%s/%s", [r.kind, r.metadata.name])
    r.spec == data.agent_admission.supplemental_specs[identity]
}

label(r) := sprintf("%s/%s", [r.kind, r.metadata.name]) if {
    is_string(r.kind)
    is_string(r.metadata.name)
} else := "malformed resource"

deny contains "incomplete, duplicate or unrecognized production release inventory" if {
    not valid_envelope
}

deny contains sprintf("unapproved capability or missing required field: %s", [label(r)]) if {
    some r in resource_list
    not valid_resource(r)
}

default allowed := false
allowed if count(deny) == 0
result := {"allowed": allowed, "violations": sort(deny)}
