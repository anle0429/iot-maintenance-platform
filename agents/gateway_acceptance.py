"""Bind actual delegated A2A transport logs to independently selected native child histories."""
from copy import deepcopy
from ipaddress import ip_address
import re


ROLES = ('retrieval', 'drift')
UUID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
NON_A2A_ROUTES = {'model', 'retrieval', 'drift', 'proxy-mcp-retrieval', 'proxy-mcp-drift'}


def context_uuid(value):
    """Accept the canonical lowercase UUID shape retained by the bounded native log fields."""
    return isinstance(value, str) and UUID_PATTERN.fullmatch(value) is not None


def coordinator_worker(address, workers):
    """Join a native socket address to an inventoried coordinator Pod, without heuristic labels."""
    if not isinstance(address, str):
        raise ValueError('Missing native coordinator source socket address')
    try:
        if address.startswith('['):
            match = re.fullmatch(r'\[([^\]]+)\]:([0-9]+)', address)
            if match is None:
                raise ValueError('Malformed bracketed source socket')
            host, port = match.groups()
        else:
            host, port = address.rsplit(':', 1)
        if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
            raise ValueError('Malformed source socket port')
        host = str(ip_address(host))
    except (ValueError, TypeError):
        raise ValueError('Require an actual Pod IP and numeric source port') from None
    worker = workers.get(host)
    if (not isinstance(worker, dict) or worker.get('role') != 'coordinator'
            or not isinstance(worker.get('pod'), str) or not worker['pod']
            or not isinstance(worker.get('uid'), str) or not worker['uid']):
        raise ValueError('The actual source IP is not an identified coordinator worker Pod')
    return {'ip': host, 'role': worker['role'], 'pod': worker['pod'], 'uid': worker['uid']}


def verify_delegation_paths(records, parent_context_id, child_contexts, workers):
    """Require one exact successful gateway-mediated child task per role for this native parent."""
    if (not context_uuid(parent_context_id) or not isinstance(child_contexts, dict)
            or set(child_contexts) != set(ROLES)
            or any(not context_uuid(value) for value in child_contexts.values())
            or len(set(child_contexts.values())) != 2 or parent_context_id in child_contexts.values()):
        raise ValueError('Require one parent UUID and two distinct independently selected child UUIDs')
    if not isinstance(workers, dict) or not workers:
        raise ValueError('Require the actual worker Pod IP inventory')
    if not isinstance(records, (list, tuple)):
        raise ValueError('Require parsed native gateway JSON log records')
    found = {}
    for row in records:
        if not isinstance(row, dict):
            raise ValueError('A native gateway log record is not an object')
        route = row.get('route', '')
        suffix = route.rsplit('/', 1)[-1] if isinstance(route, str) else ''
        # Model/MCP calls have separate acceptance. The original parent ingress
        # also has a separate coordinator-content and transport check.
        if suffix in NON_A2A_ROUTES and row.get('a2a.method') is None:
            continue
        if (suffix in {'a2a-coordinator', 'a2a-coordinator-slash'}
                and row.get('a2a.context.id') == parent_context_id
                and row.get('a2a_parent_context_id') != parent_context_id
                and row.get('a2a_root_context_id') != parent_context_id):
            continue
        attributed = (row.get('a2a_parent_context_id') == parent_context_id
                      or row.get('a2a_root_context_id') == parent_context_id
                      or row.get('a2a.context.id') in child_contexts.values())
        if not attributed:
            continue
        # Agent-card discovery can share lineage headers, but it is not a
        # message/send task and cannot count as a completed delegation.
        if row.get('http.method') != 'POST' and row.get('a2a.method') != 'message/send':
            continue
        role = next((role for role in ROLES if suffix in {'delegate-' + role, 'delegate-' + role + '-slash'}), None)
        if role is None:
            raise ValueError('An attributed child A2A call used an unapproved delegation route')
        path = '/api/a2a-sandboxes/iot-agents/iot-' + role + ('/' if suffix.endswith('-slash') else '')
        if (row.get('http.method') != 'POST' or row.get('http.path') != path
                or type(row.get('http.status')) is not int or row['http.status'] != 200
                or row.get('a2a.method') != 'message/send' or row.get('a2a.response.outcome') != 'success'
                or row.get('a2a.result.kind') != 'task' or row.get('a2a.task.state') != 'completed'
                or row.get('a2a.context.id') != child_contexts[role]
                or row.get('a2a_parent_context_id') != parent_context_id
                or row.get('a2a_root_context_id') != parent_context_id or row.get('a2a_source') != 'agent'):
            raise ValueError('The native child route does not prove one completed parent-bound delegation')
        worker = coordinator_worker(row.get('src.addr'), workers)
        if role in found:
            raise ValueError('Duplicate native message/send delegation for one child role')
        found[role] = {'context_id': child_contexts[role], 'route': route, 'http_path': path,
                       'worker': worker, 'gateway': deepcopy(row)}
    if set(found) != set(ROLES):
        raise ValueError('Require one completed gateway-mediated delegation to each selected child role')
    return {'passed': True, 'delegation_transport_verified': True, 'parent_context_id': parent_context_id,
            'successful_delegations': len(found), 'children': found,
            'scope': 'Transport evidence only: actual child A2A responses and coordinator worker attribution. '
                     'verify_coordinator must separately verify the native child histories and MCP contents.'}
