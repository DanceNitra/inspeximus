"""inspeximus — a zero-dependency memory layer and MCP server for AI agents.

Public API (stable as of 1.0.0). Submodules for the governance/erasure tooling:
  - inspeximus.deletion_manifest : DeletionManifest, ErasureTarget   (cross-store erasure record)
  - inspeximus.erasure_auditor   : ErasureAuditor, StoreProbe, ...    ('content still reconstructible?' audit)
  - inspeximus.mcp_server         : the MCP stdio server (console script: inspeximus-mcp)
"""
import importlib as _importlib
from typing import TYPE_CHECKING as _TYPE_CHECKING

from .core import (
    AmbiguousSubject,  # noqa: F401
    WriteBlocked,  # noqa: F401
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

# THE GOVERNANCE EXPORTS LOAD ON FIRST USE (3.15.0, AUDIT-B B-19). Every name below is still exported,
# listed by dir() and bound by `from inspeximus import *`; its module is imported the first time the
# name, or the submodule itself, is read from the package. The Claude Code hook is a new process per
# event, and importing it ran all eleven of these modules although it calls none of them: 208 ms per
# event with them and 148 ms without, measured with 7 interleaved fresh interpreters each. None of them
# changes core at import, and tests/test_audit_b_the_hook_imports_only_what_it_uses.py holds the public
# surface to the one 3.14.3 had.
#
#: public name -> (submodule, attribute in it)
_LAZY = {
    # 2.22.0. The auditor's half of erasure. `scan_residue` answers about a store we do NOT own, and
    # the certificate turns that answer into a document a third party verifies without our key. Exported here
    # because a capability reachable only as inspeximus.erasure_residue.residue_certificate is one a reader
    # following the obvious import does not find, which is the defect recorded in the preceding note.
    "scan_residue": ("erasure_residue", "scan_residue"),
    "residue_certificate": ("erasure_residue", "residue_certificate"),
    "verify_residue_certificate": ("erasure_residue", "verify_residue_certificate"),
    "certificate_drift": ("erasure_residue", "certificate_drift"),
    "certificate_summary": ("erasure_residue", "certificate_summary"),
    # 2.23.0. The auditor-facing pair: RFC 9943 Signed Statements over the RFC 9942 Receipts already
    # emitted by cose.py. Exported because a capability reachable only as inspeximus.scitt.signed_statement
    # is one a reader following the obvious import does not find.
    "signed_statement": ("scitt", "signed_statement"),
    "verify_signed_statement": ("scitt", "verify_signed_statement"),
    "transparent_statement": ("scitt", "transparent_statement"),
    "verify_transparent_statement": ("scitt", "verify_transparent_statement"),
    "receipts_of": ("scitt", "receipts_of"),
    "statement_digest": ("scitt", "statement_digest"),
    # The QUALIFIED half of a timestamp. `stamp()` gets a token from any authority; these say whether the
    # authority was a qualified EU service AT THE MOMENT it signed, which is a different question and the
    # one eIDAS Article 41 turns on. Exported for the same reason as the block above: a reader following
    # the obvious import does not find inspeximus.trusted_list.
    "qualified_status": ("timestamp", "qualified_status"),
    "signer_certificate": ("timestamp", "signer_certificate"),
    "certificates_in": ("timestamp", "certificates_in"),
    "TrustedList": ("trusted_list", "TrustedList"),
    "parse_trusted_list": ("trusted_list", "parse_trusted_list"),
    "classify_status": ("trusted_list", "classify_status"),
    "ActionLedger": ("actions", "ActionLedger"),
    "export_subject": ("subject_rights", "export_subject"),
    "rectify": ("subject_rights", "rectify"),
    "annex_iv": ("technical_documentation", "annex_iv"),
    "instructions_for_use": ("technical_documentation", "instructions_for_use"),
    "registration_export": ("technical_documentation", "registration_export"),
    "deployer_report": ("deployer", "deployer_report"),
    "dpia_appendix": ("deployer", "dpia_appendix"),
    "fria_appendix": ("deployer", "fria_appendix"),
    "export_audit_trail": ("agent_audit_trail", "export_jsonl"),
    "verify_audit_trail": ("agent_audit_trail", "verify_jsonl"),
    "Partitions": ("partitions", "Partitions"),
}

#: The submodules `import inspeximus` made reachable as attributes when this file imported them eagerly
#: (3.14.3 and earlier). `cose` was reachable through `scitt`. They stay reachable, loaded on first read.
_LAZY_MODULES = frozenset({"actions", "agent_audit_trail", "cose", "deployer", "erasure_residue",
                           "partitions", "scitt", "subject_rights", "technical_documentation", "timestamp",
                           "trusted_list"})

if _TYPE_CHECKING:  # static analysers see the names; the interpreter never runs this block
    from .erasure_residue import (scan_residue, residue_certificate, verify_residue_certificate,
                                  certificate_drift, certificate_summary)
    from .scitt import (signed_statement, verify_signed_statement, transparent_statement,
                        verify_transparent_statement, receipts_of, statement_digest)
    from .timestamp import qualified_status, signer_certificate, certificates_in
    from .trusted_list import TrustedList, parse_trusted_list, classify_status
    from .actions import ActionLedger
    from .subject_rights import export_subject, rectify
    from .technical_documentation import annex_iv, instructions_for_use, registration_export
    from .deployer import deployer_report, dpia_appendix, fria_appendix
    from .agent_audit_trail import export_jsonl as export_audit_trail, verify_jsonl as verify_audit_trail
    from .partitions import Partitions


def __getattr__(name):
    """Import a governance export, or one of the submodules listed above, the first time it is read."""
    if name in _LAZY:
        mod, attr = _LAZY[name]
        value = getattr(_importlib.import_module("." + mod, __name__), attr)
    elif name in _LAZY_MODULES:
        value = _importlib.import_module("." + name, __name__)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value


#: Names this file defines for its own use, which dir() leaves out so it lists what 3.14.3 listed.
_INTERNAL = frozenset({"_importlib", "_TYPE_CHECKING", "_LAZY", "_LAZY_MODULES", "_INTERNAL",
                       "__getattr__", "__dir__"})


def __dir__():
    return sorted((set(globals()) - _INTERNAL) | set(_LAZY) | _LAZY_MODULES)


__all__ = [
    "Inspeximus",
    "ActionLedger",
    "export_subject",
    "rectify",
    "annex_iv",
    "instructions_for_use",
    "registration_export",
    "deployer_report",
    "dpia_appendix",
    "fria_appendix",
    "export_audit_trail",
    "verify_audit_trail",
    "Partitions",
    "AmbiguousSubject",
    "WriteBlocked",
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
