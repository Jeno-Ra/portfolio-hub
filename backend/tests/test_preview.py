"""素材预览使用隔离测试目录，不访问真实库。"""

from pathlib import Path

from backend.tests.conftest import body


def test_preview_upload_range_and_missing(client, sample_project):
    asset = body(client.post(
        "/assets/upload", files={"file": ("clip.mp4", b"0123456789", "video/mp4")},
        data={"project_id": str(sample_project["id"])},
    ))
    url = f"/assets/{asset['id']}/preview"
    response = client.get(url, headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-type"].startswith("video/mp4")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.head(url).status_code == 200
    assert client.get("/assets/99999/preview").status_code == 404


def test_preview_rejects_external_path(client, sample_asset, tmp_path):
    from backend.app import crud
    from backend.app.database import SessionLocal

    path = tmp_path / "private.txt"
    path.write_text("secret", encoding="utf-8")
    with SessionLocal() as db:
        asset = crud.get_asset(db, sample_asset["id"])
        asset.source_path = str(Path(path))
        db.commit()
    assert client.get(f"/assets/{sample_asset['id']}/preview").status_code == 404
