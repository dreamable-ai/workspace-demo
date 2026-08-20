"""SQL-backed mapping store supporting PostgreSQL and SQLite."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, inspect, text

from .errors import TemplateConflictError
from .models import ProviderName, SandboxState, SandboxView, utc_now


@dataclass(frozen=True)
class SandboxRecord:
    id: str
    provider: ProviderName
    provider_sandbox_id: str
    state: SandboxState
    template_id: str
    created_at: datetime
    updated_at: datetime
    timeout_seconds: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    env_keys: list[str] = field(default_factory=list)

    def as_view(self) -> SandboxView:
        return SandboxView(**self.__dict__)


@dataclass(frozen=True)
class SandboxTemplateRecord:
    id: str
    provider: ProviderName
    template_id: str
    name: str
    description: str
    default_timeout_seconds: int
    created_at: datetime
    updated_at: datetime
    is_default: bool = False


@dataclass(frozen=True)
class ProviderConfigurationRecord:
    provider: ProviderName
    api_key: str
    timeout_seconds: int
    updated_at: datetime
    mode: str = ""
    domain: str | None = None
    e2b_domain: str | None = None
    base_url: str | None = None


@dataclass(frozen=True)
class WorkspaceRecord:
    id: str
    name: str
    description: str
    default_branch: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class WorkspaceRunRecord:
    id: str
    workspace_id: str
    sandbox_id: str
    version: str
    command: str
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    created_at: datetime
    updated_at: datetime


class SandboxStore:
    def __init__(self, database: Path | str) -> None:
        self.database_url = self._database_url(database)
        self._engine: Engine | None = None
        self._lock = threading.RLock()

    @staticmethod
    def _database_url(database: Path | str) -> str:
        if isinstance(database, Path):
            database.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite+pysqlite:///{database.resolve()}"
        value = str(database)
        if "://" in value:
            return value
        path = Path(value)
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+pysqlite:///{path.resolve()}"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def open(self) -> None:
        connect_args = {"check_same_thread": False} if self.is_sqlite else {}
        self._engine = create_engine(
            self.database_url,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS sandboxes (
                        id VARCHAR(64) PRIMARY KEY,
                        provider VARCHAR(32) NOT NULL,
                        provider_sandbox_id VARCHAR(255) NOT NULL,
                        state VARCHAR(32) NOT NULL,
                        template_id VARCHAR(255) NOT NULL,
                        created_at VARCHAR(40) NOT NULL,
                        updated_at VARCHAR(40) NOT NULL,
                        timeout_seconds INTEGER NULL,
                        metadata_json TEXT NOT NULL,
                        env_keys_json TEXT NOT NULL,
                        UNIQUE(provider, provider_sandbox_id)
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS workspaces (
                        id VARCHAR(64) PRIMARY KEY,
                        name VARCHAR(120) NOT NULL,
                        description TEXT NOT NULL,
                        default_branch VARCHAR(120) NOT NULL,
                        created_at VARCHAR(40) NOT NULL,
                        updated_at VARCHAR(40) NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS workspace_runs (
                        id VARCHAR(64) PRIMARY KEY,
                        workspace_id VARCHAR(64) NOT NULL,
                        sandbox_id VARCHAR(64) NOT NULL,
                        version VARCHAR(64) NOT NULL,
                        command TEXT NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        exit_code INTEGER NULL,
                        stdout TEXT NOT NULL,
                        stderr TEXT NOT NULL,
                        created_at VARCHAR(40) NOT NULL,
                        updated_at VARCHAR(40) NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS provider_configurations (
                        provider VARCHAR(32) PRIMARY KEY,
                        api_key TEXT NOT NULL,
                        mode VARCHAR(16) NOT NULL,
                        domain VARCHAR(512) NULL,
                        e2b_domain VARCHAR(512) NULL,
                        base_url VARCHAR(1024) NULL,
                        timeout_seconds INTEGER NOT NULL,
                        updated_at VARCHAR(40) NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS sandbox_templates (
                        id VARCHAR(64) PRIMARY KEY,
                        provider VARCHAR(32) NOT NULL,
                        template_id VARCHAR(255) NOT NULL,
                        name VARCHAR(120) NOT NULL,
                        description TEXT NOT NULL,
                        default_timeout_seconds INTEGER NOT NULL,
                        is_default BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at VARCHAR(40) NOT NULL,
                        updated_at VARCHAR(40) NOT NULL,
                        UNIQUE(provider, template_id)
                    )
                    """
                )
            )
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        sandbox_columns = {
            column["name"] for column in inspect(self.engine).get_columns("sandboxes")
        }
        sandbox_migrations = {
            "timeout_seconds": "ALTER TABLE sandboxes ADD COLUMN timeout_seconds INTEGER",
            "metadata_json": (
                "ALTER TABLE sandboxes ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
            ),
            "env_keys_json": (
                "ALTER TABLE sandboxes ADD COLUMN env_keys_json TEXT NOT NULL DEFAULT '[]'"
            ),
        }
        template_columns = {
            column["name"]
            for column in inspect(self.engine).get_columns("sandbox_templates")
        }
        with self.engine.begin() as connection:
            for column, statement in sandbox_migrations.items():
                if column not in sandbox_columns:
                    connection.execute(text(statement))
            if "is_default" not in template_columns:
                connection.execute(
                    text(
                        "ALTER TABLE sandbox_templates "
                        "ADD COLUMN is_default BOOLEAN NOT NULL DEFAULT FALSE"
                    )
                )

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            raise RuntimeError("SandboxStore.open() must be called first")
        return self._engine

    @staticmethod
    def _parameters(record: SandboxRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "provider": record.provider.value,
            "provider_sandbox_id": record.provider_sandbox_id,
            "state": record.state.value,
            "template_id": record.template_id,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "timeout_seconds": record.timeout_seconds,
            "metadata_json": json.dumps(
                record.metadata, ensure_ascii=False, sort_keys=True
            ),
            "env_keys_json": json.dumps(record.env_keys, ensure_ascii=False),
        }

    @staticmethod
    def _record(row: Any) -> SandboxRecord:
        return SandboxRecord(
            id=row["id"],
            provider=ProviderName(row["provider"]),
            provider_sandbox_id=row["provider_sandbox_id"],
            state=SandboxState(row["state"]),
            template_id=row["template_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            timeout_seconds=row["timeout_seconds"],
            metadata=json.loads(row["metadata_json"] or "{}"),
            env_keys=json.loads(row["env_keys_json"] or "[]"),
        )

    def insert(self, record: SandboxRecord) -> None:
        with self._lock, self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO sandboxes (
                        id, provider, provider_sandbox_id, state,
                        template_id, created_at, updated_at,
                        timeout_seconds, metadata_json, env_keys_json
                    ) VALUES (
                        :id, :provider, :provider_sandbox_id, :state,
                        :template_id, :created_at, :updated_at,
                        :timeout_seconds, :metadata_json, :env_keys_json
                    )
                    """
                ),
                self._parameters(record),
            )

    def get(self, gateway_id: str) -> SandboxRecord | None:
        with self._lock, self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM sandboxes WHERE id = :id"), {"id": gateway_id}
            ).mappings().first()
        return self._record(row) if row is not None else None

    def update_state(self, gateway_id: str, state: SandboxState) -> SandboxRecord | None:
        now = utc_now().isoformat()
        with self._lock, self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE sandboxes SET state = :state, updated_at = :updated_at "
                    "WHERE id = :id"
                ),
                {"state": state.value, "updated_at": now, "id": gateway_id},
            )
        return self.get(gateway_id)

    def list(self, limit: int = 100) -> list[SandboxRecord]:
        with self._lock, self.engine.connect() as connection:
            rows = connection.execute(
                text("SELECT * FROM sandboxes ORDER BY created_at DESC LIMIT :limit"),
                {"limit": limit},
            ).mappings().all()
        return [self._record(row) for row in rows]

    @staticmethod
    def _provider_configuration_record(row: Any) -> ProviderConfigurationRecord:
        return ProviderConfigurationRecord(
            provider=ProviderName(row["provider"]),
            api_key=row["api_key"],
            mode=row["mode"] or "",
            domain=row["domain"],
            e2b_domain=row["e2b_domain"],
            base_url=row["base_url"],
            timeout_seconds=row["timeout_seconds"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def upsert_provider_configuration(
        self, record: ProviderConfigurationRecord
    ) -> None:
        parameters = {
            "provider": record.provider.value,
            "api_key": record.api_key,
            "mode": record.mode,
            "domain": record.domain,
            "e2b_domain": record.e2b_domain,
            "base_url": record.base_url,
            "timeout_seconds": record.timeout_seconds,
            "updated_at": record.updated_at.isoformat(),
        }
        with self._lock, self.engine.begin() as connection:
            existing = connection.execute(
                text(
                    "SELECT provider FROM provider_configurations "
                    "WHERE provider = :provider"
                ),
                {"provider": record.provider.value},
            ).first()
            if existing is None:
                connection.execute(
                    text(
                        """
                        INSERT INTO provider_configurations (
                            provider, api_key, mode, domain, e2b_domain, base_url,
                            timeout_seconds, updated_at
                        ) VALUES (
                            :provider, :api_key, :mode, :domain, :e2b_domain, :base_url,
                            :timeout_seconds, :updated_at
                        )
                        """
                    ),
                    parameters,
                )
            else:
                connection.execute(
                    text(
                        """
                        UPDATE provider_configurations SET
                            api_key = :api_key,
                            mode = :mode,
                            domain = :domain,
                            e2b_domain = :e2b_domain,
                            base_url = :base_url,
                            timeout_seconds = :timeout_seconds,
                            updated_at = :updated_at
                        WHERE provider = :provider
                        """
                    ),
                    parameters,
                )

    def get_provider_configuration(
        self, provider: ProviderName
    ) -> ProviderConfigurationRecord | None:
        with self._lock, self.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT * FROM provider_configurations WHERE provider = :provider"
                ),
                {"provider": provider.value},
            ).mappings().first()
        return self._provider_configuration_record(row) if row is not None else None

    def list_provider_configurations(self) -> list[ProviderConfigurationRecord]:
        with self._lock, self.engine.connect() as connection:
            rows = connection.execute(
                text("SELECT * FROM provider_configurations ORDER BY provider")
            ).mappings().all()
        return [self._provider_configuration_record(row) for row in rows]

    @staticmethod
    def _template_record(row: Any) -> SandboxTemplateRecord:
        return SandboxTemplateRecord(
            id=row["id"],
            provider=ProviderName(row["provider"]),
            template_id=row["template_id"],
            name=row["name"],
            description=row["description"] or "",
            default_timeout_seconds=row["default_timeout_seconds"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            is_default=bool(row["is_default"]),
        )

    def insert_template(self, record: SandboxTemplateRecord) -> None:
        with self._lock, self.engine.begin() as connection:
            duplicate = connection.execute(
                text(
                    "SELECT id FROM sandbox_templates "
                    "WHERE provider = :provider AND template_id = :template_id"
                ),
                {"provider": record.provider.value, "template_id": record.template_id},
            ).first()
            if duplicate is not None:
                raise TemplateConflictError(
                    f"Template {record.template_id!r} already exists for {record.provider.value}"
                )
            should_be_default = record.is_default
            if not should_be_default and record.template_id == "code-interpreter":
                existing_default = connection.execute(
                    text("SELECT id FROM sandbox_templates WHERE is_default = TRUE LIMIT 1")
                ).first()
                should_be_default = existing_default is None
            if should_be_default:
                connection.execute(text("UPDATE sandbox_templates SET is_default = FALSE"))
            connection.execute(
                text(
                    """
                    INSERT INTO sandbox_templates (
                        id, provider, template_id, name, description,
                        default_timeout_seconds, is_default, created_at, updated_at
                    ) VALUES (
                        :id, :provider, :template_id, :name, :description,
                        :default_timeout_seconds, :is_default, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "id": record.id,
                    "provider": record.provider.value,
                    "template_id": record.template_id,
                    "name": record.name,
                    "description": record.description,
                    "default_timeout_seconds": record.default_timeout_seconds,
                    "is_default": should_be_default,
                    "created_at": record.created_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                },
            )

    def get_template(self, template_catalog_id: str) -> SandboxTemplateRecord | None:
        with self._lock, self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM sandbox_templates WHERE id = :id"),
                {"id": template_catalog_id},
            ).mappings().first()
        return self._template_record(row) if row is not None else None

    def list_templates(self) -> list[SandboxTemplateRecord]:
        with self._lock, self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT * FROM sandbox_templates "
                    "ORDER BY is_default DESC, provider, name, template_id"
                )
            ).mappings().all()
        return [self._template_record(row) for row in rows]

    def get_default_template(self) -> SandboxTemplateRecord | None:
        with self._lock, self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM sandbox_templates WHERE is_default = TRUE LIMIT 1")
            ).mappings().first()
        return self._template_record(row) if row is not None else None

    def set_default_template(self, template_catalog_id: str) -> SandboxTemplateRecord | None:
        with self._lock, self.engine.begin() as connection:
            exists = connection.execute(
                text("SELECT id FROM sandbox_templates WHERE id = :id"),
                {"id": template_catalog_id},
            ).first()
            if exists is None:
                return None
            connection.execute(text("UPDATE sandbox_templates SET is_default = FALSE"))
            connection.execute(
                text(
                    "UPDATE sandbox_templates SET is_default = TRUE, updated_at = :updated_at "
                    "WHERE id = :id"
                ),
                {"id": template_catalog_id, "updated_at": utc_now().isoformat()},
            )
        return self.get_template(template_catalog_id)

    def ensure_default_template(
        self, preferred_template_id: str = "code-interpreter"
    ) -> SandboxTemplateRecord | None:
        current = self.get_default_template()
        if current is not None:
            return current
        with self._lock, self.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT id FROM sandbox_templates WHERE template_id = :template_id "
                    "ORDER BY CASE WHEN provider = 'pai' THEN 0 ELSE 1 END, created_at LIMIT 1"
                ),
                {"template_id": preferred_template_id},
            ).first()
        if row is None:
            return None
        return self.set_default_template(row[0])

    def delete_template(self, template_catalog_id: str) -> bool:
        with self._lock, self.engine.begin() as connection:
            result = connection.execute(
                text("DELETE FROM sandbox_templates WHERE id = :id"),
                {"id": template_catalog_id},
            )
        deleted = bool(result.rowcount)
        if deleted:
            self.ensure_default_template()
        return deleted

    @staticmethod
    def _workspace_record(row: Any) -> WorkspaceRecord:
        return WorkspaceRecord(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            default_branch=row["default_branch"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def insert_workspace(self, record: WorkspaceRecord) -> None:
        with self._lock, self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO workspaces (
                        id, name, description, default_branch, created_at, updated_at
                    ) VALUES (
                        :id, :name, :description, :default_branch, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "id": record.id,
                    "name": record.name,
                    "description": record.description,
                    "default_branch": record.default_branch,
                    "created_at": record.created_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                },
            )

    def get_workspace(self, workspace_id: str) -> WorkspaceRecord | None:
        with self._lock, self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM workspaces WHERE id = :id"), {"id": workspace_id}
            ).mappings().first()
        return self._workspace_record(row) if row is not None else None

    def list_workspaces(self, limit: int = 100) -> list[WorkspaceRecord]:
        with self._lock, self.engine.connect() as connection:
            rows = connection.execute(
                text("SELECT * FROM workspaces ORDER BY updated_at DESC LIMIT :limit"),
                {"limit": limit},
            ).mappings().all()
        return [self._workspace_record(row) for row in rows]

    def touch_workspace(self, workspace_id: str) -> WorkspaceRecord | None:
        with self._lock, self.engine.begin() as connection:
            connection.execute(
                text("UPDATE workspaces SET updated_at = :updated_at WHERE id = :id"),
                {"id": workspace_id, "updated_at": utc_now().isoformat()},
            )
        return self.get_workspace(workspace_id)

    def insert_workspace_run(self, record: WorkspaceRunRecord) -> None:
        with self._lock, self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO workspace_runs (
                        id, workspace_id, sandbox_id, version, command, status,
                        exit_code, stdout, stderr, created_at, updated_at
                    ) VALUES (
                        :id, :workspace_id, :sandbox_id, :version, :command, :status,
                        :exit_code, :stdout, :stderr, :created_at, :updated_at
                    )
                    """
                ),
                {
                    **record.__dict__,
                    "created_at": record.created_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                },
            )

    def list_workspace_runs(
        self, workspace_id: str, limit: int = 50
    ) -> list[WorkspaceRunRecord]:
        with self._lock, self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT * FROM workspace_runs WHERE workspace_id = :workspace_id "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {"workspace_id": workspace_id, "limit": limit},
            ).mappings().all()
        return [
            WorkspaceRunRecord(
                id=row["id"],
                workspace_id=row["workspace_id"],
                sandbox_id=row["sandbox_id"],
                version=row["version"],
                command=row["command"],
                status=row["status"],
                exit_code=row["exit_code"],
                stdout=row["stdout"] or "",
                stderr=row["stderr"] or "",
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]
