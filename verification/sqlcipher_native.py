"""Exercise old/new encrypted files and instrument the complete SQLCipher amalgamation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from provenance import BASE, ROOT

DEFINES = [
    'SQLITE_CORE', 'SQLITE_HAS_CODEC', 'SQLITE_THREADSAFE=1', 'SQLITE_TEMP_STORE=2',
    'SQLITE_EXTRA_INIT=sqlcipher_extra_init', 'SQLITE_EXTRA_SHUTDOWN=sqlcipher_extra_shutdown',
    'SQLCIPHER_CRYPTO_OPENSSL', 'HAVE_STDINT_H=1', 'SQLITE_DEFAULT_FOREIGN_KEYS=1',
    'SQLITE_ENABLE_API_ARMOR', 'SQLITE_ENABLE_COLUMN_METADATA', 'SQLITE_ENABLE_DBSTAT_VTAB',
    'SQLITE_ENABLE_FTS3', 'SQLITE_ENABLE_FTS3_PARENTHESIS', 'SQLITE_ENABLE_FTS5',
    'SQLITE_ENABLE_JSON1', 'SQLITE_ENABLE_LOAD_EXTENSION=1', 'SQLITE_ENABLE_RTREE',
    'SQLITE_ENABLE_STAT4', 'SQLITE_SOUNDEX', 'SQLITE_USE_URI', 'HAVE_USLEEP=1',
    'HAVE_ISNAN', 'HAVE_LOCALTIME_R',
]


def run(command, **kwargs):
    subprocess.run([str(part) for part in command], check=True, **kwargs)


def compile_library(source, output, args, instrumented):
    flags = ['-g', '-O1', '-fno-omit-frame-pointer']
    if instrumented:
        flags += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    run([args.cc, '-std=c11', *flags, *(f'-D{value}' for value in DEFINES),
         '-I', args.openssl / 'include', '-c', source / 'sqlite3.c', '-o', output])


def compile_client(source, library, output, args, language, instrumented):
    flags = ['-g', '-O1', '-Wall', '-Wextra', '-Werror', '-fno-omit-frame-pointer']
    if instrumented:
        flags += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    compiler = args.cc if language == 'c' else args.cxx
    crypto = next((args.openssl / lib / 'libcrypto.a' for lib in ('lib', 'lib64')
                   if (args.openssl / lib / 'libcrypto.a').is_file()), None)
    if crypto is None:
        raise ValueError('An existing static OpenSSL installation is required')
    client = output.with_suffix('.o')
    run([compiler, '-x', language, '-std=c11' if language == 'c' else '-std=c++17', *flags,
         '-DSQLITE_HAS_CODEC', '-I', source, '-c', ROOT / 'verification/sqlcipher.c', '-o', client])
    run([compiler, *flags, client, library, crypto, '-lpthread', '-ldl', '-lm', '-o', output])


def verify_files(old, new, directory, env):
    def call(executable, mode, path, *extra):
        run([executable, mode, path, *extra], env=env, stdout=subprocess.DEVNULL)

    for creator, reader, name in ((old, new, 'old-created'), (new, old, 'new-created')):
        path = directory / f'{name}.db'
        call(creator, 'create', path)
        if path.read_bytes()[:16] == b'SQLite format 3\0':
            raise ValueError('Encrypted fixture has a plaintext header')
        call(reader, 'verify', path)
        call(reader, 'wrong-key', path)
        writer = subprocess.Popen([str(new), 'hold-update', str(path)], env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            if writer.stdout.readline().strip() != 'committed WAL available':
                raise ValueError('Writer did not retain its committed WAL')
            if Path(str(path) + '-wal').stat().st_size <= 32:
                raise ValueError('No committed WAL frames to inspect')
            call(old, 'verify-updated', path)
            call(new, 'verify-updated', path)
            writer.communicate('\n', timeout=30)
            if writer.returncode != 0:
                raise ValueError('Writer failed while closing the retained WAL')
        finally:
            if writer.poll() is None:
                writer.kill()
                writer.wait()
        call(old, 'verify-updated', path)
        exported = directory / f'{name}-export.db'
        call(new, 'export', path, exported)
        call(old, 'verify-updated', exported)
        call(new, 'verify-updated', exported)
        call(new, 'rekey', path)
        call(old, 'verify-rekey', path)
        call(new, 'verify-rekey', path)
        call(old, 'wrong-key', path)
        call(new, 'wrong-key', path)


def verify(args):
    args.output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env['UBSAN_OPTIONS'] = 'halt_on_error=1:print_stacktrace=1'
    env['ASAN_OPTIONS'] = 'halt_on_error=1:detect_leaks=' + ('0' if sys.platform == 'darwin' else '1')
    with tempfile.TemporaryDirectory(prefix='sqlcipher-files-', dir=args.output) as temporary:
        work = Path(temporary)
        old_source = work / 'old'
        old_source.mkdir()
        for name in ('sqlite3.c', 'sqlite3.h'):
            data = subprocess.check_output(['git', 'show', f'{BASE}:libsqlite3-sys/sqlcipher/{name}'], cwd=ROOT)
            (old_source / name).write_bytes(data)
        old_hash = hashlib.sha256((old_source / 'sqlite3.c').read_bytes()).hexdigest()
        if old_hash != 'de78ef087cbc26e8fe584de05f1790f3e34897db8731e8154dc674d3f2df28cb':
            raise ValueError('Unexpected original SQLCipher 4.10.0 source')
        new_source = ROOT / 'libsqlite3-sys/sqlcipher'
        for name, source, instrumented in (('old', old_source, False), ('old-sanitized', old_source, True),
                                           ('new-sanitized', new_source, True)):
            compile_library(source, work / f'{name}.o', args, instrumented)
        old = work / 'old-client'
        compile_client(old_source, work / 'old.o', old, args, 'c', False)
        results = []
        for language in ('c', 'c++'):
            bad = work / f'old-{language}'
            compile_client(old_source, work / 'old-sanitized.o', bad, args, language, True)
            failure = subprocess.run([str(bad), 'open', ':memory:'], env=env, capture_output=True, text=True)
            (args.output / f'old-{language}.log').write_text(failure.stdout + failure.stderr)
            if failure.returncode == 0 or 'incorrect function type' not in failure.stderr:
                raise ValueError('The original callback defect was not detected by UBSan')
            new = work / f'new-{language}'
            compile_client(new_source, work / 'new-sanitized.o', new, args, language, True)
            run([new, 'open', ':memory:'], env=env)
            files = work / language
            files.mkdir()
            verify_files(old, new, files, env)
            results.append({'language': language, 'original_callback_detected': True,
                            'updated_full_sqlcipher_sanitizers': True, 'bidirectional_encrypted_files': True,
                            'live_wal': True, 'rollback_and_savepoint': True, 'export': True, 'rekey': True,
                            'incorrect_key_rejected': True})
        summary = {'original_sha256': old_hash,
                   'updated_sha256': hashlib.sha256((new_source / 'sqlite3.c').read_bytes()).hexdigest(),
                   'compiler': subprocess.check_output([args.cc, '--version'], text=True).splitlines()[0],
                   'openssl_instrumented': False, 'results': results}
        (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cc', default='clang')
    parser.add_argument('--cxx', default='clang++')
    parser.add_argument('--openssl', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'target/sqlcipher-verification')
    verify(parser.parse_args())
