"""Persistent Git-backed workspaces and reproducible Sandbox execution."""

from __future__ import annotations

import base64
import binascii
import os
import re
import shlex
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath

from .errors import (
    WorkspaceConflictError,
    WorkspaceNotFoundError,
    WorkspacePathError,
    WorkspaceVersionError,
)
from .models import (
    CommandRequest,
    CreateSandboxRequest,
    FileWriteRequest,
    WorkspaceCommitResult,
    WorkspaceFileReadResult,
    WorkspaceFileView,
    WorkspaceRunRequest,
    WorkspaceRunResult,
    WorkspaceSyncResult,
    WorkspaceVersionView,
    WorkspaceView,
    utc_now,
)
from .service import SandboxGatewayService
from .storage import SandboxStore, WorkspaceRecord, WorkspaceRunRecord

_VERSION_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")
_MAX_WORKSPACE_FILE_BYTES = 10_000_000
_MAX_ARCHIVE_BYTES = 100_000_000


class WorkspaceService:
    """Own project source in durable Git repositories, never in ephemeral sandboxes."""

    def __init__(
        self,
        store: SandboxStore,
        storage_root: Path,
        sandbox_service: SandboxGatewayService,
    ) -> None:
        self.store = store
        self.storage_root = storage_root.resolve()
        self.sandbox_service = sandbox_service
        self._lock = threading.RLock()

    def open(self) -> None:
        self.storage_root.mkdir(parents=True, exist_ok=True)

    def _record(self, workspace_id: str) -> WorkspaceRecord:
        record = self.store.get_workspace(workspace_id)
        if record is None:
            raise WorkspaceNotFoundError(f"Workspace {workspace_id!r} was not found")
        return record

    def _repo(self, workspace_id: str) -> Path:
        record = self._record(workspace_id)
        repo = (self.storage_root / record.id).resolve()
        if repo.parent != self.storage_root or not (repo / ".git").is_dir():
            raise WorkspaceConflictError(
                f"Workspace {workspace_id!r} repository is unavailable"
            )
        return repo

    @staticmethod
    def _git(repo: Path, *args: str, binary: bool = False) -> str | bytes:
        command = [
            "git",
            "-c",
            "user.name=Workspace Gateway",
            "-c",
            "user.email=workspace-gateway@localhost",
            "-C",
            str(repo),
            *args,
        ]
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=not binary,
                env={**os.environ, "LC_ALL": "C"},
            )
        except FileNotFoundError as exc:
            raise WorkspaceConflictError("Git is required by Workspace storage") from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="replace") if binary else exc.stderr
            raise WorkspaceConflictError(str(stderr or "Git operation failed").strip()) from exc
        return result.stdout

    @staticmethod
    def _relative_path(raw_path: str) -> PurePosixPath:
        path = PurePosixPath(raw_path.strip())
        if not raw_path.strip() or path.is_absolute() or ".." in path.parts:
            raise WorkspacePathError(
                "Workspace paths must be relative and cannot contain '..'"
            )
        if path.parts[0] == ".git":
            raise WorkspacePathError("The internal .git directory cannot be accessed")
        return path

    def _file_path(self, repo: Path, raw_path: str) -> tuple[PurePosixPath, Path]:
        relative = self._relative_path(raw_path)
        target = (repo / Path(*relative.parts)).resolve()
        if target == repo or repo not in target.parents:
            raise WorkspacePathError("Workspace path escapes the repository")
        return relative, target

    @staticmethod
    def _resolve_version(repo: Path, version: str | None) -> str:
        requested = (version or "HEAD").strip()
        if requested != "HEAD" and not _VERSION_PATTERN.fullmatch(requested):
            raise WorkspaceVersionError("Version must be a commit hash returned by Workspace")
        try:
            result = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "--verify", f"{requested}^{{commit}}"],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise WorkspaceVersionError(f"Workspace version {requested!r} was not found") from exc
        return result.stdout.strip()

    def _is_dirty(self, repo: Path) -> bool:
        return bool(str(self._git(repo, "status", "--porcelain")).strip())

    def _files(self, repo: Path) -> list[WorkspaceFileView]:
        files: list[WorkspaceFileView] = []
        for path in sorted(repo.rglob("*")):
            if ".git" in path.relative_to(repo).parts or not path.is_file():
                continue
            stat = path.stat()
            files.append(
                WorkspaceFileView(
                    path=path.relative_to(repo).as_posix(),
                    size=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=utc_now().tzinfo),
                )
            )
        return files

    def _view(self, record: WorkspaceRecord) -> WorkspaceView:
        repo = self._repo(record.id)
        return WorkspaceView(
            **record.__dict__,
            current_version=self._resolve_version(repo, None),
            dirty=self._is_dirty(repo),
            file_count=len(self._files(repo)),
        )

    def create(self, name: str, description: str = "") -> WorkspaceView:
        name = name.strip()
        description = description.strip()
        if not name:
            raise WorkspaceConflictError("Workspace name cannot be empty")
        if len(name) > 120 or len(description) > 1000:
            raise WorkspaceConflictError("Workspace name or description is too long")
        workspace_id = f"ws_{uuid.uuid4().hex}"
        repo = (self.storage_root / workspace_id).resolve()
        now = utc_now()
        record = WorkspaceRecord(
            id=workspace_id,
            name=name,
            description=description,
            default_branch="main",
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            repo.mkdir(parents=True, exist_ok=False)
            try:
                subprocess.run(
                    ["git", "init", "--initial-branch=main", str(repo)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self._git(repo, "commit", "--allow-empty", "-m", "Initialize workspace")
                self.store.insert_workspace(record)
            except Exception:
                import shutil

                shutil.rmtree(repo, ignore_errors=True)
                raise
        return self._view(record)

    def get(self, workspace_id: str) -> WorkspaceView:
        return self._view(self._record(workspace_id))

    def list(self, limit: int = 100) -> list[WorkspaceView]:
        return [self._view(record) for record in self.store.list_workspaces(limit)]

    def list_files(self, workspace_id: str) -> list[WorkspaceFileView]:
        return self._files(self._repo(workspace_id))

    def write_file(
        self,
        workspace_id: str,
        path: str,
        *,
        text: str | None = None,
        content_base64: str | None = None,
    ) -> WorkspaceFileView:
        repo = self._repo(workspace_id)
        relative, target = self._file_path(repo, path)
        if (text is None) == (content_base64 is None):
            raise WorkspaceConflictError(
                "Provide exactly one of text or content_base64"
            )
        if text is not None:
            content = text.encode()
        else:
            try:
                content = base64.b64decode(content_base64 or "", validate=True)
            except (binascii.Error, ValueError) as exc:
                raise WorkspaceConflictError("content_base64 must be valid Base64") from exc
        if len(content) > _MAX_WORKSPACE_FILE_BYTES:
            raise WorkspaceConflictError(
                f"Workspace files cannot exceed {_MAX_WORKSPACE_FILE_BYTES} bytes"
            )
        with self._lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            self.store.touch_workspace(workspace_id)
        stat = target.stat()
        return WorkspaceFileView(
            path=relative.as_posix(),
            size=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=utc_now().tzinfo),
        )

    def read_file(
        self, workspace_id: str, path: str, version: str | None = None
    ) -> WorkspaceFileReadResult:
        repo = self._repo(workspace_id)
        relative, target = self._file_path(repo, path)
        if version is None:
            if not target.is_file():
                raise WorkspacePathError(f"Workspace file {path!r} was not found")
            content = target.read_bytes()
            resolved_version = None
        else:
            resolved_version = self._resolve_version(repo, version)
            try:
                content = self._git(repo, "show", f"{resolved_version}:{relative.as_posix()}", binary=True)
            except WorkspaceConflictError as exc:
                raise WorkspacePathError(
                    f"Workspace file {path!r} was not found in version {resolved_version[:12]}"
                ) from exc
            assert isinstance(content, bytes)
        return WorkspaceFileReadResult(
            path=relative.as_posix(),
            content_base64=base64.b64encode(content).decode(),
            size=len(content),
            version=resolved_version,
        )

    def commit(self, workspace_id: str, message: str) -> WorkspaceCommitResult:
        repo = self._repo(workspace_id)
        with self._lock:
            dirty = self._is_dirty(repo)
            if dirty:
                self._git(repo, "add", "--all")
                self._git(repo, "commit", "-m", message)
                self.store.touch_workspace(workspace_id)
            version = self.history(workspace_id, limit=1)[0]
        return WorkspaceCommitResult(version=version, created=dirty)

    def history(self, workspace_id: str, limit: int = 50) -> list[WorkspaceVersionView]:
        repo = self._repo(workspace_id)
        separator = "\x1f"
        output = str(
            self._git(
                repo,
                "log",
                f"--max-count={limit}",
                f"--format=%H{separator}%h{separator}%s{separator}%an{separator}%cI",
            )
        )
        versions: list[WorkspaceVersionView] = []
        for line in output.splitlines():
            commit_hash, short_hash, message, author, created_at = line.split(separator, 4)
            versions.append(
                WorkspaceVersionView(
                    version=commit_hash,
                    short_version=short_hash,
                    message=message,
                    author=author,
                    created_at=datetime.fromisoformat(created_at),
                )
            )
        return versions

    async def sync(
        self, workspace_id: str, sandbox_id: str, version: str | None = None
    ) -> WorkspaceSyncResult:
        repo = self._repo(workspace_id)
        resolved_version = self._resolve_version(repo, version)
        archive = self._git(repo, "archive", "--format=tar.gz", resolved_version, binary=True)
        assert isinstance(archive, bytes)
        if len(archive) > _MAX_ARCHIVE_BYTES:
            raise WorkspaceConflictError(
                f"Workspace archive cannot exceed {_MAX_ARCHIVE_BYTES} bytes"
            )
        sandbox_code_dir = str(self.sandbox_service.sandbox_code_dir)
        archive_path = f"{sandbox_code_dir}/.workspace-gateway-source.tar.gz"
        quoted_code_dir = shlex.quote(sandbox_code_dir)
        quoted_archive_path = shlex.quote(archive_path)
        await self.sandbox_service.write_file(
            sandbox_id,
            FileWriteRequest(
                path=archive_path,
                content_base64=base64.b64encode(archive).decode(),
            ),
        )
        extract = await self.sandbox_service.run_command(
            sandbox_id,
            CommandRequest(
                command=(
                    f"find {quoted_code_dir} -mindepth 1 -maxdepth 1 "
                    "! -name .workspace-gateway-source.tar.gz -exec rm -rf -- {} + && "
                    f"tar -xzf {quoted_archive_path} -C {quoted_code_dir} && "
                    f"rm -f {quoted_archive_path}"
                ),
                cwd=sandbox_code_dir,
                timeout_seconds=120,
            ),
        )
        if extract.exit_code != 0:
            raise WorkspaceConflictError(
                f"Workspace extraction failed: {extract.stderr or extract.stdout}"
            )
        return WorkspaceSyncResult(
            workspace_id=workspace_id,
            sandbox_id=sandbox_id,
            version=resolved_version,
            file_count=len(
                str(
                    self._git(
                        repo,
                        "ls-tree",
                        "-r",
                        "--name-only",
                        resolved_version,
                    )
                ).splitlines()
            ),
        )

    async def run(
        self, workspace_id: str, request: WorkspaceRunRequest
    ) -> WorkspaceRunResult:
        self._record(workspace_id)
        if request.version is not None and request.auto_commit:
            raise WorkspaceConflictError(
                "auto_commit cannot be used together with an explicit version"
            )
        if request.auto_commit:
            committed = self.commit(workspace_id, request.commit_message)
            version = committed.version.version
        else:
            version = self._resolve_version(self._repo(workspace_id), request.version)

        created_sandbox = request.sandbox_id is None
        if request.sandbox_id is None:
            template = self.store.get_default_template()
            if template is None:
                raise WorkspaceConflictError("No default sandbox template is configured")
            sandbox = await self.sandbox_service.create(
                CreateSandboxRequest(
                    provider=template.provider,
                    template_id=template.template_id,
                    timeout_seconds=template.default_timeout_seconds,
                    metadata={"workspace_id": workspace_id, "workspace_version": version},
                )
            )
        else:
            sandbox = await self.sandbox_service.get(request.sandbox_id)

        await self.sync(workspace_id, sandbox.id, version)
        result = await self.sandbox_service.run_command(
            sandbox.id,
            CommandRequest(
                command=request.command,
                cwd=str(self.sandbox_service.sandbox_code_dir),
                timeout_seconds=request.timeout_seconds,
                env=request.env,
            ),
        )
        run_id = f"run_{uuid.uuid4().hex}"
        now = utc_now()
        self.store.insert_workspace_run(
            WorkspaceRunRecord(
                id=run_id,
                workspace_id=workspace_id,
                sandbox_id=sandbox.id,
                version=version,
                command=request.command,
                status="succeeded" if result.exit_code == 0 else "failed",
                exit_code=result.exit_code,
                stdout=result.stdout[-20_000:],
                stderr=result.stderr[-20_000:],
                created_at=now,
                updated_at=now,
            )
        )
        return WorkspaceRunResult(
            run_id=run_id,
            workspace_id=workspace_id,
            sandbox=sandbox,
            version=version,
            command=request.command,
            result=result,
            created_sandbox=created_sandbox,
        )
