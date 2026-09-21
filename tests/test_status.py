"""Offline checks for the public status boundary and developer deployment view.

Run: python tests/test_status.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as A

c = A.app.test_client()


def test_public_status_is_coarse_and_cached():
    calls = []
    old = A._run_status_checks
    try:
        A._status_cache.update(checked_at=0, services=None, refreshing=False)
        A._run_status_checks = lambda: calls.append(1) or {
            "chronos": "operational", "ai": "degraded", "data": "operational", "materials": "operational"}
        first = c.get("/status/public")
        second = c.get("/status/public")
        assert first.status_code == 200
        data = first.get_json()
        assert data["status"] == "degraded" and len(calls) == 1
        assert set(data) == {"status", "checked_at", "services"}
        assert set(data["services"]) == {"chronos", "ai", "data", "materials"}
        assert "pinecone" not in str(data).lower() and "project" not in str(data).lower()
        assert second.get_json() == data
    finally:
        A._run_status_checks = old


def test_deployment_requires_verified_developer():
    old_verify = A.verify_user
    old_dev = A.is_dev_user
    try:
        A.verify_user = lambda: None
        assert c.get("/status/deployment").status_code == 401
        A.verify_user = lambda: {"uid": "student", "email": "student@example.test"}
        A.is_dev_user = lambda user: False
        assert c.get("/status/deployment").status_code == 403
        A.is_dev_user = lambda user: True
        # Missing Cloud config deliberately produces no configuration detail.
        data = c.get("/status/deployment").get_json()
        assert data == {"status": "unavailable"}
    finally:
        A.verify_user, A.is_dev_user = old_verify, old_dev


def test_cloud_status_mapping():
    class Build:
        id = "build-123"; status = 2; start_time = None; finish_time = None; source = None
    class BuildClient:
        def list_builds(self, **_kwargs): return iter([Build()])
    class Service:
        latest_ready_revision = "chronos-00042"; traffic = []
    class RunClient:
        def get_service(self, **_kwargs): return Service()
    class BuildModule:
        CloudBuildClient = BuildClient
    class RunModule:
        ServicesClient = RunClient
    old = (A.CLOUD_STATUS_PROJECT_ID, A.CLOUD_STATUS_REGION, A.CLOUD_STATUS_SERVICE,
           A.CLOUD_STATUS_BUILD_TRIGGER_ID, A.cloudbuild_v1, A.run_v2)
    try:
        A.CLOUD_STATUS_PROJECT_ID, A.CLOUD_STATUS_REGION = "p", "us-east1"
        A.CLOUD_STATUS_SERVICE, A.CLOUD_STATUS_BUILD_TRIGGER_ID = "chronos", "trigger"
        A.cloudbuild_v1, A.run_v2 = BuildModule, RunModule
        data = A.deployment_status_snapshot()
        assert data["status"] == "deploying" and data["build"]["state"] == "building"
        assert data["service"]["revision"] == "chronos-00042"
        assert "p" in data["build"]["console_url"]
    finally:
        (A.CLOUD_STATUS_PROJECT_ID, A.CLOUD_STATUS_REGION, A.CLOUD_STATUS_SERVICE,
         A.CLOUD_STATUS_BUILD_TRIGGER_ID, A.cloudbuild_v1, A.run_v2) = old


if __name__ == "__main__":
    test_public_status_is_coarse_and_cached()
    test_deployment_requires_verified_developer()
    test_cloud_status_mapping()
    print("ok - status page boundary, cache, and Cloud deployment mapping")
