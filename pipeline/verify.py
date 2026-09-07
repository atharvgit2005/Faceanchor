"""Independent re-verification from transaction hash only."""

import argparse
import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup
from eth_typing import HexStr
from rich.panel import Panel
from rich.table import Table

from pipeline.chain import explorer_tx, get_contract, get_w3
from pipeline.imaging import download_image, image_hashes, phash_distance
from pipeline.util import console, sha256_json


def verify_from_tx(
    tx_hash: str,
    image_url_override: Optional[str] = None,
) -> dict[str, Any]:
    """Independently verify attestation from transaction hash alone without local pipeline state."""
    clean_tx = tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"

    w3 = get_w3()
    contract = get_contract(w3)

    console.print(f"[cyan]Querying transaction receipt for [bold]{clean_tx}[/bold]...[/cyan]")
    receipt = w3.eth.get_transaction_receipt(HexStr(clean_tx))

    # 1. Decode Attested event from receipt logs
    events = contract.events.Attested().process_receipt(receipt)
    if not events:
        raise ValueError(f"No Attested event found in transaction {clean_tx} receipt logs.")

    event_args = events[0]["args"]
    ev_hash = "0x" + event_args["evidenceHash"].hex()
    img_hash = "0x" + event_args["imageHash"].hex()
    ph_hash = "0x" + event_args["phash"].hex()
    uri = event_args["uri"]
    timestamp = event_args["timestamp"]
    attester = event_args["attester"]
    block_num = receipt["blockNumber"]

    # Parse JSON uri {"post": ..., "image": ...} if formatted as JSON
    post_url = uri
    direct_img_from_uri = None
    try:
        parsed_uri = json.loads(uri)
        if isinstance(parsed_uri, dict):
            post_url = parsed_uri.get("post", uri)
            direct_img_from_uri = parsed_uri.get("image")
    except Exception:
        pass

    console.print(Panel(
        f"[bold white]Attester:[/bold white] {attester}\n"
        f"[bold white]Block Number:[/bold white] {block_num} | [bold white]Timestamp:[/bold white] {timestamp}\n"
        f"[bold white]URI (Raw On-Chain):[/bold white] {uri}\n"
        f"[bold white]Target Post URL:[/bold white] {post_url}\n"
        f"[bold white]Target Direct Image:[/bold white] {direct_img_from_uri or 'None'}\n"
        f"[bold cyan]On-Chain evidenceHash:[/bold cyan] {ev_hash}\n"
        f"[bold cyan]On-Chain imageHash:   [/bold cyan] {img_hash}\n"
        f"[bold cyan]On-Chain phash:       [/bold cyan] {ph_hash}",
        title="Decoded On-Chain Attestation Event"
    ))

    # 2. Cross-check against contract storage get(evidenceHash)
    evidence_bytes = event_args["evidenceHash"]
    stored = contract.functions.get(evidence_bytes).call()
    assert stored[0] == attester, "Storage attester mismatch"
    assert "0x" + stored[3].hex() == img_hash, "Storage imageHash mismatch"
    assert "0x" + stored[4].hex() == ph_hash, "Storage phash mismatch"
    assert stored[5] == uri, "Storage URI mismatch"
    console.print("[green]✓ On-chain contract storage state verified: bit-for-bit match with event logs.[/green]")

    # 3. Live re-fetch: try direct image URL first; fall back to og:image only after direct fails
    target_image_source = None
    live_path = None

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_target = Path(tmp_dir) / "live_image.jpg"

        # 3a. Try direct image URL first (override, image from URI JSON, or post_url if an image)
        direct_sources = []
        if image_url_override:
            direct_sources.append(image_url_override)
        if direct_img_from_uri and direct_img_from_uri not in direct_sources:
            direct_sources.append(direct_img_from_uri)
        if any(post_url.lower().split("?")[0].endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".heic")):
            if post_url not in direct_sources:
                direct_sources.append(post_url)

        for src in direct_sources:
            src_str = str(src)
            if os.path.exists(src_str):
                import shutil
                shutil.copy(src_str, str(tmp_target))
                live_path = str(tmp_target)
                target_image_source = src_str
                break
            else:
                downloaded = download_image(src_str, tmp_target)
                if downloaded and os.path.exists(downloaded):
                    live_path = downloaded
                    target_image_source = src_str
                    break
                else:
                    console.print(f"[yellow]Direct image URL failed or expired: {src_str}[/yellow]")

        # 3b. Fall back to live OpenGraph og:image scrape ONLY after direct image URL fails
        if not live_path:
            console.print(f"[cyan]Attempting live OpenGraph scrape fallback of post URL: {post_url}...[/cyan]")
            try:
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    )
                }
                resp = requests.get(post_url, headers=headers, timeout=8)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    og = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
                    if og and og.get("content"):
                        raw_content = og.get("content")
                        og_url = str(raw_content[0] if isinstance(raw_content, list) else raw_content)
                        console.print(f"[green]✓ Extracted live og:image fallback: {og_url}[/green]")
                        downloaded = download_image(og_url, tmp_target)
                        if downloaded and os.path.exists(downloaded):
                            live_path = downloaded
                            target_image_source = og_url
            except Exception as e:
                console.print(f"[yellow]Live og:image fallback scrape failed: {e}[/yellow]")

        if not live_path or not os.path.exists(live_path):
            raise RuntimeError(
                f"Could not retrieve live image for verification from {post_url}. "
                "Direct image URL failed or expired, and og:image fallback was unavailable or blocked."
            )

        live_h = image_hashes(live_path)

    live_sha = "0x" + live_h["sha256"]
    live_ph = "0x" + live_h["phash"].zfill(64)

    # 5. Compare hashes
    clean_onchain_img = img_hash.lower().removeprefix("0x").zfill(64)
    clean_live_sha = live_h["sha256"].lower().removeprefix("0x").zfill(64)

    clean_onchain_phash = ph_hash.lower().removeprefix("0x").zfill(64)
    clean_live_phash = live_h["phash"].lower().removeprefix("0x").zfill(64)

    distance = phash_distance(clean_live_phash, clean_onchain_phash)

    if clean_live_sha == clean_onchain_img:
        verdict = "EXACT"
        verdict_color = "bold green"
    elif distance <= 8:
        verdict = f"SAME_IMAGE_REENCODED (pHash Hamming distance: {distance})"
        verdict_color = "bold green"
    else:
        verdict = f"TAMPERED_OR_REPLACED (pHash Hamming distance: {distance})"
        verdict_color = "bold red"

    # Comparison Table
    table = Table(title="Independent Image Hash Verification")
    table.add_column("Metric", style="bold cyan")
    table.add_column("On-Chain Attested", style="white")
    table.add_column("Live Verified Image", style="yellow")
    table.add_column("Match Status", justify="center")

    sha_match = clean_live_sha == clean_onchain_img
    phash_match = distance <= 8

    table.add_row(
        "SHA-256",
        img_hash[:26] + "...",
        live_sha[:26] + "...",
        "[green]EXACT[/green]" if sha_match else "[yellow]Diverged (Re-encoded)[/yellow]",
    )
    table.add_row(
        "pHash (256-bit)",
        ph_hash[:26] + "...",
        live_ph[:26] + "...",
        f"[green]Tolerated (dist: {distance})[/green]" if phash_match else f"[red]FAILED (dist: {distance})[/red]",
    )

    console.print(table)

    verdict_panel = Panel(
        f"[bold]FINAL CRYPTOGRAPHIC VERDICT:[/bold] [{verdict_color}]{verdict}[/{verdict_color}]\n\n"
        f"[white]Transaction:[/white] {clean_tx}\n"
        f"[white]Block:[/white] {block_num} | [white]Timestamp:[/white] {timestamp}\n"
        f"[white]Post URL:[/white] {post_url}\n"
        f"[white]Fetched Image URL:[/white] {target_image_source}\n"
        f"[white]Explorer:[/white] {explorer_tx(clean_tx)}",
        title="Independent Verification Report"
    )
    console.print(verdict_panel)

    return {
        "tx_hash": clean_tx,
        "verdict": verdict,
        "sha256_match": sha_match,
        "phash_distance": distance,
        "evidenceHash": ev_hash,
        "imageHash": img_hash,
        "phash": ph_hash,
        "uri": uri,
        "post_url": post_url,
        "fetched_image_url": target_image_source,
    }


