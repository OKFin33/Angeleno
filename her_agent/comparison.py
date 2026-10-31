"""Serial development runner on the production core; offline, unscored only.

No credentials, provider construction, live switch, or held-out inputs. The
synthetic actors exercise plumbing, not Jo's quality. Request guards count every
attempt before dispatch, including failures; the explicit comparison_live entry additionally
uses the original account ledger and declared frozen development inputs.
"""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import itertools
import json
import shutil
from pathlib import Path
import tempfile
from unittest.mock import patch
from zoneinfo import ZoneInfo

from .author import assistant_control_pack
from .places import FixedClock
from .provider import TextDelta, ToolCalls
from .runtime import ConversationRuntime, open_store
from .web_lookup import PUBLIC_AREAS, PUBLIC_TOPICS, PUBLIC_PLACES, PublicQuery, WebLookup

ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class RunStopped(RuntimeError):
    pass


@contextmanager
def model_clock(now, zone):
    """Freeze conversational memory time, never account/billing time."""
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    with patch('her_agent.memory.datetime', FrozenDatetime), \
         patch.dict('os.environ', {'ANGELENO_TIMEZONE': zone}):
        yield


@contextmanager
def offline_clock(now, zone):
    """Process-local fixture clock/egress fence; this runner is strictly serial."""
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    def refuse(*args, **kwargs):
        raise RunStopped('offline_only')
    with patch('socket.socket.connect', side_effect=refuse), \
         patch('socket.socket.connect_ex', side_effect=refuse), \
         patch('socket.getaddrinfo', side_effect=refuse), \
         patch('her_agent.config.load_env', side_effect=refuse), \
         patch('her_agent.provider.configured_providers', side_effect=refuse), \
         patch('her_agent.memory.datetime', FrozenDatetime), \
         patch.dict('os.environ', {'ANGELENO_TIMEZONE': zone}):
        yield


class FixedPack:
    def __init__(self, pack, now, zone):
        self.pack, self.today = pack, now.astimezone(ZoneInfo(zone)).date().isoformat()

    def __getattr__(self, name):
        return getattr(self.pack, name)

    def messages(self, history, variant='guided', today=None, *, memory_context=None):
        if today is not None:
            raise RunStopped('caller_clock_override')
        return self.pack.messages(history, variant, today=self.today, memory_context=memory_context)


def load_config(path):
    path = Path(path)
    c = json.loads(path.read_text())
    if (c['format'] != 'angeleno-development/1' or c['mode'] != 'dry_run'
            or c['scope'] != 'development_only' or c['paid_execution'] is not False
            or c['holdout_included'] is not False or c['frozen_for_real_comparison'] is not False):
        raise ValueError('Only unfrozen, offline development is supported')
    for name, expected in c['files'].items():
        source = (path.parent / name).resolve()
        if not source.is_relative_to(path.parent.resolve()) or file_hash(source) != expected:
            raise ValueError('Development material hash/path mismatch')
    if not {c['anchors'], c['corpus']} <= c['files'].keys():
        raise ValueError('Both input files must be pinned')
    now = datetime.fromisoformat(c['clock_utc'])
    if now.tzinfo is None or c['timezone'] != 'America/Los_Angeles':
        raise ValueError('Explicit LA clock required')
    if (c['actor_requests_per_turn'], c['tools_per_turn']) != (4, 3):
        raise ValueError('Limits must match the shared production loop')
    for key in ('actor_requests_total', 'writer_requests_total', 'writer_requests_per_segment',
                'actor_max_tokens', 'writer_max_tokens'):
        if type(c[key]) is not int or c[key] < 1:
            raise ValueError('Positive integer guard required')
    if not 0 < c['turn_timeout_seconds'] <= 60 or not 0 < c['maintenance_timeout_seconds'] <= 60:
        raise ValueError('Bounded phase timeouts required')
    anchors = json.loads((path.parent / c['anchors']).read_text())['anchors']
    if len(anchors) != 3 or len({a['id'] for a in anchors}) != 3:
        raise ValueError('Three distinct development anchors required')
    for a in anchors:
        if not a['user_turns'] or any(not isinstance(t, str) or not t.strip() for t in a['user_turns']):
            raise ValueError('Nonempty fixed inputs required')
        for key in ('jo_output', 'assistant_output', 'owner_preference', 'owner_reason_and_turn_refs', 'boundary_results'):
            if a[key] is not None:
                raise ValueError('Inputs cannot contain outcomes')
        if len({b['id'] for b in a['clarifications']}) != len(a['clarifications']):
            raise ValueError('Distinct clarification IDs required')
    expected = {(a['id'], role) for a in anchors for role in ('character', 'assistant')}
    if len(c['order']) != 6 or {tuple(x) for x in c['order']} != expected:
        raise ValueError('Exactly one run of each arm/anchor required')
    corpus = json.loads((path.parent / c['corpus']).read_text())
    return c, anchors, corpus


