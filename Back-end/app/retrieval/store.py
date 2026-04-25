import lancedb


class LanceDBStore:
    def __init__(self, db_path: str, table_name: str):
        self.db_path = db_path
        self.table_name = table_name

    def connect(self):
        return lancedb.connect(self.db_path)

    def table_exists(self) -> bool:
        return self.table_name in self.connect().table_names()

    def open_table(self):
        return self.connect().open_table(self.table_name)

    def search_hybrid(self, query_vector, query_text: str, limit: int):
        return (
            self.open_table()
            .search(query_type="hybrid")
            .vector(query_vector)
            .text(query_text)
            .limit(limit)
            .to_list()
        )

    def search_hybrid_scoped(
        self,
        query_vector,
        query_text: str,
        limit: int,
        canonical_source_ids: list[str],
    ):
        """Hybrid search narrowed to chunks whose canonical_source_id is in *canonical_source_ids*.

        An empty or None ``canonical_source_ids`` is treated as "no filter" and
        delegates to :meth:`search_hybrid`. SQL injection is avoided by
        escaping single quotes in each ID.
        """
        if not canonical_source_ids:
            return self.search_hybrid(query_vector, query_text, limit)
        escaped = [f"'{str(doc_id).replace(chr(39), chr(39) + chr(39))}'" for doc_id in canonical_source_ids]
        predicate = f"canonical_source_id IN ({', '.join(escaped)})"
        return (
            self.open_table()
            .search(query_type="hybrid")
            .vector(query_vector)
            .text(query_text)
            .where(predicate)
            .limit(limit)
            .to_list()
        )

    def load_documents(self):
        """Return every row from the (already open) documents table as a list of dicts.

        ``self.table_name`` must point at the documents table for this to be
        meaningful. Used by the scope selector.
        """
        return self.open_table().to_pandas().to_dict("records")

    def fetch_source_page_window(self, source: str, page_start: int, page_end: int, limit: int | None = None):
        escaped_source = (source or "").replace("'", "''")
        query = (
            f"source = '{escaped_source}' "
            f"AND page >= {int(page_start)} "
            f"AND page <= {int(page_end)}"
        )
        search = self.open_table().search().where(query)
        search = search.limit(10000 if limit is None else int(limit))
        return search.to_list()

    def fetch_canonical_page_window(
        self,
        canonical_source_id: str,
        page_start: int,
        page_end: int,
        limit: int | None = None,
    ):
        escaped_source_id = (canonical_source_id or "").replace("'", "''")
        query = (
            f"canonical_source_id = '{escaped_source_id}' "
            f"AND page_start <= {int(page_end)} "
            f"AND page_end >= {int(page_start)}"
        )
        search = self.open_table().search().where(query)
        search = search.limit(10000 if limit is None else int(limit))
        return search.to_list()

    def add_rows(self, rows):
        db = self.connect()
        if self.table_exists():
            table = db.open_table(self.table_name)
            table.add(rows)
            return False
        db.create_table(self.table_name, data=rows)
        return True

    def rebuild_fts_index(self, field_name: str = "search_text"):
        self.open_table().create_fts_index(field_name, replace=True)

    def sample_rows(self, limit: int):
        return self.open_table().to_pandas().head(limit)
