import shutil
import subprocess
import venv

from fi.alk.harness.runtime_files import relocate_runtime_files


def test_copied_worlds_relocate_venv_launchers_without_changing_shared_build(tmp_path):
    build = tmp_path / "build"
    scripts = build / ".venv" / "bin"
    scripts.mkdir(parents=True)
    script = f"#!{scripts}/python\nprint('ready')\n"
    (scripts / "serve").write_text(script)
    (scripts / "python").symlink_to("/usr/bin/python3")
    (build / "launch").symlink_to(scripts / "serve")
    (build / "data.txt").write_text("baseline")
    worlds = [tmp_path / "world-0", tmp_path / "world-1"]
    for world in worlds:
        shutil.copytree(build, world, symlinks=True)
        relocate_runtime_files(build, world)
        assert str(world) in (world / "launch").read_text()
        assert (world / "launch").resolve() == world / ".venv" / "bin" / "serve"
        assert (world / "data.txt").stat().st_ino != (build / "data.txt").stat().st_ino
    (worlds[0] / "data.txt").write_text("changed")
    assert (worlds[1] / "data.txt").read_text() == "baseline"
    assert (build / "data.txt").read_text() == "baseline"
    assert (scripts / "serve").read_text() == script


def test_editable_imports_resolve_to_each_private_world(tmp_path):
    build = tmp_path / "build"
    package = build / "agentpkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    venv.EnvBuilder(with_pip=False).create(build / ".venv")
    interpreter = build / ".venv" / "bin" / "python"
    site = subprocess.check_output(
        [
            str(interpreter),
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        text=True,
    ).strip()
    from pathlib import Path

    # Setuptools' editable .pth imports a finder whose mapping holds absolute paths.
    finder = "__editable___agentpkg_1_finder"
    (Path(site) / f"{finder}.py").write_text(
        "import sys\nfrom importlib.util import spec_from_file_location\n"
        f"MAPPING = {{'agentpkg': {str(package)!r}}}\n"
        "class Finder:\n"
        " @classmethod\n"
        " def find_spec(cls, fullname, path=None, target=None):\n"
        "  if fullname in MAPPING:\n"
        "   return spec_from_file_location(fullname, MAPPING[fullname] + '/__init__.py')\n"
        "def install(): sys.meta_path.append(Finder)\n"
    )
    (Path(site) / "__editable__.agentpkg.pth").write_text(
        f"import {finder}; {finder}.install()\n"
    )
    for index in range(2):
        world = tmp_path / f"world-{index}"
        shutil.copytree(build, world, symlinks=True)
        relocate_runtime_files(build, world)
        output = subprocess.check_output(
            [
                str(world / ".venv" / "bin" / "python"),
                "-c",
                "import agentpkg; from pathlib import Path; "
                "print(agentpkg.__file__); "
                "Path(agentpkg.__file__).with_name('state').write_text('private')",
            ],
            cwd=tmp_path,
            text=True,
        ).strip()
        assert Path(output) == world / "agentpkg" / "__init__.py"
        assert (world / "agentpkg" / "state").read_text() == "private"
    assert not (package / "state").exists()
