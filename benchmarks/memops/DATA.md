# The MemOps data this benchmark runs on

The data is not in this repository. It belongs to MemTensor, it is MIT licensed, and it is 193.6 MiB
in 806 files. Fetch it from the source and verify it against the manifest beside this file.

## Fetch

```bash
git clone --depth 1 https://github.com/MemTensor/MemOps /tmp/memops
cd /tmp/memops && git fetch --depth 1 origin 312af65e2c7b6d1b70f062ffa8b4cde32aaf6f35 && git checkout FETCH_HEAD
```

Then put the two directories where the harness looks for them:

| MemOps | here |
|---|---|
| `generated_result/2-evidence_conversation` | `benchmarks/memops/data` |
| `generated_result/4-inject_evidence_with_distractors` | `benchmarks/memops/data_lc` |

## Verify

`DATA_MANIFEST.sha256` holds the sha256 of each of the 806 files, taken over LF line endings, so the
hash matches whether a checkout wrote LF or CRLF:

```bash
python - <<'PY'
import hashlib, os
bad = 0
for line in open("benchmarks/memops/DATA_MANIFEST.sha256"):
    if line.startswith("#") or not line.strip():
        continue
    want, path = line.split("  ", 1)
    path = path.strip()
    if not os.path.exists(path):
        print("MISSING", path); bad += 1; continue
    got = hashlib.sha256(open(path, "rb").read().replace(b"\r\n", b"\n")).hexdigest()
    if got != want:
        print("DIFFERS", path); bad += 1
print("manifest check:", "OK" if not bad else "%d files wrong" % bad)
PY
```

## Why it is not committed here

It was, verbatim, until 2026-10-26. Three reasons to fetch it instead, in the order they matter:

1. **A copy proves nothing.** While the files sat here, nobody could tell our copy from an edited one.
   The manifest and the pinned commit make that checkable by anyone, which is the point of a benchmark
   whose result went against us.
2. It is someone else's data, and redistributing 193.6 MiB of it earned us nothing it does not already
   have at its own address.
3. It was 194 of this repository's 224 MiB, so every person who cloned us paid for it.

What stays here is ours and is what the claim rests on: the pre-registration, the harness, the judge
calibration, and the results in `results/`, 0.80 MiB in all. The numbers in `README.md` were measured
on the data at the commit above, and nothing in this change touches them.
