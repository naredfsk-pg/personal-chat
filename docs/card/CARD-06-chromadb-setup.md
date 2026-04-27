# CARD-06 | ChromaDB Setup + Embeddings

**Phase:** 2 — Memory  
**Priority:** P0  
**Estimated effort:** 1 day  
**Depends on:** CARD-03  
**Blocks:** CARD-08, CARD-09

---

## Goal

ตั้ง vector store ด้วย ChromaDB สำหรับ long-term semantic memory — upsert ข้อความแล้วค้นหาด้วยความหมายได้ ใช้ `text-embedding-004` ของ Google

---

## Tasks

### 1. ติดตั้ง ChromaDB

```
chromadb>=0.5
```

ข้อมูลจะเก็บใน `./data/chroma/` (persistent mode)

### 2. เขียน `VectorStore` class

```python
# src/infrastructure/vector_store/chroma_store.py
from dataclasses import dataclass
import chromadb
from chromadb.config import Settings

@dataclass(frozen=True)
class SearchResult:
    id: str
    content: str
    score: float            # 0.0 = identical, 2.0 = opposite (cosine distance)
    metadata: dict

class VectorStore:
    def __init__(self, persist_dir: str, collection_name: str = "memories") -> None:
        self._client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    async def upsert(
        self,
        doc_id: str,
        content: str,
        embedding: list[float],
        metadata: dict | None = None,
    ) -> None:
        """บันทึกหรืออัปเดต document พร้อม embedding."""
        self._collection.upsert(
            ids=[doc_id],
            documents=[content],
            embeddings=[embedding],
            metadatas=[metadata or {}],
        )

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        where: dict | None = None,
    ) -> list[SearchResult]:
        """ค้นหา documents ที่ใกล้เคียง query embedding มากที่สุด."""
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
            include=["documents", "distances", "metadatas"],
        )
        return [
            SearchResult(
                id=rid,
                content=doc,
                score=dist,
                metadata=meta,
            )
            for rid, doc, dist, meta in zip(
                results["ids"][0],
                results["documents"][0],
                results["distances"][0],
                results["metadatas"][0],
            )
        ]

    async def delete(self, doc_id: str) -> None:
        self._collection.delete(ids=[doc_id])

    def count(self) -> int:
        return self._collection.count()
```

### 3. เชื่อม GeminiClient กับ VectorStore

สร้าง `MemoryService` ที่ wrap ทั้งสอง เพื่อไม่ให้ handler ต้องรู้ว่าใช้ embeddings ยังไง:

```python
# src/core/memory/memory_service.py
import uuid
from src.infrastructure.gemini.client import GeminiClient
from src.infrastructure.vector_store.chroma_store import VectorStore, SearchResult

class MemoryService:
    def __init__(self, gemini: GeminiClient, store: VectorStore) -> None:
        self._gemini = gemini
        self._store = store

    async def remember(self, user_id: int, content: str) -> str:
        """Embed แล้ว upsert — คืน doc_id."""
        doc_id = str(uuid.uuid4())
        embedding = await self._gemini.embed_text(content)
        await self._store.upsert(
            doc_id=doc_id,
            content=content,
            embedding=embedding,
            metadata={"user_id": user_id},
        )
        return doc_id

    async def recall(self, user_id: int, query: str, top_k: int = 5) -> list[SearchResult]:
        """Embed query แล้ว search — คืนเฉพาะ memories ของ user นั้น."""
        embedding = await self._gemini.embed_text(query)
        return await self._store.search(
            query_embedding=embedding,
            top_k=top_k,
            where={"user_id": user_id},  # filter เฉพาะ user นั้น
        )
```

### 4. ตั้งค่า path ใน Config

```python
# src/bot/config.py
chroma_persist_dir: str = "./data/chroma"
```

### 5. Register ใน `main.py`

```python
vector_store = VectorStore(persist_dir=config.chroma_persist_dir)
memory_service = MemoryService(gemini=gemini, store=vector_store)
dp["memory_service"] = memory_service
```

---

## Integration Tests

```python
# tests/infrastructure/test_chroma_store.py

async def test_upsert_and_search_returns_correct_document():
    store = VectorStore(persist_dir=":memory:")   # in-memory mode สำหรับ test
    embedding = [0.1] * 768
    await store.upsert("doc1", "Python programming", embedding, {"user_id": 1})
    
    results = await store.search(embedding, top_k=1)
    assert results[0].id == "doc1"
    assert results[0].content == "Python programming"

async def test_search_filters_by_user_id():
    store = VectorStore(persist_dir=":memory:")
    embedding = [0.1] * 768
    await store.upsert("doc1", "user1 note", embedding, {"user_id": 1})
    await store.upsert("doc2", "user2 note", embedding, {"user_id": 2})
    
    results = await store.search(embedding, top_k=5, where={"user_id": 1})
    assert all(r.id != "doc2" for r in results)

async def test_upsert_overwrites_existing_doc():
    store = VectorStore(persist_dir=":memory:")
    embedding = [0.1] * 768
    await store.upsert("doc1", "original", embedding)
    await store.upsert("doc1", "updated", embedding)
    
    results = await store.search(embedding, top_k=1)
    assert results[0].content == "updated"
    assert store.count() == 1   # ไม่เพิ่ม

async def test_delete_removes_document():
    store = VectorStore(persist_dir=":memory:")
    embedding = [0.1] * 768
    await store.upsert("doc1", "to delete", embedding)
    await store.delete("doc1")
    assert store.count() == 0

async def test_search_latency_under_100ms():
    # upsert 1000 documents แล้วจับเวลา search
    import time
    store = VectorStore(persist_dir=":memory:")
    for i in range(1000):
        await store.upsert(str(i), f"document {i}", [0.1] * 768, {"user_id": 1})
    
    start = time.perf_counter()
    await store.search([0.1] * 768, top_k=5)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 100
```

---

## Definition of Done

- [ ] `upsert()` บันทึก document ลง disk ได้ (persistent)
- [ ] `search()` คืนผลที่ filter เฉพาะ `user_id` นั้น
- [ ] search latency < 100ms กับ 1,000 documents
- [ ] `upsert()` ซ้ำ doc_id เดิม → update ไม่ duplicate
- [ ] integration tests ทุกกรณีผ่าน

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| `persist_dir` ไม่มีอยู่ | ChromaDB สร้างให้อัตโนมัติ |
| Collection ว่าง แล้ว search | คืน `[]` ไม่ raise |
| `top_k` มากกว่าจำนวน documents | คืนเท่าที่มี ไม่ error |
| Embedding vector มี dimension ผิด | ChromaDB raise error ตอน upsert → catch แล้ว log |
| Disk เต็ม | ChromaDB raise IOError → catch แล้ว alert user |
