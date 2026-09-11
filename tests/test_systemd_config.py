"""Unit tests verifying systemd unit configuration and sudoers privileges."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_palworld_manager_service_sandbox_configuration() -> None:
    """Verifies that palworld-manager.service configures correct filesystem sandboxing."""
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
        "/etc/letsencrypt",
        "/var/log/letsencrypt",
        "/etc/sudoers.d",
        "/etc/systemd/system",
    ]
    for p in required_paths:
        assert p in rwp_line, f"ReadWritePaths must include {p}"


def test_deploy_script_sudoers_configuration() -> None:
    """Verifies that deploy.sh configures the unified /etc/sudoers.d/palmanager drop-in."""
    deploy_script = REPO_ROOT / "scripts" / "deploy.sh"
    assert deploy_script.is_file(), f"Deploy script not found: {deploy_script}"

    content = deploy_script.read_text(encoding="utf-8")
    assert "/etc/sudoers.d/palmanager" in content
    assert "deploy.sh *" in content
    assert "palworld-cert-manager.sh *" in content


def test_install_script_sudoers_configuration() -> None:
    """Verifies that install.sh configures the unified /etc/sudoers.d/palmanager drop-in."""
    install_script = REPO_ROOT / "scripts" / "install.sh"
    assert install_script.is_file(), f"Install script not found: {install_script}"

    content = install_script.read_text(encoding="utf-8")
    assert "/etc/sudoers.d/palmanager" in content
    assert "deploy.sh *" in content
    assert "palworld-cert-manager.sh *" in content