class CorpusWeb:
    """Explicit same-universe mapping; empty and unmapped are different."""
    def __init__(self, corpus, case):
        self.corpus, self.case, self.calls = deepcopy(corpus), case, []
        if case not in corpus['cases']:
            raise ValueError('Unknown fixed corpus case')
        self.rows = {r['id']: r for r in corpus['records']}
        if len(self.rows) != len(corpus['records']):
            raise ValueError('Duplicate corpus record')
        self.mapping, words = {}, {}
        for row in corpus['queries']:
            key = row['area'], row['topic'], row['place_id']
            query = PublicQuery(*key)
            ids = row['record_ids']
            if key in self.mapping or not isinstance(ids, list) or any(x not in self.rows for x in ids):
                raise ValueError('Invalid corpus mapping')
            if 'uncovered' in row and type(row['uncovered']) is not bool:
                raise ValueError('Uncovered flag must be boolean')
            if row.get('uncovered') is True:
                if ids:
                    raise ValueError('Uncovered query cannot claim records')
                ids = None
            if query.text() in words and words[query.text()] != ids:
                raise ValueError('Equal queries must see equal records')
            self.mapping[key], words[query.text()] = ids, ids
        expected = set(itertools.product(PUBLIC_AREAS, PUBLIC_TOPICS, [None, *PUBLIC_PLACES]))
        if set(self.mapping) != expected:
            raise ValueError('All 24 public combinations require explicit mappings')
        for r in self.rows.values():
            if 'content_fixture' in r:
                source = (ROOT / r['content_fixture']).resolve()
                if not source.is_relative_to(ROOT/'data/places/fixtures') or file_hash(source) != r['content_fixture_sha256']:
                    raise ValueError('Synthetic fixture drift')

    async def search(self, query):
        if type(query) is not PublicQuery:
            raise RunStopped('nonpublic_query')
        query.__post_init__()
        key = query.area, query.topic, query.place_id
        if key not in self.mapping or self.mapping[key] is None:
            raise RunStopped('corpus_uncovered')
        self.calls.append({'kind': 'search', 'query': query.text()})
        ids = [] if self.case == 'empty' else self.mapping[key]
        return {'results': [{k: self.rows[i][k] for k in ('title', 'url', 'summary', 'date')} for i in ids[:5]]}

    async def fetch(self, url):
        row = next((r for r in self.rows.values() if r['url'] == url), None)
        if row is None:
            raise RunStopped('corpus_uncovered')
        self.calls.append({'kind': 'fetch', 'url': url})
        if self.case == 'conflicting_body':
            body = row['conflicting_body']
        elif 'content_fixture' in row:
            body = (ROOT / row['content_fixture']).read_text()
        else:
            body = row['content']
        if self.case == 'truncated_body':
            body += '\n合成冗长正文' * 800 + '\n合成例外：关闭。'
        return {'title': row['title'], 'content': body}


