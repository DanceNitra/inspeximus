# -*- coding: utf-8 -*-
"""The fixture for the chain-binding measurement: conversational correction chains that MUST bind, and
unrelated pairs that MUST NOT.

Written BEFORE any change to the extractor, from how people actually type at an assistant, not from what
the shipped regexes happen to accept. That ordering is the whole point: a fixture narrowed until it agrees
with the code measures nothing. Every chain here is a real correction of ONE fact, and every negative pair
is two statements a store must keep apart -- if the keyer binds them, a later "correction" silently
destroys an unrelated record.

Surface forms deliberately covered, because the defect is about surface variation and nothing else:
plain copula, possessive, "the Y of X", first person ("I'm ... now"), discourse-marked corrections
("actually", "correction:", "update:", "fyi", "that changed --"), verb-carried changes ("moved to",
"switched to", "changed to"), subject dropped on the follow-up turn, and third-person restatement.
"""

# Each chain: (chain_id, shape, [turns...], final_value)
# `shape` labels the hardest surface feature in the chain, so a partial result says WHICH shapes defeat
# the keyer instead of only how many.
CHAINS = [
    ("title", "first-person bare predication + correction marker", [
        "my title is Staff Engineer",
        "actually I'm a Principal Engineer now",
        "correction: my title is Distinguished Engineer",
    ], "Distinguished Engineer"),

    ("employer", "verb-carried change, employer named after a clause", [
        "I work at Acme Corp",
        "I switched jobs, I work at Globex now",
        "update: my employer is Initech",
    ], "Initech"),

    ("city", "'that changed --' discourse marker", [
        "I live in Berlin",
        "that changed, I live in Lisbon now",
    ], "Lisbon"),

    ("atlas_deadline", "subject dropped on the follow-up turn", [
        "the deadline for Project Atlas is March 3",
        "the Project Atlas deadline moved to April 10",
        "Project Atlas's deadline is now May 1",
    ], "May 1"),

    ("language", "'actually I prefer' -- relation carried by the verb", [
        "my preferred programming language is Python",
        "actually I prefer Rust these days",
    ], "Rust"),

    ("manager", "3 turns, reason clause before the correction", [
        "my manager is Dana",
        "Dana left, so my manager is Priya now",
        "correction: my manager is Sam",
    ], "Sam"),

    ("team", "membership stated as 'I'm on the X team'", [
        "I'm on the Payments team",
        "I moved to the Platform team last week",
    ], "Platform"),

    ("email", "'changed to'", [
        "my email is dan@example.com",
        "my email changed to dan@newmail.com",
    ], "dan@newmail.com"),

    ("timezone", "'I'm now in X' -- adverb before the value", [
        "I'm in the CET timezone",
        "I'm now in the PST timezone",
    ], "PST"),

    ("dan_role", "third-person restatement, possessive alternating with copula", [
        "Dan's role is tech lead",
        "Dan is now an engineering manager",
        "correction: Dan's role is director",
    ], "director"),

    ("analytics_store", "first-person plural, 'we switched X to Y'", [
        "we use Postgres for the analytics store",
        "we switched the analytics store to ClickHouse",
        "the analytics store is DuckDB now",
    ], "DuckDB"),

    ("diet", "bare adjective predication, no relation noun anywhere", [
        "I'm vegetarian",
        "I'm vegan now",
    ], "vegan"),

    ("phone", "'fyi' marker", [
        "my phone number is 555-0101",
        "fyi my phone number is 555-0199",
    ], "555-0199"),

    ("release", "value restated inside a longer clause", [
        "the current release is v2.1",
        "we shipped v2.2 yesterday, so the current release is v2.2",
        "the current release is v3.0",
    ], "v3.0"),

    # The repository's own documented failure, quoted from README.md's "Honest scope" paragraph:
    # "my official title ... was Junior Data Analyst" and "so my current title is Data Analyst" yield
    # different keys that never meet. Not invented for this fixture -- it is the example the product
    # already admits to failing, so it is the fairest possible chain to be measured on.
    ("readme_title", "README's own example: official/current modifiers + a 'so' lead-in", [
        "my official title was Junior Data Analyst",
        "so my current title is Data Analyst",
    ], "Data Analyst"),
]

