import os
import sys


def _run() -> int:
    from src.cli import main

    return main()


def _build() -> int:
    import importlib

    project_root = os.path.dirname(os.path.abspath(__file__))
    data_sep = os.pathsep
    pyproject = os.path.join(project_root, "pyproject.toml")

    pyinstaller = importlib.import_module("PyInstaller.__main__")
    pyinstaller.run([
        "--onefile",
        "--name", "trackforge",
        "--paths", project_root,
        "--add-data", f"{pyproject}{data_sep}.",
        "--collect-submodules", "src",
        "--exclude-module", "PyInstaller",
        os.path.abspath(__file__),
    ])
    return 0


if getattr(sys, "frozen", False):
    sys.exit(_run())


if __name__ == "__main__":
    sys.exit(_build())
