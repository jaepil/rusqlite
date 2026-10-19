# Managed fork verification

`provenance.py` requires the pinned upstream 0.39.0 revision, byte-identical Rust APIs and SQLite/SQLCipher sources, and exactly the removal of the bundled memory-management define from the original build script. Archive checks inspect both normalized package identities, original manifests, shipped native/Rust source bytes and licenses before publication.

`examples/uqa_build_options.rs` checks the compiled SQLite option rather than inferring it from source. It verifies ordinary values and connection-local memory release on both backends, plus SQLCipher encryption, incorrect-key rejection and durable reopen on native platforms. Linux and macOS also run the existing library tests with UQA's blob, statement-cache and scalar-function features; Emscripten executes the same compiled-option and value checks with plain bundled SQLite. Runtime observations establish correctness, not a throughput improvement.

Local 2026-10-20 verification passed the SQLCipher library suite and both native example configurations. CI owns archive creation and publication; no local package build, publication dry-run or registry upload is used. Push `uqa-v0.39.1` only for the reviewed merged source. Publication needs all native and Emscripten jobs and the registry secret, and then checks a fresh registry consumer. The upstream license remains unchanged.
