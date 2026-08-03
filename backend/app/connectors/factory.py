from __future__ import annotations

from pydantic import ValidationError

from app.connectors.api_connector import APIConnector, APIConnectorConfig
from app.connectors.base import BaseConnector
from app.connectors.file_connector import FileConnector
from app.connectors.sql_connector import SQLConnector, SQLConnectorConfig
from app.models import DataSource


def build_connector(source: DataSource) -> BaseConnector:
    config = source.connection_config or {}

    if source.type == "file":
        path = config.get("path")
        if not path:
            raise ValueError("connection_config.path is required for file sources")
        return FileConnector(source_id=source.id, file_path=path, sheet=config.get("sheet"))

    if source.type == "sql":
        try:
            sql_config = SQLConnectorConfig.model_validate(config)
        except ValidationError as exc:
            raise ValueError(f"invalid connection_config for a sql source: {exc}") from exc
        return SQLConnector(source_id=source.id, **sql_config.model_dump())

    if source.type == "api":
        try:
            api_config = APIConnectorConfig.model_validate(config)
        except ValidationError as exc:
            raise ValueError(f"invalid connection_config for an api source: {exc}") from exc
        return APIConnector(source_id=source.id, config=api_config)

    raise ValueError(f"connector for source type '{source.type}' is not implemented yet")
