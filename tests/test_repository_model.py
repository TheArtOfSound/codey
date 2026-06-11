from codey.saas.models.repository import Repository


def test_repository_user_relationship_uses_selectin_loading() -> None:
    assert Repository.user.property.lazy == "selectin"
