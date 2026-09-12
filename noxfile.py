import nox

nox.options.default_venv_backend = "uv"
nox.options.reuse_existing_virtualenvs = True

INSTALL_ARGS = ("uv", "sync", "--group=dev", "--frozen", "--no-default-groups", "--active")


@nox.session(tags=["tests"], python=["3.12"])
def integration(session: nox.Session) -> None:
    session.run_install(*INSTALL_ARGS, f"--python={session.python}")
    session.run("pytest", "tests/integration")


@nox.session(tags=["tests"])
def unit(session: nox.Session) -> None:
    install_args = list(INSTALL_ARGS)
    if session.python:
        install_args.append(f"--python={session.python}")
    session.run_install(*install_args)
    session.run("pytest", "tests/unit", "-v")
