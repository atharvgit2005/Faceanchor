"""Imaging utilities: normalization, SHA-256, perceptual hash (pHash), and image downloader."""

import io
import os
from pathlib import Path
from typing import Any, Union

import imagehash
import pillow_heif
import requests
from PIL import Image, ImageDraw, ImageOps
from rich.panel import Panel
from rich.table import Table

from pipeline.util import console, sha256_bytes

# Register HEIF opener so PIL can open HEIC/HEIF images seamlessly
pillow_heif.register_heif_opener()  # pyright: ignore[reportPrivateImportUsage]


def normalize_image(path_or_bytes: Union[str, Path, bytes, bytearray, Image.Image], out_path: Union[str, Path]) -> str:
    """Load image, register pillow_heif, convert to RGB, apply EXIF orientation, save JPEG quality 95.

    Returns the path to normalized JPEG.
    """
    out_path_str = str(out_path)
    os.makedirs(os.path.dirname(os.path.abspath(out_path_str)), exist_ok=True)

    if isinstance(path_or_bytes, (str, Path)):
        img = Image.open(path_or_bytes)
    elif isinstance(path_or_bytes, (bytes, bytearray)):
        img = Image.open(io.BytesIO(path_or_bytes))
    elif isinstance(path_or_bytes, Image.Image):
        img = path_or_bytes.copy()
    else:
        raise TypeError(f"Unsupported image input type: {type(path_or_bytes)}")

    # Apply EXIF rotation if present
    img = ImageOps.exif_transpose(img)

    # Convert any mode (RGBA, P, CMYK, L) to RGB
    if img.mode != "RGB":
        img = img.convert("RGB")

    img.save(out_path_str, format="JPEG", quality=95)
    return out_path_str


def image_hashes(path: Union[str, Path]) -> dict[str, Any]:
    """Compute cryptographic SHA-256, 256-bit perceptual hash (16×16 DCT pHash), dHash, and dimensions."""
    path_str = str(path)
    with open(path_str, "rb") as f:
        raw_bytes = f.read()

    sha = sha256_bytes(raw_bytes)

    with Image.open(path_str) as img:
        width, height = img.size
        # 256-bit pHash (hash_size=16 produces 256 bits = 64 hex chars)
        phash_val = str(imagehash.phash(img, hash_size=16))
        dhash_val = str(imagehash.dhash(img))

    return {
        "sha256": sha,
        "phash": phash_val,
        "dhash": dhash_val,
        "width": width,
        "height": height,
    }


def phash_distance(a: str, b: str) -> int:
    """Compute Hamming distance between two hex pHash strings."""
    hash_a = imagehash.hex_to_hash(a)
    hash_b = imagehash.hex_to_hash(b)
    return int(hash_a - hash_b)


def download_image(url: str, out_path: Union[str, Path], timeout: int = 10) -> str | None:
    """Download image with browser UA, validate mime type and minimum size, normalize and save.

    Never raises; returns out_path on success or None on failure.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code != 200:
            return None

        content_type = resp.headers.get("Content-Type", "").lower()
        if "image" not in content_type and not any(
            url.lower().split("?")[0].endswith(ext)
            for ext in (".jpg", ".jpeg", ".png", ".webp", ".heic")
        ):
            return None

        if len(resp.content) < 2048:  # reject < 2KB
            return None

        return normalize_image(resp.content, out_path)
    except Exception:
        return None


if __name__ == "__main__":
    sample_path = Path("samples/me.jpg")
    out_dir = Path("out")
    out_dir.mkdir(exist_ok=True)
    recompressed_path = out_dir / "recompressed.jpg"

    if not sample_path.exists():
        console.print("[yellow]samples/me.jpg not found. Generating a synthetic portrait for DoD test...[/yellow]")
        sample_path.parent.mkdir(parents=True, exist_ok=True)
        # Create a sample synthetic gradient portrait with features
        test_img = Image.new("RGB", (400, 500), color=(220, 230, 242))
        draw = ImageDraw.Draw(test_img)
        # Head / face oval
        draw.ellipse([100, 100, 300, 380], fill=(235, 195, 165), outline=(180, 140, 110), width=3)
        # Eyes
        draw.ellipse([145, 190, 175, 215], fill=(255, 255, 255), outline=(50, 50, 50), width=2)
        draw.ellipse([155, 195, 168, 210], fill=(70, 40, 20))
        draw.ellipse([225, 190, 255, 215], fill=(255, 255, 255), outline=(50, 50, 50), width=2)
        draw.ellipse([235, 195, 248, 210], fill=(70, 40, 20))
        # Mouth
        draw.arc([160, 290, 240, 330], start=10, end=170, fill=(180, 50, 50), width=4)
        # Shoulders
        draw.polygon([(50, 500), (350, 500), (320, 400), (80, 400)], fill=(40, 70, 120))
        test_img.save(str(sample_path), format="JPEG", quality=95)
        console.print(f"[green]Saved sample synthetic image to {sample_path}[/green]")

    # Normalize sample first
    norm_sample = normalize_image(sample_path, out_dir / "normalized_me.jpg")
    orig_hashes = image_hashes(norm_sample)

    # Re-save at JPEG quality 60
    with Image.open(norm_sample) as img:
        img.save(str(recompressed_path), format="JPEG", quality=60)

    recomp_hashes = image_hashes(recompressed_path)
    distance = phash_distance(orig_hashes["phash"], recomp_hashes["phash"])

    table = Table(title="Imaging Hashes & Recompression Comparison")
    table.add_column("Property", style="bold cyan")
    table.add_column("Original (Q95)", style="white")
    table.add_column("Recompressed (Q60)", style="yellow")

    table.add_row("SHA-256", orig_hashes["sha256"][:24] + "...", recomp_hashes["sha256"][:24] + "...")
    table.add_row("pHash (256-bit)", orig_hashes["phash"][:24] + "...", recomp_hashes["phash"][:24] + "...")
    table.add_row("dHash", orig_hashes["dhash"], recomp_hashes["dhash"])
    table.add_row("Dimensions", f"{orig_hashes['width']}x{orig_hashes['height']}", f"{recomp_hashes['width']}x{recomp_hashes['height']}")

    console.print(table)

    sha_different = orig_hashes["sha256"] != recomp_hashes["sha256"]
    phash_close = distance < 6

    panel_text = (
        f"[bold]SHA-256 Match:[/bold] {'[red]DIFFERENT (Expected)[/red]' if sha_different else '[green]SAME[/green]'}\n"
        f"[bold]pHash Distance:[/bold] [green]{distance}[/green] (< 6 threshold: {phash_close})\n\n"
        "[bold cyan]Conclusion:[/bold cyan] Demonstrates why we store both hashes: "
        "SHA-256 detects any byte manipulation, while pHash tolerates platform recompression."
    )
    console.print(Panel(panel_text, title="Dual-Hash Integrity Proof"))

    assert sha_different, "Expected SHA-256 to differ under recompression"
    assert phash_close, f"Expected pHash distance < 6, got {distance}"
    console.print("[bold green]✓ Prompt 1 Definition of Done satisfied![/bold green]")
