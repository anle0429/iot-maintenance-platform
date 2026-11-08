"""Authenticate new policy receipts and child A2A transport without starting native services."""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from ipaddress import ip_address
from pathlib import Path, PurePosixPath
from math import isfinite
from datetime import datetime, timedelta, timezone
import argparse
import json
import re
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents.admission import canonical, decision_value, require_same_admitted_input
from agents.admission_cases import cases
from agents.answer_acceptance import KNOWLEDGE, unique_object
from agents.delegation_acceptance import verify_coordinator
from agents.gateway_acceptance import verify_delegation_paths
from agents.admission_ci import require_policy_phase
from agents.ci_contract import release_identity, require_receipt
from agents.prompt_registry import prompt_pins

POLICIES = ['infra/policy/agent-admission.rego', 'infra/policy/agent-admission-approved.json', 'agents/response-policy.json']
REQUIRED_SOURCE = {'agents/admission.py', 'agents/admission_cases.py', 'agents/policy_execution.py',
                   'scripts/check-agent-admission.py', 'infra/policy/opa-assets.json', *POLICIES}
CAPSULE_NAME = 'agent-delegation-20261108'
CAPSULE_MANIFEST_SHA256 = '64b9c25d4e9721f19f409b8228f67434da30e7bdba4c41b51eab5f269eead5e0'
REVIEW_INDEX_SHA256 = '525ad06fb56441532ba5849633729cba88ca126b51fb03385538edca3bc4ccf3'
REVIEW_INDEX_NAME = 'review-index.json'


def require(condition, message):
    """Keep evidence validation active even when Python assertions are disabled."""
    if not condition:
        raise ValueError(message)


def member_name(name):
    """Reject escaped, ambiguous or platform-dependent archive member references."""
    require(isinstance(name, str) and name and '\\' not in name and '\x00' not in name, 'Invalid native member name')
    path = PurePosixPath(name)
    require(not path.is_absolute() and '..' not in path.parts and path.as_posix() == name and name != '.',
            'Native member escaped its declared archive')
    return name


def prefix_name(prefix):
    """Normalize one explicitly selected relative directory without permitting traversal."""
    return member_name(prefix.rstrip('/')) + '/'


def payload(files, name):
    """Read exactly one required original byte payload rather than a replacement summary."""
    member_name(name)
    require(name in files and isinstance(files[name], bytes), 'Missing original native payload: ' + name)
    return files[name]


def native_json(files, name):
    """Decode unambiguous native JSON while preserving the original bytes separately."""
    try:
        return json.loads(payload(files, name), object_pairs_hook=unique_object)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('Invalid original native JSON: ' + name) from error


def load_native_archive(raw, *, expected_archive_sha256, expected_manifest_sha256, archive_prefix=''):
    """Authenticate a complete safe archive in memory against independent archive and inventory pins."""
    require(isinstance(raw, bytes) and sha256(raw).hexdigest() == expected_archive_sha256, 'Native archive hash changed')
    prefix = prefix_name(archive_prefix) if archive_prefix else ''
    files = {}; total = 0
    try:
        with tarfile.open(fileobj=BytesIO(raw)) as archive:
            for entry in archive:
                full = member_name(entry.name.rstrip('/') if entry.isdir() else entry.name)
                require(not prefix or full == prefix.rstrip('/') or full.startswith(prefix), 'Unexpected archive root')
                if entry.isdir():
                    continue
                require(entry.isfile() and 0 <= entry.size <= 128*1024*1024, 'Unsafe native archive member')
                name = member_name(full[len(prefix):] if prefix else full)
                require(name not in files and len(files) < 100000, 'Duplicate or excessive native archive members')
                total += entry.size
                require(total <= 512*1024*1024, 'Native archive exceeds the bounded verifier budget')
                files[name] = archive.extractfile(entry).read()
    except tarfile.TarError as error:
        raise ValueError('Unreadable native archive') from error
    manifest_raw = payload(files, 'manifest.json')
    require(sha256(manifest_raw).hexdigest() == expected_manifest_sha256, 'Native member inventory hash changed')
    manifest = native_json(files, 'manifest.json')
    require(isinstance(manifest, dict) and isinstance(manifest.get('files'), dict), 'Missing native member inventory')
    files.pop('manifest.json')
    require(set(files) == set(manifest['files']), 'Incomplete or unrecorded native payloads')
    for name, pin in manifest['files'].items():
        body = payload(files, name)
        require(isinstance(pin, dict) and type(pin.get('bytes')) is int and len(body) == pin['bytes']
                and sha256(body).hexdigest() == pin.get('sha256'), 'Native payload hash changed: ' + name)
    return files


def verify_source(files, seal, source_prefix, *, required=REQUIRED_SOURCE):
    """Bind every as-run source byte and the current critical interpreter to the captured source seal."""
    prefix = prefix_name(source_prefix)
    require(isinstance(seal, dict) and seal.get('format') == 'iot-agent-admission-source-v1'
            and isinstance(seal.get('files'), dict) and required <= set(seal['files']), 'Incomplete policy source seal')
    require(isinstance(seal.get('commit'), str) and re.fullmatch('[0-9a-f]{40}', seal['commit']), 'Missing source commit label')
    for name, digest in seal['files'].items():
        raw = payload(files, prefix + member_name(name))
        require(sha256(raw).hexdigest() == digest, 'As-run source hash changed: ' + name)
        if name in required:
            require((ROOT/name).read_bytes().replace(b'\r\n', b'\n') == raw.replace(b'\r\n', b'\n'),
                    'Unreviewed current policy interpreter or source: ' + name)
    assets = native_json(files, prefix + 'infra/policy/opa-assets.json')
    require(isinstance(assets.get('sha256'), str) and re.fullmatch('[0-9a-f]{64}', assets['sha256'])
            and isinstance(assets.get('version'), str) and assets['version']
            and type(assets.get('bytes')) is int and assets['bytes'] > 0, 'Invalid captured OPA asset lock')
    return assets


