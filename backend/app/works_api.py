"""作品接口：增删改查 + 两种录入方式（浏览器上传 / 服务器按路径）。

单独成模块的原因：这些路由加上解析逻辑原本都堆在 main.py 里，
一个文件同时管中间件、路由、检索、管理动作和设置，改哪块都要先翻半天。
"""

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.app import crud, indexer, vector_store, vision
from backend.app.database import get_db
from backend.app.files import CODE_SUFFIXES, extract_text
from backend.app.models import Work
from backend.app.response import Envelope, ok
from backend.app.schemas import (
    IngestRequest,
    WorkCreate,
    WorkResponse,
    WorkUpdate,
)
from backend.app.uploads import (
    IMAGE_SUFFIXES,
    VIDEO_SUFFIXES,
    check_image,
    check_suffix,
    is_media_file,
    safe_name,
    save_upload,
)

router = APIRouter()


def _infer_type(path: Path) -> str:
    """按扩展名推断作品类型，推不出来就当文本。"""
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in CODE_SUFFIXES:
        return "code"
    return "text"


def _content_for(
    work_type: str, path: Path, notes: str, cover_path: Path | None = None
) -> str:
    """按类型产出「作品内容」。

    图片 → 视觉模型生成描述
    视频 → 只存元数据（手填 notes），给了封面则额外描述封面画面
    其余 → 解析正文（PDF / Word / 表格 / 代码 / 纯文本），手填 notes 拼在最前面
    """
    try:
        if work_type == "image":
            return vision.describe_image(path)
        if work_type == "video":
            content = notes.strip()
            if cover_path:
                content = f"{content}\n封面画面：{vision.describe_image(cover_path)}".strip()
            return content or "（暂无描述）"
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


def _store_work(
    db: Session, *, title: str, work_type: str, content: str, tags: list[str], source: Path
) -> Work:
    created = crud.create_work(
        db, WorkCreate(title=title, type=work_type, content=content, tags=tags)
    )
    created.source_path = str(source)
    db.commit()
    db.refresh(created)
    indexer.index_quietly(db, created)
    return created


@router.post("/works", response_model=Envelope[WorkResponse], status_code=201)
def create_work(work: WorkCreate, db: Session = Depends(get_db)):
    created = crud.create_work(db, work)
    # 入库后立刻建索引：否则新作品搜不到（之前就是这个坑）
    indexer.index_quietly(db, created)
    return ok(created, "已创建")


@router.post("/works/upload", response_model=Envelope[WorkResponse], status_code=201)
def upload_work(
    file: UploadFile = File(...),
    cover: UploadFile | None = File(None),
    title: str = Form(""),
    type: str = Form(""),
    tags: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    """浏览器 / 手机上传录入：文件先落盘到 media/，再按类型解析。

    为什么网页必须走这条：`<input type=file>` 只给文件内容，不给本地绝对路径，
    所以"填路径"那种方式在浏览器里根本不成立。`/works/ingest` 留给服务器端场景
    （docker cp 放进去的文件、批量脚本），两边解析逻辑是同一个 `_content_for`。

    两道校验在落盘前后各做一次：
    - 落盘前看扩展名（白名单），不合规的文件根本不会写进磁盘；
    - 落盘后看图片魔数，防止 `.png` 其实是改名的视频（视觉模型那边只会报解码失败）。
    """
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
                    status_code=400, detail="封面只能是图片（png / jpg / webp / gif / bmp）"
                )
            saved_cover = save_upload(cover)
            check_image(cover_name, saved_cover)
        work_type = type or _infer_type(saved)
        content = _content_for(work_type, saved, notes, saved_cover)
    except (HTTPException, ValueError) as exc:
        # 解析失败就别留下垃圾文件
        saved.unlink(missing_ok=True)
        if saved_cover:
            saved_cover.unlink(missing_ok=True)
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise

    return ok(
        _store_work(
            db,
            title=title.strip() or Path(original).stem,
            work_type=work_type,
            content=content,
            tags=_split_tags(tags),
            source=saved,
        ),
        "已上传",
    )


@router.post("/works/ingest", response_model=Envelope[WorkResponse], status_code=201)
def ingest_work(payload: IngestRequest, db: Session = Depends(get_db)):
    """按路径录入：文件已经在这台机器上时用（服务器/脚本），浏览器用 `/works/upload`。"""
    path = Path(payload.path)
    work_type = payload.type or _infer_type(path)
    cover_path = Path(payload.cover_path) if payload.cover_path else None
    content = _content_for(work_type, path, payload.notes or "", cover_path)

    return ok(
        _store_work(
            db,
            title=payload.title or path.stem,
            work_type=work_type,
            content=content,
            tags=payload.tags,
            source=path,
        ),
        "已录入",
    )


@router.get("/works", response_model=Envelope[list[WorkResponse]])
def list_works(skip: int = 0, limit: int = 10, db: Session = Depends(get_db)):
    works = crud.list_works(db, skip=skip, limit=limit)
    return ok(works, f"共 {len(works)} 条")


@router.get("/works/{work_id}", response_model=Envelope[WorkResponse])
def get_work(work_id: int, db: Session = Depends(get_db)):
    work = crud.get_work(db, work_id)
    if work is None:
        raise HTTPException(status_code=404, detail="作品不存在")
    return ok(work)


@router.patch("/works/{work_id}", response_model=Envelope[WorkResponse])
def patch_work(work_id: int, data: WorkUpdate, db: Session = Depends(get_db)):
    work = crud.update_work(db, work_id, data)
    if work is None:
        raise HTTPException(status_code=404, detail="作品不存在")
    # 内容变了要重建索引，否则检索还在命中旧片段
    indexer.index_quietly(db, work)
    return ok(work, "已更新")


@router.delete("/works/{work_id}", response_model=Envelope[dict])
def remove_work(work_id: int, db: Session = Depends(get_db)):
    """删除走 200 + 信封而不是 204：204 不能带响应体，前端就没法统一按 code 判断。"""
    work = crud.get_work(db, work_id)
    if work is None:
        raise HTTPException(status_code=404, detail="作品不存在")

    source = Path(work.source_path) if work.source_path else None
    crud.delete_work(db, work_id)
    vector_store.delete_work(work_id)  # 同步清掉向量，否则残留脏数据

    # 只清 media/ 里我们自己存的那份副本；用户原始文件永远不碰
    if source is not None and is_media_file(source):
        source.unlink(missing_ok=True)

    return ok({"id": work_id}, "已删除")
