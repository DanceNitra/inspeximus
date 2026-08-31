"""Qualified standing is held ON A DATE, and a check that ignores the date is a different check.

Measured across the 25 national trusted lists reachable on 2026-08-31: 1477 qualified timestamp
services, of which 570 (39 percent) have held both a qualified and a non-qualified status at
different times. So "is this authority on the list" and "was this authority qualified when it signed
this" disagree for two services in five, and only the second one is the question an auditor asks.

The fixtures here are REAL published bytes, not invented ones:

  trusted_list_at_excerpt.xml   one Austrian service that went accredited (2015) -> withdrawn (2016)
                                -> granted (2018). One certificate, four verdicts, and the only
                                thing that changes between them is the date.
  trusted_list_es_excerpt.xml   the Izenpe service whose live TSA signed the token below.
  izenpe_granted.tsr            a real token from a QUALIFIED authority       (positive control)
  digicert_granted.tsr          a real token from a valid NON-EU authority    (negative control)

The pair of tokens is the point. Both are genuine, both carry PKIStatus granted, and a check that
could not tell them apart would pass everything.
"""
from __future__ import annotations

import datetime as dt
import os

import pytest

from inspeximus.timestamp import (certificates_in, qualified_status, read_status,
                                  signer_certificate)
from inspeximus.trusted_list import (TrustedList, TrustedListError, cert_sha256, classify_status,
                                     list_pointers, parse_trusted_list, ski_of)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name, binary=False):
    path = os.path.join(FIXTURES, name)
    with open(path, "rb" if binary else "r", **({} if binary else {"encoding": "utf-8"})) as fh:
        return fh.read()


@pytest.fixture(scope="module")
def austria():
    return parse_trusted_list(_read("trusted_list_at_excerpt.xml"))


@pytest.fixture(scope="module")
def spain():
    return parse_trusted_list(_read("trusted_list_es_excerpt.xml"))


@pytest.fixture(scope="module")
def izenpe_token():
    return _read("izenpe_granted.tsr", binary=True)


@pytest.fixture(scope="module")
def digicert_token():
    return _read("digicert_granted.tsr", binary=True)


# ── the core claim: one certificate, four answers, only the date changes ────────────────────────────
@pytest.mark.parametrize("when, verdict, qualified", [
    ("2014-01-01T00:00:00Z", "UNKNOWN_BEFORE_THE_LISTS_EARLIEST_RECORD", None),
    ("2015-09-01T00:00:00Z", "LISTED_UNDER_A_STATUS_THIS_LIBRARY_WILL_NOT_JUDGE", None),
    ("2017-01-01T00:00:00Z", "LISTED_BUT_NOT_QUALIFIED_AT_THE_TIME", False),
    ("2019-01-01T00:00:00Z", "QUALIFIED_AT_THE_TIME", True),
])
def test_one_service_gives_four_different_answers_across_its_own_history(austria, when, verdict,
                                                                        qualified):
    service = austria.services[0]
    ski = sorted(service.identifiers["skis"])[0]
    got = austria.qualified_at(when, ski=ski)
    assert got["verdict"] == verdict, got
    assert got["qualified"] is qualified, got
    # Whatever the verdict, the service was found. A run that stopped matching would produce
    # NOT_ON_ANY_LIST_LOADED for every row and this parametrize would still look like it exercised
    # four cases.
    assert got["matched"], "the signer stopped matching, so none of these rows tested a date"


def test_the_four_answers_are_actually_four(austria):
    service = austria.services[0]
    ski = sorted(service.identifiers["skis"])[0]
    seen = {austria.qualified_at(w, ski=ski)["verdict"]
            for w in ("2014-01-01T00:00:00Z", "2015-09-01T00:00:00Z",
                      "2017-01-01T00:00:00Z", "2019-01-01T00:00:00Z")}
    assert len(seen) == 4, seen


def test_unknown_is_not_the_same_answer_as_no(austria):
    """A gap in the record and a withdrawal must not collapse into one verdict.

    Both are "not qualified" to a caller reading only the boolean, and they call for opposite
    actions: one means find an older list, the other means the timestamp is worth less than the
    operator thinks.
    """
    ski = sorted(austria.services[0].identifiers["skis"])[0]
    early = austria.qualified_at("2014-01-01T00:00:00Z", ski=ski)
    withdrawn = austria.qualified_at("2017-01-01T00:00:00Z", ski=ski)
    assert early["qualified"] is None and withdrawn["qualified"] is False
    assert early["verdict"] != withdrawn["verdict"]


