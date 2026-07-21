from __future__ import annotations

import pytest

from waldur_tools.config import Settings

API_URL = "https://portal.example.test"


@pytest.fixture
def settings(tmp_path):
    return Settings(api_url=API_URL, token="secret-token", cache_dir=tmp_path)


@pytest.fixture
def allocations():
    return [
        {
            "url": f"{API_URL}/api/openportal-allocations/aaa/",
            "uuid": "aaa",
            "service_name": "Isambard 3",
            "project_name": "Project A",
            "customer_name": "UKRI",
            "backend_id": "proja.brics",
            "node_usage": "50.0",
            "node_limit": 100,
            "is_active": True,
            "state": "OK",
        },
        {
            "url": f"{API_URL}/api/openportal-allocations/bbb/",
            "uuid": "bbb",
            "service_name": "Isambard-AI",
            "project_name": "Project B",
            "customer_name": "UKRI",
            "backend_id": "projb.brics",
            "node_usage": "0.0",
            "node_limit": 200,
            "is_active": True,
            "state": "OK",
        },
    ]


@pytest.fixture
def associations():
    return [
        {
            "uuid": "1",
            "username": "alice",
            "groupname": "g.a",
            "allocation": f"{API_URL}/api/openportal-allocations/aaa/",
        },
        {
            "uuid": "2",
            "username": "bob",
            "groupname": "g.b",
            "allocation": f"{API_URL}/api/openportal-allocations/bbb/",
        },
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
def usage_reports():
    return [
        {
            "id": 1,
            "year": 2026,
            "month": 3,
            "project_identifier": "proja.brics",
            "resource": "brics.i3.clusters.macs",
            "is_complete": True,
            "report": {
                "project": "proja.brics",
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
