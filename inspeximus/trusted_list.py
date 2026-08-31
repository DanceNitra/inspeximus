"""EU Trusted List membership for timestamps, checked AT THE TIME THE TOKEN WAS MADE.

WHY THIS EXISTS. `timestamp.py` can obtain a token from any RFC 3161 authority, and every one of them
returns the same PKIStatus 0. Nothing in a token says whether the authority behind it is QUALIFIED
under eIDAS. That word is not decoration: Article 41 gives a qualified timestamp a rebuttable
presumption of the date it shows, and an ordinary one no presumption at all. An operator who believes
their provider is qualified, and is wrong, is holding weaker evidence than they think.

Qualified status is not a property of a certificate. It is a property of a service ON A DATE. The
member state grants it, and can withdraw it. Among the 42 qualified timestamp services in the Belgian,
Austrian and Greek lists measured on 2026-08-31, five are withdrawn today and were granted earlier, so
the naive question gets the wrong answer in both directions: a 2022 token from a since-withdrawn
service IS qualified, and today's token from that same service is not.

    parse_trusted_list(xml)      services, their statuses, and every status they have HELD
    TrustedList.qualified_at()   the verdict for one signer at one moment
    ski_of(cert_der)             the Subject Key Identifier, which history entries carry when the
                                 certificate itself is absent

WHAT THE LIST DOES NOT CONTAIN, measured rather than assumed. It does not publish TSA endpoints: of
those same 42 services, zero carry a ServiceSupplyPoint. You cannot use the Trusted List to FIND a
qualified authority. You use it to check one you were already given, which is the direction an audit
needs anyway.

THREE LIMITS, REPEATED IN EVERY VERDICT AND NOT ONLY HERE:

1. This does not verify the XAdES signature on the list. It trusts the bytes it was handed. A list
   fetched over HTTPS from the Commission is not the same as a list whose signature you checked, and
   an auditor who cares about the difference must check it with a tool that does XAdES.
2. A verdict here is about MEMBERSHIP, not about the token. Whether the token is authentic, and
   whether that signer really signed it, is `verify_with_openssl`'s job. Both must pass, and neither
   substitutes for the other.
3. Before the earliest status the list records for a service, the answer is UNKNOWN, never "not
   qualified". A list that began recording in 2020 says nothing about 2015.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import re
import xml.etree.ElementTree as ET

__all__ = ["TrustedList", "Service", "ServiceStatus", "parse_trusted_list", "list_pointers",
           "ski_of", "cert_sha256", "TrustedListError",
           "QTST", "STATUS_GRANTED", "QUALIFIED_STATUSES"]

#: The service type that means "qualified timestamp". A list holds many other types, and matching a
#: signer against all of them would let a qualified *signature* service vouch for a timestamp.
QTST = "http://uri.etsi.org/TrstSvc/Svctype/TSA/QTST"

_SVC = "http://uri.etsi.org/TrstSvc/TrustedList/Svcstatus/"

STATUS_GRANTED = _SVC + "granted"

#: Only `granted` confers qualified status under eIDAS. Measured across 29 national lists on
#: 2026-08-31: 527 of 1190 status instances.
QUALIFIED_STATUSES = frozenset([STATUS_GRANTED])

#: Statuses that end qualified standing. `withdrawn` dominates (403 instances), and the `ceased` and
#: `revoked` forms are the Directive-era ways of saying the same thing.
NOT_QUALIFIED_STATUSES = frozenset(
    _SVC + s for s in ("withdrawn", "supervisionceased", "supervisionrevoked",
                       "accreditationceased", "accreditationrevoked",
                       "deprecatedbynationallaw", "deprecatedatnationallevel"))

#: Statuses this library refuses to convert into a yes or a no, and says so.
#:
#: `accredited` (123 instances) and `undersupervision` (97) are supervision statuses from Directive
#: 1999/93/EC, which eIDAS replaced from 2016-07-01. They are not a defect in the data and they are
#: not `granted` either. Whether a token signed under one carries the Article 41 presumption is a
#: question of law about a token's vintage, and answering it in a library would be a legal opinion
#: dressed as a boolean. `recognisedatnationallevel` is a national arrangement rather than the EU
#: presumption, and `supervisionincessation` describes a service winding down while still supervised.
#:
#: 220 of 1190 status instances fall here, so treating them as either yes or no would have been a
#: silent decision about a fifth of the corpus.
UNDETERMINED_STATUSES = frozenset(
    _SVC + s for s in ("accredited", "undersupervision", "setbynationallaw",
                       "supervisionincessation", "recognisedatnationallevel"))


def classify_status(status):
    """One of "qualified", "not_qualified", "undetermined", for any ETSI service status.

    An unrecognised status is "undetermined" rather than "not_qualified", because a status this
    library has not seen is something it does not understand, and reporting a misunderstanding as a
    denial is the direction that produces confident wrong answers.
    """
    if status in QUALIFIED_STATUSES:
        return "qualified"
    if status in NOT_QUALIFIED_STATUSES:
        return "not_qualified"
    return "undetermined"

#: How a status at the moment in question outranks another when a signer matches several services.
#: A yes beats a "we will not judge", which beats a gap in the record, which beats a no. The order
#: matters because a certificate reissued across two list entries can match both.
_RANK = {"qualified": 3, "undetermined": 2, "not_qualified": 0}

_SCOPE = ("This is a MEMBERSHIP check against trusted-list bytes that were NOT signature-verified "
          "here, and it says nothing about whether the token is authentic or was signed by this "
          "certificate. Verify the token separately with a real RFC 3161 verifier. Both checks must "
          "pass, and neither substitutes for the other.")


class TrustedListError(Exception):
    """The document handed in is not a trusted list we can read. Distinct from "the signer is not on
    it", because an unreadable list must never be reported as an absence."""


