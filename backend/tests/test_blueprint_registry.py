from pathlib import Path


def test_api_registry_does_not_require_removed_docs_package():
    source = (Path(__file__).resolve().parents[1] / 'app/api/__init__.py').read_text(encoding='utf-8')
    assert 'from docs.' not in source
    assert 'docs_bp' not in source
