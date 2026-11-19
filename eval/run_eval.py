"""评测脚本：recall@5、拒绝准确率、检索耗时。

用法（在项目根目录）：
    uv run python eval/run_eval.py

工作方式：
1. 从 eval/corpus.json 录入语料（标记为评测数据），建索引
2. 从 eval/questions.json 逐条检索，统计指标
3. 结束后删掉自己录入的作品，不影响你库里的真实数据

注意：corpus.json 是**合成数据**，用它跑出的数字只能证明"链路与阈值是否合理"，
不能当作简历上的真实指标。换成你自己的作品后重跑，那才是真实数字。
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app import crud, models, schemas, vector_store  # noqa: E402
from backend.app.database import SessionLocal, init_db  # noqa: E402
from backend.app.embedding import embed_text  # noqa: E402
from backend.app.indexer import index_work  # noqa: E402
from backend.app.main import MAX_DISTANCE, RETRIEVAL_TOP_K  # noqa: E402

EVAL_DIR = ROOT / "eval"


def load_json(name):
    return json.loads((EVAL_DIR / name).read_text(encoding="utf-8"))


def seed_corpus(db, works) -> list[int]:
    """录入语料，返回新建作品的 id（用于事后精确清理）。"""
    created = []
    for item in works:
        work = crud.create_work(
            db,
            schemas.WorkCreate(
                title=item["title"],
                type=item["type"],
                content=item["content"],
                tags=item["tags"],
            ),
        )
        index_work(db, work)
        created.append(work.id)
    return created


def cleanup(db, ids: list[int]) -> None:
    for work_id in ids:
        vector_store.delete_work(work_id)
        crud.delete_work(db, work_id)


def run() -> int:
    db = SessionLocal()
    init_db()
    created_ids: list[int] = []

    try:
        corpus = load_json("corpus.json")["works"]
        cases = load_json("questions.json")["cases"]

        print(f"语料 {len(corpus)} 条 · 评测集 {len(cases)} 条")
        print("录入语料并建索引…")
        created_ids = seed_corpus(db, corpus)
        print(f"完成，Chroma 片段数 = {vector_store.count()}\n")

        title_to_id = {w.title: w.id for w in crud.list_works(db, skip=0, limit=200)}
        id_to_title = {v: k for k, v in title_to_id.items()}

        stats = {"事实型": [], "细节型": [], "多作品": [], "口语同义": [], "应拒绝": []}
        latencies: list[float] = []
        failures: list[tuple[str, str, str]] = []

        for case in cases:
            query = case["query"]
            expected_titles = case.get("expected") or []
            kind = case["kind"]

            t0 = time.perf_counter()
            hits = vector_store.query(embed_text(query), top_k=RETRIEVAL_TOP_K)
            latencies.append((time.perf_counter() - t0) * 1000)

            relevant = [h for h in hits if h["distance"] <= MAX_DISTANCE]
            got_titles = [id_to_title.get(h["work_id"], "?") for h in relevant]
            got_top5 = [id_to_title.get(h["work_id"], "?") for h in hits]

            if not expected_titles:
                ok = not relevant  # 应拒绝：过滤后应为空
            else:
                ok = any(title in got_top5 for title in expected_titles)

            stats[kind].append(ok)
            if not ok:
                failures.append((kind, query, "命中：" + "、".join(got_top5[:3])))

        total = sum(len(v) for v in stats.values())
        passed = sum(sum(v) for v in stats.values())

        print("=" * 60)
        print(f"{'类型':<8}{'通过':<8}{'准确率':<10}")
        for kind, results in stats.items():
            if not results:
                continue
            print(
                f"{kind:<8}{sum(results)}/{len(results):<6}{sum(results) / len(results):.0%}"
            )

        recall_cases = [ok for k, v in stats.items() if k != "应拒绝" for ok in v]
        refusal_cases = stats["应拒绝"]
        print("-" * 60)
        print(
            f"recall@5      = {sum(recall_cases) / len(recall_cases):.2f}  "
            f"({sum(recall_cases)}/{len(recall_cases)})"
        )
        print(
            f"拒绝准确率     = {sum(refusal_cases) / len(refusal_cases):.2f}  "
            f"({sum(refusal_cases)}/{len(refusal_cases)})"
        )
        print(
            f"检索平均耗时   = {sum(latencies) / len(latencies):.0f} ms  "
            f"(p95 = {sorted(latencies)[int(len(latencies) * 0.95) - 1]:.0f} ms)"
        )
        print(f"总计          = {passed}/{total}")

        if failures:
            print("\n未通过的用例：")
            for kind, query, detail in failures:
                print(f"  [{kind}] {query}")
                print(f"        {detail}")

        print("\n（以上基于 eval/corpus.json 的合成数据，仅用于验证链路与阈值）")
        return 0 if passed == total else 1

    finally:
        if created_ids:
            cleanup(db, created_ids)
            print(f"\n已清理评测语料（{len(created_ids)} 条），未触碰库内其他作品。")
        db.close()


if __name__ == "__main__":
    raise SystemExit(run())
