"""跨界数据引用必须逐条申报 —— 职责分离从"靠人记得"变成"会红的检查"。

自带的 import / 相对路径检查看不见 SQL。于是这些耦合过去完全没有守卫：

* ``quant.analyst_signals.ingestion_job_id -> public.ingestion_jobs ON DELETE
  CASCADE``：删一条飞书投递任务会级联删掉 quant 的分析师信号；
* n8n 自己的 ``workflow_entity`` / ``workflow_published_version`` /
  ``execution_entity`` 被 quant 直接查，而且**同一段 35 行 SQL 被抄了两份**，
  其中一份内联在 ``app/routers/`` 里 —— AGENTS.md 的变更地图明确规定 routers
  只做 HTTP 边界与入参校验。

现在这三类都在 ``config/components.json`` 的 ``foreign_data_contracts`` 里，
并且**钉到文件**：换个文件抄一份也会红。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest


def _verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_component_boundaries",
        pathlib.Path(__file__).resolve().with_name("verify_component_boundaries.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ForeignContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _verifier()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.mod.ROOT = self.root
        self.mod.COMPONENT_ROOTS = {"quant-research": "quant-service"}
        self.mod.OWNED_SCHEMAS = {"quant-research": ("quant",)}
        (self.root / "quant-service" / "app").mkdir(parents=True)

    def _source(self, name: str, body: str) -> None:
        (self.root / "quant-service" / "app" / name).write_text(body, encoding="utf-8")

    def _manifest(self, *contracts: dict) -> dict:
        return {"foreign_data_contracts": list(contracts)}

    def _contract(self, obj: str, *sites: str, consumer: str = "quant-research") -> dict:
        return {"consumer": consumer, "object": obj, "owner": "n8n", "kind": "read",
                "sites": list(sites), "note": "测试"}

    def test_its_own_schema_is_not_a_crossing(self) -> None:
        self._source("clean.py", 'SQL = "SELECT 1 FROM quant.instruments"')
        self.assertEqual(self.mod.foreign_data_violations(self._manifest()), [])

    def test_an_http_provenance_label_is_not_a_crossing(self) -> None:
        """``source: 'quant.market.events'`` 是出处标签，不是查表。

        feishu-relay 的 baidu-pan 归档脚本里全是这种字样，它其实走 /api/v1 契约。
        没有"必须跟在 SQL 关键字后面"这条限制，这里就会一片假阳性。
        """
        self._source("label.py", "SPEC = {'path': '/api/v1/events/market', 'source': 'quant.market.events'}")
        self.assertEqual(self.mod.foreign_data_violations(self._manifest()), [])

    def test_an_undeclared_crossing_fails(self) -> None:
        self._source("sneaky.py", 'SQL = "SELECT 1 FROM public.workflow_entity"')
        violations = self.mod.foreign_data_violations(self._manifest())
        self.assertEqual(len(violations), 1)
        self.assertIn("public.workflow_entity", violations[0])
        self.assertIn("without a foreign_data_contracts entry", violations[0])

    def test_a_declared_crossing_passes_only_at_its_declared_site(self) -> None:
        self._source("audit.py", 'SQL = "SELECT 1 FROM public.workflow_entity"')
        contract = self._contract("public.workflow_entity", "quant-service/app/audit.py")
        self.assertEqual(self.mod.foreign_data_violations(self._manifest(contract)), [])

    def test_copying_a_declared_object_into_a_second_file_fails(self) -> None:
        """这正是 n8n 那三张表踩过的坑：同一段 SQL 被抄进 routers/。"""
        self._source("audit.py", 'SQL = "SELECT 1 FROM public.workflow_entity"')
        self._source("router_copy.py", 'SQL = "SELECT 1 FROM public.workflow_entity"')
        contract = self._contract("public.workflow_entity", "quant-service/app/audit.py")
        violations = self.mod.foreign_data_violations(self._manifest(contract))
        self.assertEqual(len(violations), 1)
        self.assertIn("undeclared site", violations[0])
        self.assertIn("router_copy.py", violations[0])

    def test_a_foreign_key_is_caught_too(self) -> None:
        self._source("schema.py", 'DDL = "... REFERENCES public.ingestion_jobs(job_id) ON DELETE CASCADE"')
        violations = self.mod.foreign_data_violations(self._manifest())
        self.assertEqual(len(violations), 1)
        self.assertIn("public.ingestion_jobs", violations[0])

    def test_a_stale_contract_is_reported_so_the_boundary_keeps_tightening(self) -> None:
        """耦合拆掉之后，申报必须跟着删，否则白名单只会越积越多。"""
        contract = self._contract("public.workflow_entity", "quant-service/app/audit.py")
        violations = self.mod.foreign_data_violations(self._manifest(contract))
        self.assertEqual(len(violations), 1)
        self.assertIn("stale foreign_data_contracts entry", violations[0])

    def test_a_malformed_contract_is_rejected_by_the_manifest_check(self) -> None:
        for broken in ({"consumer": "quant-research", "object": "public.x", "owner": "n8n",
                        "kind": "read", "note": "no sites"},
                       {"consumer": "quant-research", "object": "public.x", "owner": "n8n",
                        "kind": "read", "note": "", "sites": ["a.py"]}):
            with self.assertRaises(ValueError):
                self.mod._validate_foreign_contracts({"foreign_data_contracts": [broken]})


class ExtendedScanTests(unittest.TestCase):
    """2026-10-08 起：表名常量、shell 里的 psql、清单声明的额外路径都要看见。"""

    def setUp(self) -> None:
        self.mod = _verifier()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.mod.ROOT = self.root
        self.mod.COMPONENT_ROOTS = {"feishu-relay": "feishu-relay"}
        self.mod.OWNED_SCHEMAS = {}

    def _write(self, relative: str, body: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def _manifest(self, *contracts: dict, owned_objects: list[str] | None = None) -> dict:
        component = {"id": "feishu-relay", "paths": ["feishu-relay/**", "workflows/edge-relay/**"]}
        if owned_objects is not None:
            component["owned_data"] = {"objects": owned_objects}
        return {"components": [component], "foreign_data_contracts": list(contracts)}

    def test_a_table_name_constant_is_a_crossing(self) -> None:
        """百度盘历史导出就是这样：先把表名写进常量，再拼 SQL。"""
        self._write("feishu-relay/scripts/export.mjs",
                    "const TABLES = { bars: { table: 'quant.canonical_bars_daily' } };\n")
        violations = self.mod.foreign_data_violations(self._manifest())
        self.assertEqual(len(violations), 1)
        self.assertIn("quant.canonical_bars_daily", violations[0])

    def test_a_table_name_in_a_test_fixture_is_not(self) -> None:
        self._write("feishu-relay/scripts/export.test.mjs", "const fixture = { table: 'quant.example' };\n")
        self.assertEqual(self.mod.foreign_data_violations(self._manifest()), [])

    def test_sql_in_a_shell_script_is_seen_even_after_long_flags(self) -> None:
        """``--quiet`` 是参数不是 SQL 注释；按注释剥掉就会把后面的查询一起剥掉。"""
        self._write("feishu-relay/scripts/edge/failover.sh",
                    'psql --quiet --command "SELECT 1 FROM public.workflow_entity"\n')
        violations = self.mod.foreign_data_violations(self._manifest())
        self.assertEqual(len(violations), 1)
        self.assertIn("public.workflow_entity", violations[0])

    def test_paths_declared_outside_the_component_directory_are_scanned(self) -> None:
        self._write("workflows/edge-relay/flow.json", '{"query": "SELECT 1 FROM quant.market_events"}\n')
        violations = self.mod.foreign_data_violations(self._manifest())
        self.assertEqual(len(violations), 1)
        self.assertIn("workflows/edge-relay/flow.json", violations[0])

    def test_a_component_reading_its_own_table_in_public_is_not_a_crossing(self) -> None:
        self._write("feishu-relay/adapter/ledger.mjs",
                    "await db.query('CREATE TABLE IF NOT EXISTS ingestion_topics (id int)');\n")
        self._write("feishu-relay/scripts/edge/failback.sh", 'psql -c "SELECT 1 FROM public.ingestion_topics"\n')
        manifest = self._manifest(owned_objects=["public.ingestion_topics"])
        self.assertEqual(self.mod.foreign_data_violations(manifest), [])
        self.assertEqual(self.mod.owned_data_violations(manifest), [])

    def test_claiming_a_table_the_component_never_creates_fails(self) -> None:
        """否则"声明别人的表归我"就能让一处跨界引用静默通过。"""
        manifest = self._manifest(owned_objects=["public.workflow_entity"])
        violations = self.mod.owned_data_violations(manifest)
        self.assertEqual(len(violations), 1)
        self.assertIn("nothing in its paths creates it", violations[0])

    def test_one_contract_can_name_several_objects_and_each_must_stay_live(self) -> None:
        self._write("feishu-relay/scripts/export.mjs",
                    "const A = 'quant.market_events'; const B = 'quant.canonical_bars_daily';\n")
        contract = {"consumer": "feishu-relay", "objects": ["quant.market_events", "quant.canonical_bars_daily"],
                    "owner": "quant-research", "kind": "read",
                    "sites": ["feishu-relay/scripts/export.mjs"], "note": "测试"}
        self.assertEqual(self.mod.foreign_data_violations(self._manifest(contract)), [])
        self._write("feishu-relay/scripts/export.mjs", "const A = 'quant.market_events';\n")
        violations = self.mod.foreign_data_violations(self._manifest(contract))
        self.assertEqual(len(violations), 1)
        self.assertIn("stale foreign_data_contracts entry", violations[0])
        self.assertIn("quant.canonical_bars_daily", violations[0])

    def test_a_contract_without_any_object_is_rejected(self) -> None:
        broken = {"consumer": "feishu-relay", "objects": [], "owner": "quant-research", "kind": "read",
                  "sites": ["a.mjs"], "note": "空"}
        with self.assertRaises(ValueError):
            self.mod._validate_foreign_contracts({"foreign_data_contracts": [broken]})


class SharedDatabaseTopologyTests(unittest.TestCase):
    """两个组件落在同一个库里必须申报 —— import 检查看不到拓扑这一半。

    47owner 主栈把 feishu-adapter 和 quant-research* 放进同一个 ``n8n`` 库，
    而清单只把 feishu-relay 记作 ``runtime: 47edge``。事实和声明不一致，
    看清单的人会以为两边早就分库了。shared-peer 那套栈已经拆成两个库。
    """

    def setUp(self) -> None:
        self.mod = _verifier()
        self.manifest = json.loads(
            (pathlib.Path(__file__).resolve().parents[1] / "config" / "components.json")
            .read_text(encoding="utf-8"))

    def test_the_repository_declares_what_it_actually_shares(self) -> None:
        self.assertEqual(self.mod.shared_database_violations(self.manifest), [])

    def test_removing_the_declaration_fails(self) -> None:
        stripped = dict(self.manifest, shared_runtime_databases=[])
        violations = self.mod.shared_database_violations(stripped)
        self.assertTrue(violations)
        self.assertTrue(all("without a shared_runtime_databases entry" in item for item in violations))

    def test_an_incomplete_declaration_fails(self) -> None:
        partial = dict(self.manifest, shared_runtime_databases=[
            dict(entry, components=["quant-research"])
            for entry in self.manifest["shared_runtime_databases"]])
        violations = self.mod.shared_database_violations(partial)
        self.assertTrue(violations)
        self.assertTrue(all("but the manifest declares" in item for item in violations))

    def test_a_stale_declaration_fails(self) -> None:
        """shared-peer 已经分库了；谁再声明它共库就该红。"""
        stale = dict(self.manifest, shared_runtime_databases=[
            *self.manifest["shared_runtime_databases"],
            {"compose": "deploy/shared-peer/compose.yaml", "database": "trading_hareness",
             "components": ["quant-research", "feishu-relay"], "note": "不成立"}])
        violations = self.mod.shared_database_violations(stale)
        self.assertEqual(len(violations), 1)
        self.assertIn("stale shared_runtime_databases entry", violations[0])

    def test_the_peer_stack_is_the_separated_reference(self) -> None:
        """终态长什么样：shared-peer 把 quant 与 n8n 放在两个不同的库。"""
        declared = {entry["compose"] for entry in self.manifest["shared_runtime_databases"]}
        self.assertNotIn("deploy/shared-peer/compose.yaml", declared)


class RealRepositoryTests(unittest.TestCase):
    """真仓库现在必须是干净的，而且 n8n 的表只允许出现在那一个适配器里。"""

    def test_the_repository_passes_its_own_foreign_contract_check(self) -> None:
        mod = _verifier()
        manifest = json.loads(
            (pathlib.Path(__file__).resolve().parents[1] / "config" / "components.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(mod.foreign_data_violations(manifest), [])

    def test_every_n8n_table_lives_in_exactly_one_adapter(self) -> None:
        manifest = json.loads(
            (pathlib.Path(__file__).resolve().parents[1] / "config" / "components.json")
            .read_text(encoding="utf-8"))
        n8n_sites = {site
                     for entry in manifest["foreign_data_contracts"]
                     if entry["owner"] == "n8n"
                     for site in entry["sites"]}
        self.assertEqual(n8n_sites, {"quant-service/app/n8n_workflow_audit.py"})

    def test_quant_data_reaches_the_relay_only_through_the_legacy_export(self) -> None:
        """relay 读 quant 表只允许在那一个遗留导出脚本里；归档主路径走 /api/v1。"""
        manifest = json.loads(
            (pathlib.Path(__file__).resolve().parents[1] / "config" / "components.json")
            .read_text(encoding="utf-8"))
        relay_quant_sites = {site
                             for entry in manifest["foreign_data_contracts"]
                             if entry["consumer"] == "feishu-relay" and entry["owner"] == "quant-research"
                             for site in entry["sites"]}
        self.assertEqual(relay_quant_sites, {"feishu-relay/scripts/media/baidu-pan-history-export.mjs"})
        self.assertEqual(_verifier().owned_data_violations(manifest), [])


if __name__ == "__main__":
    unittest.main()
