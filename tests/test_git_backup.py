import os
import tempfile
from pathlib import Path

from app.git_backup import IsolatedGitBackupManager

SAMPLE_INI = """[/Script/Pal.PalGameWorldSettings]
OptionSettings=(ServerName="Test Server",ExpRate=1.500000)
"""


def test_git_backup_manager_lifecycle(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / "repo"
        ini_file = Path(tmpdir) / "PalWorldSettings.ini"
        with open(ini_file, "w", encoding="utf-8") as f:
            f.write(SAMPLE_INI)

        def mock_init(self, live_ini, backup_branch="config-snapshots"):
            self.live_ini_path = Path(live_ini).resolve()
            self.backup_repo_dir = repo_dir.resolve()
            self.backup_branch = backup_branch
            self.staged_file = self.backup_repo_dir / "PalWorldSettings.ini"
            self._init_repo()

        monkeypatch.setattr(IsolatedGitBackupManager, "__init__", mock_init)

        mgr = IsolatedGitBackupManager(str(ini_file))

        # 1. Create first commit
        commit1 = mgr.create_commit("SAVE", "Initial web save")
        assert commit1 is not None

        # 2. Modify INI and create second commit
        with open(ini_file, "w", encoding="utf-8") as f:
            f.write(SAMPLE_INI.replace("ExpRate=1.500000", "ExpRate=2.000000"))

        commit2 = mgr.create_commit("RESTART", "Pre-restart backup")
        assert commit2 is not None

        # 3. Check history
        history = mgr.get_history()
        assert len(history) >= 2
        assert history[0]["hash"] == commit2

        # 4. Check diff
        diff = mgr.get_diff(commit2)
        assert "ExpRate" in diff

        # 5. Restore commit1
        assert mgr.restore_commit(commit1) is True
        with open(ini_file, encoding="utf-8") as f:
            content = f.read()
        assert "ExpRate=1.500000" in content


def test_git_backup_manager_empty_or_missing_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        ini_file = os.path.join(tmpdir, "non_existent.ini")
        repo_dir = os.path.join(tmpdir, "backup_repo")
        mgr = IsolatedGitBackupManager(ini_file, backup_repo_dir=repo_dir)
        assert mgr.create_commit("SAVE", "Missing file commit") is None
        assert mgr.get_diff("non_existent_hash") == ""