# -- time -------------------------------------------------------------------------------------------
_TS = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?"
                 r"(Z|[+-]\d{2}:?\d{2})?$")


def _parse_time(text):
    """An ISO-8601 instant as an aware UTC datetime, or None.

    Hand-rolled because `datetime.fromisoformat` did not accept a trailing `Z` until Python 3.11, and
    every StatusStartingTime in these lists ends in one. A parser that returned None for the normal
    case would make every service look undated, and undated reads as UNKNOWN, which is a
    plausible-looking answer that would be wrong for all of them.
    """
    if not text:
        return None
    m = _TS.match(text.strip())
    if not m:
        return None
    y, mo, d, h, mi, s, tz = m.groups()
    try:
        out = _dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s),
                           tzinfo=_dt.timezone.utc)
    except ValueError:
        return None
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        out -= sign * _dt.timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
    return out


def _as_utc(when):
    """Accept a datetime, an ISO-8601 string, or None (meaning now), and return an aware UTC one.

    A naive datetime is read as UTC rather than refused, because the alternative is a caller passing
    `datetime.utcnow()`, which is the obvious thing to reach for, and getting an exception where they
    expected an answer.
    """
    if when is None:
        return _dt.datetime.now(_dt.timezone.utc)
    if isinstance(when, str):
        got = _parse_time(when)
        if got is None:
            raise ValueError("could not read %r as a time" % (when,))
        return got
    if isinstance(when, _dt.datetime):
        return when if when.tzinfo else when.replace(tzinfo=_dt.timezone.utc)
    raise TypeError("expected a datetime, an ISO-8601 string, or None")


# -- certificate helpers ------------------------------------------------------------------------------
def cert_sha256(der):
    """The SHA-256 of a certificate's DER bytes, which is how a signer is matched exactly."""
    return hashlib.sha256(bytes(der)).hexdigest()


_SKI_OID = bytes([0x06, 0x03, 0x55, 0x1D, 0x0E])            # 2.5.29.14 subjectKeyIdentifier


