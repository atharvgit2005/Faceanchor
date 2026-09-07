"""Smart contract interaction using web3.py v7/v8."""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional, cast

from eth_account import Account
from rich.table import Table
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from web3.types import TxParams

from pipeline.util import console


def get_w3() -> Web3:
    """Initialize Web3 provider, configure PoA middleware for Polygon Amoy if needed, and assert chain ID."""
    rpc_url = os.getenv("RPC_URL", "").strip()
    if not rpc_url:
        raise ValueError("RPC_URL is not set in environment or .env")

    chain_id_env = os.getenv("CHAIN_ID", "80002").strip()
    expected_chain_id = int(chain_id_env)

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise ConnectionError(f"Could not connect to Ethereum/Polygon RPC at: {rpc_url}")

    if expected_chain_id == 80002:
        # Polygon Amoy uses Proof of Authority (PoA); inject ExtraDataToPOAMiddleware at layer 0
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    try:
        actual_chain_id = w3.eth.chain_id
    except Exception as e:
        raise RuntimeError(f"Failed to query eth_chainId from RPC: {e}")

    if actual_chain_id != expected_chain_id:
        raise ValueError(
            f"Chain ID mismatch: RPC returned {actual_chain_id}, but CHAIN_ID is set to {expected_chain_id}"
        )

    return w3


def get_contract(w3: Web3, contract_address: Optional[str] = None):
    """Load EvidenceRegistry contract instance using pipeline/abi/EvidenceRegistry.json."""
    addr = contract_address or os.getenv("CONTRACT_ADDRESS", "").strip()
    if not addr:
        raise ValueError("CONTRACT_ADDRESS is not set in environment or .env")

    abi_path = Path("pipeline/abi/EvidenceRegistry.json")
    if not abi_path.exists():
        raise FileNotFoundError(f"Contract ABI not found at {abi_path}. Run forge inspect or make build.")

    with open(abi_path) as f:
        abi = json.load(f)

    return w3.eth.contract(address=Web3.to_checksum_address(addr), abi=abi)


def explorer_tx(tx_hash: str) -> str:
    """Return block explorer URL for a given transaction hash."""
    chain_id = int(os.getenv("CHAIN_ID", "80002"))
    clean_hash = tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"
    if chain_id == 80002:
        return f"https://amoy.polygonscan.com/tx/{clean_hash}"
    return "local anvil (no explorer)"


def explorer_address(addr: str) -> str:
    """Return block explorer URL for a given contract or wallet address."""
    chain_id = int(os.getenv("CHAIN_ID", "80002"))
    if chain_id == 80002:
        return f"https://amoy.polygonscan.com/address/{addr}"
    return "local anvil (no explorer)"


