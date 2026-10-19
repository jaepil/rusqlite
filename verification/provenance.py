"""Verify the managed packages against their pinned upstream source and archives."""
import argparse
import pathlib
import subprocess
import tarfile
import tomllib

BASE = '2a1790a69107cd03dae85d501dcbdb11c5b32ef3'
ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGES = [('uqa-libsqlite3-sys', '0.37.1', 'libsqlite3-sys'), ('uqa-rusqlite', '0.39.1', '.')]


def verify():
    for name, version, relative in PACKAGES:
        manifest = tomllib.loads((ROOT / relative / 'Cargo.toml').read_text())
        assert manifest['package']['name'] == name
        assert manifest['package']['version'] == version
        assert manifest['package']['repository'] == 'https://github.com/jaepil/rusqlite'
        assert manifest['package']['license'] == 'MIT'
    subprocess.run(['git', 'diff', '--exit-code', BASE, '--', 'src', 'libsqlite3-sys/src', 'libsqlite3-sys/sqlite3', 'libsqlite3-sys/sqlcipher', 'LICENSE', 'libsqlite3-sys/LICENSE'], cwd=ROOT, check=True)
    upstream = subprocess.check_output(['git', 'show', f'{BASE}:libsqlite3-sys/build.rs'], cwd=ROOT)
    define = b'            .flag("-DSQLITE_ENABLE_MEMORY_MANAGEMENT")\n'
    assert upstream.count(define) == 1
    assert (ROOT / 'libsqlite3-sys/build.rs').read_bytes() == upstream.replace(define, b'')
    root_manifest = tomllib.loads((ROOT / 'Cargo.toml').read_text())
    native = root_manifest['target']['cfg(not(all(target_family = "wasm", target_os = "unknown")))']['dependencies']['libsqlite3-sys']
    assert native['package'] == 'uqa-libsqlite3-sys' and native['version'] == '=0.37.1'
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
    print('Both registry archives retain reviewed source, package identity and licenses')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archives', type=pathlib.Path)
    args = parser.parse_args()
    verify()
    if args.archives:
        archives(args.archives)
    print('Pinned upstream source and one-line build correction verified')