def ski_of(der):
    """The Subject Key Identifier from a certificate's DER, as lowercase hex, or None.

    Needed because a ServiceHistoryInstance often carries only X509SubjectName and X509SKI with no
    certificate. Of the 42 qualified timestamp services measured, every CURRENT entry carries a
    certificate and some history entries do not, so without SKI matching the historical half of this
    module would answer UNKNOWN for exactly the cases it exists to decide.

    This scans for the extension rather than parsing the whole certificate, and checks the shape
    strictly: the OID, an optional `critical` BOOLEAN, then the `OCTET STRING` wrapping the
    `KeyIdentifier` `OCTET STRING`. A loose scan finds the same five bytes inside a signature and
    reports a Subject Key Identifier that is not one.
    """
    b = bytes(der)
    at = b.find(_SKI_OID)
    while at != -1:
        i = at + len(_SKI_OID)
        if b[i:i + 3] in (b"\x01\x01\xff", b"\x01\x01\x00"):
            i += 3
        if i + 1 < len(b) and b[i] == 0x04:                 # extnValue, an OCTET STRING
            ln = b[i + 1]
            if ln < 0x80:
                inner = b[i + 2:i + 2 + ln]
                if len(inner) >= 2 and inner[0] == 0x04 and inner[1] == len(inner) - 2:
                    return inner[2:].hex()
        at = b.find(_SKI_OID, at + 1)
    return None


def _norm_ski(text):
    return text.lower().replace(":", "").replace(" ", "") if text else None


def _norm_subject(text):
    """Compare subject names with whitespace removed and case folded.

    The same distinguished name appears in these lists with and without a space after each comma, so
    an exact string comparison would miss matches a reader would call identical.
    """
    return re.sub(r"\s+", "", (text or "")).lower()


# -- the parsed list ----------------------------------------------------------------------------------
class ServiceStatus:
    """One status a service has held, and the moment it started holding it."""

    __slots__ = ("status", "since", "certificates", "skis", "subjects", "name")

    def __init__(self, status, since, certificates, skis, subjects, name=None):
        self.status = status
        self.since = since
        self.certificates = certificates                    # {sha256 hex}
        self.skis = skis                                    # {hex}
        self.subjects = subjects                            # {normalised name}
        self.name = name

    @property
    def qualified(self):
        return self.status in QUALIFIED_STATUSES

    @property
    def kind(self):
        return classify_status(self.status)

    @property
    def short(self):
        return self.status.rsplit("/", 1)[-1] if self.status else None

    def __repr__(self):
        return "ServiceStatus(%s since %s)" % (self.short, self.since)


class Service:
    """A qualified timestamp service, with every status the list records for it."""

    __slots__ = ("territory", "provider", "name", "type", "statuses")

    def __init__(self, territory, provider, name, type_, statuses):
        self.territory = territory
        self.provider = provider
        self.name = name
        self.type = type_
        self.statuses = statuses

    @property
    def identifiers(self):
        """Every certificate digest, SKI and subject the service has ever been listed under."""
        certs, skis, subs = set(), set(), set()
        for s in self.statuses:
            certs |= s.certificates
            skis |= s.skis
            subs |= s.subjects
        return {"certificates": certs, "skis": skis, "subjects": subs}

    def status_at(self, when):
        """The status in force at `when`, or None when the list records nothing that early.

        None means UNKNOWN and callers must not read it as "not granted". The distinction is the
        point of the module: a list that began recording in 2020 is silent about 2015, and silence is
        not a denial.
        """
        when = _as_utc(when)
        best = None
        for s in self.statuses:
            if s.since is not None and s.since <= when:
                if best is None or s.since > best.since:
                    best = s
        return best

    def __repr__(self):
        return "Service(%s, %s, %s)" % (self.territory, self.provider, self.name)


