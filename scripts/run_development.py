"""Run the three public development anchors offline; no live/paid mode."""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
from angeleno.author import load_pack
from angeleno.comparison import load_config, run_development


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT/'examples/comparison/development.json')
    parser.add_argument('--output', type=Path, required=True, help='New receipt inside this checkout private/ or tmp/')
    args = parser.parse_args()
    dest = args.output.resolve()
    if not any(dest.is_relative_to(ROOT/d) for d in ('private', 'tmp')):
        parser.error('Output must remain in ignored private/ or tmp/')
    c, anchors, corpus = load_config(args.config)
    pack = load_pack(ROOT/'examples/demo/author-pack.json')
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open('x') as f:
        json.dump({'status':'started', 'mode':'dry_run'}, f)
    dest.chmod(0o600)
    try:
        report = await run_development(c, anchors, corpus, pack)
        report['observed_commit'] = subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()
        report['tracked_diff_present'] = bool(subprocess.check_output(['git','diff','--name-only','HEAD'], cwd=ROOT))
        report['source_kind'] = 'public_synthetic_character_not_Jo'
        report['runner_files'] = {str(p.relative_to(ROOT)): __import__('hashlib').sha256(p.read_bytes()).hexdigest()
            for p in (Path(__file__), ROOT/'src/angeleno/comparison.py')}
    except BaseException as exc:
        dest.write_text(json.dumps({'status':'aborted','error_type':type(exc).__name__,'mode':'dry_run'})+'\n')
        raise
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: report[k] for k in ('status','mode','model_calls','network_calls','ready_for_real_comparison')}))
    if report['status'] != 'completed':
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
