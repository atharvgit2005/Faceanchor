"""Core utility functions for FaceAnchor pipeline: env loading, logging, and hashing."""

import hashlib
import json
import os
import sys
from typing import Any, Iterable, Optional

from dotenv import load_dotenv
from rich.console import Console

console = Console()

DEFAULT_REQUIRED_KEYS = (
    "SERPAPI_KEY",
    "RPC_URL",
    "CHAIN_ID",
    "PRIVATE_KEY",
    "CONTRACT_ADDRESS",
    "EMBEDDING_SALT",
)


def load_env(required_keys: Optional[Iterable[str]] = None) -> dict[str, str]:
    """Load environment variables from .env and fail loudly if any required key is missing."""
    load_dotenv(override=True)

    keys_to_check = DEFAULT_REQUIRED_KEYS if required_keys is None else required_keys
    missing = [k for k in keys_to_check if not os.getenv(k)]

    if missing:
        console.print(
            "[bold red]Configuration Error:[/bold red] Missing required environment variable(s):"
        )
        for key in missing:
            console.print(f"  - [red]{key}[/red]")
        console.print(
            "[yellow]Please check your .env file against .env.example[/yellow]"
        )
        sys.exit(1)

    return {k: os.environ[k] for k in keys_to_check}


def step(n: int, total: int, title: str) -> None:
    """Print a rich rule denoting a pipeline step."""
    console.print()
    console.rule(f"[bold cyan][{n}/{total}][/bold cyan] [bold white]{title}[/bold white]")


def sha256_bytes(b: bytes) -> str:
    """Compute SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(b).hexdigest()


def canonical_json(obj: Any) -> bytes:
    """Serialize object to deterministic canonical JSON bytes (sorted keys, no whitespace, ascii-safe)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sha256_json(obj: Any) -> str:
    """Compute SHA-256 hex digest of canonically serialized JSON object."""
    return sha256_bytes(canonical_json(obj))
