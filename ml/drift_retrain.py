"""Gate Airflow drift-triggered KFP retraining, retaining durable intent and refusing ambiguous replay."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse, json, math, re, sqlite3, subprocess, sys

FIELDS = ('temperature_mean_10m', 'vibration_max_10m', 'current_mean_10m', 'current_std_10m',
          'rpm_mean_10m', 'reading_count_10m', 'machine_age_days', 'prior_failures_24h')


def digest(path):
    """Bind small control-plane inputs to original bytes before any training side effect."""
    return sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    """Read a retained JSON object without executing content from the document."""
    return json.loads(Path(path).read_bytes())


def save(path, value):
    """Persist a new receipt exclusively; a killed write remains an explicit unresolved attempt."""
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False); stream.write('\n'); stream.flush()
        import os
        os.fsync(stream.fileno())


def clock(value):
    """Require explicit offsets for age, cooldown and deadline comparisons."""
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None: raise ValueError('Explicit timezone required')
    return result.astimezone(timezone.utc)


def local(root, value):
    """Resolve only a canonical project-relative trusted deployment input."""
    if not isinstance(value, str) or '\\' in value or ':' in value:
        raise ValueError('Project-relative paths required')
    path = Path(value)
    resolved = (root / path).resolve()
    if not value or path.is_absolute() or '..' in path.parts or path.as_posix() != value or not resolved.is_relative_to(root):
        raise ValueError('Project-relative paths required')
    return resolved


def validate(root, directory, policy):
    """Bind the successful native drift report and fixed training recipe; never infer quality from PSI."""
    required = {'format', 'enabled', 'allow_manual_probe', 'cooldown_seconds', 'max_report_age_seconds',
                'reference_id', 'reference_sha256', 'source_sha256', 'training_files'}
    if set(policy) != required or policy['format'] != 'iot-drift-retrain-v1': raise ValueError('Unknown retrain policy')
    if type(policy['enabled']) is not bool or type(policy['allow_manual_probe']) is not bool:
        raise ValueError('Explicit policy booleans required')
    if policy['cooldown_seconds'] != 3600 or policy['max_report_age_seconds'] != 600:
        raise ValueError('Keep one-hour cooldown and ten-minute report admission')
    for name in ['reference_id', 'reference_sha256', 'source_sha256']:
        if not re.fullmatch('[0-9a-f]{64}', policy[name]): raise ValueError('Invalid input binding')
    if not isinstance(policy['training_files'], dict) or not policy['training_files']:
        raise ValueError('Seal the training recipe before enabling retrain')
    for name, expected in policy['training_files'].items():
        if not re.fullmatch('[0-9a-f]{64}', expected) or digest(local(root, name)) != expected:
            raise ValueError('Training recipe changed')
    report, retrieval, completion = (read(directory / n) for n in ['report.json', 'retrieval.json', 'completion.json'])
    if not (completion['passed'] is True and completion['push_status'] == 200 and retrieval['passed'] is True
            and retrieval['native_feast_retrieval'] is True and retrieval['all_eight_values_checked'] is True
            and completion['report_sha256'] == digest(directory / 'report.json')
            and completion['metrics_sha256'] == digest(directory / 'metrics.prom')
            and completion['reference_id'] == report['reference_id'] == policy['reference_id']
            and retrieval['reference_sha256'] == policy['reference_sha256']
            and retrieval['source_sha256'] == policy['source_sha256']
            and retrieval['rows'] == report['unique_rows'] and 32 <= report['unique_rows'] <= 2048):
        raise ValueError('Native drift completion or input binding differs')
    if report['policy'] != {'bins':10, 'psi_threshold':.2, 'drifted_feature_fraction':.25, 'pseudocount':.5}:
        raise ValueError('Keep the frozen PSI decision policy')
    if set(report['features']) != set(FIELDS): raise ValueError('Require all eight drift inputs')
    flags = 0
    for item in report['features'].values():
        score = item['psi']
        if type(score) not in [int, float] or not math.isfinite(score) or score < 0 or item['drifted'] is not (score >= .2):
            raise ValueError('Inconsistent drift decision')
        flags += score >= .2
    if report['drift_detected'] is not (flags >= 2) or report['drifted_feature_fraction'] != flags / 8:
        raise ValueError('Inconsistent aggregate drift decision')
    if clock(report['start']) >= clock(report['end']): raise ValueError('Invalid report window')
    return report, completion


def process(root, directory, policy_path, state, probe, train, now=None):
    """Serialize admissions and return a prior success on replay; an uncertain intent can never submit twice."""
    root = Path(root).resolve(); directory = Path(directory).resolve(); state = Path(state)
    if not directory.is_relative_to(root / 'outputs/offline-drift'): raise ValueError('Only native drift output accepted')
    if type(probe) is not bool: raise ValueError('Manual probe must be a boolean')
    policy = read(policy_path); report, completion = validate(root, directory, policy)
    now = now or datetime.now(timezone.utc); now = clock(now.isoformat())
    reason = 'detected_drift' if report['drift_detected'] else 'no_detected_drift'
    if probe:
        if not policy['allow_manual_probe']: raise ValueError('Manual probe disabled by policy')
        reason = 'explicit_manual_probe'
    audit = {'report_sha256': digest(directory / 'report.json'), 'completion_sha256': digest(directory / 'completion.json'),
             'policy_sha256': digest(policy_path), 'reference_id': report['reference_id'], 'window_id': report['window_id'],
             'drift_detected': report['drift_detected'], 'manual_probe': probe, 'reason': reason, 'checked_utc': now.isoformat()}
    if not policy['enabled'] or not (probe or report['drift_detected']):
        return {**audit, 'passed': True, 'action': 'skipped', 'reason': reason if policy['enabled'] else 'policy_disabled'}
    state.mkdir(parents=True, exist_ok=True)
    # SQLite releases this process lock after a kill. The separate, fsynced intent remains durable.
    connection = sqlite3.connect(state / 'admission.sqlite', timeout=1)
    try:
        connection.execute('BEGIN IMMEDIATE')
        key = sha256(json.dumps([audit['reference_id'], audit['window_id'], audit['policy_sha256']], separators=(',', ':')).encode()).hexdigest()[:24]
        run = 'drift-' + key; attempt = state / run
        if (attempt / 'result.json').exists():
            previous = read(attempt / 'result.json')
            if previous['report_sha256'] != audit['report_sha256'] or not previous['passed']:
                raise ValueError('Replay conflicts with retained result')
            return {**previous, 'action': 'reused_completed_submission', 'replayed_utc': now.isoformat()}
        if any(not (p.parent / 'result.json').exists() for p in state.glob('drift-*/intent.json')):
            raise ValueError('Unresolved training intent; reconcile the existing KFP run before another submission')
        age = (now - clock(completion['completed_wall_utc'])).total_seconds()
        if not 0 <= age <= policy['max_report_age_seconds']: raise ValueError('Stale or future drift completion')
        completed = [read(p)['completed_utc'] for p in state.glob('drift-*/result.json')]
        if completed and (now - max(clock(t) for t in completed)).total_seconds() < policy['cooldown_seconds']:
            return {**audit, 'passed': True, 'action': 'skipped', 'reason': 'cooldown'}
        attempt.mkdir(exist_ok=False)
        save(attempt / 'intent.json', {**audit, 'run_name': run})
        trained = train(run, attempt)
        if not (trained['passed'] is True and trained['production'] is False and trained['run_id']
                and trained['state'] == 'SUCCEEDED' and trained['run_name'] == run):
            raise ValueError('Native KFP success and candidate-only registration required')
        result = {**audit, **trained, 'action': 'submitted_and_verified', 'completed_utc': datetime.now(timezone.utc).isoformat()}
        save(attempt / 'result.json', result)
        return result
    finally:
        connection.rollback(); connection.close()


def native_train(root, run, attempt):
    """Reuse the unchanged guarded runner that calls KFP API, then read its backend result independently."""
    root = Path(root)
    session = read(root / 'data/runtime/ci/session.json')
    recipe = root / 'data/ci/releases/train-jenkins-2'
    command = [sys.executable, str(root / 'scripts/run-versioned-training.py'), '--run', run,
        '--deadline-utc', session['auto_stop_utc'], '--source-binding', str(recipe / 'source-snapshot.json'),
        '--deployment-manifest', str(recipe / 'deployment.json'), '--oracle', 'outputs/distributed-oracle/distributed-smoke-27-v2/verification.json']
    save(attempt / 'runner-command.json', {'command': command, 'scope': 'Unchanged five-task KFP recipe; fixed labelled medium cohort, not unlabelled drift observations or automatic promotion.'})
    output = root / 'outputs/versioned-training' / run
    try:
        with (attempt / 'controller.txt').open('x') as stream:
            subprocess.run(command, cwd=root, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=3900)
    except Exception:
        # A runner timeout may leave an accepted backend run. Reconcile/terminate only its exact identity.
        if (output / 'submission.json').is_file():
            try:
                endpoint, actual = backend_run(read(output / 'submission.json')['run_id'], run, terminate=True)
                save(attempt / 'failed-run-cleanup.json', {'endpoint':endpoint, 'run':json.loads(json.dumps(actual, default=str))})
            except Exception as error:
                save(attempt / 'failed-run-cleanup-error.json', {'type':type(error).__name__, 'scope':'Unresolved intent retained; no automatic resubmission.'})
        raise
    verification, submission = read(output / 'verification.json'), read(output / 'submission.json')
    endpoint, actual = backend_run(submission['run_id'], run)
    save(attempt / 'backend-run.json', json.loads(json.dumps(actual, default=str)))
    if verification['run_id'] != submission['run_id'] or verification['production'] is not False:
        raise ValueError('Runner and backend lineage disagree')
    return {'passed': verification['passed'], 'run_id': submission['run_id'], 'run_name': actual['display_name'],
        'state': str(actual['state']).upper(), 'production': verification['production'],
        'registered_model_version': verification['registered_model_version'], 'model_run_id': verification['model_run_id'],
        'data_version': verification['data_version'], 'api_endpoint': endpoint,
        'verification_sha256': digest(output / 'verification.json'), 'backend_run_sha256': digest(attempt / 'backend-run.json')}


def backend_run(run_id, expected_name, terminate=False):
    """Read the private observed KFP service, refusing termination of another workflow's run."""
    import kfp
    service = json.loads(subprocess.check_output(['/usr/local/bin/k3s', 'kubectl', 'get', 'service', 'ml-pipeline', '-n', 'kubeflow', '-o', 'json']))
    endpoint = 'http://' + service['spec']['clusterIP'] + ':8888'
    client = kfp.Client(host=endpoint, namespace='kubeflow'); actual = client.get_run(run_id).to_dict()
    if actual['display_name'] != expected_name: raise ValueError('Backend run belongs to another workflow')
    if terminate and str(actual['state']).upper() not in ['SUCCEEDED','FAILED','CANCELED','CANCELLED','SKIPPED']:
        client.terminate_run(run_id); actual = client.get_run(run_id).to_dict()
    return endpoint, actual


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['root', 'report', 'policy', 'state', 'output']: parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--manual-probe', action='store_true'); args = parser.parse_args()
    result = process(args.root, args.report, args.policy, args.state, args.manual_probe,
                     lambda run, attempt: native_train(args.root, run, attempt))
    save(args.output, result); print(json.dumps(result, allow_nan=False))
