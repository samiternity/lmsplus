#!/usr/bin/env python3
"""Export explicitly reviewed committed blobs; keep metadata outside public source."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import shutil

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = 'tools/release/public-files.json'
REQUIRED = {'README.md', 'LICENSE', 'SECURITY.md', 'CONTRIBUTING.md', '.env.example', 'cf_worker_merged.js', 'run.py', 'requirements.txt', 'app/__init__.py', 'app/config.py', 'app/models/account.py', 'docs/SELF_HOSTING.md', 'docs/CONFIGURATION.md', 'wrangler.example.toml', '.dev.vars.example', 'supabase/functions/proxy/index.ts', 'proxies/netlify/netlify/edge-functions/proxy.ts', 'proxies/vercel/api/proxy.ts', 'proxies/val-town/main.ts'}
DENIED = {'.git', '.cache', '.netlify', '.vercel', '.temp', 'node_modules', '__pycache__', 'data', 'backups', 'logs', 'scratch', 'validation'}


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], stderr=subprocess.PIPE)


def allowed(name):
    p = PurePosixPath(name)
    return (not p.is_absolute() and '\\' not in name and ':' not in name and '..' not in p.parts and not any(x.lower() in DENIED for x in p.parts)
        and p.suffix.lower() not in {'.pem', '.key', '.p12', '.pfx', '.jks', '.keystore'}
        and not name.startswith('docs/private/') and name not in {'app/deployment_endpoints.json', '.firebaserc', 'firebase.json', 'deployment.txt', 'mcp_probe_worker.js'}
        and (not name.startswith('.github/workflows/') or name == '.github/workflows/public-ci.yml')
        and not any((part.startswith('.env') and part != '.env.example') or (part.startswith('.dev.vars') and part != '.dev.vars.example') for part in p.parts)
        and not re.search(r'serviceAccountKey|gha-creds-|\.encryption_key|\.log(?:\.|$)', name))


def inventory(folder):
    found = {}
    for p in folder.rglob('*'):
        if p.is_symlink():
            raise ValueError('Symlink in export: ' + p.name)
        if p.is_file():
            name = p.relative_to(folder).as_posix()
            if not allowed(name):
                raise ValueError('Prohibited export path: ' + name)
            found[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return found


def export(ref, output, previous=None):
    sha = git('rev-parse', '--verify', ref + '^{commit}').decode().strip()
    names = json.loads(git('show', sha + ':' + MANIFEST))['files']
    if len(names) != len(set(names)) or not REQUIRED.issubset(names):
        raise ValueError('Manifest duplicates or missing required paths.')
    if output.exists() or output.is_symlink():
        raise ValueError('Output already exists; choose a fresh private directory.')
    previous_hashes = None
    if previous:
        previous_meta = json.loads((previous / 'metadata.json').read_text())
        previous_hashes = inventory(previous / 'source')
        if previous_hashes != previous_meta['sha256']:
            raise ValueError('Previous export differs from recorded checksums.')
    entries = {}
    for row in git('ls-tree', '-r', '-z', sha).split(b'\0'):
        if row:
            header, name = row.split(b'\t', 1)
            entries[name.decode()] = header.decode().split()
    # Known official runtime overlays are never exported or used to fill missing settings.
    try:
        origins = json.loads(git('show', sha + ':app/deployment_endpoints.json'))
    except subprocess.CalledProcessError:
        origins = {}
    private_origins = [v.encode() for v in origins.values() if isinstance(v, str) and v.startswith('https://')]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.lmsplus-export-', dir=output.parent) as temp:
        stage = Path(temp) / 'candidate'
        stage.mkdir(mode=0o700)
        source = stage / 'source'
        source.mkdir(parents=True, mode=0o700)
        for name in names:
            if not allowed(name) or name not in entries:
                raise ValueError('Prohibited or missing manifest entry: ' + name)
            mode, kind, oid = entries[name]
            if kind != 'blob' or mode not in {'100644', '100755'}:
                raise ValueError('Unsupported file type: ' + name)
            data = git('cat-file', 'blob', oid)
            if b'-----BEGIN PRIVATE KEY-----' + bytes([10]) in data or b'-----BEGIN RSA PRIVATE KEY-----' + bytes([10]) in data:
                raise ValueError('Private-key marker: ' + name)
            if any(origin in data for origin in private_origins):
                raise ValueError('Official deployment origin: ' + name)
            p = source / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            p.chmod(0o755 if mode == '100755' else 0o644)
        hashes = inventory(source)
        (stage / 'metadata.json').write_text(json.dumps({'source_sha': sha, 'files': len(hashes), 'sha256': hashes,
            'status': 'private candidate; security scans, rights clearance and setup verification required',
            'previous_source_sha': previous_meta['source_sha'] if previous else None}, indent=2) + '\n')
        if previous:
            result = subprocess.run(['git', 'diff', '--no-index', '--binary', '--no-ext-diff', '--src-prefix=previous/', '--dst-prefix=candidate/', str(previous / 'source'), str(source)], capture_output=True)
            if result.returncode not in (0, 1):
                raise ValueError('Cannot compare exports.')
            # Normalize paths so the patch is reviewable/applicable without private directory names.
            patch = result.stdout
            for prefix, folder, replacement in [('previous/', previous / 'source', 'a/'), ('candidate/', source, 'b/')]:
                for spelling in [str(folder), str(folder).lstrip('/')]:
                    patch = patch.replace((prefix + spelling + '/').encode(), replacement.encode())
            (stage / 'changes.patch').write_bytes(patch)
        stage.rename(output)
    return sha, len(hashes)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--previous', type=Path)
    args = parser.parse_args()
    try:
        sha, count = export(args.ref, args.output.resolve(), args.previous.resolve() if args.previous else None)
        print(f'Private candidate exported: {count} files from {sha}. Publication is not authorized.')
    except (ValueError, subprocess.CalledProcessError, OSError, KeyError) as error:
        raise SystemExit('Export refused: ' + (str(error) if not isinstance(error, subprocess.CalledProcessError) else 'committed source unavailable'))
