#!/usr/bin/env python3
"""Budget upstream Substrate workloads and replace development credentials with private Secret references."""
import sys
import yaml


def render(documents):
    """Preserve upstream objects and privileges while adding bounded resources and private object-store access."""
    budgets = {
        'ate-api-server': ('100m', '256Mi', '1', '1Gi'),
        'ate-controller': ('100m', '128Mi', '500m', '512Mi'),
        'atelet': ('100m', '256Mi', '1', '2Gi'),
        'atenet-router': ('100m', '128Mi', '1', '512Mi'),
        'atenet-dns': ('30m', '64Mi', '250m', '128Mi'),
        'rustfs': ('100m', '256Mi', '1', '2Gi'),
        'valkey': ('20m', '64Mi', '250m', '256Mi'),
        'ate-client': ('20m', '64Mi', '250m', '256Mi'),
    }
    keys = {'AWS_ACCESS_KEY_ID': 'access', 'RUSTFS_ACCESS_KEY': 'access',
            'AWS_SECRET_ACCESS_KEY': 'secret', 'RUSTFS_SECRET_KEY': 'secret'}
    for body in documents:
        if not body:
            continue
        spec = body.get('spec', {})
        if body.get('kind') in {'Deployment', 'DaemonSet', 'StatefulSet', 'Job'}:
            pod = spec['template']['spec']
        elif body.get('kind') == 'Pod':
            pod = spec
        else:
            yield body
            continue
        for group in ['containers', 'initContainers']:
            for container in pod.get(group, []):
                name = container['name']
                budget = budgets.get(name, ('50m', '64Mi', '500m', '512Mi'))
                container['resources'] = {'requests': dict(zip(['cpu', 'memory'], budget[:2])),
                                          'limits': dict(zip(['cpu', 'memory'], budget[2:]))}
                for env in container.get('env', []):
                    if env['name'] in keys:
                        env.pop('value', None)
                        env['valueFrom'] = {'secretKeyRef': {'name': 'iot-substrate-object-store', 'key': keys[env['name']]}}
                if name == 'atelet':
                    for port in container.get('ports', []):
                        if port.get('name') == 'prometheus':
                            port.pop('hostPort', None)
        yield body


if __name__ == '__main__':
    yaml.safe_dump_all(render(yaml.safe_load_all(sys.stdin)), sys.stdout, sort_keys=False)
