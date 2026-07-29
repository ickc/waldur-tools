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
            "project_name": "Project A",
            "customer_name": "UKRI",
            "total_credits": "30000.00000",
            "total_spend": "10000.00",
            "current_month_spend": "2000.00",
            "end_date": "2026-08-01",
        },
        {
            "project_name": "Project B",
            "customer_name": "UKRI",
            "total_credits": "1000.00000",
            "total_spend": "0.00",
            "current_month_spend": "0.00",
            "end_date": "2026-08-01",
        },
    ]


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
