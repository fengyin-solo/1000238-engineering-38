"""preflight 流水线的回归测试：重点覆盖数据准备环节与可重复执行约定。

运行：cd backend && python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import hashlib
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import preflight  # noqa: E402


def _paths_for(backend: Path, workdir: Path) -> preflight.Paths:
    return preflight.Paths(
        backend=backend,
        frontend=backend.parent / "frontend",
        workdir=workdir,
        venv_python=backend / ".venv" / "bin" / "python",
        seed_file=backend / "app" / "seed.py",
        requirements=backend / "requirements.txt",
        run_sh=backend / "run.sh",
    )


class WorkdirTestCase(unittest.TestCase):
    def test_reset_workdir_clears_stale_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / ".preflight"
            workdir.mkdir()
            (workdir / "上次运行的残留.json").write_text("{}", encoding="utf-8")
            preflight.reset_workdir(_paths_for(BACKEND_DIR, workdir))
            self.assertTrue(workdir.is_dir())
            self.assertEqual(list(workdir.iterdir()), [])


class PrepareDataTestCase(unittest.TestCase):
    """把 app/ 复制到临时目录再改种子数据，验证校验规则，不碰仓库里的真实文件。"""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp())
        self.backend = self._tmp / "backend"
        shutil.copytree(
            BACKEND_DIR / "app",
            self.backend / "app",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        self.paths = _paths_for(self.backend, self._tmp / "work")
        preflight.reset_workdir(self.paths)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _patch_seed(self, old: str, new: str, count: int = 1) -> None:
        seed = self.backend / "app" / "seed.py"
        text = seed.read_text(encoding="utf-8")
        self.assertIn(old, text, f"补丁目标不存在：{old}")
        seed.write_text(text.replace(old, new, count), encoding="utf-8")

    def _problems(self) -> list[str]:
        return preflight.prepare_data(self.paths).problems

    def test_current_seed_passes(self) -> None:
        outcome = preflight.prepare_data(self.paths)
        self.assertEqual(outcome.problems, [])
        self.assertTrue((self.paths.workdir / "manifest.json").exists())

    def test_missing_module_rows_reported(self) -> None:
        seed = self.backend / "app" / "seed.py"
        text = seed.read_text(encoding="utf-8")
        seed.write_text(re.sub(r'"crew": \[.*?\}\]', '"crew": []', text, count=1, flags=re.S), encoding="utf-8")
        problems = self._problems()
        self.assertTrue(any("crew" in p and "缺项" in p for p in problems), problems)

    def test_missing_list_field_reported(self) -> None:
        self._patch_seed("  '成员编号': 'CREW-0001',\n", "")
        problems = self._problems()
        self.assertTrue(any("成员编号" in p and "缺项" in p for p in problems), problems)

    def test_invalid_entry_date_reported(self) -> None:
        self._patch_seed("'进场日期': '2026-09-01'", "'进场日期': '2026-13-40'")
        problems = self._problems()
        self.assertTrue(any("进场日期" in p and "不是合法" in p for p in problems), problems)

    def test_entry_after_exit_reported(self) -> None:
        self._patch_seed("'离场日期': '2026-09-01'", "'离场日期': '2026-08-01'")
        problems = self._problems()
        self.assertTrue(any("晚于离场日期" in p for p in problems), problems)

    def test_unknown_status_reported(self) -> None:
        self._patch_seed("'status': '待进场'", "'status': '放假中'")
        problems = self._problems()
        self.assertTrue(any("放假中" in p and "不在允许序列" in p for p in problems), problems)

    def test_leave_status_accepted(self) -> None:
        self._patch_seed("'status': '待进场'", "'status': '已请假'")
        self.assertEqual(self._problems(), [])

    def test_seed_file_never_modified(self) -> None:
        digest_before = hashlib.sha256((self.backend / "app" / "seed.py").read_bytes()).hexdigest()
        self._problems()
        digest_after = hashlib.sha256((self.backend / "app" / "seed.py").read_bytes()).hexdigest()
        self.assertEqual(digest_before, digest_after)

    def test_rerun_overwrites_manifest_without_residue(self) -> None:
        self.assertEqual(self._problems(), [])
        first = (self.paths.workdir / "manifest.json").read_text(encoding="utf-8")
        self.assertEqual(self._problems(), [])
        second = (self.paths.workdir / "manifest.json").read_text(encoding="utf-8")
        self.assertEqual(first, second)
        self.assertEqual([p.name for p in self.paths.workdir.iterdir()], ["manifest.json"])


if __name__ == "__main__":
    unittest.main()
