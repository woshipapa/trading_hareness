"""Frozen logical reconciliation used only by ow0120 and ow0121 migrations.

The contract contains schema metadata, never business rows, placement or secrets.
Do not change this module or its contract after these revisions are integrated.
"""
import json
import re
from copy import deepcopy
from pathlib import Path

import sqlalchemy as sa

CONTRACT_PATH=Path(__file__).parent/'contracts/owner_legacy_20261009.json'
CATALOG_QUERIES={
 'relations':"""SELECT c.relname,c.relkind::text,c.relispartition,
    pg_get_expr(c.relpartbound,c.oid),c.reloptions,
    CASE WHEN c.relkind IN ('v','m') THEN pg_get_viewdef(c.oid,true) END
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='quant' AND c.relkind IN ('r','p','v','m') ORDER BY 1""",
 'columns':"""SELECT c.relname||'.'||a.attname,format_type(a.atttypid,a.atttypmod),
    a.attnotnull,pg_get_expr(d.adbin,d.adrelid),a.attidentity::text,a.attgenerated::text
    FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
    WHERE n.nspname='quant' AND c.relkind IN ('r','p','v','m') AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1""",
 'constraints':"""SELECT c.relname||'.'||x.conname,x.contype::text,
    pg_get_constraintdef(x.oid,true),x.convalidated,x.condeferrable,x.condeferred
    FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='quant' ORDER BY 1""",
 'indexes':"""SELECT ci.relname,ct.relname,pg_get_indexdef(i.indexrelid),
    i.indisvalid,i.indisready,i.indisunique,i.indisprimary
    FROM pg_index i JOIN pg_class ci ON ci.oid=i.indexrelid JOIN pg_class ct ON ct.oid=i.indrelid
    JOIN pg_namespace n ON n.oid=ct.relnamespace WHERE n.nspname='quant' ORDER BY 1""",
 'functions':"""SELECT p.proname||'('||pg_get_function_identity_arguments(p.oid)||')',
    pg_get_function_result(p.oid),l.lanname,md5(pg_get_functiondef(p.oid)),p.prosecdef,p.proconfig,p.provolatile::text
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang
    WHERE n.nspname='quant' AND p.prokind IN ('f','p') ORDER BY 1""",
 'triggers':"""SELECT c.relname||'.'||t.tgname,pg_get_triggerdef(t.oid,true),t.tgenabled::text
    FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='quant' AND NOT t.tgisinternal ORDER BY 1""",
 'sequences':"""SELECT c.relname,format_type(s.seqtypid,NULL),s.seqstart,s.seqincrement,
    s.seqmax,s.seqmin,s.seqcache,s.seqcycle FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='quant' ORDER BY 1""",
 'enums':"""SELECT t.typname,array_agg(e.enumlabel ORDER BY e.enumsortorder) FROM pg_type t
    JOIN pg_namespace n ON n.oid=t.typnamespace JOIN pg_enum e ON e.enumtypid=t.oid
    WHERE n.nspname='quant' GROUP BY t.typname ORDER BY 1""",
 'sequence_owners':"""SELECT s.relname,t.relname,a.attname FROM pg_class s
    JOIN pg_namespace n ON n.oid=s.relnamespace JOIN pg_depend d ON d.objid=s.oid AND d.deptype IN ('a','i')
    JOIN pg_class t ON t.oid=d.refobjid JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=d.refobjsubid
    WHERE n.nspname='quant' AND s.relkind='S' ORDER BY 1""",
}


def load_contract():
    return json.loads(CONTRACT_PATH.read_text(encoding='utf-8'))


def required_contract(contract,*,final):
    expected=deepcopy(contract['required'])
    expected['sequence_owners']=deepcopy(contract['sequence_owners'])
    # Operator tuning and physical storage placement are not logical DDL.
    for value in expected['relations'].values():value[3]=None
    if not final:
        deferred=set(contract['deferred_0118_relations'])
        for table in deferred:expected['relations'].pop(table,None)
        for kind in ('columns','constraints','triggers'):
            expected[kind]={key:value for key,value in expected[kind].items() if key.split('.')[0] not in deferred}
        expected['indexes']={key:value for key,value in expected['indexes'].items() if value[0] not in deferred}
        for key in contract['deferred_0119_constraints']:expected['constraints'].pop(key,None)
    return expected


def required_diff(actual,expected):
    missing,different={},{}
    for kind,items in expected.items():
        current=actual.get(kind,{})
        absent=sorted(items.keys()-current.keys())
        wrong=sorted(key for key in items.keys()&current.keys() if current[key]!=items[key])
        if absent:missing[kind]=absent
        if wrong:different[kind]=wrong
    return {'missing':missing,'different':different}


def quote(name):
    if not re.fullmatch(r'[a-z_][a-z0-9_]*',name):raise ValueError('unsafe schema identifier')
    return '"'+name+'"'


def column_definition(name,value):
    data_type,not_null,default,identity,generated=value
    if identity or generated:raise RuntimeError('unsupported missing generated column: '+name)
    return quote(name)+' '+data_type+(' DEFAULT '+default if default is not None else '')+(' NOT NULL' if not_null else '')


def concurrent_index(definition):
    return re.sub(r'^CREATE (UNIQUE )?INDEX ',lambda m:'CREATE '+(m[1] or '')+'INDEX CONCURRENTLY IF NOT EXISTS ',definition,count=1)