# Each negative pair: (pair_id, why, statement_a, statement_b)
# These are the control. A keyer that binds everything scores a perfect bind rate and is worthless; these
# are the inputs it cannot examine its way out of.
NEGATIVES = [
    ("diff-relation-self", "two different facts about the same person",
     "my title is Staff Engineer", "my manager is Dana"),

    ("nonreferring-expletive", "NON-REFERRING SUBJECT: 'it' identifies nothing and must never key",
     "my deadline is Friday", "It is important to ship on Friday"),

    ("nonreferring-there", "NON-REFERRING SUBJECT: existential 'there'",
     "my deadline is Friday", "There is a hard deadline on Friday"),

    ("nonreferring-that", "NON-REFERRING SUBJECT: anaphoric 'that'",
     "my phone number is 555-0101", "That is a memorable number"),

    ("diff-person", "same relation, different holder",
     "I live in Berlin", "my sister lives in Berlin"),

    ("near-miss-name", "one character apart, two people",
     "Dan's title is director", "Dana's title is director"),

    ("diff-project", "same relation, different project",
     "the deadline for Project Atlas is March 3", "the deadline for Project Bravo is March 3"),

    ("two-self-attributes", "two bare self-predications that are NOT the same attribute",
     "I'm vegetarian", "I'm exhausted"),

    ("near-miss-relation", "'email' vs 'email signature'",
     "my email is dan@example.com", "my email signature is 'Best, Dan'"),

    ("diff-slot-same-subject", "same subject 'we', two different systems",
     "we use Postgres for the analytics store", "we use Redis for the session cache"),

    ("self-vs-other-employer", "I vs my wife",
     "I work at Acme Corp", "my wife works at Acme Corp"),

    ("subject-becomes-object", "the team as MY team vs the team as an actor",
     "I'm on the Payments team", "the Payments team is hiring two designers"),

    ("value-as-subject", "the value of a fact restated as a topic sentence",
     "my preferred language is Python", "Python is a great language for beginners"),

    # --- added after the baseline run, aimed at mechanisms the fix introduces ------------------------
    # This pair FALSE-BINDS on unmodified main: both sentences yield the key "i'm", because the
    # contraction is not in the non-referring set and "now" is accepted as a copula. Two unrelated facts
    # about the user therefore retire each other. Kept as a control precisely because it is a hazard the
    # baseline already has -- it makes the "before" column worse, not better.
    ("contraction-pronoun-key", "'I'm now X' twice: a contracted pronoun is still a pronoun",
     "I'm now in the PST timezone", "I'm now the on-call engineer"),

    ("possessive-subject-vs-self", "a relation held by someone else must not key onto the user's own",
     "I'm in the CET timezone", "my colleague is in the PST timezone"),

    ("same-relation-noun-diff-holder", "the head noun matches; the holder does not",
     "I'm on the Payments team", "my manager is on the Platform team"),

    # A modifier that marks the CURRENT statement of a relation may be folded away; one that marks a
    # HISTORICAL fact may not. If 'former' is stripped like 'current' is, a correction destroys the record
    # of the previous job. This is the pair that decides whether the modifier list was drawn correctly.
    ("former-vs-current", "'former employer' is a different fact, not a stale phrasing",
     "my former employer is Acme Corp", "my employer is Globex"),

    # ADVERSARIAL, and expected to be hard: the head-noun frame cannot tell a value from an adjective.
    # 'the PST timezone' and 'the wrong timezone' are the same shape, so a complaint can look like a
    # correction. Carried openly rather than removed -- if it binds, that is a residual false-bind to
    # report, not a case to delete from the fixture.
    ("headnoun-adjective-value", "a complaint shaped exactly like a value",
     "I'm in the CET timezone", "I'm in the wrong timezone"),
]