class TrustedList:
    """The qualified timestamp services parsed out of one or more national trusted lists."""

    def __init__(self, services, sources=()):
        self.services = list(services)
        self.sources = list(sources)
        #: Where the lists came from, when they were fetched, and the SHA-256 of each. Empty unless
        #: this was built by `from_cache`, and reported in `qualified_at` so a verdict never arrives
        #: without a way to check the data behind it.
        self.provenance = {}

    def __len__(self):
        return len(self.services)

    def extend(self, other):
        """Merge another list in. Territories are independent, so services do not collide."""
        self.services.extend(other.services)
        self.sources.extend(other.sources)
        return self

    @property
    def territories(self):
        return sorted({s.territory for s in self.services if s.territory})

    # ── a cache, so a verifier does not need the network or 200 MB of XML ───────────────────────────
    def to_cache(self, provenance=None):
        """A JSON-serialisable digest of the identifiers and dates a check actually reads.

        The XML these came from is roughly 200 MB across 25 territories, which no verifier wants to
        carry and no test should download. `provenance` records where each list came from and the
        SHA-256 of the bytes parsed, so a reader can re-fetch and confirm the digest rather than
        taking it on trust. A cache without that would be an unsourced claim about European law.
        """
        return {"version": 1, "provenance": provenance or {},
                "services": [{"territory": s.territory, "provider": s.provider, "name": s.name,
                              "type": s.type,
                              "statuses": [{"status": st.status,
                                            "since": st.since.isoformat() if st.since else None,
                                            "name": st.name,
                                            "certificates": sorted(st.certificates),
                                            "skis": sorted(st.skis),
                                            "subjects": sorted(st.subjects)}
                                           for st in s.statuses]}
                             for s in self.services]}

    @classmethod
    def from_cache(cls, data):
        """Rebuild a TrustedList from `to_cache` output."""
        if not isinstance(data, dict) or "services" not in data:
            raise TrustedListError("this is not a trusted-list cache")
        if data.get("version") != 1:
            raise TrustedListError("unknown cache version %r; refusing to guess its shape"
                                   % (data.get("version"),))
        services = []
        for s in data["services"]:
            services.append(Service(
                territory=s.get("territory"), provider=s.get("provider"), name=s.get("name"),
                type_=s.get("type"),
                statuses=[ServiceStatus(status=st.get("status") or "",
                                        since=_parse_time(st.get("since")),
                                        certificates=set(st.get("certificates") or ()),
                                        skis=set(st.get("skis") or ()),
                                        subjects=set(st.get("subjects") or ()),
                                        name=st.get("name"))
                          for st in s.get("statuses", [])]))
        out = cls(services, sources=sorted((data.get("provenance") or {}).get("territories", {})))
        out.provenance = data.get("provenance") or {}
        return out

    #: Member states republish on their own schedules, and a list carries a NextUpdate of at most six
    #: months. A cache older than this can still be right and cannot be relied on, so the verdict says
    #: how old it is rather than refusing.
    STALE_AFTER_DAYS = 90

    def _staleness(self):
        when = _parse_time((self.provenance or {}).get("generated_utc"))
        if when is None:
            return None
        age = (_dt.datetime.now(_dt.timezone.utc) - when).days
        if age < self.STALE_AFTER_DAYS:
            return None
        return ("this trusted-list cache was built %d days ago (%s). A service granted or withdrawn "
                "since then is not reflected here, and a withdrawal is the direction that makes this "
                "verdict too generous. Refresh it with tools/fetch_trusted_lists.py."
                % (age, when.date().isoformat()))

    def lookup(self, cert_der=None, ski=None, subject=None):
        """Every service matching this signer, each carrying HOW it matched.

        The three ways are not equally strong, and the caller is told which one fired. A certificate
        digest is exact. An SKI is exact for a correctly issued certificate, but the issuer chooses
        it rather than deriving it. A subject name is weakest, because two certificates can share
        one, so it is reported and never accepted alone as proof.
        """
        want_cert = cert_sha256(cert_der) if cert_der is not None else None
        want_ski = _norm_ski(ski) or (_norm_ski(ski_of(cert_der)) if cert_der is not None else None)
        want_sub = _norm_subject(subject) if subject else None
        hits = []
        for svc in self.services:
            for st in svc.statuses:
                how = None
                if want_cert and want_cert in st.certificates:
                    how = "certificate"
                elif want_ski and want_ski in st.skis:
                    how = "ski"
                elif want_sub and want_sub in st.subjects:
                    how = "subject"
                if how:
                    hits.append({"service": svc, "status": st, "matched_by": how})
        return hits

    def qualified_at(self, when, cert_der=None, ski=None, subject=None,
                     accept_subject_match=False):
        """Was this signer a QUALIFIED timestamp service at `when`?

        Returns a verdict whose `qualified` is True, False, or None, where None means the check could
        not reach an answer. A caller that folds None into False turns "the list does not go back
        that far" into "this timestamp is worthless", and those are different findings.
        """
        when = _as_utc(when)
        hits = self.lookup(cert_der=cert_der, ski=ski, subject=subject)
        if not accept_subject_match:
            hits = [h for h in hits if h["matched_by"] != "subject"]
        out = {"qualified": None, "verdict": "NOT_ON_ANY_LIST_LOADED", "when": when.isoformat(),
               "matched": [], "territories_loaded": self.territories,
               "services_loaded": len(self.services), "problems": [], "scope": _SCOPE,
               "list_generated_utc": (self.provenance or {}).get("generated_utc")}
        stale = self._staleness()
        if stale:
            out["problems"].append(stale)
        if not self.services:
            out["verdict"] = "NOTHING_LOADED"
            out["problems"].append("no trusted list was loaded, so this checked nothing")
            return out
        if not hits:
            out["qualified"] = False
            out["problems"].append(
                "this signer is on none of the %d qualified timestamp services loaded (%s). That is "
                "an absence within what was loaded, not within the EU: load the remaining member "
                "states before reporting it as one."
                % (len(self.services), ", ".join(self.territories) or "no territory named"))
            return out
        best = None
        for h in hits:
            svc, st = h["service"], h["status"]
            in_force = svc.status_at(when)
            out["matched"].append({
                "territory": svc.territory, "provider": svc.provider, "service": svc.name,
                "matched_by": h["matched_by"], "listed_status": st.short,
                "status_at_the_time": in_force.short if in_force else None,
                "in_force_since": (in_force.since.isoformat()
                                   if in_force is not None and in_force.since else None)})
            rank = 1 if in_force is None else _RANK[in_force.kind]
            if best is None or rank > best[0]:
                best = (rank, in_force)
        rank, in_force = best
        if rank == 3:
            out["qualified"] = True
            out["verdict"] = "QUALIFIED_AT_THE_TIME"
        elif rank == 2:
            out["verdict"] = "LISTED_UNDER_A_STATUS_THIS_LIBRARY_WILL_NOT_JUDGE"
            out["problems"].append(
                "at %s the service held the status %r, which is not `granted` and is not one of the "
                "statuses that end qualified standing. Under Directive 1999/93/EC, which eIDAS "
                "replaced from 2016-07-01, supervision statuses such as `accredited` and "
                "`undersupervision` were how a member state vouched for a service. Whether a token "
                "of that vintage carries the Article 41 presumption is a legal question, so this "
                "answers neither yes nor no."
                % (when.isoformat(), in_force.short))
        elif rank == 1:
            out["verdict"] = "UNKNOWN_BEFORE_THE_LISTS_EARLIEST_RECORD"
            out["problems"].append(
                "the service is listed, but the list records no status as early as %s, so its "
                "standing at that moment is unknown rather than absent" % when.isoformat())
        else:
            out["qualified"] = False
            out["verdict"] = "LISTED_BUT_NOT_QUALIFIED_AT_THE_TIME"
            out["problems"].append(
                "the service is on the list, and at %s its status was %r. Qualified status is held "
                "on a date, so a token from another date can still be qualified."
                % (when.isoformat(), in_force.short))
        return out


