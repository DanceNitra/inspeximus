"""3.23.1: every capture leaves one record line, so a day's saving is a sum anyone can recompute.

`print_capture` appends one JSON line per call, in every mode, to <key home>/inspeximus/saver-stats/<UTC date>.jsonl:
bytes in, the bytes the agent received, the seconds spent, the mode and ranking, the intent's length, and whether
the command read an earlier capture. No command, output, intent or path. `inspeximus-saver stats` sums the lines into
a net saving (follow-up reads charged as a cost) and a gross one, and says when it cannot see a gap."""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_3230_the_saver_captures_bash_output_in_the_hook import (  # noqa: E402,F401
    _big, _config, embed, home)

from inspeximus import context_saver as cs  # noqa: E402

FIELDS = {"ts", "v", "mode", "ranking", "bytes_in", "platform_shown", "bytes_out", "elapsed_s", "intent_chars", "why",
          "follow_up", "top_unseen"}


def _records():
    d = cs.stats_dir()
    out = []
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        with open(os.path.join(d, name), encoding="utf-8") as f:
            out += [json.loads(ln) for ln in f]
    return out


def _capture(text):
    p = cs.new_capture_path()
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return p


def test_every_mode_writes_one_record_with_what_the_agent_received(home):
    _config(home, {"saver": {"without_reranker": "hybrid"}})
    shown = {}
    for mode, text, kw in (("passthrough", "short\n", {}),
                           ("digest", _big(), {"embedders": (embed, None, "t")}),
                           ("pointer", "y" * (cs.CAPTURE_DIGEST_MAX_BYTES + 10), {}),
                           ("raw", _big(), {"embedders": (None, None, None)})):
        out = io.BytesIO()
        assert cs.print_capture(_capture(text), "section 3", out=out, err=io.StringIO(), **kw) == mode
        shown[mode] = (len(text.encode("utf-8")), len(out.getvalue()))
    rec = _records()
    assert [r["mode"] for r in rec] == ["passthrough", "digest", "pointer", "raw"]
    for r in rec:
        assert set(r) == FIELDS, set(r) ^ FIELDS
        assert (r["bytes_in"], r["bytes_out"]) == shown[r["mode"]], r
        assert r["intent_chars"] == len("section 3") and r["follow_up"] is False and r["elapsed_s"] >= 0
    assert rec[1]["ranking"] == "hybrid" and rec[0]["ranking"] is None
    assert rec[3]["why"] and rec[1]["why"] is None
    assert rec[1]["bytes_out"] < rec[1]["bytes_in"] / 2, "the digest record does not show a saving: no proof"


def test_a_record_that_cannot_be_written_leaves_the_output_byte_identical(home):
    _config(home, {"saver": {"without_reranker": "hybrid"}})
    text = _big()
    first = io.BytesIO()
    cs.print_capture(_capture(text), "section 3", out=first, embedders=(embed, None, "t"))
    shutil_dir = cs.stats_dir()
    for n in os.listdir(shutil_dir):
        os.remove(os.path.join(shutil_dir, n))
    os.rmdir(shutil_dir)
    open(shutil_dir, "w").close()                    # a file where the directory goes: every write fails
    p = _capture(text)
    second = io.BytesIO()
    assert cs.print_capture(p, "section 3", out=second, embedders=(embed, None, "t")) == "digest"
    assert second.getvalue() == first.getvalue().replace(first.getvalue().split(b"full output: ")[-1],
                                                         (p + "\n").encode())
    assert cs.record_capture({"x": 1}) is False


def test_the_sweep_removes_only_day_files_older_than_the_keep(home):
    d = cs.stats_dir()
    os.makedirs(d)
    now = time.time()
    old = time.strftime("%Y-%m-%d", time.gmtime(now - (cs.STATS_KEEP_DAYS + 2) * 86400))
    kept = time.strftime("%Y-%m-%d", time.gmtime(now - (cs.STATS_KEEP_DAYS - 2) * 86400))
    for name in (old + ".jsonl", kept + ".jsonl", "1999-01-01.txt", "notes.jsonl", "1999-01-01.jsonl.bak"):
        open(os.path.join(d, name), "w").close()
    os.makedirs(os.path.join(d, "1999-01-02.jsonl"))
    gone = cs.sweep_stats(now)
    assert [os.path.basename(p) for p in gone] == [old + ".jsonl"]
    assert sorted(os.listdir(d)) == sorted([kept + ".jsonl", "1999-01-01.txt", "notes.jsonl", "1999-01-01.jsonl.bak",
                                            "1999-01-02.jsonl"])


