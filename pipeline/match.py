"""Face matching with engine corroboration and threshold evaluation."""

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
from rich.panel import Panel
from rich.table import Table

from pipeline.face import encode_face_from_path_quiet
from pipeline.imaging import download_image, image_hashes
from pipeline.util import console


def cosine_distance(u: list[float], v: list[float]) -> float:
    """Calculate cosine distance: 1 - (u . v) / (||u|| * ||v||)."""
    arr_u = np.array(u, dtype=np.float64)
    arr_v = np.array(v, dtype=np.float64)
    norm_u = np.linalg.norm(arr_u)
    norm_v = np.linalg.norm(arr_v)
    if norm_u == 0 or norm_v == 0:
        return 1.0
    dot = np.dot(arr_u, arr_v)
    sim = dot / (norm_u * norm_v)
    return float(np.clip(1.0 - sim, 0.0, 2.0))


def rank_candidates(
    candidates: list[dict[str, Any]],
    reference_embedding: list[float],
    out_dir: str = "out",
    threshold: float = 0.35,
) -> list[dict[str, Any]]:
    """Download candidates, extract faces quietly, compute cosine distance, apply corroboration bonus, and rank."""
    out_path = Path(out_dir)
    cands_dir = out_path / "cands"
    cands_dir.mkdir(parents=True, exist_ok=True)

    ranked: list[dict[str, Any]] = []

    for i, cand in enumerate(candidates):
        # Collect candidate image URLs in priority order: original image, then thumbnail
        sources: list[str] = []
        for key in ("image", "thumbnail"):
            val = cand.get(key)
            if isinstance(val, dict):
                s = val.get("link") or val.get("url") or val.get("original") or ""
            elif isinstance(val, str):
                s = val.strip()
            else:
                s = ""
            if s and s not in sources:
                sources.append(s)

        cand_entry = dict(cand)

        if not sources:
            cand_entry.update({
                "status": "download_failed",
                "distance": None,
                "adjusted": None,
                "image_hashes": None,
                "local_path": None,
            })
            ranked.append(cand_entry)
            continue

        cand_dest = cands_dir / f"{i}.jpg"
        local_path: Optional[str] = None

        for img_source in sources:
            if os.path.exists(img_source):
                import shutil
                shutil.copy(img_source, str(cand_dest))
                local_path = str(cand_dest)
                cand_entry["downloaded_source"] = img_source
                break
            else:
                downloaded = download_image(img_source, cand_dest)
                if downloaded and os.path.exists(downloaded):
                    local_path = downloaded
                    cand_entry["downloaded_source"] = img_source
                    break

        if not local_path or not os.path.exists(local_path):
            cand_entry.update({
                "status": "download_failed",
                "distance": None,
                "adjusted": None,
                "image_hashes": None,
                "local_path": None,
            })
            ranked.append(cand_entry)
            continue

        hashes = image_hashes(local_path)
        cand_emb = encode_face_from_path_quiet(local_path)

        if cand_emb is None:
            cand_entry.update({
                "status": "no_face",
                "distance": None,
                "adjusted": None,
                "image_hashes": hashes,
                "local_path": local_path,
            })
        else:
            dist = cosine_distance(reference_embedding, cand_emb)
            corroboration = len(cand.get("engines", ["google_lens"]))
            # adjusted = distance / (1 + 0.15 * (corroboration - 1))
            adjusted = dist / (1.0 + 0.15 * max(0, corroboration - 1))

            cand_entry.update({
                "status": "scored",
                "distance": round(dist, 4),
                "adjusted": round(adjusted, 4),
                "image_hashes": hashes,
                "local_path": local_path,
            })

        ranked.append(cand_entry)

    # Sort scored candidates by adjusted score ascending, unscored at end
    def sort_key(c: dict[str, Any]) -> tuple:
        if c.get("status") == "scored" and c.get("adjusted") is not None:
            return (0, c["adjusted"])
        if c.get("status") == "no_face":
            return (1, 999.0)
        return (2, 999.0)

    ranked_sorted = sorted(ranked, key=sort_key)

    with open(out_path / "ranked.json", "w") as f:
        json.dump(ranked_sorted, f, indent=2)

    return ranked_sorted


