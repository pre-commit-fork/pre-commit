from __future__ import annotations

import contextlib
import functools
import os
import sys
from typing import Generator
from typing import Sequence

import before_commit.constants as C
from before_commit.envcontext import envcontext
from before_commit.envcontext import PatchesT
from before_commit.envcontext import UNSET
from before_commit.envcontext import Var
from before_commit.hook import Hook
from before_commit.languages import helpers
from before_commit.languages.python import bin_dir
from before_commit.prefix import Prefix
from before_commit.util import clean_path_on_failure
from before_commit.util import cmd_output
from before_commit.util import cmd_output_b
from before_commit.util import rmtree

ENVIRONMENT_DIR: str = 'node_env'


@functools.lru_cache(maxsize=1)
def get_default_version() -> str:
    # nodeenv does not yet support `-n system` on windows
    if sys.platform == 'win32':
        return C.DEFAULT
    # if node is already installed, we can save a bunch of setup time by
    # using the installed version
    elif all(helpers.exe_exists(exe) for exe in ('node', 'npm')):
        return 'system'
    else:
        return C.DEFAULT


def _envdir(prefix: Prefix, version: str) -> str:
    directory = helpers.environment_dir(ENVIRONMENT_DIR, version)
    return prefix.path(directory)


def get_env_patch(venv: str) -> PatchesT:
    if sys.platform == 'cygwin':  # pragma: no cover
        _, win_venv, _ = cmd_output('cygpath', '-w', venv)
        install_prefix = fr'{win_venv.strip()}\bin'
        lib_dir = 'lib'
    elif sys.platform == 'win32':  # pragma: no cover
        install_prefix = bin_dir(venv)
        lib_dir = 'Scripts'
    else:  # pragma: win32 no cover
        install_prefix = venv
        lib_dir = 'lib'
    return (
        ('NODE_VIRTUAL_ENV', venv),
        ('NPM_CONFIG_PREFIX', install_prefix),
        ('npm_config_prefix', install_prefix),
        ('NPM_CONFIG_USERCONFIG', UNSET),
        ('npm_config_userconfig', UNSET),
        ('NODE_PATH', os.path.join(venv, lib_dir, 'node_modules')),
        ('PATH', (bin_dir(venv), os.pathsep, Var('PATH'))),
    )


@contextlib.contextmanager
def in_env(
        prefix: Prefix,
        language_version: str,
) -> Generator[None, None, None]:
    with envcontext(get_env_patch(_envdir(prefix, language_version))):
        yield


def health_check(prefix: Prefix, language_version: str) -> str | None:
    with in_env(prefix, language_version):
        retcode, _, _ = cmd_output_b('node', '--version', retcode=None)
        if retcode != 0:  # pragma: win32 no cover
            return f'`node --version` returned {retcode}'
        else:
            return None


def install_environment(
        prefix: Prefix, version: str, additional_dependencies: Sequence[str],
) -> None:
    additional_dependencies = tuple(additional_dependencies)
    assert prefix.exists('package.json')
    envdir = _envdir(prefix, version)

    # https://msdn.microsoft.com/en-us/library/windows/desktop/aa365247(v=vs.85).aspx?f=255&MSPPError=-2147217396#maxpath
    if sys.platform == 'win32':  # pragma: no cover
        envdir = fr'\\?\{os.path.normpath(envdir)}'
    with clean_path_on_failure(envdir):
        cmd = [
            sys.executable, '-mnodeenv', '--prebuilt', '--clean-src', envdir,
        ]
        if version != C.DEFAULT:
            cmd.extend(['-n', version])
        cmd_output_b(*cmd)

        with in_env(prefix, version):
            # https://npm.community/t/npm-install-g-git-vs-git-clone-cd-npm-install-g/5449
            # install as if we installed from git

            local_install_cmd = (
                'npm', 'install', '--dev', '--prod',
                '--ignore-prepublish', '--no-progress', '--no-save',
            )
            helpers.run_setup_cmd(prefix, local_install_cmd)

            _, pkg, _ = cmd_output('npm', 'pack', cwd=prefix.prefix_dir)
            pkg = prefix.path(pkg.strip())

            install = ('npm', 'install', '-g', pkg, *additional_dependencies)
            helpers.run_setup_cmd(prefix, install)

            # clean these up after installation
            if prefix.exists('node_modules'):  # pragma: win32 no cover
                rmtree(prefix.path('node_modules'))
            os.remove(pkg)


def run_hook(
        hook: Hook,
        file_args: Sequence[str],
        color: bool,
) -> tuple[int, bytes]:
    with in_env(hook.prefix, hook.language_version):
        return helpers.run_xargs(hook, hook.cmd, file_args, color=color)
