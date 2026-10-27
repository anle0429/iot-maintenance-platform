"""Attribute real Spark task attempts to job groups and retain stage-level skew, IO and spill evidence."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics

from generator.storage import write_json


def summarize(path: Path) -> dict:
    """Keep bounded task statistics for this benchmark; retries count as performed work, not successful output."""
    stage_groups, stages, groups = defaultdict(set), {}, {}
    application_ended = False
    with path.open(encoding='utf-8') as source:
        while line := source.readline(8 * 1024 * 1024 + 1):
            if len(line) > 8 * 1024 * 1024:
                raise ValueError('oversized Spark event')
            event = json.loads(line)
            kind = event['Event']
            if kind == 'SparkListenerJobStart':
                group = (event.get('Properties') or {}).get('spark.jobGroup.id')
                if group:
                    groups.setdefault(group, {'job_ids': [], 'stage_ids': []})['job_ids'].append(event['Job ID'])
                    for stage in event['Stage IDs']:
                        stage_groups[stage].add(group)
            elif kind == 'SparkListenerTaskEnd':
                stage_id = event['Stage ID']
                stage = stages.setdefault(stage_id, {'task_attempts': 0, 'failed_attempts': 0, 'shuffle_read_bytes': 0,
                                                     'shuffle_write_bytes': 0, 'input_bytes': 0, 'disk_spill_bytes': 0,
                                                     'memory_spill_bytes': 0, 'tasks': []})
                info, metrics = event['Task Info'], event.get('Task Metrics') or {}
                reads = metrics.get('Shuffle Read Metrics') or {}
                read_bytes = reads.get('Remote Bytes Read', 0) + reads.get('Local Bytes Read', 0)
                stage['task_attempts'] += 1
                stage['failed_attempts'] += (event.get('Task End Reason') or {}).get('Reason') != 'Success'
                stage['shuffle_read_bytes'] += read_bytes
                stage['shuffle_write_bytes'] += (metrics.get('Shuffle Write Metrics') or {}).get('Shuffle Bytes Written', 0)
                stage['input_bytes'] += (metrics.get('Input Metrics') or {}).get('Bytes Read', 0)
                stage['disk_spill_bytes'] += metrics.get('Disk Bytes Spilled', 0)
                stage['memory_spill_bytes'] += metrics.get('Memory Bytes Spilled', 0)
                duration = max(0, info['Finish Time'] - info['Launch Time'])
                stage['tasks'].append({'partition': info.get('Index'), 'attempt': info.get('Attempt', 0),
                                       'duration_ms': duration, 'shuffle_read_bytes': read_bytes,
                                       'input_bytes': (metrics.get('Input Metrics') or {}).get('Bytes Read', 0)})
                if sum(value['task_attempts'] for value in stages.values()) > 100_000:
                    raise ValueError('event summary exceeds bounded benchmark task count')
            elif kind == 'SparkListenerStageCompleted':
                info = event['Stage Info']
                if info['Stage ID'] in stages:
                    stages[info['Stage ID']]['name'] = info.get('Stage Name')
            elif kind == 'SparkListenerApplicationEnd':
                application_ended = True
    for stage_id, stage in stages.items():
        durations = [task['duration_ms'] for task in stage['tasks']]
        nonempty = [task['duration_ms'] for task in stage['tasks'] if task['shuffle_read_bytes'] or task['input_bytes']]
        stage['duration_ms'] = {'max': max(durations, default=0), 'median': statistics.median(durations) if durations else None,
                                'nonempty_median': statistics.median(nonempty) if nonempty else None}
        stage['groups'] = sorted(stage_groups[stage_id])
    for group, item in groups.items():
        ids = sorted(stage for stage, assigned in stage_groups.items() if group in assigned)
        item['stage_ids'] = ids
        item['totals'] = {key: sum(stages.get(stage, {}).get(key, 0) for stage in ids)
                          for key in ('task_attempts', 'failed_attempts', 'shuffle_read_bytes', 'shuffle_write_bytes',
                                      'input_bytes', 'disk_spill_bytes', 'memory_spill_bytes')}
    return {'application_ended': application_ended, 'groups': groups,
            'stages': {str(key): value for key, value in sorted(stages.items())},
            'event_log_bytes': path.stat().st_size,
            'scope': 'Actual task-attempt metrics and durations from the completed event log. Reused stages may be attributed to multiple job groups; do not sum groups as disjoint application work. Nonempty-task median excludes empty read tasks, while all attempts remain visible. Cumulative bytes are not peak simultaneous scratch.'}


def main() -> None:
    """Read event logs under the Spark image UID and preserve a small JSON summary for review and UI navigation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.log)
    if not result['application_ended']:
        raise ValueError('application end missing; do not present partial task statistics as final')
    write_json(args.report, result)


if __name__ == '__main__':
    main()
