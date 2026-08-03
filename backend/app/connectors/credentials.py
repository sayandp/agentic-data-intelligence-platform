"""Environment-resolved credentials.

connection_config is a JSON column in Postgres and is returned verbatim by
GET /sources - it must never contain a secret value, only the NAME of an
environment variable to resolve at connection/ingest time. Every credential a
connector needs (a DB connection string, an API token) is passed through this
module rather than read from connection_config directly.
"""

from __future__ import annotations

import os


class MissingCredentialError(ValueError):
    def __init__(self, env_var_name: str):
        self.env_var_name = env_var_name
        super().__init__(
            f"environment variable '{env_var_name}' is not set - it's required to resolve a "
            "credential for this source. connection_config stores the env var's NAME, never "
            "the secret itself, so this must be set in the environment the app runs in."
        )


def resolve_env_var(name: str | None) -> str:
    """Looks up `name` in the environment and returns its value.

    Raises ValueError if `name` itself is missing/empty (a config problem -
    connection_config didn't name a variable at all), or MissingCredentialError
    if the named variable isn't set (an environment problem - the config is
    fine, the deployment is missing a secret).
    """
    if not name:
        raise ValueError("an environment variable name is required but was not provided in connection_config")
    value = os.environ.get(name)
    if not value:
        raise MissingCredentialError(name)
    return value
