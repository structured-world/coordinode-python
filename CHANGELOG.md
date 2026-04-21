# Changelog

## [1.0.0](https://github.com/structured-world/coordinode-python/compare/v0.9.1...v1.0.0) (2026-04-21)


### ⚠ BREAKING CHANGES

* **sdk:** HybridTextVectorSearch RPC removed upstream.

### Features

* **sdk:** expose consistency controls and document v0.4 features ([8d69841](https://github.com/structured-world/coordinode-python/commit/8d69841d158674fe0d5db53d2498399879dd9f9e))
* **sdk:** update for coordinode-server v0.4.1 ([4b0274b](https://github.com/structured-world/coordinode-python/commit/4b0274bcbc2b80339bc89bc5f7239a5853393bbc)), closes [#46](https://github.com/structured-world/coordinode-python/issues/46)
* **sdk:** update for coordinode-server v0.4.1 ([#47](https://github.com/structured-world/coordinode-python/issues/47)) ([7b426c0](https://github.com/structured-world/coordinode-python/commit/7b426c04396c789c2d7d6e3915ab320617102bea))


### Bug Fixes

* **demo,build,tests:** always pin SDK in Colab, broaden extension ignores, drop created flag ([10ab38d](https://github.com/structured-world/coordinode-python/commit/10ab38d917d440571342a560b8841f124d08d18e))
* **demo,tests,docs:** guard apt install, proto stub import, README examples, and port-probe fallback ([443b189](https://github.com/structured-world/coordinode-python/commit/443b189e2ad7f28fb378517f10b896b9948b3a16))
* **demo,tests,docs:** restore embedded adapter wrap, tighten test cleanup, clarify Cypher helpers ([e938194](https://github.com/structured-world/coordinode-python/commit/e938194bf9708cf6da2a4783dff5b9124e848a5f))
* **demo:** close failed CoordinodeClient before raise; reinstate hard-fail on unhealthy port 7080 ([991f6e7](https://github.com/structured-world/coordinode-python/commit/991f6e760b3180e02fc12ba374196ad34d7fe2d3))
* **demo:** drop port probe, switch embedded to file-backed persistence ([0b7fd95](https://github.com/structured-world/coordinode-python/commit/0b7fd95245f48eab3ae9ddb3e9499171c68a89f4))
* **demo:** install protobuf-compiler before embedded build in Colab ([354a32d](https://github.com/structured-world/coordinode-python/commit/354a32dde28dc52c27c56b4c76dc3fd46f39210c))
* **demo:** pin coordinode SDK in 03 notebook Colab branch, surface unhealthy-port fallback ([4f6a797](https://github.com/structured-world/coordinode-python/commit/4f6a797ae72ae97fdb285aa4312a82b0bc34b15a))
* **demo:** remove unpinned coordinode override from Colab install block ([c8cea55](https://github.com/structured-world/coordinode-python/commit/c8cea550da63e410a5e6e30f4ebafd9b512ef85a))
* **demo:** stable DEMO_TAG in embedded mode and portable temp dir ([c2d62b0](https://github.com/structured-world/coordinode-python/commit/c2d62b0595867651df43c2b3d3fdbec4341d6642))
* **demo:** tighten pin condition and hard-fail on unhealthy gRPC port ([5957b8b](https://github.com/structured-world/coordinode-python/commit/5957b8bb43ce0ee5c47ac2ff5f773cc599566200))
* **sdk,demo:** tighten consistency validation and pin coordinode SDK in Colab ([4175e96](https://github.com/structured-world/coordinode-python/commit/4175e967a5d935d54ab98b7b7f9035594e0f9e63))
* **sdk,demo:** validate causal-read precondition; clarify embedded install path ([50ddc08](https://github.com/structured-world/coordinode-python/commit/50ddc08a89a21fca73be007cb22b57a0054225c3))
* **sdk:** validate after_index type before causal-read precondition check ([d62e53b](https://github.com/structured-world/coordinode-python/commit/d62e53b0c5f64f66f79e3c46e8be0b9fa006120e))


### Documentation

* **coordinode:** note CREATE TEXT INDEX prerequisite for hybrid search example ([8f88a67](https://github.com/structured-world/coordinode-python/commit/8f88a67597c8bf0daa7f8076d5f1201d171ac03b))
* **demo:** expand seed success message to cover embedded + server modes ([2cd87d3](https://github.com/structured-world/coordinode-python/commit/2cd87d3092f5d0112c662f20309846cec38ea793))

## [0.9.1](https://github.com/structured-world/coordinode-python/compare/v0.9.0...v0.9.1) (2026-04-16)


### Bug Fixes

* **langchain:** catch gRPC errors in keyword_search, add missing methods to README ([b4f887f](https://github.com/structured-world/coordinode-python/commit/b4f887f0faaecafed8464ff73753839944b4e00c))
* **langchain:** catch gRPC errors in keyword_search, add missing methods to README ([#44](https://github.com/structured-world/coordinode-python/issues/44)) ([871bd11](https://github.com/structured-world/coordinode-python/commit/871bd111960b6b9289e253ac2f5ea9255db7442a))

## [0.9.0](https://github.com/structured-world/coordinode-python/compare/v0.8.0...v0.9.0) (2026-04-16)


### Features

* **langchain:** add CoordinodeGraph.keyword_search() ([8169085](https://github.com/structured-world/coordinode-python/commit/81690850e4ff081fb12af13ef39a91fe2df6c0f3)), closes [#22](https://github.com/structured-world/coordinode-python/issues/22)
* **langchain:** add CoordinodeGraph.keyword_search() ([#41](https://github.com/structured-world/coordinode-python/issues/41)) ([35d2f5e](https://github.com/structured-world/coordinode-python/commit/35d2f5e674a9c62421770cd48914c3698627261c))


### Bug Fixes

* **langchain:** use "id" key in keyword_search() output, matching similarity_search() ([81633f9](https://github.com/structured-world/coordinode-python/commit/81633f9bf5ea2273ebdcf508ce44ebfc8673ca1e))

## [0.8.0](https://github.com/structured-world/coordinode-python/compare/v0.7.0...v0.8.0) (2026-04-16)


### Features

* **client,adapters,demo:** schema DDL API, full-text search, Colab notebooks ([d5a4eb9](https://github.com/structured-world/coordinode-python/commit/d5a4eb9112986507295df7ffd80d5482625e26b3))
* **client,adapters,demo:** schema DDL API, full-text search, Colab notebooks ([4ed2391](https://github.com/structured-world/coordinode-python/commit/4ed23912eeade1b0042cbff0a48e065c0a3e4e25))


### Bug Fixes

* **client,demo:** accept schema_mode as str|int; fix error messages; run install-sdk.sh at Jupyter startup ([e5c50c4](https://github.com/structured-world/coordinode-python/commit/e5c50c48c985f67336c121d1902e3dc541bf7362))
* **client,demo:** align type annotations and query_facts param guard ([e581683](https://github.com/structured-world/coordinode-python/commit/e581683aa4da7aa6e3ce04716bcbd5b6ea9cee31))
* **client,demo:** align type annotations with runtime; exec in docker; notebook fixes ([62ea048](https://github.com/structured-world/coordinode-python/commit/62ea0485d02e5b44dde45fb1a36ecb1e3fd7de1a))
* **client,demo:** limit validation, HybridResult comment, embedded fallthrough ([4655747](https://github.com/structured-world/coordinode-python/commit/465574762f8732b753cfea7c8ca5f06ab2b4bda4))
* **client,demo:** reject bool schema_mode, guard _EMBEDDED_PIP_SPEC reference ([a17b45a](https://github.com/structured-world/coordinode-python/commit/a17b45ab5cf7604da228274653a0c3972fd07259))
* **client:** remove schema_mode from create_edge_type — proto field absent ([ded059d](https://github.com/structured-world/coordinode-python/commit/ded059d45f3f9dd60b23f098ae6caf65a64dbf08))
* **langchain:** pass cypher params positionally for injected client compatibility ([8b7ceb4](https://github.com/structured-world/coordinode-python/commit/8b7ceb4c8e9b50f035a938d5676e3274d7d2da75))


### Documentation

* **demo:** clarify rustup supply-chain note in all notebooks ([565a74c](https://github.com/structured-world/coordinode-python/commit/565a74c8d0cacef114568861744dbff431b4fee7))

## [0.7.0](https://github.com/structured-world/coordinode-python/compare/v0.6.0...v0.7.0) (2026-04-13)


### Features

* add coordinode-embedded in-process Python package ([e1c2e3c](https://github.com/structured-world/coordinode-python/commit/e1c2e3c288cc7bc5b51467375b318dc6b59f9803))

## [0.6.0](https://github.com/structured-world/coordinode-python/compare/v0.5.0...v0.6.0) (2026-04-13)


### Features

* **client:** add get_labels, get_edge_types, traverse ([a1c75ee](https://github.com/structured-world/coordinode-python/commit/a1c75ee3fe1361710a6d7a8e0518a3ecee166ed6))
* **client:** add get_labels(), get_edge_types(), traverse() — R-SDK3 ([4163364](https://github.com/structured-world/coordinode-python/commit/4163364d13e9ebbfebced5fe9714a51c08df5c28))


### Bug Fixes

* **client:** add type guards for direction and max_depth in traverse() ([ae37106](https://github.com/structured-world/coordinode-python/commit/ae3710676fd5564876b806a2e63805d3698556b4))
* **client:** correct schema type string representations ([1e59f71](https://github.com/structured-world/coordinode-python/commit/1e59f7159c99277921c023745f2f9da70347f33c))
* **client:** validate direction in traverse(), fix lint, guard test cleanup ([80a73c6](https://github.com/structured-world/coordinode-python/commit/80a73c6c547a0b819b90c13244d3b821f81ec10e))
* **client:** validate max_depth &gt;= 1 in traverse(); xfail strict=True ([4990122](https://github.com/structured-world/coordinode-python/commit/4990122eea80f0f85198d63296153fa4106428f3))

## [0.5.0](https://github.com/structured-world/coordinode-python/compare/v0.4.4...v0.5.0) (2026-04-12)


### Features

* **langchain:** add similarity_search() to CoordinodeGraph ([7c4d4c0](https://github.com/structured-world/coordinode-python/commit/7c4d4c0b88fc934b83a6bee85ed8db04c5b69b6c)), closes [#20](https://github.com/structured-world/coordinode-python/issues/20)
* similarity_search() for LangChain + upsert_relations() idempotency test ([f0ad603](https://github.com/structured-world/coordinode-python/commit/f0ad60333ea294dc9b2eea263c3c096d5250280a))
* use MERGE for edges, wildcard patterns, type()/labels() functions ([1101ac8](https://github.com/structured-world/coordinode-python/commit/1101ac8dd3d1a37f744ef275723c78a10b2f83d2))
* use MERGE for edges, wildcard patterns, type()/labels() functions ([6d009a7](https://github.com/structured-world/coordinode-python/commit/6d009a714a90c3a915deb84d618a15ea35830a20)), closes [#24](https://github.com/structured-world/coordinode-python/issues/24)


### Bug Fixes

* **langchain:** align similarity_search() signature with Sequence protocol and issue spec ([ab3559e](https://github.com/structured-world/coordinode-python/commit/ab3559e84d6dfee411e1cac45aac22f251e71abb))
* **langchain:** deduplicate relationship triples after _first_label normalization ([f0e1ff3](https://github.com/structured-world/coordinode-python/commit/f0e1ff3f954e83f11671fb385c058af514c72c6e))
* **langchain:** guard empty query_vector via len() for Sequence compatibility ([951b487](https://github.com/structured-world/coordinode-python/commit/951b48739be1f242b8638e9e9302a7a049f6ec79))
* **langchain:** sort similarity_search() results by distance + tighten test ([c9246ac](https://github.com/structured-world/coordinode-python/commit/c9246acc1ce7ea54fae40e02a58e34a1e44b5f28))
* **langchain:** use min() in _first_label for deterministic label selection ([778e8c3](https://github.com/structured-world/coordinode-python/commit/778e8c336588b13465993312c50f48cf9b0f8e63))


### Documentation

* **langchain:** explain why refresh_schema uses no LIMIT on DISTINCT query ([ab1ea64](https://github.com/structured-world/coordinode-python/commit/ab1ea64b1934a3b42a439cb46b00abcefed7a003))

## [0.4.4](https://github.com/structured-world/coordinode-python/compare/v0.4.3...v0.4.4) (2026-04-09)


### Bug Fixes

* **adapters:** fix wildcard [r] in refresh_schema, depth default, docstrings ([64a2877](https://github.com/structured-world/coordinode-python/commit/64a2877b12dabb759f44fd5e40f7b49896a55f2e))
* **adapters:** raise NotImplementedError for unsupported wildcard patterns ([19a3b34](https://github.com/structured-world/coordinode-python/commit/19a3b346efbb080da35b2db6b442dc3fbf82d669))
* **adapters:** use unconditional CREATE for edges; fix get_rel_map ([f045c77](https://github.com/structured-world/coordinode-python/commit/f045c77e01a5d8ba20dcb87923ed68f4ce4337dc))
* CoordiNode Cypher compatibility — add_graph_documents, __type__/__label__, MATCH+CREATE ([d59e27e](https://github.com/structured-world/coordinode-python/commit/d59e27e02a7bd0d97d97d950ffd5b3ad982b8817))
* harden refresh_schema, _stable_document_id, get_rel_map limit ([828e8d9](https://github.com/structured-world/coordinode-python/commit/828e8d976c607d1f7745eec9beb6c5692e252913))
* **langchain:** enforce node.id as merge key; stable document IDs ([13487c9](https://github.com/structured-world/coordinode-python/commit/13487c9ca3b72ffce1a7ba17b8c37e988a692a10))
* **langchain:** implement add_graph_documents and use __label__/__type__ ([50fb1f1](https://github.com/structured-world/coordinode-python/commit/50fb1f1c23519e43eb6d55f1d8014400cb700471)), closes [#14](https://github.com/structured-world/coordinode-python/issues/14)
* **llama-index:** use __type__ for rel type and MATCH+CREATE for edges ([c06a820](https://github.com/structured-world/coordinode-python/commit/c06a820448c9d4d1bd0c33e2d356b75be6abb542)), closes [#14](https://github.com/structured-world/coordinode-python/issues/14)

## [0.4.3](https://github.com/structured-world/coordinode-python/compare/v0.4.2...v0.4.3) (2026-04-09)


### Bug Fixes

* **ci:** install workspace packages as editable + fix lint ([e6e503f](https://github.com/structured-world/coordinode-python/commit/e6e503f265179d54dc694a471c34f4b93ef531be))
* **ci:** install workspace packages as editable + fix lint ([f5d0b2c](https://github.com/structured-world/coordinode-python/commit/f5d0b2c75fceab9e49804a5ac610da4bd3fb8d59))
* **coordinode:** move package into coordinode/ subdirectory for correct wheel build ([2a63dd0](https://github.com/structured-world/coordinode-python/commit/2a63dd0ac7e6cead2a2e9686a2d9c8ac4f118f8e))
* **coordinode:** move package into coordinode/ subdirectory for correct wheel build ([9328532](https://github.com/structured-world/coordinode-python/commit/932853228928bd084e67641a165bfcaffb0d8535))

## [0.4.2](https://github.com/structured-world/coordinode-python/compare/v0.4.1...v0.4.2) (2026-04-09)


### Bug Fixes

* **coordinode:** fix empty wheel — use sources mapping instead of packages ([93aba40](https://github.com/structured-world/coordinode-python/commit/93aba40a069f645a3e47fc9dc32238d068c1b83e))
* **coordinode:** use sources mapping to include package files in wheel ([9a1204a](https://github.com/structured-world/coordinode-python/commit/9a1204a74dfb5b7ded0eb070860eb652a5e19c8a))

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
