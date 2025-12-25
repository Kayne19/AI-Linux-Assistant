from geminiCaller import GeminiCaller
from local_caller import LocalCaller
from vectorDB import VectorDB


def main():
    vg = VectorDB()
    gc = LocalCaller()
    vg.ingest_data()
    
    print("\nRAG System Online (Type 'exit' to quit)")
    while True:
        user_query = input("\nCapCom > ")
        if user_query.lower() in ["exit", "quit"]:
            break
            
        context_block = vg.retrieve_context(user_query)
        print("Thinking...")
        response = gc.call_api(user_query, context_block)
        
        print("\n" + "="*60)
        print("RESPONSE:")
        print("="*60)
        print(response)

# --- MAIN LOOP ---
if __name__ == "__main__":
    main()

    
    