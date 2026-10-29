#!/usr/bin/env python3
"""Use a bounded Recreate rollout for the single CPU model when two simultaneous reservations cannot fit this lab node."""
import sys
import yaml


def render(documents):
    """Preserve model identity and resources while avoiding an unschedulable second copy on the retained node."""
    for body in documents:
        if body and body.get('kind') == 'Deployment':
            labels = body['spec']['template']['metadata']['labels']
            if labels.get('app.kubernetes.io/name') == 'iot-llm-model':
                if body['spec']['replicas'] != 1:
                    raise ValueError('This renderer is scoped to the single-node single-model lab')
                body['spec']['strategy'] = {'type': 'Recreate'}
        yield body


if __name__ == '__main__':
    yaml.safe_dump_all(render(yaml.safe_load_all(sys.stdin)), sys.stdout, sort_keys=False)
