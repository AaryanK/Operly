"""Consistent, verified SQLite backups."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import os
import shutil
import subprocess
from contextlib import closing
from urllib.parse import urlparse,unquote

from packages.database.schema import synchronous_database_url


def sqlite_path(database_url: str) -> Path:
    url = synchronous_database_url(database_url)
    prefix = "sqlite:///"
    if not url.startswith(prefix) or url == "sqlite:///:memory:":
        raise RuntimeError("Backup supports file-backed SQLite databases only")
    return Path(url[len(prefix):]).expanduser().resolve()


def verified_backup(database_url: str, output_dir: Path | None = None) -> Path:
    source = sqlite_path(database_url)
    if not source.is_file():
        raise RuntimeError("SQLite database file does not exist")
    destination_dir = (output_dir or source.parent).resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = destination_dir / f"{source.stem}.backup-{stamp}{source.suffix}"
    if destination.exists():
        raise RuntimeError("Refusing to overwrite an existing backup")
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            destination.unlink(missing_ok=True)
            raise RuntimeError("Backup integrity verification failed")
    return destination


def verified_postgres_dump(database_url:str,output_dir:Path) -> Path:
    executable=shutil.which("pg_dump")
    if not executable:raise RuntimeError("pg_dump is not installed; use a verified Railway PostgreSQL backup")
    parsed=urlparse(database_url.replace("postgresql+asyncpg://","postgresql://",1).replace("postgresql+psycopg://","postgresql://",1))
    if parsed.scheme not in {"postgres","postgresql"} or not parsed.hostname or not parsed.path.strip("/"):
        raise RuntimeError("Invalid PostgreSQL DATABASE_URL")
    output_dir=output_dir.resolve();output_dir.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination=output_dir/f"operly-postgres-{stamp}.dump"
    if destination.exists():raise RuntimeError("Refusing to overwrite an existing PostgreSQL dump")
    environment=os.environ.copy()
    if parsed.password:environment["PGPASSWORD"]=unquote(parsed.password)
    command=[executable,"--format=custom","--file",str(destination),"--host",parsed.hostname,"--port",str(parsed.port or 5432),"--username",unquote(parsed.username or ""),"--dbname",parsed.path.strip("/")]
    result=subprocess.run(command,env=environment,capture_output=True,text=True)
    if result.returncode or not destination.is_file() or destination.stat().st_size==0:
        destination.unlink(missing_ok=True);raise RuntimeError("pg_dump failed")
    return destination
