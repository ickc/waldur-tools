from __future__ import annotations

import pytest

from waldur_tools.config import Settings

API_URL = "https://portal.example.test"


@pytest.fixture
def settings(tmp_path):
    return Settings(api_url=API_URL, token="secret-token", cache_dir=tmp_path)


@pytest.fixture
def allocations():
    """Two projects, and one of them on two services -- as the real portal does."""
    return [
        {
            "url": f"{API_URL}/api/openportal-allocations/aaa/",
            "uuid": "aaa",
            "service_name": "Isambard 3",
            "project_name": "Project A",
            "project_uuid": "pa",
            "customer_name": "UKRI",
            "groupname": "brics.abc1",
            "backend_id": "abc1.brics",
            "node_usage": "50.0",
            "node_limit": 100,
            "is_active": True,
            "state": "OK",
        },
        {
            "url": f"{API_URL}/api/openportal-allocations/aaa2/",
            "uuid": "aaa2",
            "service_name": "Isambard 3 Multi Architecture System",
            "project_name": "Project A",
            "project_uuid": "pa",
            "customer_name": "UKRI",
            "groupname": "brics.abc1",
            "backend_id": "abc1.brics",
            "node_usage": "0.0",
            "node_limit": 100,
            "is_active": True,
            "state": "OK",
        },
        {
            "url": f"{API_URL}/api/openportal-allocations/bbb/",
            "uuid": "bbb",
            "service_name": "Isambard 3",
            "project_name": "Project B",
            "project_uuid": "pb",
            "customer_name": "UKRI",
            "groupname": "brics.abc2",
            "backend_id": "abc2.brics",
            "node_usage": "0.0",
            "node_limit": 200,
            "is_active": True,
            "state": "OK",
        },
    ]


@pytest.fixture
def associations():
    """One row per service alongside out-of-scope and fully blanked rows."""
    return [
        {
            "uuid": "1",
            "username": "alice.abc1",
            "groupname": "brics.abc1",
            "useridentifier": "alice.abc1.brics",
            "allocation": f"{API_URL}/api/openportal-allocations/aaa/",
        },
        {
            # Same person, same project, second service -- as the portal does.
            "uuid": "1b",
            "username": "alice.abc1",
            "groupname": "brics.abc1",
            "useridentifier": "alice.abc1.brics",
            "allocation": f"{API_URL}/api/openportal-allocations/aaa2/",
        },
        {
            "uuid": "2",
            "username": "bob.abc2",
            "groupname": "brics.abc2",
            "useridentifier": "bob.abc2.brics",
            # Points at a second allocation this token cannot see, as most do.
            "allocation": f"{API_URL}/api/openportal-allocations/unseen/",
        },
        {
            "uuid": "3",
            "username": "carol.zzz",
            "groupname": "brics.zzz",
            "useridentifier": "carol.zzz.brics",
            "allocation": f"{API_URL}/api/openportal-allocations/zzz/",
        },
        {
            # The portal blanks every field it will not let you read.
            "uuid": "4",
            "username": None,
            "groupname": None,
            "useridentifier": None,
            "allocation": f"{API_URL}/api/openportal-allocations/hidden/",
        },
    ]


@pytest.fixture
def users():
    return [
        {"unix_username": "alice", "full_name": "Alice A", "email": "alice@example.test"},
        {"unix_username": "bob", "full_name": "Bob B", "email": "bob@example.test"},
    ]


@pytest.fixture
def projects():
    """Only ``created`` and ``customer_name`` matter: the viz counts projects
    that existed by each month against the ones that ran something."""
    return [
        {"name": "Project A", "customer_name": "UKRI", "created": "2025-01-05T00:00:00+00:00"},
        {"name": "Project B", "customer_name": "UKRI", "created": "2026-01-05T00:00:00+00:00"},
        {"name": "Elsewhere", "customer_name": "Other Uni", "created": "2024-01-05T00:00:00+00:00"},
    ]


@pytest.fixture
def accounting_summary():
    return [
        {
            # `project_uuid` is what `reports.allocations` joins on, because
            # project names are not unique in a real estate and only one of a
            # duplicated pair holds the credits.
            "project_uuid": "pa",
            "project_name": "Project A",
            "customer_name": "UKRI",
            "total_credits": "30000.00000",
            "total_spend": "10000.00",
            "current_month_spend": "2000.00",
            "start_date": "2026-01-01",
            "end_date": "2026-08-01",
        },
        {
            "project_uuid": "pb",
            "project_name": "Project B",
            "customer_name": "UKRI",
            "total_credits": "1000.00000",
            "total_spend": "0.00",
            "current_month_spend": "0.00",
            "start_date": "2026-06-01",
            # Open ended, as the internal projects are: measured to the
            # snapshot date instead of an award end.
            "end_date": None,
        },
    ]


