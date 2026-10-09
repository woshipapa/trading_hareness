from datetime import date
from types import SimpleNamespace
from contextlib import contextmanager
from unittest.mock import patch
import unittest
from app.longhu_critical_close import repair


class CriticalCloseTests(unittest.IsolatedAsyncioTestCase):
    async def run_repair(self,quotes):
        @contextmanager
        def transaction():
            yield SimpleNamespace(execute=lambda *a:SimpleNamespace(fetchall=lambda:[{'symbol':'002074.SZ','name':'国轩高科'}]))
        async def blocking(action,*args,**kwargs):
            kwargs.pop('timeout_seconds',None)
            return action(*args,**kwargs)
        writes=[]
        gateway=object()
        with patch('app.longhu_critical_close.fetch',return_value=(quotes,{'received':len(quotes)})) as fetch:
            result=await repair(date(2026,10,9),db=SimpleNamespace(transaction=transaction),
                source_factory=lambda:SimpleNamespace(_source=gateway),run_public_blocking=blocking,
                run_database_blocking=blocking,persist_rows=lambda c,cap,key,rows,*rest:writes.append((cap,rows)) or len(rows))
        self.assertIs(fetch.call_args.args[0],gateway)
        self.assertEqual(fetch.call_args.args[2],date(2026,10,9))
        return result,writes

    async def test_missing_held_bar_is_repaired_without_fabricated_enrichment(self):
        result,writes=await self.run_repair([{'ts_code':'002074.SZ','trade_date':'20261009','open':31.3,
            'high':31.79,'low':30.2,'close':30.41,'pre_close':31.3,'vol':10,'amount':304100}])
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['repaired'],1)
        self.assertEqual([cap for cap,rows in writes],['daily','stk_limit'])
        self.assertNotIn('adj_factor',writes[0][1][0])
        self.assertEqual(writes[0][1][0]['amount'],'304.1')

    async def test_global_success_cannot_hide_missing_held_symbol(self):
        result,writes=await self.run_repair([])
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['unresolved'],['002074.SZ'])
        self.assertEqual(writes,[])
