import json
import os
import chromadb
from chromadb.utils import embedding_functions

class VectorDB:
    def __init__(self):
        self.JSON_PATH = "extracted_clean_final.json"
        self.DB_PATH = "chroma_db"
        self.COLLECTION_NAME = "RAG_Context"
        self.EMBED_MODEL = "all-MiniLM-L6-v2"
        self.CONTEXT_WINDOW = 32

    def get_db(self):
        # Initializes the Vector Database
        client = chromadb.PersistentClient(path=self.DB_PATH)
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=self.EMBED_MODEL,
            device="cuda" 
        )
        return client.get_or_create_collection(name=self.COLLECTION_NAME, embedding_function=ef)

    def ingest_data(self):
        
        if not os.path.exists(self.JSON_PATH):
            print(f"Error: {self.JSON_PATH} not found.")
            return

        print(f"INGESTING: {self.JSON_PATH}")
        collection = self.get_db()
        
        with open(self.JSON_PATH, 'r') as f:
            data = json.load(f)
        
        docs, metas, ids = [], [], []
        
        # Check if DB is already populated
        # UPDATE THIS LATER WHEN WE ADD MORE DOCUMENTS
        if collection.count() > 0:
            print(f"Collection already has {collection.count()} items. Skipping ingest.")
            return

        print(f"   - Processing {len(data)} elements...")
        
        for idx, el in enumerate(data):
            text = el.get("text", "").strip()

            docs.append(text)
            meta = el.get("metadata", {})
            meta_flat = {
                "page": meta.get("page_number", 0),
                "source": meta.get("filename", "Unknown"),
                "type": el.get("type", "Text")
            }
            metas.append(meta_flat)
            ids.append(f"vec_{idx}")

        batch_size = 5000
        for i in range(0, len(docs), batch_size):
            print(f"   - Writing batch {i} to {i+batch_size}...")
            collection.add(
                documents=docs[i:i+batch_size],
                metadatas=metas[i:i+batch_size],
                ids=ids[i:i+batch_size]
            )
        print(f"SAVED {len(docs)} VECTORS TO DISK.")


    def retrieve_context(self, query):
        collection = self.get_db()
        print(f"\nSearching manual for: '{query}'...")
        
        results = collection.query(
            query_texts=[query],
            n_results=self.CONTEXT_WINDOW
        )
        
        context_text = ""
        sources = []
        
        if results['documents']:
            for i in range(len(results['documents'][0])):
                doc = results['documents'][0][i]
                meta = results['metadatas'][0][i]
                page = meta.get('page', '?')
                
                context_text += f"---\n[Source: Page {page}]\n{doc}\n"
                sources.append(str(page))
                
        print(f"   (Found context on pages: {', '.join(sources)})")
        return context_text