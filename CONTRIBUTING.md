# Contributing to coordinode-python

This repository is archived and no longer accepts issues or pull requests.
structured-world has ended its investment in Python development and maintenance.
See the [archival announcement](README.md#status-archived).

## Development setup

```bash
git clone --recurse-submodules https://github.com/structured-world/coordinode-python.git
cd coordinode-python
uv sync
make test
```

`make test` regenerates the protobuf stubs and runs the unit suite. Integration
tests need a running CoordiNode server; see `docker-compose.yml`.

These instructions are retained for anyone inspecting or independently using
the archived source. Existing licenses continue to apply; no maintenance or
review by structured-world is offered.
