"""Independent acceptance rules for the bounded, alternating retrieval comparison."""
from math import isfinite
from statistics import median


def summarize(rows):
    """Retain all ten attempts and calculate only the four measured complete pairs."""
    expected = [(pair, arm) for pair in range(5)
                for arm in (['substrate', 'plain'] if pair % 2 == 0 else ['plain', 'substrate'])]
    if len(rows) != 10 or [(r['pair'], r['arm']) for r in rows] != expected:
        raise ValueError('Require one warmup and four alternating pairs without omitted attempts')
    if (len({r['question_sha256'] for r in rows}) != 1 or len({r['task'] for r in rows}) != 10
            or any(r['warmup'] != (r['pair'] == 0) or r['accepted'] is not True
                   or r['http_status'] != 200 or not isfinite(r['elapsed_seconds'])
                   or r['elapsed_seconds'] <= 0 for r in rows)):
        raise ValueError('Require the same question, unique tasks and real accepted positive timings')
    medians = {arm: median(r['elapsed_seconds'] for r in rows if r['arm'] == arm and not r['warmup'])
               for arm in ['substrate', 'plain']}
    return {'passed': True, 'pairs': 4, 'warmup_requests': 2, 'accepted_requests': len(rows),
            'medians_seconds': medians, 'substrate_percent_change': 100 * (medians['substrate'] / medians['plain'] - 1),
            'all_attempts_retained': True}
