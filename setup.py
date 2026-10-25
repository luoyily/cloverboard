"""Build the WebUI into wheels; editable installs use the source checkout."""

from pathlib import Path
import shutil
import subprocess

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithWebUI(build_py):
    def run(self):
        super().run()
        if self.editable_mode:
            return
        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError("Building CloverBoard requires Node.js and npm. Install a wheel to use the prebuilt WebUI.")
        web = Path(__file__).resolve().parent / "web"
        subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=web, check=True)
        subprocess.run([npm, "run", "build"], cwd=web, check=True)
        build_root = Path(self.build_lib).resolve()
        target = build_root / "cloverboard" / "_static"
        if not target.resolve().is_relative_to(build_root) or target.is_symlink():
            raise RuntimeError("WebUI build output must stay inside the package build directory")
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(web / "dist", target)


setup(cmdclass={"build_py": BuildWithWebUI})
