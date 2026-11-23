"""项目接口：项目是「容器」，本身没有类型，只有标题 / 简介 / 封面 / 标签。

一个项目下可以同时有图片、视频、音频、文本、代码、文档 —— 这正是"作品集"该有的样子。
素材与项目是多对多：同一段素材可以同时出现在「王者无界嬴政」和「2026 合集」里，
删项目不会删素材（素材可能还挂在别的项目下），删素材会自动从所有项目里消失。
"""

from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.app import crud, indexer, models, vector_store
from backend.app.database import get_db
from backend.app.response import Envelope, ok
from backend.app.schemas import (
    LinkAssetsRequest,
    ProjectCreate,
    ProjectDetail,
    ProjectResponse,
    ProjectUpdate,
)
from backend.app.uploads import (
    IMAGE_SUFFIXES,
    check_image,
    discard_media,
    safe_name,
    save_upload,
)

router = APIRouter()


def _payload(project: models.Project) -> dict:
    """把 ORM 对象压成响应体：素材件数与类型分布在列表页就要显示，
    否则用户得点进去才知道这个项目里到底有什么。"""
    counts = Counter(asset.type or "" for asset in project.assets)
    cover = project.cover_path
    return {
        "id": project.id,
        "title": project.title,
        "summary": project.summary,
        "cover_path": cover,
        # 浏览器拿不到容器里的绝对路径，统一走 /media 静态托管
        "cover_url": f"/media/{Path(cover).name}" if cover else None,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "tags": [tag.name for tag in project.tags],
        "asset_count": len(project.assets),
        "kind_counts": dict(counts),
    }


def _detail(project: models.Project) -> dict:
    return {**_payload(project), "assets": sorted(project.assets, key=lambda a: a.id)}


@router.get("/projects", response_model=Envelope[list[ProjectResponse]])
def list_projects(skip: int = 0, limit: int = 200, db: Session = Depends(get_db)):
    projects = crud.list_projects(db, skip=skip, limit=limit)
    return ok([_payload(project) for project in projects], f"共 {len(projects)} 个项目")


@router.post("/projects", response_model=Envelope[ProjectResponse], status_code=201)
def create_project(project: ProjectCreate, db: Session = Depends(get_db)):
    created = crud.create_project(db, project)
    return ok(_payload(created), "已创建")


@router.get("/projects/{project_id}", response_model=Envelope[ProjectDetail])
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return ok(_detail(project))


@router.patch("/projects/{project_id}", response_model=Envelope[ProjectResponse])
def patch_project(project_id: int, data: ProjectUpdate, db: Session = Depends(get_db)):
    project = crud.update_project(db, project_id, data)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return ok(_payload(project), "已更新")


@router.post("/projects/{project_id}/cover", response_model=Envelope[ProjectResponse])
def upload_cover(
    project_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    """给项目换封面（可选功能，不传就没有封面，列表里显示占位）。

    单独一个接口而不是塞进 `POST /projects`：新建项目是最常走的一条路，
    为了可选的封面把所有请求都变成 multipart 不值得。
    """
    if crud.get_project(db, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    original = safe_name(file.filename or "")
    if Path(original).suffix.lower() not in IMAGE_SUFFIXES:
        raise HTTPException(
            status_code=400, detail="封面只能是图片（png / jpg / webp / gif / bmp）"
        )

    try:
        saved = save_upload(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        check_image(original, saved)
    except ValueError as exc:
        # 落盘了才发现不是图片：这半份副本要删掉
        saved.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    project = crud.set_project_cover(db, project_id, str(saved))
    return ok(_payload(project), "封面已更新")


@router.delete("/projects/{project_id}", response_model=Envelope[dict])
def remove_project(
    project_id: int, with_assets: bool = False, db: Session = Depends(get_db)
):
    """默认只删容器，素材留着；传 with_assets=true 才会连素材一起删（连向量一起清）。"""
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    removed_assets = 0
    if with_assets:
        for asset in list(project.assets):
            source = asset.source_path
            crud.delete_asset(db, asset.id)
            vector_store.delete_asset(asset.id)
            discard_media(source)  # 连上传副本一起清，别在 media/ 留孤儿文件
            removed_assets += 1

    crud.delete_project(db, project_id)
    message = f"已删除项目，连同 {removed_assets} 件素材" if removed_assets else "已删除项目"
    return ok({"id": project_id, "removed_assets": removed_assets}, message)


@router.post("/projects/{project_id}/assets", response_model=Envelope[dict])
def link_assets(
    project_id: int, payload: LinkAssetsRequest, db: Session = Depends(get_db)
):
    """把已有素材挂到这个项目下（复用它，不复制内容）。"""
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    added = crud.link_assets(db, project_id, payload.asset_ids)
    return ok({"project_id": project_id, "added": added}, f"已挂上 {added} 件素材")


@router.delete("/projects/{project_id}/assets/{asset_id}", response_model=Envelope[dict])
def unlink_asset(project_id: int, asset_id: int, db: Session = Depends(get_db)):
    """从这里移除，但素材本身还在（可能挂在别的项目下）。"""
    if not crud.unlink_asset(db, project_id, asset_id):
        raise HTTPException(status_code=404, detail="这个项目下没有这件素材")
    # 素材的项目归属变了，向量库里的 projects 元数据也要跟着更新
    asset = crud.get_asset(db, asset_id)
    if asset is not None:
        indexer.index_quietly(db, asset)
    return ok({"project_id": project_id, "asset_id": asset_id}, "已移除")
