from backend.app import models
from backend.app.database import SessionLocal, init_db
from backend.app.uploads import MEDIA_DIR


def _data(response):
    payload = response.json()
    assert payload["code"] == 0, payload
    return payload["data"]


def _create_group(client, project_id, name, parent_id=None):
    payload = {"name": name}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    response = client.post(f"/projects/{project_id}/groups", json=payload)
    assert response.status_code == 201, response.text
    return _data(response)


def _project_asset(client, project_id, asset_id):
    project = _data(client.get(f"/projects/{project_id}"))
    return next(asset for asset in project["assets"] if asset["id"] == asset_id)


def test_group_scope_and_asset_move(client, sample_project, sample_asset):
    project_id = sample_project["id"]
    other = _data(client.post("/projects", json={"title": "Other"}))
    root = _create_group(client, project_id, "Root")
    child = _create_group(client, project_id, "Same", root["id"])

    # Same names are allowed in different scopes, but a group cannot cross projects.
    assert _create_group(client, project_id, "Same")["parent_id"] is None
    assert client.patch(
        f"/projects/{other['id']}/assets/{sample_asset['id']}/group",
        json={"group_id": child["id"]},
    ).status_code == 404

    moved = client.patch(
        f"/projects/{project_id}/assets/{sample_asset['id']}/group",
        json={"group_id": child["id"]},
    )
    assert moved.status_code == 200
    assert _data(moved)["group_id"] == child["id"]
    assert _project_asset(client, project_id, sample_asset["id"])["project_group_id"] == child["id"]


def test_migration_backfills_old_associations_and_is_idempotent(client, sample_project, sample_asset):
    # Simulate a pre-group database: project_assets exists, but the new link row does not.
    with SessionLocal() as db:
        db.execute(
            models.project_asset_groups.delete().where(
                models.project_asset_groups.c.project_id == sample_project["id"],
                models.project_asset_groups.c.asset_id == sample_asset["id"],
            )
        )
        db.commit()

    init_db()
    with SessionLocal() as db:
        row = db.execute(models.project_asset_groups.select()).first()
        assert row is not None
        assert row.project_id == sample_project["id"]
        assert row.asset_id == sample_asset["id"]
        assert row.group_id is None
        before = db.execute(models.project_asset_groups.select()).all()

    init_db()
    with SessionLocal() as db:
        assert db.execute(models.project_asset_groups.select()).all() == before


def test_init_db_preserves_nonempty_group_association(client, sample_project, sample_asset):
    group = _create_group(client, sample_project["id"], "Keep")
    assert client.patch(
        f"/projects/{sample_project['id']}/assets/{sample_asset['id']}/group",
        json={"group_id": group["id"]},
    ).status_code == 200

    init_db()
    init_db()
    with SessionLocal() as db:
        row = db.execute(
            models.project_asset_groups.select().where(
                models.project_asset_groups.c.project_id == sample_project["id"],
                models.project_asset_groups.c.asset_id == sample_asset["id"],
            )
        ).first()
        assert row.group_id == group["id"]


def test_group_delete_promotes_children_and_clears_parent_asset_data(client, sample_project, sample_asset):
    project_id = sample_project["id"]
    parent = _create_group(client, project_id, "Parent")
    child = _create_group(client, project_id, "Child", parent["id"])
    assert client.patch(
        f"/projects/{project_id}/assets/{sample_asset['id']}/group",
        json={"group_id": parent["id"]},
    ).status_code == 200

    child_asset = _data(
        client.post(
            "/assets",
            json={"title": "Child asset", "type": "text", "content": "x", "project_ids": [project_id]},
        )
    )
    assert client.patch(
        f"/projects/{project_id}/assets/{child_asset['id']}/group",
        json={"group_id": child["id"]},
    ).status_code == 200

    assert client.delete(f"/projects/{project_id}/groups/{parent['id']}").status_code == 200
    groups = _data(client.get(f"/projects/{project_id}/groups"))
    assert next(group for group in groups if group["id"] == child["id"])["parent_id"] is None
    assert _project_asset(client, project_id, sample_asset["id"])["project_group_id"] is None
    assert _project_asset(client, project_id, child_asset["id"])["project_group_id"] == child["id"]


def test_same_asset_keeps_independent_groups_in_both_projects(client, sample_project, sample_asset):
    first_id = sample_project["id"]
    second = _data(client.post("/projects", json={"title": "Second"}))
    first_group = _create_group(client, first_id, "Shared name")
    second_group = _create_group(client, second["id"], "Shared name")
    assert client.post(f"/projects/{second['id']}/assets", json={"asset_ids": [sample_asset['id']]}).status_code == 200

    assert client.patch(
        f"/projects/{first_id}/assets/{sample_asset['id']}/group", json={"group_id": first_group["id"]}
    ).status_code == 200
    assert client.patch(
        f"/projects/{second['id']}/assets/{sample_asset['id']}/group", json={"group_id": second_group["id"]}
    ).status_code == 200
    assert _project_asset(client, first_id, sample_asset["id"])["project_group_id"] == first_group["id"]
    assert _project_asset(client, second["id"], sample_asset["id"])["project_group_id"] == second_group["id"]