def pick_match(ranked: list[dict[str, Any]], threshold: float = 0.35) -> Optional[dict[str, Any]]:
    """Select the winning post based on threshold and social criteria; print comparison table."""
    scored_cands = [c for c in ranked if c.get("status") == "scored" and c.get("adjusted") is not None]

    # Find winning candidate
    winner: Optional[dict[str, Any]] = None
    _is_weak_match = False

    # 1. First social candidate under threshold
    for c in scored_cands:
        if c.get("is_social") and c["adjusted"] <= threshold:
            winner = dict(c)
            winner["strength"] = "STRONG"
            break

    # 2. Fallback: best social candidate under threshold * 1.3
    if winner is None:
        for c in scored_cands:
            if c.get("is_social") and c["adjusted"] <= (threshold * 1.3):
                winner = dict(c)
                winner["strength"] = "WEAK"
                _is_weak_match = True
                console.print(
                    f"[bold yellow]Warning: Weak match detected[/bold yellow] "
                    f"(adjusted distance {c['adjusted']:.4f} <= {threshold * 1.3:.4f})"
                )
                break

    # Build rich comparison table
    table = Table(title=f"Face Matching & Corroboration Ranking (Threshold: {threshold})")
    table.add_column("#", justify="right", style="bold cyan")
    table.add_column("Domain", style="yellow")
    table.add_column("Engines", style="magenta")
    table.add_column("Status", justify="center")
    table.add_column("Distance", justify="right")
    table.add_column("Adjusted", justify="right")
    table.add_column("Verdict", justify="center")

    for i, c in enumerate(ranked[:15], 1):
        status_key = str(c.get("status") or "")
        status_style = {
            "scored": "[green]scored[/green]",
            "no_face": "[yellow]no_face[/yellow]",
            "download_failed": "[red]failed[/red]",
        }.get(status_key, status_key)

        dist_str = f"{c['distance']:.4f}" if c.get("distance") is not None else "-"
        adj_str = f"{c['adjusted']:.4f}" if c.get("adjusted") is not None else "-"

        if winner is not None and c.get("link") == winner.get("link"):
            verdict = "[bold green]MATCH (WINNER)[/bold green]"
        elif c.get("adjusted") is not None and c["adjusted"] <= (threshold * 1.3):
            verdict = "[yellow]near[/yellow]"
        else:
            verdict = "[dim]no[/dim]"

        table.add_row(
            str(i),
            c.get("domain", ""),
            ", ".join(c.get("engines", [])),
            status_style,
            dist_str,
            adj_str,
            verdict,
        )

    console.print(table)
    return winner


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(override=True)

    out_dir = Path("out")
    face_file = out_dir / "face.json"
    cands_file = out_dir / "candidates.json"

    if not face_file.exists():
        console.print("[bold red]out/face.json does not exist. Please run face detection first (pipeline.face).[/bold red]")
        sys.exit(1)

    with open(face_file) as f:
        face_data = json.load(f)
    ref_embedding = face_data["embedding"]

    threshold = float(os.getenv("FACE_THRESHOLD", "0.35"))

    # If candidates.json does not exist, initialize a sample candidate dataset for self-testing
    if not cands_file.exists():
        console.print("[yellow]out/candidates.json not found. Initializing mock candidate set for verification...[/yellow]")
        test_cands = [
            {
                "title": "My Public Post on X",
                "link": "https://x.com/user/status/1234567890",
                "domain": "x.com",
                "is_social": True,
                "engines": ["google_lens", "yandex"],
                "image": "samples/me.jpg",
                "thumbnail": "samples/me.jpg",
                "source": "x.com",
            },
            {
                "title": "Recompressed photo on LinkedIn",
                "link": "https://linkedin.com/in/user/recent-activity",
                "domain": "linkedin.com",
                "is_social": True,
                "engines": ["google_lens"],
                "image": "out/recompressed.jpg",
                "thumbnail": "out/recompressed.jpg",
                "source": "linkedin.com",
            },
            {
                "title": "Random web article without face",
                "link": "https://example.com/article/1",
                "domain": "example.com",
                "is_social": False,
                "engines": ["google_lens"],
                "image": "https://invalid.example.com/nonexistent.jpg",
                "thumbnail": "",
                "source": "example.com",
            }
        ]
        with open(cands_file, "w") as f:
            json.dump(test_cands, f, indent=2)

    with open(cands_file) as f:
        candidates = json.load(f)

    ranked = rank_candidates(candidates, ref_embedding, out_dir="out", threshold=threshold)
    winner = pick_match(ranked, threshold=threshold)

    if winner:
        match_record = {
            "post_url": winner["link"],
            "domain": winner["domain"],
            "image_url": winner.get("image") or winner.get("thumbnail") or "",
            "image_sha256": winner["image_hashes"]["sha256"],
            "image_phash": winner["image_hashes"]["phash"],
            "face_distance": winner["distance"],
            "adjusted_score": winner["adjusted"],
            "engines": winner["engines"],
        }
        with open(out_dir / "match.json", "w") as f:
            json.dump(match_record, f, indent=2)

        console.print(Panel(
            f"[bold green]Winning Post URL:[/bold green] {winner['link']}\n"
            f"[bold cyan]Domain:[/bold cyan] {winner['domain']} | [bold cyan]Engines:[/bold cyan] {', '.join(winner['engines'])}\n"
            f"[bold cyan]Face Distance:[/bold cyan] {winner['distance']} (Adjusted: {winner['adjusted']})\n"
            f"[bold cyan]Image SHA-256:[/bold cyan] {winner['image_hashes']['sha256'][:24]}...\n"
            f"[bold cyan]Image pHash:[/bold cyan] {winner['image_hashes']['phash'][:24]}...\n"
            f"Saved match record to [bold white]out/match.json[/bold white]",
            title="Match Confirmed"
        ))
        console.print("[bold green]✓ Match ranking verification complete.[/bold green]")
        sys.exit(0)
    else:
        best_dist = None
        scored = [c for c in ranked if c.get("distance") is not None]
        if scored:
            best_dist = scored[0]["distance"]
        console.print(
            f"[bold red]No match found under threshold {threshold}. "
            f"Best distance was {best_dist}. Exiting with status 3 for threshold tuning.[/bold red]"
        )
        sys.exit(3)
