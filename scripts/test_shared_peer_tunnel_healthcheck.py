"""隧道的 healthcheck 必须真的穿过隧道，不能只验自己的监听。

2026-09-30~10-08 这条链断了八天没人发现，就是因为 healthcheck 说谎：

    test: nc -z 127.0.0.1 5432 && nc -z 127.0.0.1 5433 && nc -z 127.0.0.1 5681

而隧道是 ``ssh -L 0.0.0.0:5432:127.0.0.1:15432``。**ssh 一绑上本地端口，
``nc -z`` 就永远成功**，哪怕每一条转发通道都被对端拒绝。于是 db-tunnel 顶着
``Up 8 days (healthy)``、db-batch-tunnel 顶着两周 healthy，日志里刷满
``channel N: open failed: connect failed: Connection refused``，而
``depends_on: db-tunnel: service_healthy`` 照旧放 quant-research 启动，
它就在 ``psycopg_pool.PoolTimeout`` 上崩溃重启了八天，15682/15683 全程空响应。

现在改成发一个 Postgres SSLRequest 再读一个字节。这里锁住三件事：探针确实在
走 Postgres 协议、那串八进制确实是合法的 SSLRequest、以及 healthcheck 的
timeout 留得比 ``nc -w`` 长。
"""
from __future__ import annotations

import pathlib
import re
import struct
import unittest

import yaml

COMPOSE = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "shared-peer" / "compose.yaml"
#: Postgres 的 SSLRequest：int32 长度 8 + int32 请求码 80877103。
SSL_REQUEST = struct.pack("!ii", 8, 80877103)


def _healthcheck(service: str) -> dict:
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return document["services"][service]["healthcheck"]


def _decode_octal(text: str) -> bytes:
    """把 printf 的 ``\\000`` 形式还原成字节，用来证明探针发的就是 SSLRequest。"""
    escapes = re.findall(r"\\([0-7]{1,3})", text)
    return bytes(int(value, 8) for value in escapes)


class TunnelHealthcheckTests(unittest.TestCase):
    def test_the_session_tunnel_speaks_postgres_instead_of_only_binding(self) -> None:
        command = _healthcheck("db-tunnel")["test"][1]
        self.assertIn("printf", command)
        self.assertIn("nc -w", command)
        self.assertEqual(_decode_octal(command), SSL_REQUEST)
        # 读回来的那一个字节必须被判断，否则等于没验
        self.assertRegex(command, r"head -c 1\s*\|\s*grep -q")

    def test_the_batch_tunnel_speaks_postgres_too(self) -> None:
        command = _healthcheck("db-batch-tunnel")["test"][1]
        self.assertEqual(_decode_octal(command), SSL_REQUEST)
        self.assertRegex(command, r"head -c 1\s*\|\s*grep -q")

    def test_a_bare_listener_check_is_no_longer_sufficient(self) -> None:
        """回归闸门：谁把它改回只有 ``nc -z``，这条就红。"""
        for service in ("db-tunnel", "db-batch-tunnel"):
            command = _healthcheck(service)["test"][1]
            stripped = re.sub(r"nc -z [^&|]*", "", command)
            self.assertIn("printf", stripped,
                          f"{service} 的 healthcheck 退回成只验监听了")

    def test_the_timeout_outlasts_the_probe(self) -> None:
        """探针自己要等 ``nc -w N`` 秒，healthcheck 的 timeout 必须比它长，
        否则探针还没读到字节就被判成超时失败。"""
        for service in ("db-tunnel", "db-batch-tunnel"):
            health = _healthcheck(service)
            command = health["test"][1]
            wait = max(int(value) for value in re.findall(r"nc -w (\d+)", command))
            timeout = int(re.fullmatch(r"(\d+)s", str(health["timeout"])).group(1))
            self.assertGreater(timeout, wait, f"{service} timeout 不够探针用")

    def test_the_probe_targets_the_port_that_gates_startup(self) -> None:
        """quant-research 的硬依赖是 5432；批量通道是 5433。"""
        self.assertRegex(_healthcheck("db-tunnel")["test"][1], r"nc -w \d+ 127\.0\.0\.1 5432")
        self.assertRegex(_healthcheck("db-batch-tunnel")["test"][1], r"nc -w \d+ 127\.0\.0\.1 5433")


if __name__ == "__main__":
    unittest.main()
