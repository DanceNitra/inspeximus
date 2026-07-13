"""mnemo — a zero-dependency memory layer and MCP server for AI agents.

Public API (stable as of 1.0.0). Submodules for the governance/erasure tooling:
  - mnemo.deletion_manifest : DeletionManifest, ErasureTarget   (cross-store erasure record)
  - mnemo.erasure_auditor   : ErasureAuditor, StoreProbe, ...    ('content still reconstructible?' audit)
  - mnemo.mnemo_mcp         : the MCP stdio server (console script: mnemo-mcp)
"""
from .mnemo import (  # noqa: F401
    Mnemo,
    new_receipt_keypair,
    new_source_keypair,
    sign_revert,
    sign_erasure,
    erasure_challenge,
    attest,
    is_universal_executor,
    __version__,
)

__all__ = [
    "Mnemo",
    "new_receipt_keypair",
    "new_source_keypair",
    "sign_revert",
    "sign_erasure",
    "erasure_challenge",
    "attest",
    "is_universal_executor",
    "__version__",
]