def verify_source_packets(files):
    """Bind both complete as-run packets to immutable Git/archive bytes and the final gateway contract."""
    import subprocess
    from scripts.evidence_bundle import INDEX_SHA256, checked_index
    from scripts.git_sources import git_blobs, git_environment, resolve_commit
    from infra.agentgateway import candidate

    inventory_raw = (ROOT/'docs/evidence-archive/manifest.json').read_bytes()
    require(sha256(inventory_raw).hexdigest() == INDEX_SHA256, 'Original evidence inventory changed')
    archived = checked_index(json.loads(inventory_raw, object_pairs_hook=unique_object))
    commits = {'source': '9471bfe0b2adf3230ea88835df033062fd77a803',
               'source-ci': 'ad2da29262e3088c942a9240d7157708c094aca4'}
    packets = {}
    for packet, commit in commits.items():
        prefix = packet+'/'; tree = prefix+'tree/'
        seal = native_json(files, prefix+'manifest.json')
        require(isinstance(seal, dict) and seal.get('format') == 'iot-sealed-llm-ci-v1'
                and seal.get('commit') == commit and isinstance(seal.get('files'), dict)
                and len(seal['files']) == 402, 'Unexpected complete source packet: '+packet)
        names = set(seal['files'])
        for name, digest in seal['files'].items():
            member_name(name)
            require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest), 'Invalid source packet pin')
            require(sha256(payload(files, tree+name)).hexdigest() == digest, 'Captured source seal differs: '+name)
        require({name for name in files if name.startswith(tree)} == {tree+name for name in names},
                'Missing or unrecorded source packet member: '+packet)
        actual = resolve_commit(ROOT, commit)
        tracked_tree = set(subprocess.check_output(['git', 'ls-tree', '-r', '-z', '--name-only', actual],
            cwd=ROOT, env=git_environment(), timeout=30).decode('utf-8').rstrip('\0').split('\0'))
        tracked = names & tracked_tree; historical = names-tracked
        require(len(tracked) == 144 and len(historical) == 258 and historical <= archived.keys(),
                'Source packet Git/historical inventory differs: '+packet)
        for name, original in git_blobs(ROOT, commit, sorted(tracked)).items():
            require(original == payload(files, tree+name), 'Source packet differs from immutable Git: '+name)
        for name in historical:
            original = payload(files, tree+name); pin = archived[name]
            require(len(original) == pin['bytes'] and sha256(original).hexdigest() == pin['sha256'],
                    'Source packet differs from original evidence: '+name)
        packets[packet] = {'commit': commit, 'source_files': len(names), 'tracked_git_files': len(tracked),
                           'historical_evidence_files': len(historical)}

    configmap = native_json(files, 'final/gateway-configuration.json')
    try:
        gateway = json.loads(configmap['data']['config.yaml'], object_pairs_hook=unique_object)
        routes = {bind['port']: {route['name']: route for listener in bind['listeners']
                                for route in listener['routes']} for bind in gateway['binds']}
        hashes = {role: routes[port][role]['policies']['apiKey']['keys'][0]['keyHash']
                  for role, port in candidate.PORTS.items() if role != 'a2a'}
        hashes['a2a'] = routes[18083]['a2a-coordinator']['policies']['apiKey']['keys'][0]['keyHash']
        delegation = routes[18083]['delegate-retrieval']['policies']['apiKey']['keys'][0]['keyHash']
        values = native_json(files, 'native/kagent-values.json')
        a2a_url, proxy_url = values['controller']['a2aBaseUrl'], values['proxy']['url']
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('Incomplete captured gateway or kagent configuration') from error
    require(gateway == candidate.config(hashes, delegation_hash=delegation, tracing=True),
            'Final gateway differs from the reviewed fixed-backend configuration')
    expected_url = 'http://'+candidate.DNS_NAME+':18083'
    require(a2a_url == proxy_url == expected_url, 'Both kagent A2A/discovery flags must use the private gateway')
    return {'passed': True, 'source_packets': packets, 'listener_ports': sorted(routes),
            'gateway_configuration_matches_current_renderer': True, 'gateway_a2a_base_url': expected_url}


def absolute_native_path(value):
    """Require the original Linux invocation path without resolving it on the reviewing computer."""
    require(isinstance(value, str) and value and '\\' not in value and '\x00' not in value, 'Invalid native command path')
    path = PurePosixPath(value)
    require(path.is_absolute() and '..' not in path.parts and path.as_posix() == value, 'Unbounded native command path')
    return value


