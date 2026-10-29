"""Execute real specialist/MCP questions in Jupyter and retain failed cells and original native receipts."""
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
import argparse
import base64
import json
import os
import shutil
import subprocess
import sys


NOTEBOOK = 'notebooks/03_agent_feature_drift_rag.ipynb'


def source_manifest(source):
    """Require exact sealed source files, including the original catalog bytes, before any live request."""
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    required = {NOTEBOOK, 'scripts/run-agent-notebook.py', 'agents/__init__.py',
                'agents/probe.py', 'agents/answer_acceptance.py', 'knowledge/catalog.json'}
    if not required.issubset(seal['files']): raise ValueError('Notebook source seal is incomplete')
    for name, digest in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Sealed notebook source changed: ' + name)
    return seal


def run(root, source, output, feature_run):
    """Check real resource/data/session state, then let an actual Jupyter kernel invoke the native agents."""
    if os.name != 'posix' or os.geteuid() != 0: raise ValueError('Use sudo on the retained private lab VM')
    root, source, output = root.resolve(), source.resolve(), output.resolve()
    if (not root.is_relative_to('/srv/iot-data') or not source.is_relative_to(root / 'data/llm-source')
            or not output.is_relative_to(root / 'outputs') or output.exists()):
        raise ValueError('Use sealed lab source and a new project output directory')
    seal = source_manifest(source)
    if sha256(Path(__file__).read_bytes()).hexdigest() != seal['files']['scripts/run-agent-notebook.py']:
        raise ValueError('Execute the sealed notebook runner')
    now = datetime.now(timezone.utc)
    lease_path = (root / 'outputs' / feature_run / 'verification.json').resolve()
    if not lease_path.is_relative_to(root / 'outputs'): raise ValueError('Use a native feature-preparation report')
    lease = json.loads(lease_path.read_bytes())
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    ram = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))) * 1024
    if (not lease['passed'] or lease['machines'] != 200 or datetime.fromisoformat(lease['prepared_wall_utc']) > now
            or (datetime.fromisoformat(lease['lease_expires_utc']) - now).total_seconds() < 1200
            or (datetime.fromisoformat(session['auto_stop_utc']) - now).total_seconds() < 1800
            or ram < 8 * 1024**3 or shutil.disk_usage('/srv/iot-fast').free < 10 * 1024**3):
        raise ValueError('Require genuine features and enough session/RAM/SSD for notebook execution')
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'
    private = []
    for name in ['agent-platform.json', 'mcp-tools.json']:
        for value in json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values():
            private.extend([value.encode(), base64.b64encode(value.encode())])
    def kube(kind, name, namespace):
        """Read the actual specifically named private Kubernetes object without exporting Secrets."""
        return subprocess.check_output(['/usr/local/bin/k3s', 'kubectl', 'get', kind, name,
                                        '-n', namespace, '-o', 'json'], timeout=20)
    controller_raw = kube('service', 'kagent-controller', 'iot-agents')
    controller = json.loads(controller_raw)
    if controller['spec'].get('type', 'ClusterIP') != 'ClusterIP': raise ValueError('Keep A2A private')
    base = 'http://' + controller['spec']['clusterIP'] + ':8083'
    opener = build_opener(ProxyHandler({}))
    with opener.open(base + '/api/substrate/status', timeout=20) as response: actors_raw = response.read()
    actors = json.loads(actors_raw)['data']['actors']
    if any(a['atespace'] == 'iot-agents' and a['status'] == 'Running' for a in actors):
        raise ValueError('Wait for the preceding actor workload to become idle')
    from jupyter_client.kernelspec import KernelSpecManager
    from nbclient import NotebookClient
    from nbconvert import HTMLExporter
    import nbformat
    os.environ['PATH'] = str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']
    os.environ['JUPYTER_PATH'] = str(Path(sys.prefix) / 'share/jupyter')
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    manager = KernelSpecManager(); kernel = manager.get_kernel_spec('python3')
    if Path(kernel.argv[0]).absolute() != Path(sys.executable).absolute():
        raise ValueError('The registered kernel must use this isolated notebook environment')
    output.mkdir(parents=True, exist_ok=False)
    def keep(name, raw):
        """Reject credential-bearing artifacts and retain all successful or failed original outputs."""
        if any(value in raw for value in private): raise ValueError('Credential-bearing notebook output refused')
        path = output / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    def save(name, value):
        """Serialize strict phase metadata without synthesizing a native response."""
        keep(name, (json.dumps(value, indent=2, allow_nan=False) + '\n').encode())
    save('source.json', seal); keep('feature-lease.json', lease_path.read_bytes())
    keep('controller-service.json', controller_raw); keep('actors-before.json', actors_raw)
    context = {'source_root': str(source), 'output_root': str(output / 'requests'),
               'controller_base_url': base, 'feature_lease': lease, 'source_files': seal['files']}
    save('context.json', context)
    os.environ['IOT_AGENT_NOTEBOOK_CONTEXT'] = str(output / 'context.json')
    document = nbformat.read(source / NOTEBOOK, as_version=4)
    if any(c.cell_type == 'code' and (c.execution_count is not None or c.outputs) for c in document.cells):
        raise ValueError('Start from the unexecuted source notebook')
    started = datetime.now(timezone.utc).isoformat(); passed = False
    try:
        client = NotebookClient(document, timeout=420, kernel_name='python3', force_raise_errors=True,
                                ipython_hist_file=':memory:', resources={'metadata': {'path': str(source)}})
        client.km = client.create_kernel_manager()
        if client.km.kernel_spec.argv != kernel.argv:
            raise ValueError('The execution kernel differs from the checked isolated kernel')
        client.execute()
        passed = True
    finally:
        keep('executed.ipynb', nbformat.writes(document).encode())
        html, _ = HTMLExporter().from_notebook_node(document)
        keep('executed.html', html.encode())
        errors = sum(c.cell_type == 'code' and any(o.output_type == 'error' for o in c.outputs) for c in document.cells)
        report = {'passed': passed and errors == 0, 'started_utc': started, 'completed_utc': datetime.now(timezone.utc).isoformat(),
                  'code_cells': sum(c.cell_type == 'code' for c in document.cells),
                  'executed_code_cells': sum(c.cell_type == 'code' and c.execution_count is not None for c in document.cells),
                  'error_cells': errors, 'source_notebook_sha256': seal['files'][NOTEBOOK],
                  'executed_notebook_sha256': sha256((output / 'executed.ipynb').read_bytes()).hexdigest(),
                  'runner_sha256': seal['files']['scripts/run-agent-notebook.py'],
                  'python': sys.version, 'kernel_argv': kernel.argv,
                  'packages': {name: version(name) for name in ['nbclient', 'nbformat', 'nbconvert', 'ipykernel', 'jupyter-client']},
                  'source_files_unchanged': source_manifest(source) == seal,
                  'coursework_complete': False,
                  'scope': 'Real Jupyter kernel invokes two native specialist agents; original MCP histories and source-grounded final answers retained. No coordinator, throughput, HA or full-coursework claim.'}
        save('execution.json', report)
        # Probe outputs are original files written by the notebook; scan them before any publication.
        for path in output.rglob('*'):
            if path.is_file() and any(value in path.read_bytes() for value in private):
                raise ValueError('Credential-bearing native notebook artifact refused')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--feature-run', required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.runtime_root, args.source_root, args.output, args.feature_run))
