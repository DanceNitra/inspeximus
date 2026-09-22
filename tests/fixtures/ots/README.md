# A real anchor, kept as bytes

These four files are one published head of our hosted transparency log, its OpenTimestamps receipt,
one calendar's upgrade response, and the header of the Bitcoin block that covers it. They are here
so the anchor tests run offline against a REAL proof rather than one the tests made up, and so a
change in our own stamping is visible as a change in these bytes.

| file | what it is |
|---|---|
| `head.json` | the head that was stamped, LF endings, exactly the bytes the receipt is about |
| `head.json.ots` | the receipt as stamped on 2026-09-22, four calendar promises, no block yet |
| `upgrade.alice.bin` | what `alice.btc.calendar.opentimestamps.org` answered when asked later |
| `block968177.header.hex` | the 80-byte header of block 968177, hash `00000000000000000000da19d18bc39596d5c8bf846954485ec7cf8c1cdaab74` |

The header is the part you are meant to distrust. Fetch it from your own node, or from any explorer,
and compare: an offline verifier is only offline because you chose where the block came from.
