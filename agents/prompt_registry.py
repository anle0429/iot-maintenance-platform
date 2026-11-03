"""Bind immutable MLflow Prompt Registry versions to the reviewed response policy."""

from hashlib import sha256


ROLES = ('retrieval', 'drift', 'coordinator')


def prompt_pins(prompts, versions):
    """Describe the reviewed templates and reject incomplete or invalid version pins."""
    if set(prompts) != set(ROLES) or set(versions) != set(ROLES):
        raise ValueError('The shared release requires exactly three prompt versions')
    pins = {}
    for role in ROLES:
        version = versions[role]
        if type(version) is not int or version < 1:
            raise ValueError(f'Invalid prompt version for {role}')
        pins[role] = {'uri': f'prompts:/iot-agent-{role}/{version}', 'version': version,
                      'sha256': sha256(prompts[role].encode()).hexdigest()}
    return pins


def verify_prompts(prompts, versions, load):
    """Load all exact versions and return metadata only if the templates match Helm input."""
    pins = prompt_pins(prompts, versions)
    for role, pin in pins.items():
        prompt = load(pin['uri'])
        if prompt.name != f'iot-agent-{role}' or prompt.version != pin['version'] or prompt.template != prompts[role]:
            raise ValueError(f'Registered prompt identity or template differs for {role}')
    return pins
