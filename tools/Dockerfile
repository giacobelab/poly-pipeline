# PolyCI test tools: everything the QEMU boot test needs, prepared once.
# The "Build labs" workflow builds this only when this file changes (the
# image tag is a hash of it) and pushes it to ghcr.io/<owner>/polyci-qemu.
FROM ubuntu:24.04

ARG SOURCE_REPO=https://github.com/ruffm/PolyCI
LABEL org.opencontainers.image.source=${SOURCE_REPO} \
      org.opencontainers.image.description="PolyCI test tools: qemu-system-i386 and python3-pexpect"

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      qemu-system-x86 python3 python3-pexpect \
 && rm -rf /var/lib/apt/lists/* \
 && qemu-system-i386 --version | head -1 \
 && python3 -c "import pexpect; print('pexpect', pexpect.__version__)"
