"""Unit tests verifying systemd unit configuration and sudoers privileges."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_palworld_manager_service_sandbox_configuration() -> None:
    """Verify that palworld-manager.service configures correct filesystem sandboxing.

    Regression test for Issue #30: Sudoers drop-in harmonization and systemd sandbox read-write paths for Certbot.
    """
    service_file = REPO_ROOT / "scripts" / "palworld-manager.service"
    assert service_file.is_file(), f"Service unit file not found: {service_file}"

    content = service_file.read_text(encoding="utf-8")

    # ProtectSystem=full makes /etc read-only, which breaks certbot and updater
    assert "ProtectSystem=true" in content, "ProtectSystem should be set to true"
    assert "ProtectSystem=full" not in content, "ProtectSystem=full must not be set"

    # Verify essential ReadWritePaths
    assert "ReadWritePaths=" in content, "ReadWritePaths must be defined"
    rwp_line = next(line for line in content.splitlines() if line.startswith("ReadWritePaths="))
    required_paths = [
        "/var/lib/palmanager",
        "/opt/palworld-web-manager",
        "/etc/systemd/system",
    ]
    for p in required_paths:
        assert p in rwp_line, f"ReadWritePaths must include {p}"

    assert "/etc/sudoers.d" not in rwp_line, "ReadWritePaths must NOT include /etc/sudoers.d (superseded by supervisor)"
    assert "palworld-supervisor.service" in content, "Manager must declare dependency on supervisor"


def test_deploy_script_supervisor_configuration() -> None:
    """Verify that deploy.sh configures the supervisor provisioning and cleans up legacy sudoers.

    Regression test for Issue #30: Sudoers drop-in harmonization and systemd sandbox read-write paths for Certbot.
    """
    deploy_script = REPO_ROOT / "scripts" / "deploy.sh"
    assert deploy_script.is_file(), f"Deploy script not found: {deploy_script}"

    content = deploy_script.read_text(encoding="utf-8")
    assert (
        "/etc/sudoers.d/palmanager" not in content or "rm -f /etc/sudoers.d/palmanager" in content
    ), "Deploy must clean up legacy sudoers"
    assert "palworld-supervisor.service" in content, "Deploy must install supervisor service"
    assert "systemd-journal" in content, "Deploy must grant journal group access"
    assert "palworld-cert-manager.sh" not in content
    assert "systemctl restart palworld-manager.service" in content


def test_install_script_supervisor_configuration() -> None:
    """Verify that install.sh configures the supervisor provisioning and cleans up legacy sudoers.

    Regression test for Issue #30: Sudoers drop-in harmonization and systemd sandbox read-write paths for Certbot.
    """
    install_script = REPO_ROOT / "scripts" / "install.sh"
    assert install_script.is_file(), f"Install script not found: {install_script}"

    content = install_script.read_text(encoding="utf-8")
    assert (
        "/etc/sudoers.d/palmanager" not in content or "rm -f /etc/sudoers.d/palmanager" in content
    ), "Deploy must clean up legacy sudoers"
    assert "palworld-supervisor.service" in content, "Deploy must install supervisor service"
    assert "systemd-journal" in content, "Deploy must grant journal group access"
    assert "palworld-cert-manager.sh" not in content
    assert "systemctl restart palworld-manager.service" in content


def test_palworld_cert_renew_service_definition() -> None:
    """Verify that palworld-cert-renew.service invokes the native TLS engine."""
    service_file = REPO_ROOT / "scripts" / "palworld-cert-renew.service"
    assert service_file.is_file(), f"Service unit file not found: {service_file}"
    content = service_file.read_text(encoding="utf-8")
    assert "ExecStart=/opt/palworld-web-manager/.venv/bin/python -m app.engine.tls_manager renew" in content
    assert "User=palmanager" in content, "palworld-cert-renew.service must run as palmanager user"
    assert "Group=palmanager" in content, "palworld-cert-renew.service must run as palmanager group"


def test_palworld_supervisor_service_configuration() -> None:
    """Verify that palworld-supervisor.service runs as root with correct runtime directory."""
    service_file = REPO_ROOT / "scripts" / "palworld-supervisor.service"
    assert service_file.is_file(), f"Supervisor service unit not found: {service_file}"
    content = service_file.read_text(encoding="utf-8")
    assert "User=root" in content, "Supervisor must run as root"
    assert "Group=root" in content, "Supervisor must run as root group"
    assert "RuntimeDirectory=palmanager" in content, "Supervisor must manage /run/palmanager"
    assert "python -m app.supervisor.main" in content, "Supervisor must use correct entrypoint"