# -- parsing ------------------------------------------------------------------------------------------
def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _find(el, name):
    for child in el:
        if _local(child.tag) == name:
            return child
    return None


def _text(el, name):
    got = _find(el, name)
    return (got.text or "").strip() if got is not None and got.text else None


def _guard(xml_text):
    if re.search(r"<!(DOCTYPE|ENTITY)", xml_text[:20000], re.I):
        # ElementTree expands internal entities, so a declared DOCTYPE is the entity-expansion attack
        # surface. A real trusted list carries no DOCTYPE, so refusing one costs nothing and closes
        # it.
        raise TrustedListError("this document declares a DOCTYPE, which a trusted list does not; "
                               "refusing to parse it")


def _multilingual(holder):
    """The English text of a multilingual name element, falling back to the first non-empty one."""
    if holder is None:
        return None
    best = None
    for nm in holder:
        text = (nm.text or "").strip()
        if not text:
            continue
        if nm.get("{http://www.w3.org/XML/1998/namespace}lang", "") == "en":
            return text
        best = best or text
    return best


def _digital_identity(el):
    certs, skis, subs = set(), set(), set()
    sdi = _find(el, "ServiceDigitalIdentity")
    if sdi is None:
        return certs, skis, subs
    for did in sdi:
        for leaf in did:
            name, value = _local(leaf.tag), (leaf.text or "").strip()
            if not value:
                continue
            if name == "X509Certificate":
                try:
                    certs.add(cert_sha256(base64.b64decode(value)))
                except Exception:                           # noqa: BLE001
                    pass
            elif name == "X509SKI":
                try:
                    skis.add(base64.b64decode(value).hex())
                except Exception:                           # noqa: BLE001
                    pass
            elif name == "X509SubjectName":
                subs.add(_norm_subject(value))
    return certs, skis, subs