# ── the two real tokens ────────────────────────────────────────────────────────────────────────────
def test_a_token_from_a_qualified_authority_is_reported_qualified(spain, izenpe_token):
    assert read_status(izenpe_token)["status_text"] == "granted"
    got = qualified_status(izenpe_token, spain, when="2026-08-31T12:00:00Z")
    assert got["verdict"] == "QUALIFIED_AT_THE_TIME", got
    assert got["qualified"] is True
    assert got["matched"][0]["matched_by"] == "certificate", got["matched"]
    assert got["matched"][0]["territory"] == "ES"


def test_a_valid_token_from_an_unlisted_authority_is_reported_unlisted(spain, digicert_token):
    """The negative control, and it is a REAL token rather than corrupted bytes.

    DigiCert's authority is genuine and its token is granted: it fails this check only because it is
    not on an EU trusted list, which is the single property being tested. A check that could not
    produce this answer would report every timestamp as qualified.
    """
    assert read_status(digicert_token)["status_text"] == "granted"
    got = qualified_status(digicert_token, spain, when="2026-08-31T12:00:00Z")
    assert got["verdict"] == "NOT_ON_ANY_LIST_LOADED", got
    assert got["qualified"] is False
    assert got["matched"] == []


def test_the_two_tokens_disagree(spain, izenpe_token, digicert_token):
    """Guards against a fixture that stopped discriminating. If the list ever fails to load, both
    tokens return the same verdict and each test above would still pass on its own."""
    a = qualified_status(izenpe_token, spain, when="2026-08-31T12:00:00Z")
    b = qualified_status(digicert_token, spain, when="2026-08-31T12:00:00Z")
    assert a["qualified"] is not b["qualified"]


# ── reading the signer out of a token ──────────────────────────────────────────────────────────────
def test_the_signer_is_chosen_by_its_extended_key_usage(digicert_token):
    certs = certificates_in(digicert_token)
    assert len(certs) >= 2, "expected the signer and its chain"
    signer = signer_certificate(digicert_token)
    assert signer is not None
    assert signer in certs
    # id-kp-timeStamping. RFC 3161 section 2.3 requires it on the signer and forbids other purposes,
    # so this is a property of the role rather than of position in the file.
    assert bytes([0x06, 0x08, 0x2B, 0x06, 0x01, 0x05, 0x05, 0x07, 0x03, 0x08]) in signer


def test_an_extracted_certificate_is_the_complete_encoding(spain, izenpe_token):
    """The extracted bytes must be the whole TLV, header included.

    A body-only slice still looks like a certificate and still has a SHA-256, so nothing raises. It
    simply matches no entry in any trusted list, and every authority in Europe reads as unlisted.
    """
    signer = signer_certificate(izenpe_token)
    assert signer[0] == 0x30, "a DER certificate starts with a SEQUENCE tag"
    listed = spain.services[0].identifiers["certificates"]
    assert cert_sha256(signer) in listed


def test_a_token_that_names_no_signer_is_not_an_absence_from_the_list(spain):
    got = qualified_status(b"\x30\x03\x02\x01\x00", spain)
    assert got["verdict"] == "NO_SIGNER_CERTIFICATE_IN_THE_TOKEN"
    assert got["qualified"] is None
    assert got["signer_found"] is False


def test_ski_is_read_from_the_extension_and_not_from_any_matching_bytes(izenpe_token):
    signer = signer_certificate(izenpe_token)
    got = ski_of(signer)
    assert got and len(got) == 40, got                      # a 20-byte SHA-1 key identifier
    assert ski_of(b"\x06\x03\x55\x1d\x0e" + b"\xff" * 40) is None, \
        "the OID appearing in arbitrary bytes must not be read as a key identifier"


# ── an empty or unloaded list must not answer "no" ──────────────────────────────────────────────────
def test_an_empty_list_checked_nothing_and_says_so(izenpe_token):
    got = qualified_status(izenpe_token, TrustedList([]))
    assert got["verdict"] == "NOTHING_LOADED"
    assert got["qualified"] is None, "an empty list must never report a signer as unqualified"


def test_an_absence_is_scoped_to_what_was_loaded(spain, digicert_token):
    got = qualified_status(digicert_token, spain, when="2026-08-31T12:00:00Z")
    joined = " ".join(got["problems"])
    assert "not within the EU" in joined
    assert got["territories_loaded"] == ["ES"]