def verify_process_ledger(files, prefix, report, declared, assets):
    """Reconcile every recorded argv, exit and stream hash with the original check/version/eval payloads."""
    ledger = native_json(files, prefix + 'process-ledger.json')
    require(ledger == report.get('process_ledger'), 'Embedded and original process ledgers disagree')
    require(isinstance(ledger, dict) and ledger.get('pinned_opa_sha256') == assets['sha256'], 'Ledger OPA pin differs')
    opa = absolute_native_path(ledger.get('pinned_opa_path'))
    commands = ledger.get('invocations')
    require(isinstance(commands, list) and len(commands) == 2 + 2*len(declared)
            and all(isinstance(row, dict) for row in commands), 'Missing or extra OPA process dispatches')
    require(ledger.get('allowed_verbs') == ['check', 'eval', 'version']
            and ledger.get('invocation_count') == len(commands)
            and ledger.get('verb_counts') == dict(Counter(row.get('verb') for row in commands)), 'Incorrect derived process counts')
    require(not {'model_requests', 'network_requests', 'deployment_commands_run'} & set(ledger), 'Invented process-side-effect counters')
    check = commands[0].get('argv')
    require(isinstance(check, list) and len(check) == 4 and check[:3] == [opa, 'check', '--strict'], 'OPA strict check command differs')
    rule = absolute_native_path(check[3]); suffix = '/' + POLICIES[0]
    require(rule.endswith(suffix), 'OPA checked a different policy file')
    source_root = rule[:-len(suffix)]
    require(source_root, 'Missing original source root')
    first_eval = commands[2].get('argv')
    require(isinstance(first_eval, list) and len(first_eval) > 5, 'Missing original evaluation arguments')
    first_input = absolute_native_path(first_eval[5]); end = '/' + declared[0]['id'] + '/input.json'
    require(first_input.endswith(end), 'OPA evaluated an unexpected first input')
    output_root = first_input[:-len(end)]
    expected = [([opa, 'check', '--strict', rule], 0, 'opa-check.stdout', 'opa-check.stderr'),
                ([opa, 'version'], 0, 'opa-version.txt', 'opa-version.stderr')]
    for case in declared:
        base = [opa, 'eval', '--strict', '--strict-builtin-errors', '--input', output_root + '/' + case['id'] + '/input.json']
        for policy in POLICIES:
            base += ['--data', source_root + '/' + policy]
        current = case['id'] + '/'
        expected += [(base + ['--format=json', 'data.iot.agent_admission.result'], 0,
                      current+'decision.stdout', current+'decision.stderr'),
                     (base + ['--format=json', '--fail', 'data.iot.agent_admission.result.allowed = true'],
                      0 if case['expected_allowed'] else 1, current+'gate.stdout', current+'gate.stderr')]
    for sequence, (row, (argv, code, stdout_name, stderr_name)) in enumerate(zip(commands, expected, strict=True), 1):
        require(isinstance(row, dict) and row.get('sequence') == sequence and row.get('argv') == argv
                and row.get('verb') == argv[1] and type(row.get('exit_code')) is int and row['exit_code'] == code
                and 'error' not in row, 'OPA invocation identity or exit differs')
        require(row.get('stdout_sha256') == sha256(payload(files, prefix+stdout_name)).hexdigest()
                and row.get('stderr_sha256') == sha256(payload(files, prefix+stderr_name)).hexdigest(),
                'OPA invocation stream hash differs from original output')
    version = payload(files, prefix+'opa-version.txt').decode('utf-8')
    require(re.search(r'^Version:\s*' + re.escape(assets['version']) + r'\s*$', version, re.MULTILINE), 'Native OPA version differs from lock')
    return len(commands)


def verify_policy_suite(files, policy_prefix, *, expected_case_ids, source_prefix):
    """Recompute the declared native case inventory and authenticate every actual decision and gate exit."""
    prefix = prefix_name(policy_prefix)
    seal = native_json(files, prefix+'source.json')
    assets = verify_source(files, seal, source_prefix)
    observed = native_json(files, prefix+'observed.json')
    require(payload(files, prefix+'observed.json') == canonical(observed), 'Observed policy input is not the original canonical envelope')
    declared = cases(observed)
    identifiers = [case['id'] for case in declared]
    require(isinstance(expected_case_ids, (list, tuple)) and list(expected_case_ids) == identifiers
            and len(set(identifiers)) == len(identifiers), 'Declared policy case inventory differs from explicit expected identifiers')
    report = native_json(files, prefix+'verification.json')
    require(report.get('passed') is True and report.get('source_commit') == seal['commit']
            and report.get('resource_count') == len(observed['resources'])
            and report.get('observed_input_sha256') == sha256(canonical(observed)).hexdigest(), 'Suite baseline/source binding differs')
    require(isinstance(report.get('cases'), list) and len(report['cases']) == len(declared), 'Missing native policy case results')
    require(not {'model_requests', 'network_requests', 'deployment_commands_run'} & set(report), 'Invented suite-side-effect counters')
    policy_pins = {name: seal['files'][name] for name in POLICIES}
    denied = []; allowed = 0
    for case, row in zip(declared, report['cases'], strict=True):
        name = prefix + case['id'] + '/'
        raw = payload(files, name+'input.json')
        require(raw == canonical(case['input']), 'Native case input differs from the source-declared mutation: ' + case['id'])
        decision = decision_value(native_json(files, name+'decision.stdout'))
        require(decision['allowed'] is case['expected_allowed'], 'OPA decision disagrees with the independent expected case')
        decision_exit = native_json(files, name+'decision-exit.json').get('exit_code')
        gate_exit = native_json(files, name+'gate-exit.json').get('exit_code')
        expected_exit = 0 if case['expected_allowed'] else 1
        require(type(decision_exit) is int and decision_exit == 0 and type(gate_exit) is int and gate_exit == expected_exit,
                'Native decision/gate process exits differ')
        gate = native_json(files, name+'gate.stdout')
        if case['expected_allowed']:
            require(isinstance(gate, dict) and len(gate.get('result', [])) == 1
                    and len(gate['result'][0].get('expressions', [])) == 1
                    and gate['result'][0]['expressions'][0].get('value') is True, 'Allow gate did not return an explicit truth result')
        else:
            require(isinstance(gate, dict) and not gate.get('result'), 'Denied gate returned a defined result')
        receipt = native_json(files, name+'verification.json')
        require(isinstance(row, dict) and row.get('case') == case['id'] and row.get('expected_allowed') is case['expected_allowed']
                and {key: value for key, value in row.items() if key not in {'case', 'expected_allowed'}} == receipt,
                'Case summary differs from its original verification receipt')
        require(receipt.get('allowed') is decision['allowed'] and receipt.get('violations') == decision['violations']
                and type(receipt.get('gate_exit')) is int and receipt['gate_exit'] == gate_exit
                and receipt.get('input_sha256') == sha256(raw).hexdigest() and receipt.get('policy_files') == policy_pins
                and receipt.get('opa_sha256') == assets['sha256'] and receipt.get('opa_version') == assets['version'],
                'Original decision receipt does not bind exact input/policy/binary')
        if case['expected_allowed']:
            require_same_admitted_input(receipt, case['input']); allowed += 1
        else:
            try:
                require_same_admitted_input(receipt, case['input'])
            except ValueError:
                denied.append(case['id'])
            else:
                raise ValueError('A denied receipt authorized the deployment binding')
    require(report.get('allowed_cases') == allowed and report.get('denied_cases') == len(denied)
            and report.get('denied_receipt_bindings_rejected') == denied, 'Suite outcome/binding counts differ')
    changed = deepcopy(observed)
    agent = next(row for row in changed['resources'] if row['kind'] == 'SandboxAgent' and row['metadata']['name'] == 'iot-retrieval')
    agent['spec']['declarative']['tools'][0]['mcpServer']['toolNames'].append('start_pump')
    try:
        require_same_admitted_input(report['cases'][0], changed)
    except ValueError:
        require(report.get('post_admission_tool_edit_rejected') is True, 'Missing original post-admission edit rejection')
    else:
        raise ValueError('A changed configuration reused the original allow receipt')
    processes = verify_process_ledger(files, prefix, report, declared, assets)
    return {'passed': True, 'case_count': len(declared), 'allowed_cases': allowed, 'denied_cases': len(denied),
            'opa_command_count': processes, 'resource_count': len(observed['resources']),
            'observed_input_sha256': sha256(canonical(observed)).hexdigest(), 'source_files': len(seal['files']),
            'source_commit_label': seal['commit'], 'post_admission_tool_edit_rejected': True,
            'scope': 'Authenticated source-bound OPA inputs, decisions, exits and process streams. '
                     'Cluster/deployment/model side effects require separate native CI observations.'}


