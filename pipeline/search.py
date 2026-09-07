"""Multi-engine reverse image search (Google Lens + Yandex) via SerpAPI."""

import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse, urlunparse

from rich.panel import Panel
from rich.table import Table
from serpapi import GoogleSearch

from pipeline.util import console

SOCIAL_DOMAINS = [
    "instagram.com",
    "x.com",
    "twitter.com",
    "linkedin.com",
    "facebook.com",
    "threads.net",
    "reddit.com",
    "tiktok.com",
    "pinterest.com",
    "youtube.com",
]


class SearchFailed(Exception):
    """Raised when all search engines fail or return zero results."""
    pass


def normalize_url(url: str) -> str:
    """Normalize URL by lowercasing host, stripping query parameters, fragment, and trailing slash."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        netloc = parsed.netloc.lower()
        return urlunparse((parsed.scheme, netloc, path, "", "", ""))
    except Exception:
        return url.strip().rstrip("/")


def extract_domain(url: str) -> str:
    """Extract and lowercase host domain, removing leading www."""
    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def search_google_lens(image_url: str, api_key: Optional[str] = None) -> list[dict[str, Any]]:
    """Query SerpAPI Google Lens engine for visual matches."""
    key = api_key or os.getenv("SERPAPI_KEY")
    if not key:
        raise ValueError("SERPAPI_KEY is not configured in environment or .env")

    params = {
        "engine": "google_lens",
        "url": image_url,
        "hl": "en",
        "api_key": key,
    }

    search = GoogleSearch(params)
    results = search.get_dict()

    if "error" in results:
        raise RuntimeError(f"Google Lens SerpAPI error: {results['error']}")

    matches = results.get("visual_matches", [])
    parsed = []
    for item in matches:
        parsed.append({
            "engine": "google_lens",
            "title": item.get("title", ""),
            "link": item.get("link", ""),
            "source": item.get("source", ""),
            "thumbnail": item.get("thumbnail", ""),
            "image": item.get("original") or item.get("image") or item.get("thumbnail") or "",
        })
    return parsed


def search_yandex(image_url: str, api_key: Optional[str] = None) -> list[dict[str, Any]]:
    """Query SerpAPI Yandex images engine for visual matches."""
    key = api_key or os.getenv("SERPAPI_KEY")
    if not key:
        raise ValueError("SERPAPI_KEY is not configured in environment or .env")

    params = {
        "engine": "yandex_images",
        "url": image_url,
        "api_key": key,
    }

    search = GoogleSearch(params)
    results = search.get_dict()

    if "error" in results:
        raise RuntimeError(f"Yandex SerpAPI error: {results['error']}")

    matches = (
        results.get("image_results", [])
        or results.get("images_results", [])
        or results.get("organic_results", [])
    )
    parsed = []
    for item in matches:
        raw_img = item.get("original") or item.get("image") or item.get("thumbnail") or ""
        raw_thumb = item.get("thumbnail") or ""
        img_str = raw_img.get("link", "") if isinstance(raw_img, dict) else (raw_img if isinstance(raw_img, str) else "")
        thumb_str = raw_thumb.get("link", "") if isinstance(raw_thumb, dict) else (raw_thumb if isinstance(raw_thumb, str) else "")

        parsed.append({
            "engine": "yandex",
            "title": item.get("title", ""),
            "link": item.get("link", ""),
            "source": item.get("source", ""),
            "thumbnail": thumb_str,
            "image": img_str or thumb_str,
        })
    return parsed


def reverse_image_search(
    image_url: str,
    out_dir: str = "out",
    engines: Iterable[str] = ("google_lens", "yandex"),
    api_key: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Execute reverse search across engines, log errors without crashing other engines, merge and rank candidates."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    all_raw: list[dict[str, Any]] = []
    engine_errors: dict[str, str] = {}

    for engine in engines:
        try:
            console.print(f"[cyan]Searching reverse image index via [bold]{engine}[/bold]...[/cyan]")
            if engine == "google_lens":
                res = search_google_lens(image_url, api_key)
            elif engine == "yandex":
                res = search_yandex(image_url, api_key)
            else:
                console.print(f"[yellow]Skipping unknown engine {engine}[/yellow]")
                continue

            raw_file = out_path / f"search_{engine}_raw.json"
            with open(raw_file, "w") as f:
                json.dump(res, f, indent=2)

            console.print(f"[green]✓ {engine}: retrieved {len(res)} matches (saved to {raw_file})[/green]")
            all_raw.extend(res)
        except Exception as e:
            engine_errors[engine] = str(e)
            console.print(f"[yellow]Engine {engine} encountered an error: {e}[/yellow]")

    # Deduplicate & merge candidates by normalized URL
    merged: dict[str, dict[str, Any]] = {}
    rank_counter = 0

    for item in all_raw:
        link = item.get("link", "")
        norm_link = normalize_url(link)
        if not norm_link:
            continue

        domain = extract_domain(norm_link)
        is_social = any(domain == s or domain.endswith("." + s) for s in SOCIAL_DOMAINS)

        if norm_link not in merged:
            merged[norm_link] = {
                "title": item.get("title", ""),
                "link": link,
                "domain": domain,
                "is_social": is_social,
                "engines": [item["engine"]],
                "image": item.get("image") or item.get("thumbnail") or "",
                "thumbnail": item.get("thumbnail") or "",
                "source": item.get("source", ""),
                "_rank": rank_counter,
            }
            rank_counter += 1
        else:
            cand = merged[norm_link]
            if item["engine"] not in cand["engines"]:
                cand["engines"].append(item["engine"])
            if not cand.get("image") and item.get("image"):
                cand["image"] = item["image"]

    # Sort: is_social first, then len(engines) desc, then original rank
    sorted_candidates = sorted(
        merged.values(),
        key=lambda c: (not c["is_social"], -len(c["engines"]), c["_rank"]),
    )

    # Clean up internal rank key
    for c in sorted_candidates:
        c.pop("_rank", None)

    candidates_file = out_path / "candidates.json"
    with open(candidates_file, "w") as f:
        json.dump(sorted_candidates, f, indent=2)

    if not sorted_candidates:
        err_msg = (
            f"All engines failed or returned zero results for URL: {image_url}. "
            f"Engine errors: {engine_errors}"
        )
        raise SearchFailed(err_msg)

    # Render rich candidate table
    table = Table(title=f"Reverse Image Search Candidates ({len(sorted_candidates)} found)")
    table.add_column("#", justify="right", style="bold cyan")
    table.add_column("Engines", style="magenta")
    table.add_column("Domain", style="yellow")
    table.add_column("Social", justify="center")
    table.add_column("Title", style="white")
    table.add_column("Link", style="blue")

    for i, c in enumerate(sorted_candidates[:15], 1):
        social_mark = "[bold green]YES[/bold green]" if c["is_social"] else "[dim]no[/dim]"
        title_trunc = (c["title"][:47] + "...") if len(c["title"]) > 50 else c["title"]
        link_trunc = (c["link"][:57] + "...") if len(c["link"]) > 60 else c["link"]
        table.add_row(str(i), ", ".join(c["engines"]), c["domain"], social_mark, title_trunc, link_trunc)

    console.print(table)
    return sorted_candidates


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(override=True)

    img_url = os.getenv("IMAGE_PUBLIC_URL")
    if not img_url:
        console.print("[bold red]IMAGE_PUBLIC_URL is not set in .env[/bold red]")
        console.print("[yellow]Please populate IMAGE_PUBLIC_URL with your public sample image URL.[/yellow]")
        sys.exit(1)

    try:
        candidates = reverse_image_search(img_url, out_dir="out")
    except SearchFailed as e:
        console.print(Panel(f"[bold red]Search Failed:[/bold red] {e}", title="Error"))
        sys.exit(1)

    has_social = any(c["is_social"] for c in candidates)
    if has_social:
        console.print("[bold green]✓ At least one social media match found in candidates![/bold green]")
        console.print("[bold green]✓ Prompt 3 Definition of Done satisfied![/bold green]")
        sys.exit(0)
    else:
        console.print(
            "[bold yellow]Notice:[/bold yellow] Search succeeded but no candidate matched the known SOCIAL_DOMAINS list.\n"
            "This indicates the photo is either not indexed on social media or newly uploaded."
        )
        sys.exit(2)
