import hashlib
import importlib
import json
import os
import socket
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_db(path, version):
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version={int(version)}")
        connection.execute("CREATE TABLE sentinel(value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('preserve-me')")


with tempfile.TemporaryDirectory() as temporary:
    os.environ["LOCALAPPDATA"] = temporary
    server = importlib.import_module("server")
    from history_store import HistoryStore, read_history_schema_version

    root = Path(temporary) / "AC6WinLossTracker"
    server.DATA_ROOT = root
    server.CONFIG_PATH = root / "config.json"
    server.RUNTIME_PATH = root / ".runtime.json"
    server.OVERLAY_RUNTIME_PATH = root / ".overlay-runtime.json"
    server.DASHBOARD_RUNTIME_PATH = root / ".dashboard-runtime.json"

    assert HistoryStore.SCHEMA_VERSION == 3
    empty = root / "empty"
    empty.mkdir()
    assert read_history_schema_version(empty) == 0
    assert list(empty.iterdir()) == []

    original_find_spec = server.importlib.util.find_spec
    original_import_module = server.importlib.import_module
    try:
        server.importlib.util.find_spec = lambda name: None if name == "mss" else original_find_spec(name)
        try:
            server.inspect_startup_environment()
            raise AssertionError("missing dependency must fail")
        except server.StartupEnvironmentError as error:
            assert error.code == "ENV-DEPENDENCY-MISSING"
        server.importlib.util.find_spec = lambda name: object()
        server.importlib.import_module = lambda name: (_ for _ in ()).throw(OSError("DLL load failed")) if name == "mss" else original_import_module(name)
        try:
            server.inspect_startup_environment()
            raise AssertionError("dependency import failure must fail")
        except server.StartupEnvironmentError as error:
            assert error.code == "ENV-DEPENDENCY-IMPORT"
    finally:
        server.importlib.util.find_spec = original_find_spec
        server.importlib.import_module = original_import_module

    for schema in (3, 4, 99):
        case = root / f"schema-{schema}"
        case.mkdir(parents=True)
        db = case / "history.db"
        make_db(db, schema)
        before = (digest(db), db.stat().st_size, db.stat().st_mtime_ns, sorted(p.name for p in case.iterdir()))
        assert read_history_schema_version(case) == schema
        with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == schema
            assert connection.execute("SELECT value FROM sentinel").fetchone()[0] == "preserve-me"
        after = (digest(db), db.stat().st_size, db.stat().st_mtime_ns, sorted(p.name for p in case.iterdir()))
        assert after == before

    corrupt = root / "corrupt"
    corrupt.mkdir()
    corrupt_db = corrupt / "history.db"
    corrupt_db.write_bytes(b"not-a-sqlite-database")
    corrupt_before = digest(corrupt_db)
    try:
        read_history_schema_version(corrupt)
        raise AssertionError("corrupt database must not be accepted")
    except sqlite3.DatabaseError:
        pass
    assert digest(corrupt_db) == corrupt_before

    class FakeStats:
        def __init__(self):
            self.reset_calls = 0

        def reset(self):
            self.reset_calls += 1
            raise AssertionError("stats reset must not run")

    original_stats = server.stats
    original_data_root = server.DATA_ROOT
    original_environment_inspection = server.inspect_startup_environment
    callback_calls = []
    try:
        server.stats = FakeStats()
        server.inspect_startup_environment = lambda: None
        for schema in (4, 99):
            case = root / f"main-schema-{schema}"
            case.mkdir(parents=True)
            db = case / "history.db"
            make_db(db, schema)
            server.DATA_ROOT = case
            server.CONFIG_PATH = case / "config.json"
            server.RUNTIME_PATH = case / ".runtime.json"
            config = dict(server.DEFAULT_CONFIG, port=18760 + schema)
            server.CONFIG_PATH.write_text(json.dumps(config), encoding="utf-8")
            with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True) as connection:
                before_rows = connection.execute("SELECT value FROM sentinel").fetchall()
                before_schema = connection.execute("PRAGMA user_version").fetchone()[0]
            before = (
                digest(db), db.stat().st_size, db.stat().st_mtime_ns,
                before_schema, before_rows, sorted(path.name for path in case.iterdir()),
            )
            try:
                server.main(on_ready=lambda: callback_calls.append(schema))
                raise AssertionError("future schema must fail")
            except server.StartupEnvironmentError as error:
                assert error.code == "ENV-HISTORY-FUTURE-SCHEMA"
            with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True) as connection:
                after_rows = connection.execute("SELECT value FROM sentinel").fetchall()
                after_schema = connection.execute("PRAGMA user_version").fetchone()[0]
            after = (
                digest(db), db.stat().st_size, db.stat().st_mtime_ns,
                after_schema, after_rows, sorted(path.name for path in case.iterdir()),
            )
            assert after == before
            assert server.stats.reset_calls == 0
            assert callback_calls == []
            assert not server.RUNTIME_PATH.exists()

        # A duplicate bind fails before history inspection, reset, runtime,
        # detector thread, or overlay callback and leaves all data unchanged.
        duplicate = root / "duplicate"
        duplicate.mkdir(parents=True)
        db = duplicate / "history.db"
        make_db(db, 3)
        before = digest(db)
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        server.DATA_ROOT = duplicate
        server.CONFIG_PATH = duplicate / "config.json"
        server.RUNTIME_PATH = duplicate / ".runtime.json"
        server.RUNTIME_PATH.write_text(
            json.dumps({"pid": 4242, "port": port, "token": "do-not-change"}),
            encoding="utf-8",
        )
        runtime_before = server.RUNTIME_PATH.read_bytes()
        stats_path = duplicate / "stats.json"
        stats_path.write_text('{"sentinel":"do-not-change"}', encoding="utf-8")
        stats_before = stats_path.read_bytes()
        server.CONFIG_PATH.write_text(json.dumps(dict(server.DEFAULT_CONFIG, port=port)), encoding="utf-8")
        try:
            server.main(on_ready=lambda: callback_calls.append("duplicate"))
            raise AssertionError("duplicate bind must fail")
        except server.StartupEnvironmentError as error:
            assert error.code == "ENV-PORT-IN-USE"
        finally:
            blocker.close()
        assert digest(db) == before
        assert server.stats.reset_calls == 0
        assert callback_calls == []
        assert server.RUNTIME_PATH.read_bytes() == runtime_before
        assert stats_path.read_bytes() == stats_before

        # Owner-side write validation also remains before stats reset.
        class FakeHttpServer:
            closed = False

            def __init__(self, address, handler):
                self.server_address = address

            def server_close(self):
                self.closed = True

        original_http_server = server.QuietThreadingHTTPServer
        original_preflight = server.preflight_history_schema
        original_validate = server.validate_owned_filesystem
        fake_holder = {}

        def make_fake_server(address, handler):
            fake_holder["server"] = FakeHttpServer(address, handler)
            return fake_holder["server"]

        server.QuietThreadingHTTPServer = make_fake_server
        server.preflight_history_schema = lambda: 3
        server.validate_owned_filesystem = lambda: (_ for _ in ()).throw(
            server.StartupEnvironmentError(
                "ENV-WRITE-PERMISSION", "write failed", "check permissions"
            )
        )
        writable = root / "write-failure"
        writable.mkdir(parents=True)
        server.DATA_ROOT = writable
        server.CONFIG_PATH = writable / "config.json"
        server.CONFIG_PATH.write_text(
            json.dumps(dict(server.DEFAULT_CONFIG, port=18765)), encoding="utf-8"
        )
        try:
            server.main(on_ready=lambda: callback_calls.append("write-failure"))
            raise AssertionError("write failure must fail")
        except server.StartupEnvironmentError as error:
            assert error.code == "ENV-WRITE-PERMISSION"
        assert fake_holder["server"].closed
        assert server.stats.reset_calls == 0
        assert callback_calls == []
        server.QuietThreadingHTTPServer = original_http_server
        server.preflight_history_schema = original_preflight
        server.validate_owned_filesystem = original_validate
    finally:
        server.stats = original_stats
        server.DATA_ROOT = original_data_root
        server.inspect_startup_environment = original_environment_inspection

print("read-only schema 3/4/99 and duplicate-start non-mutation preflight: OK")