# ── the weakest match is not accepted silently ─────────────────────────────────────────────────────
def test_a_subject_name_alone_does_not_prove_membership(spain):
    subject = sorted(spain.services[0].identifiers["subjects"])[0]
    assert spain.lookup(subject=subject), "the fixture must contain this subject for the test to mean anything"
    assert spain.qualified_at(None, subject=subject)["verdict"] == "NOT_ON_ANY_LIST_LOADED"
    opted_in = spain.qualified_at("2026-08-31T12:00:00Z", subject=subject,
                                  accept_subject_match=True)
    assert opted_in["verdict"] == "QUALIFIED_AT_THE_TIME"
    assert opted_in["matched"][0]["matched_by"] == "subject"


# ── the pointer bug that cost nine countries ───────────────────────────────────────────────────────
_LOTL = """<?xml version="1.0" encoding="UTF-8"?>
<TrustServiceStatusList xmlns="http://uri.etsi.org/02231/v2#"
                        xmlns:ns3="http://uri.etsi.org/02231/v2/additionaltypes#">
  <SchemeInformation><PointersToOtherTSL>
    <OtherTSLPointer>
      <TSLLocation>https://example.test/fr-tsl.pdf</TSLLocation>
      <AdditionalInformation>
        <OtherInformation><SchemeTerritory>FR</SchemeTerritory></OtherInformation>
        <OtherInformation><ns3:MimeType>application/pdf</ns3:MimeType></OtherInformation>
      </AdditionalInformation>
    </OtherTSLPointer>
    <OtherTSLPointer>
      <TSLLocation>https://example.test/fr-tsl.xml</TSLLocation>
      <AdditionalInformation>
        <OtherInformation><SchemeTerritory>FR</SchemeTerritory></OtherInformation>
        <OtherInformation>
          <ns3:MimeType>application/vnd.etsi.tsl+xml</ns3:MimeType>
        </OtherInformation>
      </AdditionalInformation>
    </OtherTSLPointer>
  </PointersToOtherTSL></SchemeInformation>
</TrustServiceStatusList>
"""


def test_the_pdf_pointer_is_not_taken_for_the_machine_readable_one():
    """The PDF is listed FIRST here, which is the shape that produced the bug.

    Every country is pointed at twice, and taking the first pointer per territory fetched the PDF for
    9 of 31 countries, France and Spain among them. Nothing raised: the fetch returned 200, the file
    was 400 KB, and it parsed as zero qualified services. A caller would have read that as "France
    lists no qualified timestamp authority".
    """
    assert list_pointers(_LOTL) == {"FR": "https://example.test/fr-tsl.xml"}
    assert len(list_pointers(_LOTL, mime=None)) == 1, "both pointers collapse onto one territory"


def test_a_pdf_does_not_parse_as_an_empty_list_of_services():
    """The second half of the same defect: a wrong document must refuse, not read as an absence."""
    with pytest.raises(TrustedListError):
        parse_trusted_list("%PDF-1.4\n%\xe2\xe3\xcf\xd3\n4 0 obj\n<</Filter/FlateDecode>>")


def test_a_document_declaring_a_doctype_is_refused():
    with pytest.raises(TrustedListError):
        parse_trusted_list('<?xml version="1.0"?><!DOCTYPE t [<!ENTITY a "b">]><t/>')


# ── statuses this library refuses to judge ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("status, expected", [
    ("granted", "qualified"),
    ("withdrawn", "not_qualified"),
    ("supervisionceased", "not_qualified"),
    ("accreditationrevoked", "not_qualified"),
    ("accredited", "undetermined"),
    ("undersupervision", "undetermined"),
    ("recognisedatnationallevel", "undetermined"),
])
def test_each_published_status_lands_where_it_should(status, expected):
    assert classify_status("http://uri.etsi.org/TrstSvc/TrustedList/Svcstatus/" + status) == expected


def test_a_status_we_have_never_seen_is_undetermined_rather_than_denied():
    """A status this library does not understand must not be reported as a denial. Reporting a
    misunderstanding as a no is how a confident wrong answer gets published."""
    assert classify_status("http://uri.etsi.org/TrstSvc/TrustedList/Svcstatus/somethingnew") \
        == "undetermined"


# ── times ──────────────────────────────────────────────────────────────────────────────────────────
def test_a_naive_datetime_is_read_as_utc_rather_than_refused(spain):
    got = spain.qualified_at(dt.datetime(2026, 8, 31, 12, 0, 0))
    assert got["verdict"] in ("NOT_ON_ANY_LIST_LOADED", "QUALIFIED_AT_THE_TIME")