def ddl_plan(actual,contract):
    expected=required_contract(contract,final=False)
    normalized=deepcopy(actual)
    for value in normalized.get('relations',{}).values():value[3]=None
    differences=required_diff(normalized,expected)['different']
    allowed=contract['replaceable_index_definitions']
    for kind,keys in differences.items():
        for key in keys:
            if kind!='indexes' or key not in allowed or normalized[kind][key]!=allowed[key]:
                raise RuntimeError('incompatible existing definition: '+kind+':'+key)
    actions=[]
    missing_tables={name for name,value in expected['relations'].items() if name not in normalized['relations'] and value[0]=='r'}
    for name,value in expected['sequences'].items():
        if name in normalized['sequences']:continue
        data_type,start,increment,maximum,minimum,cache,cycle=value
        actions.append(('transaction',f'CREATE SEQUENCE IF NOT EXISTS quant.{quote(name)} AS {data_type} INCREMENT BY {increment} MINVALUE {minimum} MAXVALUE {maximum} START WITH {start} CACHE {cache} '+('CYCLE' if cycle else 'NO CYCLE')))
    for table in sorted(missing_tables):
        columns=[column_definition(name,expected['columns'][table+'.'+name]) for name in contract['column_order'][table]]
        actions.append(('transaction',f'CREATE TABLE IF NOT EXISTS quant.{quote(table)} ('+', '.join(columns)+')'))
    for key,value in expected['columns'].items():
        table,column=key.split('.')
        if expected['relations'][table][0] in {'v','m'}:
            if table in normalized['relations'] and key not in normalized['columns']:
                raise RuntimeError('incompatible existing view columns: '+key)
            continue
        if key not in normalized['columns'] and table not in missing_tables:
            actions.append(('transaction',f'ALTER TABLE quant.{quote(table)} ADD COLUMN IF NOT EXISTS '+column_definition(column,value)))
    for name,(table,column) in expected['sequence_owners'].items():
        if name not in normalized.get('sequence_owners',{}):
            actions.append(('transaction',f'ALTER SEQUENCE quant.{quote(name)} OWNED BY quant.{quote(table)}.{quote(column)}'))
    for key in contract['forbidden_constraints']:
        if key in normalized['constraints']:
            table,name=key.split('.')
            actions.append(('transaction',f'ALTER TABLE quant.{quote(table)} DROP CONSTRAINT IF EXISTS {quote(name)}'))
    order={'p':0,'u':1,'x':2,'c':3,'f':4}
    for key,value in sorted(expected['constraints'].items(),key=lambda item:(order.get(item[1][0],5),item[0])):
        if key in normalized['constraints']:continue
        table,name=key.split('.')
        actions.append(('transaction',f'ALTER TABLE quant.{quote(table)} ADD CONSTRAINT {quote(name)} '+value[1]))
    if 'intraday_advisory_deliveries' in missing_tables:
        actions.append(('transaction',"ALTER TABLE quant.intraday_advisory_deliveries ADD CONSTRAINT intraday_advisory_deliveries_delivery_kind_check CHECK (delivery_kind IN ('signal','analysis'))"))
        actions.append(('transaction',"ALTER TABLE quant.intraday_advisory_deliveries ADD CONSTRAINT intraday_advisory_deliveries_check CHECK ((event_id IS NOT NULL)::integer + (analysis_run_id IS NOT NULL)::integer = 1)"))
    for key,value in expected['functions'].items():
        if key not in normalized['functions']:
            if key not in contract['new_function_ddl']:raise RuntimeError('missing baseline function: '+key)
            actions.append(('transaction',contract['new_function_ddl'][key]))
    for key,value in expected['triggers'].items():
        if key not in normalized['triggers']:actions.append(('transaction',value[0]))
    for table,value in expected['relations'].items():
        if table not in normalized['relations'] and value[0]=='v':
            actions.append(('transaction',f'CREATE VIEW quant.{quote(table)} AS '+value[4]))
    for name,value in expected['indexes'].items():
        if name in normalized['indexes'] and name not in differences.get('indexes',[]):continue
        # PK/UNIQUE constraints above own their backing indexes.
        if value[5] or any(key.endswith('.'+name) and constraint[0] in {'p','u','x'} for key,constraint in expected['constraints'].items()):continue
        if name in differences.get('indexes',[]):actions.append(('concurrent','DROP INDEX CONCURRENTLY IF EXISTS quant.'+quote(name)))
        actions.append(('concurrent',concurrent_index(value[1])))
    return actions


def catalog_snapshot(bind):
    bind.exec_driver_sql('SET LOCAL search_path=pg_catalog')
    return {kind:{row[0]:list(row[1:]) for row in bind.execute(sa.text(query)).all()}
        for kind,query in CATALOG_QUERIES.items()}


def validate(bind,*,final=True):
    contract=load_contract()
    actual=catalog_snapshot(bind)
    for value in actual['relations'].values():value[3]=None
    result=required_diff(actual,required_contract(contract,final=final))
    forbidden=[key for key in contract['forbidden_constraints'] if key in actual['constraints']]
    if result['missing'] or result['different'] or forbidden:
        raise RuntimeError('owner legacy schema validation failed: '+json.dumps({**result,'forbidden_constraints':forbidden},sort_keys=True))
    return result


def reconcile(op):
    contract=load_contract()
    bind=op.get_bind()
    bind.exec_driver_sql("SET LOCAL lock_timeout='3s'")
    bind.exec_driver_sql("SET LOCAL statement_timeout='30s'")
    plan=ddl_plan(catalog_snapshot(bind),contract)
    for kind,statement in plan:
        if kind=='transaction':op.execute(sa.text(statement))
    concurrent=[statement for kind,statement in plan if kind=='concurrent']
    if concurrent:
        with op.get_context().autocommit_block():
            for statement in concurrent:op.execute(sa.text(statement))
    validate(op.get_bind(),final=False)
