"""Jo startup requires an explicit selected asset, not a voice label or name."""
import json
import re
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.parse import urlsplit


class ReferenceConfigurationError(ValueError):
    """Predefined nonsecret messages, safe for the startup CLI to show."""


def expected_jo_reference(env):
    if env.get('VOXCPM_REFERENCE_PURPOSE') != 'jo':
        raise ReferenceConfigurationError('Jo startup requires VOXCPM_REFERENCE_PURPOSE=jo; diagnostic reference is not Jo validation')
    asset_id, digest = env.get('VOXCPM_REFERENCE_ID', ''), env.get('VOXCPM_REFERENCE_SHA256', '')
    if not isinstance(asset_id, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}', asset_id):
        raise ReferenceConfigurationError('Jo startup requires a logical VOXCPM_REFERENCE_ID from the explicitly selected asset')
    if not isinstance(digest, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', digest):
        raise ReferenceConfigurationError('Jo startup requires VOXCPM_REFERENCE_SHA256; wait for selected asset access/verification')
    return asset_id, digest.lower()


def optimization_requested(env):
    value=env.get('VOXCPM_COMPILE','0')
    if value not in ('0','1'):
        raise ReferenceConfigurationError('VOXCPM_COMPILE must be0or1')
    return value=='1'


def validate_jo_health(env, health):
    asset_id, digest = expected_jo_reference(env)
    compiled=optimization_requested(env)
    if compiled and (not isinstance(health,dict) or health.get("optimization_active") is not True):
        raise ValueError("Compiled TTS candidate has not completed optimized warmup")
    if not compiled and isinstance(health,dict) and health.get("optimization_active") is True:
        raise ValueError("Expected eager TTS candidate, received compiled endpoint")
    if (not isinstance(health, dict) or health.get('ready') is not True
            or health.get('model') != 'VoxCPM2' or health.get('sample_rate') != 48000
            or health.get('reference_purpose') != 'jo' or health.get('reference_verified') is not True
            or health.get('reference_asset_id') != asset_id or health.get('reference_sha256') != digest):
        raise ValueError('TTS has not verified the explicitly selected Jo reference; voice=jo alone is insufficient')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def check_jo_endpoint(env):
    expected_jo_reference(env)  # Missing local selection fails before network.
    optimization_requested(env)
    url = env['VOXCPM_URL'].rstrip('/') + '/health'
    handlers = [_NoRedirect()]
    if urlsplit(url).hostname in {'localhost', '127.0.0.1', '::1'}:
        handlers.append(ProxyHandler({}))
    try:
        with build_opener(*handlers).open(url, timeout=3) as response:
            if response.status != 200:
                raise ValueError('TTS reference health unavailable')
            value = json.loads(response.read(65537))
    except Exception:
        raise ValueError('Cannot verify selected Jo reference at TTS health; startup stopped') from None
    validate_jo_health(env, value)
