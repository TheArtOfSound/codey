from __future__ import annotations

import logging

import codey.nfet.config as nfet_config
from codey.nfet.config import NFETConfig, load_config, validate_config


def test_load_config_falls_back_for_non_object_root(tmp_path) -> None:
    config_path = tmp_path / "codey.config.json"
    config_path.write_text('["not", "an", "object"]', encoding="utf-8")

    config = load_config(tmp_path)

    assert config.alpha == 1.0
    assert config.auto_sweep_interval_minutes == 30
    assert config.sweep_credit_cost == 0


def test_load_config_parses_string_boolean_overrides(tmp_path) -> None:
    config_path = tmp_path / "codey.config.json"
    config_path.write_text(
        """
        {
          "impact_phase_change_alert": "false",
          "critical_phase_block_deploy": "true",
          "auto_sweep_on_commit": "0"
        }
        """,
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.impact_phase_change_alert is False
    assert config.critical_phase_block_deploy is True
    assert config.auto_sweep_on_commit is False


def test_load_config_rejects_non_finite_numeric_overrides(tmp_path) -> None:
    config_path = tmp_path / "codey.config.json"
    config_path.write_text(
        """
        {
          "alpha": NaN,
          "beta": Infinity,
          "sigma_star": "-Infinity",
          "auto_sweep_interval_minutes": Infinity,
          "critical_phase_block_deploy": NaN
        }
        """,
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.alpha == 1.0
    assert config.beta == 2.0
    assert config.sigma_star == 0.5
    assert config.auto_sweep_interval_minutes == 30
    assert config.critical_phase_block_deploy is False


def test_load_config_falls_back_for_oversized_config(
    monkeypatch,
    caplog,
    tmp_path,
) -> None:
    config_path = tmp_path / "codey.config.json"
    config_path.write_text('{"alpha": 2.0}', encoding="utf-8")
    monkeypatch.setattr(nfet_config, "_MAX_CONFIG_CHARS", 8)
    caplog.set_level(logging.WARNING, logger="codey.nfet.config")

    config = load_config(tmp_path)

    assert config == NFETConfig()
    assert "config file is too large" in caplog.text


def test_load_config_redacts_parse_failure_logs(monkeypatch, caplog) -> None:
    class _UnreadableConfig:
        def read_text(self, *, encoding: str) -> str:
            raise OSError(
                "open failed https://user:secret@example.test/config?access_token=access123 "
                "api_key=key123 auth_token=auth123 refresh_token=refresh123 "
                "client_secret=client123 password=pw123 "
                "mirror=config#client_secret=fragment123 "
                "authorization=Bearer bearer123 for operator@example.test"
            )

        def __str__(self) -> str:
            return "https://path-user:path-secret@example.test/codey.config.json?token=path-token"

    monkeypatch.setattr(
        nfet_config,
        "_find_config",
        lambda _project_root: _UnreadableConfig(),
    )
    caplog.set_level(logging.WARNING, logger="codey.nfet.config")

    config = load_config("ignored")

    assert config == NFETConfig()
    assert "path-user:path-secret" not in caplog.text
    assert "path-token" not in caplog.text
    assert "user:secret" not in caplog.text
    assert "secret@example.test" not in caplog.text
    assert "access123" not in caplog.text
    assert "key123" not in caplog.text
    assert "auth123" not in caplog.text
    assert "refresh123" not in caplog.text
    assert "client123" not in caplog.text
    assert "fragment123" not in caplog.text
    assert "pw123" not in caplog.text
    assert "bearer123" not in caplog.text
    assert "operator@example.test" not in caplog.text
    assert "https://***@example.test/codey.config.json?token=***" in caplog.text
    assert "https://***@example.test/config?access_token=***" in caplog.text
    assert "api_key=***" in caplog.text
    assert "auth_token=***" in caplog.text
    assert "refresh_token=***" in caplog.text
    assert "client_secret=***" in caplog.text
    assert "password=***" in caplog.text
    assert "authorization=Bearer ***" in caplog.text
    assert "***@example.test" in caplog.text


def test_load_config_clamps_thresholds_to_valid_order(tmp_path) -> None:
    config_path = tmp_path / "codey.config.json"
    config_path.write_text(
        """
        {
          "ridge_threshold": -1,
          "caution_threshold": -1
        }
        """,
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.ridge_threshold == 0.02
    assert config.caution_threshold == 0.01
    assert config.caution_threshold < config.ridge_threshold


def test_validate_config_rejects_non_finite_numeric_values() -> None:
    config = NFETConfig(
        alpha=float("nan"),
        beta=float("inf"),
        auto_sweep_interval_minutes=float("nan"),  # type: ignore[arg-type]
        history_retention_days=float("inf"),  # type: ignore[arg-type]
        sweep_credit_cost=float("nan"),  # type: ignore[arg-type]
    )

    errors = validate_config(config)

    assert any(error.startswith("alpha must be positive") for error in errors)
    assert any(error.startswith("beta must be positive") for error in errors)
    assert any(
        error.startswith("auto_sweep_interval_minutes must be >= 1")
        for error in errors
    )
    assert any(
        error.startswith("history_retention_days must be >= 1")
        for error in errors
    )
    assert any(error.startswith("sweep_credit_cost must be >= 0") for error in errors)


def test_is_finite_number_rejects_float_overflow() -> None:
    assert nfet_config._is_finite_number(10**10000) is False
