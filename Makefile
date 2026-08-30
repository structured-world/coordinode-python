.PHONY: proto proto-check install install-pip test test-unit test-integration lint clean

PROTO_SRC  := proto
PROTO_OUT  := coordinode/coordinode/_proto
# grpcio-tools lives in the synced environment, so both the include-path probe
# below and the generation itself have to run through the same interpreter.
# A bare python3 finds neither.
PYTHON     ?= uv run python

# Well-known types that ship with grpc_tools. The proto submodule vendors its
# own google/protobuf/descriptor.proto so the Rust build works on hosts without
# protobuf-devel; that copy is older than what this protoc expects and, if it
# wins the include search, generation dies with "Malformed descriptor.proto
# doesn't contain google.protobuf.FeatureSet". Searching here first keeps
# descriptor.proto on protoc's own copy while google/api/* still resolves from
# the submodule, which is the only place it exists.
GRPC_INC := $(shell $(PYTHON) -c "import grpc_tools, os; print(os.path.join(os.path.dirname(grpc_tools.__file__), '_proto'))")

# Generate gRPC stubs from proto submodule into coordinode/_proto/
proto:
	@echo "==> Generating proto stubs..."
	@mkdir -p $(PROTO_OUT)
	$(PYTHON) -m grpc_tools.protoc \
		-I"$(GRPC_INC)" \
		-I$(PROTO_SRC) \
		--python_out=$(PROTO_OUT) \
		--grpc_python_out=$(PROTO_OUT) \
		--pyi_out=$(PROTO_OUT) \
		$$(find $(PROTO_SRC) -name '*.proto' -not -path '$(PROTO_SRC)/google/protobuf/*')
	@# Add __init__.py to every generated package directory
	@find $(PROTO_OUT) -type d -exec touch {}/__init__.py \;
	@# Fix absolute imports in all generated pb2 files (grpc_tools generates absolute paths)
	@# sed -i.bak is portable: macOS needs empty-string backup arg, GNU sed uses -i alone;
	@# using .bak suffix works on both, then we clean up the backup files.
	@find $(PROTO_OUT) -name '*.py' -exec sed -i.bak \
		's/from coordinode\.v1\./from coordinode._proto.coordinode.v1./g' {} \;
	@find $(PROTO_OUT) -name '*.py.bak' -delete
	@echo "==> Proto generation complete: $(PROTO_OUT)/"

proto-check:
	@test -f $(PROTO_OUT)/coordinode/v1/query/cypher_pb2.py || \
		(echo "ERROR: Proto stubs not generated. Run: make proto" && exit 1)

# Install using uv (recommended for contributors).
# uv sync runs first — it installs grpcio-tools which proto generation requires.
install:
	uv sync
	$(MAKE) proto

# Install using pip (alternative — works without uv)
install-pip:
	pip install -e "coordinode[dev]"
	pip install -e langchain-coordinode/
	pip install -e llama-index-coordinode/
	$(MAKE) proto

test: proto-check test-unit

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v --timeout=30

lint:
	ruff check coordinode/ langchain-coordinode/ llama-index-coordinode/ tests/
	ruff format --check coordinode/ langchain-coordinode/ llama-index-coordinode/ tests/

clean:
	rm -rf $(PROTO_OUT)
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name dist -exec rm -rf {} + 2>/dev/null || true
