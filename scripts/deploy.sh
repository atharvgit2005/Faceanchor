#!/usr/bin/env bash
set -euo pipefail

# Ensure foundry binaries are on PATH
export PATH="$HOME/.foundry/bin:$PATH"

# Load environment variables
if [ -f .env ]; then
    set -a
    source .env
    set +a
else
    echo "Error: .env file not found. Please create one from .env.example" >&2
    exit 1
fi

if [ -z "${RPC_URL:-}" ]; then
    echo "Error: RPC_URL is not set in .env" >&2
    exit 1
fi

if [ -z "${PRIVATE_KEY:-}" ]; then
    echo "Error: PRIVATE_KEY is not set in .env" >&2
    exit 1
fi

CHAIN_ID="${CHAIN_ID:-80002}"

echo "=========================================="
echo " Deploying EvidenceRegistry.sol"
echo " RPC: $RPC_URL (Chain ID: $CHAIN_ID)"
echo "=========================================="

cd foundry

# Recompile contract and export fresh ABI
echo "Compiling and exporting ABI..."
forge inspect src/EvidenceRegistry.sol:EvidenceRegistry abi --json > ../pipeline/abi/EvidenceRegistry.json

# Deploy using forge create
echo "Deploying contract via forge create..."
DEPLOY_OUTPUT=$(forge create src/EvidenceRegistry.sol:EvidenceRegistry \
    --rpc-url "$RPC_URL" \
    --private-key "$PRIVATE_KEY" \
    --gas-price 50gwei \
    --priority-gas-price 30gwei \
    --broadcast \
    --json)

# Parse deployedTo address
DEPLOYED_ADDRESS=$(echo "$DEPLOY_OUTPUT" | grep -o '"deployedTo":"[^"]*"' | head -n1 | cut -d'"' -f4 || true)

if [ -z "$DEPLOYED_ADDRESS" ]; then
    # Fallback jq or python parsing
    DEPLOYED_ADDRESS=$(python3 -c "import json, sys; data=json.loads('''$DEPLOY_OUTPUT'''); print(data.get('deployedTo', ''))" 2>/dev/null || true)
fi

if [ -z "$DEPLOYED_ADDRESS" ]; then
    echo "Error: Failed to parse deployed contract address from output:" >&2
    echo "$DEPLOY_OUTPUT" >&2
    exit 1
fi

echo ""
echo "=========================================="
echo " Contract successfully deployed to:"
echo " $DEPLOYED_ADDRESS"
echo "=========================================="
echo ""
echo "ACTION REQUIRED: Please set CONTRACT_ADDRESS in your .env:"
echo "CONTRACT_ADDRESS=$DEPLOYED_ADDRESS"

# Polygonscan verification if Amoy (80002) and POLYGONSCAN_API_KEY is provided
if [ "$CHAIN_ID" = "80002" ] && [ -n "${POLYGONSCAN_API_KEY:-}" ]; then
    echo ""
    echo "Attempting automated source code verification on Polygonscan Amoy..."
    set +e
    forge verify-contract "$DEPLOYED_ADDRESS" src/EvidenceRegistry.sol:EvidenceRegistry \
        --chain-id 80002 \
        --etherscan-api-key "$POLYGONSCAN_API_KEY" \
        --verifier-url "https://api-amoy.polygonscan.com/api" \
        --watch || {
        echo "Note: Automated verification command completed. If unverified, use manual 'Verify & Publish' on amoy.polygonscan.com:"
        echo "  Compiler: 0.8.24, Optimization: Yes (200 runs), Single File src/EvidenceRegistry.sol"
    }
    set -e
fi

cd ..
exit 0