@pytest.fixture
def customers():
    """`customer_credit` and `customer_unallocated_credit` are the only
    organisation-level quantities the portal carries, and the difference between
    them is credit handed down to projects."""
    return [
        {
            "uuid": "cust-ukri",
            "name": "UKRI",
            "projects_count": 2,
            "customer_credit": "50000.00000",
            "customer_unallocated_credit": "20000.00000",
        },
        {
            # The legacy internal customer carries nulls, not zeroes.
            "uuid": "cust-legacy",
            "name": "Example Internal Projects",
            "projects_count": 1,
            "customer_credit": None,
            "customer_unallocated_credit": None,
        },
    ]


@pytest.fixture
def sacct_output():
    """``sacct --parsable2`` lines in :data:`waldur_tools.slurm.FIELDS` order.

    Deliberately raw text rather than a ready-made frame: the reshaping is the
    part with edge cases in it, and every one below is a case seen in the real
    output -- a job cancelled before it started, one that inherited the
    partition's time limit, and the tiny single-node jobs that dominate the
    count on a machine like this.
    """
    return "\n".join(
        [
            # Two months, so the per-month figures have more than one column.
            "101|alice.abc1|brics.abc1|grace|COMPLETED|2026-03-01T09:00:00|"
            "2026-03-01T09:00:06|60|6|1|1|1",
            "102|alice.abc1|brics.abc1|grace|COMPLETED|2026-03-01T10:00:00|"
            "2026-03-01T11:00:00|1440|36000|4|288|4",
            "103|bob.abc2|brics.abc2|grace|FAILED|2026-03-02T08:00:00|"
            "2026-03-04T08:00:00|2880|7200|16|1152|16",
            # Never started: `wait_seconds` must be null, not zero.
            "104|bob.abc2|brics.abc2|grace|CANCELLED by 1234|2026-03-02T09:00:00|"
            "Unknown|1440|0|2|144|0",
            # No requested limit of its own, so no requested node hours either.
            "105|alice.abc1|brics.abc1|grace|COMPLETED|2026-04-01T09:00:00|"
            "2026-04-01T09:00:30|Partition_Limit|30|1|1|1",
            "106|alice.abc1|brics.abc1|grace|TIMEOUT|2026-04-05T09:00:00|"
            "2026-04-05T21:00:00|720|43200|8|576|8",
        ]
    )


@pytest.fixture
def job_records(sacct_output):
    from waldur_tools import slurm

    return slurm.parse(sacct_output)


@pytest.fixture
def user_usage_rows():
    """``allocation`` and ``user`` are carried because they are the row's key.

    One row per allocation, user and month is what the endpoint promises, and
    ``cache.ROW_KEYS`` holds it to that -- so the fixture has to be faithful
    about those two fields or the guard has nothing to check.
    """
    return [
        {
            "allocation": f"{API_URL}/api/openportal-allocations/aaa/",
            "user": f"{API_URL}/api/users/alice/",
            "username": "alice.abc1.brics",
            "full_name": "Alice",
            "node_usage": "1.5",
            "year": 2025,
            "month": 1,
        },
        {
            "allocation": f"{API_URL}/api/openportal-allocations/aaa/",
            "user": f"{API_URL}/api/users/alice/",
            "username": "alice.abc1.brics",
            "full_name": "Alice",
            "node_usage": "2.5",
            "year": 2026,
            "month": 2,
        },
        # A second project of ours, so monthly aggregation has more than one.
        {
            "allocation": f"{API_URL}/api/openportal-allocations/bbb/",
            "user": f"{API_URL}/api/users/bob/",
            "username": "bob.abc2.brics",
            "full_name": "Bob",
            "node_usage": "1.0",
            "year": 2026,
            "month": 2,
        },
        # Another organisation's user: visible on this endpoint, but not ours.
        {
            "allocation": f"{API_URL}/api/openportal-allocations/zzz/",
            "user": f"{API_URL}/api/users/carol/",
            "username": "carol.zzz.brics",
            "full_name": "Carol",
            "node_usage": "99.0",
            "year": 2026,
            "month": 2,
        },
    ]


#: The projects the invoice fixture bills, keyed by the ``project_uuid`` the
#: allocations carry. ``pz`` deliberately has no allocation and no usage rows:
#: it is a project that has ended, and the portal drops a terminated project's
#: usage retrospectively while leaving its invoices exactly as they were.
PROJECT_NAMES = {"pa": "Project A", "pb": "Project B", "pz": "Project Z"}


