PYTHON ?= python3
PYTEST ?= $(PYTHON) -m pytest

.PHONY: help install-dev test test-online test-whisper test-whisperx

help:
	@echo "make install-dev   安装包和测试依赖"
	@echo "make test          运行默认离线测试（不含 online / slow 标记）"
	@echo "make test-online   运行需联网的可选测试（需 MEDIA_INTAKE_ONLINE=1）"
	@echo "make test-whisper  运行 tiny 模型转写测试（需 MEDIA_INTAKE_WHISPER=1，可能下载模型）"
	@echo "make test-whisperx 运行真实 whisperX 测试（需 MEDIA_INTAKE_WHISPERX=1，可能下载模型）"

install-dev:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTEST) -m "not online and not slow and not whisperx"

test-online:
	MEDIA_INTAKE_ONLINE=1 $(PYTEST) -m online

test-whisper:
	MEDIA_INTAKE_WHISPER=1 $(PYTEST) -m slow

test-whisperx:
	MEDIA_INTAKE_WHISPERX=1 $(PYTEST) -m whisperx
