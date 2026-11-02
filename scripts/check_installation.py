#!/usr/bin/env python3
"""Build artifacts and smoke-test the installed app outside the checkout, offline."""
from pathlib import Path
import json
import os
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MEDIA = {'.wav', '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.aiff', '.mp4', '.mov', '.webm'}


def run(args, *, cwd, env, timeout=120, expected=0):
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)
    if result.returncode != expected:
        raise RuntimeError(f'{args[0]} exited {result.returncode}: {result.stderr[-4000:]}')
    return result.stdout


def check_names(names):
    for name in names:
        p = Path(name)
        assert not {'private', '.scratch', '.git', '.venv', '__pycache__'} & set(p.parts), name
        assert p.suffix.lower() not in MEDIA | {'.sqlite', '.sqlite3', '.db'}, name
        assert not p.name.startswith('.env') or p.name == '.env.example', name


def main():
    env = {k:v for k,v in os.environ.items() if not k.startswith(
        ('ANGELENO_', 'DEEPSEEK_', 'DASHSCOPE_', 'LIVEKIT_', 'VOXCPM_', 'PYTHONPATH', 'PYTHONHOME'))}
    with tempfile.TemporaryDirectory(prefix='angeleno-install-') as temporary:
        base = Path(temporary); dist = base/'dist'; work = base/'work'; work.mkdir()
        run(['uv', 'build', '--out-dir', str(dist)], cwd=ROOT, env=env)
        wheel, = dist.glob('*.whl'); source, = dist.glob('*.tar.gz')
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist(); check_names(names)
            required = ['angeleno/_resources/.env.example', 'angeleno/_resources/characters/jo/author-pack.json',
                        'angeleno/_resources/characters/jo/NOTICE', 'angeleno/_resources/web/index.html',
                        'angeleno/_resources/web/livekit-client.umd.js', 'angeleno/_resources/web/livekit-client.LICENSE',
                        'angeleno/_resources/data/places/sources.json', 'angeleno/_resources/NOTICE',
                        'angeleno/_resources/LICENSES/Apache-2.0.txt']
            assert all(p in names for p in required), 'missing runtime assets or notices'
        with tarfile.open(source) as archive:
            check_names(archive.getnames())
        venv = base/'venv'
        run(['uv', 'venv', '--python', sys.executable, str(venv)], cwd=work, env=env)
        python = venv/'bin/python'
        # Install only this wheel and reuse the already-synced dependency set.
        # Adding the dependency directory does not process its editable .pth
        # files, so the app must come from the wheel, not this checkout.
        run(['uv', 'pip', 'install', '--no-deps', '--python', str(python), str(wheel)], cwd=work, env=env)
        purelib = Path(run([str(python), '-c', 'import sysconfig;print(sysconfig.get_path("purelib"))'], cwd=work, env=env).strip())
        (purelib/'dependencies.pth').write_text(sysconfig.get_path('purelib')+'\n')
        template = run([str(venv/'bin/angeleno'), 'config'], cwd=work, env=env)
        assert 'ANGELENO_LANGUAGE=en' in template and 'DEEPSEEK_API_KEY=\n' in template
        empty = work/'empty.env'; empty.write_text('')
        preview = json.loads(run([str(venv/'bin/angeleno'), 'preview', '--env-file', str(empty),
                                  '--pack', 'characters/jo/author-pack.json'], cwd=work, env=env))
        assert preview['id']=='jo' and preview['source_count']==4 and preview['network_calls']==0
        check = json.loads(run([str(venv/'bin/angeleno-voice'), 'check', '--env-file', str(empty)],
                              cwd=work, env=env, expected=1))
        assert not check['ready'] and not any('Author configuration:' in e for e in check['errors'])
        run([str(venv/'bin/angeleno-trace'), '--help'], cwd=work, env=env)
        program = '''
import json, os, socket, sqlite3
from pathlib import Path
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden'))
import angeleno
from angeleno.paths import ROOT, resource_path
from angeleno.baseline import configured_pack
from angeleno.runtime import open_store
from angeleno.worker.audio_budget import ASRAllowance, asr_settings
from angeleno.worker.web_server import handler_for
from angeleno.provider import configured_providers
from angeleno.trace import Trace, Binding, load_rows
assert not str(angeleno.__file__).startswith(os.environ['SOURCE_CHECKOUT'])
assert ROOT==Path.cwd()
assert resource_path('web/index.html').is_file()
pack=configured_pack({})
state=Path.cwd().parent/'operator-state'; env={'ANGELENO_DATA_DIR':str(state), 'ANGELENO_ASR_MAX_SECONDS':'7'}
store=open_store(ROOT,pack,'install-test',data_dir=state,create=True,purpose='development')
store.append('installation','user','Synthetic installation evidence'); store.close()
store=open_store(ROOT,pack,'install-test',data_dir=state,purpose='development')
assert store.pending()[0]['text']=='Synthetic installation evidence';store.close()
allowance=ASRAllowance(*asr_settings(env));allowance.reserve(2)
again=ASRAllowance(*asr_settings(env))
with sqlite3.connect(again.path) as db: assert db.execute('SELECT reserved_seconds FROM asr_allowance').fetchone()[0]==2
env.update(ANGELENO_PAID_ENABLED='1', DEEPSEEK_API_KEY='synthetic-installation-key', DEEPSEEK_MODEL='deepseek-flash',
 ANGELENO_PRICE_MODEL='deepseek-flash', ANGELENO_BUDGET_CURRENCY='CNY', ANGELENO_PRICE_INPUT='2',
 ANGELENO_PRICE_CACHE_HIT='0.04', ANGELENO_PRICE_OUTPUT='8', ANGELENO_PRICE_SOURCE='synthetic-installation',
 ANGELENO_MIN_BALANCE='1', ANGELENO_BUDGET_CARRYOVER='0')
actor,writer=configured_providers(env)
import asyncio
asyncio.run(actor.aclose());asyncio.run(writer.aclose())
assert (state/'budget.sqlite3').exists()
trace_env={**env,'ANGELENO_TRACE':'1','ANGELENO_MEMORY_PURPOSE':'development', 'ANGELENO_DIAGNOSTICS_FILE':str(state/'turns.jsonl')}
sink=Trace(trace_env);assert sink.path and sink.write(Binding(sink,'install'),'turn_input',{'text':'synthetic'})
assert len(load_rows(sink.path,'development',env=trace_env))==1
assert not (Path.cwd()/'private').exists()
print(json.dumps({'installed':True,'character':pack.id,'external_state_reopened':True,'network_calls':0}))
'''
        result = json.loads(run([str(python), '-c', program], cwd=work,
                                env={**env, 'SOURCE_CHECKOUT':str(ROOT)}))
        assert result['installed'] and result['external_state_reopened']
        print('PASS installed app: wheel/sdist assets and notices, three commands, non-checkout cwd, external state/ledgers/trace; no private files or audio')


if __name__ == '__main__':
    main()