def _write_day(day, rows):
    os.makedirs(cs.stats_dir(), exist_ok=True)
    with open(os.path.join(cs.stats_dir(), day + ".jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_stats_sum_net_and_gross_and_say_when_a_gap_cannot_be_seen(home):
    now = time.time()
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    old = time.strftime("%Y-%m-%d", time.gmtime(now - 10 * 86400))
    rows = [{"mode": "digest", "ranking": "hybrid", "bytes_in": 100000, "platform_shown": cs.PLATFORM_PREVIEW_BYTES,
             "bytes_out": 3000, "elapsed_s": 4.0, "top_unseen": True},
            {"mode": "digest", "ranking": "hybrid", "bytes_in": 20000, "platform_shown": 20000, "bytes_out": 2500,
             "elapsed_s": 1.0, "top_unseen": False},
            {"mode": "passthrough", "ranking": None, "bytes_in": 800, "platform_shown": 800, "bytes_out": 800,
             "elapsed_s": 0.1},
            {"mode": "passthrough", "ranking": None, "bytes_in": 2000, "platform_shown": 2000, "bytes_out": 2000,
             "elapsed_s": 0.1, "follow_up": True}]
    _write_day(today, rows)
    _write_day(old, rows[:1])
    for _ in range(5):                               # five captures today, four records: a gap of one
        _capture("x")
    s = cs.stats([old, today], now=now)
    # Baseline: what Claude Code would have shown, not the whole output. The 100,000-byte output would have been a
    # preview block, so its digest of 3,000 bytes COST context, and the old baseline (bytes in) hid that.
    p = cs.PLATFORM_PREVIEW_BYTES
    assert (s["gross_in"], s["net_in"], s["bytes_out"]) == (222800, 2 * p + 20800, 11300)
    assert (s["gross_saved"], s["net_saved"], s["follow_ups"], s["captures"]) == (211500, 2 * p + 20800 - 11300, 1, 5)
    assert s["bands"]["whole"] == {"captures": 2, "shown": 20800, "saved": 17500}
    assert s["bands"]["preview"] == {"captures": 2, "shown": 2 * p, "saved": 2 * (p - 3000)}
    assert (s["top_unseen"], s["digests"]) == (2, 3)
    assert s["gap"] == {old: None, today: 1}
    text = cs.stats_text(s)
    first = text.splitlines()[0]
    assert first.startswith("%s to %s: the saver kept %d bytes" % (old, today, 2 * p + 20800 - 11300))
    assert "%d bytes saved (negative" % (2 * (p - 3000)) in text
    assert "gap unknown: captures for this day are already swept" in text and "GAP: 1 captures" in text


def test_a_zero_byte_capture_is_no_gap_and_a_capture_with_output_still_is(home):
    # Measured 2026-10-26: 328 of 333 files in saver-out were zero bytes, every one with no record, and `stats`
    # reported "GAP: 149 captures have no record" while nothing was missing. An empty file never held an output.
    now = time.time()
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    for _ in range(7):
        cs.new_capture_path()                        # created empty, never written
    assert cs.stats([today], now=now)["gap"] == {today: 0}
    _capture("output with no record")                # the control: a real capture with no record is still a gap
    assert cs.stats([today], now=now)["gap"] == {today: 1}
    _write_day(today, [{"mode": "raw", "bytes_in": 0, "why": "NotAPlatformFile"}])
    assert cs.stats([today], now=now)["gap"] == {today: 1}, "a zero-byte record hid a real gap"


def test_the_stats_command_leads_with_the_day_and_its_saving(home, capsys):
    today = time.strftime("%Y-%m-%d", time.gmtime())
    _write_day(today, [{"mode": "digest", "ranking": "hybrid", "bytes_in": 1000, "platform_shown": 1000,
                        "bytes_out": 100, "elapsed_s": 1}])
    assert cs.main(["stats"]) == 0
    assert capsys.readouterr().out.startswith("%s: the saver kept 900 bytes out of the agent's context, 90.0%%" % today)
    assert cs.main(["stats", "--day", "26-10-2026"]) == 2


def test_the_baseline_is_what_claude_code_would_have_shown(home):
    # Up to 30,000 characters Claude Code shows an output whole; past them, a preview block of about 2 KB.
    two_byte = "é" * 29000                     # 58,000 bytes, 29,000 characters: still shown whole
    assert cs.platform_shown(two_byte.encode("utf-8")) == 58000
    assert cs.platform_shown(b"a" * 30000) == 30000
    assert cs.platform_shown(b"a" * 30001) == cs.PLATFORM_PREVIEW_BYTES
    _config(home, {"saver": {"without_reranker": "hybrid"}})
    late = _big("the answer is here: zebra-quartz")  # the answer sits in section 3, past the preview
    assert len(late) > cs.PLATFORM_CUT_CHARS
    cs.print_capture(_capture(late), "zebra-quartz", out=io.BytesIO(), embedders=(embed, None, "t"))
    small = _big("the answer is here: zebra-quartz")[:20000]
    cs.print_capture(_capture(small), "zebra-quartz", out=io.BytesIO(), embedders=(embed, None, "t"))
    big_rec, small_rec = _records()
    assert big_rec["platform_shown"] == cs.PLATFORM_PREVIEW_BYTES and big_rec["top_unseen"] is True
    assert small_rec["platform_shown"] == small_rec["bytes_in"] and small_rec["top_unseen"] is False


def test_a_capture_the_saver_cannot_rank_is_printed_whole(home):
    # No embedder: the saver cannot rank, and a failure costs context, never output.
    _config(home, {"saver": {"without_reranker": "hybrid"}})
    text = _big()
    out = io.BytesIO()
    assert cs.print_capture(_capture(text), "section 3", out=out, err=io.StringIO(),
                            embedders=(None, None, None)) == "raw"
    assert out.getvalue() == text.encode("utf-8")


def test_print_capture_records_whether_it_was_a_follow_up(home):
    cs.print_capture(_capture("short\n"), "x", out=io.BytesIO(), follow_up=True)
    cs.print_capture(_capture("short\n"), "x", out=io.BytesIO())
    assert [r["follow_up"] for r in _records()] == [True, False]


def test_a_record_holds_no_command_output_intent_or_path(home):
    _config(home, {"saver": {"without_reranker": "hybrid"}})
    mark = "ISXMARK%d" % os.getpid()
    p = _capture(_big("%s-output" % mark))
    cs.print_capture(p, mark + "-intent", out=io.BytesIO(), embedders=(embed, None, "t"))
    (rec,) = _records()
    assert rec["intent_chars"] == len(mark + "-intent") and rec["mode"] == "digest", "no proof the record saw it"
    raw = "".join(open(os.path.join(cs.stats_dir(), n), encoding="utf-8").read() for n in os.listdir(cs.stats_dir()))
    for leak in (mark, os.path.basename(p), "saver-out"):
        assert leak not in raw, leak
