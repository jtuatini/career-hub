"""Analytics + reminders + full backup export. Everything computed locally."""

import io
import sqlite3
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import get_db
from app.services import analytics as analytics_service

router = APIRouter(prefix="/api", tags=["insights"])


@router.get("/analytics")
def analytics(db: Session = Depends(get_db)) -> dict:
    return {
        "funnel": analytics_service.funnel(db),
        "by_resume": analytics_service.by_resume(db),
        "reminders": analytics_service.reminders(db),
        "action_queue": analytics_service.action_queue(db),
        "counts": analytics_service.counts(db),
    }


@router.get("/export")
def export_backup() -> StreamingResponse:
    """Zip of the SQLite DB (consistent snapshot via the backup API) plus all
    stored documents. Excludes the AI workspace scratch."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        if settings.db_path.exists():
            # backup() gives a consistent snapshot even mid-write (WAL);
            # serialize() turns the snapshot into one clean database image.
            src = sqlite3.connect(settings.db_path)
            dest = sqlite3.connect(":memory:")
            try:
                src.backup(dest)
                zf.writestr("appbot.sqlite3", dest.serialize())
            finally:
                dest.close()
                src.close()
        files_dir = settings.files_dir
        if files_dir.exists():
            for path in sorted(files_dir.rglob("*")):
                if path.is_file():
                    zf.writestr(f"files/{path.relative_to(files_dir)}", path.read_bytes())
    buffer.seek(0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="appbot-backup-{stamp}.zip"'
        },
    )
