"""每个运行面都要登记；工作站上会写 owner 的任务不能悄悄出现。"""
from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import verify_runtime_hosts as hosts  # noqa: E402

MANIFEST = json.loads((pathlib.Path(__file__).resolve().parents[1] / "config" / "components.json")
                      .read_text(encoding="utf-8"))


def fake_tasks(default: set[str], optional: set[str] = frozenset()):
    def names(*, all_optional: bool) -> set[str]:
        return default | optional if all_optional else set(default)
    return names


class RuntimeHostTests(unittest.TestCase):
    def manifest(self) -> dict:
        return copy.deepcopy(MANIFEST)

    def test_the_repository_declares_every_host_and_task(self) -> None:
        self.assertEqual(hosts.host_violations(MANIFEST), [])

    def test_a_component_on_an_undeclared_host_fails(self) -> None:
        manifest = self.manifest()
        manifest["components"][0]["runtime"] = "somewhere-new"
        violations = hosts.host_violations(manifest, fake_tasks({"teacher.cycle"}))
        self.assertTrue(any("undeclared host 'somewhere-new'" in item for item in violations))

    def test_a_host_must_list_the_components_that_run_on_it(self) -> None:
        manifest = self.manifest()
        owner = next(host for host in manifest["hosts"] if host["id"] == "47owner")
        owner["components"] = []
        violations = hosts.host_violations(manifest, fake_tasks({"teacher.cycle"}))
        self.assertTrue(any("does not list component quant-research" in item for item in violations))

    def test_an_unclassified_supervisor_task_fails(self) -> None:
        """一个新加的任务可能会写 owner；不说清楚就不许上。"""
        violations = hosts.host_violations(self.manifest(), fake_tasks({"teacher.cycle", "owner.import-something"}))
        self.assertEqual(len(violations), 1)
        self.assertIn("owner.import-something is not classified", violations[0])

    def test_an_optional_task_must_be_classified_too(self) -> None:
        violations = hosts.host_violations(self.manifest(), fake_tasks({"teacher.cycle"}, {"new.optional"}))
        self.assertTrue(any("new.optional is not classified" in item for item in violations))

    def test_a_retired_task_must_not_start_by_default(self) -> None:
        violations = hosts.host_violations(self.manifest(), fake_tasks({"teacher.cycle", "watchlist.sync"}))
        self.assertEqual(violations, ["supervisor task watchlist.sync is classified retired but starts by default"])

    def test_patterns_classify_families_of_tasks(self) -> None:
        self.assertEqual(hosts.host_violations(self.manifest(), fake_tasks({"paperkb.anything-new"})), [])

    def test_an_unknown_class_is_rejected(self) -> None:
        manifest = self.manifest()
        workstation = next(host for host in manifest["hosts"] if "supervisor_tasks" in host)
        workstation["supervisor_tasks"]["teacher.cycle"] = "writes-a-bit"
        violations = hosts.host_violations(manifest, fake_tasks({"teacher.cycle"}))
        self.assertTrue(any("unknown class 'writes-a-bit'" in item for item in violations))

    def test_the_owner_writers_are_exactly_the_ones_named(self) -> None:
        workstation = next(host for host in MANIFEST["hosts"] if "supervisor_tasks" in host)
        writers = {name for name, value in workstation["supervisor_tasks"].items() if value == "owner-writer"}
        self.assertEqual(writers, {"teacher.cycle"})


if __name__ == "__main__":
    unittest.main()
