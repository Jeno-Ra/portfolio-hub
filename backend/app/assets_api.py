"""素材接口：增删改查 + 两种录入方式（浏览器上传 / 服务器按路径）。

素材（asset）是真正装内容的那一层：一张图、一段视频、一份文档、一段代码。
项目（project）只是容器，见 projects_api.py —— 同一件素材可以挂在多个项目下。

单独成模块的原因：这些路由加上解析逻辑原本都堆在 main.py 里，
一个文件同时管中间件、路由、检索、管理动作和设置，改哪块都要先翻半天。
"""

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app import crud, indexer, kinds, models, vector_store, vision
from backend.app.database import get_db
from backend.app.files import extract_text
from backend.app.response import Envelope, ok
from backend.app.schemas import AssetCreate, AssetResponse, AssetUpdate, IngestRequest
from backend.app.uploads import (
    IMAGE_SUFFIXES,
    check_image,
    check_suffix,
    discard_media,
    is_media_file,
    safe_name,
    save_upload,
)

router = APIRouter()


def _content_for(
    kind: str, path: Path, notes: str, cover_path: Path | None = None
) -> str:
    """按内置类型产出「素材正文」。

    kind 来自文件扩展名（见 kinds.of），不是用户选的 —— 用户选什么都不能改变
    "正文从哪来"这件事，否则图片就没法保证一定走了视觉模型。

    - image    → 视觉模型生成描述
    - video    → 只存元数据（手填 notes），给了封面则额外描述封面画面
    - audio    → 只存手填描述（不做语音转写，零额外成本）
    - 其余     → 解析正文（PDF / Word / 表格 / 代码 / 纯文本），手填 notes 拼在最前面
    """
    try:
        if kind == kinds.IMAGE:
            return vision.describe_image(path)
        if kind == kinds.VIDEO:
            content = notes.strip()
            if cover_path:
                content = (
                    f"{content}\n封面画面：{vision.describe_image(cover_path)}".strip()
                )
            return content or "（暂无描述）"
        if kind == kinds.AUDIO:
            return notes.strip() or "（暂无描述）"
        text = extract_text(path)
        note = notes.strip()
        return f"{note}\n\n{text}" if note else text
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        # 不支持的类型 / 扫描版 PDF / 解码失败，都是调用方的问题
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # 解析失败原因很多，统一转成 502 给用户看
        raise HTTPException(status_code=502, detail=f"文件解析失败：{exc}") from exc


def _split_tags(raw: str) -> list[str]:
    """逗号分隔的标签串 → 列表，中英文逗号都认。"""
    return [part.strip() for part in raw.replace("，", ",").split(",") if part.strip()]


def _store_asset(
    db: Session,
    *,
    title: str,
    kind: str,
    content: str,
    tags: list[str],
    source: Path,
    project_ids: list[int],
    group_id: int | None = None,
) -> models.Asset:
    created = crud.create_asset(
        db,
        AssetCreate(
            title=title,
            type=kind,
            content=content,
            tags=tags,
            project_ids=project_ids,
            group_id=group_id,
        ),
    )
    created.source_path = str(source)
    db.commit()
    db.refresh(created)
    indexer.index_quietly(db, created)
    return created


