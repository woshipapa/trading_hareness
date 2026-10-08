"""每个发布单元都必须有一条"不重建镜像"的热更新路径，而且必须 fail closed。

47 的发布模型是：日常源码改动走带版本的 source overlay，复用现有镜像；只有运行
时依赖或基础设施契约变了才发不可变镜像。四个单元里有三个本来就是这样，但
**xhs-intel 不是** —— ``deploy-xhs-intel-edge.sh`` 只要
``XHS_INTEL_TREE_SHA256`` 变了就 ``docker compose build xhs-collector``，也就是
改一行 Python 就重建一次镜像（那个镜像要装 nodejs/npm、pip 依赖，还要对外部
Spider_XHS 跑 npm ci）。

这里把"每个单元都有热更新路径、且都 fail closed"变成会红的检查。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import runpy
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "config" / "components.json").read_text(encoding="utf-8"))
PATHS = MANIFEST.get("hot_update_paths") or {}

#: 单元 → 它必须重建/重启的容器服务，以及必须出现的"不重建镜像"证据。
NO_BUILD_MARKERS = ("--no-build", "--pull never")


class HotUpdateCoverageTests(unittest.TestCase):
    def test_every_release_unit_has_a_declared_hot_update_path(self) -> None:
        declared_units = {entry["unit"] for entry in PATHS.values()}
        expected = set()
        for component in MANIFEST["components"]:
            expected.update(component["release_units"])
        # owner-schema 是数据库迁移单元，本质上不能"热更新"，它要先应用再切代码。
        expected.discard("owner-schema")
        # edge-workflows 是 n8n 工作流导入，不是容器源码。
        expected.discard("edge-workflows")
        self.assertEqual(declared_units, expected,
                         "有发布单元没有声明热更新路径，或声明了不存在的单元")

    def test_each_declared_script_exists_and_is_executable(self) -> None:
        for name, entry in PATHS.items():
            script = ROOT / entry["script"]
            self.assertTrue(script.is_file(), f"{name} 声明的热更新脚本不存在：{entry['script']}")
            self.assertTrue(os.access(script, os.X_OK), f"{entry['script']} 不可执行")

    def test_each_entry_declares_it_reuses_the_existing_image(self) -> None:
        for name, entry in PATHS.items():
            self.assertTrue(entry.get("reuses_image"), f"{name} 没有声明复用现有镜像")
            self.assertTrue(entry.get("full_release_required_when"),
                            f"{name} 没有写明什么情况下必须改走镜像发布")


class NoImageBuildTests(unittest.TestCase):
    """热更新脚本不得构建或拉取运行时镜像。"""

    def _body(self, relative: str) -> str:
        lines = []
        for line in (ROOT / relative).read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            lines.append(line.split(" #", 1)[0])
        return "\n".join(lines)

    def test_no_hot_update_script_builds_a_runtime_image(self) -> None:
        for name, entry in PATHS.items():
            body = self._body(entry["script"])
            self.assertFalse(re.search(r"docker\s+(compose\s+)?build\b", body),
                             f"{name} 的热更新脚本里出现了 docker build —— 热更新不该重建镜像")
            self.assertFalse(re.search(r"docker\s+(compose\s+)?pull\b", body),
                             f"{name} 的热更新脚本里出现了 docker pull")

    def test_container_restarts_are_pinned_to_the_existing_image(self) -> None:
        """只要脚本会重建容器，就必须带 --no-build 和 --pull never。"""
        for name, entry in PATHS.items():
            body = self._body(entry["script"])
            if "docker compose" not in body or "up -d" not in body:
                continue  # 纯静态资源的单元不需要重启容器
            for marker in NO_BUILD_MARKERS:
                self.assertIn(marker, body,
                              f"{name} 重建容器时缺少 {marker}，可能会触发构建或拉取")


class FailClosedTests(unittest.TestCase):
    """依赖面一变必须拒绝，让人去走镜像发布。

    断言一律用布尔形式 —— ``assertIn`` 在失败时会把整份几百行的脚本喷进输出，
    谁撞上这条都得先翻一屏才知道发生了什么。
    """

    def _requires(self, unit: str, *needles: str) -> None:
        body = (ROOT / PATHS[unit]["script"]).read_text(encoding="utf-8")
        for needle in needles:
            self.assertTrue(needle in body,
                            f"{PATHS[unit]['script']} 里缺少 fail-closed 标记：{needle!r}")

    def test_the_relay_gates_on_adapter_dependencies(self) -> None:
        self._requires("feishu-relay", "dependency_fields_match",
                       "publish an immutable image release")

    def test_the_xhs_overlay_gates_on_requirements_and_dockerfile(self) -> None:
        self._requires("xhs-intel", "requirements.txt differs from the running image",
                       "xhs-intel/Dockerfile changed", "exit 42")

    def test_the_owner_path_gates_on_migrations_and_unknown_paths(self) -> None:
        self._requires("quant-research", "quant-service/migrations/versions/*.py")
        body = (ROOT / PATHS["quant-research"]["script"]).read_text(encoding="utf-8")
        self.assertTrue(
            re.search(r"\*\)\s*echo \"full release required for: \$path\" >&2; exit 1", body),
            "owner 发布脚本缺少「未知路径一律拒绝」的兜底分支")


class ProofOfExecutionTests(unittest.TestCase):
    """overlay 发布必须证明**进程真的在跑 overlay**，而不只是文件就位。

    2026-10-08 实测踩到这件事：xhs 的 overlay 发布报了成功，而它的门禁只验了
    /health 有响应、镜像 id 未变、``test -f /app/hotfix/current/edge_api.py``。
    这三条加起来仍然不能排除"compose 的 command 丢了或开关没置上，进程安静地
    跑着 /app/edge_api.py"。和 db-tunnel 当年那个只验自己监听的 healthcheck 是
    同一类毛病：验的是旁证，不是事实。

    两种可接受的证明方式：
      * 应用自己在 /health 里报 ``runtime_source=source-overlay``（中继、bridge）；
      * 直接看 PID 1 的 argv 是不是 overlay 路径（xhs，它的 /health 不报这个）。

    纯静态资源的单元没有进程，豁免。
    """

    #: 没有常驻进程、只发静态资源的单元。
    STATIC_ONLY = {"edge-quant-console"}

    def test_every_process_bearing_overlay_proves_it_is_executing(self) -> None:
        for name, entry in PATHS.items():
            if entry["unit"] in self.STATIC_ONLY:
                continue
            body = (ROOT / entry["script"]).read_text(encoding="utf-8")
            self_report = "runtime_source" in body
            process_argv = "/proc/1/cmdline" in body
            self.assertTrue(
                self_report or process_argv,
                f"{entry['script']} 没有任何「进程确实在跑 overlay」的证明："
                "要么让应用在 /health 里报 runtime_source，要么检查 PID 1 的 argv。"
                "只验文件存在是不够的。")

    def test_a_file_existence_check_alone_is_not_accepted(self) -> None:
        """这条是上面那条的反向说明：光有 test -f 不算证明。"""
        for name, entry in PATHS.items():
            if entry["unit"] in self.STATIC_ONLY:
                continue
            body = (ROOT / entry["script"]).read_text(encoding="utf-8")
            if "test -f /app/hotfix/current" in body:
                self.assertTrue("runtime_source" in body or "/proc/1/cmdline" in body,
                                f"{entry['script']} 只有文件存在检查")


class ComponentOwnershipTests(unittest.TestCase):
    """热更新脚本应该放在它所属组件的目录里。"""

    def test_each_script_lives_with_the_thing_it_releases(self) -> None:
        expected_prefix = {
            "feishu-relay": "feishu-relay/",
            "xhs-intel": "xhs-intel/",
        }
        for name, prefix in expected_prefix.items():
            self.assertTrue(PATHS[name]["script"].startswith(prefix),
                            f"{name} 的热更新脚本不在自己的组件目录下："
                            f"{PATHS[name]['script']}")


class CentralEdgeSecretsTests(unittest.TestCase):
	"""Edge 发布只能消费中央同步后的 ``$edge_dir/.env``。"""

	EDGE_SCRIPTS = (
		"feishu-relay/scripts/edge/deploy-edge-relay-workflows.sh",
		"feishu-relay/scripts/edge/deploy-feishu-relay-edge-release.sh",
		"feishu-relay/scripts/edge/deploy-xhs-intel-edge.sh",
		"feishu-relay/scripts/edge/failback-feishu-relay-to-remote.sh",
		"feishu-relay/scripts/edge/failover-feishu-relay-to-local.sh",
		"feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh",
		"xhs-intel/scripts/hotfix-xhs-intel-edge.sh",
	)

	def test_edge_scripts_default_to_the_central_env(self) -> None:
		for relative in self.EDGE_SCRIPTS:
			body = (ROOT / relative).read_text(encoding="utf-8")
			self.assertTrue(
				"RELAY_EDGE_SECRETS_ENV:-$edge_dir/.env" in body,
				f"{relative} 没有默认读取中央 edge env",
			)
			self.assertFalse(
				"/etc/feishu-relay-edge/secrets.env" in body,
				f"{relative} 仍依赖旧的远端 secrets.env",
			)

	def test_xhs_release_syncs_central_secrets_without_staging_credentials(self) -> None:
		body = (ROOT / "feishu-relay/scripts/edge/deploy-xhs-intel-edge.sh").read_text(
			encoding="utf-8"
		)
		self.assertTrue("config/secrets/sync-secrets.sh\" edge" in body)
		self.assertFalse(".xhs-credentials" in body)
		self.assertFalse('update_env "$secrets_env"' in body)

	def test_edge_database_passwords_do_not_reuse_the_owner_password(self) -> None:
		module = runpy.run_path(str(ROOT / "config/secrets/env-split.py"))
		result = module["build_edge"]({
			"POSTGRES_PASSWORD": "owner-only",
			"EDGE_RELAY_PGUSER": "relay-test",
			"EDGE_RELAY_PGPASSWORD": "relay-only",
			"EDGE_RELAY_PGDATABASE": "relay-db-test",
			"EDGE_QUANT_PGPASSWORD": "quant-only",
		})
		self.assertTrue("RELAY_PGUSER=relay-test" in result)
		self.assertTrue("RELAY_PGPASSWORD=relay-only" in result)
		self.assertTrue("RELAY_PGDATABASE=relay-db-test" in result)
		self.assertTrue("PGPASSWORD=quant-only" in result)
		self.assertFalse("owner-only" in result)

	def test_owner_hotfix_path_defaults_to_the_component_directory(self) -> None:
		module = runpy.run_path(str(ROOT / "config/secrets/env-split.py"))
		default = module["build_owner"]({})
		self.assertIn(
			"QUANT_HOTFIX_HOST_DIR=/home/stockpeer/trading_hareness/hotfix/quant-service",
			default,
		)

		custom = module["build_owner"]({"QUANT_HOTFIX_HOST_DIR": "/srv/quant-hotfix"})
		self.assertIn(
			"QUANT_HOTFIX_HOST_DIR=/home/stockpeer/trading_hareness/hotfix/quant-service",
			custom,
		)
		self.assertNotIn("QUANT_HOTFIX_HOST_DIR=/srv/quant-hotfix", custom)

	def test_owner_database_password_prefers_the_peer_identity(self) -> None:
		module = runpy.run_path(str(ROOT / "config/secrets/env-split.py"))
		separated = module["build_owner"]({
			"POSTGRES_PASSWORD": "local-only",
			"PEER_DB_PASSWORD": "peer-only",
		})
		self.assertIn("PEER_DB_PASSWORD=peer-only", separated)
		self.assertNotIn("local-only", separated)

		fallback = module["build_owner"]({"POSTGRES_PASSWORD": "legacy-owner"})
		self.assertIn("PEER_DB_PASSWORD=legacy-owner", fallback)


if __name__ == "__main__":
    unittest.main()
