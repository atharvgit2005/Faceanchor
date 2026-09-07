// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test, console} from "forge-std/Test.sol";
import {EvidenceRegistry} from "../src/EvidenceRegistry.sol";

contract EvidenceRegistryTest is Test {
    EvidenceRegistry public registry;

    bytes32 public constant TEST_EVIDENCE = 0x1111111111111111111111111111111111111111111111111111111111111111;
    bytes32 public constant TEST_IMAGE = 0x2222222222222222222222222222222222222222222222222222222222222222;
    bytes32 public constant TEST_PHASH = 0x3333333333333333333333333333333333333333333333333333333333333333;
    string public constant TEST_URI = "https://x.com/user/status/1234567890";

    function setUp() public {
        registry = new EvidenceRegistry();
    }

    function test_AttestAndReadBack() public {
        assertFalse(registry.isAttested(TEST_EVIDENCE));

        vm.expectEmit(true, true, false, true);
        emit EvidenceRegistry.Attested(
            TEST_EVIDENCE,
            address(this),
            TEST_IMAGE,
            TEST_PHASH,
            TEST_URI,
            uint64(block.timestamp)
        );

        registry.attest(TEST_EVIDENCE, TEST_IMAGE, TEST_PHASH, TEST_URI);

        assertTrue(registry.isAttested(TEST_EVIDENCE));

        EvidenceRegistry.Attestation memory att = registry.get(TEST_EVIDENCE);
        assertEq(att.attester, address(this));
        assertEq(att.timestamp, uint64(block.timestamp));
        assertEq(att.blockNumber, uint64(block.number));
        assertEq(att.imageHash, TEST_IMAGE);
        assertEq(att.phash, TEST_PHASH);
        assertEq(att.uri, TEST_URI);
    }

    function test_RevertOnDuplicateAttestation() public {
        registry.attest(TEST_EVIDENCE, TEST_IMAGE, TEST_PHASH, TEST_URI);

        vm.expectRevert(abi.encodeWithSelector(EvidenceRegistry.AlreadyAttested.selector, TEST_EVIDENCE));
        registry.attest(TEST_EVIDENCE, TEST_IMAGE, TEST_PHASH, TEST_URI);
    }
}
