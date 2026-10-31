"""Explicit, unscored real-model entry; frozen local corpus, no live Web.

This module never runs on import. The CLI requires --run and an operator manifest;
permission for a private payload remains external to that manifest. A previous
approval rejection must be resolved before invoking it, not worked around here.
"""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess

from .author import Document, assistant_control_pack, load_pack
from .comparison import ROOT, RunStopped, _run_segments, digest, file_hash, load_config
from .config import load_env, resolve_path
from .provider import configured_providers


def revision():
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()


def load_manifest(path, config_path):
    """All materials and branch status validated before loading credentials."""
    path = Path(path).resolve()
    m = json.loads(path.read_text())
    if (m['format'] != 'angeleno-development-models/1' or m['status'] != 'frozen_development_inputs'
            or m['holdout_included'] is not False or m['branch_policy'] != 'stop_unclassified'
            or m['candidate_commit'] != revision() or m['config_sha256'] != file_hash(config_path)):
        raise ValueError('Explicit current development manifest required')
    for field in ('authorization_reference', 'corpus_acceptance', 'model_version_limitation'):
        if not isinstance(m[field], str) or not m[field].strip():
            raise ValueError('Record authorization, corpus scope and model-version limits')
    def pinned(row):
        p = (path.parent / row['path']).resolve()
        if file_hash(p) != row['sha256']:
            raise ValueError('Frozen material drift')
        return p
    pack = load_pack(pinned(m['character_pack']))
    for row in m['shared_grounding']:
        p = pinned(row)
        doc = Document(str(p), 'grounding', row['sha256'], p.read_text(), '')
        pack = replace(pack, documents=(*pack.documents, doc))
    roles = {'character': pack, 'assistant': assistant_control_pack(pack)}
    if {k: digest(p.provenance()) for k, p in roles.items()} != m['roles_sha256']:
        raise ValueError('Frozen role/material fingerprint mismatch')
    return m, pack


class Receipt:
    """Own a new private receipt; replace atomically, never resume/replay HTTP."""
    def __init__(self, path, mode="real_models_frozen_replay"):
        self.path = Path(path).absolute()
        resolved = self.path.resolve()
        if (self.path.is_symlink() or resolved != self.path
                or not any(resolved.is_relative_to(ROOT / d) for d in ('private', 'tmp'))):
            raise ValueError('Receipt must be a nonsymlink private/tmp path')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('x') as f:
            os.chmod(self.path, 0o600)
            json.dump({'status':'started','mode':mode},f)
        self.temp = self.path.with_name(self.path.name + '.partial')
        if self.temp.exists() or self.temp.is_symlink():
            raise ValueError('Receipt temporary path already exists')

    def save(self, report):
        # A crash can leave the exclusively-created .partial for inspection;
        # neither an old receipt nor an unknown model request is automatically retried.
        with self.temp.open('x') as f:
            os.chmod(self.temp, 0o600)
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(self.temp, self.path)


async def run_models(c, anchors, corpus, pack, env, manifest, *, output,
                     provider_builder=None, branch_selector=None):
    """Use original provider/ledger, never create an alternative spending history.

    Unknown branch is conservatively stopped. A later reviewed operator adapter
    may supply the existing selector seam; no model judge infers clarifications.
    """
    if manifest['model_alias'] != c['model']:
        raise ValueError('Model differs from frozen configuration')
    runtime_root = Path(manifest['runtime_root']).resolve()
    data = resolve_path(env.get('ANGELENO_DATA_DIR', 'private/state'), root=runtime_root)
    ledger_path = resolve_path(env.get('ANGELENO_BUDGET_LEDGER', str(data/'budget.sqlite3')), root=runtime_root)
    if ledger_path != Path(manifest['ledger_path']).resolve() or not ledger_path.is_file():
        raise ValueError('Existing original ledger required; no new ledger')
    receipt = Receipt(output)
    pairs, before, last = {}, None, None
    builder = provider_builder or (lambda: configured_providers(env, root=runtime_root))
    def factory(kind, aid, role):
        nonlocal before
        key = aid, role
        if key not in pairs:
            pair = builder()
            pairs[key] = pair  # close even if validation below fails
            actor, writer = pair
            if (actor.budget.ledger.path.resolve() != ledger_path
                    or actor.budget is not writer.budget
                    or (actor.model, writer.model) != (c['model'], c['model'])
                    or (actor.max_tokens, writer.max_tokens) != (c['actor_max_tokens'], c['writer_max_tokens'])):
                raise ValueError('Providers must share frozen model and original ledger')
            if before is None:
                before = actor.budget.ledger.summary()
                if before['unresolved_reserved_micros']:
                    raise RunStopped('existing_unresolved_usage')
        return pairs[key][0 if kind == 'actor' else 1]
    def save(report):
        nonlocal last
        report.update(candidate_commit=manifest['candidate_commit'], manifest_sha256=digest(manifest),
                      corpus_acceptance=manifest['corpus_acceptance'],
                      model_version_limitation=manifest['model_version_limitation'],
                      external_web_requests=0, ledger_before=before)
        if pairs:
            report['ledger_current'] = next(iter(pairs.values()))[0].budget.ledger.summary()
        receipt.save(report)
        last = report
    def request_check():
        if next(iter(pairs.values()))[0].budget.ledger.summary()['unresolved_reserved_micros']:
            raise RunStopped('unresolved_usage')
    try:
        # Validate actual provider settings before creating any conversation state.
        aid, role = c['order'][0]
        factory('actor', aid, role)
        return await _run_segments(c, anchors, corpus, pack, provider_factory=factory,
            branch_selector=branch_selector or (lambda *args: 'uncovered'),
            real_models=True, checkpoint=save, request_check=request_check)
    except BaseException as exc:
        report = last or {'segments': [], 'requests': [], 'mode': 'real_models_frozen_replay'}
        report.update(status='aborted', error_type=type(exc).__name__)
        save(report)
        raise
    finally:
        for pair in pairs.values():
            await asyncio.gather(*(p.aclose() for p in pair), return_exceptions=True)


async def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Explicit paid models; never live Web')
    parser.add_argument('--config', type=Path, default=ROOT/'examples/comparison/development.json')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.run:
        # Keep default dry-run behavior and its complete network/config fences.
        from .comparison import run_development
        c, anchors, corpus = load_config(args.config)
        receipt = Receipt(args.output, mode='dry_run')
        report = await run_development(c, anchors, corpus, load_pack(ROOT/'examples/demo/author-pack.json'))
        report['observed_commit'] = revision()
        receipt.save(report)
    else:
        if args.manifest is None or args.env_file is None:
            parser.error('--run requires explicit --manifest and --env-file')
        if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT, text=True).strip():
            raise ValueError('Tracked candidate is dirty')
        c, anchors, corpus = load_config(args.config)
        m, pack = load_manifest(args.manifest, args.config)
        # Necessary existing runtime consumption only; never print/export keys.
        env = load_env(environ={}, env_file=args.env_file, root=Path(m['runtime_root']).resolve())
        env['ANGELENO_PAID_ENABLED'] = '1' # explicit --run, process only
        report = await run_models(c, anchors, corpus, pack, env, m, output=args.output)
    print(json.dumps({'status': report['status'], 'mode': report['mode'], 'owner_calibration': None}))
    return 0 if report['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
