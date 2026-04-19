from pathlib import Path
from ingestion.identity.sidecar import load_sidecar, sidecar_path


def _stub_pdf(tmp_path: Path, name: str = "doc.pdf") -> Path:
    p = tmp_path / name
    p.write_bytes(b"")
    return p


def test_load_sidecar_returns_dict_for_valid_yaml(tmp_path):
    pdf = _stub_pdf(tmp_path)
    sidecar = tmp_path / "doc.meta.yaml"
    sidecar.write_text("canonical_title: My Doc\nversion: '8.0'\n", encoding="utf-8")
    result = load_sidecar(pdf)
    assert isinstance(result, dict)
    assert result["canonical_title"] == "My Doc"
    assert result["version"] == "8.0"


def test_load_sidecar_returns_none_when_no_sidecar(tmp_path):
    pdf = _stub_pdf(tmp_path)
    assert load_sidecar(pdf) is None


def test_load_sidecar_returns_none_for_corrupt_yaml(tmp_path):
    pdf = _stub_pdf(tmp_path)
    sidecar = tmp_path / "doc.meta.yaml"
    sidecar.write_bytes(b"\x00\xff\xfe invalid: [unclosed")
    assert load_sidecar(pdf) is None


def test_load_sidecar_returns_none_for_yaml_list(tmp_path):
    pdf = _stub_pdf(tmp_path)
    sidecar = tmp_path / "doc.meta.yaml"
    sidecar.write_text("- item1\n- item2\n", encoding="utf-8")
    assert load_sidecar(pdf) is None


def test_load_sidecar_returns_none_for_yaml_scalar(tmp_path):
    pdf = _stub_pdf(tmp_path)
    sidecar = tmp_path / "doc.meta.yaml"
    sidecar.write_text("just a string\n", encoding="utf-8")
    assert load_sidecar(pdf) is None


def test_load_sidecar_prefers_yaml_over_yml(tmp_path):
    pdf = _stub_pdf(tmp_path)
    yaml_file = tmp_path / "doc.meta.yaml"
    yml_file = tmp_path / "doc.meta.yml"
    yaml_file.write_text("source: yaml\n", encoding="utf-8")
    yml_file.write_text("source: yml\n", encoding="utf-8")
    result = load_sidecar(pdf)
    assert result is not None
    assert result["source"] == "yaml"


def test_load_sidecar_falls_back_to_yml(tmp_path):
    pdf = _stub_pdf(tmp_path)
    yml_file = tmp_path / "doc.meta.yml"
    yml_file.write_text("source: yml\n", encoding="utf-8")
    result = load_sidecar(pdf)
    assert result is not None
    assert result["source"] == "yml"


def test_sidecar_path_returns_yaml_path_when_exists(tmp_path):
    pdf = _stub_pdf(tmp_path)
    sidecar = tmp_path / "doc.meta.yaml"
    sidecar.write_text("x: 1\n", encoding="utf-8")
    path = sidecar_path(pdf)
    assert path == sidecar


def test_sidecar_path_returns_none_when_no_sidecar(tmp_path):
    pdf = _stub_pdf(tmp_path)
    assert sidecar_path(pdf) is None


def test_sidecar_path_consistent_with_load_sidecar(tmp_path):
    pdf = _stub_pdf(tmp_path)
    sidecar = tmp_path / "doc.meta.yaml"
    sidecar.write_text("x: 1\n", encoding="utf-8")
    assert sidecar_path(pdf) is not None
    assert load_sidecar(pdf) is not None

    sidecar.unlink()
    assert sidecar_path(pdf) is None
    assert load_sidecar(pdf) is None