def verify_record(record_path: str, tx_hash: str) -> bool:
    """Verify local record JSON file against on-chain evidence hash."""
    clean_tx = tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"

    with open(record_path) as f:
        data = json.load(f)

    record_obj = data.get("record", data)
    computed_evidence = sha256_json(record_obj)
    computed_bytes32 = "0x" + computed_evidence.zfill(64)

    w3 = get_w3()
    contract = get_contract(w3)
    receipt = w3.eth.get_transaction_receipt(HexStr(clean_tx))
    events = contract.events.Attested().process_receipt(receipt)
    if not events:
        raise ValueError(f"No Attested event found in transaction {clean_tx}")

    onchain_evidence = "0x" + events[0]["args"]["evidenceHash"].hex()

    is_match = computed_bytes32.lower() == onchain_evidence.lower()
    if is_match:
        console.print(Panel(
            f"[bold green]✓ RECORD INTEGRITY CONFIRMED[/bold green]\n"
            f"Computed Hash: {computed_bytes32}\n"
            f"On-chain Hash: {onchain_evidence}\n"
            f"Status: MATCH (Record has not been altered)",
            title="Record Verification"
        ))
    else:
        console.print(Panel(
            f"[bold red]✗ RECORD TAMPER DETECTED[/bold red]\n"
            f"Computed Hash: {computed_bytes32}\n"
            f"On-chain Hash: {onchain_evidence}\n"
            f"Status: TAMPERED (Record content has been modified)",
            title="Record Verification"
        ))

    return is_match


