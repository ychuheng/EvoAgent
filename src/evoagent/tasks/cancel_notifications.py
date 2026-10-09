"""PostgreSQL 取消提示的事务触发器；正文及权限仍以 Task 行为准。"""

CHANNEL = "evoagent_task_cancel"
TRIGGER_NAME = "tasks_cancel_committed"
FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION evoagent_notify_task_cancel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_catalog.pg_notify('evoagent_task_cancel', NEW.id::text);
    RETURN NEW;
END $$
"""
TRIGGER_SQL = """
CREATE TRIGGER tasks_cancel_committed AFTER UPDATE OF cancel_requested ON tasks
FOR EACH ROW WHEN (NOT OLD.cancel_requested AND NEW.cancel_requested)
EXECUTE FUNCTION evoagent_notify_task_cancel()
"""
