"""Map declared replay clocks to bounded offline windows and expose label-free batch gauges."""
from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import urlsplit

FIELDS = ('temperature_mean_10m', 'vibration_max_10m', 'current_mean_10m',
          'current_std_10m', 'rpm_mean_10m', 'reading_count_10m', 'machine_age_days', 'prior_failures_24h')


def utc(value):
    """Require an explicit UTC clock rather than interpreting a naive local timestamp."""
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError('Require an explicit UTC timestamp')
    return result.astimezone(timezone.utc)


def window(config, interval_end):
    """Advance the simulated closed window with scheduler time, stopping at sealed data boundaries."""
    required = {'format', 'wall_origin', 'event_origin', 'width_seconds', 'data_start', 'data_end',
                'reference_train_end', 'sample_limit', 'source_sha256', 'reference_sha256'}
    if set(config) != required or config['format'] != 'iot-offline-drift-clock-v1':
        raise ValueError('Unknown offline monitoring configuration')
    for name in ('source_sha256', 'reference_sha256'):
        if not re.fullmatch('[0-9a-f]{64}', config[name]):
            raise ValueError('Pin immutable input hashes')
    if type(config['sample_limit']) is not int or not 32 <= config['sample_limit'] <= 2048:
        raise ValueError('Require32..2048 sampled observations')
    width = config['width_seconds']
    if type(width) is not int or not 300 <= width <= 86400:
        raise ValueError('Require a bounded300..86400s window')
    delta = utc(interval_end) - utc(config['wall_origin'])
    if delta.total_seconds() < 0:
        raise ValueError('Scheduler interval predates the declared replay origin')
    end = utc(config['event_origin']) + delta
    start = end - timedelta(seconds=width)
    if start < max(utc(config['data_start']), utc(config['reference_train_end'])) or end > utc(config['data_end']):
        raise ValueError('Monitoring window crosses train or sealed offline bounds')
    return start, end


def push_url(endpoint):
    """Restrict this host-network lab publisher to the explicit loopback Pushgateway."""
    parsed = urlsplit(endpoint)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.port != 19092
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise ValueError('Use the private loopback Pushgateway on19092')
    return 'http://127.0.0.1:19092/metrics/job/iot_offline_drift'


def exposition(report, completed_at):
    """Replace one stable grouping with gauges; run IDs, timestamps and machine IDs are never labels."""
    if set(report['features']) != set(FIELDS) or report['unique_rows'] < 32:
        raise ValueError('Incomplete drift assessment')
    def label(value):
        return json.dumps(str(value), ensure_ascii=True)
    rows = []
    def gauge(name, help_text, values):
        rows.extend(['# HELP ' + name + ' ' + help_text, '# TYPE ' + name + ' gauge'])
        rows.extend(name + labels + ' ' + str(value) for labels, value in values)
    gauge('iot_offline_drift_psi', 'Marginal PSI; not accuracy or a p-value.',
          [('{feature=' + label(name) + '}', report['features'][name]['psi']) for name in FIELDS])
    gauge('iot_offline_drift_feature_alert', 'Declared PSI policy flag.',
          [('{feature=' + label(name) + '}', int(report['features'][name]['drifted'])) for name in FIELDS])
    for suffix, text, value in [
            ('detected', 'Declared fraction-of-features policy decision.', int(report['drift_detected'])),
            ('feature_fraction', 'Fraction of eight features above the fixed PSI threshold.', report['drifted_feature_fraction']),
            ('sample_rows', 'Unique sampled feature snapshots, not independent requests.', report['unique_rows']),
            ('window_start_seconds', 'Start of simulated event-time window inclusive.', utc(report['start']).timestamp()),
            ('window_end_seconds', 'End of simulated event-time window exclusive.', utc(report['end']).timestamp()),
            ('last_success_seconds', 'Actual wall-clock completion of a successful offline comparison.', utc(completed_at).timestamp())]:
        gauge('iot_offline_drift_' + suffix, text, [('', value)])
    gauge('iot_offline_drift_reference_info', 'Pinned label-free training reference identity.',
          [('{reference_id=' + label(report['reference_id']) + '}', 1)])
    raw = '\n'.join(rows) + '\n'
    # Standard JSON validation rejects accidental NaN/Infinity in derived numbers before any HTTP push.
    json.dumps(report, allow_nan=False)
    return raw


def dashboard():
    """Keep metric freshness and simulated event clocks visible beside the eight PSI values."""
    expressions = [
        ('PSI by feature (fixed train reference)', 'iot_offline_drift_psi{job="iot_offline_drift"}', 'short'),
        ('Features flagged by declared policy', 'iot_offline_drift_feature_alert{job="iot_offline_drift"}', 'short'),
        ('Unique snapshots sampled', 'iot_offline_drift_sample_rows{job="iot_offline_drift"}', 'none'),
        ('Seconds since successful comparison', 'time()-iot_offline_drift_last_success_seconds{job="iot_offline_drift"}', 's'),
        ('SIMULATED window start', 'iot_offline_drift_window_start_seconds{job="iot_offline_drift"} * 1000', 'dateTimeAsIso'),
        ('SIMULATED window end (exclusive)', 'iot_offline_drift_window_end_seconds{job="iot_offline_drift"} * 1000', 'dateTimeAsIso')]
    panels = []
    for index, (title, expr, unit) in enumerate(expressions):
        defaults = {'unit': unit, 'min': 0, 'color': {'mode': 'palette-classic'} if index < 2 else {'mode': 'fixed', 'fixedColor': 'blue'}}
        if index == 2:
            defaults['decimals'] = 0
        if index == 3:
            defaults.update(color={'mode': 'thresholds'}, thresholds={'mode': 'absolute',
                'steps': [{'color': 'green', 'value': None}, {'color': 'red', 'value': 600}]})
        panels.append({'id': index + 1, 'title': title, 'type': 'timeseries' if index < 2 else 'stat',
            'datasource': {'type': 'prometheus', 'uid': 'iot-host-prometheus'},
            'gridPos': {'x': index % 2 * 12, 'y': index // 2 * 8, 'w': 12, 'h': 8},
            'targets': [{'refId': 'A', 'expr': expr, 'legendFormat': '{{feature}}'}],
            'fieldConfig': {'defaults': defaults, 'overrides': []},
            'options': {'reduceOptions': {'calcs': ['lastNotNull'], 'values': False}, 'graphMode': 'none', 'colorMode': 'value'} if index >= 2 else {}})
    return {'id': None, 'uid': 'iot-offline-drift', 'title': 'IoT offline drift / simulated replay',
        'schemaVersion': 39, 'version': 1, 'editable': False, 'timezone': 'utc', 'refresh': '5s',
        'description': 'Airflow reads immutable Feast offline features. Simulated windows after train; no labels, accuracy or automatic promotion. Pushgateway retains old gauges: inspect wall-clock freshness.',
        'tags': ['iot', 'drift', 'offline-replay'], 'time': {'from': 'now-15m', 'to': 'now'}, 'panels': panels}
