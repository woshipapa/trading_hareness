"""停用的本地任务不能因为一次 supervisor 重启就自己回来。

watchlist.sync / watchlist.refresh 把本机 5681 的观察池推给 47edge 上已退役的
quant-intraday-edge。目标早已 disable，它们每 5 分钟失败一次、没人看见。
"""
import importlib
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import svc_supervisor  # noqa: E402


def _reload(**env):
    original = {key: os.environ.get(key) for key in env}
    for key, value in env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        return importlib.reload(svc_supervisor)
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class RetiredEdgeWatchlistSyncTests(unittest.TestCase):
    def test_the_edge_watchlist_sync_is_off_by_default(self):
        names = {task["name"] for task in _reload(WATCHLIST_EDGE_SYNC=None).TASKS}
        self.assertNotIn("watchlist.sync", names)
        self.assertNotIn("watchlist.refresh", names)

    def test_one_flag_restores_both_tasks(self):
        names = {task["name"] for task in _reload(WATCHLIST_EDGE_SYNC="1").TASKS}
        self.assertTrue({"watchlist.sync", "watchlist.refresh"} <= names)

    def tearDown(self):
        _reload(WATCHLIST_EDGE_SYNC=None)


if __name__ == "__main__":
    unittest.main()
