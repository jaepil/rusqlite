"""Regenerate bundled SQLCipher from the exact reviewed upstream release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'verification/sqlcipher.json'
DESTINATION = ROOT / 'libsqlite3-sys/sqlcipher'
SOURCE_FILES = ('sqlite3.c', 'sqlite3.h', 'sqlite3ext.h')


def run(command, cwd, env, log):
    with log.open('w') as output:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'{command!r} failed:\n{log.read_text()[-12000:]}')


def regenerate(source, check):
    metadata = json.loads(MANIFEST.read_text())
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    if revision != metadata['commit']:
        raise ValueError(f'Expected SQLCipher {metadata["commit"]}, found {revision}')
    subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--'], cwd=source, check=True)
    if subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], cwd=source):
        raise ValueError('The official source checkout contains untracked files')
    if (source / 'VERSION').read_text().strip() != metadata['sqlite_version']:
        raise ValueError('Unexpected upstream SQLite version')
    with tempfile.TemporaryDirectory(prefix='uqa-sqlcipher-') as directory:
        build = Path(directory)
        env = os.environ.copy()
        for name in ('CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS'):
            env[name] = ''
        env['CCACHE'] = 'none'
        run([str(source / 'configure'), '--disable-tcl'], build, env, build / 'configure.log')
        run(['make', '-j2', 'sqlite3.c'], build, env, build / 'amalgamation.log')
        env.update({
            'LIBSQLITE3_SYS_BUNDLING': '1',
            'SQLCIPHER_LIB_DIR': str(build),
            'SQLCIPHER_INCLUDE_DIR': str(build),
            'CARGO_TARGET_DIR': str(ROOT / 'target/sqlcipher-bindgen'),
            'BINDGEN_EXTRA_CLANG_ARGS': '--target=' + metadata['bindgen_target'],
        })
        messages = build / 'cargo.jsonl'
        errors = build / 'cargo.log'
        command = ['cargo', 'build', '-p', 'uqa-libsqlite3-sys', '--locked', '--no-default-features',
                   '--features', 'sqlcipher,buildtime_bindgen,session', '--message-format=json']
        with messages.open('w') as output, errors.open('w') as stderr:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=output, stderr=stderr)
        if result.returncode:
            raise RuntimeError(errors.read_text())
        outputs = [json.loads(line) for line in messages.read_text().splitlines()]
        native = [item for item in outputs if item['reason'] == 'build-script-executed'
                  and '#uqa-libsqlite3-sys@' in item['package_id']]
        if len(native) != 1:
            raise ValueError('Expected one native binding generator output')
        generated = {name: (build / name).read_bytes() for name in SOURCE_FILES}
        generated['bindgen_bundled_version.rs'] = (Path(native[0]['out_dir']) / 'bindgen.rs').read_bytes()
        generated['LICENSE'] = (source / 'LICENSE.md').read_bytes()
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in generated.items()}
        if check:
            for name, data in generated.items():
                if (DESTINATION / name).read_bytes() != data:
                    raise ValueError(f'Bundled {name} differs from regenerated official source')
            if metadata['sha256'] != hashes:
                raise ValueError('Generated source hashes differ from the provenance manifest')
        else:
            for name, data in generated.items():
                (DESTINATION / name).write_bytes(data)
            metadata['sha256'] = hashes
            MANIFEST.write_text(json.dumps(metadata, indent=2) + '\n')
    print(f'SQLCipher {metadata["version"]} / SQLite {metadata["sqlite_version"]}: all generated files verified')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Clean checkout of the pinned SQLCipher commit')
    parser.add_argument('--check', action='store_true', help='Compare regenerated files without changing tracked files')
    args = parser.parse_args()
    regenerate(args.source.resolve(), args.check)