def _status_of(el):
    certs, skis, subs = _digital_identity(el)
    return ServiceStatus(status=_text(el, "ServiceStatus") or "",
                         since=_parse_time(_text(el, "StatusStartingTime")),
                         certificates=certs, skis=skis, subjects=subs,
                         name=_multilingual(_find(el, "ServiceName")))


def parse_trusted_list(xml, territory=None, service_type=QTST):
    """Parse one national trusted list into its qualified timestamp services.

    `service_type=None` keeps every service type. The default keeps only QTST, because a provider
    qualified for signatures is not thereby qualified for timestamps, and a membership check that
    accepted any type would answer True for the wrong reason.
    """
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8", "replace")
    _guard(xml)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise TrustedListError("could not parse this as XML (%s)" % e) from None

    scheme_territory = territory
    if scheme_territory is None:
        for el in root.iter():
            if _local(el.tag) == "SchemeTerritory" and el.text:
                scheme_territory = el.text.strip()
                break

    services = []
    for tsp in root.iter():
        if _local(tsp.tag) != "TrustServiceProvider":
            continue
        info = _find(tsp, "TSPInformation")
        provider = _multilingual(_find(info, "TSPName")) if info is not None else None
        holder = _find(tsp, "TSPServices")
        # `holder or ()` reads an EMPTY element as absent today and as present from a future
        # Python, because an element's truth value is changing. Both happen to iterate to
        # nothing here, but the check has the shape that hides a missing target, so it is
        # written against None explicitly.
        for svc in (holder if holder is not None else ()):
            si = _find(svc, "ServiceInformation")
            if si is None:
                continue
            history_el = _find(svc, "ServiceHistory")
            history = [h for h in (history_el if history_el is not None else ())
                       if _local(h.tag) == "ServiceHistoryInstance"]
            type_ = _text(si, "ServiceTypeIdentifier")
            if service_type is not None and type_ != service_type:
                # A service can change type, and its history is where that shows. Keep it when it was
                # EVER the type asked for, so a token predating the change still resolves.
                if not any(_text(h, "ServiceTypeIdentifier") == service_type for h in history):
                    continue
            services.append(Service(territory=scheme_territory, provider=provider,
                                    name=_multilingual(_find(si, "ServiceName")), type_=type_,
                                    statuses=[_status_of(si)] + [_status_of(h) for h in history]))
    return TrustedList(services, sources=[scheme_territory] if scheme_territory else [])


#: The MIME type of a machine-readable trusted list. Each country is also pointed at as a PDF for
#: human readers, under the same SchemeTerritory.
TSL_MIME = "application/vnd.etsi.tsl+xml"


