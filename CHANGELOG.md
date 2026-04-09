# Changelog

## [0.4.1](https://github.com/structured-world/coordinode-python/compare/v0.4.0...v0.4.1) (2026-04-09)


### Bug Fixes

* **release:** pin build version via SETUPTOOLS_SCM_PRETEND_VERSION ([0194b34](https://github.com/structured-world/coordinode-python/commit/0194b34d36f26c31c47687958d25e268367c12c1))
* **release:** pin build version via SETUPTOOLS_SCM_PRETEND_VERSION ([ea7d09a](https://github.com/structured-world/coordinode-python/commit/ea7d09a26735b6a9470d3912108edee5463b4272))

## [0.4.0](https://github.com/structured-world/coordinode-python/compare/v0.3.0...v0.4.0) (2026-04-09)


### Features

* **coordinode:** import __version__ from hatch-vcs _version.py ([1c64499](https://github.com/structured-world/coordinode-python/commit/1c644997f975587f5e48c3e1da093b9634d26e68))
* initial Python SDK for CoordiNode ([a95c513](https://github.com/structured-world/coordinode-python/commit/a95c513f85b07d63911ee0a2052d0f7316f20359))
* uv workspace + PyPI READMEs + donation QR ([744f5f6](https://github.com/structured-world/coordinode-python/commit/744f5f68c1b09a5c0856daddb69fd198432d3684))


### Bug Fixes

* **build:** portable sed -i.bak instead of macOS-only sed -i '' ([1c64499](https://github.com/structured-world/coordinode-python/commit/1c644997f975587f5e48c3e1da093b9634d26e68))
* **build:** sync .PHONY — install-uv to install-pip ([1c64499](https://github.com/structured-world/coordinode-python/commit/1c644997f975587f5e48c3e1da093b9634d26e68))
* **client,langchain,tests:** closed-loop guard, relationships TODO, remove dead fixture ([f843229](https://github.com/structured-world/coordinode-python/commit/f8432293e44c62e0ec3e5b1f8b22cd48bbed702e))
* **client,tests,langchain:** port sentinel, lazy connect, schema parser ([a704de5](https://github.com/structured-world/coordinode-python/commit/a704de54f85b83151be418df38a0a648d9343782))
* **client:** add debug logging on gRPC health check failure ([ea8f1ff](https://github.com/structured-world/coordinode-python/commit/ea8f1ff4a452c9f670f5bd7e72699353d63084c0))
* **client:** parse host:port regardless of default port value ([1c64499](https://github.com/structured-world/coordinode-python/commit/1c644997f975587f5e48c3e1da093b9634d26e68))
* **client:** use regex for host:port parsing to avoid bare IPv6 misparse ([ada7809](https://github.com/structured-world/coordinode-python/commit/ada7809b451086b4b61f28d72c7ba5e0e4621ee0)), closes [#1](https://github.com/structured-world/coordinode-python/issues/1)
* correct API usage across SDK, tests, and integrations ([afc7e8b](https://github.com/structured-world/coordinode-python/commit/afc7e8b73ee6402c232fc79598949675f4e5a36d)), closes [#1](https://github.com/structured-world/coordinode-python/issues/1)
* **langchain,tests:** implement relationship introspection via Cypher, fix formatting ([27e92f3](https://github.com/structured-world/coordinode-python/commit/27e92f353786fa55369b1314780e2bf7f183ddf0))
* **llama-index,client,build:** prevent param collision, port conflict, and sed idempotency ([92d10eb](https://github.com/structured-world/coordinode-python/commit/92d10ebb1df7e4c040b325a383a781d1807605fc))
* **llama-index:** align delete() to use n.id (string) not id(n) (int graph ID) ([ea8f1ff](https://github.com/structured-world/coordinode-python/commit/ea8f1ff4a452c9f670f5bd7e72699353d63084c0))
* **llama-index:** escape node label in upsert_nodes with _cypher_ident ([ea8f1ff](https://github.com/structured-world/coordinode-python/commit/ea8f1ff4a452c9f670f5bd7e72699353d63084c0))
* **llama-index:** extract rel type from dict in get_rel_map variable-length path ([bc156c3](https://github.com/structured-world/coordinode-python/commit/bc156c39c447c749db5c3ecc745bbcca0fef0aa5))
* **llama-index:** prevent Cypher injection via backtick-escaped identifiers ([1c64499](https://github.com/structured-world/coordinode-python/commit/1c644997f975587f5e48c3e1da093b9634d26e68))
* make proto stubs importable and tests green ([c988e75](https://github.com/structured-world/coordinode-python/commit/c988e75dca77cb701ed3635a341d65db3aa2292d))
* **release:** remove trailing blank line in release.yml ([1c64499](https://github.com/structured-world/coordinode-python/commit/1c644997f975587f5e48c3e1da093b9634d26e68))
* **release:** remove unused outputs from release-please.yml ([1c64499](https://github.com/structured-world/coordinode-python/commit/1c644997f975587f5e48c3e1da093b9634d26e68))
* **release:** use default versioning strategy for release-please ([73c15b5](https://github.com/structured-world/coordinode-python/commit/73c15b5760bf85e0ba7fb41a67e0dcc6bbfe1f34))
* **release:** use default versioning strategy for release-please ([ad8b121](https://github.com/structured-world/coordinode-python/commit/ad8b121974609510d7dda9cc5ce076384278e7ce))
* SDK API correctness — constructor, cypher result, VectorResult fields ([2462cb9](https://github.com/structured-world/coordinode-python/commit/2462cb9555c03d0fad3d2af4a0e41d5bf81d2a4c))
* **test:** correct xfail reason for test_vector_search ([0c246ae](https://github.com/structured-world/coordinode-python/commit/0c246ae30d99b3525add88968755b0afac5fc2fb))
* **types,client:** use tuple syntax in isinstance, remove no-op proto import ([cc786bb](https://github.com/structured-world/coordinode-python/commit/cc786bbbd52188eb9b44a4e748c5d33373a79246))
* **types:** exclude bool from vector fast-path; fix docstring and id() usage ([ecca302](https://github.com/structured-world/coordinode-python/commit/ecca30209a9e835b3cfb9b2a08f5c8491e16ac27))


### Documentation

* **release:** document single shared version intent in release-please-config.json ([ea8f1ff](https://github.com/structured-world/coordinode-python/commit/ea8f1ff4a452c9f670f5bd7e72699353d63084c0))
