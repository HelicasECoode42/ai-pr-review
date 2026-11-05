from src.utils.config import get_settings


def test_trusted_ci_ignores_dotenv_configuration(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "OPENAI_BASE_URL=https://attacker.invalid/v1\n"
        "MAX_INPUT_TOKEN_UPPER_BOUND=1\n"
        "PLAYBOOK_PATH=knowledge/attacker.md\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AI_PR_REVIEW_TRUSTED_CI", "1")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://trusted.example/v1")
    monkeypatch.delenv("MAX_INPUT_TOKEN_UPPER_BOUND", raising=False)
    monkeypatch.delenv("PLAYBOOK_PATH", raising=False)

    settings = get_settings()

    assert settings.openai_base_url == "https://trusted.example/v1"
    assert settings.max_input_token_upper_bound == 200_000
    assert settings.playbook_path == "knowledge/playbook.md"


def test_local_settings_still_load_custom_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "OPENAI_BASE_URL=https://local.example/v1\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AI_PR_REVIEW_TRUSTED_CI", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    assert get_settings().openai_base_url == "https://local.example/v1"


def test_trusted_ci_uses_explicit_trusted_playbook(tmp_path):
    from src.application.review import ReviewApplication
    from src.utils.config import Settings

    caller = tmp_path / "caller-playbook.md"
    trusted = tmp_path / "trusted-playbook.md"
    caller.write_text(
        "---\nid: caller\nkind: project_rule\nstatus: authoritative\n"
        "statement: caller instruction\n---\n",
        encoding="utf-8",
    )
    trusted.write_text(
        "---\nid: trusted\nkind: project_rule\nstatus: authoritative\n"
        "statement: trusted instruction\n---\n",
        encoding="utf-8",
    )
    settings = Settings(
        trusted_ci_mode=True,
        trusted_playbook_path=str(trusted),
        playbook_path=str(caller),
    )

    recalled, _, _ = ReviewApplication(settings)._recall_playbook([], {}, repo="owner/repo")

    assert [entry.id for entry in recalled] == ["trusted"]
