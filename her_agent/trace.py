"""Explicit local text trace on the existing diagnostics stream; never telemetry.

The memory and billing databases remain authoritative. This is an optional
observation, never a source for actor prompts. No audio or HTTP headers enter it.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "angeleno-trace/1"
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_EVENT_BYTES = 256 * 1024
_CURRENT = ContextVar("angeleno_trace", default=None)
_LINKS = {}  # bounded transport ID -> immutable call/turn binding
_SECRET_KEYS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "api_key", "apikey", "password", "secret", "access_token", "refresh_token"}
_AUDIO_KEYS = {"audio", "pcm", "samples", "reference_audio", "audio_data", "audio_content"}


def trace_path(env, root=ROOT):
    if env.get("ANGELENO_TRACE", "0") not in {"0", "1"} or env.get("ANGELENO_TRACE_REASONING", "0") not in {"0", "1"}:
        raise ValueError("Trace switches must be0or1")
    if env.get("ANGELENO_TRACE", "0") != "1":
        return None
    value = env.get("ANGELENO_DIAGNOSTICS_FILE", "")
    if not value:
        raise ValueError("Trace requires ANGELENO_DIAGNOSTICS_FILE inside private/")
    path = Path(value)
    if not path.is_absolute(): path = Path(root) / path
    private = Path(root).resolve() / "private"
    if private.is_symlink() or not path.resolve().is_relative_to(private):
        raise ValueError("Trace must remain in private/")
    for candidate in (path, *path.parents):
        if candidate.is_symlink():raise ValueError("Trace may not follow symlinks inside private/")
        if candidate.resolve()==private:break
    return path.resolve()


@dataclass(frozen=True)
class Binding:
    sink: object
    session: str
    turn: str | None = None
    content: bool = True
    transport: str | None = None


@contextmanager
def activate(binding):
    token = _CURRENT.set(binding)
    try:
        yield
    finally:
        try: _CURRENT.reset(token)
        except (ValueError, RuntimeError): pass  # capture must never break cancellation/SDK cleanup


def current(): return _CURRENT.get()


def register(transport, binding):
    if binding is not None:
        _LINKS[transport] = replace(binding, transport=transport)
        while len(_LINKS) > 256: _LINKS.pop(next(iter(_LINKS)))


def forget(session):
    for key in list(_LINKS):
        if _LINKS[key].session==session:del _LINKS[key]


def linked(transport): return _LINKS.get(transport)


def record(event, *, binding=None, **data):
    try:
        binding = binding or current()
        return bool(binding and binding.sink.write(binding, event, data))
    except Exception:
        return False


def timing(event, metadata):
    request=metadata.get("request_id")
    binding = linked(request)
    if binding is None:
        if isinstance(request,str) and request.startswith('her_'):return
        binding=current()
    if binding is not None:
        record("timing", binding=binding, kind=event, observer="operator_metadata", **metadata)


def tool(event, call, *, reason=None, status=None):
    try:
        function=call.get('function',{}) if isinstance(call,dict) else {}
        record(event, operation=function.get('name') if isinstance(function,dict) else None,
               call_id=call.get('id') if isinstance(call,dict) else None,
               arguments=function.get('arguments') if isinstance(function,dict) else None,
               reason=reason, status=status)
    except Exception:pass


class Trace:
    def __init__(self, env=None, *, root=ROOT, purpose=None):
        self.root = Path(root).resolve()
        self._claimed_inode = None
        self.env = dict(os.environ if env is None else env)
        self.purpose = purpose or self.env.get("ANGELENO_MEMORY_PURPOSE")
        self.path = None
        self.error = None
        self.capture_reasoning = self.env.get("ANGELENO_TRACE_REASONING", "0") == "1"
        self.secrets = tuple(sorted({v for k,v in self.env.items() if k in {
            "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"}
            and isinstance(v,str) and v}, key=len, reverse=True))
        try:
            self.path = trace_path(self.env, root)
            if self.path and self.purpose not in {"development", "real_user"}:
                raise ValueError("Trace requires explicit real_user or development purpose")
            declared = self.env.get("ANGELENO_MEMORY_PURPOSE")
            if self.path and declared and declared != self.purpose:
                raise ValueError("Trace purpose differs from store purpose")
        except Exception as exc:
            self.path = None; self.error = type(exc).__name__
        self.version = None
        self.dirty = None
        if self.path:
            try:
                self.version = subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True,stderr=subprocess.DEVNULL,timeout=1).strip()
                self.dirty = bool(subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True,stderr=subprocess.DEVNULL,timeout=1).strip())
            except Exception: self.version = "unknown"

    def clean(self, value, depth=0):
        if depth > 30: return {"capture_truncated": "nesting_limit"}
        if isinstance(value, dict):
            return {str(k): ("[omitted]" if str(k).casefold() in _SECRET_KEYS | _AUDIO_KEYS else self.clean(v,depth+1)) for k,v in value.items()}
        if isinstance(value,(list,tuple)):
            values = [self.clean(v,depth+1) for v in value[:2048]]
            return values + ([{"capture_truncated_items":len(value)-2048}] if len(value)>2048 else [])
        if isinstance(value,str):
            for secret in self.secrets: value=value.replace(secret,'[redacted]')
            value=re.sub(r'(?i)\b(?:sk|api)-[a-z0-9_-]{8,}', '[redacted]', value)
            value=re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----', '[redacted private key]', value)
            value=re.sub(r'(?i)\b(?:password|api key|access token)\s+is\s+[^\r\n]+', '[redacted credential]', value)
            value=re.sub(r'(?im)\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:[^\r\n]*', '[redacted header]', value)
            value=re.sub(r"""(?i)(["']?(?:api[_-]?key|password|access_token|refresh_token|secret)["']?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;]+)""", r'\1[redacted]', value)
            if len(value)>64000: return {"text":value[:64000],"capture_truncated_chars":len(value)-64000}
            return value
        if value is None or type(value) in (int,float,bool): return value
        return "[unsupported value]"  # never repr(bytes), audio frames, clients or exceptions

    def write(self, binding, event, data):
        if self.path is None: return False
        fd = None
        try:
            if trace_path(self.env,self.root) != self.path: return False
            if self.path.is_symlink() or any(p.is_symlink() for p in self.path.parents): return False
            fd=os.open(self.path,os.O_APPEND|os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:return False
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if info.st_size>MAX_FILE_BYTES:return False
            # Each file has one purpose; never merge evaluation and real chats.
            inode=(info.st_dev,info.st_ino)
            if self._claimed_inode!=inode:
                os.lseek(fd,0,os.SEEK_SET)
                for raw in os.read(fd,info.st_size).splitlines():
                    try: previous=json.loads(raw)
                    except ValueError:continue
                    if previous.get('schema')==SCHEMA and previous.get('purpose')!=self.purpose:return False
                self._claimed_inode=inode
            cleaned=self.clean(data) if binding.content else {"content_omitted":"temporary_no_save"}
            row={"schema":SCHEMA,"purpose":self.purpose,"session":binding.session,"turn":binding.turn,
                 "transport_request":binding.transport,"event":event,"monotonic_seconds":time.monotonic(),
                 "created":datetime.now(timezone.utc).isoformat(),"capture_redacted":cleaned!=data,"data":cleaned}
            raw=(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n').encode()
            if len(raw)>MAX_EVENT_BYTES:
                row['data']={"capture_truncated":True,"original_redacted_bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}
                raw=(json.dumps(row,ensure_ascii=False)+'\n').encode()
            if info.st_size+len(raw)>MAX_FILE_BYTES:
                row.update(event='capture_limit',data={"capture_truncated":True,"reason":"file_limit_32MiB"})
                raw=(json.dumps(row)+'\n').encode()
                if info.st_size+len(raw)>MAX_FILE_BYTES+2048:return False
            return os.write(fd,raw)==len(raw)
        except Exception:
            return False
        finally:
            if fd is not None:os.close(fd)


def load_rows(path, purpose, *, root=ROOT):
    path=trace_path({'ANGELENO_TRACE':'1','ANGELENO_DIAGNOSTICS_FILE':str(path)},root)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1
                or info.st_size>MAX_FILE_BYTES+2048 or stat.S_IMODE(info.st_mode)!=0o600):
            raise ValueError('Unsafe or oversized trace')
        text=os.read(fd,info.st_size).decode('utf-8')
    finally:os.close(fd)
    rows=[]
    for line in text.splitlines():
        try: row=json.loads(line)
        except ValueError:continue
        if not isinstance(row,dict) or row.get('schema')!=SCHEMA:continue
        if row.get('purpose')!=purpose:raise ValueError('Trace purpose mismatch; no content shown')
        if not isinstance(row.get('data'),dict) or not isinstance(row.get('session'),str):continue
        rows.append(row)
    return rows


def report(rows, *, session=None, turn=None, expand=()):
    selected=[r for r in rows if (session is None or r['session']==session) and (turn is None or r['turn']==turn)]
    result=[]
    for sid in dict.fromkeys(r['session'] for r in selected):
        ss=[r for r in selected if r['session']==sid]
        entry={'session':sid,'purpose':ss[0]['purpose'],'configuration':[r['data'] for r in ss if r['event']=='session'],'turns':[]}
        for tid in dict.fromkeys(r['turn'] for r in ss if r['turn'] is not None):
            events=[r for r in ss if r['turn']==tid]
            item={'turn':tid,'events':len(events),'input':None,'generated':None,'delivery':None,'models':[],'tools':[],'memory':[],'cards':[],'timing':[],'timing_counts':{}}
            for r in events:
                e,d=r['event'],r['data']
                if e=='turn_input':item['input']=d
                elif e=='generation_end':item['generated']=d
                elif e=='delivery':item['delivery']=d
                elif e=='model_end':item['models'].append({k:v for k,v in d.items() if k not in {'output','reasoning_content','tool_calls'}})
                elif e.startswith('tool_'):item['tools'].append({'event':e,**{k:v for k,v in d.items() if k in {'operation','call_id','reason','status'}}})
                elif e.startswith('card_'):item['cards'].append({'event':e,**{k:v for k,v in d.items() if k!='card'},'card_status':d.get('card',{}).get('status')})
                elif e.startswith('memory_'):item['memory'].append({'event':e,**{k:v for k,v in d.items() if k!='proposal'}})
                elif e=='timing':
                    kind=d.get('kind','unknown');item['timing_counts'][kind]=item['timing_counts'].get(kind,0)+1
                    if kind not in {'audio_http_chunk','audio_push'} and len(item['timing'])<50:item['timing'].append(d)
            for field in ('input','generated','delivery'):
                if isinstance(item[field],dict) and isinstance(item[field].get('text'),str) and len(item[field]['text'])>8000:
                    item[field]={**item[field], 'text':item[field]['text'][:8000], 'viewer_preview_truncated':True}
            item['capture_incomplete'] = item['generated'] is None or item['delivery'] is None
            if expand:
                wanted={'input':{'model_request','session'},'tools':{r['event'] for r in events if r['event'].startswith(('tool_','card_'))},
                        'memory':{r['event'] for r in events if r['event'].startswith('memory_')},'timing':{'timing'},'reasoning':{'model_end'}}
                kinds=set().union(*(wanted.get(x,set()) for x in expand))
                item['details']=[r for r in events if 'all' in expand or r['event'] in kinds]
            entry['turns'].append(item)
        result.append(entry)
    return result


def main():
    parser=argparse.ArgumentParser(description='Read private per-turn traces; never reads memory databases or opens services.')
    parser.add_argument('--file',type=Path,required=True);parser.add_argument('--purpose',choices=('real_user','development'),required=True)
    parser.add_argument('--session');parser.add_argument('--turn');parser.add_argument('--expand',nargs='*',choices=('input','tools','memory','timing','reasoning','all'),default=[])
    args=parser.parse_args()
    try:
        rows=load_rows(args.file,args.purpose);print(json.dumps(report(rows,session=args.session,turn=args.turn,expand=args.expand),ensure_ascii=False,indent=2))
    except (OSError,ValueError):parser.exit(2,'Trace unavailable, unsafe path/mode or purpose mismatch; no content shown.\n')


if __name__=='__main__':main()
