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

Before a first pull request can be merged, you sign the
[Contributor License Agreement](CLA.md). Signing happens in the pull request:
a bot posts the request, you reply with the sentence it asks for, and the
signature is recorded in `signatures/` in this repository. It is a one-time
step per GitHub account.

In short, the CLA says that you keep the copyright in your contribution, that
you grant the project's copyright holder (and any successor the copyright is
assigned to) a perpetual, worldwide, royalty-free, irrevocable licence to use,
modify, distribute and sublicense it under any terms, and that you are entitled
to make that grant. The project promises in return that your contribution stays
available under Apache-2.0 and that you remain free to do anything with your
own work.

If your employer owns what you write, ask them to confirm they permit the
contribution before you sign.

## Questions

Open an issue, or write to oss@sw.foundation.
