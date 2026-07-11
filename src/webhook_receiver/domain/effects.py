"""What a handler decides to do, expressed as data rather than as a database call.

A handler is a pure function: event in, ``Effect`` out. It touches no session and
issues no SQL. That is not architectural purity for its own sake -- it is what
lets the interesting half of the system be tested without a database, and it
keeps the transactional rules (advisory lock held, effect and status committing
together) in exactly one place instead of being re-implemented, slightly wrong,
in every handler somebody adds later.

The two effect shapes are not arbitrary. They differ in the one property that
decides whether an event can be *superseded* (FR-10):

* ``Credit`` is **commutative**. Applying +500 then +300 lands on the same
  balance as +300 then +500, so a late credit is not stale -- it is simply late,
  and it must still be applied. Dropping it would lose money.
* ``SetBalance`` is **last-writer-wins**. Applying an older snapshot after a
  newer one silently rewinds the account, so a late snapshot *must* be discarded
  rather than applied.

This is the whole of FR-10 in two sentences, and it is why the demo domain has a
snapshot event at all: an additive-only domain cannot demonstrate out-of-order
handling, because in an additive-only domain order does not matter.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Credit:
    """Move the balance by ``amount_minor`` (negative for a debit).

    Commutative, so it is applied regardless of arrival order. Its protection
    against double-application is the unique ``ledger_entry.event_id``, not an
    ordering check.
    """

    account_ref: str
    amount_minor: int


@dataclass(frozen=True, slots=True)
class SetBalance:
    """Reconcile the balance to an absolute value the provider asserts.

    Last-writer-wins, so it carries an ordering obligation: applying it out of
    order would clobber newer state. The guard lives in the adapter, which is the
    only place that can compare it against what has already been applied.
    """

    account_ref: str
    balance_minor: int


type Effect = Credit | SetBalance
