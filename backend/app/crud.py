"""数据访问层：所有对数据库的读写都收在这里，
路由层只负责收发 HTTP，不直接写 SQL / 不直接操作 ORM 对象。

两层结构：项目（Project）是容器，素材（Asset）是内容；素材与项目多对多。
"""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app import models, schemas


def _get_or_create_tags(db: Session, names: list[str]) -> list[models.Tag]:
    """按名字拿标签，没有就新建。同一个请求里重复的名字只算一次。"""
    if not names:
        return []

    # 一次查询把已存在的都捞出来，避免逐个 SELECT
    stmt = select(models.Tag).where(models.Tag.name.in_(names))
    existing = {tag.name: tag for tag in db.scalars(stmt)}

    result: list[models.Tag] = []
    for name in dict.fromkeys(names):  # dict.fromkeys 去重且保留顺序
        tag = existing.get(name)
        if tag is None:
            tag = models.Tag(name=name)
            db.add(tag)
        result.append(tag)
    return result


def _resolve_projects(db: Session, ids: list[int]) -> list[models.Project]:
    """把客户端给的 id 列表换成项目对象；不存在的直接忽略（多传了不报错）。"""
    if not ids:
        return []
    stmt = select(models.Project).where(models.Project.id.in_(ids))
    return list(db.scalars(stmt))


# ---------------------------------------------------------------- 素材


def create_asset(db: Session, data: schemas.AssetCreate) -> models.Asset:
    asset = models.Asset(
        title=data.title,
        type=data.type,
        content=data.content,
    )
    asset.tags = _get_or_create_tags(db, data.tags)
    asset.projects = _resolve_projects(db, data.project_ids)
    db.add(asset)
    db.flush()
    if data.group_id is not None:
        if len(data.project_ids) != 1 or get_group(db, data.project_ids[0], data.group_id) is None:
            raise ValueError("分组必须属于唯一项目")
        db.execute(models.project_asset_groups.insert().values(project_id=data.project_ids[0], asset_id=asset.id, group_id=data.group_id))
    db.commit()
    db.refresh(asset)
    return asset


def _asset_query():
    """素材的默认加载策略：标签和所属项目一起捞，避免列表页 N+1 查询。"""
    return select(models.Asset).options(
        selectinload(models.Asset.tags),
        selectinload(models.Asset.projects),
    )


def list_assets(
    db: Session,
    skip: int = 0,
    limit: int = 200,
    project_id: int | None = None,
    kind: str | None = None,
) -> list[models.Asset]:
    """列出素材。可按项目或内置类型过滤；必须显式 order_by，否则分页会漏行/重行。"""
    stmt = _asset_query().order_by(models.Asset.id)
    if project_id is not None:
        stmt = stmt.where(
            models.Asset.id.in_(
                select(models.project_assets.c.asset_id).where(
                    models.project_assets.c.project_id == project_id
                )
            )
        )
    if kind:
        stmt = stmt.where(models.Asset.type == kind)
    return list(db.scalars(stmt.offset(skip).limit(limit)))


def assets_missing_hash(db: Session) -> list[models.Asset]:
    """返回历史素材中有源文件但尚未计算哈希的记录，用于惰性迁移。"""
    stmt = _asset_query().where(
        models.Asset.content_hash.is_(None), models.Asset.source_path.is_not(None)
    )
    return list(db.scalars(stmt))


def find_assets_by_hash(db: Session, content_hash: str) -> list[models.Asset]:
    """查找完全相同的上传素材，预加载项目以便用户选择要复用哪一条。"""
    stmt = (
        select(models.Asset)
        .options(selectinload(models.Asset.tags), selectinload(models.Asset.projects))
        .where(models.Asset.content_hash == content_hash)
        .order_by(models.Asset.id)
    )
    return list(db.scalars(stmt))


def get_asset(db: Session, asset_id: int) -> models.Asset | None:
    return db.get(
        models.Asset,
        asset_id,
        options=[
            selectinload(models.Asset.tags),
            selectinload(models.Asset.projects),
        ],
    )


def update_asset(
    db: Session, asset_id: int, data: schemas.AssetUpdate
) -> models.Asset | None:
    """部分更新。只处理客户端真正传了的字段（exclude_unset），
    否则 PATCH 会退化成"把没传的字段全部清空"。"""
    asset = get_asset(db, asset_id)
    if asset is None:
        return None

    payload = data.model_dump(exclude_unset=True)
    for field, value in payload.items():
        if field == "tags":
            asset.tags = _get_or_create_tags(db, value)
        elif field == "project_ids":
            old_ids = {p.id for p in asset.projects}
            asset.projects = _resolve_projects(db, value)
            removed = old_ids - set(value)
            if removed:
                db.execute(models.project_asset_groups.delete().where(models.project_asset_groups.c.asset_id == asset_id, models.project_asset_groups.c.project_id.in_(removed)))
        else:
            setattr(asset, field, value)

    db.commit()
    db.refresh(asset)
    return asset


