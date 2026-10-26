/*
 * Unified Query Algebra
 *
 * Copyright (c) 2023-2026 Cognica, Inc.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sqlite3.h"

static void require(int condition, const char *message) {
    if (!condition) {
        fprintf(stderr, "%s\n", message);
        exit(1);
    }
}

static void execute(sqlite3 *database, const char *sql) {
    char *error = NULL;
    int status = sqlite3_exec(database, sql, NULL, NULL, &error);
    if (status != SQLITE_OK) {
        fprintf(stderr, "%s: %s\n", sql, error != NULL ? error : sqlite3_errmsg(database));
        sqlite3_free(error);
        exit(1);
    }
}

static sqlite3 *open_database(const char *path, const char *key) {
    sqlite3 *database = NULL;
    require(sqlite3_open(path, &database) == SQLITE_OK, "database open failed");
    require(sqlite3_key(database, key, (int)strlen(key)) == SQLITE_OK, "database key failed");
    return database;
}

static void expect_text(sqlite3 *database, const char *sql, const char *expected) {
    sqlite3_stmt *statement = NULL;
    require(sqlite3_prepare_v2(database, sql, -1, &statement, NULL) == SQLITE_OK, sqlite3_errmsg(database));
    require(sqlite3_step(statement) == SQLITE_ROW, "expected one result row");
    const unsigned char *value = sqlite3_column_text(statement, 0);
    require(value != NULL && strcmp((const char *)value, expected) == 0, sql);
    require(sqlite3_step(statement) == SQLITE_DONE, "unexpected additional row");
    require(sqlite3_finalize(statement) == SQLITE_OK, "statement finalization failed");
}

static void verify_values(sqlite3 *database, int updated) {
    expect_text(database, "SELECT group_concat(id || ':' || value || ':' || hex(bytes), '|') FROM (SELECT * FROM fixture ORDER BY id)",
                updated ? "1:original:0001FF|2:committed:1234|3:upgraded:00AA" : "1:original:0001FF|2:committed:1234");
    expect_text(database, "PRAGMA integrity_check", "ok");
}

static void verify(sqlite3 *database, int updated) {
    verify_values(database, updated);
    sqlite3_stmt *statement = NULL;
    require(sqlite3_prepare_v2(database, "PRAGMA cipher_integrity_check", -1, &statement, NULL) == SQLITE_OK, "cipher integrity prepare failed");
    require(sqlite3_step(statement) == SQLITE_DONE, "cipher integrity failed");
    require(sqlite3_finalize(statement) == SQLITE_OK, "cipher integrity finalization failed");
}

int main(int argc, char **argv) {
    require(argc >= 3, "usage: sqlcipher MODE DATABASE [EXPORT]");
    const char *mode = argv[1];
    const char *key = strcmp(mode, "verify-rekey") == 0 ? "replacement-key" : "fixture-key";
    if (strcmp(mode, "wrong-key") == 0) {
        key = "incorrect";
    }
    sqlite3 *database = open_database(argv[2], key);
    if (strcmp(mode, "open") == 0) {
        require(sqlite3_close(database) == SQLITE_OK, "database close failed");
        return 0;
    }
    if (strcmp(mode, "wrong-key") == 0) {
        require(sqlite3_exec(database, "SELECT count(*) FROM sqlite_master", NULL, NULL, NULL) == SQLITE_NOTADB, "incorrect key was accepted");
        require(sqlite3_close(database) == SQLITE_OK, "database close failed");
        return 0;
    }
    expect_text(database, "SELECT sqlite_compileoption_used('ENABLE_MEMORY_MANAGEMENT')", "0");
    if (strcmp(mode, "create") == 0) {
        expect_text(database, "PRAGMA journal_mode=WAL", "wal");
        execute(database, "CREATE TABLE fixture(id INTEGER PRIMARY KEY, value TEXT NOT NULL, bytes BLOB NOT NULL); BEGIN; INSERT INTO fixture VALUES(1,'original',x'0001FF'),(2,'committed',x'1234'); COMMIT; BEGIN; INSERT INTO fixture VALUES(999,'rolled-back',x'FF'); ROLLBACK;");
        verify(database, 0);
    } else if (strcmp(mode, "update") == 0 || strcmp(mode, "hold-update") == 0) {
        verify(database, 0);
        expect_text(database, "PRAGMA journal_mode", "wal");
        execute(database, "BEGIN IMMEDIATE; INSERT INTO fixture VALUES(3,'upgraded',x'00AA'); SAVEPOINT retained; DELETE FROM fixture WHERE id=1; ROLLBACK TO retained; RELEASE retained; COMMIT;");
        verify(database, 1);
        if (strcmp(mode, "hold-update") == 0) {
            puts("committed WAL available");
            fflush(stdout);
            require(getchar() == '\n', "WAL observer did not finish");
        }
    } else if (strcmp(mode, "verify") == 0) {
        verify(database, 0);
    } else if (strcmp(mode, "verify-updated") == 0 || strcmp(mode, "verify-rekey") == 0) {
        verify(database, 1);
    } else if (strcmp(mode, "rekey") == 0) {
        verify(database, 1);
        require(sqlite3_rekey(database, "replacement-key", 15) == SQLITE_OK, "database rekey failed");
        /* Check the physical HMAC envelope after close checkpoints the rekeyed WAL. */
        verify_values(database, 1);
    } else if (strcmp(mode, "export") == 0) {
        require(argc == 4, "export destination required");
        char *sql = sqlite3_mprintf("ATTACH DATABASE %Q AS exported KEY 'fixture-key'; SELECT sqlcipher_export('exported'); DETACH DATABASE exported;", argv[3]);
        require(sql != NULL, "export SQL allocation failed");
        execute(database, sql);
        sqlite3_free(sql);
    } else {
        require(0, "unknown mode");
    }
    require(sqlite3_close(database) == SQLITE_OK, "database close failed");
    return 0;
}
