"""Selected synthetic file declarations and fake health only; no endpoints/audio."""
import json
import io
from contextlib import redirect_stderr
from unittest.mock import Mock, patch
import unittest
from worker.reference import expected_jo_reference, validate_jo_health, check_jo_endpoint, _NoRedirect
from worker.reference import ReferenceConfigurationError


def env():
    return {'VOXCPM_URL':'http://127.0.0.1:18080','VOXCPM_REFERENCE_PURPOSE':'jo',
            'VOXCPM_REFERENCE_ID':'selected-synthetic','VOXCPM_REFERENCE_SHA256':'a'*64}

def health():
    return {'ready':True,'model':'VoxCPM2','sample_rate':48000,'reference_purpose':'jo',
            'reference_asset_id':'selected-synthetic','reference_sha256':'a'*64,'reference_verified':True}

class ReferenceWorkerTests(unittest.TestCase):
    def test_trial_cli_names_the_missing_field_without_starting_resources(self):
        from scripts import run_trial
        output=io.StringIO()
        with patch.object(run_trial,'preflight',side_effect=ReferenceConfigurationError('Jo startup requires VOXCPM_REFERENCE_SHA256')), \
                patch('subprocess.Popen',side_effect=AssertionError('no resource')),redirect_stderr(output):
            self.assertEqual(run_trial.main(['--mode','product','--check-host']),1)
        self.assertIn('VOXCPM_REFERENCE_SHA256',output.getvalue())

    def test_missing_declaration_never_opens_endpoint(self):
        for key in ('VOXCPM_REFERENCE_PURPOSE','VOXCPM_REFERENCE_ID','VOXCPM_REFERENCE_SHA256'):
            value=env();value.pop(key)
            with patch('worker.reference.build_opener',side_effect=AssertionError('no network')):
                with self.assertRaises(ValueError):check_jo_endpoint(value)
        for purpose in ('diagnostic','',None):
            with self.assertRaises(ValueError):expected_jo_reference({**env(),'VOXCPM_REFERENCE_PURPOSE':purpose})

    def test_voice_jo_or_name_alone_cannot_match_selected_asset(self):
        good=health();validate_jo_health(env(),good)
        cases=[{'voice':'jo','reference':'jo-reference-no-bgm.wav','ready':True},
               {**good,'reference_purpose':'diagnostic'},{**good,'reference_sha256':'b'*64},
               {**good,'reference_asset_id':'other'},{**good,'reference_verified':False},
               {**good,'ready':False},{**good,'sample_rate':24000}]
        for value in cases:
            with self.assertRaises(ValueError):validate_jo_health(env(),value)

    def test_only_verified_health_and_no_redirect_are_accepted(self):
        response=Mock(status=200);response.read.return_value=json.dumps(health()).encode()
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        opener=Mock();opener.open.return_value=response
        with patch('worker.reference.build_opener',return_value=opener):check_jo_endpoint(env())
        opener.open.assert_called_once_with('http://127.0.0.1:18080/health',timeout=3)
        self.assertIsNone(_NoRedirect().redirect_request(None,None,None,None,None,None))
        response.read.return_value=b'invalid'
        with patch('worker.reference.build_opener',return_value=opener):
            with self.assertRaisesRegex(ValueError,'startup stopped'):check_jo_endpoint(env())
