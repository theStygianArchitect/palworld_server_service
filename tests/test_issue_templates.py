"""Automated verification test for GitHub Issue Forms and Community Config."""

import glob
import os

import yaml


def test_issue_templates_exist_and_are_valid_yaml():
    """Verify that all issue templates are valid YAML and follow GitHub Issue Forms schema."""
    template_dir = os.path.join(".github", "ISSUE_TEMPLATE")
    assert os.path.isdir(template_dir), f"Directory {template_dir} does not exist"

    # Legacy markdown templates must not exist
    md_templates = glob.glob(os.path.join(template_dir, "*.md"))
    assert len(md_templates) == 0, f"Legacy markdown templates found: {md_templates}"

    expected_forms = ["bug_report.yml", "feature_request.yml", "security_report.yml", "docs.yml"]
    for form_name in expected_forms:
        path = os.path.join(template_dir, form_name)
        assert os.path.isfile(path), f"Missing required issue form: {form_name}"
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
            assert isinstance(data, dict), f"{form_name} did not parse to a dictionary"
            assert "name" in data, f"{form_name} missing 'name'"
            assert "description" in data, f"{form_name} missing 'description'"
            assert "body" in data, f"{form_name} missing 'body'"
            assert isinstance(data["body"], list), f"{form_name} 'body' must be a list"
            assert len(data["body"]) > 0, f"{form_name} 'body' cannot be empty"


def test_issue_template_config_valid():
    """Verify that config.yml is valid and enforces blank_issues_enabled=False."""
    config_path = os.path.join(".github", "ISSUE_TEMPLATE", "config.yml")
    assert os.path.isfile(config_path), "config.yml missing"
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
        assert isinstance(data, dict), "config.yml did not parse to a dictionary"
        assert data.get("blank_issues_enabled") is False, "blank_issues_enabled must be false"
        assert "contact_links" in data, "config.yml must contain contact_links"
        assert len(data["contact_links"]) >= 2, "config.yml must link to Security and Discussions"
