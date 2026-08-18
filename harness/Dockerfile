# The harness in a container.
#
# Three runtimes have to be here, and each is here for a reason worth stating. Python runs the
# harness itself. Node runs the Claude Code CLI, which the Agent SDK drives as a subprocess --
# without it every stage fails at its first model call. The docker client is here because the
# harness starts containers of its own: a store for a world that needs a real engine, and an
# agent whose tools cannot be imported. It talks to a daemon over the socket proxy rather than
# running one of its own, so there is no Docker-in-Docker and no privileged mode.

FROM node:22-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:${PATH}"

# Bookworm's python3 is 3.11, which satisfies requires-python. The venv is not isolation from
# anything here; it is what lets pip write at all, since a Debian-managed interpreter refuses.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

# The client alone, not the daemon: the static tarball is a few megabytes where the distribution
# package would pull in an engine this container must never run.
ARG DOCKER_CLI_VERSION=27.3.1
RUN curl -fsSL "https://download.docker.com/linux/static/stable/$(uname -m)/docker-${DOCKER_CLI_VERSION}.tgz" \
      | tar -xz -C /tmp docker/docker \
    && mv /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker

RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

# The harness has its own manifest but imports the parent checkout's public fi.simulate API.
# Build from the parent checkout: docker build -f harness/Dockerfile .
COPY pyproject.toml README.md ./
COPY src ./src
COPY harness/pyproject.toml harness/README.md ./harness/
COPY harness/src ./harness/src
RUN pip install -e . && pip install -e ./harness

COPY harness/ui ./harness/ui

# Reachable from outside the container, which loopback would not be.
ENV HARNESS_HOST=0.0.0.0 \
    HARNESS_PORT=8777
EXPOSE 8777

CMD ["python", "harness/ui/server.py"]
