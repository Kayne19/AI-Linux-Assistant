from agents.memory_extractor import MemoryExtractor


class FakeWorker:
    def __init__(self, response_text):
        self.response_text = response_text

    def generate_text(self, *args, **kwargs):
        return self.response_text


class RecordingWorker:
    def __init__(self, response_text):
        self.response_text = response_text
        self.calls = []

    def generate_text(self, *args, **kwargs):
        self.calls.append(kwargs)
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


def test_memory_extractor_includes_recent_history_for_reference_resolution():
    worker = RecordingWorker(
        """
        {
          "facts": [],
          "issues": [],
          "attempts": [],
          "constraints": [
            {"constraint_key": "willingness", "constraint_value": "User does not want to do the requested action", "source_type": "user"}
          ],
          "preferences": [],
          "session_summary": ""
        }
        """
    )
    extractor = MemoryExtractor(worker=worker)

    extractor.call_api(
        "I don't wanna do that",
        "Okay, we can take a different approach.",
        recent_history=[
            ("assistant", "Please reboot the machine and tell me what changes."),
            ("user", "I don't wanna do that"),
            ("model", "Okay, we can take a different approach."),
        ],
    )

    assert worker.calls, "expected the extractor to call the worker"
    payload = worker.calls[0]["user_message"]
    assert "recent_history" in payload
    assert "Please reboot the machine and tell me what changes." in payload
    assert '"role": "assistant"' in payload


def test_memory_extractor_requests_native_structured_output():
    worker = RecordingWorker(
        """
        {
          "facts": [],
          "issues": [],
          "attempts": [],
          "constraints": [],
          "preferences": [],
          "session_summary": ""
        }
        """
    )
    extractor = MemoryExtractor(worker=worker)

    extractor.call_api("user said something", "assistant replied")

    assert worker.calls, "expected the extractor to call the worker"
    kwargs = worker.calls[0]
    assert kwargs["structured_output"] is True
    assert kwargs["output_schema"]["type"] == "object"
    assert "facts" in kwargs["output_schema"]["properties"]
