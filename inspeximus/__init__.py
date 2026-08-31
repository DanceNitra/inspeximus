"""inspeximus — a zero-dependency memory layer and MCP server for AI agents.

Public API (stable as of 1.0.0). Submodules for the governance/erasure tooling:
  - inspeximus.deletion_manifest : DeletionManifest, ErasureTarget   (cross-store erasure record)
  - inspeximus.erasure_auditor   : ErasureAuditor, StoreProbe, ...    ('content still reconstructible?' audit)
  - inspeximus.mcp_server         : the MCP stdio server (console script: inspeximus-mcp)
"""
from .core import (
    AmbiguousSubject,  # noqa: F401
    Inspeximus,
    new_receipt_keypair,
    receipt_key_for,
    new_source_keypair,
    sign_revert,
    sign_support,
    sign_erasure,
    erasure_challenge,
    verify_erasure_certificate,
    attest,
    derive_key,
    regex_extractor,
    make_llm_extractor,
    default_distiller,
    is_universal_executor,
    detect_pii,
    redact_pii,
    new_encryption_key,
    # 2.5.0. The README and the site advertise this as a headline capability while it existed only
    # as inspeximus.core.evaluate_applicability -- not exported, not a method, not an MCP tool. A
    # reader following the obvious import would have hit ImportError on the feature we led with.
    evaluate_applicability,
    __version__,
)

# 2.22.0. The auditor's half of erasure. `scan_residue` answers about a store we do NOT own, and
# the certificate turns that answer into a document a third party verifies without our key. Exported here
# because a capability reachable only as inspeximus.erasure_residue.residue_certificate is one a reader
# following the obvious import does not find, which is the defect recorded in the preceding note.
from .erasure_residue import (
    scan_residue,
    residue_certificate,
    verify_residue_certificate,
    certificate_drift,
    certificate_summary,
)

# 2.23.0. The auditor-facing pair: RFC 9943 Signed Statements over the RFC 9942 Receipts already
# emitted by cose.py. Exported because a capability reachable only as inspeximus.scitt.signed_statement
# is one a reader following the obvious import does not find.
from .scitt import (
    signed_statement,
    verify_signed_statement,
    transparent_statement,
    verify_transparent_statement,
    receipts_of,
    statement_digest,
)

# The QUALIFIED half of a timestamp. `stamp()` gets a token from any authority; these say whether the
# authority was a qualified EU service AT THE MOMENT it signed, which is a different question and the
# one eIDAS Article 41 turns on. Exported for the same reason as the block above: a reader following
# the obvious import does not find inspeximus.trusted_list.
from .timestamp import qualified_status, signer_certificate, certificates_in
from .trusted_list import TrustedList, parse_trusted_list, classify_status

__all__ = [
    "Inspeximus",
    "AmbiguousSubject",
    "new_receipt_keypair",
    "receipt_key_for",
    "new_source_keypair",
    "sign_revert",
    "sign_support",
    "sign_erasure",
    "erasure_challenge",
    "verify_erasure_certificate",
    "scan_residue",
    "qualified_status",
    "signer_certificate",
    "certificates_in",
    "TrustedList",
    "parse_trusted_list",
    "classify_status",
    "residue_certificate",
    "verify_residue_certificate",
    "certificate_drift",
    "certificate_summary",
    "signed_statement",
    "verify_signed_statement",
    "transparent_statement",
    "verify_transparent_statement",
    "receipts_of",
    "statement_digest",
    "attest",
    "derive_key",
    "regex_extractor",
    "make_llm_extractor",
    "default_distiller",
    "is_universal_executor",
    "detect_pii",
    "redact_pii",
    "new_encryption_key",
    "evaluate_applicability",
    "__version__",
]
