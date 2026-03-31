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
