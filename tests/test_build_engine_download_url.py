from uuid import uuid4

from codey.saas.build_mode.engine import _download_endpoint


def test_build_engine_download_endpoint_uses_public_route() -> None:
    project_id = uuid4()

    assert _download_endpoint(project_id) == f"/build/{project_id}/download/zip"
