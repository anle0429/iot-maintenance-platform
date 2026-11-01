"""Plot all native three-pair measurements with Matplotlib 3.10.7; never substitute the one-pair pilot."""
from hashlib import sha256
from pathlib import Path
import json
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
SOURCES = {}


def read(relative):
    """Bind exact native report bytes to the generated figure's source manifest."""
    body = (ROOT / relative).read_bytes()
    SOURCES[relative] = sha256(body).hexdigest()
    result = json.loads(body)
    if not result['passed'] or result['repetitions'] != 3:
        raise ValueError('Plot only the completed native three-pair report')
    return result


def main():
    """Show medians and every paired observation on independent, zero-origin axes."""
    corrections = read('docs/evidence/batch-benchmarks/final/corrections.json')
    skew = read('docs/evidence/batch-benchmarks/final/skew.json')
    panels = [('Schema reader', corrections, 'schema', ['inferred', 'declared'], ['Full inference', 'Declared union']),
              ('Dedup winner selection', corrections, 'dedup', ['window', 'aggregate'], ['Window', 'Aggregate candidate']),
              ('Factory-policy join', skew, 'skew', ['baseline', 'broadcast'], ['Sort merge', 'Broadcast'])]
    fig, axes = plt.subplots(1, 3, figsize=(16, 6.6))
    for axis, (title, report, experiment, strategies, labels) in zip(axes, panels):
        rows = [r for r in report['measurements'] if r.get('experiment', 'skew') == experiment]
        values = [[next(r['seconds'] for r in rows if r['strategy'] == method and r['repeat'] == repeat)
                   for repeat in range(3)] for method in strategies]
        if len(rows) != 6 or any(value <= 0 for column in values for value in column):
            raise ValueError('Missing or invalid paired measurements')
        medians = list(map(statistics.median, values))
        bars = axis.bar([0, 1], medians, width=.52, color=['#2563eb', '#d97706'], alpha=.78, zorder=2)
        for repeat in range(3):
            offset = (repeat - 1) * .055
            axis.plot([offset, 1 + offset], [values[0][repeat], values[1][repeat]],
                      marker='o', markersize=5, linewidth=1, color='#334155', alpha=.65, zorder=3)
        axis.bar_label(bars, labels=[f'{v:.3f} s' for v in medians], padding=7, fontsize=11, weight='bold')
        axis.set_xticks([0, 1], labels, fontsize=10)
        axis.set_ylim(0, max(max(column) for column in values) * 1.28)
        axis.set_title(title, fontsize=14, weight='bold', pad=24, color='#0f172a')
        axis.set_ylabel('Seconds per measured action', fontsize=10)
        axis.set_axisbelow(True)
        axis.grid(axis='y', color='#e2e8f0', linewidth=.7)
        axis.spines[['top', 'right']].set_visible(False)
        ratio = medians[0] / medians[1]
        axis.text(.5, 1.025, f'Baseline / candidate median: {ratio:.3f}x', transform=axis.transAxes,
                  ha='center', fontsize=10, color='#475569')
    fig.suptitle('Measured alternatives on fixed inputs', x=.055, y=.965, ha='left',
                 fontsize=23, weight='bold', color='#0f172a')
    fig.text(.055, .895, 'Three alternating pairs | Bars: medians | Connected dots: all observations | Independent y-axis scales',
             fontsize=11, color='#475569')
    fig.text(.055, .09, 'Schema: complete JSON read including inference | Dedup: warmed normalized input, shared conflict check excluded',
             fontsize=10, color='#475569')
    fig.text(.055, .055, 'Skew: 17.28M Silver rows, separate resources | Derived from native reports; not a runtime screenshot or end-to-end speedup',
             fontsize=10, color='#475569')
    fig.tight_layout(rect=(.035, .14, .99, .855), w_pad=3)
    fig.savefig(OUT / 'batch-benchmark-pairs.png', dpi=150, facecolor='white')
    plt.close(fig)
    (OUT / 'batch-benchmark-sources.json').write_text(json.dumps({'kind': 'plot-derived-from-completed-native-reports',
                'runtime_screenshot': False, 'repetitions': 3, 'source_sha256': SOURCES,
                'plot': 'batch-benchmark-pairs.png'}, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
