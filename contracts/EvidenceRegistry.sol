// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title EvidenceRegistry
 * @notice Stores tamper-evident cryptographic attestations of visual evidence records.
 */
contract EvidenceRegistry {
    struct Attestation {
        address attester;
        uint64 timestamp;
        uint64 blockNumber;
        bytes32 imageHash;
        bytes32 phash;
        string uri;
    }

    mapping(bytes32 => Attestation) private attestations;

    event Attested(
        bytes32 indexed evidenceHash,
        address indexed attester,
        bytes32 imageHash,
        bytes32 phash,
        string uri,
        uint64 timestamp
    );

    error AlreadyAttested(bytes32 evidenceHash);

    function attest(
        bytes32 evidenceHash,
        bytes32 imageHash,
        bytes32 phash,
        string calldata uri
    ) external {
        if (attestations[evidenceHash].timestamp != 0) {
            revert AlreadyAttested(evidenceHash);
        }

        attestations[evidenceHash] = Attestation({
            attester: msg.sender,
            timestamp: uint64(block.timestamp),
            blockNumber: uint64(block.number),
            imageHash: imageHash,
            phash: phash,
            uri: uri
        });

        emit Attested(
            evidenceHash,
            msg.sender,
            imageHash,
            phash,
            uri,
            uint64(block.timestamp)
        );
    }

    function get(bytes32 evidenceHash) external view returns (Attestation memory) {
        return attestations[evidenceHash];
    }

    function isAttested(bytes32 evidenceHash) external view returns (bool) {
        return attestations[evidenceHash].timestamp != 0;
    }
}