def delete_asset(db: Session, asset_id: int) -> models.Asset | None:
    """删除素材，返回被删的对象（接口层要用它的 source_path 去清上传副本）。
    切片与标签/项目关联由关系上的 cascade 一并清理。"""
    asset = get_asset(db, asset_id)
    if asset is None:
        return None
    db.delete(asset)
    db.commit()
    return asset


# ---------------------------------------------------------------- 项目


def create_project(db: Session, data: schemas.ProjectCreate) -> models.Project:
    project = models.Project(title=data.title, summary=data.summary)
    project.tags = _get_or_create_tags(db, data.tags)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def list_projects(db: Session, skip: int = 0, limit: int = 200) -> list[models.Project]:
    """项目列表。素材一起捞出来，列表上要显示件数和类型分布。"""
    stmt = (
        select(models.Project)
        .options(
            selectinload(models.Project.tags),
            selectinload(models.Project.assets),
        )
        .order_by(models.Project.id.desc())  # 新建的排前面
        .offset(skip)
        .limit(limit)
    )
    return list(db.scalars(stmt))


def get_project(db: Session, project_id: int) -> models.Project | None:
    return db.get(
        models.Project,
        project_id,
        options=[
            selectinload(models.Project.tags),
            selectinload(models.Project.assets).selectinload(models.Asset.tags),
            selectinload(models.Project.assets).selectinload(models.Asset.projects),
        ],
    )


def update_project(
    db: Session, project_id: int, data: schemas.ProjectUpdate
) -> models.Project | None:
    project = get_project(db, project_id)
    if project is None:
        return None

    payload = data.model_dump(exclude_unset=True)
    for field, value in payload.items():
        if field == "tags":
            project.tags = _get_or_create_tags(db, value)
        else:
            setattr(project, field, value)

    db.commit()
    db.refresh(project)
    return project


def set_project_cover(
    db: Session, project_id: int, cover_path: str
) -> models.Project | None:
    """给项目换封面。封面就是一张图，存路径，不复制内容。"""
    project = get_project(db, project_id)
    if project is None:
        return None

    old = project.cover_path
    project.cover_path = cover_path
    db.commit()
    db.refresh(project)

    # 旧封面是我们自己存的就删掉，别让 media/ 攒废弃图片
    if old and old != cover_path:
        stale = Path(old)
        if stale.is_file():
            stale.unlink(missing_ok=True)
    return project


def delete_project(db: Session, project_id: int) -> bool:
    """删项目只删「容器」，素材本身留着（素材可能还挂在别的项目下）。
    要连素材一起删，由接口层先逐条 delete_asset。"""
    project = get_project(db, project_id)
    if project is None:
        return False
    db.delete(project)
    db.commit()
    return True


def link_assets(db: Session, project_id: int, asset_ids: list[int]) -> int:
    """把已有素材挂到项目下（已挂的不重复加），返回实际新挂上的条数。"""
    project = get_project(db, project_id)
    if project is None:
        return 0

    current = {asset.id for asset in project.assets}
    incoming = [
        asset for asset in _resolve_assets(db, asset_ids) if asset.id not in current
    ]
    project.assets.extend(incoming)
    db.commit()
    return len(incoming)


def _resolve_assets(db: Session, ids: list[int]) -> list[models.Asset]:
    if not ids:
        return []
    stmt = select(models.Asset).where(models.Asset.id.in_(ids))
    return list(db.scalars(stmt))


def unlink_asset(db: Session, project_id: int, asset_id: int) -> bool:
    """从项目里移除一条素材（素材本身不删）。"""
    project = get_project(db, project_id)
    if project is None:
        return False
    target = next((asset for asset in project.assets if asset.id == asset_id), None)
    if target is None:
        return False
    project.assets.remove(target)
    db.execute(models.project_asset_groups.delete().where(models.project_asset_groups.c.project_id == project_id, models.project_asset_groups.c.asset_id == asset_id))
    db.commit()
    return True


# ---------------------------------------------------------------- 素材分组

def list_groups(db: Session, project_id: int):
    return list(db.scalars(select(models.AssetGroup).where(models.AssetGroup.project_id == project_id).order_by(models.AssetGroup.id)))

def get_group(db: Session, project_id: int, group_id: int):
    return db.scalar(select(models.AssetGroup).where(models.AssetGroup.project_id == project_id, models.AssetGroup.id == group_id))

def create_group(db: Session, project_id: int, data: schemas.GroupCreate):
    if data.parent_id is not None and get_group(db, project_id, data.parent_id) is None: return None
    sibling = db.scalar(select(models.AssetGroup).where(models.AssetGroup.project_id == project_id, models.AssetGroup.parent_id == data.parent_id, models.AssetGroup.name == data.name))
    if sibling is not None: raise ValueError("同级分组名称已存在")
    g=models.AssetGroup(project_id=project_id,name=data.name,parent_id=data.parent_id); db.add(g); db.commit(); db.refresh(g); return g

