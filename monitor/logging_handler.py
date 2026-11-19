import logging
import threading

_local = threading.local()


class DatabaseLogHandler(logging.Handler):
    """Persist 'monitor.*' log records to LogEntry so they show in the Ops UI.

    Pass extra={"search_id": .., "run_id": ..} to link a record to a search/run.
    Never raises: a logging failure must not break a task.
    """

    def emit(self, record):
        if getattr(_local, "busy", False):
            return
        _local.busy = True
        try:
            from monitor.models import LogEntry

            message = record.getMessage()
            if record.exc_info:
                message = f"{message}\n{self.formatException(record.exc_info)}"
            LogEntry.objects.create(
                level=record.levelname,
                logger=record.name[:100],
                message=message,
                search_id=getattr(record, "search_id", None),
                run_id=getattr(record, "run_id", None),
            )
        except Exception:  # noqa: BLE001 - DB down, table missing, broken txn...
            pass
        finally:
            _local.busy = False

    def formatException(self, exc_info):
        return logging.Formatter().formatException(exc_info)
