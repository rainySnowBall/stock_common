import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_common.backtest.stock_resolver import (
    StockIdentityExtractor,
    StockSymbolResolver,
    normalize_ts_code,
)
from stock_common.search.schemas import SearchResult


class FakeSearchClient:
    def __init__(self) -> None:
        self.queries = []

    def search_sync(self, query, history=None):
        del history
        self.queries.append(query)
        return (
            SearchResult(
                title="中国卫星股票代码",
                snippet="中国卫星 A 股证券代码为 600118，上市地点为上海证券交易所。",
            ),
        )


class FakeIdentityClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = []

    def complete_sync(self, messages):
        self.calls.append(tuple(messages))
        return self.content


class StockResolverTests(unittest.TestCase):
    def test_normalizes_common_a_share_code_formats(self) -> None:
        self.assertEqual(normalize_ts_code("600118"), "600118.SH")
        self.assertEqual(normalize_ts_code("000001"), "000001.SZ")
        self.assertEqual(normalize_ts_code("SH600118"), "600118.SH")
        self.assertEqual(normalize_ts_code("600118.sh"), "600118.SH")

    def test_resolves_stock_name_from_local_stock_basic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "market.sqlite3"
            _write_stock_basic(db_path)

            resolved = StockSymbolResolver(database_path=db_path).resolve(
                "回测 中国卫星 PE 后20%买入"
            )

            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertEqual(resolved.ts_code, "600118.SH")
            self.assertEqual(resolved.name, "中国卫星")
            self.assertEqual(resolved.source, "local_stock_basic")

    def test_resolves_stock_code_from_search_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "missing.sqlite3"
            search = FakeSearchClient()

            resolved = StockSymbolResolver(
                database_path=db_path,
                search_client=search,
            ).resolve("中国卫星")

            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertEqual(resolved.ts_code, "600118.SH")
            self.assertEqual(search.queries, ["中国卫星 A股 股票代码"])

    def test_llm_extracted_stock_code_goes_directly_to_ts_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = FakeIdentityClient('{"stock_name": "", "stock_code": "600519"}')

            resolved = StockSymbolResolver(
                database_path=Path(tmp) / "missing.sqlite3",
                identity_extractor=StockIdentityExtractor(client=identity),
            ).resolve("回测茅台")

            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertEqual(resolved.ts_code, "600519.SH")
            self.assertEqual(resolved.source, "llm_extract")
            self.assertEqual(len(identity.calls), 1)

    def test_llm_extracted_stock_name_uses_search(self) -> None:
        identity = FakeIdentityClient('{"stock_name": "茅台", "stock_code": ""}')
        search = FakeSearchClient()

        resolved = StockSymbolResolver(
            search_client=search,
            identity_extractor=StockIdentityExtractor(client=identity),
        ).resolve("回测茅台")

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.ts_code, "600118.SH")
        self.assertEqual(search.queries, ["茅台 A股 股票代码"])

    def test_llm_empty_identity_returns_none_without_search(self) -> None:
        identity = FakeIdentityClient('{"stock_name": "", "stock_code": ""}')
        search = FakeSearchClient()

        resolved = StockSymbolResolver(
            search_client=search,
            identity_extractor=StockIdentityExtractor(client=identity),
        ).resolve("回测低PE策略")

        self.assertIsNone(resolved)
        self.assertEqual(search.queries, [])


def _write_stock_basic(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_basic (
                ts_code TEXT PRIMARY KEY,
                symbol TEXT,
                name TEXT,
                list_status TEXT
            );
            INSERT INTO stock_basic VALUES ('600118.SH', '600118', '中国卫星', 'L');
            """
        )
        conn.commit()


if __name__ == "__main__":
    unittest.main()
