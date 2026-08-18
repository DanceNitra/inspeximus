"""Value-level suppression skips values under 4 characters, and said nothing about it.

`_retired_values()` carries `if len(v) < 4: continue`. The rule is defensible -- a two-character
value matches everywhere -- and the ERASURE path applies the same rule and TELLS the caller:
`erasure_residue` documents it and emits a `problems` entry saying "no values were searchable (all
empty or under 4 characters), so nothing was compared -- an empty search is not a clean result".

The SUPPRESSION path applied it silently. `recall(suppress_stale_values=True)` returned a
byte-identical context and no diagnostic, so a caller could not tell "nothing was stale" from
"suppression could not see these values at all".

MEASURED 2026-08-18 on a generated corpus: of six contested keys, the one whose values were currency
codes (EUR / GBP / JPY) was the only one where suppression did nothing, and `_retired_values()`
returned zero keys for it while returning them for the other five. Nothing in the API said why.

Same property, two subsystems, one of them mute. These tests pin the mute one.
"""
from inspeximus import Inspeximus


def _short_value_store():
    """A key whose values are all 3 characters -- invisible to value-level suppression."""
    m = Inspeximus(path=None)
    m.remember("the billing currency is EUR", key="billing-currency", object="EUR")
    m.remember("the billing currency is JPY", key="billing-currency", object="JPY")
    return m


def _long_value_store():
    """The control: same shape, values long enough to be seen."""
    m = Inspeximus(path=None)
    m.remember("the deploy region is us-east-1", key="deploy-region", object="us-east-1")
    m.remember("the deploy region is eu-west-2", key="deploy-region", object="eu-west-2")
    return m


def test_the_premise_holds_short_values_are_invisible_to_suppression():
    """CONTROL. If `_retired_values` ever starts seeing short values, every assertion below is about
    a case that no longer arises, and the file must fail rather than pass quietly."""
    short = _short_value_store()
    long_ = _long_value_store()
    assert short._retired_values() == [], "short values became visible -- these tests are now vacuous"
    assert long_._retired_values(), "the control store produced no retired values at all"


def test_the_report_names_the_keys_suppression_cannot_see():
    rep = _short_value_store().supersession_report()
    assert rep["values_too_short_to_suppress"]["count"] == 1
    assert "billing-currency" in rep["values_too_short_to_suppress"]["keys"]
    assert "under 4 characters" in rep["values_too_short_to_suppress"]["note"]


def test_a_key_whose_values_are_long_enough_is_not_named():
    """The other direction. A field that flags everything is not a diagnostic."""
    rep = _long_value_store().supersession_report()
    assert rep["values_too_short_to_suppress"]["count"] == 0
    assert rep["values_too_short_to_suppress"]["keys"] == []


def test_the_existing_report_is_unchanged():
    """The addition must not disturb what callers already read."""
    rep = _short_value_store().supersession_report()
    assert rep["superseded_total"] == 1
    assert isinstance(rep["by_policy"], dict) and rep["by_policy"]


def test_a_store_with_no_keys_at_all_reports_zero_not_an_error():
    m = Inspeximus(path=None)
    m.remember("an unkeyed observation about nothing in particular")
    rep = m.supersession_report()
    assert rep["values_too_short_to_suppress"]["count"] == 0


def test_a_tenant_view_does_not_see_another_tenants_short_value_keys():
    """The hazard this file's own neighbourhood records twice: `supersession_report` is view-bound and
    now calls a helper. If that helper were left unbound it would compute over the PARENT store and
    hand one tenant a list of another tenant's keys."""
    m = Inspeximus(path=None)
    acme = m.for_tenant("acme")
    globex = m.for_tenant("globex")
    acme.remember("the billing currency is EUR", key="acme-currency", object="EUR")
    acme.remember("the billing currency is JPY", key="acme-currency", object="JPY")
    globex.remember("the billing currency is GBP", key="globex-currency", object="GBP")
    globex.remember("the billing currency is USD", key="globex-currency", object="USD")

    a_keys = acme.supersession_report()["values_too_short_to_suppress"]["keys"]
    g_keys = globex.supersession_report()["values_too_short_to_suppress"]["keys"]

    assert "acme-currency" in a_keys
    assert "globex-currency" not in a_keys, "a tenant was shown another tenant's key"
    assert "globex-currency" in g_keys
    assert "acme-currency" not in g_keys, "a tenant was shown another tenant's key"
