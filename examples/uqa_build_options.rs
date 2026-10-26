use rusqlite::Connection;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let connection = Connection::open_in_memory()?;
    let enabled: i64 = connection.query_row(
        "SELECT sqlite_compileoption_used('ENABLE_MEMORY_MANAGEMENT')",
        [],
        |row| row.get(0),
    )?;
    assert_eq!(
        enabled, 0,
        "bundled page caches must not share the memory-management mutex"
    );
    connection.execute_batch("CREATE TABLE proof(value INTEGER); INSERT INTO proof VALUES (42)")?;
    connection.release_memory()?;
    assert_eq!(
        connection.query_row("SELECT value FROM proof", [], |row| row.get::<_, i64>(0))?,
        42
    );
    #[cfg(all(feature = "bundled-sqlcipher", not(target_os = "emscripten")))]
    {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("encrypted.db");
        {
            let connection = Connection::open(&path)?;
            connection.execute_batch("PRAGMA key = 'verification-only'; CREATE TABLE proof(value INTEGER); INSERT INTO proof VALUES (42)")?;
            let version: String =
                connection.query_row("PRAGMA cipher_version", [], |row| row.get(0))?;
            assert_eq!(version, "4.19.0 community");
            assert_eq!(rusqlite::version(), "3.53.4");
            connection.release_memory()?;
            println!("SQLCipher {version}: local cache reclamation passed");
        }
        assert_ne!(&std::fs::read(&path)?[..16], b"SQLite format 3\0");
        {
            let connection = Connection::open(&path)?;
            connection.execute_batch("PRAGMA key = 'wrong-key'")?;
            assert!(connection
                .query_row("SELECT value FROM proof", [], |row| row.get::<_, i64>(0))
                .is_err());
        }
        {
            let connection = Connection::open(&path)?;
            connection.execute_batch("PRAGMA key = 'verification-only'")?;
            assert_eq!(
                connection.query_row("SELECT value FROM proof", [], |row| row.get::<_, i64>(0))?,
                42
            );
        }
    }
    println!("ENABLE_MEMORY_MANAGEMENT={enabled}; output and cache reclamation verified");
    Ok(())
}
