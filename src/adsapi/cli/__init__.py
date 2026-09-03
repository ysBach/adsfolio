"""CLI package with lazy access to the Click command group."""

__all__ = ["main"]


def __getattr__(name: str):
    """Load the command group only when the package API requests it."""
    if name == "main":
        from .commands import main

        return main
    raise AttributeError(name)
