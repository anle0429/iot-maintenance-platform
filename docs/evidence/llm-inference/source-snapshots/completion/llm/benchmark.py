"""Measure real streamed inference, usage and tool calls through an OpenAI-compatible private endpoint."""
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from urllib.request import Request, urlopen
import argparse
import json
import time

WORKLOAD = [
    'Explain in three short sentences why duplicate sensor events can distort a rolling vibration average.',
    'Explain in three short sentences why an industrial pump can have a high vibration reading without a confirmed failure.',
    'Explain in three short sentences why a prediction score must identify the model and feature timestamp.',
]


def streamed_request(base_url, body):
    """Record first generated content separately from headers, full response time and actual reported token usage."""
    request = Request(base_url.rstrip('/') + '/v1/chat/completions', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    started = time.monotonic(); first = None; text = []; events = []; usage = None
    with urlopen(request, timeout=180) as response:
        status = response.status
        for raw in response:
            if not raw.startswith(b'data: '): continue
            value = raw[6:].strip()
            if value == b'[DONE]': break
            event = json.loads(value); events.append(event)
            if event.get('error'): raise ValueError('Model returned a streamed error')
            if event.get('usage'): usage = event['usage']
            for choice in event.get('choices', []):
                delta = choice.get('delta', {})
                if delta.get('content') or delta.get('tool_calls'):
                    if first is None: first = time.monotonic() - started
                if delta.get('content'): text.append(delta['content'])
    elapsed = time.monotonic() - started
    if status != 200 or first is None or not usage or usage.get('completion_tokens', 0) <= 0:
        raise ValueError('A successful generated stream with actual token usage is required')
    return {'status': status, 'ttft_seconds': first, 'elapsed_seconds': elapsed, 'usage': usage,
            'completion_tokens_per_second': usage['completion_tokens'] / elapsed, 'text': ''.join(text), 'events': events}


def benchmark(base_url, model, output, repeats=3):
    """Warm each fixed prompt once, then run the same uncached deterministic workload in declared order."""
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    body = {'model': model, 'messages': [], 'max_tokens': 64, 'temperature': 0, 'seed': 7,
            'cache_prompt': False, 'stream': True, 'stream_options': {'include_usage': True}}
    warmup = []; rows = []
    for prompt in WORKLOAD:
        warmup.append(streamed_request(base_url, dict(body, messages=[{'role': 'user', 'content': prompt}])))
    for repeat in range(repeats):
        for index, prompt in enumerate(WORKLOAD):
            row = streamed_request(base_url, dict(body, messages=[{'role': 'user', 'content': prompt}]))
            row.update({'repeat': repeat, 'prompt_index': index}); rows.append(row)
    (output / 'requests.json').write_text(json.dumps({'workload': WORKLOAD, 'parameters': body, 'warmup': warmup, 'measured': rows}, indent=2)+'\n')
    report = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'requests': len(rows), 'warmup_requests': len(warmup),
              'median_ttft_seconds': median(row['ttft_seconds'] for row in rows),
              'median_elapsed_seconds': median(row['elapsed_seconds'] for row in rows),
              'median_completion_tokens_per_second': median(row['completion_tokens_per_second'] for row in rows),
              'reported_completion_tokens': sum(row['usage']['completion_tokens'] for row in rows),
              'scope': 'Single client, one small CPU model, fixed uncached prompts. Not concurrent serving capacity or model-quality evaluation.'}
    (output / 'summary.json').write_text(json.dumps(report, indent=2)+'\n'); return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True); parser.add_argument('--model', default='iot-qwen2.5-1.5b')
    parser.add_argument('--output', type=Path, required=True); parser.add_argument('--repeats', type=int, choices=range(1, 6), default=3)
    args = parser.parse_args(); print(json.dumps(benchmark(args.base_url, args.model, args.output, args.repeats), indent=2))