def attest(
    evidence_hash_hex: str,
    image_hash_hex: str,
    phash_hex: str,
    uri: str,
    out_dir: str = "out",
) -> dict[str, Any]:
    """Attest evidence record on-chain with idempotency check, EIP-1559 gas calculation, and event decoding."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    w3 = get_w3()
    contract = get_contract(w3)
    chain_id = int(os.getenv("CHAIN_ID", "80002"))

    private_key = os.getenv("PRIVATE_KEY", "").strip()
    if not private_key:
        raise ValueError("PRIVATE_KEY is not set in environment or .env")

    account = Account.from_key(private_key)
    balance_wei = w3.eth.get_balance(account.address)
    balance_pol = float(w3.from_wei(balance_wei, "ether"))

    console.print(f"Wallet Address: [bold cyan]{account.address}[/bold cyan]")
    console.print(f"Wallet Balance: [bold green]{balance_pol:.5f}[/bold green] Native Token (POL/ETH)")

    if balance_wei == 0:
        if chain_id == 80002:
            raise RuntimeError(
                f"Wallet {account.address} has 0 balance on Polygon Amoy. "
                "Please fund your wallet at https://faucet.polygon.technology (Select Polygon Amoy)."
            )
        else:
            raise RuntimeError(
                f"Wallet {account.address} has 0 balance on local network. Ensure Anvil is running with funded accounts."
            )

    # Prepare bytes32 values
    ev_clean = evidence_hash_hex.removeprefix("0x").zfill(64)
    img_clean = image_hash_hex.removeprefix("0x").zfill(64)
    ph_clean = phash_hex.removeprefix("0x").zfill(64)

    evidence_bytes32 = bytes.fromhex(ev_clean)
    image_bytes32 = bytes.fromhex(img_clean)
    phash_bytes32 = bytes.fromhex(ph_clean)

    # 1. Idempotency check: isAttested
    if contract.functions.isAttested(evidence_bytes32).call():
        console.print("[bold yellow]Idempotency Check:[/bold yellow] Evidence hash is already anchored on-chain.")
        att_raw = contract.functions.get(evidence_bytes32).call()
        existing_result = {
            "status": "already_anchored",
            "attester": att_raw[0],
            "timestamp": att_raw[1],
            "block_number": att_raw[2],
            "imageHash": "0x" + att_raw[3].hex(),
            "phash": "0x" + att_raw[4].hex(),
            "uri": att_raw[5],
            "contract": contract.address,
            "explorer_contract": explorer_address(contract.address),
        }
        return existing_result

    # 2. Build transaction
    nonce = w3.eth.get_transaction_count(account.address, "pending")
    gas_est = contract.functions.attest(
        evidence_bytes32, image_bytes32, phash_bytes32, uri
    ).estimate_gas({"from": account.address})
    gas_limit = int(gas_est * 1.25)

    try:
        fee_history = w3.eth.fee_history(1, "latest", [50])
        base_fee = fee_history["baseFeePerGas"][-1]
        priority_fee = w3.to_wei(30, "gwei") if chain_id == 80002 else w3.to_wei(1, "gwei")
        max_fee = int(base_fee * 1.5) + priority_fee
        tx_params = {
            "from": account.address,
            "nonce": nonce,
            "chainId": chain_id,
            "gas": gas_limit,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority_fee,
        }
    except Exception:
        tx_params = {
            "from": account.address,
            "nonce": nonce,
            "chainId": chain_id,
            "gas": gas_limit,
            "gasPrice": int(w3.eth.gas_price * 1.2),
        }

    tx = contract.functions.attest(
        evidence_bytes32, image_bytes32, phash_bytes32, uri
    ).build_transaction(cast(TxParams, tx_params))

    console.print("[cyan]Signing and broadcasting transaction to Polygon...[/cyan]")
    signed_tx = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    tx_hash_hex = "0x" + tx_hash.hex()
    console.print(f"[cyan]Transaction submitted: [bold white]{tx_hash_hex}[/bold white]. Awaiting receipt...[/cyan]")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    block = w3.eth.get_block(receipt["blockNumber"])
    block_timestamp = block.get("timestamp", int(time.time()))

    # Decode Attested event from receipt logs
    decoded_event = {}
    try:
        events = contract.events.Attested().process_receipt(receipt)
        if events:
            raw_args = dict(events[0]["args"])
            for k, v in raw_args.items():
                if isinstance(v, bytes):
                    decoded_event[k] = "0x" + v.hex()
                else:
                    decoded_event[k] = v
    except Exception as e:
        console.print(f"[yellow]Could not decode logs: {e}[/yellow]")

    result = {
        "status": "anchored",
        "tx_hash": tx_hash_hex,
        "block_number": receipt["blockNumber"],
        "timestamp": block_timestamp,
        "contract": contract.address,
        "explorer_tx": explorer_tx(tx_hash_hex),
        "explorer_contract": explorer_address(contract.address),
        "event": decoded_event,
    }

    # Append to out/anchor.json
    anchor_file = out_path / "anchor.json"
    history = []
    if anchor_file.exists():
        try:
            with open(anchor_file) as f:
                history = json.load(f)
        except Exception:
            history = []
    history.append(result)
    with open(anchor_file, "w") as f:
        json.dump(history, f, indent=2)

    return result


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(override=True)

    record_file = Path("out/record.json")
    if not record_file.exists():
        console.print("[bold red]out/record.json not found. Run Prompt 5 first.[/bold red]")
        sys.exit(1)

    with open(record_file) as f:
        data = json.load(f)

    ev_hash = data["bytes32"]["evidenceHash"]
    img_hash = data["bytes32"]["imageHash"]
    ph_hash = data["bytes32"]["phash"]
    post_url = data["record"]["match"]["post_url"]

    console.print(f"[bold cyan]Anchoring evidence to contract for URL: {post_url}[/bold cyan]")

    # Run 1: Attest
    res1 = attest(ev_hash, img_hash, ph_hash, post_url, out_dir="out")

    table = Table(title="Blockchain Attestation Summary")
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="white")

    table.add_row("Status", res1.get("status", ""))
    table.add_row("Contract", res1.get("contract", ""))
    table.add_row("Transaction Hash", res1.get("tx_hash", "N/A (already anchored)"))
    table.add_row("Block Number", str(res1.get("block_number", "")))
    table.add_row("Timestamp", str(res1.get("timestamp", "")))
    table.add_row("Explorer Link", res1.get("explorer_tx", res1.get("explorer_contract", "")))

    console.print(table)

    # Run 2: Idempotent re-run
    console.print("\n[bold cyan]Testing idempotency (Run 2)...[/bold cyan]")
    res2 = attest(ev_hash, img_hash, ph_hash, post_url, out_dir="out")
    assert res2["status"] == "already_anchored", f"Expected already_anchored, got {res2['status']}"
    console.print("[bold green]✓ Second run successfully detected 'already anchored' without sending a tx![/bold green]")
    console.print("[bold green]✓ Prompt 7 Definition of Done satisfied![/bold green]")
