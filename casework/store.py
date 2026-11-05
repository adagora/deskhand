import sqlite3
from contextlib import contextmanager
from pathlib import Path


def open_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA synchronous=FULL")
    return connection


@contextmanager
def transaction(path: Path):
    connection = open_database(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
    finally:
        connection.close()


@contextmanager
def reading(path: Path):
    connection = open_database(path)
    try:
        yield connection
    finally:
        connection.close()
