"""Provision the private credential shared by retrieval MCP and its search backend."""
import json
import secrets
import subprocess


def ensure_search_secret(kubectl, namespace, name):
    """Create once without logging token bytes or rotating a concurrent release's key."""
    command = [*kubectl, '-n', namespace]
    shape = '{{if and (eq .type "Opaque") (ge (len (index .data "token")) 44)}}valid{{else}}invalid{{end}}'

    def exists():
        """Ask the API to return a verdict without printing the Secret."""
        result = subprocess.run([*command, 'get', 'secret', name, '--ignore-not-found',
                                 '-o', 'go-template=' + shape], capture_output=True, check=True)
        if result.stdout not in (b'', b'valid'):
            raise ValueError('Existing RAG Search credential is malformed')
        return result.stdout == b'valid'

    if exists():
        return
    secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': name, 'namespace': namespace},
              'type': 'Opaque', 'stringData': {'token': secrets.token_urlsafe(32)}}
    create = subprocess.run([*command, 'create', '-f', '-'], input=json.dumps(secret).encode(),
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if create.returncode and not exists():
        raise RuntimeError('Failed to create the private RAG Search credential')