def invoice(year, month, lines, customer="UKRI", state="created"):
    """One month's invoice, carrying the traps the real ones do.

    ``lines`` maps ``project_uuid`` to that project's node hours, because a real
    invoice is itemised: one usage line per project, and ``incurred_costs`` the
    sum of them. ``reports.invoiced_projects`` reads the lines and
    ``reports.invoiced`` the header, and the two agreeing is the whole basis for
    reporting off the ledger.

    ``incurred_costs`` is the node hours -- the portal bills one credit per node
    hour, ``unit_price`` exactly ``1.0000000000`` on every usage line -- and
    ``price``/``total`` are that same usage net of the credit line the portal
    writes to zero a grant-funded invoice out. So the totals are 0.00 while the
    month really billed the lot, which is why ``reports.invoiced`` reads
    ``incurred_costs`` and nothing else, and why ``invoiced_projects`` keeps
    only the lines priced at one credit an hour.
    """
    hours = sum(lines.values())
    items = [
        {
            "name": "Isambard 3 / NODE",
            "billing_type": "usage",
            "measured_unit": "hours",
            "quantity": f"{value:.10f}",
            "unit_price": "1.0000000000",
            "total": f"{value:.2f}",
            "project_name": PROJECT_NAMES[uuid],
            "project_uuid": uuid,
        }
        for uuid, value in lines.items()
    ]
    return {
        "number": 100000 + year * 100 + month,
        "year": year,
        "month": month,
        "state": state,
        "price": "0.0000000000",
        "tax": "0.0000000000",
        "total": "0.0000000000",
        "incurred_costs": f"{hours:.10f}",
        "customer_details": {"name": customer, "email": "billing@example.test"},
        "items": [
            *items,
            {
                # The credit line, which is not node hours and must not be summed
                # as if it were. Its unit is blank and its unit price is a large
                # negative, which is what marks it off from a usage line.
                "name": "Credit",
                "billing_type": "usage",
                "measured_unit": "",
                "quantity": "1",
                "unit_price": f"-{hours:.2f}",
                "total": f"-{hours:.2f}",
            },
        ],
    }


@pytest.fixture
def invoices():
    """Invoices matching ``user_usage_rows``, plus a project that has ended.

    January 2025 bills the 1.5 node hours Project A's usage rows sum to.
    February 2026 bills the 2.5 and 1.0 that Projects A and B sum to, **and a
    further 4.0 to Project Z** -- which has no allocation and no usage rows at
    all, because the portal stops returning a terminated project's usage while
    its invoices stand. So the ledger reads 7.5 that month where the usage
    endpoint can only reach 3.5, and the missing 4.0 is not a bad pull. It is
    the case ``reports.reconcile`` calls ``project ended``.

    Carol's 99.0 is neither: her organisation is invoiced separately, and its
    invoice is here to be filtered out rather than netted off.
    """
    return [
        invoice(2025, 1, {"pa": 1.5}),
        invoice(2026, 2, {"pa": 2.5, "pb": 1.0, "pz": 4.0}, state="pending"),
        invoice(2026, 2, {"pa": 99.0}, customer="Other Uni"),
    ]


@pytest.fixture
def usage_reports():
    return [
        {
            "id": 1,
            "year": 2026,
            "month": 3,
            "project_identifier": "abc1.brics",
            "resource": "brics.i3.clusters.macs",
            "is_complete": True,
            "report": {
                "project": "abc1.brics",
                "reports": {
                    "2026-03-01": {
                        "num_jobs": 10,
                        "total_wait_seconds": 100,
                        "user_job_counts": {"alice": 10},
                    },
                    "2026-03-02": {
                        "num_jobs": 0,
                        "total_wait_seconds": 0,
                        "user_job_counts": {},
                    },
                },
            },
        }
    ]


def _quotas(project, alice_home, alice_scratch, bob_home, stamp, scratch_limit="5.00 TB"):
    """One reading: the project's own quota plus two people's, at one instant.

    ``scratch_limit`` is a parameter because a quota is not a constant: alice's
    scratch is raised part-way through January, which is the one thing that can
    make a month's peak, median and limit describe three different states of
    the world.
    """
    return {
        "generated_at": stamp,
        "project": "abc1.brics",
        "project_quotas": {"projects": {"limit": "20.00 TB", "usage": project}},
        "user_quotas": {
            "alice.abc1.brics": {
                "home": {"limit": "100.00 GB", "usage": alice_home},
                "scratch": {"limit": scratch_limit, "usage": alice_scratch},
            },
            "bob.abc1.brics": {"home": {"limit": "100.00 GB", "usage": bob_home}},
        },
    }


