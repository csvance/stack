"""ADO PR add_comment / add_label / remove_label / list_labels via respx."""

from __future__ import annotations

import httpx
import respx

from stack_core.ado import pr as ado_pr
from stack_core.ado.client import AdoClient

ORG_URL = "https://dev.azure.com/myorg"
PROJECT = "myproj"
REPO = "myrepo"
PR_ID = 42
PAT = "p"


@respx.mock
def test_add_comment_posts_thread():
    captured = {}

    def handler(request):
        import json
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": 999})

    base = f"{ORG_URL}/{PROJECT}/_apis/git/repositories/{REPO}/pullrequests/{PR_ID}/threads"
    respx.post(base).mock(side_effect=handler)
    with AdoClient(ORG_URL, PAT) as client:
        thread_id = ado_pr.add_comment(
            client, PROJECT, REPO, PR_ID, "hello", organization_url=ORG_URL,
        )
    assert thread_id == 999
    assert captured["body"]["comments"][0]["content"] == "hello"


@respx.mock
def test_add_label_posts_to_labels_endpoint():
    captured = {}

    def handler(request):
        import json
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "guid", "name": "stack-conflict", "active": True})

    base = f"{ORG_URL}/{PROJECT}/_apis/git/repositories/{REPO}/pullrequests/{PR_ID}/labels"
    respx.post(base).mock(side_effect=handler)
    with AdoClient(ORG_URL, PAT) as client:
        ado_pr.add_label(
            client, PROJECT, REPO, PR_ID, "stack-conflict", organization_url=ORG_URL,
        )
    assert captured["body"] == {"name": "stack-conflict"}


@respx.mock
def test_remove_label_calls_delete():
    base = f"{ORG_URL}/{PROJECT}/_apis/git/repositories/{REPO}/pullrequests/{PR_ID}/labels/stack-conflict"
    route = respx.delete(base).mock(return_value=httpx.Response(204))
    with AdoClient(ORG_URL, PAT) as client:
        ado_pr.remove_label(
            client, PROJECT, REPO, PR_ID, "stack-conflict", organization_url=ORG_URL,
        )
    assert route.call_count == 1


@respx.mock
def test_list_labels_returns_names():
    base = f"{ORG_URL}/{PROJECT}/_apis/git/repositories/{REPO}/pullrequests/{PR_ID}/labels"
    respx.get(base).mock(
        return_value=httpx.Response(
            200,
            json={"value": [{"name": "stack-conflict"}, {"name": "wip"}]},
        )
    )
    with AdoClient(ORG_URL, PAT) as client:
        labels = ado_pr.list_labels(client, PROJECT, REPO, PR_ID, organization_url=ORG_URL)
    assert labels == ["stack-conflict", "wip"]


@respx.mock
def test_list_labels_handles_empty():
    base = f"{ORG_URL}/{PROJECT}/_apis/git/repositories/{REPO}/pullrequests/{PR_ID}/labels"
    respx.get(base).mock(return_value=httpx.Response(200, json={"value": []}))
    with AdoClient(ORG_URL, PAT) as client:
        labels = ado_pr.list_labels(client, PROJECT, REPO, PR_ID, organization_url=ORG_URL)
    assert labels == []