def workers_from_pods(value, *, namespace='iot-agents'):
    """Rebuild worker IP identities from actual Running Pods and exact retained pool labels."""
    require(isinstance(value, dict) and value.get('kind') in {'PodList', 'List'} and isinstance(value.get('items'), list),
            'Require the original worker PodList readback')
    workers = {}
    for pod in value['items']:
        require(isinstance(pod, dict), 'Malformed native Pod readback')
        metadata = pod.get('metadata', {}); status = pod.get('status', {})
        pool = metadata.get('labels', {}).get('ate.dev/worker-pool')
        role = next((role for role in ['retrieval', 'drift', 'coordinator'] if pool == 'iot-'+role+'-pool'), None)
        if role is None or status.get('phase') != 'Running':
            continue
        require(pod.get('kind') == 'Pod' and pod.get('apiVersion') == 'v1', 'Worker inventory item is not a native v1 Pod')
        require(metadata.get('namespace') == namespace, 'Worker Pod belongs to a different namespace')
        require(isinstance(metadata.get('name'), str) and metadata['name'] and isinstance(metadata.get('uid'), str)
                and metadata['uid'], 'Missing native worker Pod identity')
        require(isinstance(status.get('podIP'), str), 'Native worker Pod IP is not a string')
        try:
            address = str(ip_address(status.get('podIP')))
        except ValueError as error:
            raise ValueError('Missing native Running worker Pod IP') from error
        require(address not in workers, 'Duplicate native worker Pod IP')
        workers[address] = {'role': role, 'pod': metadata['name'], 'uid': metadata['uid']}
    require(workers, 'No actual Running worker Pods in readback')
    return workers


def verify_conversation(files, *, response_path, child_paths, gateway_path, workers_path, worker_pods_path,
                        knowledge_root=KNOWLEDGE, acceptance_options=None, allowed_non_json_lines=()):
    """Re-run actual native child-history acceptance before joining exact gateway and worker identities."""
    require(isinstance(child_paths, dict) and set(child_paths) == {'retrieval', 'drift'}, 'Require both original child-history paths')
    children = {}; names = [response_path, gateway_path, workers_path, worker_pods_path]
    for role, paths in child_paths.items():
        require(isinstance(paths, dict) and set(paths) == {'session', 'tasks'}, 'Require verbatim native session and tasks payloads')
        children[role] = {kind: native_json(files, path) for kind, path in paths.items()}
        names += list(paths.values())
    response = native_json(files, response_path)
    coordinator = verify_coordinator(response, children, knowledge_root=knowledge_root, **(acceptance_options or {}))
    workers = native_json(files, workers_path)
    require(workers == workers_from_pods(native_json(files, worker_pods_path)), 'Worker inventory disagrees with actual Pod IP/name/UID readbacks')
    require(isinstance(allowed_non_json_lines, (list, tuple))
            and all(isinstance(line, str) and '\n' not in line and '\r' not in line
                    and not line.lstrip().startswith(('{', '[')) for line in allowed_non_json_lines),
            'Only exact reviewed non-request startup text may be allowed')
    records = []; unparsed = 0
    for line in payload(files, gateway_path).splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line, object_pairs_hook=unique_object)
        except json.JSONDecodeError:
            require(not line.lstrip().startswith((b'{', b'[')), 'Malformed native gateway JSON record cannot be skipped')
            require(line.decode('utf-8') in allowed_non_json_lines, 'Unreviewed non-JSON gateway output cannot be skipped')
            unparsed += 1
            continue
        require(isinstance(row, dict), 'Native gateway JSON line is not an object')
        records.append(row)
    transport = verify_delegation_paths(records, coordinator['context_id'],
        {role: row['context_id'] for role, row in coordinator['children'].items()}, workers)
    return {'passed': True, 'coordinator': coordinator, 'transport': transport, 'worker_inventory_verified': True,
            'input_sha256': {name: sha256(payload(files, name)).hexdigest() for name in names},
            'gateway_json_records': len(records), 'non_json_gateway_lines': unparsed,
            'scope': 'Selected child contents and exact gateway transport agree with original worker Pod readbacks. '
                     'Payload authenticity requires the separately pinned complete native archive.'}


