"""Record real private model/MCP/A2A requests through the prepared candidate gateway."""

from pathlib import Path
from uuid import uuid4
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from infra.agentgateway.candidate import DNS_NAME, PORTS, ROLES, bearer_hash
from infra.agentgateway.probe import GatewayProbe, checked_tool_facts


def main():
    """Run bounded checks from the selected private network or an existing loopback forward."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', type=Path, required=True, help='Private directory of role-scoped token files')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--loopback', action='store_true', help='Use retained loopback forwards instead of cluster DNS')
    parser.add_argument('--loopback-start-port', type=int, default=28080)
    parser.add_argument('--send-coordinator', action='store_true', help='Send a new v0.3 coordinator task after card checks')
    args = parser.parse_args()
    tokens = {role: (args.credentials / role).read_text().strip() for role in PORTS}
    if len({bearer_hash(value) for value in tokens.values()}) != len(PORTS):
        raise ValueError('Require four distinct scoped credentials')
    if not 1024 <= args.loopback_start_port <= 65532:
        parser.error('loopback port block must fit four nonprivileged ports')
    ports = {role: args.loopback_start_port + offset for offset, role in enumerate(PORTS)} if args.loopback else PORTS
    probe = GatewayProbe('127.0.0.1' if args.loopback else DNS_NAME, tokens, args.output, ports)
    probe.model()
    machine = checked_tool_facts('retrieval', probe.mcp('retrieval', {'machine_id': 'P-001'}))
    window = 'agw-' + uuid4().hex[:12]
    drift = checked_tool_facts('drift', probe.mcp('drift', {'window_id': window,
        'machine_ids': [f'P-{i:03d}' for i in range(1, 33)]}), window=window)
    if any(drift[key] != machine[key] for key in ('source_manifest_sha256', 'as_of')):
        raise ValueError('The routed MCP checks span different source snapshots')
    for role in ROLES:
        question = None
        if role == 'coordinator' and args.send_coordinator:
            question = ('Use both specialists. Get current features, prediction and a vibration reference for P-001. '
                        'Assess drift for all 32 unique machines P-001 through P-032 using window_id '
                        + window + '-delegate. Keep the actual model, source and reference IDs.')
        probe.a2a(role, question)
    result = {'transport_and_tool_contracts_passed': True, 'requests': len(probe.receipts),
              'coordinator_protocol_completed': args.send_coordinator, 'coordinator_factual_acceptance': False,
              'scope': 'Gateway transport, native completion/tool results and A2A cards or task. Separate '
                       'child acceptance, network-isolation proof and shared ModelConfig traffic are still required.'}
    (args.output / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
