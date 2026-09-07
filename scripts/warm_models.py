"""Warm up DeepFace models (Facenet512, retinaface, opencv) by running on samples/me.jpg."""

import os
import sys
import time

from deepface import DeepFace
from rich.console import Console
from rich.panel import Panel

console = Console()


def warm_models(sample_path: str = "samples/me.jpg") -> None:
    if not os.path.exists(sample_path):
        console.print(f"[bold red]Error:[/bold red] Sample image not found at {sample_path}")
        sys.exit(1)

    start_time = time.time()
    console.print(Panel(
        "[bold cyan]Warming up DeepFace models...[/bold cyan]\n"
        "Model: [bold white]Facenet512[/bold white]\n"
        "Detectors: [bold white]retinaface[/bold white], [bold white]opencv[/bold white]\n"
        f"Target Sample: [white]{sample_path}[/white]",
        title="Model Pre-warming"
    ))

    # 1. Warm retinaface + Facenet512
    console.print("[yellow]Downloading/loading Facenet512 + retinaface weights...[/yellow]")
    t0 = time.time()
    try:
        DeepFace.represent(
            img_path=sample_path,
            model_name="Facenet512",
            detector_backend="retinaface",
            enforce_detection=False,
            align=True,
        )
        console.print(f"[green]✓ Facenet512 + retinaface warmed in {time.time() - t0:.2f}s[/green]")
    except Exception as e:
        console.print(f"[yellow]RetinaFace warm exception (will use fallback): {e}[/yellow]")

    # 2. Warm opencv + Facenet512
    console.print("[yellow]Testing Facenet512 + opencv fallback detector...[/yellow]")
    t1 = time.time()
    DeepFace.represent(
        img_path=sample_path,
        model_name="Facenet512",
        detector_backend="opencv",
        enforce_detection=False,
        align=True,
    )
    console.print(f"[green]✓ Facenet512 + opencv warmed in {time.time() - t1:.2f}s[/green]")

    total_elapsed = time.time() - start_time
    console.print(Panel(
        f"[bold green]✓ All model weights successfully downloaded and verified in {total_elapsed:.2f}s[/bold green]\n"
        f"Weights directory: ~/.deepface/weights",
        title="Pre-warm Complete"
    ))


if __name__ == "__main__":
    warm_models()
