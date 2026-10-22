"""lighttrace: self-hosted tracing for AI apps and agents.

In an app:   import lighttrace; lighttrace.register()          (pip install lighttrace)
The server:  lighttrace                                         (pip install "lighttrace[server]")
"""

__version__ = "0.1.0"

from .client import register  # noqa: E402  (stdlib-only at import; OpenTelemetry loads inside register)

__all__ = ["register"]


def main():
    """The `lighttrace` command: run the server."""
    try:
        from .app import main as serve
    except ImportError as e:
        raise SystemExit(f'lighttrace: the server needs its extra: pip install "lighttrace[server]" ({e.name} is missing)')
    serve()
