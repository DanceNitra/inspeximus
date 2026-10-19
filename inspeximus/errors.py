"""The exceptions that mean "the input cannot be used", as opposed to a defect (3.21, AUDIT-A F-2).

The command line turns these, and OSError and json.JSONDecodeError, into one line and exit code 2
(`inspeximus <command>: error: <message>`). Any other exception keeps its traceback, because it is a
defect to report, not a mistake to correct. INSPEXIMUS_TRACEBACK=1 shows the traceback for these too.

Both subclass the exception the library raised before 3.21, so a caller that catches ValueError, or
KeyError for `UnknownName`, still catches them.
"""


class UsageError(ValueError):
    """An argument or a value the caller passed cannot be used: a bad format, a name or a number that
    does not exist, or a state the request contradicts (an objection already standing)."""


class UnknownName(UsageError, KeyError):
    """A name the caller passed is not there. A KeyError as well, as it was before 3.21."""

    def __str__(self):                                   # KeyError quotes its message; the message is a sentence
        return str(self.args[0]) if self.args else ""
