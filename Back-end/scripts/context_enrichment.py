import json

# ------------------------------------------------------------------
# Pipeline overview:
# extracted_clean.json + doc_context.txt -> extracted_clean_final.json
# Adds ai_context + embedding_text used by VectorDB ingest.
# ------------------------------------------------------------------
import ollama
from tqdm import tqdm

def enrich_elements(json_path, context_text_path, model="mannix/llama3.1-8b-abliterated"):
    # Enrichment stage: adds ai_context + embedding_text to each element.
    
    # 1. Load Data (cleaned elements + full document text).
    with open(json_path, "r", encoding="utf-8") as f:
        elements = json.load(f)
        
    with open(context_text_path, "r", encoding="utf-8") as f:
        full_doc_context = f.read()

    print(f"📂 Loaded {len(elements)} elements.")
    
    # 2. Filter Candidates (Skip tiny text or headers to save time).
    # We process references to the list, so 'elements' gets updated in place
    target_types = ["NarrativeText", "ListItem", "UncategorizedText"]
    candidates = [
        el for el in elements 
        if el.get("type") in target_types and len(el.get("text", "")) > 50
    ]
    
    print(f"🧠 AI Enrichment: Processing {len(candidates)} valid text chunks...")

    # 3. Define System Prompt (Cached per run).
    system_msg = f"""
    <document_context>
    {full_doc_context[:25000]} 
    ... (truncated)
    </document_context>
    
    You are a retrieval optimizer. Your job is to situate chunks within the document context above.
    """

    # 4. Loop (in-place updates to elements list).
    for i, element in enumerate(tqdm(candidates, colour="green")):
        text_content = element.get("text", "")
        
        user_msg = f"""
        <chunk>
        {text_content}
        </chunk>
        
        Give a short, succinct sentence situating this chunk within the document context.
        """
        
        try:
            response = ollama.chat(
                model=model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg}
                ],
                options={
                    "temperature": 0.1,
                    "num_ctx": 8192, # Safe for 8B models
                    "num_predict": 60 
                }
            )
            
            context = response['message']['content'].strip()
            
            # 5. WRITE TO ELEMENT (this is what VectorDB ingests via embedding_text).
            if "metadata" not in element:
                element["metadata"] = {}
                
            element["metadata"]["ai_context"] = context
            # Prepare the final string for your Vector DB
            element["metadata"]["embedding_text"] = f"CONTEXT: {context}\n\nCONTENT: {text_content}"
            
            # OPTIONAL: Save checkpoint every 100 items.
            if i > 0 and i % 100 == 0:
                with open(json_path.replace(".json", "_enriched_partial.json"), "w") as f:
                    json.dump(elements, f)

        except KeyboardInterrupt:
            print("\n🛑 Stopping early, saving progress...")
            break
        except Exception as e:
            print(f"⚠️ Error on item {i}: {e}")
            continue

    # 6. Final Save (extracted_clean_final.json).
    final_path = json_path.replace(".json", "_final.json")
    with open(final_path, "w", encoding="utf-8") as f:
        json.dump(elements, f, indent=2)
    
    print(f"✅ Finished! Saved to {final_path}")

if __name__ == "__main__":
    # Entry point: extracted_clean.json + doc_context.txt -> extracted_clean_final.json
    enrich_elements(
        json_path="extracted_clean.json",
        context_text_path="doc_context.txt"
    )
