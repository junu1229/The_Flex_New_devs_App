import inspect
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.core import database_pool as database_pool_module
from app.core.database_pool import DatabasePool
from app.services import reservations


class DatabasePoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_uses_configured_async_url_and_async_pool(self):
        url_cases = (
            (
                "postgresql://user:password@database:5432/propertyflow",
                "postgresql+asyncpg://user:password@database:5432/propertyflow",
            ),
            (
                "postgres://user:password@database:5432/propertyflow"
                "?application_name=postgresql://worker",
                "postgresql+asyncpg://user:password@database:5432/propertyflow"
                "?application_name=postgresql://worker",
            ),
            (
                "postgresql+asyncpg://user:password@database:5432/propertyflow",
                "postgresql+asyncpg://user:password@database:5432/propertyflow",
            ),
        )

        for configured_url, expected_url in url_cases:
            with self.subTest(configured_url=configured_url):
                pool = DatabasePool()
                engine = object()
                session_factory = object()

                with (
                    patch.object(
                        database_pool_module.settings,
                        "database_url",
                        configured_url,
                    ),
                    patch.object(
                        database_pool_module.settings,
                        "database_pool_size",
                        7,
                    ),
                    patch.object(
                        database_pool_module.settings,
                        "database_max_overflow",
                        11,
                    ),
                    patch.object(
                        database_pool_module.settings,
                        "database_pool_recycle",
                        1234,
                    ),
                    patch.object(
                        database_pool_module,
                        "create_async_engine",
                        return_value=engine,
                    ) as create_engine,
                    patch.object(
                        database_pool_module,
                        "async_sessionmaker",
                        return_value=session_factory,
                    ) as create_session_factory,
                ):
                    await pool.initialize()

                create_engine.assert_called_once_with(
                    expected_url,
                    pool_size=7,
                    max_overflow=11,
                    pool_pre_ping=True,
                    pool_recycle=1234,
                    echo=False,
                )
                create_session_factory.assert_called_once_with(
                    bind=engine,
                    class_=database_pool_module.AsyncSession,
                    expire_on_commit=False,
                )
                self.assertIs(pool.engine, engine)
                self.assertIs(pool.session_factory, session_factory)

    async def test_initialize_is_idempotent(self):
        pool = DatabasePool()
        engine = object()
        session_factory = object()

        with (
            patch.object(
                database_pool_module.settings,
                "database_url",
                "postgresql://user:password@database/propertyflow",
            ),
            patch.object(
                database_pool_module,
                "create_async_engine",
                return_value=engine,
            ) as create_engine,
            patch.object(
                database_pool_module,
                "async_sessionmaker",
                return_value=session_factory,
            ) as create_session_factory,
        ):
            await pool.initialize()
            await pool.initialize()

        create_engine.assert_called_once()
        create_session_factory.assert_called_once()

    async def test_initialize_reraises_engine_failure(self):
        pool = DatabasePool()
        engine_failure = RuntimeError("database unavailable")

        with (
            patch.object(
                database_pool_module.settings,
                "database_url",
                "postgresql://user:password@database/propertyflow",
            ),
            patch.object(
                database_pool_module,
                "create_async_engine",
                side_effect=engine_failure,
            ),
        ):
            with self.assertLogs(database_pool_module.logger, level="ERROR") as logs:
                with self.assertRaises(RuntimeError) as raised:
                    await pool.initialize()

        self.assertIs(raised.exception, engine_failure)
        self.assertIn("Database pool initialization failed", logs.output[0])
        self.assertIsNone(pool.engine)
        self.assertIsNone(pool.session_factory)

    async def test_get_session_returns_factory_result_directly(self):
        pool = DatabasePool()
        session = object()
        pool.session_factory = Mock(return_value=session)

        result = pool.get_session()
        try:
            self.assertFalse(inspect.isawaitable(result))
            self.assertIs(result, session)
        finally:
            if inspect.iscoroutine(result):
                result.close()

        pool.session_factory.assert_called_once_with()


class ReservationDatabaseFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_failure_propagates_instead_of_returning_mock_data(self):
        database_failure = RuntimeError("database unavailable")
        initialize = AsyncMock(side_effect=database_failure)

        with patch.object(database_pool_module.db_pool, "initialize", initialize):
            with self.assertRaises(RuntimeError) as raised:
                await reservations.calculate_total_revenue("prop-001", "tenant-a")

        self.assertIs(raised.exception, database_failure)
        initialize.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
