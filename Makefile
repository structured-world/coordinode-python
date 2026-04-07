.PHONY: proto proto-check install test test-unit test-integration lint clean

PROTO_SRC  := proto
PROTO_OUT  := coordinode/_proto

# Generate gRPC stubs from proto submodule into coordinode/_proto/
proto:
	@echo "==> Generating proto stubs..."
	@mkdir -p $(PROTO_OUT)
	python -m grpc_tools.protoc \
		-I$(PROTO_SRC) \
		--python_out=$(PROTO_OUT) \
		--grpc_python_out=$(PROTO_OUT) \
		--pyi_out=$(PROTO_OUT) \
		$$(find $(PROTO_SRC) -name '*.proto')
	@# Add __init__.py to every generated package directory
	@find $(PROTO_OUT) -type d -exec touch {}/__init__.py \;
	@# Fix relative imports in generated *_pb2_grpc.py files (grpc_tools generates absolute)
	@find $(PROTO_OUT) -name '*_pb2_grpc.py' -exec sed -i '' \
		's/^from coordinode\./from coordinode._proto.coordinode./g' {} \;
	@echo "==> Proto generation complete: $(PROTO_OUT)/"

proto-check:
	@test -f $(PROTO_OUT)/coordinode/v1/query/cypher_pb2.py || \
		(echo "ERROR: Proto stubs not generated. Run: make proto" && exit 1)

# Install all packages in editable mode for development
install: proto
	pip install -e "coordinode[dev]"
	pip install -e langchain-coordinode/
	pip install -e llama-index-coordinode/

test: proto-check test-unit

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v --timeout=30

lint:
	ruff check coordinode/ langchain-coordinode/ llama-index-coordinode/ tests/
	ruff format --check coordinode/ langchain-coordinode/ llama-index-coordinode/ tests/

clean:
	rm -rf $(PROTO_OUT)/coordinode
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name dist -exec rm -rf {} + 2>/dev/null || true
