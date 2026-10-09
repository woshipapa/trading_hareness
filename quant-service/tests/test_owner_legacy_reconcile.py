"""Frozen, additive schema reconciliation and immutable migration bodies."""
import ast
import hashlib
import unittest
from copy import deepcopy
from pathlib import Path

from migrations.owner_legacy_reconcile import (
    ddl_plan,
    load_contract,
    required_contract,
    required_diff,
)


class OwnerLegacyContractTests(unittest.TestCase):
    def setUp(self):
        self.contract=load_contract()
        self.actual=deepcopy(required_contract(self.contract,final=True))

    def test_complete_shared_schema_needs_zero_ddl(self):
        self.assertEqual(ddl_plan(self.actual,self.contract),[])
        self.assertEqual(required_diff(self.actual,required_contract(self.contract,final=True)),{'missing':{},'different':{}})

    def test_additional_shared_objects_do_not_fail_validation(self):
        self.actual['relations']['additional_runtime_table']=['r',False,None,None,None]
        self.actual['columns']['additional_runtime_table.value']=['text',False,None,'','']
        self.assertEqual(ddl_plan(self.actual,self.contract),[])

    def test_missing_fence_is_additive_and_no_provider_or_data_update(self):
        del self.actual['columns']['runtime_leases.fence']
        plan=ddl_plan(self.actual,self.contract)
        self.assertEqual(len(plan),1)
        self.assertIn('ADD COLUMN IF NOT EXISTS "fence" bigint DEFAULT 0 NOT NULL',plan[0][1])

    def test_wrong_column_definition_fails_before_any_ddl(self):
        self.actual['columns']['runtime_leases.fence'][0]='integer'
        with self.assertRaisesRegex(RuntimeError,'columns:runtime_leases.fence'):
            ddl_plan(self.actual,self.contract)

    def test_missing_archive_view_is_created_without_alter_table_columns(self):
        table='edge_evidence_changes_all'
        del self.actual['relations'][table]
        self.actual['columns']={key:value for key,value in self.actual['columns'].items()
                                if not key.startswith(table+'.')}
        plan=ddl_plan(self.actual,self.contract)
        self.assertEqual(len(plan),1)
        self.assertTrue(plan[0][1].startswith('CREATE VIEW quant."'+table+'" AS '))

    def test_invalid_index_is_not_silently_accepted(self):
        self.actual['indexes']['intraday_order_book_recent_idx'][2]=False
        with self.assertRaisesRegex(RuntimeError,'indexes:intraday_order_book_recent_idx'):
            ddl_plan(self.actual,self.contract)

    def test_only_reviewed_legacy_index_may_be_replaced_concurrently(self):
        self.actual['indexes']['intraday_order_book_recent_idx']=deepcopy(self.contract['replaceable_index_definitions']['intraday_order_book_recent_idx'])
        plan=ddl_plan(self.actual,self.contract)
        self.assertEqual([kind for kind,_sql in plan],['concurrent','concurrent'])
        self.assertTrue(plan[0][1].startswith('DROP INDEX CONCURRENTLY'))
        self.assertTrue(plan[1][1].startswith('CREATE INDEX CONCURRENTLY'))

    def test_predecessor_defers_original_0118_objects(self):
        expected=required_contract(self.contract,final=False)
        for table in self.contract['deferred_0118_relations']:
            self.assertNotIn(table,expected['relations'])
            self.assertFalse(any(key.startswith(table+'.') for key in expected['constraints']))
        self.assertNotIn('intraday_advisory_deliveries.advisory_delivery_kind',expected['constraints'])

    def test_0118_parent_and_0119_parent_are_the_agreed_single_chain(self):
        root=Path(__file__).resolve().parents[1]/'migrations/versions'
        for file,parent in [('20261008_0118_card_observations.py','20261010_ow0120'),
                            ('20261009_0119_personal_review_reminders.py','20261008_0118')]:
            tree=ast.parse((root/file).read_text(encoding='utf-8'))
            assignment=next(node for node in tree.body if isinstance(node,ast.Assign)
                and any(isinstance(target,ast.Name) and target.id=='down_revision' for target in node.targets))
            self.assertEqual(ast.literal_eval(assignment.value),parent)

    def test_original_0118_and_0119_migration_functions_are_unchanged(self):
        root=Path(__file__).resolve().parents[1]/'migrations/versions'
        expected={
            '20261008_0118_card_observations.py':'49d49757ac0bbd613a7544431d28f71a29a2b29536dcb367c27200a8d3960e01',
            '20261009_0119_personal_review_reminders.py':'9ae8b422d555302a1d9711cb5356358bd5d6704ce43469cbbb506d20e7c09387',
        }
        for name,digest in expected.items():
            tree=ast.parse((root/name).read_text(encoding='utf-8'))
            functions=ast.Module(body=[node for node in tree.body
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))],type_ignores=[])
            self.assertEqual(hashlib.sha256(ast.dump(functions,include_attributes=False).encode()).hexdigest(),digest)
        original_0118=(root/'20261008_0118_card_observations.py').read_text(encoding='utf-8').replace(
            'down_revision = "20261010_ow0120"','down_revision = "20261008_sep0002"')
        self.assertEqual(hashlib.sha256(original_0118.encode()).hexdigest(),
                         'd9012ae2166c65214d3685dd5c43adf00e4f62872214a7a2ab447afcded5c88a')
        self.assertEqual(hashlib.sha256((root/'20261009_0119_personal_review_reminders.py').read_text(encoding='utf-8').encode()).hexdigest(),
                         '1f48e7ba80c23f4f5d290c7a10e5cb2395cb5d1f327bea5c259fbfd1749b425b')


if __name__=='__main__':unittest.main()
