"""Face detection and salted cryptographic embeddings using DeepFace."""

import json
import os
from pathlib import Path
from typing import Any, Optional

import numpy as np
from deepface import DeepFace
from PIL import Image
from rich.panel import Panel
from rich.table import Table

from pipeline.imaging import normalize_image
from pipeline.util import canonical_json, console, sha256_bytes

MODEL = "Facenet512"
DETECTOR = "retinaface"
FALLBACK_DETECTOR = "opencv"


def _get_salt() -> str:
    """Retrieve EMBEDDING_SALT from environment or raise an error."""
    salt = os.getenv("EMBEDDING_SALT")
    if not salt:
        # Check if .env has it
        from dotenv import load_dotenv
        load_dotenv(override=True)
        salt = os.getenv("EMBEDDING_SALT")
    if not salt:
        raise ValueError("EMBEDDING_SALT is not set in environment or .env file.")
    return salt


def encode_face(image_path: str, out_dir: str) -> dict[str, Any]:
    """Detect largest face, save crop, extract Facenet512 embedding, and compute salted commitment."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # 1. Normalize image
    normalized_input = normalize_image(image_path, out_path / "input.jpg")

    # 2. Extract faces
    detector_used = DETECTOR
    try:
        faces = DeepFace.extract_faces(
            img_path=normalized_input,
            detector_backend=DETECTOR,
            enforce_detection=True,
            align=True,
        )
    except Exception as e:
        console.print(f"[yellow]RetinaFace face extraction failed ({e}). Falling back to {FALLBACK_DETECTOR}...[/yellow]")
        detector_used = FALLBACK_DETECTOR
        faces = DeepFace.extract_faces(
            img_path=normalized_input,
            detector_backend=FALLBACK_DETECTOR,
            enforce_detection=True,
            align=True,
        )

    if not faces:
        raise RuntimeError("No face detected in input image.")

    if len(faces) > 1:
        console.print(f"[bold yellow]Warning:[/bold yellow] Detected {len(faces)} faces in image. Selecting the largest by area.")

    # Select largest face by area
    largest_face = max(faces, key=lambda f: f["facial_area"]["w"] * f["facial_area"]["h"])
    bbox = largest_face["facial_area"]

    # 3. Save crop to face_crop.jpg
    crop_arr = largest_face["face"]
    if crop_arr.dtype in (np.float32, np.float64) or crop_arr.max() <= 1.0:
        crop_uint8 = np.clip(crop_arr * 255.0, 0, 255).astype(np.uint8)
    else:
        crop_uint8 = np.clip(crop_arr, 0, 255).astype(np.uint8)

    crop_path = out_path / "face_crop.jpg"
    Image.fromarray(crop_uint8).save(str(crop_path), format="JPEG", quality=95)

    # 4. Generate embedding for the largest face
    representations = DeepFace.represent(
        img_path=normalized_input,
        model_name=MODEL,
        detector_backend=detector_used,
        enforce_detection=True,
        align=True,
    )

    if not representations:
        raise RuntimeError("Failed to compute representation for detected face.")

    # Match representation closest to largest face bbox
    rep = max(representations, key=lambda r: r.get("facial_area", {}).get("w", 0) * r.get("facial_area", {}).get("h", 0))
    embedding = rep["embedding"]

    # 5. Salted commitment: sha256( bytes.fromhex(EMBEDDING_SALT) + canonical_json([round(x, 6) for x in embedding]) )
    salt_hex = _get_salt()
    salt_bytes = bytes.fromhex(salt_hex)
    rounded_emb = [round(float(x), 6) for x in embedding]
    canonical_emb = canonical_json(rounded_emb)
    commitment_hex = sha256_bytes(salt_bytes + canonical_emb)

    result = {
        "model": MODEL,
        "detector": detector_used,
        "bbox": bbox,
        "crop_path": str(crop_path),
        "embedding": embedding,
        "embedding_commitment": commitment_hex,
    }

    # Write ONLY to out_dir/face.json (never committed)
    with open(out_path / "face.json", "w") as f:
        json.dump(result, f, indent=2)

    return result


def encode_face_from_path_quiet(path: str) -> Optional[list[float]]:
    """Quietly extract face embedding from image path without raising exceptions. Returns None if no face found."""
    if not os.path.exists(path):
        return None

    for detector in (DETECTOR, FALLBACK_DETECTOR):
        try:
            reps = DeepFace.represent(
                img_path=path,
                model_name=MODEL,
                detector_backend=detector,
                enforce_detection=False,
                align=True,
            )
            if not reps:
                continue

            valid = [r for r in reps if r.get("embedding")]
            if not valid:
                continue

            # Pick largest face
            best = max(valid, key=lambda r: r.get("facial_area", {}).get("w", 0) * r.get("facial_area", {}).get("h", 0))
            # If area is 0 or full image placeholder with 0 confidence, check confidence
            if best.get("confidence", 1.0) == 0 and best.get("facial_area", {}).get("w", 0) <= 0:
                continue

            return best["embedding"]
        except Exception:
            continue

    return None


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(override=True)

    sample_img = "samples/me.jpg"
    test_out = "out"
    console.print(f"[bold cyan]Running Face Encoding DoD on {sample_img}...[/bold cyan]")

    result = encode_face(sample_img, test_out)

    table = Table(title="Face Detection & Commitment Summary")
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="white")

    table.add_row("Model", result["model"])
    table.add_row("Detector", result["detector"])
    table.add_row("Bounding Box (x, y, w, h)", f"{result['bbox']['x']}, {result['bbox']['y']}, {result['bbox']['w']}, {result['bbox']['h']}")
    table.add_row("Crop Path", result["crop_path"])
    table.add_row("Embedding Dims", str(len(result["embedding"])))
    table.add_row("First 5 Dims", str([round(x, 4) for x in result["embedding"][:5]]))
    table.add_row("Salted Commitment", result["embedding_commitment"])

    console.print(table)

    crop_file = Path(result["crop_path"])
    assert crop_file.exists() and crop_file.stat().st_size > 0, "face_crop.jpg does not exist or is empty"
    assert len(result["embedding"]) == 512, f"Expected 512 dimensions for Facenet512, got {len(result['embedding'])}"
    assert len(result["embedding_commitment"]) == 64, "Expected 64 hex character commitment"

    console.print(Panel(
        f"[bold green]✓ Face Crop saved at:[/bold green] {crop_file}\n"
        f"[bold green]✓ Commitment Hash (bytes32):[/bold green] {result['embedding_commitment']}\n"
        "[bold cyan]Biometrics Status:[/bold cyan] Raw embeddings saved ONLY locally in out/face.json. On-chain record will store only the commitment hash.",
        title="Privacy & Integrity Verified"
    ))
    console.print("[bold green]✓ Prompt 2 Definition of Done satisfied![/bold green]")