@pytest.fixture
def storage_reports():
    """Quota readings shaped like the collector's, with its awkward parts kept.

    Every figure in the fixture is invented. What is *not* invented is the
    shape, and each row below is here because the parsing has to survive it:

    - A finished month (January) carrying ``daily_reports`` **and** a top-level
      snapshot dated after the last of them, which is the only way the last day
      of a month is ever reported.
    - The month in progress (February) carrying the snapshot alone, with no
      ``daily_reports`` key at all.
    - The same project-day reported twice under two ``resource`` values, by
      collectors minutes apart and disagreeing slightly, because storage
      belongs to the filesystem rather than to the cluster.
    - A quota whose limit is not a size, so the fill percentage has a null to
      carry rather than a division by zero.
    - A reading dated outside the month its row claims, which must be dropped
      rather than filed under the wrong column.
    - A project this token does not administer, for ``scope`` to remove.
    - A quota whose *newest* reading is unreadable while older ones parsed, so
      the end-of-month statistic has to answer "unknown" rather than repeat the
      last figure it could read.
    - A quota **raised part-way through the month**, so the peak fill, the peak
      size and the limit belong to three different states of the world and may
      not be written as one reading.
    """
    # The last January reading of bob's home came back with neither figure in a
    # shape the parser recognises. The 29th and the 30th are readable, so this
    # is the only thing separating "the end of the month" from "the last thing
    # we could read", and the two must not be confused.
    january_shared = _quotas(
        "3.10 TB",
        "31.00 GB",
        "710.00 GB",
        "96.00 GB",
        "2025-01-31T10:15:00.000000000Z",
    )
    january_shared["user_quotas"]["bob.abc1.brics"]["home"] = {
        "limit": "unknown",
        "usage": "unknown",
    }
    return [
        {
            "id": 1,
            "year": 2025,
            "month": 1,
            "project_identifier": "abc1.brics",
            "resource": "brics.i3.clusters.macs",
            "report": {
                **_quotas(
                    "3.00 TB",
                    "30.00 GB",
                    "700.00 GB",
                    "95.00 GB",
                    "2025-01-31T10:00:00.000000000Z",
                ),
                "daily_reports": {
                    "2025-01-29": _quotas(
                        "1.00 TB",
                        "10.00 GB",
                        "500.00 GB",
                        "80.00 GB",
                        "2025-01-29T10:00:00.000000000Z",
                        scratch_limit="4.00 TB",
                    ),
                    "2025-01-30": _quotas(
                        "2.00 TB",
                        "20.00 GB",
                        "600.00 GB",
                        "90.00 GB",
                        "2025-01-30T10:00:00.000000000Z",
                    ),
                },
            },
        },
        {
            "id": 2,
            "year": 2025,
            "month": 1,
            "project_identifier": "abc1.brics",
            "resource": "brics.i3.clusters.shared",
            "report": {
                **january_shared,
                "daily_reports": {
                    "2025-01-29": _quotas(
                        "1.10 TB",
                        "11.00 GB",
                        "510.00 GB",
                        "81.00 GB",
                        "2025-01-29T10:15:00.000000000Z",
                        scratch_limit="4.00 TB",
                    ),
                    "2025-01-30": _quotas(
                        "2.10 TB",
                        "21.00 GB",
                        "610.00 GB",
                        "91.00 GB",
                        "2025-01-30T10:15:00.000000000Z",
                    ),
                },
            },
        },
        {
            "id": 3,
            "year": 2026,
            "month": 2,
            "project_identifier": "abc1.brics",
            "resource": "brics.i3.clusters.macs",
            "report": _quotas(
                "4.00 TB",
                "40.00 GB",
                "800.00 GB",
                "97.00 GB",
                "2026-02-18T10:00:00.000000000Z",
            ),
        },
        {
            "id": 4,
            "year": 2025,
            "month": 1,
            "project_identifier": "abc2.brics",
            "resource": "brics.i3.clusters.macs",
            "report": {
                "generated_at": "2025-01-31T11:00:00.000000000Z",
                "project": "abc2.brics",
                "project_quotas": {"projects": {"limit": "unlimited", "usage": "1.00 TB"}},
                "user_quotas": {},
                "daily_reports": {
                    # February in a January row: dropped rather than filed wrong.
                    "2025-02-01": {
                        "generated_at": "2025-02-01T11:00:00.000000000Z",
                        "project": "abc2.brics",
                        "project_quotas": {"projects": {"limit": "20.00 TB", "usage": "9.00 TB"}},
                        "user_quotas": {},
                    },
                },
            },
        },
        {
            "id": 5,
            "year": 2025,
            "month": 1,
            "project_identifier": "zzz9.brics",
            "resource": "brics.i3.clusters.macs",
            "report": {
                "generated_at": "2025-01-31T12:00:00.000000000Z",
                "project": "zzz9.brics",
                "project_quotas": {"projects": {"limit": "20.00 TB", "usage": "19.00 TB"}},
                "user_quotas": {
                    "carol.zzz9.brics": {"home": {"limit": "100.00 GB", "usage": "99.00 GB"}}
                },
            },
        },
    ]
