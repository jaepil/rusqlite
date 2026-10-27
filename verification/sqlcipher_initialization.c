/*
 * Unified Query Algebra
 *
 * Copyright (c) 2023-2026 Cognica, Inc.
 */

#define _POSIX_C_SOURCE 200809L

#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include "sqlite3.h"

struct sqlite3_mutex {
    pthread_mutex_t native;
    atomic_uintptr_t owner;
    unsigned depth;
};
static struct sqlite3_mutex fixed[16];
static pthread_once_t once = PTHREAD_ONCE_INIT;
static _Thread_local char thread_identity;
static _Atomic(sqlite3_mutex *) initialization_mutex;
static atomic_int pause_once;
static pthread_mutex_t gate = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t changed = PTHREAD_COND_INITIALIZER;
static int paused, resume_initializer;
static int initialization_result;

static void create_mutex(sqlite3_mutex *mutex, int recursive) {
    pthread_mutexattr_t attributes;
    assert(pthread_mutexattr_init(&attributes) == 0);
    assert(pthread_mutexattr_settype(&attributes, recursive ? PTHREAD_MUTEX_RECURSIVE : PTHREAD_MUTEX_NORMAL) == 0);
    assert(pthread_mutex_init(&mutex->native, &attributes) == 0);
    assert(pthread_mutexattr_destroy(&attributes) == 0);
    atomic_init(&mutex->owner, 0);
    mutex->depth = 0;
}
static void create_fixed(void) {
    for (unsigned index = 0; index < 16; ++index) create_mutex(&fixed[index], 0);
}
static int mutex_init(void) {
    return pthread_once(&once, create_fixed) == 0 ? SQLITE_OK : SQLITE_ERROR;
}
static int mutex_end(void) { return SQLITE_OK; }
static sqlite3_mutex *mutex_alloc(int kind) {
    assert(mutex_init() == SQLITE_OK);
    if (kind >= SQLITE_MUTEX_STATIC_MAIN) {
        assert(kind < 16);
        return &fixed[kind];
    }
    sqlite3_mutex *mutex = malloc(sizeof(*mutex));
    if (mutex == NULL) return NULL;
    create_mutex(mutex, kind == SQLITE_MUTEX_RECURSIVE);
    if (kind == SQLITE_MUTEX_RECURSIVE) {
        sqlite3_mutex *empty = NULL;
        atomic_compare_exchange_strong(&initialization_mutex, &empty, mutex);
    }
    return mutex;
}
static void mutex_free(sqlite3_mutex *mutex) {
    assert(pthread_mutex_destroy(&mutex->native) == 0);
    free(mutex);
}
static void mutex_enter(sqlite3_mutex *mutex) {
    assert(pthread_mutex_lock(&mutex->native) == 0);
    atomic_store(&mutex->owner, (uintptr_t)&thread_identity);
    ++mutex->depth;
}
static int mutex_try(sqlite3_mutex *mutex) {
    if (pthread_mutex_trylock(&mutex->native) != 0) return SQLITE_BUSY;
    atomic_store(&mutex->owner, (uintptr_t)&thread_identity);
    ++mutex->depth;
    return SQLITE_OK;
}
static void mutex_leave(sqlite3_mutex *mutex) {
    assert(mutex->depth != 0);
    const int outermost = --mutex->depth == 0;
    if (outermost) atomic_store(&mutex->owner, 0);
    assert(pthread_mutex_unlock(&mutex->native) == 0);
    if (outermost && mutex == atomic_load(&initialization_mutex) &&
        atomic_exchange(&pause_once, 1) == 0) {
        pthread_mutex_lock(&gate);
        paused = 1;
        pthread_cond_broadcast(&changed);
        while (!resume_initializer) pthread_cond_wait(&changed, &gate);
        pthread_mutex_unlock(&gate);
    }
}
static int mutex_held(sqlite3_mutex *mutex) {
    return atomic_load(&mutex->owner) == (uintptr_t)&thread_identity;
}
static int mutex_notheld(sqlite3_mutex *mutex) { return !mutex_held(mutex); }
static void *initialize(void *argument) {
    (void)argument;
    initialization_result = sqlite3_initialize();
    return NULL;
}
static int verify_initialization(void) {
    const sqlite3_mutex_methods methods = {
        mutex_init, mutex_end, mutex_alloc, mutex_free, mutex_enter,
        mutex_try, mutex_leave, mutex_held, mutex_notheld
    };
    atomic_store(&initialization_mutex, NULL);
    atomic_store(&pause_once, 0);
    paused = 0;
    resume_initializer = 0;
    assert(sqlite3_config(SQLITE_CONFIG_MUTEX, &methods) == SQLITE_OK);
    pthread_t initializer;
    assert(pthread_create(&initializer, NULL, initialize, NULL) == 0);
    pthread_mutex_lock(&gate);
    while (!paused) pthread_cond_wait(&changed, &gate);
    pthread_mutex_unlock(&gate);

    sqlite3 *database = NULL;
    int result = sqlite3_open(":memory:", &database);
    if (result == SQLITE_OK) result = sqlite3_key(database, "publication-key", 15);
    printf("key before initializing thread returns: rc=%d\n", result);
    if (result == SQLITE_OK) {
        sqlite3_stmt *statement = NULL;
        assert(sqlite3_prepare_v2(database, "SELECT count(*) FROM pragma_function_list WHERE name='sqlcipher_export'", -1, &statement, NULL) == SQLITE_OK);
        assert(sqlite3_step(statement) == SQLITE_ROW);
        assert(sqlite3_column_int(statement, 0) == 1);
        assert(sqlite3_finalize(statement) == SQLITE_OK);
        assert(sqlite3_exec(database, "CREATE TABLE fixture(value); INSERT INTO fixture VALUES(42)", NULL, NULL, NULL) == SQLITE_OK);
    }
    assert(sqlite3_close(database) == SQLITE_OK);

    pthread_mutex_lock(&gate);
    resume_initializer = 1;
    pthread_cond_broadcast(&changed);
    pthread_mutex_unlock(&gate);
    assert(pthread_join(initializer, NULL) == 0);
    assert(initialization_result == SQLITE_OK);
    assert(sqlite3_open(":memory:", &database) == SQLITE_OK);
    assert(sqlite3_key(database, "publication-key", 15) == SQLITE_OK);
    assert(sqlite3_close(database) == SQLITE_OK);
    assert(sqlite3_shutdown() == SQLITE_OK);
    return result != SQLITE_OK;
}

int main(void) {
    /* Exercise first initialization and complete shutdown/reinitialization. */
    for (unsigned cycle = 0; cycle < 3; ++cycle) {
        if (verify_initialization() != 0) return 1;
    }
    puts("Concurrent crypto and extension initialization passed across three lifetimes");
    return 0;
}
