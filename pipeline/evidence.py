"""Canonical deterministic evidence record construction."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.panel import Panel
from rich.table import Table

from pipeline.imaging import image_hashes, normalize_image
from pipeline.util import console, sha256_json


def to_bytes32_hex(val: str) -> str:
    """Format a hex string as a 32-byte 0x-prefixed hex string (64 characters + 0x), left-padding if needed."""
    clean = val.lower().removeprefix("0x")
    if len(clean) > 64:
        raise ValueError(f"Value exceeds 32 bytes (64 hex chars): {val}")
    padded = clean.zfill(64)
    return "0x" + padded


def build_record(
    face_json: dict[str, Any],
    match_json: dict[str, Any],
    input_image_path: str,
    out_dir: str = "out",
    candidates_considered: int = 1,
) -> dict[str, Any]:
    """Assemble deterministic canonical evidence record (schema version 1) and calculate on-chain bytes32 hashes."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # 1. Normalize input image and compute input hashes
    norm_input = normalize_image(input_image_path, out_path / "input.jpg")
    inp_hashes = image_hashes(norm_input)

    # 2. Timestamp (deterministic with FACEANCHOR_FIXED_TIME override)
    fixed_time = os.getenv("FACEANCHOR_FIXED_TIME")
    if fixed_time:
        try:
            ts = int(fixed_time)
            created_at = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            created_at = fixed_time
    else:
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 3. Build deterministic record dict (strictly NO local paths, NO salt, NO raw embeddings)
    record = {
        "schema": "faceanchor/1",
        "created_at": created_at,
        "input": {
            "sha256": inp_hashes["sha256"],
            "phash": inp_hashes["phash"],
        },
        "face": {
            "model": face_json["model"],
            "detector": face_json["detector"],
            "embedding_commitment": face_json["embedding_commitment"],
        },
        "search": {
            "engines": sorted(set(match_json.get("engines", []))),
            "candidates_considered": candidates_considered,
        },
        "match": {
            "post_url": match_json["post_url"],
            "domain": match_json["domain"],
            "image_url": match_json["image_url"],
            "image_sha256": match_json["image_sha256"],
            "image_phash": match_json["image_phash"],
            "face_distance": float(round(match_json["face_distance"], 4)),
            "engines": match_json.get("engines", []),
            **({"note": match_json["note"]} if match_json.get("note") else {}),
        },
    }

    evidence_hash = sha256_json(record)
    evidence_hash_bytes32 = to_bytes32_hex(evidence_hash)
    image_hash_bytes32 = to_bytes32_hex(match_json["image_sha256"])
    phash_bytes32 = to_bytes32_hex(match_json["image_phash"])

    envelope = {
        "record": record,
        "evidence_hash": evidence_hash,
        "bytes32": {
            "evidenceHash": evidence_hash_bytes32,
            "imageHash": image_hash_bytes32,
            "phash": phash_bytes32,
        },
    }

    record_file = out_path / "record.json"
    with open(record_file, "w") as f:
        json.dump(envelope, f, indent=2)

    return envelope


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(override=True)

    out_dir = Path("out")
    face_file = out_dir / "face.json"
    match_file = out_dir / "match.json"
    sample_img = "samples/me.jpg"

    if not face_file.exists() or not match_file.exists():
        console.print("[bold red]Missing out/face.json or out/match.json. Run Prompts 2 and 4 first.[/bold red]")
        sys.exit(1)

    with open(face_file) as f:
        face_data = json.load(f)
    with open(match_file) as f:
        match_data = json.load(f)

    # Test determinism: Run twice under fixed time
    fixed_ts = "1700000000"
    os.environ["FACEANCHOR_FIXED_TIME"] = fixed_ts

    env1 = build_record(face_data, match_data, sample_img, out_dir="out")
    env2 = build_record(face_data, match_data, sample_img, out_dir="out")

    hash1 = env1["evidence_hash"]
    hash2 = env2["evidence_hash"]

    assert hash1 == hash2, f"Determinism failure: hash1 ({hash1}) != hash2 ({hash2})"

    table = Table(title="Canonical Evidence Record Summary")
    table.add_column("Field", style="bold cyan")
    table.add_column("Value", style="white")

    table.add_row("Schema", env1["record"]["schema"])
    table.add_row("Created At", env1["record"]["created_at"])
    table.add_row("Input SHA-256", env1["record"]["input"]["sha256"][:24] + "...")
    table.add_row("Input pHash", env1["record"]["input"]["phash"][:24] + "...")
    table.add_row("Face Model/Detector", f"{env1['record']['face']['model']} / {env1['record']['face']['detector']}")
    table.add_row("Embedding Commitment", env1["record"]["face"]["embedding_commitment"][:24] + "...")
    table.add_row("Matched Post URL", env1["record"]["match"]["post_url"])
    table.add_row("Face Distance", str(env1["record"]["match"]["face_distance"]))
    table.add_row("On-chain evidenceHash", env1["bytes32"]["evidenceHash"])
    table.add_row("On-chain imageHash", env1["bytes32"]["imageHash"])
    table.add_row("On-chain phash", env1["bytes32"]["phash"])

    console.print(table)
    console.print(Panel(
        f"[bold green]✓ Evidence Hash:[/bold green] {env1['evidence_hash']}\n"
        f"[bold green]✓ Identical Run 1 & Run 2 Hash Check:[/bold green] {hash1 == hash2}\n"
        f"[bold green]✓ Contract Parameters (bytes32):[/bold green]\n"
        f"  evidenceHash: {env1['bytes32']['evidenceHash']}\n"
        f"  imageHash:    {env1['bytes32']['imageHash']}\n"
        f"  phash:        {env1['bytes32']['phash']}\n"
        "Saved canonical record to out/record.json",
        title="Determinism & Integrity Proof"
    ))
    console.print("[bold green]✓ Prompt 5 Definition of Done satisfied![/bold green]")
