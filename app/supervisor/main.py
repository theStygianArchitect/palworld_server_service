"""CLI entrypoint for the palworld-supervisor daemon."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from app.supervisor.server import SupervisorServer

log = logging.getLogger(__name__)

DEFAULT_SOCKET_PATH: str = "/run/palmanager/supervisor.sock"
DEFAULT_LOG_LEVEL: str = "INFO"


def main() -> None:
    """Parse arguments, configure logging, and run the supervisor server."""
    parser = argparse.ArgumentParser(description="Palworld Supervisor Daemon")
    parser.add_argument(
        "--socket-path",
        default=os.environ.get("SUPERVISOR_SOCKET_PATH", DEFAULT_SOCKET_PATH),
        help="Path to the Unix Domain Socket (default: %(default)s)",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("SUPERVISOR_LOG_LEVEL", DEFAULT_LOG_LEVEL),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: %(default)s)",
    )
    parser.add_argument(
        "--allowed-uid",
        type=int,
        default=int(os.environ.get("SUPERVISOR_ALLOWED_UID", "0")),
        help="UID allowed to connect (default: 0, auto-detect palmanager if possible)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    allowed_uid = args.allowed_uid
    if allowed_uid == 0 and os.name == "posix":
        try:
            import pwd  # pylint: disable=import-outside-toplevel  # type: ignore[import-not-found]
            allowed_uid = pwd.getpwnam("palmanager").pw_uid  # type: ignore[attr-defined]
        except KeyError:
            log.warning("User 'palmanager' not found. Allowing only root (UID 0) connections.")
        except ImportError:
            log.warning("pwd module not available. Allowing only root (UID 0) connections.")

    server = SupervisorServer(socket_path=args.socket_path, allowed_uid=allowed_uid)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    if os.name == "posix":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.ensure_future(server.stop()))  # type: ignore[misc]

    try:
        loop.run_until_complete(server.start())
        loop.run_forever()
    except KeyboardInterrupt:
        log.info("Received keyboard interrupt, shutting down")
    except NotImplementedError as e:
        log.error("Initialization failed: %s", e)
    finally:
        loop.run_until_complete(server.stop())
        loop.close()


if __name__ == "__main__":
    main()
