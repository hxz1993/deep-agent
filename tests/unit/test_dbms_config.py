from __future__ import annotations

import unittest

from deep_agent.core.config import get_database_url
from deep_agent.options import OPTION


class DbmsConfigTests(unittest.TestCase):
    def test_build_mysql_database_url_from_dbms(self) -> None:
        OPTION.dbms.name = "mysql"
        OPTION.dbms.username = "root"
        OPTION.dbms.password = "secret"
        OPTION.dbms.hostname = "127.0.0.1"
        OPTION.dbms.port = 3306
        OPTION.dbms.db_name = "deep_agent"

        url = get_database_url()
        assert url is not None
        self.assertTrue(url.startswith("mysql+asyncmy://root:secret@127.0.0.1:3306/deep_agent"))


if __name__ == "__main__":
    unittest.main()
