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

__all__ = [
    "Inspeximus",
    "AmbiguousSubject",
    "new_receipt_keypair",
    "new_source_keypair",
    "sign_revert",
    "sign_support",
    "sign_erasure",
    "erasure_challenge",
    "verify_erasure_certificate",
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
