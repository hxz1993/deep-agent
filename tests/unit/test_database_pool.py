from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from deep_agent.db.factory import (
    MYSQL_POOL_RECYCLE_SECONDS,
    _is_asyncmy_closed_transport_error,
    _mark_asyncmy_pre_ping_disconnect,
    _set_mysql_session_utc,
    create_engine,
)


class DatabasePoolConfigurationTests(unittest.TestCase):
    def test_mysql_uses_pre_ping_and_recycles_before_test_timeout(self) -> None:
        engine = SimpleNamespace(sync_engine=object())
        with (
            patch("deep_agent.db.factory.create_async_engine", return_value=engine) as create,
            patch("deep_agent.db.factory.async_sessionmaker", return_value=object()),
            patch("deep_agent.db.factory.event.listen") as listen,
        ):
            create_engine("mysql+asyncmy://user:pass@db:3306/deepagent")

        create.assert_called_once_with(
            "mysql+asyncmy://user:pass@db:3306/deepagent",
            pool_pre_ping=True,
            pool_recycle=MYSQL_POOL_RECYCLE_SECONDS,
        )
        self.assertEqual(
            listen.call_args_list,
            [
                unittest.mock.call(
                    engine.sync_engine,
                    "connect",
                    _set_mysql_session_utc,
                    insert=True,
                ),
                unittest.mock.call(
                    engine.sync_engine,
                    "handle_error",
                    _mark_asyncmy_pre_ping_disconnect,
                ),
            ],
        )

    def test_sqlite_keeps_existing_pool_behavior(self) -> None:
        engine = SimpleNamespace(sync_engine=object())
        with (
            patch("deep_agent.db.factory.create_async_engine", return_value=engine) as create,
            patch("deep_agent.db.factory.async_sessionmaker", return_value=object()),
            patch("deep_agent.db.factory.event.listen") as listen,
        ):
            create_engine("sqlite+aiosqlite:////tmp/deep-agent.db")

        create.assert_called_once_with(
            "sqlite+aiosqlite:////tmp/deep-agent.db",
            pool_pre_ping=True,
        )
        listen.assert_not_called()

    def test_mysql_connection_is_initialized_to_utc(self) -> None:
        cursor = unittest.mock.Mock()
        connection = unittest.mock.Mock()
        connection.cursor.return_value = cursor

        _set_mysql_session_utc(connection, object())

        cursor.execute.assert_called_once_with("SET time_zone = '+00:00'")
        cursor.close.assert_called_once_with()


class AsyncmyDisconnectRecognitionTests(unittest.TestCase):
    _CLOSED_MESSAGE = (
        "unable to perform operation on "
        "<TCPTransport closed=True reading=False 0x123>; the handler is closed"
    )

    def test_exact_runtime_error_is_recognized(self) -> None:
        self.assertTrue(_is_asyncmy_closed_transport_error(RuntimeError(self._CLOSED_MESSAGE)))

    def test_other_errors_are_not_recognized(self) -> None:
        self.assertFalse(_is_asyncmy_closed_transport_error(RuntimeError("business failure")))
        self.assertFalse(_is_asyncmy_closed_transport_error(ValueError(self._CLOSED_MESSAGE)))

    def test_pre_ping_marks_exact_error_as_disconnect(self) -> None:
        context = SimpleNamespace(
            is_pre_ping=True,
            original_exception=RuntimeError(self._CLOSED_MESSAGE),
            is_disconnect=False,
        )

        _mark_asyncmy_pre_ping_disconnect(context)

        self.assertTrue(context.is_disconnect)

    def test_non_pre_ping_does_not_mark_business_operation(self) -> None:
        context = SimpleNamespace(
            is_pre_ping=False,
            original_exception=RuntimeError(self._CLOSED_MESSAGE),
            is_disconnect=False,
        )

        _mark_asyncmy_pre_ping_disconnect(context)

        self.assertFalse(context.is_disconnect)

    def test_pre_ping_does_not_mark_unrelated_runtime_error(self) -> None:
        context = SimpleNamespace(
            is_pre_ping=True,
            original_exception=RuntimeError("unrelated runtime failure"),
            is_disconnect=False,
        )

        _mark_asyncmy_pre_ping_disconnect(context)

        self.assertFalse(context.is_disconnect)


if __name__ == "__main__":
    unittest.main()
