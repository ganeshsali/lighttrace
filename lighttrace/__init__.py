"""LightTrace: self-hosted tracing for AI apps and agents.

In an app:   import lighttrace; lighttrace.register()          (pip install lighttrace)
The server:  lighttrace                                         (pip install "lighttrace[server]")
"""

__version__ = "0.1.0"

from .client import register

__all__ = ["register"]


def main():
    """The `lighttrace` command: run the server."""
    try:
        from .app import main as serve
    except ImportError as e:
        raise SystemExit(f'lighttrace: the server needs its extra: pip install "lighttrace[server]" '
                         f"({e.name} is missing)") from None
    serve()