def object_identities(value, expected_kind=None):
    """Keep complete native resource UIDs/specs while excluding volatile Kubernetes status metadata."""
    require(isinstance(value, dict) and isinstance(value.get('items'), list), 'Missing native resource list')
    result = {}
    for item in value['items']:
        require(isinstance(item, dict) and (expected_kind is None or item.get('kind') == expected_kind), 'Unexpected native resource kind')
        metadata = item.get('metadata', {}); name = metadata.get('name')
        require(isinstance(name, str) and name and name not in result and metadata.get('namespace') == 'iot-agents'
                and isinstance(metadata.get('uid'), str) and metadata['uid'] and isinstance(item.get('spec'), dict),
                'Missing, duplicate or foreign native resource identity')
        result[name] = {'uid': metadata['uid'], 'spec': item['spec']}
    return result


def resource_identities(value):
    """Authenticate all five independently captured namespace inventories without dropping extra objects."""
    kinds = {'sandboxagents': 'SandboxAgent', 'modelconfigs': 'ModelConfig', 'remotemcpservers': 'RemoteMCPServer',
             'workerpools': 'WorkerPool', 'deployments': 'Deployment'}
    require(isinstance(value, dict) and set(value) == set(kinds), 'Require the complete five-kind resource snapshot')
    result = {name: object_identities(value[name], kind) for name, kind in kinds.items()}
    require(all(result.values()), 'An independently observed resource inventory is empty')
    return result


def gpu_output_tokens(raw):
    """Read one finite native llama.cpp output-token counter rather than trusting a stated delta."""
    values = [line.split()[1:] for line in raw.decode('utf-8').splitlines()
              if line.startswith('llamacpp:tokens_predicted_total ')]
    require(len(values) == 1 and len(values[0]) == 1, 'Missing or ambiguous native GPU output-token counter')
    try:
        value = float(values[0][0])
    except ValueError as error:
        raise ValueError('Invalid native GPU output-token counter') from error
    require(isfinite(value) and value >= 0, 'Non-finite or negative native GPU output-token counter')
    return value


def ci_artifact_files(files, artifacts_prefix, build):
    """Compare the complete captured CI directory with Jenkins' own archived-file inventory."""
    prefix = prefix_name(artifacts_prefix)
    indexed = []; marker = 'build-' + str(build['number']) + '/outputs/ci-agents/'
    require(isinstance(build.get('artifacts'), list), 'Missing Jenkins artifact inventory')
    for artifact in build['artifacts']:
        name = artifact.get('relativePath')
        require(isinstance(name, str) and name.startswith(marker), 'Unexpected Jenkins artifact location')
        indexed.append(member_name(name[len(marker):]))
    actual = {name[len(prefix):] for name in files if name.startswith(prefix)}
    require(len(indexed) == len(set(indexed)) and actual == set(indexed), 'CI output capture is incomplete or selectively filtered')
    return actual


def verify_ci_decision(files, decision_prefix, policy_pins, assets, *, allowed):
    """Bind a real CI decision and truth-constrained process exit to the exact policy input and source."""
    prefix = prefix_name(decision_prefix)
    value = native_json(files, prefix+'input.json'); raw = payload(files, prefix+'input.json')
    require(raw == canonical(value), 'CI policy input is not the original canonical envelope')
    decision = decision_value(native_json(files, prefix+'decision.stdout'))
    require(decision['allowed'] is allowed, 'CI did not reach the required explicit OPA outcome')
    for label, expected in [('decision', 0), ('gate', 0 if allowed else 1)]:
        code = native_json(files, prefix+label+'-exit.json').get('exit_code')
        require(type(code) is int and code == expected, 'CI OPA decision/gate exit differs')
        payload(files, prefix+label+'.stderr')
    gate = native_json(files, prefix+'gate.stdout')
    if allowed:
        require(isinstance(gate, dict) and len(gate.get('result', [])) == 1
                and len(gate['result'][0].get('expressions', [])) == 1
                and gate['result'][0]['expressions'][0].get('value') is True, 'CI allow gate has no explicit true result')
    else:
        require(isinstance(gate, dict) and not gate.get('result'), 'CI denied gate returned a defined result')
    receipt = native_json(files, prefix+'verification.json')
    require(receipt.get('allowed') is allowed and receipt.get('violations') == decision['violations']
            and type(receipt.get('gate_exit')) is int and receipt['gate_exit'] == (0 if allowed else 1)
            and receipt.get('input_sha256') == sha256(raw).hexdigest() and receipt.get('policy_files') == policy_pins
            and receipt.get('opa_sha256') == assets['sha256'] and receipt.get('opa_version') == assets['version'],
            'CI OPA receipt does not authenticate its actual input/policy/binary')
    return receipt, value


