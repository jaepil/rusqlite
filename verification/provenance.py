"""Verify the managed packages against their pinned upstream source and archives."""
import argparse
import hashlib
import json
import pathlib
import subprocess
import tarfile
import tomllib

BASE = '2a1790a69107cd03dae85d501dcbdb11c5b32ef3'
ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGES = [('uqa-libsqlite3-sys', '0.37.3', 'libsqlite3-sys'), ('uqa-rusqlite', '0.39.3', '.')]


def verify():
    for name, version, relative in PACKAGES:
        manifest = tomllib.loads((ROOT / relative / 'Cargo.toml').read_text())
        assert manifest['package']['name'] == name
        assert manifest['package']['version'] == version
        assert manifest['package']['repository'] == 'https://github.com/jaepil/rusqlite'
        assert manifest['package']['license'] == 'MIT'
    subprocess.run(['git', 'diff', '--exit-code', BASE, '--', 'src', 'libsqlite3-sys/src', 'libsqlite3-sys/sqlite3', 'LICENSE', 'libsqlite3-sys/LICENSE'], cwd=ROOT, check=True)
    sqlcipher = json.loads((ROOT / 'verification/sqlcipher.json').read_text())
    assert sqlcipher['tag'] == 'v4.19.0'
    assert sqlcipher['commit'] == 'c4b275a47932888216bade83aff2bbc73df0ff85'
    assert [patch['file'] for patch in sqlcipher['patches']] == ['patches/sqlcipher-initialization.patch']
    for patch in sqlcipher['patches']:
        assert hashlib.sha256((ROOT / 'verification' / patch['file']).read_bytes()).hexdigest() == patch['sha256']
    assert set(sqlcipher['sha256']) == {'sqlite3.c', 'sqlite3.h', 'sqlite3ext.h', 'bindgen_bundled_version.rs', 'LICENSE'}
    for name, digest in sqlcipher['sha256'].items():
        assert hashlib.sha256((ROOT / 'libsqlite3-sys/sqlcipher' / name).read_bytes()).hexdigest() == digest, name
    lock = tomllib.loads((ROOT / 'Cargo.lock').read_text())
    assert [p['version'] for p in lock['package'] if p['name'] == 'bindgen'] == [sqlcipher['bindgen_version']]
    upstream = subprocess.check_output(['git', 'show', f'{BASE}:libsqlite3-sys/build.rs'], cwd=ROOT)
    define = b'            .flag("-DSQLITE_ENABLE_MEMORY_MANAGEMENT")\n'
    assert upstream.count(define) == 1
    initialization = b'                .flag("-DSQLITE_EXTRA_INIT=sqlcipher_extra_init")\n'
    serialized = b'                .flag("-DSQLITE_EXTRA_INIT_MUTEXED=sqlcipher_extra_init")\n'
    builtin = b'                .flag("-DSQLITE_EXTRA_AUTOEXT=sqlcipher_extra_autoext")\n'
    assert upstream.count(initialization) == 1
    expected = upstream.replace(define, b'').replace(initialization, initialization + serialized + builtin)
    assert (ROOT / 'libsqlite3-sys/build.rs').read_bytes() == expected
    root_manifest = tomllib.loads((ROOT / 'Cargo.toml').read_text())
    native = root_manifest['target']['cfg(not(all(target_family = "wasm", target_os = "unknown")))']['dependencies']['libsqlite3-sys']
    assert native['package'] == 'uqa-libsqlite3-sys' and native['version'] == '=0.37.3'
    assert root_manifest['dependencies']['rusqlite-macros'] == {'version': '0.4.2', 'optional': True}


def archives(directory):
    for name, version, relative in PACKAGES:
        prefix = f'{name}-{version}/'
        with tarfile.open(directory / f'{name}-{version}.crate') as archive:
            manifest = tomllib.loads(archive.extractfile(prefix + 'Cargo.toml').read().decode())
            assert manifest['package']['name'] == name and manifest['package']['version'] == version
            assert archive.extractfile(prefix + 'Cargo.toml.orig').read() == (ROOT / relative / 'Cargo.toml').read_bytes()
            for member in archive.getmembers():
                if member.isfile() and member.name.endswith(('.rs', '.c', '.h')):
                    source = ROOT / relative / member.name.removeprefix(prefix)
                    assert source.is_file(), member.name
                    assert archive.extractfile(member).read() == source.read_bytes(), member.name
            licenses = [m for m in archive.getmembers() if m.name.startswith(prefix) and pathlib.PurePosixPath(m.name).name.startswith('LICENSE')]
            assert licenses, name
            for member in licenses:
                source = ROOT / relative / member.name.removeprefix(prefix)
                assert archive.extractfile(member).read() == source.read_bytes(), member.name
            if relative == 'libsqlite3-sys':
                assert prefix + 'sqlcipher/LICENSE' in [member.name for member in licenses]
    print('Both registry archives retain reviewed source, package identity and licenses')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archives', type=pathlib.Path)
    args = parser.parse_args()
    verify()
    if args.archives:
        archives(args.archives)
    print('Pinned Rust/plain SQLite baseline, SQLCipher release and page-cache build configuration verified')
