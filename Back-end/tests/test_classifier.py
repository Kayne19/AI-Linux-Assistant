from agents.classifier import Classifier


def test_classifier_parse_labels_supports_legacy_wire_format():
    classifier = Classifier(worker=object())

    labels = classifier._parse_labels("labels=proxmox|debian,conf=0.85")

    assert labels == ["proxmox", "debian"]


def test_classifier_parse_labels_supports_json_wire_format():
    classifier = Classifier(worker=object())

    labels = classifier._parse_labels('{"labels":["docker","unknown","general"],"conf":0.8}')

    assert labels == ["docker", "general"]