def ci_run(files, prefix, scenario, source_commit, stage_names):
    """Require an attributable terminal Jenkins run and its real stage/console outcome."""
    prefix = prefix_name(prefix); launch = native_json(files, prefix+'launch.json'); build = native_json(files, prefix+'build.json')
    number = build.get('number')
    require(type(number) is int and number > 0 and build.get('building') is False and build.get('inProgress') is False
            and build.get('fullDisplayName') == 'iot-llm-coordinator #' + str(number), 'Missing terminal coordinator Jenkins identity')
    require(launch.get('source_commit') == source_commit and launch.get('role') == 'coordinator'
            and launch.get('scenario') == scenario and launch.get('expected_build') == number and launch.get('http') == 201,
            'Jenkins observation belongs to another launch/source/scenario')
    parameters = [parameter for action in build.get('actions', []) for parameter in action.get('parameters', [])]
    require(len(parameters) == 3 and len({p.get('name') for p in parameters}) == 3
            and {p['name']: p.get('value') for p in parameters} == {'SOURCE_COMMIT': source_commit,
                'ADMISSION_SCENARIO': scenario, 'PROMPT_VERSIONS': '1,1,1'}, 'Jenkins parameters differ from the controlled release')
    denied = scenario == 'unsafe-tool'; result = 'FAILURE' if denied else 'SUCCESS'
    console = payload(files, prefix+'console.txt')
    require(build.get('result') == result and console.rstrip().endswith(('Finished: '+result).encode()), 'Jenkins result and original console disagree')
    stages = native_json(files, prefix+'stages.json'); rows = stages.get('stages')
    require(str(stages.get('id')) == str(number) and stages.get('status') == ('FAILED' if denied else 'SUCCESS')
            and isinstance(rows, list) and [row.get('name') for row in rows] == stage_names + ['Declarative: Post Actions'],
            'Jenkins stage inventory or run identity differs from the source pipeline')
    policy_index = stage_names.index('Admit model tools and sandbox with OPA')
    for index, row in enumerate(rows):
        expected = 'SUCCESS' if not denied or index < policy_index or index == len(rows)-1 else 'FAILED'
        require(row.get('status') == expected, 'Jenkins did not reach the intended policy/deployment boundary')
    if denied:
        for stage in stage_names[policy_index+1:]:
            require(('Stage "'+stage+'" skipped due to earlier failure(s)').encode() in console,
                    'Original console does not confirm the downstream stage was skipped')
    require(type(build.get('timestamp')) is int and type(build.get('duration')) is int and build['duration'] > 0,
            'Missing native Jenkins execution window')
    return build, release_identity('coordinator', number, source_commit)


