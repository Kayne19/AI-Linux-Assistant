from local_caller import LocalCaller
from gemini_caller import GeminiCaller
from context_agent import Contextualizer
from openAI_caller import OpenAICaller
from vectorDB import VectorDB
from classifier import Classifier
from dotenv import load_dotenv
from system_prompt import SYSTEM_PROMPT
# --- SETUP ---
class modelRouter:
    def __init__(self):
        self.conversation_history = []
        self.system_prompt_text = SYSTEM_PROMPT
        self.database = VectorDB()
        self.context_agent = Contextualizer()
        self.chatBot = OpenAICaller(self, self.system_prompt_text)
        self.classification_agent = Classifier()
        # self.vdb.ingest_data()

    def ask_question(self, user_question):
        relevant_documents = self.classification_agent.call_api(user_question, self.conversation_history)
        retrieval_query = self.context_agent.call_api(user_question, self.conversation_history)
        context_block = self.database.retrieve_context(retrieval_query, relevant_documents)
        return self.chatBot.call_api(user_question, context_block)

    def update_history(self, role, content):
        self.conversation_history.append((role, content))
    def get_history(self):
        return self.conversation_history
    def getDB(self):
        return self.database
