from memory_extractor import MemoryExtractor


class FakeWorker:
    def __init__(self, response_text):
        self.response_text = response_text

    def generate_text(self, *args, **kwargs):
        return self.response_text


def test_memory_extractor_returns_empty_result_on_invalid_json():
    extractor = MemoryExtractor(worker=FakeWorker("not json"))

    result = extractor.call_api("user said something", "assistant replied")

    assert result == {
        "facts": [],
        "issues": [],
        "attempts": [],
        "constraints": [],
        "preferences": [],
        "session_summary": "",
    }


def test_memory_extractor_normalizes_structured_output():
    extractor = MemoryExtractor(
        worker=FakeWorker(
            """
            {
              "facts": [
                {"fact_key": "OS Distribution", "fact_value": "Debian 12", "confidence": 1.4},
                {"fact_key": "OS Distribution", "fact_value": "ignored duplicate"}
              ],
              "issues": [
                {"title": "Docker permission denied", "category": "Containers", "summary": "permission denied on docker.sock", "status": "OPEN"}
              ],
              "attempts": [
                {"action": "restarted docker", "command": "systemctl restart docker", "outcome": "did not help", "status": "FAILED", "issue_title": "Docker permission denied"}
              ],
              "constraints": [
                {"constraint_key": "Remote Access", "constraint_value": "User is connected over SSH"}
              ],
              "preferences": [
                {"preference_key": "Editor", "preference_value": "Prefer vim"}
              ],
              "session_summary": "Docker socket issue under investigation."
            }
            """
        )
    )

    result = extractor.call_api("question", "answer")

    assert result["facts"] == [
        {
            "fact_key": "os_distribution",
            "fact_value": "Debian 12",
            "source_type": "user",
            "source_ref": "user_question",
            "confidence": 1.0,
            "verified": False,
        }
    ]
    assert result["issues"][0]["category"] == "containers"
    assert result["issues"][0]["status"] == "open"
    assert result["attempts"][0]["status"] == "failed"
    assert result["attempts"][0]["source_type"] == "user"
    assert result["constraints"][0]["constraint_key"] == "remote_access"
    assert result["constraints"][0]["source_type"] == "user"
    assert result["preferences"][0]["preference_key"] == "editor"
    assert result["preferences"][0]["source_type"] == "user"
    assert result["session_summary"] == "Docker socket issue under investigation."
