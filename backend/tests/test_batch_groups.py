from backend.tests.conftest import body


def _group(client, project_id, name):
    return body(client.post(f"/projects/{project_id}/groups", json={"name": name}))


def _asset(client, project_id, title):
    return body(client.post("/assets", json={
        "title": title, "type": "text", "content": title, "project_ids": [project_id]
    }))


def _groups(client, project_id):
    project = body(client.get(f"/projects/{project_id}"))
    return {a["id"]: a["project_group_id"] for a in project["assets"]}


def test_batch_move_success(client, sample_project):
    pid = sample_project["id"]
    first = _asset(client, pid, "first")
    second = _asset(client, pid, "second")
    target = _group(client, pid, "Target")

    response = client.patch(f"/projects/{pid}/assets/group", json={
        "asset_ids": [first["id"], second["id"]], "group_id": target["id"]
    })
    assert response.status_code == 200, response.text
    assert body(response)["asset_ids"] == [first["id"], second["id"]]
    grouped = _groups(client, pid)
    assert grouped[first["id"]] == target["id"]
    assert grouped[second["id"]] == target["id"]


def test_batch_move_cross_project_is_atomic(client, sample_project):
    pid = sample_project["id"]
    other = body(client.post("/projects", json={"title": "Other"}))
    own = _asset(client, pid, "own")
    foreign = _asset(client, other["id"], "foreign")
    target = _group(client, pid, "Target")

    response = client.patch(f"/projects/{pid}/assets/group", json={
        "asset_ids": [own["id"], foreign["id"]], "group_id": target["id"]
    })
    assert response.status_code == 404
    assert _groups(client, pid)[own["id"]] is None


def test_batch_move_invalid_id_is_atomic(client, sample_project):
    pid = sample_project["id"]
    asset = _asset(client, pid, "asset")
    target = _group(client, pid, "Target")

    response = client.patch(f"/projects/{pid}/assets/group", json={
        "asset_ids": [asset["id"], 999999], "group_id": target["id"]
    })
    assert response.status_code == 404
    assert _groups(client, pid)[asset["id"]] is None


def test_batch_move_preserves_other_project_group(client, sample_project):
    first_pid = sample_project["id"]
    second = body(client.post("/projects", json={"title": "Second"}))
    asset = _asset(client, first_pid, "shared")
    assert client.post(f"/projects/{second['id']}/assets", json={"asset_ids": [asset['id']]}).status_code == 200
    first_group = _group(client, first_pid, "First")
    second_group = _group(client, second["id"], "Second")
    assert client.patch(f"/projects/{first_pid}/assets/group", json={"asset_ids": [asset["id"]], "group_id": first_group["id"]}).status_code == 200
    assert client.patch(f"/projects/{second['id']}/assets/{asset['id']}/group", json={"group_id": second_group["id"]}).status_code == 200

    response = client.patch(f"/projects/{first_pid}/assets/group", json={"asset_ids": [asset["id"]], "group_id": None})
    assert response.status_code == 200
    assert _groups(client, first_pid)[asset["id"]] is None
    assert _groups(client, second["id"])[asset["id"]] == second_group["id"]
