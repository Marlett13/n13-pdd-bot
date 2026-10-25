import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from tools import diagnose_vps


class DiagnosticPrivacyTests(unittest.TestCase):
    def test_exception_summary_keeps_only_fixed_types(self):
        text = 'Traceback (most recent call last):\nModuleNotFoundError: private-value\nValueError: private-value\nprivate-valueError: private-value'
        result = diagnose_vps.exception_summary(text)
        self.assertEqual(result, {'ValueError': 1, 'ModuleNotFoundError': 1})
        self.assertNotIn('private-value', json.dumps(result))

    def test_raw_logs_environment_and_probe_failure_are_not_printed(self):
        secret = 'synthetic-sensitive-marker'
        obj = {'Config': {'Env': [secret], 'Labels': {'com.docker.compose.project': 'n13-pdd',
                'com.docker.compose.service': 'pdd'}}, 'State': {'Status': 'running', 'Running': True},
                'RestartCount': 0, 'Image': 'sha256:test'}
        results = [SimpleNamespace(returncode=0, stdout=json.dumps([obj]), stderr=''),
                   SimpleNamespace(returncode=0, stdout='ERROR '+secret, stderr=''),
                   SimpleNamespace(returncode=0, stdout='timeout '+secret, stderr=''),
                   SimpleNamespace(returncode=1, stdout=secret, stderr=secret)]
        output = io.StringIO()
        with patch.object(diagnose_vps, 'run', side_effect=results), contextlib.redirect_stdout(output):
            self.assertEqual(diagnose_vps.main(), 1)
        self.assertNotIn(secret, output.getvalue())
        report = json.loads(output.getvalue())
        self.assertEqual(report['incident_log_counts']['error'], 1)
        self.assertEqual(report['runtime']['result'], 'failed_without_raw_output')

    def test_foreign_compose_container_is_rejected(self):
        obj = {'Config': {'Labels': {'com.docker.compose.project': 'foreign'}}}
        with patch.object(diagnose_vps, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps([obj]))):
            with self.assertRaisesRegex(SystemExit, 'scope mismatch'):
                diagnose_vps.main()
