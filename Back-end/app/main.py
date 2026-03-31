from orchestration.session_bootstrap import bootstrap_interactive_session


def main():
    session_info = bootstrap_interactive_session()
    router = session_info.build_router()

    print(
        "\n"
        f"User: {session_info.username}  |  "
        f"Project: {session_info.project_name}  |  "
        f"Chat: {session_info.chat_session_title or session_info.chat_session_id}"
    )
    print("(Type 'exit' to quit)")
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

    
    
