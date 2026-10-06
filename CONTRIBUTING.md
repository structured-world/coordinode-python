# Contributing to coordinode-python

Contributions of every kind are welcome: bug reports, features, documentation,
examples.

## Development setup

```bash
git clone --recurse-submodules https://github.com/structured-world/coordinode-python.git
cd coordinode-python
uv sync
make test
```

`make test` regenerates the protobuf stubs and runs the unit suite. Integration
tests need a running CoordiNode server; see `docker-compose.yml`.

## Pull request process

1. Fork the repository and create a branch (`feat/description` or `fix/description`).
2. Make the change, with tests.
3. Make sure `ruff check`, `ruff format --check` and `make test` pass.
4. Write commit messages in the [Conventional Commits](https://www.conventionalcommits.org/) form.
5. Open a pull request describing what changed and why.

## Contributor License Agreement (CLA)

Before a first pull request can be merged, you sign the Structured World
Contributor License Agreement once, at <https://sw.foundation/cla>. It covers
every repository of the organisation and takes a minute: sign in with GitHub,
confirm your e-mail address, sign. The `CLA` status on your pull request then
turns green by itself.

You keep the copyright in your contribution. If you contribute as part of your
job, your employer may also need to sign the corporate agreement; the page
above explains when.

## Questions

Open an issue, or write to oss@sw.foundation.
