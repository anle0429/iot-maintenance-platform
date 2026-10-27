"""Summarize actual Spark task IO/spill events without confusing cumulative work with peak disk footprint."""

import argparse
import json
from pathlib import Path

from generator.storage import write_json


def summarize_events(path: Path) -> dict:
    """Stream one Spark event log and total metrics from completed task attempts."""
    result={'application_ended':False,'task_attempts':0,'failed_attempts':0,'input_bytes_read':0,'output_bytes_written':0,
            'shuffle_bytes_written':0,'shuffle_bytes_read':0,'disk_bytes_spilled':0,'memory_bytes_spilled':0,
            'max_task_peak_execution_memory':0,'stages_completed':0}
    with path.open(encoding='utf-8') as source:
        while line:=source.readline(8*1024*1024+1):
            if len(line)>8*1024*1024:
                raise ValueError('event log record exceeds bounded reader limit')
            event=json.loads(line)
            if event['Event']=='SparkListenerApplicationEnd':
                result['application_ended']=True
            if event['Event']=='SparkListenerStageCompleted':
                result['stages_completed']+=1
            if event['Event']!='SparkListenerTaskEnd':
                continue
            metrics=event.get('Task Metrics',{})
            result['task_attempts']+=1
            result['failed_attempts']+=event.get('Task End Reason',{}).get('Reason')!='Success'
            result['input_bytes_read']+=metrics.get('Input Metrics',{}).get('Bytes Read',0)
            result['output_bytes_written']+=metrics.get('Output Metrics',{}).get('Bytes Written',0)
            result['shuffle_bytes_written']+=metrics.get('Shuffle Write Metrics',{}).get('Shuffle Bytes Written',0)
            reads=metrics.get('Shuffle Read Metrics',{})
            result['shuffle_bytes_read']+=reads.get('Remote Bytes Read',0)+reads.get('Local Bytes Read',0)
            result['disk_bytes_spilled']+=metrics.get('Disk Bytes Spilled',0)
            result['memory_bytes_spilled']+=metrics.get('Memory Bytes Spilled',0)
            result['max_task_peak_execution_memory']=max(result['max_task_peak_execution_memory'],metrics.get('Peak Execution Memory',0))
    result['event_log_bytes']=path.stat().st_size
    result['scope']='Cumulative task-attempt metrics across checks/ingest/queries; not peak simultaneous disk, host RSS, or net dataset bytes.'
    return result


def main():
    """Write a bounded event summary for a completed application's actual event-log file."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    write_json(args.report,summarize_events(args.log))


if __name__=='__main__':
    main()