class RequestGuard:
    def __init__(self, config, changed=lambda: None):
        self.c, self.rows, self.changed = config, [], changed
        self.verify = lambda: None

    def reserve(self, anchor, role, kind, turn, max_turns, messages, tools):
        total = [r for r in self.rows if r['kind'] == kind]
        segment = [r for r in total if r['anchor'] == anchor and r['role'] == role]
        cap = max_turns * self.c['actor_requests_per_turn'] if kind == 'actor' else self.c['writer_requests_per_segment']
        if len(total) >= self.c[kind + '_requests_total'] or len(segment) >= cap:
            raise RunStopped('request_cap')
        if kind == 'actor' and sum(r['turn'] == turn for r in segment) >= self.c['actor_requests_per_turn']:
            raise RunStopped('turn_request_cap')
        row = dict(anchor=anchor, role=role, kind=kind, turn=turn, status='attempted',
                   input_sha256=digest(messages), input_chars=sum(len(m.get('content') or '') for m in messages),
                   tools=[t['function']['name'] for t in tools or []], output='', tool_calls=[])
        self.rows.append(row)
        self.changed()
        return row


class GuardedProvider:
    def __init__(self, provider, guard, anchor, role, kind, max_turns):
        self.provider, self.guard = provider, guard
        self.anchor, self.role, self.kind, self.max_turns = anchor, role, kind, max_turns
        self.model = guard.c['model']
        self.max_tokens = guard.c[kind + '_max_tokens']
        self.turn = 0
        if (provider.model, provider.max_tokens) != (self.model, self.max_tokens):
            raise ValueError('Provider model/output cap differs from common configuration')

    async def _stream(self, method, messages, tools=None):
        row = self.guard.reserve(self.anchor, self.role, self.kind, self.turn, self.max_turns, messages, tools)
        temperature = self.guard.c['temperature' if self.kind == 'actor' else 'writer_temperature']
        iterator = None
        try:
            kwargs = {'temperature': temperature}
            if method == 'chat_events':
                kwargs['tools'] = tools
            iterator = getattr(self.provider, method)(messages, **kwargs)
            async for piece in iterator:
                if isinstance(piece, str):
                    row['output'] += piece
                elif isinstance(piece, TextDelta):
                    row['output'] += piece.text
                elif isinstance(piece, ToolCalls):
                    row['tool_calls'].extend(deepcopy(piece.calls))
                self.guard.changed()
                yield piece
            self.guard.verify()
            row['status'] = 'completed'
        except BaseException as exc:
            row.update(status='failed_or_cancelled', error_type=type(exc).__name__)
            raise
        finally:
            if iterator is not None:
                await iterator.aclose()
            self.guard.changed()

    def chat_events(self, messages, *, tools=None, temperature=.7):
        return self._stream('chat_events', messages, tools)

    def chat(self, messages, *, temperature=.2):
        return self._stream('chat', messages)

    async def aclose(self):
        close = getattr(self.provider, 'aclose', None)
        if close:
            await close()


class ScriptedActor:
    """Same scripted exercise for both roles; never a generated model answer."""
    def __init__(self, config):
        self.model, self.max_tokens = config['model'], config['actor_max_tokens']

    async def chat_events(self, messages, *, tools=None, temperature=.7):
        user_index = max(i for i, m in enumerate(messages) if m['role'] == 'user')
        utterance = messages[user_index]['content']
        replies = [json.loads(m['content']) for m in messages[user_index+1:] if m['role'] == 'tool']
        if replies:
            result = replies[-1].get('results', [])
            if result:
                name, args = 'web_fetch', {'result_id': result[0]['result_id']}
            else:
                yield TextDelta('合成演练回执：本轮已结束；这不是模型回答或现实结论。')
                return
        elif '不用查' in utterance:
            name, args = 'place_cancel', {}
        elif any(x in utterance for x in ('Fisher', '下周', '11-26', '小展', '建筑和空间')):
            name, args = 'web_search', {'area': 'usc_exposition', 'topic': 'architecture'}
            if any(x in utterance for x in ('Fisher', '下周', '11-26')):
                args.update(topic='visit_hours', place_id='place.fisher')
                args['when'] = '2026-11-26' if '11-26' in utterance else '2026-11-07' if '下周' in utterance else '2026-10-31'
                args['supplement'] = 'Fisher' not in utterance
            elif '小展' in utterance:
                args['topic'] = 'small_art_exhibitions'
        else:
            name, args = 'memory_search', {'query': utterance[:128]}
        yield ToolCalls(({'id': 'dry-' + name, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}},))