# PRE-EXISTING and DELIBERATELY UNCHANGED, reported separately so the headline number above is about the
# pairs this work claims to handle and this hazard is still visible rather than absent.
#
# The bare-copula path reads "X is Y" as "the current description of X is Y" and keys on the subject alone
# -- that is the shipped contract, and it is what keys the README's own example "The API rate limit is 500
# rps". The cost is that two DIFFERENT attributes of the same entity share one key and retire each other.
# It behaves identically before and after this change, so it is not a regression, and narrowing it would
# remove an advertised capability to fix a hazard this unit did not introduce. The documented remedy is the
# one the README already gives: if you control the write and the entity has more than one attribute, pass
# `key=` explicitly.
KNOWN_UNFIXED = [
    ("bare-copula-two-attributes", "two attributes of one named entity share the subject-only key",
     "Dan is now an engineering manager", "Dan is 34"),
    ("bare-copula-possessed", "the same shape one level down, on a possessed subject",
     "my wife is Sarah", "my wife is tired"),
]

# Non-declarative conversational prose: questions, opinions, narration, instructions, assistant replies.
# This is the anti-greed control. The keyer must stay CONSERVATIVE here -- a key derived from prose like
# this is what produced the pronoun-key collisions the non-referring guard was written for. Two numbers
# come off it: how often a key is derived at all, and how many records retire each other when the whole
# corpus is ingested into one store. Every supersession here is a candidate silent data loss, because no
# two of these sentences are about the same fact.
#
# NOT the MemOps corpus. MemOps is not redistributed in this repository (benchmarks/memops/README.md),
# so the 103-supersession measurement recorded in core.py cannot be re-run here. This is a stand-in
# written to the same shape, and it is labelled as one.
PROSE = [
    "What is the fastest way to migrate this schema?",
    "It is important to ship the migration before the freeze.",
    "There are three ways to approach this, and none of them is cheap.",
    "These are just a few of the options we discussed last quarter.",
    "Do you think there is a simpler design here?",
    "I have been thinking about the retry logic all morning.",
    "Could you summarise what we agreed on Tuesday?",
    "That was a surprisingly good result for a first attempt.",
    "Honestly, the whole approach feels over-engineered to me.",
    "Let me know when the staging environment is back up.",
    "We should probably write that down somewhere permanent.",
    "The build failed again, which is the third time today.",
    "Nothing about this is urgent, so take your time.",
    "Everyone on the call agreed that the API needs versioning.",
    "Some of the tests are flaky under load.",
    "Which of these two libraries would you pick?",
    "Here is the stack trace from the failing worker.",
    "This is the part I never understood about the scheduler.",
    "Thanks, that clears it up.",
    "One thing I noticed is that the cache never expires.",
    "Anyone who has run this at scale will tell you it is painful.",
    "Something is wrong with the way we compute the checksum.",
    "Why is the latency spiking every hour on the hour?",
    "How do I roll back a migration that already ran?",
    "Both approaches are defensible and I do not have a strong view.",
    "Another option is to shard by tenant instead of by region.",
    "I am not sure that is what the spec actually says.",
    "It looks like the retry loop swallows the exception.",
    "Such a design would double our write amplification.",
    "Where did the original benchmark numbers come from?",
    "When you get a chance, please review the pull request.",
    "The docs say one thing and the code does another.",
    "Sorry, I misread your last message.",
    "Good catch, I had not considered the empty-input case.",
    "Running it twice produced two different answers.",
    "Nobody has touched that module in about two years.",
    "Others have hit the same problem on the mailing list.",
    "So what you are saying is that the index is never used.",
    "Well, that explains the memory growth.",
    "Actually, hold on, let me re-read the trace.",
    "Wait, was that before or after the config change?",
    "Right, so the plan is to land the parser first.",
    "Okay, I will take another look tomorrow morning.",
    "Anything else you want me to check while I am in there?",
    "Whose responsibility is the on-call rotation this month?",
    "Each of the shards holds about forty gigabytes.",
    "All of the alerts fired at once, which is suspicious.",
    "None of this is written down anywhere.",
    "Here we go again with the certificate expiry.",
    "Everything about the deployment story needs a rethink.",
    "I appreciate you digging into that.",
    "The team is going to want a design document first.",
    "You are right that the naming is confusing.",
    "They shipped a fix upstream last week.",
    "He mentioned something about a rate limiter.",
    "She is the one who wrote the original implementation.",
    "We are going to need a bigger machine.",
    "Somebody should probably file an issue for this.",
    "This kind of thing is exactly why we added the linter.",
    "Anyway, the point is that the retry budget is too small.",
]


def chain_ids():
    return [c[0] for c in CHAINS]


def negative_ids():
    return [n[0] for n in NEGATIVES]
