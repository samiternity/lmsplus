"""Exercise committed exports, refusal paths, and repeatable update patches."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('release_export', Path(__file__).resolve().parents[1] / 'tools/release/export_source.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        self.original_root = module.ROOT
        module.ROOT = self.repo
        self.addCleanup(setattr, module, 'ROOT', self.original_root)
        self.git('init', '-q')
        self.git('config', 'user.email', 'synthetic@example.invalid')
        self.git('config', 'user.name', 'Synthetic Test')
        self.names = sorted(module.REQUIRED | {module.MANIFEST})
        for name in self.names:
            p = self.repo / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('synthetic\n')
        self.save_manifest()
        self.commit()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.PIPE)

    def save_manifest(self):
        (self.repo / module.MANIFEST).write_text(json.dumps({'files': self.names}))

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'synthetic fixture')

    def test_only_committed_bytes_and_no_overwrite(self):
        (self.repo / 'README.md').write_text('uncommitted secret-like local data')
        (self.repo / '.env').write_text('local only')
        out = self.base / 'export'
        module.export('HEAD', out)
        self.assertEqual((out / 'source/README.md').read_text(), 'synthetic\n')
        self.assertFalse((out / 'source/.env').exists())
        with self.assertRaises(ValueError): module.export('HEAD', out)

    def test_forbidden_and_missing_manifest_paths_fail_atomically(self):
        for name in ['.env', 'missing.py']:
            self.names.append(name)
            if name == '.env': (self.repo / name).write_text('synthetic')
            self.save_manifest(); self.commit()
            out = self.base / name.replace('.', '_')
            with self.assertRaises(ValueError): module.export('HEAD', out)
            self.assertFalse(out.exists())
            self.names.remove(name)

    def test_previous_tampering_is_rejected(self):
        out = self.base / 'export'
        module.export('HEAD', out)
        (out / 'source/README.md').write_text('tampered')
        with self.assertRaises(ValueError): module.export('HEAD', self.base / 'next', out)

    def test_patch_is_applicable_and_hashes_recorded(self):
        first = self.base / 'first'
        module.export('HEAD', first)
        (self.repo / 'README.md').write_text('reviewed update\n')
        self.commit()
        second = self.base / 'second'
        sha, count = module.export('HEAD', second, first)
        metadata = json.loads((second / 'metadata.json').read_text())
        self.assertEqual(metadata['source_sha'], sha)
        self.assertEqual(len(metadata['sha256']), count)
        subprocess.run(['git', 'apply', '--check', str(second / 'changes.patch')], cwd=first / 'source', check=True, capture_output=True)

    def test_required_provider_cannot_be_omitted(self):
        self.names.remove('proxies/vercel/api/proxy.ts')
        self.save_manifest(); self.commit()
        with self.assertRaises(ValueError): module.export('HEAD', self.base / 'export')

    def test_symlinks_are_rejected(self):
        (self.repo / 'README.md').unlink()
        (self.repo / 'README.md').symlink_to('LICENSE')
        self.commit()
        with self.assertRaises(ValueError): module.export('HEAD', self.base / 'export')