def test_upload_with_group_returns_asset_and_persists_group(client, sample_project):
    group = _create_group(client, sample_project["id"], "Uploads")
    response = client.post(
        "/assets/upload",
        data={"project_id": str(sample_project["id"]), "group_id": str(group["id"])},
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 201, response.text
    uploaded = _data(response)
    assert uploaded["title"] == "notes"
    assert uploaded["project_ids"] == [sample_project["id"]]
    assert _project_asset(client, sample_project["id"], uploaded["id"])["project_group_id"] == group["id"]


def test_upload_rejects_invalid_group_before_writing(client, sample_project):
    response = client.post(
        "/assets/upload",
        data={"project_id": str(sample_project["id"]), "group_id": "999999"},
        files={"file": ("bad.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400
    assert not any(MEDIA_DIR.iterdir())


def test_group_rejects_self_and_descendant_cycles(client, sample_project):
    project_id = sample_project["id"]
    root = _create_group(client, project_id, "Root")
    child = _create_group(client, project_id, "Child", root["id"])
    self_cycle = client.patch(f"/projects/{project_id}/groups/{root['id']}", json={"parent_id": root["id"]})
    descendant_cycle = client.patch(
        f"/projects/{project_id}/groups/{root['id']}", json={"parent_id": child["id"]}
    )
    assert self_cycle.status_code == 400
    assert descendant_cycle.status_code == 400


def test_duplicate_sibling_is_rejected_but_same_name_other_parent_is_allowed(client, sample_project):
    project_id = sample_project["id"]
    first = _create_group(client, project_id, "Duplicate")
    duplicate = client.post(f"/projects/{project_id}/groups", json={"name": "Duplicate"})
    assert duplicate.status_code == 400
    parent = _create_group(client, project_id, "Parent")
    nested = _create_group(client, project_id, "Duplicate", parent["id"])
    assert nested["parent_id"] == parent["id"]
    assert nested["id"] != first["id"]


def test_update_rejects_rename_and_move_to_duplicate_sibling(client, sample_project):
    project_id = sample_project["id"]
    parent = _create_group(client, project_id, "Parent")
    first = _create_group(client, project_id, "First", parent["id"])
    second = _create_group(client, project_id, "Second", parent["id"])
    root = _create_group(client, project_id, "First")
    for group, payload in [(second, {"name": "First"}), (first, {"parent_id": None})]:
        response = client.patch(f"/projects/{project_id}/groups/{group['id']}", json=payload)
        assert response.status_code == 400
        assert "同级分组名称已存在" in response.json()["message"]
    groups = {g["id"]: g for g in _data(client.get(f"/projects/{project_id}/groups"))}
    assert groups[second["id"]]["name"] == "Second"
    assert groups[first["id"]]["parent_id"] == parent["id"]
    assert groups[root["id"]]["parent_id"] is None
    assert client.patch(f"/projects/{project_id}/groups/{first['id']}", json={"name": "First"}).status_code == 200


def test_delete_conflicting_promotion_preserves_groups_and_assets(client, sample_project, sample_asset):
    project_id = sample_project["id"]
    parent = _create_group(client, project_id, "Parent")
    child = _create_group(client, project_id, "Conflict", parent["id"])
    root = _create_group(client, project_id, "Conflict")
    assert client.patch(f"/projects/{project_id}/assets/{sample_asset['id']}/group", json={"group_id": parent["id"]}).status_code == 200
    response = client.delete(f"/projects/{project_id}/groups/{parent['id']}")
    assert response.status_code == 400
    assert "名称冲突" in response.json()["message"]
    groups = {g["id"]: g for g in _data(client.get(f"/projects/{project_id}/groups"))}
    assert set(groups) == {parent["id"], child["id"], root["id"]}
    assert groups[child["id"]]["parent_id"] == parent["id"]
    assert _project_asset(client, project_id, sample_asset["id"])["project_group_id"] == parent["id"]


def test_group_list_is_flat_for_deep_nesting(client, sample_project):
    project_id = sample_project["id"]
    parent_id = None
    for index in range(30):
        parent_id = _create_group(client, project_id, f"Level {index}", parent_id)["id"]
    groups = _data(client.get(f"/projects/{project_id}/groups"))
    assert len(groups) == 30
    assert all(set(group) == {"id", "project_id", "name", "parent_id"} for group in groups)
    assert groups[-1]["parent_id"] == groups[-2]["id"]


def test_asset_can_be_detached_and_reattached_to_group(client, sample_project, sample_asset):
    project_id = sample_project["id"]
    group = _create_group(client, project_id, "Reattach")
    endpoint = f"/projects/{project_id}/assets/{sample_asset['id']}/group"
    assert client.patch(endpoint, json={"group_id": group["id"]}).status_code == 200
    detached = client.patch(endpoint, json={"group_id": None})
    assert detached.status_code == 200
    assert _project_asset(client, project_id, sample_asset["id"])["project_group_id"] is None
    assert client.patch(endpoint, json={"group_id": group["id"]}).status_code == 200
    assert _project_asset(client, project_id, sample_asset["id"])["project_group_id"] == group["id"]
