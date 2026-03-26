from model_router import ModelRouter


def main():
    router = ModelRouter()
    
    print("\n(Type 'exit' to quit)")
    while True:
        user_query = input("\nUser > ")
        if user_query.lower() in ["exit", "quit"]:
            break
            
        print("Thinking...")
        response = router.ask_question(user_query)
        
        print("\n" + "="*60)
        print("RESPONSE:")
        print("="*60)
        print(response)

if __name__ == "__main__":
    main()

    
    