def demo_tamper(record_path: str, tx_hash: str) -> None:
    """Demonstrate tamper detection by copying record in memory, flipping 1 character, and verifying."""
    console.print("[bold yellow]Running Demo: Record Tamper Detection...[/bold yellow]")
    with open(record_path) as f:
        data = json.load(f)

    # Deep copy to ensure original file is NEVER modified on disk
    tampered_data = copy.deepcopy(data)
    record_obj = tampered_data.get("record", tampered_data)
    original_url = record_obj["match"]["post_url"]

    # Mutate 1 character in URL
    tampered_url = original_url[:-1] + ("x" if original_url[-1] != "x" else "y")
    record_obj["match"]["post_url"] = tampered_url

    computed_hash = "0x" + sha256_json(record_obj).zfill(64)

    w3 = get_w3()
    contract = get_contract(w3)
    clean_hash = tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"
    receipt = w3.eth.get_transaction_receipt(HexStr(clean_hash))
    events = contract.events.Attested().process_receipt(receipt)
    onchain_evidence = "0x" + events[0]["args"]["evidenceHash"].hex()

    console.print(f"Original Post URL: [dim]{original_url}[/dim]")
    console.print(f"Tampered Post URL: [red]{tampered_url}[/red]")
    console.print(f"Tampered Computed Hash: [red]{computed_hash}[/red]")
    console.print(f"On-Chain Evidence Hash: [green]{onchain_evidence}[/green]")

    if computed_hash.lower() != onchain_evidence.lower():
        console.print(Panel(
            "[bold red]VERDICT: TAMPERED[/bold red]\n"
            "Single character modification was detected by SHA-256 canonical hashing.",
            title="Tamper Demonstration"
        ))
    else:
        console.print("[red]Error: Tamper was not detected![/red]")


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(override=True)

    parser = argparse.ArgumentParser(description="Independent verification of FaceAnchor attestations.")
    parser.add_argument("--tx", required=True, help="Transaction hash to verify")
    parser.add_argument("--record", help="Path to evidence record JSON file")
    parser.add_argument("--image-url", help="Override URL or local path for live image")
    parser.add_argument("--demo-tamper", action="store_true", help="Run tamper detection demonstration")
    parser.add_argument("--demo-wrong-image", help="Path to a different image to demonstrate pHash failure")

    args = parser.parse_args()

    if args.demo_tamper:
        rec = args.record or "out/record.json"
        demo_tamper(rec, args.tx)
        return

    if args.demo_wrong_image:
        console.print(f"[bold yellow]Running Demo: Wrong Image Replaced Detection ({args.demo_wrong_image})...[/bold yellow]")
        verify_from_tx(args.tx, image_url_override=args.demo_wrong_image)
        return

    if args.record:
        verify_record(args.record, args.tx)
        return

    # Default: Independent verify from tx hash only
    verify_from_tx(args.tx, image_url_override=args.image_url)


if __name__ == "__main__":
    main()
