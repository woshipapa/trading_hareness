"""The single place in quant-research that knows n8n's own table layout.

``public.workflow_entity`` / ``public.workflow_published_version`` /
``public.execution_entity`` belong to **n8n**, not to us: they are a third
party's internal schema, complete with its ORM's camelCase columns. Reading
them is a deliberate compatibility seam, not part of the quant contract.

It used to be two seams. The same 35-line query was pasted into
``analyst_sync_health_projection.py`` *and* inline into
``routers/analyst_research_reads.py`` — the second one directly contradicting
AGENTS.md's change map, which reserves ``app/routers/`` for "HTTP boundary and
request validation". Two copies of a third party's schema means an n8n upgrade
has to be chased through two files, and the copies can drift apart silently.

So the rule is now: **this module is the only site allowed to name an n8n
table**, and ``scripts/verify_component_boundaries.py`` enforces it against
``config/components.json``'s ``foreign_data_contracts``. Any new call site, or
any new foreign object, fails the boundary check.

Read-only by construction: the SQL is a SELECT and nothing here writes.
"""

from __future__ import annotations

#: The two archive workflows whose operational health the research board shows.
REPORTS_WORKFLOW_ID = "remoteArchiveReports123"
MESSAGES_WORKFLOW_ID = "remoteArchiveMessages123"

#: One canonical query. ``e`` is the resident scheduler's latest trigger run;
#: ``smoke`` is the most recent successful CLI run of the *published* version.
#: CLI runs are useful smoke diagnostics, but n8n 2.33 can leave their audit
#: row in ``running`` after the child process exits, so they can never prove
#: that the resident scheduler executed the published graph.
WORKFLOW_AUDIT_SQL = """SELECT w.id,w.active,w."activeVersionId" AS active_version_id,
                          (w."activeVersionId" IS NOT NULL
                           AND w."activeVersionId"=p."publishedVersionId") AS published,
                          e.status AS latest_execution_status,e."startedAt" AS latest_started_at,
                          e."stoppedAt" AS latest_stopped_at,
                          e."workflowVersionId" AS latest_execution_version_id,
                          smoke.status AS smoke_execution_status,
                          smoke."stoppedAt" AS smoke_execution_at,
                          smoke."workflowVersionId" AS smoke_execution_version_id
                     FROM public.workflow_entity w
                LEFT JOIN public.workflow_published_version p ON p."workflowId"=w.id
                LEFT JOIN LATERAL (
                    SELECT status,"startedAt","stoppedAt","workflowVersionId"
                      FROM public.execution_entity
                     WHERE "workflowId"=w.id AND "deletedAt" IS NULL
                       AND mode='trigger'
                     ORDER BY "startedAt" DESC NULLS LAST,id DESC LIMIT 1
                ) e ON TRUE
                LEFT JOIN LATERAL (
                    SELECT status,"stoppedAt","workflowVersionId"
                      FROM public.execution_entity
                     WHERE "workflowId"=w.id
                       AND mode='cli' AND status='success' AND finished=true
                       AND "workflowVersionId"=w."activeVersionId"
                     ORDER BY "stoppedAt" DESC NULLS LAST,id DESC LIMIT 1
                ) smoke ON TRUE
                    WHERE w.id IN ('remoteArchiveReports123','remoteArchiveMessages123')
                    ORDER BY w.id"""

#: Why a caller got no rows. An isolated quant database simply has no n8n
#: tables, and that must not look the same as "the workflows are disabled" —
#: the board reports the reason instead of an empty list with no explanation.
UNAVAILABLE_NOTICE = ("n8n 的审计表不可读（独立 quant 库没有这些表，或 n8n 改了自己的 schema）；"
                      "工作流健康状态未知，不代表工作流已停用")

__all__ = ["MESSAGES_WORKFLOW_ID", "REPORTS_WORKFLOW_ID", "UNAVAILABLE_NOTICE", "WORKFLOW_AUDIT_SQL"]