def verify_ci(files, denied_prefix, allowed_prefix, denied_artifacts_prefix, allowed_artifacts_prefix, source_prefix,
              *, knowledge_root=KNOWLEDGE, acceptance_options=None, expected_source_files=None):
    """Reconcile a strict denied CI window and a separate accepted source/prompt/deployment/child-answer run."""
    source_prefix = prefix_name(source_prefix); denied_prefix = prefix_name(denied_prefix); allowed_prefix = prefix_name(allowed_prefix)
    denied_artifacts_prefix = prefix_name(denied_artifacts_prefix); allowed_artifacts_prefix = prefix_name(allowed_artifacts_prefix)
    seal = native_json(files, source_prefix+'manifest.json')
    critical = REQUIRED_SOURCE | {'agents/admission_ci.py', 'agents/ci_contract.py', 'agents/prompt_registry.py',
        'agents/answer_acceptance.py', 'agents/delegation_acceptance.py', 'scripts/ci-agents-policy.py',
        'scripts/ci-agents-prompts.py', 'scripts/ci-agents.py', 'scripts/ci-serving.py', 'infra/ci/AgentsPrompts.Jenkinsfile'}
    require(seal.get('format') == 'iot-sealed-llm-ci-v1' and isinstance(seal.get('files'), dict)
            and critical <= set(seal['files']) and re.fullmatch('[0-9a-f]{40}', str(seal.get('commit'))), 'Incomplete native CI source seal')
    require(expected_source_files is None or len(seal['files']) == expected_source_files, 'CI source inventory count differs')
    for name, digest in seal['files'].items():
        raw = payload(files, source_prefix+'tree/'+member_name(name))
        require(sha256(raw).hexdigest() == digest, 'As-run CI source changed: '+name)
        if name in critical:
            require(raw.replace(b'\r\n', b'\n') == (ROOT/name).read_bytes().replace(b'\r\n', b'\n'), 'Current CI interpreter differs from as-run source: '+name)
    digest = sha256(json.dumps(seal, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assets = native_json(files, source_prefix+'tree/infra/policy/opa-assets.json')
    pins = {name: seal['files'][name] for name in POLICIES}
    policy = native_json(files, source_prefix+'tree/agents/response-policy.json')
    pipeline = payload(files, source_prefix+'tree/infra/ci/AgentsPrompts.Jenkinsfile').decode('utf-8')
    stage_names = re.findall(r"stage\('([^']+)'\)", pipeline)
    require(len(stage_names) == 8 and len(set(stage_names)) == 8, 'Unexpected source-declared prompt-policy pipeline')
    denied_job, denied_identity = ci_run(files, denied_prefix, 'unsafe-tool', seal['commit'], stage_names)
    allowed_job, identity = ci_run(files, allowed_prefix, 'production', seal['commit'], stage_names)
    require(allowed_job['timestamp']+allowed_job['duration'] <= denied_job['timestamp']
            or denied_job['timestamp']+denied_job['duration'] <= allowed_job['timestamp'], 'Accepted and denied Jenkins executions overlap')
    denied_names = ci_artifact_files(files, denied_artifacts_prefix, denied_job)
    ci_artifact_files(files, allowed_artifacts_prefix, allowed_job)
    forbidden = {'policy.json', 'deployment-plan.json', 'deploy.json', 'verify.json', 'deploy.attempt', 'deploy-host.attempt',
                 'admitted-deploy-host.attempt', 'verify.attempt', 'verify-host.attempt'}
    require(not forbidden & denied_names, 'Denied CI reached a deployment/answer-verification artifact')
    require(b'OPA denied this candidate before any deployment command' in payload(files, denied_artifacts_prefix+'policy.txt'),
            'CI failed before the intended OPA denial boundary')
    denied_receipt, denied_input = verify_ci_decision(files, denied_artifacts_prefix+'policy-decision/', pins, assets, allowed=False)
    admitted, baseline = verify_ci_decision(files, allowed_artifacts_prefix+'policy-decision/', pins, assets, allowed=True)
    expected = next(case['input'] for case in cases(baseline) if case['id'] == 'unsafe-hardware-tool')
    require(denied_input == expected and denied_receipt['violations'] == [
        'unapproved capability or missing required field: SandboxAgent/iot-retrieval'], 'Denied candidate is not exactly the controlled unsafe-tool mutation')
    before = resource_identities(native_json(files, denied_prefix+'resources-before.json'))
    after = resource_identities(native_json(files, denied_prefix+'resources-after.json'))
    require(before == after, 'The denied CI observation changed native resource UIDs/specs')
    releases = {'iot-agents', 'iot-retrieval-mcp', 'iot-drift-mcp'}
    helm_before = native_json(files, denied_prefix+'helm-before.json'); helm_after = native_json(files, denied_prefix+'helm-after.json')
    require(isinstance(helm_before, dict) and set(helm_before) == releases and helm_before == helm_after,
            'The denied CI observation changed one of the three Helm revision readbacks')
    denied_tokens = [gpu_output_tokens(payload(files, denied_prefix+'model-'+phase+'.prom')) for phase in ['before', 'after']]
    require(denied_tokens[0] == denied_tokens[1], 'The denied CI observation has a nonzero native GPU output-token delta')
    for phase in ['test', 'build', 'deploy', 'verify']:
        value = require_receipt(native_json(files, allowed_artifacts_prefix+phase+'.json'), identity, phase)
        require('source_digest' not in value or value['source_digest'] == digest, 'Accepted phase source digest differs')
    admission = native_json(files, allowed_artifacts_prefix+'admission.json')
    require(admission.get('identity') == identity and admission.get('source_digest') == digest
            and admission.get('budget', {}).get('passed') is True
            and admission['budget']['seconds_before_actual_stop'] > admission['budget']['required_seconds'], 'Accepted source/session admission differs')
    prompts = native_json(files, allowed_artifacts_prefix+'prompts.json')
    require(prompts == {'schema_version': 1, 'phase': 'prompts', 'passed': True, **identity, 'source_digest': digest,
            'prompts': prompt_pins(policy['prompts'], dict.fromkeys(['retrieval', 'drift', 'coordinator'], 1))}, 'Actual MLflow v1 prompt receipt differs')
    policy_receipt = require_policy_phase(native_json(files, allowed_artifacts_prefix+'policy.json'), identity, digest)
    require(policy_receipt['decision'] == admitted, 'Accepted policy phase differs from its raw OPA decision')
    require_same_admitted_input(admitted, baseline)
    recheck, recheck_input = verify_ci_decision(files, allowed_artifacts_prefix+'policy-recheck-decision/', pins, assets, allowed=True)
    require(recheck_input == baseline, 'Deployment recheck changed the admitted input')
    require_same_admitted_input(recheck, baseline)
    rendered = [sha256(payload(files, allowed_artifacts_prefix+name)).hexdigest() for name in
                ['policy-candidate-rendered.yaml', 'policy-recheck-rendered.yaml', 'rendered-agents.yaml']]
    require(len(set(rendered)) == 1 and rendered[0] == policy_receipt['rendered_manifest_sha256'], 'Deployed/rechecked manifest differs from the admitted exact render')
    for label in ['policy-candidate', 'policy-recheck']:
        model = native_json(files, allowed_artifacts_prefix+label+'-model.json')
        require(model.get('metadata', {}).get('uid') == policy_receipt['model_uid']
                and model['spec'] == next(row['spec'] for row in baseline['resources'] if row['kind'] == 'ModelConfig'), 'Admitted shared model UID/spec changed')
    for phase in ['test', 'prompts', 'build', 'policy', 'admitted-deploy', 'verify']:
        value = native_json(files, allowed_artifacts_prefix+phase+'-container.json'); state = value.get('state', {})
        require(value.get('commit') == seal['commit'] and state.get('Running') is False and state.get('OOMKilled') is False
                and type(state.get('ExitCode')) is int and state['ExitCode'] == 0, 'Accepted native CI container did not complete successfully')
    failed = native_json(files, denied_artifacts_prefix+'policy-container.json')
    require(failed.get('commit') == seal['commit'] and failed['state'].get('Running') is False
            and failed['state'].get('OOMKilled') is False and type(failed['state'].get('ExitCode')) is int
            and failed['state']['ExitCode'] == 1, 'Denied policy container has a different native outcome')
    for kind in ['agents', 'models']:
        left = object_identities(native_json(files, allowed_artifacts_prefix+'all-'+kind+'-before-deploy.json'))
        right = object_identities(native_json(files, allowed_artifacts_prefix+'all-'+kind+'-after-deploy.json'))
        if kind == 'models': require(left == right, 'Accepted CI changed a retained model configuration')
        pair = {name: row for name, row in left.items() if name.startswith('iot-ab-')}
        require(len(pair) == 2 and pair == {name: row for name, row in right.items() if name.startswith('iot-ab-')}, 'Accepted CI changed a retained A/B identity/spec')
    response = native_json(files, allowed_artifacts_prefix+'agent-request/coordinator-response.json')
    children = {role: {kind: native_json(files, allowed_artifacts_prefix+'agent-request/'+role+'-'+kind+'.json')
                      for kind in ['session', 'tasks']} for role in ['retrieval', 'drift']}
    acceptance = verify_coordinator(response, children, knowledge_root=knowledge_root, **(acceptance_options or {}))
    require(acceptance == native_json(files, allowed_artifacts_prefix+'agent-acceptance.json')
            == native_json(files, allowed_artifacts_prefix+'verify.json')['actual_agent_acceptance'], 'Accepted CI answer differs from independently verified actual child histories')
    transport = native_json(files, allowed_artifacts_prefix+'agent-request/coordinator-transport.json')
    require(transport.get('http_status') == 200 and transport.get('response_complete') is True, 'Accepted CI parent response was incomplete')
    allowed_tokens = [gpu_output_tokens(payload(files, allowed_prefix+'model-'+phase+'.prom')) for phase in ['before', 'after']]
    require(allowed_tokens[1] > allowed_tokens[0], 'Accepted CI observation has no positive native GPU output-token delta')
    return {'passed': True, 'source_commit_label': seal['commit'], 'source_files': len(seal['files']),
            'denied_build': denied_job['number'], 'allowed_build': allowed_job['number'], 'denied_gate_exit': 1,
            'unchanged_denied_resources': {kind: len(rows) for kind, rows in before.items()}, 'unchanged_helm_releases': sorted(releases),
            'denied_gpu_output_token_delta': denied_tokens[1]-denied_tokens[0], 'allowed_gpu_output_token_delta': allowed_tokens[1]-allowed_tokens[0],
            'accepted_coordinator': acceptance, 'prompt_versions': prompts['prompts'],
            'scope': 'Independent native CI snapshots, complete artifact inventories and raw GPU counters for the supplied observation windows. '
                     'Accepted child contents are checked separately from gateway wire-path proof; no global no-side-effect or future-uptime claim.'}


def verify_review(files, index):
    """Use an explicitly pinned path declaration without assuming unpublished native outcomes or identifiers."""
    require(isinstance(index, dict) and index.get('format') == 'iot-agent-delegation-review-v1', 'Unknown review path declaration')
    require({'format', 'policy', 'conversation'} <= set(index) <= {'format', 'policy', 'conversation', 'ci', 'post_ci_conversation'},
            'Incomplete or unrecognized review declaration')
    policy = dict(index['policy']); prefix = policy.pop('policy_prefix')
    result = {'passed': True, 'policy': verify_policy_suite(files, prefix, **policy),
              'conversation': verify_conversation(files, **index['conversation'])}
    if 'ci' in index:
        result['ci'] = verify_ci(files, **index['ci'])
    if 'post_ci_conversation' in index:
        result['post_ci_conversation'] = verify_conversation(files, **index['post_ci_conversation'])
    return result


def verify():
    """Verify the project-default capsule only after its final inventory and path index are independently pinned."""
    from scripts.integration_evidence import capsule
    require(isinstance(CAPSULE_MANIFEST_SHA256, str) and re.fullmatch('[0-9a-f]{64}', CAPSULE_MANIFEST_SHA256)
            and isinstance(REVIEW_INDEX_SHA256, str) and re.fullmatch('[0-9a-f]{64}', REVIEW_INDEX_SHA256),
            'Final public capsule/index bindings have not been installed')
    base, public_count = capsule(CAPSULE_NAME, CAPSULE_MANIFEST_SHA256)
    index_raw = (base/REVIEW_INDEX_NAME).read_bytes()
    require(sha256(index_raw).hexdigest() == REVIEW_INDEX_SHA256, 'Final review path index changed')
    capture = json.loads((base/'native-archive.json').read_bytes(), object_pairs_hook=unique_object)
    files = load_native_archive((base/'native-receipts.tar.gz').read_bytes(), expected_archive_sha256=capture['archive_sha256'],
                                expected_manifest_sha256=capture['manifest_sha256'])
    require(len(files) == capture['native_payloads'] and capture.get('credential_scan_passed') is True,
            'Native capture payload count or original credential scan differs')
    result = verify_review(files, json.loads(index_raw, object_pairs_hook=unique_object))
    result['source_packets_and_endpoints'] = verify_source_packets(files)
    shots = json.loads((base/'ui-capture.json').read_bytes(), object_pairs_hook=unique_object)
    require(shots.get('runtime_screenshots') is True and shots.get('image_bytes_edited') is False
            and shots.get('parent_context') == result['conversation']['coordinator']['context_id']
            and shots.get('child_contexts') == {role: value['context_id'] for role, value in
                result['conversation']['coordinator']['children'].items()}, 'UI context ownership differs from the native conversation')
    require(len(shots.get('images', {})) == 8, 'Require the eight selected original UI captures')
    for name, pin in shots['images'].items():
        require(member_name(name) == name and '/' not in name and name.endswith('.jpg'), 'Unexpected screenshot path')
        body = (base/'ui'/name).read_bytes()
        require(body.startswith(b'\xff\xd8\xff') and len(body) == pin['bytes']
                and sha256(body).hexdigest() == pin['sha256'], 'Original screenshot bytes changed')
    stopped = json.loads((base/'vms-stopped.json').read_bytes(), object_pairs_hook=unique_object)
    require(len(stopped) == 2 and {row['name'] for row in stopped} == {'iot-cw-vm', 'iot-cw-gpu'}, 'Unexpected stopped VM inventory')
    vm_states = {}
    for row in stopped:
        require(row['status'] == 'TERMINATED' and row['scheduling']['maxRunDuration']['seconds'] == '28800'
                and row['scheduling']['instanceTerminationAction'] == 'STOP', 'VM did not retain the approved STOP policy')
        start = datetime.fromisoformat(row['lastStartTimestamp']); end = datetime.fromisoformat(row['lastStopTimestamp'])
        role = 'cpu' if row['name'] == 'iot-cw-vm' else 'gpu'
        original = native_json(files, 'session/'+role+'-descriptor.json')
        require(start == datetime.fromisoformat(original['lastStartTimestamp']) and
                timedelta(0) < end-start < timedelta(hours=8), 'VM stop is not within the actual approved session')
        vm_states[row['name']] = end.astimezone(timezone.utc).isoformat()
    result.update(original_ui_images=8, vms_stopped=vm_states)
    result.update(native_payloads=len(files), public_payloads=public_count)
    return result


def main():
    """Review only explicitly hash-pinned local artifacts; never launch workloads or contact a service."""
    if len(sys.argv) == 1:
        print(json.dumps(verify(), indent=2))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['archive', 'archive-sha256', 'manifest-sha256', 'index', 'index-sha256']:
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--archive-prefix', default='')
    args = parser.parse_args()
    raw = Path(args.index).read_bytes()
    require(sha256(raw).hexdigest() == args.index_sha256, 'Review path declaration hash changed')
    files = load_native_archive(Path(args.archive).read_bytes(), expected_archive_sha256=args.archive_sha256,
                                expected_manifest_sha256=args.manifest_sha256, archive_prefix=args.archive_prefix)
    print(json.dumps(verify_review(files, json.loads(raw, object_pairs_hook=unique_object)), indent=2))


if __name__ == '__main__':
    main()
