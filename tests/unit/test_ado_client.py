"""ADO client: PAT auth, retries, PR list/create/update parsing."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from stack_core.ado import pr as ado_pr
from stack_core.ado.client import AdoApiError, AdoClient

ORG_URL = "https://dev.azure.com/myorg"
PROJECT = "myproj"
REPO = "myrepo"
PAT = "secretpat"


@respx.mock
def test_pat_auth_via_basic_header():
    """ADO uses HTTP Basic with empty username and PAT as password."""
    route = respx.get(f"{ORG_URL}/foo").mock(return_value=httpx.Response(200, json={}))
    with AdoClient(ORG_URL, PAT) as client:
        client.get("/foo")
    request = route.calls[0].request
    auth_header = request.headers["authorization"]
    assert auth_header.startswith("Basic ")
    decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode()
    assert decoded == f":{PAT}"


@respx.mock
def test_retries_on_5xx():
    route = respx.get(f"{ORG_URL}/foo").mock(
        side_effect=[
            httpx.Response(503, text="busy"),
            httpx.Response(503, text="busy"),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    with AdoClient(ORG_URL, PAT, retry_initial_delay=0.0) as client:
        response = client.get("/foo")
    assert response.json() == {"ok": True}
    assert route.call_count == 3


@respx.mock
def test_gives_up_after_max_retries():
    respx.get(f"{ORG_URL}/foo").mock(return_value=httpx.Response(503, text="still busy"))
    with (
        AdoClient(ORG_URL, PAT, retry_attempts=2, retry_initial_delay=0.0) as client,
        pytest.raises(AdoApiError) as excinfo,
    ):
        client.get("/foo")
    assert excinfo.value.status_code == 503


@respx.mock
def test_4xx_raises_without_retry():
    route = respx.get(f"{ORG_URL}/foo").mock(return_value=httpx.Response(404, text="nope"))
    with AdoClient(ORG_URL, PAT) as client, pytest.raises(AdoApiError) as excinfo:
        client.get("/foo")
    assert excinfo.value.status_code == 404
    assert route.call_count == 1


@respx.mock
def test_list_for_branch_normalizes_ref():
    captured = {}

    def handler(request):
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"value": []})

    respx.get(f"{ORG_URL}/{PROJECT}/_apis/git/repositories/{REPO}/pullrequests").mock(
        side_effect=handler
    )
    with AdoClient(ORG_URL, PAT) as client:
        ado_pr.list_for_branch(
            client, PROJECT, REPO, "my-branch",
            organization_url=ORG_URL,
        )
    assert captured["params"]["searchCriteria.sourceRefName"] == "refs/heads/my-branch"


@respx.mock
def test_pull_request_parsing():
    payload = {
        "value": [
            {
                "pullRequestId": 42,
                "status": "active",
                "sourceRefName": "refs/heads/feat-stacked-1",
                "targetRefName": "refs/heads/main",
                "title": "Add feature",
                "description": "Body",
            }
        ]
    }
    respx.get(f"{ORG_URL}/{PROJECT}/_apis/git/repositories/{REPO}/pullrequests").mock(
        return_value=httpx.Response(200, json=payload)
    )
    with AdoClient(ORG_URL, PAT) as client:
        prs = ado_pr.list_for_branch(
            client, PROJECT, REPO, "feat-stacked-1", organization_url=ORG_URL
        )
    assert len(prs) == 1
    assert prs[0].pr_id == 42
    assert prs[0].source_branch == "refs/heads/feat-stacked-1"
    assert prs[0].web_url.endswith("/pullrequest/42")


@respx.mock
def test_create_pr_posts_normalized_refs():
    captured = {}

    def handler(request):
        captured["body"] = request.content.decode()
        return httpx.Response(
            201,
            json={
                "pullRequestId": 1,
                "status": "active",
                "sourceRefName": "refs/heads/feat-stacked-1",
                "targetRefName": "refs/heads/main",
                "title": "T",
                "description": "D",
            },
        )

    respx.post(f"{ORG_URL}/{PROJECT}/_apis/git/repositories/{REPO}/pullrequests").mock(
        side_effect=handler
    )
    with AdoClient(ORG_URL, PAT) as client:
        ado_pr.create(
            client, PROJECT, REPO,
            source_branch="feat-stacked-1",
            target_branch="main",
            title="T",
            description="D",
            organization_url=ORG_URL,
        )
    assert '"sourceRefName":"refs/heads/feat-stacked-1"' in captured["body"].replace(" ", "")
    assert '"targetRefName":"refs/heads/main"' in captured["body"].replace(" ", "")
