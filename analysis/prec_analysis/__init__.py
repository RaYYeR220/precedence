"""Analysis of precedence runs.

Reads the raw `data/` tree written by the harness (see docs/DATA.md), turns it
into one table of probes and one of probe pairs, and answers, per validator
and per client family: by what rule did the block order our probes, how
strictly, and which component (scheduler pass, parallel execution threads or
an external sequencer) decided it.
"""

__version__ = "0.1.0"
