# Proof-of-Presence (`faceanchor`)

Proof-of-Presence: verify that a photo of you appears in a social post, then anchor a tamper-evident, independently re-verifiable record of that match on Polygon.

---

## Live Links

- **Contract Address (Polygon Amoy)**: [`0x55e17a101884cA1447Aba1ece6c21255327707B5`](https://amoy.polygonscan.com/address/0x55e17a101884cA1447Aba1ece6c21255327707B5) (source verified ✓)
- **Example Attestation TX**: [`0x220a1e2e10d2654b487370bc920e49f429737424ce97d680a7c0f7af94e12eff`](https://amoy.polygonscan.com/tx/0x220a1e2e10d2654b487370bc920e49f429737424ce97d680a7c0f7af94e12eff)
- **Demo Video**: _TODO before submit_

---

## Pipeline Architecture

```
                           [ Input Reference Photo ]
                               (samples/me.jpg)
                                       │
                                       ▼
              ┌──────────────────────────────────────────────────┐
              │ 1. Normalization & Dual Hashing                  │
              │    • EXIF transpose & RGB normalization          │
              │    • Cryptographic SHA-256                       │
              │    • 256-bit perceptual hash (16×16 DCT pHash)   │
              └────────────────────────┬─────────────────────────┘
                                       │
                                       ▼
              ┌──────────────────────────────────────────────────┐
              │ 2. Face Detection & Salted Commitment            │
              │    • DeepFace (Facenet512 + RetinaFace)          │
              │    • Bounding box crop (face_crop.jpg)           │
              │    • Salted SHA-256 commitment hash              │
              │    • ZERO biometrics stored on-chain             │
              └────────────────────────┬─────────────────────────┘
                                       │
                                       ▼
              ┌──────────────────────────────────────────────────┐
              │ 3. Multi-Engine Reverse Search                   │
              │    • SerpAPI: Google Lens + Yandex               │
              │    • URL canonicalization & deduplication        │
              │    • Social domain prioritization                │
              └────────────────────────┬─────────────────────────┘
                                       │
                                       ▼
              ┌──────────────────────────────────────────────────┐
              │ 4. Face Matching with Corroboration              │
              │    • Cosine distance against reference           │
              │    • Multi-engine discount multiplier            │
              │    • Threshold evaluation (0.35)                 │
              └────────────────────────┬─────────────────────────┘
                                       │
                                       ▼
              ┌──────────────────────────────────────────────────┐
              │ 5. Canonical Deterministic Record                │
              │    • Deterministic JSON Schema (v1)              │
              │    • evidenceHash = SHA-256(Record)              │
              │    • Anchor on Polygon (EvidenceRegistry)        │
              └────────────────────────┬─────────────────────────┘
                                       │
                                       ▼
              ┌──────────────────────────────────────────────────┐
              │ 6. Independent Verification (TX hash only)       │
              │    • Verify with ONLY the TX hash                │
              │    • Live re-fetch & dual-hash compare           │
              │    • EXACT / SAME_IMAGE_REENCODED /              │
              │      TAMPERED_OR_REPLACED                        │
              └──────────────────────────────────────────────────┘
```

---

## Why This is Different

- **Zero-Query Reverse Search**: The search request contains only the image URL — no handle, name, or text query — and a match must come from search results; there is no manual-URL path.
- **Real Smart Contract with Indexed Events**: State is immutably recorded in storage and emitted via indexed events on Polygon, not hidden in arbitrary transaction calldata.
- **Independent Verification from TX Hash Alone**: Anyone with RPC access can independently verify an attestation with zero access to local run artifacts.
- **SHA-256 + Perceptual Hashing (pHash)**: Cryptographic SHA-256 verifies bit-for-bit identity, while 256-bit perceptual hash (16×16 DCT pHash) tolerates compression artifacts, resizing, and CDN re-encoding up to Hamming distance ≤ 8.
- **Multi-Engine Corroboration**: Candidates discovered across multiple independent search engines (Google Lens + Yandex) receive a corroboration discount.
- **Weak-Match Fallback**: If no candidate meets the primary threshold, a fallback threshold (threshold × 1.3) is applied strictly to candidates from social domains.
- **Consent-First, No Biometrics On-Chain**: Raw 512-dimensional embeddings and secrets remain local in `out/face.json`. Only a salted SHA-256 commitment is anchored to the blockchain.

---

## Scope & Ethics

- **Consent-Based Self-Verification**: Explicitly designed to allow individuals to cryptographically prove that a public image of themselves appeared in an external social media post.
- **Not a People-Search Engine**: The system does not index or catalog arbitrary individuals.
- **Strict Authorization Boundary**: The CLI enforces a mandatory `--consent` flag affirming that the user has the legal right to verify the subject's likeness.

---

## Setup & Prerequisites

### Prerequisites
- Python 3.11 (`python3.11`)
- Foundry (`forge` and `anvil`)
- Polygon Amoy testnet funds (or local Anvil fallback)
- SerpAPI API key

### Environment Configuration (`.env`)
```bash
cp .env.example .env
```
Fill in the required keys:
- `SERPAPI_KEY`: API key from [serpapi.com](https://serpapi.com).
- `RPC_URL`: Alchemy Polygon Amoy HTTPS URL (`https://polygon-amoy.g.alchemy.com/v2/...`), or `http://127.0.0.1:8545` for local Anvil.
- `CHAIN_ID`: `80002` for Amoy, or `31337` for Anvil.
- `PRIVATE_KEY`: Throwaway test wallet private key with `0x` prefix.
- `CONTRACT_ADDRESS`: Deployed contract address on Amoy/Anvil (printed by `scripts/deploy.sh` after deployment; copy and paste it here).
- `POLYGONSCAN_API_KEY`: Free API key from [polygonscan.com](https://polygonscan.com/myapikey) for contract verification.
- `IMAGE_PUBLIC_URL`: Raw GitHub URL or public image URL of your reference photo.
- `EMBEDDING_SALT`: Random 32-byte hex salt (generated once, never committed).
- `FACE_THRESHOLD`: `0.35` (default cosine distance threshold; weak-match fallback is threshold × 1.3 strictly for social candidates).

### Faucet & Anvil Fallback
- Fund your wallet address at [faucet.polygon.technology](https://faucet.polygon.technology) (Select Polygon Amoy).
- **Anvil Fallback**: If the testnet faucet is unresponsive, launch Anvil in a background terminal:
  ```bash
  anvil --port 8545
  ```
  Set `RPC_URL=http://127.0.0.1:8545` and `CHAIN_ID=31337` in `.env`.

### Smart Contract Deployment
```bash
bash scripts/deploy.sh
```
The script compiles the contract, exports the fresh ABI, deploys to your configured RPC, and prints the deployed address. Copy the resulting contract address into `.env` as `CONTRACT_ADDRESS`.

---

## Execution Guide

### 1. Download Model Weights
```bash
make warm
```
Pre-downloads `Facenet512` and `retinaface` weights to `~/.deepface/weights`.

### 2. Run Preflight Diagnostics
```bash
make preflight
```
Validates environment variables, RPC connectivity, wallet balance, contract bytecode, SerpAPI quota, and image URL reachability.

### 3. Run Full Pipeline Demo
```bash
make demo
```
Executes the full 6-step pipeline from local image normalization through on-chain anchoring and independent verification.

### 4. Independent Verification & Demo Commands
```bash
# Verify from an on-chain transaction hash alone:
python -m pipeline.verify --tx 0x...

# Verify local record JSON against on-chain hash:
python -m pipeline.verify --tx 0x... --record out/record.json

# Demo: Tampered record detection (modifies 1 char in memory without touching disk):
python -m pipeline.verify --tx 0x... --demo-tamper

# Demo: Wrong / Replaced image detection (pHash distance explodes):
python -m pipeline.verify --tx 0x... --demo-wrong-image samples/other.jpg
```

---

## Blockchain Details

- **Network**: Polygon Amoy Testnet (Chain ID `80002`) / Local Anvil (Chain ID `31337`)
- **Contract**: [`contracts/EvidenceRegistry.sol`](contracts/EvidenceRegistry.sol) (Solidity `0.8.24`, MIT)
- **Data Stored On-Chain**:
  - `evidenceHash`: Deterministic SHA-256 of the complete canonical evidence record.
  - `imageHash`: SHA-256 of the matched post's raw image bytes.
  - `phash`: 256-bit perceptual hash (16×16 DCT pHash) (stored as `bytes32`).
  - `uri`: The social media post URL.
  - `timestamp`: Block timestamp.
  - `blockNumber`: Block number of attestation.
  - `attester`: Ethereum address of the caller.
- **Manual Verification on Polygonscan**:
  1. Navigate to `https://amoy.polygonscan.com/address/<CONTRACT_ADDRESS>#readContract`.
  2. Call `isAttested(bytes32 evidenceHash)` -> returns `true`.
  3. Call `get(bytes32 evidenceHash)` -> inspects full struct (`attester`, `timestamp`, `blockNumber`, `imageHash`, `phash`, `uri`).

---

## Known Limitations

- **Input Image Authenticity**: Input must be the original posted image file, not a screenshot or phone original; screenshots defeat reverse image search.
- **Search-Engine-Served Image Hashes**: `imageHash` is computed over the search-engine-served copy of the matched image because Instagram blocks unauthenticated fetches; `pHash` confirms it's the same picture as the input.
- **Profile-Level vs. Post-Level URLs**: When reverse search returns a profile page rather than a post, the profile URL is anchored and explicitly noted in the record.
- **Instagram Carousel Posts & Private Accounts**: Instagram carousel posts: only slide 1 is exposed via og:image, so later slides are never indexed and cannot be found. Private accounts are invisible to search engines. In both cases the pipeline rejects look-alikes (observed 0.53–0.94 vs threshold 0.35) rather than forcing a match.
- **Time-Limited CDN URLs**: Instagram CDN image URLs are time-limited; after expiry verification falls back to og:image, which Instagram may block for unauthenticated fetches.
- **Search Engine Indexing Latency**: Reverse image search depends on external indexation by Google Lens and Yandex; very recent posts may experience indexing latency.
- **Thumbnail Resolution**: Candidate images returned by search engines are lower resolution thumbnails; the cosine distance threshold is set accordingly (`FACE_THRESHOLD=0.35`).
- **Single Dominant Face**: The pipeline selects the largest face detected by area per input image.
- **Anti-Scraping Restrictions**: Platforms like Instagram or X may restrict direct HTML scraping; an `--image-url` CLI override is provided in `verify.py`.
- **Testnet Volatility**: Testnets are subject to RPC rate limits and faucet availability.
- **SerpAPI Search Quota**: Queries consume SerpAPI search credits.
- **Salt Secrecy**: The privacy guarantees of the embedding commitment depend on `EMBEDDING_SALT` remaining private.

---

## Tech Stack & License

- **Runtime**: Python 3.11
- **Computer Vision**: DeepFace (`Facenet512`), RetinaFace, OpenCV, Pillow, pillow-heif, imagehash
- **Blockchain**: Foundry (`forge`, `anvil`), Solidity `^0.8.20` (`0.8.24`), `web3.py` v8, `eth-account`
- **Reverse Search**: SerpAPI (`google-search-results`), requests, BeautifulSoup4
- **CLI & Formatting**: Rich
- **License**: MIT
