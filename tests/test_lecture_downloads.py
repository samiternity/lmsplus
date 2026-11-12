"""Partition and cost-boundary tests without app startup or upstream file requests."""
import ast
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch
from flask import Flask, jsonify, request

spec = importlib.util.spec_from_file_location('lecture_downloads', 'app/utils/lecture_downloads.py')
manifest_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest_module)


def file_rows(count, per_week=1):
    return [({'week_no': i // per_week + 1}, f'https://lms.bahria.edu.pk/file{i}', 'lecture.pdf', 'PDF') for i in range(count)]


class ManifestTests(unittest.TestCase):
    def test_counts_and_exact_coverage(self):
        for count in (1, 16, 20, 21, 32, 48):
            with self.subTest(count=count):
                files, digest = manifest_module.lecture_manifest(file_rows(count))
                parts = manifest_module.lecture_parts(files)
                self.assertEqual([file['index'] for part in parts for file in part], list(range(count)))
                self.assertTrue(all(len(part) <= 20 for part in parts))
                self.assertEqual(len(parts), (count + 19) // 20)
                self.assertEqual(digest, manifest_module.lecture_manifest(file_rows(count))[1])

    def test_weeks_stay_together_and_huge_week_splits(self):
        files, _ = manifest_module.lecture_manifest(file_rows(21, 3))
        self.assertEqual([len(part) for part in manifest_module.lecture_parts(files)], [18, 3])
        files, _ = manifest_module.lecture_manifest(file_rows(48, 48))
        self.assertEqual([len(part) for part in manifest_module.lecture_parts(files)], [20, 20, 8])
        self.assertEqual(len({file['name'] for file in files}), 48)

    def test_smaller_parts_and_missing_weeks(self):
        rows = file_rows(32, 2)
        rows[0][0]['week_no'] = None
        files, _ = manifest_module.lecture_manifest(rows)
        parts = manifest_module.lecture_parts(files, 10, 20, 32)
        self.assertEqual([file['index'] for part in parts for file in part], list(range(20, 32)))
        self.assertIsNone(files[-1]['week'])
        with self.assertRaises(ValueError):
            manifest_module.lecture_parts([], 20)
        for values in ((21, 0, 10), (0, 0, 10), (20, -1, 10), (20, 0, 100)):
            with self.assertRaises(ValueError):
                manifest_module.lecture_parts(files, *values)


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.notes = [dict(week_no=i + 1, lecture_files=[f'https://lms.bahria.edu.pk/{i}'],
                           lecture_files_info=[{'name':'lecture.pdf','type':'PDF'}]) for i in range(32)]
        self.tokens = []
        token_module = ModuleType('app.utils.download_token')
        def generate(files, cookies):
            self.tokens.append(files)
            return 'encrypted-token'
        token_module.generate_download_token = generate
        scraper_module = ModuleType('app.services.lms_scraper_service')
        # The endpoint receives cookies only. No .get() exists, so file-byte fetches fail this suite.
        scraper_module.LMSScraperService = lambda: SimpleNamespace(_create_authenticated_session=lambda account:
            SimpleNamespace(cookies=SimpleNamespace(get_dict=lambda:{'session':'secret'})))
        self.modules = patch.dict(sys.modules, {'app.utils.lecture_downloads':manifest_module,
            'app.utils.download_token':token_module, 'app.services.lms_scraper_service':scraper_module})
        self.modules.start()
        tree = ast.parse(Path('app/routes/lms.py').read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in
                     ('_generate_cloudrun_memory_zip','download_lecture_notes_zip')]
        for function in functions:
            function.decorator_list = []
        namespace = dict(jsonify=jsonify, request=request, os=os, logger=logging.getLogger(__name__),
            get_active_account_id=lambda:'account', re=__import__('re'),
            file_worker_origin=lambda:'https://proxy.example.test',
            lms_service=SimpleNamespace(get_lecture_notes_for_course=lambda *a, **kw:self.notes))
        exec(compile(ast.Module(body=functions,type_ignores=[]), '<lecture endpoint>', 'exec'), namespace)
        self.endpoint = namespace['download_lecture_notes_zip']
        self.addCleanup(self.modules.stop)

    def call(self, query='', enabled=True):
        with patch.dict(os.environ, {'ZIP_NATIVE_DOWNLOADS':str(enabled).lower()}), self.app.test_request_context('/?course=CS101&'+query):
            result = self.endpoint()
            response = self.app.make_response(result)
            return response.status_code, response.get_json()

    def test_32_files_only_worker_instructions(self):
        code, data = self.call()
        self.assertEqual(code, 200)
        self.assertEqual([part['count'] for part in data['parts']], [20,12])
        for part in data['parts']:
            self.assertEqual(part['method'], 'POST')
            self.assertNotIn('fallback_url',part)
            self.assertTrue(part['download_url'].endswith('/zip'))
            self.assertTrue(all(file['download_url'].endswith('/download') for file in part['files']))
        self.assertEqual(data['parts'][0]['filename'], 'CS101_Lectures_Part_01_of_02.zip')

    def test_rollback_and_legacy_stream_never_generate_archive(self):
        self.assertEqual(self.call(enabled=False)[0],503)
        self.assertEqual(self.call('stream=1')[0],410)
        self.assertEqual(self.call('stream=1',enabled=False)[0],410)
        self.assertEqual(self.tokens,[])

    def test_refresh_manifest_and_smaller_selection(self):
        _, data = self.call()
        code, split = self.call(f"manifest={data['manifest']}&start=20&end=32&part_size=6")
        self.assertEqual(code,200)
        self.assertEqual([part['count'] for part in split['parts']],[6,6])
        self.notes[0]['lecture_files'][0] += 'changed'
        code, error = self.call('manifest='+data['manifest'])
        self.assertEqual(code,409)
        self.assertEqual(error['code'],'manifest_changed')

    def test_individual_lecture_link_cannot_fall_back_to_cloud_run(self):
        tree = ast.parse(Path('app/routes/lms.py').read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'download_file')
        function.decorator_list = []
        namespace = dict(request=request, jsonify=jsonify, logger=logging.getLogger(__name__),
                         get_active_account_id=lambda:'account',
                         file_worker_origin=lambda:'https://proxy.example.test')
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<individual endpoint>', 'exec'), namespace)
        # An overlong token formerly invoked the server-side stream fallback.
        sys.modules['app.utils.download_token'].generate_download_token = lambda *args: 'x' * 2000
        with self.app.test_request_context('/?lecture=1'):
            result = self.app.make_response(namespace['download_file']('https://lms.bahria.edu.pk/file'))
        self.assertEqual(result.status_code, 502)
        self.assertIn('proxy unavailable', result.get_json()['error'])

    def test_invalid_and_empty_requests(self):
        self.assertEqual(self.call('part_size=21')[0],400)
        self.assertEqual(self.call('start=-1')[0],400)
        self.notes.clear()
        self.assertEqual(self.call()[0],404)


if __name__ == '__main__':
    unittest.main()