def test_a_status_starting_time_with_a_trailing_z_is_parsed(austria):
    """`datetime.fromisoformat` refused a trailing Z before Python 3.11, and every date in these
    lists carries one. A parser that returned None here would make every service look undated, and
    undated reads as UNKNOWN for every query."""
    for status in austria.services[0].statuses:
        assert status.since is not None, status
        assert status.since.tzinfo is not None


# -- the cache a verifier actually carries ----------------------------------------------------------
def test_a_cache_round_trips_to_the_same_verdicts(spain, izenpe_token, digicert_token):
    """The digest a verifier ships must answer exactly as the XML it came from.

    Both tokens are checked, because a cache that lost its certificates would answer
    NOT_ON_ANY_LIST_LOADED for everything and would agree with the XML on the negative control alone.
    """
    rebuilt = TrustedList.from_cache(spain.to_cache({"generated_utc": "2026-08-31T00:00:00Z"}))
    verdicts = set()
    for token in (izenpe_token, digicert_token):
        signer = signer_certificate(token)
        assert signer is not None
        before = spain.qualified_at("2026-08-31T12:00:00Z", cert_der=signer)
        after = rebuilt.qualified_at("2026-08-31T12:00:00Z", cert_der=signer)
        assert before["verdict"] == after["verdict"]
        assert before["qualified"] is after["qualified"]
        verdicts.add(after["verdict"])
    assert len(verdicts) == 2, "both tokens agreed, so this compared nothing discriminating"


def test_a_cache_keeps_the_history_and_not_only_todays_status(austria):
    rebuilt = TrustedList.from_cache(austria.to_cache())
    ski = sorted(rebuilt.services[0].identifiers["skis"])[0]
    assert rebuilt.qualified_at("2017-01-01T00:00:00Z", ski=ski)["qualified"] is False
    assert rebuilt.qualified_at("2019-01-01T00:00:00Z", ski=ski)["qualified"] is True


def test_a_cache_of_an_unknown_version_is_refused_rather_than_guessed(spain):
    data = spain.to_cache()
    data["version"] = 99
    with pytest.raises(TrustedListError):
        TrustedList.from_cache(data)


def test_an_old_cache_says_how_old_it_is(spain, izenpe_token):
    """Staleness is reported, not enforced. A withdrawal since the cache was built is the direction
    that makes a verdict too generous, and the reader is the one who decides whether that matters."""
    old = TrustedList.from_cache(spain.to_cache({"generated_utc": "2020-01-01T00:00:00Z"}))
    got = qualified_status(izenpe_token, old, when="2026-08-31T12:00:00Z")
    assert any("days ago" in p for p in got["problems"]), got["problems"]
    fresh = TrustedList.from_cache(
        spain.to_cache({"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat()}))
    assert not any("days ago" in p for p in
                   qualified_status(izenpe_token, fresh, when="2026-08-31T12:00:00Z")["problems"])


# -- the command line, whose exit code is what a release gate reads ---------------------------------
def _cli(tmp_path, trusted, token_name, when=None):
    import json
    from inspeximus.cli import main
    cache = tmp_path / "lists.json"
    cache.write_text(json.dumps(trusted.to_cache({"generated_utc": "2026-08-31T00:00:00Z"})),
                     encoding="utf-8")
    argv = ["timestamp", "qualified", os.path.join(FIXTURES, token_name),
            "--trusted-list", str(cache)]
    if when:
        argv += ["--when", when]
    return main(argv)


def test_the_exit_code_separates_yes_from_no_from_undetermined(tmp_path, spain, capsys):
    """Three codes, not two. A gate that folded "I could not tell" into either answer would either
    block a good release or pass an unchecked one, and both look identical in CI."""
    assert _cli(tmp_path, spain, "izenpe_granted.tsr", "2026-08-31T12:00:00Z") == 0
    assert _cli(tmp_path, spain, "digicert_granted.tsr", "2026-08-31T12:00:00Z") == 1
    assert _cli(tmp_path, TrustedList([]), "izenpe_granted.tsr", "2026-08-31T12:00:00Z") == 2


def test_the_command_prints_the_scope_with_every_verdict(tmp_path, spain, capsys):
    _cli(tmp_path, spain, "izenpe_granted.tsr", "2026-08-31T12:00:00Z")
    printed = capsys.readouterr().out
    assert "NOT signature-verified" in printed
    assert "QUALIFIED_AT_THE_TIME" in printed
