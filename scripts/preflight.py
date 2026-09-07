"""Preflight diagnostics script checking environment, RPC, wallet balance, contract bytecode, SerpAPI quota, DeepFace weights, and image URL."""

import os
import sys
from pathlib import Path

import requests
from eth_account import Account
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

console = Console()


def run_preflight() -> bool:
    from dotenv import load_dotenv
    load_dotenv(override=True)

    console.print(Panel("[bold cyan]FaceAnchor: System Readiness & Preflight Diagnostics[/bold cyan]", title="Preflight"))

    all_passed = True
    table = Table(title="Preflight Diagnostic Checks")
    table.add_column("Diagnostic Check", style="bold white")
    table.add_column("Status", justify="center")
    table.add_column("Details", style="white")

    # 1. Environment Keys
    required_keys = [
        "SERPAPI_KEY",
        "RPC_URL",
        "CHAIN_ID",
        "PRIVATE_KEY",
        "CONTRACT_ADDRESS",
        "IMAGE_PUBLIC_URL",
        "EMBEDDING_SALT",
    ]
    missing_keys = [k for k in required_keys if not os.getenv(k)]
    if not missing_keys:
        table.add_row("1. Environment Variables", "[bold green]PASS[/bold green]", "All required .env keys present")
    else:
        all_passed = False
        table.add_row(
            "1. Environment Variables",
            "[bold red]FAIL[/bold red]",
            f"Missing keys: {', '.join(missing_keys)}",
        )

    # 2. RPC Reachability & Chain ID Match
    rpc_url = os.getenv("RPC_URL", "").strip()
    chain_id_env = os.getenv("CHAIN_ID", "80002").strip()
    w3 = None
    if rpc_url:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc_url))
            if int(chain_id_env) == 80002:
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

            if w3.is_connected():
                actual_id = w3.eth.chain_id
                if actual_id == int(chain_id_env):
                    table.add_row("2. RPC & Chain ID", "[bold green]PASS[/bold green]", f"Connected to {rpc_url} (Chain ID: {actual_id})")
                else:
                    all_passed = False
                    table.add_row("2. RPC & Chain ID", "[bold red]FAIL[/bold red]", f"Chain ID mismatch: RPC returned {actual_id}, expected {chain_id_env}")
            else:
                all_passed = False
                table.add_row("2. RPC & Chain ID", "[bold red]FAIL[/bold red]", f"Failed to connect to RPC at {rpc_url}")
        except Exception as e:
            all_passed = False
            table.add_row("2. RPC & Chain ID", "[bold red]FAIL[/bold red]", f"RPC connection error: {e}")
    else:
        all_passed = False
        table.add_row("2. RPC & Chain ID", "[bold red]FAIL[/bold red]", "RPC_URL is not set")

    # 3. Wallet Balance Check
    priv_key = os.getenv("PRIVATE_KEY", "").strip()
    if w3 and w3.is_connected() and priv_key:
        try:
            acc = Account.from_key(priv_key)
            bal_wei = w3.eth.get_balance(acc.address)
            bal_eth = float(w3.from_wei(bal_wei, "ether"))
            if bal_wei > 0:
                table.add_row("3. Wallet Balance", "[bold green]PASS[/bold green]", f"{acc.address} ({bal_eth:.4f} POL/ETH)")
            else:
                all_passed = False
                table.add_row("3. Wallet Balance", "[bold red]FAIL[/bold red]", f"{acc.address} has 0 balance (fund via faucet.polygon.technology)")
        except Exception as e:
            all_passed = False
            table.add_row("3. Wallet Balance", "[bold red]FAIL[/bold red]", f"Invalid PRIVATE_KEY: {e}")
    else:
        table.add_row("3. Wallet Balance", "[bold red]SKIP[/bold red]", "Skipped due to RPC or PRIVATE_KEY issue")

    # 4. Contract Bytecode Existence
    contract_addr = os.getenv("CONTRACT_ADDRESS", "").strip()
    if w3 and w3.is_connected() and contract_addr:
        try:
            checksum_addr = Web3.to_checksum_address(contract_addr)
            code = w3.eth.get_code(checksum_addr)
            if code and code != b"" and code != b"\x00":
                table.add_row("4. Contract Bytecode", "[bold green]PASS[/bold green]", f"Bytecode verified at {contract_addr}")
            else:
                all_passed = False
                table.add_row("4. Contract Bytecode", "[bold red]FAIL[/bold red]", f"No contract bytecode deployed at {contract_addr}")
        except Exception as e:
            all_passed = False
            table.add_row("4. Contract Bytecode", "[bold red]FAIL[/bold red]", f"Error checking contract: {e}")
    else:
        table.add_row("4. Contract Bytecode", "[bold red]SKIP[/bold red]", "CONTRACT_ADDRESS not configured")

    # 5. SerpAPI Key & Quota Check
    serpapi_key = os.getenv("SERPAPI_KEY", "").strip()
    if serpapi_key:
        try:
            resp = requests.get(f"https://serpapi.com/account?api_key={serpapi_key}", timeout=8)
            if resp.status_code == 200:
                acc_data = resp.json()
                searches_left = (
                    acc_data.get("plan_searches_left")
                    or acc_data.get("total_searches_left")
                    or acc_data.get("searches_per_month", 0) - acc_data.get("this_month_usage", 0)
                )
                if searches_left is not None and searches_left < 10:
                    table.add_row(
                        "5. SerpAPI Quota",
                        "[bold yellow]WARN[/bold yellow]",
                        f"Active: {searches_left} searches remaining (< 10 left; each full run costs 2)",
                    )
                else:
                    table.add_row(
                        "5. SerpAPI Quota",
                        "[bold green]PASS[/bold green]",
                        f"Key active: {searches_left} searches remaining (each full run costs 2)",
                    )
            else:
                all_passed = False
                table.add_row("5. SerpAPI Quota", "[bold red]FAIL[/bold red]", f"Invalid SerpAPI key (HTTP {resp.status_code})")
        except Exception as e:
            all_passed = False
            table.add_row("5. SerpAPI Quota", "[bold red]FAIL[/bold red]", f"SerpAPI query error: {e}")
    else:
        all_passed = False
        table.add_row("5. SerpAPI Quota", "[bold red]FAIL[/bold red]", "SERPAPI_KEY not set in .env (sign up at serpapi.com)")

    # 6. DeepFace Weights
    weights_dir = Path.home() / ".deepface" / "weights"
    facenet_weights = weights_dir / "facenet512_weights.h5"
    retina_weights = weights_dir / "retinaface.h5"
    if facenet_weights.exists() and facenet_weights.stat().st_size > 10_000_000 and retina_weights.exists():
        table.add_row("6. DeepFace Weights", "[bold green]PASS[/bold green]", f"Facenet512 & RetinaFace cached in {weights_dir}")
    else:
        all_passed = False
        table.add_row("6. DeepFace Weights", "[bold red]FAIL[/bold red]", "Weights missing. Run `make warm` to download.")

    # 7. IMAGE_PUBLIC_URL Validation
    img_url = os.getenv("IMAGE_PUBLIC_URL", "").strip()
    if img_url:
        try:
            resp = requests.head(img_url, timeout=8, allow_redirects=True)
            ctype = resp.headers.get("Content-Type", "").lower()
            if resp.status_code == 200 and ("image" in ctype or any(img_url.lower().split("?")[0].endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp"))):
                table.add_row("7. Public Image URL", "[bold green]PASS[/bold green]", f"HTTP 200, Content-Type: {ctype or 'image'}")
            else:
                all_passed = False
                table.add_row("7. Public Image URL", "[bold red]FAIL[/bold red]", f"HTTP {resp.status_code}, Content-Type: {ctype}")
        except Exception as e:
            all_passed = False
            table.add_row("7. Public Image URL", "[bold red]FAIL[/bold red]", f"Failed to fetch {img_url}: {e}")
    else:
        all_passed = False
        table.add_row("7. Public Image URL", "[bold red]FAIL[/bold red]", "IMAGE_PUBLIC_URL not set in .env")

    console.print(table)

    if all_passed:
        console.print(Panel("[bold green]✓ All Preflight Checks Passed! Ready for live demonstration.[/bold green]", border_style="green"))
    else:
        console.print(Panel("[bold red]✗ Some checks failed. Please resolve the items marked FAIL above before recording.[/bold red]", border_style="red"))

    return all_passed


if __name__ == "__main__":
    passed = run_preflight()
    sys.exit(0 if passed else 1)
