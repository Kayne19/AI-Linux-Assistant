from agents.memory_resolver import MemoryResolver


def test_memory_resolver_commits_high_confidence_user_fact_and_keeps_assistant_fact_as_candidate():
    resolver = MemoryResolver()
    extracted = {
        "facts": [
            {
                "fact_key": "os.distribution",
                "fact_value": "Debian",
                "source_type": "user",
                "source_ref": "conversation",
                "confidence": 0.95,
            },
            {
                "fact_key": "shell.default",
                "fact_value": "zsh",
                "source_type": "assistant",
                "source_ref": "conversation",
                "confidence": 0.99,
            },
        ],
        "issues": [],
        "attempts": [],
        "constraints": [],
        "preferences": [],
        "session_summary": "",
    }

    resolution = resolver.resolve(extracted, snapshot={"profile": {}, "issues": [], "attempts": [], "constraints": [], "preferences": []})

    assert resolution.committed["facts"] == [
        {
            "fact_key": "os.distribution",
            "fact_value": "Debian",
            "source_type": "user",
            "source_ref": "conversation",
            "confidence": 0.95,
        }
    ]
    assert resolution.candidates[0]["item_type"] == "fact"
    assert resolution.candidates[0]["reason"] == "non_user_source"


def test_memory_resolver_marks_conflicting_non_mutable_fact_without_committing():
    resolver = MemoryResolver()
    extracted = {
        "facts": [
            {
                "fact_key": "hardware.cpu_model",
                "fact_value": "Ryzen 7",
                "source_type": "user",
                "source_ref": "conversation",
                "confidence": 0.97,
            }
        ],
        "issues": [],
        "attempts": [],
        "constraints": [],
        "preferences": [],
        "session_summary": "",
    }

    resolution = resolver.resolve(extracted, snapshot={"profile": {"hardware.cpu_model": "Core i7"}})

    assert resolution.committed["facts"] == []
    assert resolution.conflicts[0]["item_type"] == "fact"
    assert resolution.conflicts[0]["status"] == "conflicted"


def test_memory_resolver_supersedes_mutable_user_fact_and_records_audit_entry():
    resolver = MemoryResolver()
    extracted = {
        "facts": [
            {
                "fact_key": "os.distribution",
                "fact_value": "Ubuntu 24.04",
                "source_type": "user",
                "source_ref": "user_question",
                "confidence": 0.97,
            }
        ],
        "issues": [],
        "attempts": [],
        "constraints": [],
        "preferences": [],
        "session_summary": "",
    }

    resolution = resolver.resolve(extracted, snapshot={"profile": {"os.distribution": "Debian 12"}})

    assert resolution.committed["facts"] == [
        {
            "fact_key": "os.distribution",
            "fact_value": "Ubuntu 24.04",
            "source_type": "user",
            "source_ref": "user_question",
            "confidence": 0.97,
        }
    ]
    assert resolution.conflicts[0]["item_type"] == "fact"
    assert resolution.conflicts[0]["status"] == "superseded"
    assert resolution.conflicts[0]["reason"] == "superseded_by_user_update:Ubuntu 24.04"


def test_memory_resolver_commits_explicit_user_attempt_and_preference():
    resolver = MemoryResolver()
    extracted = {
        "facts": [],
        "issues": [],
        "attempts": [
            {
                "action": "edited config",
                "command": "vim ~/.zshrc",
                "outcome": "did not help",
                "status": "failed",
                "source_type": "user",
                "source_ref": "user_question",
                "confidence": 0.82,
            }
        ],
        "constraints": [],
        "preferences": [
            {
                "preference_key": "editor",
                "preference_value": "vim",
                "source_type": "user",
                "source_ref": "user_question",
                "confidence": 0.88,
            }
        ],
        "session_summary": "",
    }

    resolution = resolver.resolve(
        extracted,
        snapshot={"profile": {}, "issues": [], "attempts": [], "constraints": [], "preferences": []},
    )

    assert len(resolution.committed["attempts"]) == 1
    assert resolution.committed["attempts"][0]["command"] == "vim ~/.zshrc"
    assert len(resolution.committed["preferences"]) == 1
    assert resolution.committed["preferences"][0]["preference_value"] == "vim"
