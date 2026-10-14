"""Is the teacher's review tracked? Stage receipts of the last six sessions, and the plans and outcomes by playbook.
Read-only, counts only. Run inside the owner's quant-research container."""
import os
from app.database import Database

print("TEACHER_REVIEW_ENABLED in the container environment:", os.environ.get("TEACHER_REVIEW_ENABLED"))
database = Database(); database.open()
with database.transaction() as c:
    c.execute("SET LOCAL statement_timeout = '30s'")
    print("-- stage receipts (teacher stages and the daily review), last 6 sessions")
    for r in c.execute("""SELECT as_of_date, input_summary->>'stage' AS stage, status,
                                 left(coalesce(output_summary->>'reason', error_message, ''), 70) AS reason
                            FROM quant.automation_runs
                           WHERE task_key='post_close_refresh.stage'
                             AND input_summary->>'stage' IN ('teacher_review_roll','teacher_outcome_review','watch_daily_review','xiaojie_outcomes')
                             AND as_of_date >= '2026-10-08' ORDER BY as_of_date DESC, 2""").fetchall():
        print(dict(r))
    tables = [r["table_name"] for r in c.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='quant' AND table_name LIKE 'teacher%' ORDER BY 1").fetchall()]
    print("-- tables:", tables)