def update_group(db: Session, project_id: int, group_id: int, data: schemas.GroupUpdate):
    g=get_group(db, project_id, group_id)
    if g is None: return None
    if data.parent_id == group_id: raise ValueError("分组不能设为自身子级")
    if data.parent_id is not None:
        p=get_group(db, project_id, data.parent_id)
        if p is None: raise ValueError("父分组不存在")
        cur=p
        while cur is not None:
            if cur.id == group_id: raise ValueError("不能移动到自己的子分组")
            cur=cur.parent
    changes = data.model_dump(exclude_unset=True)
    name = changes.get("name", g.name)
    parent_id = changes.get("parent_id", g.parent_id)
    if name is None:
        raise ValueError("分组名称不能为空")
    sibling = db.scalar(select(models.AssetGroup).where(
        models.AssetGroup.project_id == project_id,
        models.AssetGroup.parent_id == parent_id,
        models.AssetGroup.name == name,
        models.AssetGroup.id != group_id,
    ))
    if sibling is not None:
        raise ValueError("同级分组名称已存在")
    for k, v in changes.items():
        setattr(g, k, v)
    db.commit(); db.refresh(g); return g

def delete_group(db: Session, project_id: int, group_id: int):
    g=get_group(db, project_id, group_id)
    if g is None: return False
    # 检查提升后的根分组名称，发生冲突时不得修改任何数据。
    children = list(g.children)
    names = [child.name for child in children]
    if names:
        conflict = db.scalar(select(models.AssetGroup.name).where(
            models.AssetGroup.project_id == project_id,
            models.AssetGroup.parent_id.is_(None),
            models.AssetGroup.id != group_id,
            models.AssetGroup.name.in_(names),
        ))
        if conflict is not None or len(names) != len(set(names)):
            raise ValueError(f"子分组提升到根目录时名称冲突：{conflict or '子分组重名'}，请先重命名")
    # 子分组提升到根，关联素材归根，避免误删内容
    for c in children: c.parent = None
    db.flush()
    db.execute(models.project_asset_groups.update().where(models.project_asset_groups.c.project_id==project_id, models.project_asset_groups.c.group_id==group_id).values(group_id=None))
    db.delete(g); db.commit(); return True

def move_asset_group(db: Session, project_id: int, asset_id: int, group_id: int|None):
    if db.scalar(select(models.Project).where(models.Project.id==project_id)) is None: return "project"
    if db.scalar(select(models.project_assets).where(models.project_assets.c.project_id==project_id, models.project_assets.c.asset_id==asset_id)) is None: return "asset"
    if group_id is not None and get_group(db, project_id, group_id) is None: return "group"
    row=db.execute(select(models.project_asset_groups).where(models.project_asset_groups.c.project_id==project_id, models.project_asset_groups.c.asset_id==asset_id)).first()
    if row: db.execute(models.project_asset_groups.update().where(models.project_asset_groups.c.project_id==project_id, models.project_asset_groups.c.asset_id==asset_id).values(group_id=group_id))
    else: db.execute(models.project_asset_groups.insert().values(project_id=project_id,asset_id=asset_id,group_id=group_id))
    db.commit(); return None


def move_assets_group(db: Session, project_id: int, asset_ids: list[int], group_id: int | None):
    """Atomically move multiple project asset associations after validating all inputs."""
    if db.scalar(select(models.Project).where(models.Project.id == project_id)) is None:
        return "project"
    unique_ids = set(asset_ids)
    found = set(db.scalars(select(models.project_assets.c.asset_id).where(
        models.project_assets.c.project_id == project_id,
        models.project_assets.c.asset_id.in_(unique_ids),
    )))
    if found != unique_ids:
        return "asset"
    if group_id is not None and get_group(db, project_id, group_id) is None:
        return "group"
    for asset_id in unique_ids:
        row = db.execute(select(models.project_asset_groups).where(
            models.project_asset_groups.c.project_id == project_id,
            models.project_asset_groups.c.asset_id == asset_id,
        )).first()
        if row:
            db.execute(models.project_asset_groups.update().where(
                models.project_asset_groups.c.project_id == project_id,
                models.project_asset_groups.c.asset_id == asset_id,
            ).values(group_id=group_id))
        else:
            db.execute(models.project_asset_groups.insert().values(
                project_id=project_id, asset_id=asset_id, group_id=group_id,
            ))
    db.commit()
    return None

# ---------------------------------------------------------------- 会话 / 消息


def create_conversation(db: Session, title: str = "新对话") -> models.Conversation:
    conversation = models.Conversation(title=title)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def add_message(
    db: Session,
    conversation_id: int,
    role: str,
    content: str,
) -> models.Message:
    """往会话里追加一条消息。role 只能是 user / assistant。"""
    message = models.Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message