class ScriptedWriter:
    def __init__(self, config):
        self.model, self.max_tokens = config['model'], config['writer_max_tokens']

    async def chat(self, messages, *, temperature=.2):
        yield '{"retire": [], "updates": []}'


async def maintenance_barrier(runtime, timeout, *, flush=False):
    async def wait():
        if flush:
            await runtime.maintainer.flush()
        elif runtime.maintainer.task is not None:
            await runtime.maintainer.task
        if runtime.maintainer.error:
            raise RunStopped('maintenance_' + runtime.maintainer.error)
        if flush and runtime.store.pending():
            raise RunStopped('unsettled_maintenance')
    await asyncio.wait_for(wait(), timeout)


async def _run_segments(c, anchors, corpus, pack, *, provider_factory=None, branch_selector=None,
                        real_models=False, checkpoint=lambda report: None, request_check=lambda: None):
    """Exercise full segments, preserving errors and stopping unknown branches.

    branch_selector is an offline test/operator seam: return None, a listed ID,
    or 'uncovered'. No model judge guesses a branch. Dynamic referent replies
    stop as uncovered until a frozen resolver exists, rather than improvising.
    """
    if c['mode'] != 'dry_run' or c['paid_execution'] is not False:
        raise ValueError('No live execution entry')
    c, anchors, corpus = deepcopy((c, anchors, corpus))
    now = datetime.fromisoformat(c['clock_utc'])
    packs = {'character': pack, 'assistant': assistant_control_pack(pack)}
    if pack.role != 'character':
        raise ValueError('Character source required')
    grounded = lambda p: tuple(d for d in p.documents if d.kind == 'grounding')
    if grounded(pack) != grounded(packs['assistant']):
        raise ValueError('Shared grounding differs')
    CorpusWeb(corpus, c['case'])
    changed = lambda: checkpoint(report)
    guard = RequestGuard(c, changed)
    guard.verify = request_check
    report = dict(mode='dry_run', evidence='scripted_mechanism_only', config_sha256=digest(c),
        anchors_sha256=digest(anchors), corpus_sha256=digest(corpus),
        roles={r: p.provenance() for r, p in packs.items()}, segments=[], requests=guard.rows,
        model_calls=0, network_calls=0, owner_calibration=None, holdout_included=False,
        ready_for_real_comparison=False)
    if real_models:
        report.update(mode='real_models_frozen_replay', evidence='unscored_development_outputs',
                      model_calls=None, network_calls=None)
    report['status'] = 'running'
    by_id = {a['id']: a for a in anchors}
    factory = provider_factory or (lambda kind, a, role: ScriptedActor(c) if kind == 'actor' else ScriptedWriter(c))
    clock = model_clock if real_models else offline_clock
    with clock(now, c['timezone']), tempfile.TemporaryDirectory(prefix='angeleno-development-') as directory:
        for aid, role in c['order']:
            anchor = by_id[aid]
            max_turns = len(anchor['user_turns']) + len(anchor['clarifications'])
            fixed = FixedPack(packs[role], now, c['timezone'])
            segment_dir = Path(directory) / (aid + '-' + role)
            segment_dir.mkdir()
            store = open_store(segment_dir, fixed, aid.lower(), create=True)
            if store.pending():
                raise RunStopped('state_not_fresh')
            actor = GuardedProvider(factory('actor', aid, role), guard, aid, role, 'actor', max_turns)
            writer = GuardedProvider(factory('writer', aid, role), guard, aid, role, 'writer', max_turns)
            web = CorpusWeb(corpus, c['case'])
            runtime = ConversationRuntime(fixed, actor, store=store, writer=writer,
                variant=c['variant'], memory_tools=True, lookup=WebLookup(web, FixedClock(now)))
            segment = dict(anchor=aid, role=role, status='running', transcript=[], web_calls=web.calls,
                owner_preference=None, state='fresh_per_arm_and_segment', clock=c['clock_utc'])
            report['segments'].append(segment)
            changed()
            queue = [(text, 'base') for text in anchor['user_turns']]
            used = set()
            try:
                while queue:
                    text, origin = queue.pop(0)
                    if len(segment['transcript']) >= max_turns:
                        raise RunStopped('user_turn_cap')
                    await maintenance_barrier(runtime, c['maintenance_timeout_seconds'])
                    actor.turn = writer.turn = len(segment['transcript']) + 1
                    turn = runtime.begin(text)
                    row = dict(user=text, origin=origin, output='', delivery='none', card=None,
                               user_created=store.pending()[-1]['created'])
                    segment['transcript'].append(row)
                    async with asyncio.timeout(c['turn_timeout_seconds']):
                        async for piece in runtime.stream(turn):
                            row['output'] += piece  # The transcript sink receives each piece.
                            changed()
                    runtime.delivered(turn, row['output'], complete=True)
                    row.update(delivery='complete', card=getattr(turn, 'source_card', None))
                    await maintenance_barrier(runtime, c['maintenance_timeout_seconds'])
                    changed()
                    decision = branch_selector(anchor, deepcopy(row), actor.turn) if branch_selector else None
                    row['branch_decision'] = decision
                    changed()
                    if decision is not None:
                        branch = next((b for b in anchor['clarifications'] if b['id'] == decision), None)
                        if branch is None or decision in used or 'reply' not in branch:
                            raise RunStopped('branch_uncovered')
                        used.add(decision)
                        queue.insert(0, (branch['reply'], 'clarification:' + decision))
                await maintenance_barrier(runtime, c['maintenance_timeout_seconds'], flush=True)
                segment.update(status='completed', pending_events=len(store.pending()),
                    memory_revision=store.revision, memory_watermark=store.watermark)
            except (Exception, asyncio.CancelledError) as exc:
                segment.update(status='stopped', error_type=type(exc).__name__,
                    reason=str(exc) if isinstance(exc, RunStopped) else 'see_failed_request_disposition')
                if runtime.active and not runtime.active.settled:
                    output = segment['transcript'][-1]['output'] if segment['transcript'] else ''
                    runtime.delivered(runtime.active, output, complete=False)
                    if segment['transcript']:
                        segment['transcript'][-1]['delivery'] = 'interrupted'
                if isinstance(exc, asyncio.CancelledError):
                    report['cancelled'] = True
            finally:
                try:
                    await runtime.close(flush=False)
                finally:
                    shutil.rmtree(segment_dir)
                    segment['state_disposition'] = 'removed_after_close'
            changed()
            if report.get('cancelled') or (real_models and segment['status'] != 'completed'):
                break
    report['status'] = 'completed' if (len(report['segments']) == len(c['order'])
        and all(s['status'] == 'completed' for s in report['segments'])) else 'incomplete'
    changed()
    return report


async def run_development(c, anchors, corpus, pack, *, provider_factory=None, branch_selector=None):
    """Public offline entry; cannot be switched to paid execution by config."""
    return await _run_segments(c, anchors, corpus, pack, provider_factory=provider_factory,
                               branch_selector=branch_selector)
