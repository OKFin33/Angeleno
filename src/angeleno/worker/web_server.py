# SPDX-License-Identifier: Apache-2.0
# Modified from talk-to-fengge. Local page and dispatch only; no external health probes. See NOTICE.
"""Local page and LiveKit dispatch service; no external health probes.

Based on talk-to-fengge's web server (Apache-2.0); see NOTICE.
"""
from __future__ import annotations

import asyncio
from http.server import HTTPServer, SimpleHTTPRequestHandler
import json
from pathlib import Path
import secrets

from angeleno.config import load_env
from angeleno.baseline import configured_pack
from angeleno.language import configured_language

from angeleno.paths import ROOT, resource_path


async def create_room_and_token(env):
    from livekit import api
    from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest
    from livekit.protocol.room import CreateRoomRequest
    if env.get('ANGELENO_PAID_ENABLED', '0') != '1':
        raise ValueError('Calls are disabled')
    room = 'angeleno-jo-' + secrets.token_hex(8)
    identity = 'user-' + secrets.token_hex(8)
    url = env['LIVEKIT_URL']
    host = url.replace('ws://', 'http://', 1).replace('wss://', 'https://', 1)
    async with api.LiveKitAPI(host, env['LIVEKIT_API_KEY'], env['LIVEKIT_API_SECRET']) as client:
        await client.room.create_room(CreateRoomRequest(name=room))
        await client.agent_dispatch.create_dispatch(CreateAgentDispatchRequest(
            room=room, agent_name=env.get('ANGELENO_AGENT_NAME', 'angeleno-jo'),
            metadata=json.dumps({'user_identity': identity})))
    token = (api.AccessToken(env['LIVEKIT_API_KEY'], env['LIVEKIT_API_SECRET'])
             .with_identity(identity).with_name('Caller' if configured_language(env) == 'en' else '试用者')
             .with_grants(api.VideoGrants(room_join=True, room=room)).to_jwt())
    return {'token': token, 'room': room, 'identity': identity,
            'livekit_url': env.get('LIVEKIT_PUBLIC_URL') or url}


def handler_for(env, *, lifecycle=None):
    port = int(env.get('ANGELENO_WEB_PORT', '8767'))
    origins = {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}
    origins.update(x.strip() for x in env.get('ANGELENO_WEB_ALLOWED_ORIGINS', '').split(',') if x.strip())

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(resource_path("web")), **kwargs)

        def do_GET(self):
            if self.path == '/trial-status':
                self.send_json(200, lifecycle.snapshot() if lifecycle is not None else {'enabled':False})
                return
            if self.path == '/healthz':
                # This is local configuration readiness, not a remote liveness
                # claim. Remote connections occur only when the user dials.
                from angeleno.launcher import check_configuration
                ready = not check_configuration(env, starting=True) and (lifecycle is None or lifecycle.can_join())
                try:
                    pack_id = configured_pack(env).id
                    label = ({'jo': 'Jo', 'demo-local': 'Synthetic demo (Lin)'}.get(pack_id, 'Custom character')
                             if configured_language(env) == 'en' else {'jo': 'Jo', 'demo-local': '合成示例（林）'}.get(pack_id, '自定义人物'))
                except (ValueError, OSError, KeyError):
                    label = 'Character not configured' if configured_language(env) == 'en' else '人物配置未就绪'
                self.send_json(200, {'ready': ready, 'scope': 'local-configuration', 'persona_label': label, 'language': configured_language(env),
                                     **({'lifecycle_enabled':True} if lifecycle is not None else {})})
                return
            # Never expose files outside the static page directory.
            if self.path.split('?', 1)[0] not in {'/', '/index.html', '/livekit-client.umd.js', '/livekit-client.LICENSE'}:
                self.send_error(404)
                return
            super().do_GET()

        def do_POST(self):
            if self.path != '/token':
                self.send_json(404, {'error': 'not found'})
                return
            origin = self.headers.get('Origin')
            if origin not in origins:
                self.send_json(403, {'error': 'origin not allowed'})
                return
            if lifecycle is not None and not lifecycle.can_join():
                self.send_json(503, {'error':'trial is preparing or closed'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 <= size <= 4096:
                    self.send_json(413, {'error': 'request too large'})
                    return
                json.loads(self.rfile.read(size) or b'{}')
            except (ValueError, json.JSONDecodeError):
                self.send_json(400, {'error': 'invalid json'})
                return
            try:
                result = asyncio.run(asyncio.wait_for(create_room_and_token(env), timeout=4))
            except Exception:
                self.send_json(503, {'error': 'call service unavailable'})
                return
            if lifecycle is not None:
                try:lifecycle.register(result['room'],result['identity'])
                except ValueError:
                    self.send_json(409, {'error':'trial is ending'})
                    return
            # The configured client URL also applies behind a loopback proxy.
            # Request Host / forwarded headers do not select media endpoints.
            self.send_json(200, result)

        def do_OPTIONS(self):
            self.send_response(204)
            self.cors_headers()
            self.end_headers()

        def cors_headers(self):
            origin = self.headers.get('Origin')
            if origin in origins:
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')

        def send_json(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    return Handler


def main():
    env = load_env()
    server = HTTPServer(('127.0.0.1', int(env.get('ANGELENO_WEB_PORT', '8767'))), handler_for(env))
    print(f"Angeleno page: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