LOTL_URL = "https://ec.europa.eu/tools/lotl/eu-lotl.xml"
_USER_AGENT = "inspeximus-trusted-list/1.0 (+https://pypi.org/project/inspeximus/)"


def fetch(lotl_url=LOTL_URL, timeout=45, workers=12, progress=None):
    """Fetch every member state's trusted list and return (TrustedList, provenance).

    Lives in the library rather than in `tools/` so an installed package can refresh its own cache.
    Networked, and the only function here that is: everything else works on bytes you already hold.

    Unreachable territories are RECORDED, not dropped. On 2026-08-31 Hungary's list served a
    certificate its own chain did not validate. A run that quietly covered 24 of 25 countries would
    report a clean absence for the twenty-fifth, which is a wrong answer wearing the shape of a
    right one.
    """
    import concurrent.futures
    import time
    import urllib.request

    say = progress or (lambda *_: None)

    def get(url):
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def now():
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    say("fetching the list of trusted lists")
    lotl = get(lotl_url)
    pointers = list_pointers(lotl)
    pointers.pop("EU", None)                        # the LOTL's pointer to itself holds no services
    say("%d member-state lists to fetch" % len(pointers))

    provenance = {"generated_utc": now(), "lotl_url": lotl_url,
                  "lotl_sha256": hashlib.sha256(lotl).hexdigest(),
                  "territories": {}, "unreachable": {}}

    def one(item):
        territory, url = item
        try:
            return territory, url, get(url), None
        except Exception as e:                                          # noqa: BLE001
            return territory, url, None, "%s: %s" % (type(e).__name__, str(e)[:120])

    merged, done = TrustedList([]), 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for territory, url, data, problem in pool.map(one, sorted(pointers.items())):
            done += 1
            if problem is not None:
                provenance["unreachable"][territory] = problem
                say("[%2d/%2d] %s UNREACHABLE  %s" % (done, len(pointers), territory, problem))
                continue
            try:
                parsed = parse_trusted_list(data.decode("utf-8", "replace"), territory=territory)
            except TrustedListError as e:
                provenance["unreachable"][territory] = "unparsed: %s" % str(e)[:120]
                say("[%2d/%2d] %s UNPARSED     %s" % (done, len(pointers), territory, e))
                continue
            provenance["territories"][territory] = {
                "url": url, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                "fetched_utc": now(), "services": len(parsed)}
            merged.extend(parsed)
            say("[%2d/%2d] %s %4d qualified timestamp services"
                % (done, len(pointers), territory, len(parsed)))
    return merged, provenance


def list_pointers(xml, mime=TSL_MIME):
    """The per-country trusted-list locations from the EU List of Trusted Lists, as {territory: url}.

    SELECTS ON MIME TYPE, and the reason is a defect this had. Every country is pointed at twice, once
    as XML and once as a PDF for human readers, and taking the first pointer per territory returned
    the PDF for 9 of 31 countries, France and Spain among them. Nothing raised: the fetch succeeded,
    the file was 400 KB, and it parsed as zero qualified services. A caller would have read that as
    "France lists no qualified timestamp authority", which is a sentence made of real-looking data
    and is false.

    `mime=None` returns every pointer, which is how you see the PDFs rather than infer them.

    The LOTL points at itself too, and that self-pointer is kept rather than filtered, because
    dropping it silently would hide a malformed list that pointed only at itself.
    """
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8", "replace")
    _guard(xml)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise TrustedListError("could not parse this as XML (%s)" % e) from None
    out = {}
    for el in root.iter():
        if _local(el.tag) != "OtherTSLPointer":
            continue
        url = territory = declared = None
        for sub in el.iter():
            name, text = _local(sub.tag), (sub.text or "").strip()
            if not text:
                continue
            if name == "TSLLocation":
                url = text
            elif name == "SchemeTerritory":
                territory = text
            elif name == "MimeType":
                declared = text
        if not url:
            continue
        if mime is not None and declared != mime:
            continue
        out.setdefault(territory or url, url)
    return out
