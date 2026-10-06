"""Typed exceptions. Each carries the process exit code defined in the CLI spec."""

from __future__ import annotations

EXIT_OK = 0
EXIT_USER_ERROR = 1
EXIT_ENV_ERROR = 2
EXIT_LLM_ERROR = 3


class ContextBridgeError(Exception):
    """Base class for all expected ContextBridge failures."""

    exit_code: int = EXIT_USER_ERROR

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class UserError(ContextBridgeError):
    """Invalid input or usage."""

    exit_code = EXIT_USER_ERROR


class EnvironmentProblemError(ContextBridgeError):
    """The environment is missing something (git, `.context/`, ...)."""

    exit_code = EXIT_ENV_ERROR


class NotInitializedError(EnvironmentProblemError):
    def __init__(self, start: str) -> None:
        super().__init__(
            f"No .context/ directory found in {start} or any parent directory.",
            hint="Run `cb init` at the root of your project.",
        )


class NotAGitRepoError(EnvironmentProblemError):
    def __init__(self, path: str) -> None:
        super().__init__(f"{path} is not inside a git repository.")


class ConfigError(EnvironmentProblemError):
    """Config file exists but cannot be parsed or is invalid."""


class StorageError(EnvironmentProblemError):
    """The state database or state file is unreadable or corrupt."""


class LLMError(ContextBridgeError):
    """LLM compression failed after all fallbacks."""

    exit_code = EXIT_LLM_ERROR
