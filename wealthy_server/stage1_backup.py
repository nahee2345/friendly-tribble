"""Consistent SQLite online paper-ledger backup with integrity validation.

The authenticated route calls this manually. Backups exclude web login DB,
market DB and user credentials; no restoration or automatic overwrite.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def _secure_folder(parent):
    folder=Path(parent)
    if folder.is_symlink():
        raise ValueError("backup directory symlink")
    folder.mkdir(mode=0o700,parents=True,exist_ok=True)
    if os.name=="posix":
        mode=folder.stat(follow_symlinks=False)
        if mode.st_uid!=os.getuid() or not stat.S_ISDIR(mode.st_mode) or mode.st_mode & 0o077:
            raise ValueError("unsafe backup directory")
    return folder


def online_paper_backup(portfolios,*,directory=None):
    source=portfolios.path
    folder=_secure_folder(directory or source.parent.parent / "backups")
    utc=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    filename="stage1-paper-"+utc+".sqlite3"
    destination=folder/filename
    fd,tmp=tempfile.mkstemp(prefix=".paper-",suffix=".tmp",dir=folder)
    os.fchmod(fd,0o600)
    os.close(fd)
    try:
        # Access source through the existing ownership/symlink protection.
        with closing(portfolios._db()) as source_connection:
            with closing(sqlite3.connect(tmp,timeout=10)) as backup_connection:
                source_connection.backup(backup_connection)
                verified=backup_connection.execute("PRAGMA quick_check").fetchone()
                if not verified or verified[0]!="ok":
                    raise ValueError("backup quick_check failed")
        with open(tmp,"rb") as file:
            digest=hashlib.sha256()
            while True:
                chunk=file.read(1024*1024)
                if not chunk:
                    break
                digest.update(chunk)
        with open(tmp,"rb") as file:
            os.fsync(file.fileno())
        os.replace(tmp,destination)
        os.chmod(destination,0o600)
        dir_fd=os.open(folder,os.O_RDONLY|os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return {"status":"verified","filename":filename,
                "sha256":digest.hexdigest(),"bytes":destination.stat().st_size,
                "includes":["PAPER 가상계좌·입금·거래·승인·보고서"],
                "excludes":["웹 관리자·인증 DB","시장정보 DB","외부 자격증명"],
                "restore_performed":False}
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def list_paper_backups(portfolios,*,directory=None):
    folder=Path(directory or portfolios.path.parent.parent/"backups")
    if not folder.exists():
        return {"items":[]}
    _secure_folder(folder)
    items=[]
    for path in sorted(folder.glob("stage1-paper-*.sqlite3"),reverse=True)[:50]:
        if path.is_symlink() or not path.is_file():
            continue
        info=path.stat(follow_symlinks=False)
        if os.name=="posix" and (info.st_uid!=os.getuid() or info.st_mode & 0o077):
            continue
        items.append({"filename":path.name,"bytes":info.st_size,
                      "modified_utc":datetime.fromtimestamp(info.st_mtime,timezone.utc).isoformat()})
    return {"items":items,"note":"조회는 백업 목록만 제공하며 복원하지 않습니다."}