@router.post("/assets", response_model=Envelope[AssetResponse], status_code=201)
def create_asset(asset: AssetCreate, db: Session = Depends(get_db)):
    for project_id in asset.project_ids:
        if crud.get_project(db, project_id) is None:
            raise HTTPException(status_code=404, detail="项目不存在")
    try:
        created = crud.create_asset(db, asset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 入库后立刻建索引：否则新素材搜不到（之前就是这个坑）
    indexer.index_quietly(db, created)
    return ok(created, "已创建")


@router.post("/assets/upload", response_model=Envelope[AssetResponse], status_code=201)
def upload_asset(
    file: UploadFile = File(...),
    cover: UploadFile | None = File(None),
    title: str = Form(""),
    tags: str = Form(""),
    notes: str = Form(""),
    project_id: int | None = Form(None),
    group_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    """浏览器 / 手机上传录入：文件先落盘到 media/，再按扩展名判定类型并解析。

    为什么网页必须走这条：`<input type=file>` 只给文件内容，不给本地绝对路径，
    所以"填路径"那种方式在浏览器里根本不成立。`/assets/ingest` 留给服务器端场景
    （docker cp 放进去的文件、批量脚本），两边解析逻辑是同一个 `_content_for`。

    两道校验在落盘前后各做一次：
    - 落盘前看扩展名（白名单），不合规的文件根本不会写进磁盘；
    - 落盘后看图片魔数，防止 `.png` 其实是改名的视频（视觉模型那边只会报解码失败）。
    """
    original = safe_name(file.filename or "")
    if project_id is not None:
        if crud.get_project(db, project_id) is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        if group_id is not None and crud.get_group(db, project_id, group_id) is None:
            raise HTTPException(status_code=400, detail="分组不存在或不属于该项目")
    elif group_id is not None:
        raise HTTPException(status_code=400, detail="指定分组时必须同时指定项目")
    try:
        suffix = check_suffix(original)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        saved = save_upload(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        if suffix in IMAGE_SUFFIXES:
            check_image(original, saved)
    except ValueError as exc:
        # 落盘了才发现内容不对：这半份副本必须删掉，否则 media/ 会攒垃圾
        saved.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    saved_cover: Path | None = None
    try:
        if cover is not None and cover.filename:
            cover_name = safe_name(cover.filename)
            if Path(cover_name).suffix.lower() not in IMAGE_SUFFIXES:
                raise HTTPException(
                    status_code=400,
                    detail="封面只能是图片（png / jpg / webp / gif / bmp）",
                )
            saved_cover = save_upload(cover)
            check_image(cover_name, saved_cover)
        # 类型由文件本身决定，用户不用选、也选不错
        kind = kinds.of(saved)
        content = _content_for(kind, saved, notes, saved_cover)
    except (HTTPException, ValueError) as exc:
        # 解析失败就别留下垃圾文件
        saved.unlink(missing_ok=True)
        if saved_cover:
            saved_cover.unlink(missing_ok=True)
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise

    return ok(
        _store_asset(
            db,
            title=title.strip() or Path(original).stem,
            kind=kind,
            content=content,
            tags=_split_tags(tags),
            source=saved,
            project_ids=[project_id] if project_id else [],
            group_id=group_id,
        ),
        "已上传",
    )


@router.post("/assets/{asset_id}/file", response_model=Envelope[AssetResponse])
def replace_asset_file(
    asset_id: int,
    file: UploadFile = File(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    """替换素材的源文件：类型按新扩展名重判、正文重新解析、索引重建、旧副本删掉。

    真实素材经常要改好几版（片子重新剪、文稿改两遍），所以这是"同一件素材换文件"，
    不是"再建一件"——标题、标签、所属项目都保持不动。
    """
    asset = crud.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")

    original = safe_name(file.filename or "")
    try:
        suffix = check_suffix(original)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        saved = save_upload(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        if suffix in IMAGE_SUFFIXES:
            check_image(original, saved)
        kind = kinds.of(saved)
        content = _content_for(kind, saved, notes, None)
    except (HTTPException, ValueError) as exc:
        # 新文件没解析成功就当作没换过：把刚写的副本删掉，旧的继续用
        saved.unlink(missing_ok=True)
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise

    old_source = asset.source_path
    asset.type = kind
    asset.content = content
    asset.source_path = str(saved)
    db.commit()
    db.refresh(asset)
    indexer.index_quietly(db, asset)
    # 只删我们自己存的那份旧副本，用户磁盘上的原文件永远不碰
    discard_media(old_source)
    return ok(asset, "已替换文件")


@router.post("/assets/ingest", response_model=Envelope[AssetResponse], status_code=201)
def ingest_asset(payload: IngestRequest, db: Session = Depends(get_db)):
    """按路径录入：文件已经在这台机器上时用（服务器/脚本），浏览器用 `/assets/upload`。"""
    path = Path(payload.path)
    cover_path = Path(payload.cover_path) if payload.cover_path else None
    kind = payload.type or kinds.of(path)
    content = _content_for(kind, path, payload.notes or "", cover_path)

    return ok(
        _store_asset(
            db,
            title=payload.title or path.stem,
            kind=kind,
            content=content,
            tags=payload.tags,
            source=path,
            project_ids=payload.project_ids,
            group_id=None,
        ),
        "已录入",
    )


@router.get("/assets", response_model=Envelope[list[AssetResponse]])
def list_assets(
    skip: int = 0,
    limit: int = 200,
    project_id: int | None = None,
    kind: str | None = None,
    db: Session = Depends(get_db),
):
    assets = crud.list_assets(db, skip=skip, limit=limit, project_id=project_id, kind=kind)
    return ok(assets, f"共 {len(assets)} 件")


@router.get("/assets/{asset_id}", response_model=Envelope[AssetResponse])
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    asset = crud.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    return ok(asset)


@router.get("/assets/{asset_id}/preview")
@router.head("/assets/{asset_id}/preview")
def preview_asset(asset_id: int, db: Session = Depends(get_db)):
    """只提供本应用媒体目录中的副本；绝不根据客户端输入的路径读取文件。"""
    asset = crud.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    if not asset.source_path:
        raise HTTPException(status_code=404, detail="该素材没有原文件")
    path = Path(asset.source_path)
    if not is_media_file(path) or not path.is_file():
        raise HTTPException(status_code=404, detail="素材文件不可用或不在上传目录中")
    # 仅允许安全媒体和 PDF 内联；其他文件作为文本或附件处理，避免网页脚本执行。
    suffix = path.suffix.lower()
    media = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
        ".opus": "audio/ogg", ".m4a": "audio/mp4", ".flac": "audio/flac",
        ".pdf": "application/pdf",
    }
    return FileResponse(
        path, media_type=media.get(suffix, "text/plain; charset=utf-8"),
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
                 "Content-Security-Policy": "default-src 'none'; sandbox"},
    )


@router.patch("/assets/{asset_id}", response_model=Envelope[AssetResponse])
def patch_asset(asset_id: int, data: AssetUpdate, db: Session = Depends(get_db)):
    asset = crud.update_asset(db, asset_id, data)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    # 内容变了要重建索引，否则检索还在命中旧片段
    indexer.index_quietly(db, asset)
    return ok(asset, "已更新")


@router.delete("/assets/{asset_id}", response_model=Envelope[dict])
def remove_asset(asset_id: int, db: Session = Depends(get_db)):
    """删除走 200 + 信封而不是 204：204 不能带响应体，前端就没法统一按 code 判断。"""
    asset = crud.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")

    source = Path(asset.source_path) if asset.source_path else None
    crud.delete_asset(db, asset_id)
    vector_store.delete_asset(asset_id)  # 同步清掉向量，否则残留脏数据

    # 只清 media/ 里我们自己存的那份副本；用户原始文件永远不碰
    discard_media(str(source) if source is not None else None)

    return ok({"id": asset_id}, "已删除")
