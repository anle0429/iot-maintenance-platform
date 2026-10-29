"""Render the recorded CPU/GPU latency distributions from the authenticated native request capsule."""
from pathlib import Path
from statistics import median
import json
import subprocess
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]


def draw():
    """Plot nine real observations and their median for each profile; keep unequal capped outputs explicit."""
    subprocess.run([sys.executable, '-S', str(ROOT / 'scripts/verify-gpu-inference.py')], check=True, capture_output=True)
    base = ROOT / 'docs/evidence/gpu-inference/benchmark/native'
    batches = {mode: json.loads((base / (mode + '-requests.json')).read_bytes()) for mode in ['cpu', 'gpu']}
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11, 'svg.fonttype': 'none',
                         'svg.hashsalt': 'iot-gpu-inference-v1',
                         'axes.spines.top': False, 'axes.spines.right': False})
    figure, axes = plt.subplots(1, 2, figsize=(12, 6.3))
    figure.patch.set_facecolor('#f5f8fa')
    colors = ['#58758e', '#0c8d82']
    for axis, key, title in zip(axes, ['ttft_seconds', 'elapsed_seconds'], ['First generated token', 'Full response']):
        values = [[row[key] for row in batches[mode]['measured']] for mode in ['cpu', 'gpu']]
        top = max(max(items) for items in values)
        for index, items in enumerate(values):
            value = median(items)
            axis.bar(index, value, width=0.58, color=colors[index], zorder=2)
            offsets = [-0.12 + n * 0.03 for n in range(9)]
            axis.scatter([index + x for x in offsets], items, s=26, facecolor='white', edgecolor=colors[index], linewidth=1.2, zorder=3)
            axis.text(index, max(items) + top * 0.06, f'{value:.3f} s', ha='center', weight='bold', color='#18364b')
        axis.set_title(title, loc='left', fontsize=13, weight='bold', pad=16)
        axis.set_xticks([0, 1], ['CPU profile\n0 GPU layers', 'V100 profile\n99 GPU layers requested'])
        axis.set_ylabel('Seconds')
        axis.set_ylim(0, top * 1.30)
        axis.grid(axis='y', color='#dfe7ed', zorder=0)
        axis.set_axisbelow(True)
        axis.spines['left'].set_color('#c3d1db'); axis.spines['bottom'].set_color('#c3d1db')
    figure.suptitle('Qwen2.5-7B: measured GPU offload latency', x=0.065, y=0.97, ha='left', fontsize=19, weight='bold', color='#18364b')
    figure.text(0.065, 0.895, 'Same host, CUDA image and model  |  9 measured requests per profile after 3 warm-ups', color='#4f6678')
    figure.text(0.065, 0.095, 'Bars: medians. Dots: individual requests. Seed 7, temperature 0, 64-token cap, zero reported cached tokens.', fontsize=9, color='#4f6678')
    totals = {mode: sum(row['usage']['completion_tokens'] for row in batch['measured']) for mode, batch in batches.items()}
    figure.text(0.065, 0.06, f'GPU visible in both profiles. Generated outputs differ: CPU {totals["cpu"]} tokens, V100 {totals["gpu"]}. Agent routing is unchanged.', fontsize=9, color='#4f6678')
    figure.subplots_adjust(left=0.075, right=0.97, bottom=0.24, top=0.79, wspace=0.28)
    target = ROOT / 'docs/images/gpu-inference-latency'
    figure.savefig(target.with_suffix('.png'), dpi=180, facecolor=figure.get_facecolor())
    svg = target.with_suffix('.svg')
    figure.savefig(svg, facecolor=figure.get_facecolor(), metadata={'Date': None})
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines()) + '\n', encoding='utf-8', newline='\n')
    plt.close(figure)


if __name__ == '__main__':
    draw()
